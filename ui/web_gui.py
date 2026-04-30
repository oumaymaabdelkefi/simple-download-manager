#!/usr/bin/env python3
"""
SDM - Simple Download Manager webview UI.
"""

import os
import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Any

try:
    import webview
except ImportError:  # pragma: no cover - handled at runtime for users
    webview = None

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.downloader import DownloadManager, DownloadStatus, DownloadTask
from core.history import (
    QueueEntry,
    clear_history,
    delete_queue_entry,
    get_history,
    get_queue_entries,
    save_download,
    save_queue_entry,
    update_download,
    update_queue_order,
)


class SDMWebApi:
    def __init__(self):
        self.manager = DownloadManager()
        self._lock = threading.Lock()
        self._downloads: dict[str, dict[str, Any]] = {}
        self._task_ids: dict[int, str] = {}
        self._last_progress_save: dict[int, float] = {}
        self.max_active_downloads = 2
        self._load_persisted_queue()

    def add_download(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = str(payload.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "error": "Please enter a valid http(s) URL."}

        dest_dir = self._expand_path(str(payload.get("dest_dir") or "~/Downloads"))
        filename = str(payload.get("filename") or "").strip() or None
        threads = self._clamp_int(payload.get("threads"), 1, 16, 4)
        retries = self._clamp_int(payload.get("retries"), 0, 10, 3)
        bandwidth_limit = self._parse_bandwidth_limit(payload.get("bandwidth_limit"))
        download_id = uuid.uuid4().hex[:10]
        try:
            scheduled_at = self._parse_schedule(payload.get("schedule"))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        should_queue = (scheduled_at and scheduled_at > time.time()) or self._active_count() >= self.max_active_downloads
        if should_queue:
            queued_at = time.time()
            item = self._queue_item(
                url=url,
                dest_dir=dest_dir,
                filename=filename,
                threads=threads,
                retries=retries,
                bandwidth_limit=bandwidth_limit,
                scheduled_at=scheduled_at,
                queued_at=queued_at,
            )
            with self._lock:
                self._downloads[download_id] = item
            self._persist_queue_item(download_id, item)
            if scheduled_at and scheduled_at > time.time():
                self._schedule_download_start(download_id, scheduled_at)
            return {
                "ok": True,
                "id": download_id,
                "filename": filename or self._filename_from_url(url),
                "scheduled": bool(scheduled_at and scheduled_at > time.time()),
                "queued": True,
            }

        try:
            task = self._create_task(url, dest_dir, filename, threads, retries, bandwidth_limit)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        row_id = save_download(task)
        with self._lock:
            self._downloads[download_id] = {"task": task, "row_id": row_id, "visible": True}
            self._task_ids[id(task)] = download_id

        self.manager.start(task)
        return {"ok": True, "id": download_id, "filename": task.filename}

    def pause_download(self, download_id: str) -> dict[str, Any]:
        task = self._get_task(download_id)
        if task and task.status == DownloadStatus.DOWNLOADING:
            self.manager.pause(task)
        return {"ok": True}

    def resume_download(self, download_id: str) -> dict[str, Any]:
        task = self._get_task(download_id)
        if task and task.status == DownloadStatus.PAUSED:
            self.manager.resume(task)
        return {"ok": True}

    def cancel_download(self, download_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._downloads.get(download_id)
            task = item.get("task") if item else None
            if item and task is None:
                item["status"] = DownloadStatus.CANCELLED.value
                delete_queue_entry(download_id)
        if task and task.status not in (DownloadStatus.COMPLETED, DownloadStatus.CANCELLED, DownloadStatus.FAILED):
            self.manager.cancel(task)
        self._start_next_queued()
        return {"ok": True}

    def remove_download(self, download_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._downloads.get(download_id)
            if item:
                task = item["task"]
                if task and task.status not in (DownloadStatus.COMPLETED, DownloadStatus.CANCELLED, DownloadStatus.FAILED):
                    self.manager.cancel(task)
                item["visible"] = False
                if task is None:
                    delete_queue_entry(download_id)
        self._start_next_queued()
        return {"ok": True}

    def pause_all(self) -> dict[str, Any]:
        with self._lock:
            tasks = [item["task"] for item in self._downloads.values() if item["visible"] and item["task"]]
        for task in tasks:
            if task.status == DownloadStatus.DOWNLOADING:
                self.manager.pause(task)
        return {"ok": True}

    def resume_all(self) -> dict[str, Any]:
        with self._lock:
            tasks = [item["task"] for item in self._downloads.values() if item["visible"] and item["task"]]
        for task in tasks:
            if task.status == DownloadStatus.PAUSED:
                self.manager.resume(task)
        return {"ok": True}

    def clear_completed(self) -> dict[str, Any]:
        with self._lock:
            for item in self._downloads.values():
                task = item.get("task")
                if task and task.status in (DownloadStatus.COMPLETED, DownloadStatus.CANCELLED, DownloadStatus.FAILED):
                    item["visible"] = False
        return {"ok": True}

    def clear_history(self) -> dict[str, Any]:
        clear_history()
        return {"ok": True}

    def set_global_bandwidth_limit(self, value: Any) -> dict[str, Any]:
        limit = self._parse_bandwidth_limit(value)
        self.manager.set_global_bandwidth_limit(limit)
        return {"ok": True, "global_bandwidth_limit": limit}

    def start_queued(self, download_id: str) -> dict[str, Any]:
        return self._start_queued_download(download_id, force=True)

    def move_queue_up(self, download_id: str) -> dict[str, Any]:
        self._move_queue_item(download_id, -1)
        return {"ok": True}

    def move_queue_down(self, download_id: str) -> dict[str, Any]:
        self._move_queue_item(download_id, 1)
        return {"ok": True}

    def paste_clipboard(self) -> dict[str, Any]:
        try:
            return {"ok": True, "text": self._read_clipboard()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def choose_directory(self, initial_dir: str = "~/Downloads") -> dict[str, Any]:
        try:
            path = self._choose_directory(initial_dir)
            return {"ok": True, "path": path}
        except subprocess.CalledProcessError as exc:
            if sys.platform == "darwin" and exc.returncode == 1:
                return {"ok": True, "path": ""}
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def resume_history(self, row_id: int) -> dict[str, Any]:
        return self._start_from_history(row_id, mode="resume")

    def retry_history(self, row_id: int) -> dict[str, Any]:
        return self._start_from_history(row_id, mode="retry")

    def retry_history_more_threads(self, row_id: int) -> dict[str, Any]:
        return self._start_from_history(row_id, mode="more_threads")

    def retry_history_from_parts(self, row_id: int) -> dict[str, Any]:
        return self._start_from_history(row_id, mode="resume")

    def open_history_file(self, row_id: int) -> dict[str, Any]:
        entry = self._get_history_entry(row_id)
        if entry is None:
            return {"ok": False, "error": "History entry not found."}
        return self._open_path(entry.dest_path)

    def open_history_folder(self, row_id: int) -> dict[str, Any]:
        entry = self._get_history_entry(row_id)
        if entry is None:
            return {"ok": False, "error": "History entry not found."}
        return self._open_path(os.path.dirname(entry.dest_path) or ".")

    def copy_history_path(self, row_id: int) -> dict[str, Any]:
        entry = self._get_history_entry(row_id)
        if entry is None:
            return {"ok": False, "error": "History entry not found."}
        try:
            self._write_clipboard(entry.dest_path)
            return {"ok": True, "path": entry.dest_path}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _start_from_history(self, row_id: int, mode: str) -> dict[str, Any]:
        entry = self._get_history_entry(row_id)
        if entry is None:
            return {"ok": False, "error": "History entry not found."}

        download_id = uuid.uuid4().hex[:10]
        threads = entry.num_threads
        if mode == "more_threads":
            threads = min(16, max(entry.num_threads + 2, entry.num_threads * 2))
        if mode in ("retry", "more_threads"):
            self._delete_part_files(entry.dest_path)

        try:
            task = self._create_task(
                entry.url,
                os.path.dirname(entry.dest_path) or ".",
                entry.filename,
                threads,
                3,
                None,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        row_id = save_download(task)
        with self._lock:
            self._downloads[download_id] = {"task": task, "row_id": row_id, "visible": True}
            self._task_ids[id(task)] = download_id
        self.manager.start(task)
        return {"ok": True, "id": download_id, "filename": task.filename, "threads": threads}

    def _get_history_entry(self, row_id: int):
        return next((item for item in get_history(1000) if item.id == int(row_id)), None)

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            downloads = [
                self._serialize_item(download_id, item)
                for download_id, item in self._downloads.items()
                if item["visible"]
            ]
        queue = sorted(
            [item for item in downloads if item["status"] == DownloadStatus.PENDING.value],
            key=lambda item: item.get("queued_at") or 0,
        )
        return {
            "downloads": downloads,
            "queue": queue,
            "max_active_downloads": self.max_active_downloads,
            "global_bandwidth_limit": self.manager.global_limiter.rate,
            "history": [self._serialize_history(entry) for entry in get_history(100)],
        }

    def _start_scheduled_download(self, download_id: str):
        with self._lock:
            item = self._downloads.get(download_id)
            if not item or not item["visible"] or item.get("status") != DownloadStatus.PENDING.value:
                return
        self._start_queued_download(download_id)

    def _schedule_download_start(self, download_id: str, scheduled_at: float):
        delay = max(0.0, min(scheduled_at - time.time(), 24 * 60 * 60))
        callback = self._start_scheduled_download
        if scheduled_at - time.time() > delay:
            callback = lambda: self._schedule_download_start(download_id, scheduled_at)
        timer = threading.Timer(delay, callback)
        timer.daemon = True
        timer.start()

    def _create_task(self, url: str, dest_dir: str, filename: str | None, threads: int, retries: int, bandwidth_limit: int | None) -> DownloadTask:
        return self.manager.create_task(
            url=url,
            dest_dir=dest_dir,
            filename=filename,
            num_threads=threads,
            max_retries=retries,
            bandwidth_limit=bandwidth_limit,
            on_progress=self._on_progress,
            on_complete=self._on_complete,
            on_error=self._on_error,
        )

    def _on_progress(self, task: DownloadTask):
        now = time.time()
        task_id = id(task)
        if now - self._last_progress_save.get(task_id, 0) < 1.0:
            return
        self._last_progress_save[task_id] = now
        self._update_history_row(task)

    def _on_complete(self, task: DownloadTask):
        self._update_history_row(task)
        self._start_next_queued()

    def _on_error(self, task: DownloadTask):
        self._update_history_row(task)
        self._start_next_queued()

    def _active_count(self) -> int:
        with self._lock:
            return sum(
                1
                for item in self._downloads.values()
                if item.get("visible")
                and item.get("task")
                and item["task"].status == DownloadStatus.DOWNLOADING
            )

    def _start_next_queued(self):
        while self._active_count() < self.max_active_downloads:
            with self._lock:
                now = time.time()
                candidates = sorted(
                    (
                        (download_id, item)
                        for download_id, item in self._downloads.items()
                        if item.get("visible")
                        and item.get("task") is None
                        and item.get("status") == DownloadStatus.PENDING.value
                        and (not item.get("scheduled_at") or item["scheduled_at"] <= now)
                    ),
                    key=lambda pair: pair[1].get("queued_at", 0),
                )
                next_id = candidates[0][0] if candidates else None
            if not next_id:
                return
            result = self._start_queued_download(next_id)
            if not result.get("ok"):
                return

    def _start_queued_download(self, download_id: str, force: bool = False) -> dict[str, Any]:
        if not force and self._active_count() >= self.max_active_downloads:
            return {"ok": False, "error": "Active download limit reached."}

        with self._lock:
            item = self._downloads.get(download_id)
            if not item or not item.get("visible"):
                return {"ok": False, "error": "Queued download not found."}
            if item.get("task") is not None:
                return {"ok": True}
            if item.get("status") != DownloadStatus.PENDING.value:
                return {"ok": False, "error": "Queued download is not pending."}
            if not force and item.get("scheduled_at") and item["scheduled_at"] > time.time():
                return {"ok": False, "error": "Download is scheduled for later."}
            payload = item["payload"]

        try:
            task = self._create_task(
                payload["url"],
                payload["dest_dir"],
                payload["filename"],
                payload["threads"],
                payload["retries"],
                payload.get("bandwidth_limit"),
            )
            row_id = save_download(task)
        except Exception as exc:
            with self._lock:
                item = self._downloads.get(download_id)
                if item:
                    item["status"] = DownloadStatus.FAILED.value
            return {"ok": False, "error": str(exc)}

        with self._lock:
            item = self._downloads.get(download_id)
            if not item:
                return {"ok": False, "error": "Queued download not found."}
            item["task"] = task
            item["row_id"] = row_id
            self._task_ids[id(task)] = download_id
        delete_queue_entry(download_id)
        self.manager.start(task)
        return {"ok": True, "id": download_id, "filename": task.filename}

    def _move_queue_item(self, download_id: str, direction: int):
        with self._lock:
            queue = sorted(
                [
                    (item_id, item)
                    for item_id, item in self._downloads.items()
                    if item.get("visible") and item.get("task") is None and item.get("status") == DownloadStatus.PENDING.value
                ],
                key=lambda pair: pair[1].get("queued_at", 0),
            )
            index = next((i for i, (item_id, _) in enumerate(queue) if item_id == download_id), None)
            if index is None:
                return
            new_index = max(0, min(len(queue) - 1, index + direction))
            if new_index == index:
                return
            queue[index], queue[new_index] = queue[new_index], queue[index]
            base = time.time()
            persisted_order = []
            for offset, (_, item) in enumerate(queue):
                item["queued_at"] = base + offset / 1000
            for _, item in queue:
                item_id = next(k for k, v in self._downloads.items() if v is item)
                persisted_order.append((item_id, item["queued_at"]))
        update_queue_order(persisted_order)

    def _queue_item(
        self,
        url: str,
        dest_dir: str,
        filename: str | None,
        threads: int,
        retries: int,
        bandwidth_limit: int | None,
        scheduled_at: float | None,
        queued_at: float,
    ) -> dict[str, Any]:
        return {
            "task": None,
            "row_id": None,
            "visible": True,
            "payload": {
                "url": url,
                "dest_dir": dest_dir,
                "filename": filename,
                "threads": threads,
                "retries": retries,
                "bandwidth_limit": bandwidth_limit,
            },
            "scheduled_at": scheduled_at,
            "queued_at": queued_at,
            "status": DownloadStatus.PENDING.value,
        }

    def _persist_queue_item(self, download_id: str, item: dict[str, Any]):
        payload = item["payload"]
        save_queue_entry(
            QueueEntry(
                id=download_id,
                url=payload["url"],
                dest_dir=payload["dest_dir"],
                filename=payload["filename"],
                num_threads=payload["threads"],
                max_retries=payload["retries"],
                bandwidth_limit=payload.get("bandwidth_limit"),
                scheduled_at=item.get("scheduled_at"),
                queued_at=item["queued_at"],
            )
        )

    def _load_persisted_queue(self):
        for entry in get_queue_entries():
            self._downloads[entry.id] = self._queue_item(
                url=entry.url,
                dest_dir=entry.dest_dir,
                filename=entry.filename,
                threads=entry.num_threads,
                retries=entry.max_retries,
                bandwidth_limit=entry.bandwidth_limit,
                scheduled_at=entry.scheduled_at,
                queued_at=entry.queued_at,
            )
            if entry.scheduled_at and entry.scheduled_at > time.time():
                self._schedule_download_start(entry.id, entry.scheduled_at)
        self._start_next_queued()

    def _update_history_row(self, task: DownloadTask):
        with self._lock:
            download_id = self._task_ids.get(id(task))
            item = self._downloads.get(download_id) if download_id else None
            row_id = item.get("row_id") if item else None
        if row_id:
            update_download(row_id, task)

    def _get_task(self, download_id: str) -> DownloadTask | None:
        with self._lock:
            item = self._downloads.get(download_id)
            return item["task"] if item else None

    def _serialize_item(self, download_id: str, item: dict[str, Any]) -> dict[str, Any]:
        task = item.get("task")
        if task:
            data = self._serialize_task(download_id, task)
            data["history_id"] = item.get("row_id")
            data["scheduled_at"] = item.get("scheduled_at")
            return data

        payload = item.get("payload", {})
        return {
            "id": download_id,
            "url": payload.get("url", ""),
            "filename": payload.get("filename") or self._filename_from_url(payload.get("url", "")),
            "dest_path": os.path.join(payload.get("dest_dir", ""), payload.get("filename") or self._filename_from_url(payload.get("url", ""))),
            "status": item.get("status", DownloadStatus.PENDING.value),
            "total_size": 0,
            "downloaded_bytes": 0,
            "progress": 0,
            "speed": 0,
            "eta": None,
            "num_threads": payload.get("threads", 4),
            "max_retries": payload.get("retries", 3),
            "bandwidth_limit": payload.get("bandwidth_limit"),
            "error": None,
            "segments": [],
            "scheduled_at": item.get("scheduled_at"),
            "queued_at": item.get("queued_at"),
        }

    def _serialize_task(self, download_id: str, task: DownloadTask) -> dict[str, Any]:
        return {
            "id": download_id,
            "url": task.url,
            "filename": task.filename,
            "dest_path": task.dest_path,
            "status": task.status.value,
            "total_size": task.total_size,
            "downloaded_bytes": task.downloaded_bytes,
            "progress": task.progress,
            "speed": task.speed,
            "eta": task.eta,
            "num_threads": task.num_threads,
            "max_retries": task.max_retries,
            "bandwidth_limit": task.bandwidth_limit,
            "error": task.error,
            "segments": [
                {
                    "index": segment.index,
                    "downloaded": segment.downloaded,
                    "status": segment.status.value,
                }
                for segment in task.segments
            ],
        }

    def _serialize_history(self, entry) -> dict[str, Any]:
        return {
            "id": entry.id,
            "url": entry.url,
            "filename": entry.filename or "—",
            "dest_path": entry.dest_path,
            "total_size": entry.total_size or 0,
            "downloaded_bytes": entry.downloaded_bytes or 0,
            "status": entry.status,
            "start_time": (entry.start_time or "—")[:19],
            "end_time": (entry.end_time or "—")[:19],
            "num_threads": entry.num_threads,
            "error": entry.error,
            "segments": json.loads(entry.segments_json or "[]"),
        }

    def _expand_path(self, path: str) -> str:
        return os.path.abspath(os.path.expanduser(path or "~/Downloads"))

    def _filename_from_url(self, url: str) -> str:
        return url.split("/")[-1].split("?")[0] or "download"

    def _parse_schedule(self, value: Any) -> float | None:
        value = str(value or "").strip()
        if not value:
            return None
        normalized = value.replace("Z", "+00:00")
        candidates = [normalized]
        if "T" in normalized:
            candidates.append(normalized.replace("T", " "))
        formats = [
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%m/%d/%Y %H:%M",
            "%m/%d/%Y %I:%M %p",
            "%Y-%m-%d",
            "%m/%d/%Y",
        ]
        parsed = None
        for candidate in candidates:
            try:
                parsed = datetime.fromisoformat(candidate)
                break
            except ValueError:
                pass
            for fmt in formats:
                try:
                    parsed = datetime.strptime(candidate, fmt)
                    break
                except ValueError:
                    pass
            if parsed is not None:
                break
        if parsed is None:
            raise ValueError("Schedule must be a valid future date and time.")
        timestamp = parsed.timestamp()
        if timestamp <= time.time():
            raise ValueError("Schedule time must be in the future.")
        return timestamp

    def _parse_bandwidth_limit(self, value: Any) -> int | None:
        try:
            mbps = float(value or 0)
        except (TypeError, ValueError):
            return None
        if mbps <= 0:
            return None
        return int(mbps * 1024 * 1024)

    def _read_clipboard(self) -> str:
        if sys.platform == "darwin":
            return subprocess.check_output(["pbpaste"], text=True).strip()
        if sys.platform.startswith("win"):
            command = ["powershell", "-NoProfile", "-Command", "Get-Clipboard"]
            return subprocess.check_output(command, text=True).strip()
        for command in (["wl-paste"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]):
            try:
                return subprocess.check_output(command, text=True).strip()
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        raise RuntimeError("No supported clipboard reader found.")

    def _write_clipboard(self, text: str):
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
            return
        if sys.platform.startswith("win"):
            command = ["powershell", "-NoProfile", "-Command", "Set-Clipboard"]
            subprocess.run(command, input=text, text=True, check=True)
            return
        for command in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
            try:
                subprocess.run(command, input=text, text=True, check=True)
                return
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        raise RuntimeError("No supported clipboard writer found.")

    def _open_path(self, path: str) -> dict[str, Any]:
        if not os.path.exists(path):
            return {"ok": False, "error": "Path does not exist."}
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", path])
            elif sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
            return {"ok": True, "path": path}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _choose_directory(self, initial_dir: str) -> str:
        initial_dir = self._expand_path(initial_dir)
        if sys.platform == "darwin":
            escaped_dir = initial_dir.replace('"', '\\"')
            script = (
                'POSIX path of (choose folder with prompt "Choose download folder" '
                f'default location (POSIX file "{escaped_dir}" as alias))'
            )
            return subprocess.check_output(["osascript", "-e", script], text=True).strip()

        if sys.platform.startswith("win"):
            script = """
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.SelectedPath = $args[0]
if ($dialog.ShowDialog() -eq 'OK') { $dialog.SelectedPath }
"""
            return subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", script, initial_dir],
                text=True,
            ).strip()

        for command in (["zenity", "--file-selection", "--directory", f"--filename={initial_dir}/"], ["kdialog", "--getexistingdirectory", initial_dir]):
            try:
                return subprocess.check_output(command, text=True).strip()
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        raise RuntimeError("No supported folder picker found.")

    def _delete_part_files(self, dest_path: str):
        directory = os.path.dirname(dest_path) or "."
        basename = os.path.basename(dest_path)
        if not os.path.isdir(directory):
            return
        for name in os.listdir(directory):
            if name.startswith(f"{basename}.part"):
                try:
                    os.remove(os.path.join(directory, name))
                except OSError:
                    pass

    def _clamp_int(self, value: Any, low: int, high: int, default: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return max(low, min(high, number))


def main():
    if webview is None:
        raise SystemExit("pywebview is not installed. Run: pip install -r requirements.txt")

    html_path = os.path.join(os.path.dirname(__file__), "assets", "sdm_ui.html")
    api = SDMWebApi()
    webview.create_window(
        "SDM — Simple Download Manager",
        html_path,
        js_api=api,
        width=1200,
        height=820,
        min_size=(900, 640),
    )
    webview.start()


if __name__ == "__main__":
    main()
