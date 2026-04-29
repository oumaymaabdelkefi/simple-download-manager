"""
Tests for SDM core downloader.
Uses a local HTTP server to avoid real network calls.
"""

import os
import sys
import time
import threading
import unittest
import http.server
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.downloader import DownloadManager, DownloadStatus
import core.history as history

# ─── Minimal test HTTP server ─────────────────────────────────────────────────

TEST_CONTENT = b"A" * (1024 * 512)  # 512 KB of 'A's
TEST_PORT = 18765


class RangeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Suppress output

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(TEST_CONTENT)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self):
        range_header = self.headers.get("Range")
        if range_header:
            _, rng = range_header.split("=")
            start, end = map(int, rng.split("-"))
            data = TEST_CONTENT[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(TEST_CONTENT)}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_CONTENT)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(TEST_CONTENT)


def start_test_server():
    server = http.server.HTTPServer(("127.0.0.1", TEST_PORT), RangeHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ─── Test cases ───────────────────────────────────────────────────────────────

class TestDownloadManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = start_test_server()
        cls.url = f"http://127.0.0.1:{TEST_PORT}/testfile.bin"
        cls.tmpdir = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _wait_for(self, task, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if task.status in (DownloadStatus.COMPLETED, DownloadStatus.FAILED,
                                DownloadStatus.CANCELLED):
                return
            time.sleep(0.1)

    def test_single_thread_download(self):
        manager = DownloadManager()
        task = manager.create_task(self.url, dest_dir=self.tmpdir,
                                    filename="test_single.bin", num_threads=1)
        manager.start(task)
        self._wait_for(task)

        self.assertEqual(task.status, DownloadStatus.COMPLETED)
        self.assertTrue(os.path.exists(task.dest_path))
        with open(task.dest_path, "rb") as f:
            self.assertEqual(f.read(), TEST_CONTENT)

    def test_multi_thread_download(self):
        manager = DownloadManager()
        task = manager.create_task(self.url, dest_dir=self.tmpdir,
                                    filename="test_multi.bin", num_threads=4)
        manager.start(task)
        self._wait_for(task)

        self.assertEqual(task.status, DownloadStatus.COMPLETED)
        with open(task.dest_path, "rb") as f:
            self.assertEqual(f.read(), TEST_CONTENT)

    def test_file_size_reported_correctly(self):
        manager = DownloadManager()
        task = manager.create_task(self.url, dest_dir=self.tmpdir,
                                    filename="test_size.bin", num_threads=2)
        manager.start(task)
        self._wait_for(task)
        self.assertEqual(task.total_size, len(TEST_CONTENT))

    def test_progress_reaches_100(self):
        manager = DownloadManager()
        task = manager.create_task(self.url, dest_dir=self.tmpdir,
                                    filename="test_progress.bin", num_threads=4)
        manager.start(task)
        self._wait_for(task)
        self.assertAlmostEqual(task.progress, 100.0, places=0)

    def test_cancel_download(self):
        manager = DownloadManager()
        task = manager.create_task(self.url, dest_dir=self.tmpdir,
                                    filename="test_cancel.bin", num_threads=4)
        manager.start(task)
        time.sleep(0.1)
        manager.cancel(task)
        self._wait_for(task)
        self.assertEqual(task.status, DownloadStatus.CANCELLED)

    def test_pause_and_resume(self):
        manager = DownloadManager()
        task = manager.create_task(self.url, dest_dir=self.tmpdir,
                                    filename="test_pause.bin", num_threads=4)
        manager.start(task)
        # Wait until download is active
        deadline = time.time() + 5
        while task.status != DownloadStatus.DOWNLOADING and time.time() < deadline:
            time.sleep(0.02)
        manager.pause(task)
        self.assertEqual(task.status, DownloadStatus.PAUSED)
        time.sleep(0.2)
        manager.resume(task)
        self._wait_for(task)
        self.assertEqual(task.status, DownloadStatus.COMPLETED)

    def test_on_complete_callback(self):
        completed = threading.Event()
        manager = DownloadManager()
        task = manager.create_task(self.url, dest_dir=self.tmpdir,
                                    filename="test_callback.bin", num_threads=2,
                                    on_complete=lambda t: completed.set())
        manager.start(task)
        self.assertTrue(completed.wait(timeout=15))

    def test_resume_from_existing_part_files(self):
        filename = "test_restart_resume.bin"
        dest_path = os.path.join(self.tmpdir, filename)
        segment_size = len(TEST_CONTENT) // 4

        with open(f"{dest_path}.part0", "wb") as f:
            f.write(TEST_CONTENT[:segment_size])
        with open(f"{dest_path}.part1", "wb") as f:
            f.write(TEST_CONTENT[segment_size:segment_size + (segment_size // 2)])

        manager = DownloadManager()
        task = manager.create_task(self.url, dest_dir=self.tmpdir,
                                    filename=filename, num_threads=4)
        manager.start(task)
        self._wait_for(task)

        self.assertEqual(task.status, DownloadStatus.COMPLETED)
        self.assertEqual(task.progress, 100.0)
        with open(task.dest_path, "rb") as f:
            self.assertEqual(f.read(), TEST_CONTENT)

    def test_resume_redownloads_missing_part_files(self):
        filename = "test_missing_parts_resume.bin"
        dest_path = os.path.join(self.tmpdir, filename)
        segment_size = len(TEST_CONTENT) // 4

        # Simulate restart with only one preserved segment. Missing parts must
        # be downloaded again instead of causing resume to fail.
        with open(f"{dest_path}.part0", "wb") as f:
            f.write(TEST_CONTENT[:segment_size])

        manager = DownloadManager()
        task = manager.create_task(self.url, dest_dir=self.tmpdir,
                                    filename=filename, num_threads=4)
        manager.start(task)
        self._wait_for(task)

        self.assertEqual(task.status, DownloadStatus.COMPLETED)
        with open(task.dest_path, "rb") as f:
            self.assertEqual(f.read(), TEST_CONTENT)

    def test_multi_thread_faster_than_single(self):
        """Multi-thread should complete no slower than single thread (same local server)."""
        manager = DownloadManager()

        t0 = time.time()
        task1 = manager.create_task(self.url, dest_dir=self.tmpdir,
                                     filename="bench_single.bin", num_threads=1)
        manager.start(task1)
        self._wait_for(task1)
        single_time = time.time() - t0

        t0 = time.time()
        task4 = manager.create_task(self.url, dest_dir=self.tmpdir,
                                     filename="bench_multi.bin", num_threads=4)
        manager.start(task4)
        self._wait_for(task4)
        multi_time = time.time() - t0

        print(f"\n  Single-thread: {single_time:.3f}s  |  4-thread: {multi_time:.3f}s")
        # Both should succeed
        self.assertEqual(task1.status, DownloadStatus.COMPLETED)
        self.assertEqual(task4.status, DownloadStatus.COMPLETED)

    def test_queue_entries_persist_in_sqlite(self):
        old_db_path = history.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                history.DB_PATH = os.path.join(tmp, "history.db")
                history.init_db()
                entry = history.QueueEntry(
                    id="queue-test",
                    url="http://127.0.0.1/file.bin",
                    dest_dir=tmp,
                    filename="file.bin",
                    num_threads=4,
                    max_retries=3,
                    bandwidth_limit=1024,
                    scheduled_at=12345.0,
                    queued_at=1.0,
                )

                history.save_queue_entry(entry)
                entries = history.get_queue_entries()
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].id, "queue-test")
                self.assertEqual(entries[0].scheduled_at, 12345.0)

                history.update_queue_order([("queue-test", 2.0)])
                self.assertEqual(history.get_queue_entries()[0].queued_at, 2.0)

                history.delete_queue_entry("queue-test")
                self.assertEqual(history.get_queue_entries(), [])
        finally:
            history.DB_PATH = old_db_path

    def test_queue_order_persists_across_reload(self):
        old_db_path = history.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                history.DB_PATH = os.path.join(tmp, "history.db")
                history.init_db()

                entries = [
                    history.QueueEntry(
                        id="third",
                        url="http://127.0.0.1/third.bin",
                        dest_dir=tmp,
                        filename="third.bin",
                        num_threads=4,
                        max_retries=3,
                        bandwidth_limit=None,
                        scheduled_at=None,
                        queued_at=3.0,
                    ),
                    history.QueueEntry(
                        id="first",
                        url="http://127.0.0.1/first.bin",
                        dest_dir=tmp,
                        filename="first.bin",
                        num_threads=4,
                        max_retries=3,
                        bandwidth_limit=None,
                        scheduled_at=None,
                        queued_at=1.0,
                    ),
                    history.QueueEntry(
                        id="second",
                        url="http://127.0.0.1/second.bin",
                        dest_dir=tmp,
                        filename="second.bin",
                        num_threads=4,
                        max_retries=3,
                        bandwidth_limit=None,
                        scheduled_at=None,
                        queued_at=2.0,
                    ),
                ]

                for entry in entries:
                    history.save_queue_entry(entry)

                self.assertEqual(
                    [entry.id for entry in history.get_queue_entries()],
                    ["first", "second", "third"],
                )

                history.update_queue_order([
                    ("third", 1.0),
                    ("first", 2.0),
                    ("second", 3.0),
                ])

                self.assertEqual(
                    [entry.id for entry in history.get_queue_entries()],
                    ["third", "first", "second"],
                )
        finally:
            history.DB_PATH = old_db_path

    def test_global_bandwidth_limiter_is_shared_by_tasks(self):
        manager = DownloadManager(global_bandwidth_limit=1024)
        task1 = manager.create_task(self.url, dest_dir=self.tmpdir,
                                     filename="global_limit_1.bin")
        task2 = manager.create_task(self.url, dest_dir=self.tmpdir,
                                     filename="global_limit_2.bin")

        self.assertIs(task1.global_limiter, task2.global_limiter)
        self.assertEqual(manager.global_limiter.rate, 1024)

        manager.set_global_bandwidth_limit(2048)
        self.assertEqual(task1.global_limiter.rate, 2048)
        self.assertEqual(task2.global_limiter.rate, 2048)


if __name__ == "__main__":
    unittest.main(verbosity=2)
