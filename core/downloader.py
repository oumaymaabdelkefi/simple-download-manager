"""
Core download manager: handles segmented multi-threaded downloads.
"""

import os
import time
import threading
import requests
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Callable


class DownloadStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BandwidthLimiter:
    """Shared token bucket for limiting combined throughput."""

    def __init__(self, rate: Optional[int] = None):
        self.rate = rate
        self._tokens = float(rate or 0)
        self._updated_at = time.time()
        self._lock = threading.Lock()

    def set_rate(self, rate: Optional[int]):
        with self._lock:
            self.rate = rate
            self._tokens = float(rate or 0)
            self._updated_at = time.time()

    def wait(self, size: int):
        while True:
            with self._lock:
                if not self.rate or self.rate <= 0:
                    return

                now = time.time()
                elapsed = now - self._updated_at
                self._updated_at = now
                self._tokens = min(float(self.rate), self._tokens + elapsed * self.rate)

                if self._tokens >= size:
                    self._tokens -= size
                    return

                delay = (size - self._tokens) / self.rate
            time.sleep(min(delay, 1.0))


@dataclass
class SegmentInfo:
    index: int
    start: int
    end: int
    downloaded: int = 0
    status: DownloadStatus = DownloadStatus.PENDING
    retries: int = 0


@dataclass
class DownloadTask:
    url: str
    dest_path: str
    num_threads: int = 4
    max_retries: int = 3
    bandwidth_limit: Optional[int] = None  # Combined bytes per second limit
    chunk_size: int = 1024 * 64  # 64 KB

    # State
    status: DownloadStatus = DownloadStatus.PENDING
    total_size: int = 0
    downloaded_bytes: int = 0
    segments: List[SegmentInfo] = field(default_factory=list)
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    error: Optional[str] = None

    # Callbacks
    on_progress: Optional[Callable] = None
    on_complete: Optional[Callable] = None
    on_error: Optional[Callable] = None
    global_limiter: Optional[BandwidthLimiter] = None

    # Threading
    _pause_event: threading.Event = field(default_factory=threading.Event)
    _cancel_event: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self._pause_event.set()  # Not paused by default

    @property
    def progress(self) -> float:
        if self.total_size == 0:
            return 0.0
        return min(100.0, (self.downloaded_bytes / self.total_size) * 100)

    @property
    def speed(self) -> float:
        """Bytes per second."""
        if self.start_time is None:
            return 0.0
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return 0.0
        return self.downloaded_bytes / elapsed

    @property
    def eta(self) -> Optional[float]:
        """Estimated seconds remaining."""
        spd = self.speed
        if spd == 0 or self.total_size == 0:
            return None
        remaining = self.total_size - self.downloaded_bytes
        return remaining / spd

    @property
    def filename(self) -> str:
        return os.path.basename(self.dest_path)

    def throttle(self):
        """Limit combined task throughput across all segment workers."""
        if not self.bandwidth_limit or self.bandwidth_limit <= 0 or self.start_time is None:
            return
        expected_elapsed = self.downloaded_bytes / self.bandwidth_limit
        actual_elapsed = time.time() - self.start_time
        delay = expected_elapsed - actual_elapsed
        if delay > 0:
            time.sleep(min(delay, 1.0))


class SegmentDownloader(threading.Thread):
    """Downloads a single byte-range segment of a file."""

    def __init__(self, task: DownloadTask, segment: SegmentInfo, session: requests.Session):
        super().__init__(daemon=True)
        self.task = task
        self.segment = segment
        self.session = session

    def run(self):
        try:
            while self.segment.retries <= self.task.max_retries:
                try:
                    self._download()
                    return
                except Exception as e:
                    self.segment.retries += 1
                    if self.segment.retries > self.task.max_retries:
                        self.segment.status = DownloadStatus.FAILED
                        with self.task._lock:
                            self.task.error = f"Segment {self.segment.index} failed: {e}"
                        return
                    time.sleep(2 ** self.segment.retries)  # Exponential backoff
        finally:
            self.session.close()

    def _download(self):
        segment_size = self.segment.end - self.segment.start + 1
        if self.segment.downloaded >= segment_size:
            self.segment.downloaded = segment_size
            self.segment.status = DownloadStatus.COMPLETED
            return

        headers = {"Range": f"bytes={self.segment.start + self.segment.downloaded}-{self.segment.end}"}
        self.segment.status = DownloadStatus.DOWNLOADING

        with self.session.get(self.task.url, headers=headers, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            tmp_path = f"{self.task.dest_path}.part{self.segment.index}"
            mode = "ab" if self.segment.downloaded > 0 else "wb"

            with open(tmp_path, mode) as f:
                for chunk in resp.iter_content(chunk_size=self.task.chunk_size):
                    # Pause support
                    self.task._pause_event.wait()

                    # Cancel support
                    if self.task._cancel_event.is_set():
                        self.segment.status = DownloadStatus.CANCELLED
                        return

                    if chunk:
                        f.write(chunk)
                        size = len(chunk)
                        self.segment.downloaded += size
                        with self.task._lock:
                            self.task.downloaded_bytes += size
                        if self.task.global_limiter:
                            self.task.global_limiter.wait(size)
                        self.task.throttle()
                        if self.task.on_progress:
                            self.task.on_progress(self.task)

        self.segment.status = DownloadStatus.COMPLETED


class DownloadManager:
    """Orchestrates multi-threaded segmented downloads."""

    def __init__(self, global_bandwidth_limit: Optional[int] = None):
        self._tasks: dict[str, DownloadTask] = {}
        self._lock = threading.Lock()
        self.global_limiter = BandwidthLimiter(global_bandwidth_limit)

    def set_global_bandwidth_limit(self, limit: Optional[int]):
        self.global_limiter.set_rate(limit)

    def create_task(
        self,
        url: str,
        dest_dir: str = ".",
        filename: Optional[str] = None,
        num_threads: int = 4,
        max_retries: int = 3,
        bandwidth_limit: Optional[int] = None,
        on_progress: Optional[Callable] = None,
        on_complete: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
    ) -> DownloadTask:
        if not filename:
            filename = url.split("/")[-1].split("?")[0] or "download"
        dest_path = os.path.join(dest_dir, filename)
        os.makedirs(dest_dir, exist_ok=True)

        task = DownloadTask(
            url=url,
            dest_path=dest_path,
            num_threads=num_threads,
            max_retries=max_retries,
            bandwidth_limit=bandwidth_limit,
            on_progress=on_progress,
            on_complete=on_complete,
            on_error=on_error,
            global_limiter=self.global_limiter,
        )

        with self._lock:
            self._tasks[dest_path] = task

        return task

    def start(self, task: DownloadTask):
        thread = threading.Thread(target=self._run_task, args=(task,), daemon=True)
        thread.start()

    def _run_task(self, task: DownloadTask):
        try:
            with requests.Session() as session:
                # HEAD request to get file size and check range support
                head = session.head(task.url, allow_redirects=True, timeout=15)
                head.raise_for_status()

                task.total_size = int(head.headers.get("Content-Length", 0))
                accepts_ranges = head.headers.get("Accept-Ranges", "none").lower() == "bytes"

                task.start_time = time.time()
                task.status = DownloadStatus.DOWNLOADING

                if task.total_size > 0 and accepts_ranges and task.num_threads > 1:
                    self._segmented_download(task, session)
                else:
                    self._simple_download(task, session)

            if task.status == DownloadStatus.CANCELLED:
                self._cleanup_parts(task)
                return

            if any(s.status == DownloadStatus.FAILED for s in task.segments):
                raise RuntimeError(task.error or "One or more segments failed")

            self._merge_segments(task)
            task.status = DownloadStatus.COMPLETED
            task.end_time = time.time()

            if task.on_complete:
                task.on_complete(task)

        except Exception as e:
            task.status = DownloadStatus.FAILED
            task.error = str(e)
            task.end_time = time.time()
            if task.on_error:
                task.on_error(task)

    def _segmented_download(self, task: DownloadTask, session: requests.Session):
        seg_size = task.total_size // task.num_threads
        task.segments = []

        for i in range(task.num_threads):
            start = i * seg_size
            end = task.total_size - 1 if i == task.num_threads - 1 else (i + 1) * seg_size - 1
            task.segments.append(SegmentInfo(index=i, start=start, end=end))

        self._load_existing_parts(task)

        # requests.Session is not thread-safe; each segment worker gets its own session.
        workers = [SegmentDownloader(task, seg, requests.Session()) for seg in task.segments]
        for w in workers:
            w.start()
        for w in workers:
            w.join()

    def _simple_download(self, task: DownloadTask, session: requests.Session):
        seg = SegmentInfo(index=0, start=0, end=task.total_size - 1 if task.total_size else 0)
        task.segments = [seg]
        self._load_existing_parts(task)
        if task.total_size and seg.downloaded >= task.total_size:
            seg.downloaded = task.total_size
            seg.status = DownloadStatus.COMPLETED
            return
        seg.status = DownloadStatus.DOWNLOADING

        with session.get(task.url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            tmp_path = f"{task.dest_path}.part0"
            with open(tmp_path, "ab" if seg.downloaded else "wb") as f:
                for chunk in resp.iter_content(chunk_size=task.chunk_size):
                    task._pause_event.wait()
                    if task._cancel_event.is_set():
                        task.status = DownloadStatus.CANCELLED
                        return
                    if chunk:
                        f.write(chunk)
                        size = len(chunk)
                        seg.downloaded += size
                        with task._lock:
                            task.downloaded_bytes += size
                        if task.global_limiter:
                            task.global_limiter.wait(size)
                        task.throttle()
                        if task.on_progress:
                            task.on_progress(task)

        seg.status = DownloadStatus.COMPLETED

    def _load_existing_parts(self, task: DownloadTask):
        """Restore segment progress from .part files left by a previous run."""
        task.downloaded_bytes = 0
        for seg in task.segments:
            part_path = f"{task.dest_path}.part{seg.index}"
            if not os.path.exists(part_path):
                continue
            segment_size = seg.end - seg.start + 1 if task.total_size else os.path.getsize(part_path)
            existing_size = min(os.path.getsize(part_path), segment_size)
            if existing_size < os.path.getsize(part_path):
                with open(part_path, "ab") as part:
                    part.truncate(existing_size)
            seg.downloaded = existing_size
            if segment_size and existing_size >= segment_size:
                seg.status = DownloadStatus.COMPLETED
            task.downloaded_bytes += existing_size

    def _merge_segments(self, task: DownloadTask):
        with open(task.dest_path, "wb") as out:
            for seg in sorted(task.segments, key=lambda s: s.index):
                part_path = f"{task.dest_path}.part{seg.index}"
                with open(part_path, "rb") as part:
                    while True:
                        chunk = part.read(1024 * 256)
                        if not chunk:
                            break
                        out.write(chunk)
                os.remove(part_path)

    def _cleanup_parts(self, task: DownloadTask):
        for seg in task.segments:
            part_path = f"{task.dest_path}.part{seg.index}"
            if os.path.exists(part_path):
                os.remove(part_path)

    def pause(self, task: DownloadTask):
        task._pause_event.clear()
        task.status = DownloadStatus.PAUSED

    def resume(self, task: DownloadTask):
        task._pause_event.set()
        if task.status == DownloadStatus.PAUSED:
            task.status = DownloadStatus.DOWNLOADING

    def cancel(self, task: DownloadTask):
        task._cancel_event.set()
        task._pause_event.set()  # Unblock if paused
        task.status = DownloadStatus.CANCELLED

    def get_all_tasks(self) -> List[DownloadTask]:
        with self._lock:
            return list(self._tasks.values())
