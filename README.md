# SDM - Simple Download Manager

SDM is a Python download manager with segmented multi-threaded downloads, pause/resume, retry handling, persistent history, scheduled queues, bandwidth limiting, and a modern browser UI.

It is built as a small IDM/XDM-style project for both terminal and desktop/browser workflows.

## Highlights

- Multi-threaded segmented downloads using HTTP `Range` requests
- Pause, resume, cancel, retry, and resume-after-restart support
- Per-download and global bandwidth limits
- Persistent SQLite download history and queue storage
- Browser UI with live dashboard, queue tab, history tools, toasts, and filters
- CLI for downloads, history, and queue management
- Tkinter GUI and pywebview desktop UI options
- Unit tests with a local threaded HTTP test server

## Features

| Area | Feature | Status |
|---|---|---|
| Core | Download files from URL | Done |
| Core | Multi-threaded segmented download | Done |
| Core | HTTP range request support | Done |
| Core | Single-thread fallback when ranges are unavailable | Done |
| Control | Pause / resume | Done |
| Control | Cancel download | Done |
| Reliability | Automatic retry with exponential backoff | Done |
| Reliability | Resume after application restart from `.partN` files | Done |
| Reliability | Redownload missing segment files during resume | Done |
| Performance | Per-download bandwidth cap | Done |
| Performance | Global shared bandwidth cap | Done |
| Persistence | SQLite download history | Done |
| Persistence | Live downloaded bytes and segment progress metadata | Done |
| Queue | Persistent scheduled queue | Done |
| Queue | Reorder, start, cancel, and remove queued downloads | Done |
| CLI | Download, history, and queue commands | Done |
| UI | Browser UI | Done |
| UI | Desktop webview UI | Done |
| UI | Classic Tkinter GUI | Done |
| UI | Dashboard summary cards | Done |
| UI | Download filters: All, Active, Queued, Completed, Failed, Cancelled | Done |
| UI | History search and status filters | Done |
| UI | Open file, open folder, copy path, retry failed downloads | Done |
| UI | Native folder picker and clipboard paste | Done |
| UI | Toast notifications | Done |

## Quick Start

Requirements: Python 3.9+

```bash
git clone https://github.com/oumaymaabdelkefi/simple-download-manager.git
cd simple-download-manager
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Run the browser UI:

```bash
python ui/browser_gui.py
```

Run the test suite:

```bash
python -m unittest
```

## Browser UI

The browser UI is the recommended interface. It serves the HTML/CSS app locally and connects it to the same Python download core used by the CLI.

```bash
python ui/browser_gui.py
```

Main browser UI capabilities:

- Paste a URL with one click
- Choose output folder using the native folder picker
- Select thread count and retry count
- Set a per-download bandwidth limit
- Set a global MB/s limit shared by all active downloads
- View live progress, speed, ETA, segment status, retries, and bandwidth settings
- Filter downloads by `All`, `Active`, `Queued`, `Completed`, `Failed`, or `Cancelled`
- Manage queued downloads with `Start now`, `Move up`, `Move down`, `Cancel`, and `Remove`
- Search history and filter by status
- Open completed files, open their folders, or copy file paths
- Retry failed downloads normally, with more threads, or from existing part files
- Resume interrupted downloads from saved `.partN` files

## Desktop UI Options

```bash
# Desktop webview UI
python ui/web_gui.py

# Classic Tkinter UI
python ui/gui.py
```

The webview UI uses the same HTML/CSS asset as the browser UI. The Tkinter UI is a simpler classic desktop interface.

## CLI Usage

```bash
# Basic download, 4 threads by default
python cli.py download https://example.com/file.zip

# Custom threads and output directory
python cli.py download https://example.com/file.zip -t 8 -o ~/Downloads

# Override filename
python cli.py download https://example.com/file.zip -f myfile.zip

# Set max retries
python cli.py download https://example.com/file.zip -r 5

# Limit one download to 2.5 MB/s
python cli.py download https://example.com/file.zip -b 2.5

# View history
python cli.py history

# View last 50 history rows
python cli.py history -n 50

# Clear history
python cli.py history --clear
```

During a CLI download, press `Ctrl+C` once to pause. Press Enter to resume. Press `Ctrl+C` again to cancel.

Example output:

```text
Downloading: https://releases.ubuntu.com/22.04/ubuntu-22.04-desktop-amd64.iso
Threads: 8 | Max retries: 3
Saving to: /home/user/Downloads/ubuntu-22.04-desktop-amd64.iso

[████████████░░░░░░░░░░░░░░░░░░] 38.2%  367.4 MB/1.2 GB  42.1 MB/s  ETA: 18s
Done: /home/user/Downloads/ubuntu-22.04-desktop-amd64.iso  (1.2 GB)
```

## Queue Commands

The queue is stored in SQLite, so pending downloads survive application restarts.

```bash
# Add a queued download
python cli.py queue add https://example.com/file.zip -o ~/Downloads

# Add a scheduled download
python cli.py queue add https://example.com/file.zip -o ~/Downloads --schedule 2026-04-29T15:30

# List queued downloads
python cli.py queue list

# Reorder queued downloads
python cli.py queue up <queue-id>
python cli.py queue down <queue-id>

# Start one queued download immediately
python cli.py queue start <queue-id>

# Run queued downloads with two active downloads and a shared 3 MB/s cap
python cli.py queue run --max-active 2 --global-bandwidth 3

# Remove a queued download
python cli.py queue remove <queue-id>
```

## Architecture

SDM uses a layered architecture. The UI layers call the Python download core, while the core manages worker threads, task state, retries, file assembly, and persistence updates.

```text
┌─────────────────────────────────────────────────────────┐
│ UI Layer                                                 │
│ CLI              Browser UI        Webview/Tkinter GUI   │
│ cli.py           ui/browser_gui.py ui/web_gui.py/ui/gui.py│
└───────────────────────────┬─────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────┐
│ Download Core                                             │
│ DownloadManager, DownloadTask, SegmentDownloader          │
│ core/downloader.py                                        │
└───────────────┬─────────────────────────┬───────────────┘
                │                         │
┌───────────────▼──────────────┐ ┌────────▼────────────────┐
│ Worker Threads                │ │ Persistence              │
│ HTTP range requests            │ │ SQLite history + queue    │
│ Pause/cancel events            │ │ core/history.py           │
│ Retry/backoff                  │ └─────────────────────────┘
│ Part files + merge             │
└───────────────────────────────┘
```

## Component Overview

| Component | File | Responsibility |
|---|---|---|
| `DownloadManager` | `core/downloader.py` | Starts tasks and exposes pause/resume/cancel operations |
| `DownloadTask` | `core/downloader.py` | Stores one download's URL, output path, status, progress, segments, and limits |
| `SegmentDownloader` | `core/downloader.py` | Downloads one byte range into one `.partN` file |
| File assembler | `core/downloader.py` | Merges all part files into the final output file |
| `BandwidthLimiter` | `core/downloader.py` | Applies token-bucket rate limiting per task or globally |
| History/queue store | `core/history.py` | Stores history, live progress metadata, and queued downloads in SQLite |
| CLI | `cli.py` | Terminal interface for downloads, history, and queue operations |
| Browser server | `ui/browser_gui.py` | Serves the browser UI and exposes local API endpoints |
| Webview API | `ui/web_gui.py` | Connects the HTML UI to Python through pywebview |
| HTML UI | `ui/assets/sdm_ui.html` | Browser/webview interface |
| Tkinter GUI | `ui/gui.py` | Classic desktop GUI |

## Thread Model

```text
main thread
└── DownloadManager._run_task() [daemon thread]
    ├── HEAD request to detect size and range support
    ├── split file into N byte ranges
    ├── spawn N SegmentDownloader worker threads
    │   ├── worker 0: bytes 0 - 299 MB
    │   ├── worker 1: bytes 300 - 599 MB
    │   ├── worker 2: bytes 600 - 899 MB
    │   └── worker 3: bytes 900 MB - EOF
    ├── wait for workers
    └── merge .part0, .part1, ... into final file
```

Synchronization primitives:

| Primitive | Purpose |
|---|---|
| `threading.Lock` | Protects shared counters and segment state updates |
| `threading.Event` for pause | Workers block on `.wait()` between chunks |
| `threading.Event` for cancel | Workers check `.is_set()` between chunks |

If the server does not provide `Content-Length` or does not support range requests, SDM falls back to a simple single-thread download.

## HTTP Range Requests

Segmented downloads use the HTTP `Range` header:

```http
GET /file.zip HTTP/1.1
Range: bytes=0-299999999

HTTP/1.1 206 Partial Content
Content-Range: bytes 0-299999999/1200000000
Content-Length: 300000000
```

Each worker downloads a separate byte range. The data is written to temporary `.partN` files and merged only after every segment succeeds.

If the app exits before completion, the `.partN` files remain on disk. Starting the same download again, or using Resume from history, reuses existing part files. Missing part files are redownloaded instead of making the whole resume operation fail.

## Persistence

SDM stores runtime data in SQLite through `core/history.py`.

Stored data includes:

- Completed, failed, cancelled, and interrupted downloads
- Output path, URL, filename, size, timestamps, status, and thread count
- Live downloaded bytes while a task is running
- Per-segment metadata including index, byte range, downloaded bytes, retries, and status
- Persistent queue rows with order and optional scheduled start time

## Testing

Run all tests:

```bash
python -m unittest
```

The tests use a local `ThreadingHTTPServer` so segmented requests can run concurrently during test downloads.

Current verified result:

```text
Ran 16 tests
OK
```

## Performance Notes

Segmented downloading can improve speed when the server and network allow multiple simultaneous range requests. The best thread count depends on server limits, latency, file size, and available bandwidth. In practice, 4-8 threads is usually a good range for large files.

Example local benchmark from the test environment:

| Threads | Time | Notes |
|---|---:|---|
| 1 | ~0.108s | Single worker |
| 4 | ~0.105s | Four workers |

Small local files do not show meaningful speedup because startup overhead dominates. Large remote files benefit more from segmentation.

## Project Structure

```text
simple-download-manager/
├── core/
│   ├── __init__.py
│   ├── downloader.py       # Download manager, task model, workers, limiter
│   └── history.py          # SQLite history and queue persistence
├── ui/
│   ├── __init__.py
│   ├── gui.py              # Tkinter GUI
│   ├── browser_gui.py      # Browser UI server
│   ├── web_gui.py          # pywebview desktop API
│   └── assets/
│       └── sdm_ui.html     # Browser/webview HTML, CSS, and JS
├── tests/
│   ├── __init__.py
│   └── test_downloader.py  # Unit tests with local HTTP server
├── cli.py                  # CLI entry point
├── requirements.txt
└── README.md
```

## Design Decisions

1. **Threading for I/O-bound work**: Python threads are appropriate here because HTTP downloads spend most time waiting on network I/O.
2. **Layered architecture**: UI code stays separate from the download core, so CLI, browser, webview, and Tkinter can reuse the same logic.
3. **Temporary part files**: The final file is produced only after all segments complete, reducing the risk of corrupt output.
4. **SQLite persistence**: SQLite is built into Python, requires no server, and is enough for local history and queue state.
5. **Event-based pause/cancel**: `threading.Event` gives workers a simple cooperative pause and cancellation mechanism.
6. **Token-bucket bandwidth limiting**: Rate limiting can be applied per download or shared globally across concurrent downloads.

## Challenges Solved

- Coordinating progress updates from multiple worker threads safely
- Falling back when a server does not support range requests
- Preserving partial downloads across restarts
- Handling missing `.partN` files during resume
- Keeping browser/webview UI state synchronized with Python tasks
- Persisting live segment progress without blocking workers excessively
- Enforcing a shared global bandwidth cap across active queued downloads

## Evaluation Criteria Coverage

| Criterion | Implementation |
|---|---|
| Architecture design | Layered architecture, reusable core, clear UI/core/persistence separation |
| Correct implementation | Segmented downloads, merge logic, retries, pause/resume/cancel, queue, history |
| Multithreading efficiency | N segment workers, safe synchronization, local concurrent HTTP tests |
| Code quality | Dataclasses, type hints, separated modules, unit tests |
| Report and explanation | README architecture, thread model, range request, design decision, and challenge sections |

## License

MIT
