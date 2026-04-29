#!/usr/bin/env python3
"""
SDM - Simple Download Manager
Command-line interface
"""

import sys
import os
import time
import argparse
import threading
from datetime import datetime

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.downloader import DownloadManager, DownloadTask, DownloadStatus
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


def format_size(b: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def format_time(secs: float) -> str:
    if secs < 60:
        return f"{secs:.0f}s"
    m, s = divmod(secs, 60)
    return f"{int(m)}m {int(s)}s"


def print_progress(task: DownloadTask):
    bar_len = 30
    filled = int(bar_len * task.progress / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    speed_str = format_size(int(task.speed)) + "/s"
    eta_str = format_time(task.eta) if task.eta else "?"
    print(
        f"\r  [{bar}] {task.progress:5.1f}%  {format_size(task.downloaded_bytes)}"
        f"/{format_size(task.total_size)}  {speed_str}  ETA: {eta_str}   ",
        end="",
        flush=True,
    )


def cmd_download(args):
    manager = DownloadManager()
    done_event = threading.Event()
    row_id = None
    last_history_update = 0.0

    def on_complete(task: DownloadTask):
        print(f"\n✅ Done: {task.dest_path}  ({format_size(task.total_size)})")
        done_event.set()

    def on_error(task: DownloadTask):
        print(f"\n❌ Error: {task.error}")
        done_event.set()

    def on_progress(task: DownloadTask):
        nonlocal last_history_update
        print_progress(task)
        if row_id and time.time() - last_history_update >= 1.0:
            update_download(row_id, task)
            last_history_update = time.time()

    task = manager.create_task(
        url=args.url,
        dest_dir=args.output,
        filename=args.filename,
        num_threads=args.threads,
        max_retries=args.retries,
        bandwidth_limit=int(args.bandwidth * 1024 * 1024) if args.bandwidth else None,
        on_progress=on_progress,
        on_complete=on_complete,
        on_error=on_error,
    )

    row_id = save_download(task)
    print(f"⬇  Downloading: {args.url}")
    print(f"   Threads: {args.threads}  |  Max retries: {args.retries}")
    if args.bandwidth:
        print(f"   Bandwidth limit: {args.bandwidth:g} MB/s")
    print(f"   Saving to: {task.dest_path}\n")

    manager.start(task)

    try:
        while not done_event.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n\n⏸  Paused. Press Enter to resume or Ctrl+C again to cancel...")
        manager.pause(task)
        try:
            input()
            manager.resume(task)
            done_event.wait()
        except KeyboardInterrupt:
            print("\n🚫 Cancelled.")
            manager.cancel(task)

    update_download(row_id, task)


def cmd_history(args):
    entries = get_history(limit=args.limit)
    if not entries:
        print("No download history found.")
        return

    print(f"\n{'#':<4} {'Filename':<30} {'Size':>10} {'Status':<12} {'Date':<20}")
    print("─" * 80)
    for e in entries:
        size_str = format_size(e.total_size) if e.total_size else "—"
        date_str = (e.start_time or "—")[:19]
        status_icon = {"completed": "✅", "failed": "❌", "cancelled": "🚫"}.get(e.status, "⏳")
        print(f"{e.id:<4} {e.filename or '—':<30} {size_str:>10} {status_icon} {e.status:<10} {date_str}")

    if args.clear:
        clear_history()
        print("\nHistory cleared.")


def parse_schedule(value: str | None) -> float | None:
    if not value:
        return None
    return datetime.fromisoformat(value).timestamp()


def cmd_queue(args):
    if args.queue_command == "add":
        entry = QueueEntry(
            id=str(int(time.time() * 1000)),
            url=args.url,
            dest_dir=os.path.abspath(os.path.expanduser(args.output)),
            filename=args.filename,
            num_threads=args.threads,
            max_retries=args.retries,
            bandwidth_limit=int(args.bandwidth * 1024 * 1024) if args.bandwidth else None,
            scheduled_at=parse_schedule(args.schedule),
            queued_at=time.time(),
        )
        save_queue_entry(entry)
        print(f"Queued: {entry.filename or entry.url}")
        return

    entries = get_queue_entries()

    if args.queue_command == "list":
        if not entries:
            print("Queue is empty.")
            return
        print(f"\n{'ID':<15} {'Filename/URL':<35} {'Threads':>7} {'Schedule':<20}")
        print("─" * 85)
        for entry in entries:
            name = entry.filename or entry.url
            schedule = datetime.fromtimestamp(entry.scheduled_at).isoformat(timespec="minutes") if entry.scheduled_at else "asap"
            print(f"{entry.id:<15} {name[:35]:<35} {entry.num_threads:>7} {schedule:<20}")
        return

    selected = next((entry for entry in entries if entry.id == args.id), None) if getattr(args, "id", None) else (entries[0] if entries else None)
    if selected is None:
        print("No matching queued download found.")
        return

    if args.queue_command == "remove":
        delete_queue_entry(selected.id)
        print(f"Removed queued download: {selected.id}")
        return

    if args.queue_command in ("up", "down"):
        index = entries.index(selected)
        new_index = index - 1 if args.queue_command == "up" else index + 1
        new_index = max(0, min(len(entries) - 1, new_index))
        entries[index], entries[new_index] = entries[new_index], entries[index]
        base = time.time()
        update_queue_order([(entry.id, base + offset / 1000) for offset, entry in enumerate(entries)])
        print(f"Moved queued download {args.queue_command}: {selected.id}")
        return

    if args.queue_command == "start":
        if selected.scheduled_at and selected.scheduled_at > time.time() and not args.force:
            print("Queued download is scheduled for later. Use --force to start now.")
            return
        delete_queue_entry(selected.id)
        download_args = argparse.Namespace(
            url=selected.url,
            output=selected.dest_dir,
            filename=selected.filename,
            threads=selected.num_threads,
            retries=selected.max_retries,
            bandwidth=(selected.bandwidth_limit / 1024 / 1024) if selected.bandwidth_limit else 0,
        )
        cmd_download(download_args)


def main():
    parser = argparse.ArgumentParser(
        prog="sdm",
        description="🚀 Simple Download Manager — multi-threaded segmented downloader",
    )
    sub = parser.add_subparsers(dest="command")

    # Download subcommand
    dl = sub.add_parser("download", aliases=["dl"], help="Download a file")
    dl.add_argument("url", help="URL to download")
    dl.add_argument("-o", "--output", default=".", help="Output directory (default: current dir)")
    dl.add_argument("-f", "--filename", default=None, help="Override filename")
    dl.add_argument("-t", "--threads", type=int, default=4, help="Number of threads (default: 4)")
    dl.add_argument("-r", "--retries", type=int, default=3, help="Max retries per segment (default: 3)")
    dl.add_argument("-b", "--bandwidth", type=float, default=0, help="Bandwidth limit in MB/s (default: unlimited)")

    # History subcommand
    hist = sub.add_parser("history", aliases=["hist"], help="Show download history")
    hist.add_argument("-n", "--limit", type=int, default=20, help="Number of entries to show")
    hist.add_argument("--clear", action="store_true", help="Clear all history")

    # Queue subcommand
    queue = sub.add_parser("queue", aliases=["q"], help="Manage persistent download queue")
    queue_sub = queue.add_subparsers(dest="queue_command")

    queue_sub.add_parser("list", aliases=["ls"], help="List queued downloads")

    q_add = queue_sub.add_parser("add", help="Add a download to the persistent queue")
    q_add.add_argument("url", help="URL to queue")
    q_add.add_argument("-o", "--output", default=".", help="Output directory (default: current dir)")
    q_add.add_argument("-f", "--filename", default=None, help="Override filename")
    q_add.add_argument("-t", "--threads", type=int, default=4, help="Number of threads (default: 4)")
    q_add.add_argument("-r", "--retries", type=int, default=3, help="Max retries per segment (default: 3)")
    q_add.add_argument("-b", "--bandwidth", type=float, default=0, help="Bandwidth limit in MB/s (default: unlimited)")
    q_add.add_argument("--schedule", default=None, help="ISO datetime, e.g. 2026-04-29T15:30")

    q_remove = queue_sub.add_parser("remove", aliases=["rm"], help="Remove a queued download")
    q_remove.add_argument("id", help="Queue entry ID")

    q_up = queue_sub.add_parser("up", help="Move a queued download up")
    q_up.add_argument("id", help="Queue entry ID")

    q_down = queue_sub.add_parser("down", help="Move a queued download down")
    q_down.add_argument("id", help="Queue entry ID")

    q_start = queue_sub.add_parser("start", help="Start a queued download")
    q_start.add_argument("id", nargs="?", help="Queue entry ID; defaults to first item")
    q_start.add_argument("--force", action="store_true", help="Start even if scheduled for later")

    args = parser.parse_args()

    if args.command in ("download", "dl"):
        cmd_download(args)
    elif args.command in ("history", "hist"):
        cmd_history(args)
    elif args.command in ("queue", "q"):
        if args.queue_command == "ls":
            args.queue_command = "list"
        elif args.queue_command == "rm":
            args.queue_command = "remove"
        cmd_queue(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
