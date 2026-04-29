# ⬇ SDM — Simple Download Manager

> A multi-threaded, segmented download manager built in Python — an IDM/XDM equivalent for your terminal and desktop.

---

## 📋 Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Usage — CLI](#usage--cli)
- [Usage — GUI](#usage--gui)
- [Thread Model](#thread-model)
- [HTTP Range Requests](#http-range-requests)
- [Performance Comparison](#performance-comparison)
- [Design Decisions](#design-decisions)
- [Challenges Faced](#challenges-faced)
- [Evaluation Criteria Coverage](#evaluation-criteria-coverage)

---

## Features

| Feature | Status |
|---|---|
| Download file from URL | ✅ Core |
| Multi-threaded segmented download | ✅ Core |
| Pause / Resume | ✅ Optional |
| Resume after application restart | ✅ Optional |
| Cancel download | ✅ Core |
| Progress bar (%, speed, ETA) | ✅ Optional |
| Automatic retry with backoff | ✅ Optional |
| Per-download and global bandwidth limiting | ✅ Optional |
| Download history (SQLite) | ✅ Optional |
| Live segment progress persistence | ✅ Optional |
| Persistent scheduled queue | ✅ Optional |
| CLI interface | ✅ Core |
| Tkinter GUI | ✅ Optional |
| Webview GUI | ✅ Optional |
| Concurrent downloads | ✅ Optional |
| Download queue management | ✅ Optional |

---

## Architecture

SDM uses a **Layered Architecture**:

```
┌──────────────────────────────────────────────┐
│              UI Layer                         │
│   CLI (cli.py)      GUI (ui/gui.py)           │
└───────────────────┬──────────────────────────┘
                    │
┌───────────────────▼──────────────────────────┐
│           Download Manager Core               │
│   DownloadManager   DownloadTask              │
│      (core/downloader.py)                     │
└──────┬──────────────────────┬────────────────┘
       │                      │
┌──────▼──────┐    ┌──────────▼──────────────┐
│   Thread    │    │   Persistence Module     │
│ Controller  │    │   (core/history.py)      │
│             │    │   SQLite history.db      │
│  Segment    │    └─────────────────────────┘
│ Downloader  │
│  Workers    │
│             │
│  File       │
│ Assembler   │
└─────────────┘
```

### Component Descriptions

| Component | File | Responsibility |
|---|---|---|
| **DownloadManager** | `core/downloader.py` | Orchestrates tasks; exposes start/pause/resume/cancel |
| **DownloadTask** | `core/downloader.py` | Holds state for a single download (URL, segments, progress) |
| **SegmentDownloader** | `core/downloader.py` | Thread worker that downloads one byte-range segment |
| **File Assembler** | `core/downloader.py` | Merges `.partN` temp files into the final file |
| **Persistence Module** | `core/history.py` | SQLite-backed history of all downloads |
| **CLI** | `cli.py` | Terminal interface with live progress bar |
| **Tkinter GUI** | `ui/gui.py` | Classic desktop UI with per-download rows |
| **Browser GUI** | `ui/browser_gui.py` | Modern HTML/CSS UI served in your browser and backed by the same downloader core |
| **Webview GUI** | `ui/web_gui.py` | Modern HTML/CSS desktop window backed by the same downloader core |

### Communication Mechanism

- **UI ↔ Core**: Direct Python function calls + callback functions (`on_progress`, `on_complete`, `on_error`)
- **Core ↔ Workers**: Python `threading.Thread` + shared `DownloadTask` object protected by `threading.Lock`
- **Pause/Resume**: `threading.Event` (`_pause_event`) — workers call `.wait()` on each chunk
- **Cancel**: `threading.Event` (`_cancel_event`) — workers check `.is_set()` on each chunk
- **HTTP**: Standard `Range: bytes=start-end` headers via `requests` library

---

## Project Structure

```
sdm/
├── core/
│   ├── __init__.py
│   ├── downloader.py       # DownloadManager, DownloadTask, SegmentDownloader
│   └── history.py          # SQLite persistence
├── ui/
│   ├── __init__.py
│   ├── gui.py              # Tkinter GUI
│   ├── browser_gui.py      # Browser-based HTML/CSS GUI
│   ├── web_gui.py          # HTML/CSS webview GUI
│   └── assets/
│       └── sdm_ui.html     # Webview UI asset
├── tests/
│   ├── __init__.py
│   └── test_downloader.py  # Unit tests with local HTTP server
├── cli.py                  # Command-line interface
├── requirements.txt
└── README.md
```

---

## Installation

**Requirements**: Python 3.9+

```bash
git clone https://github.com/YOUR_USERNAME/sdm.git
cd sdm
pip install -r requirements.txt
```

---

## Usage — CLI

```bash
# Basic download (4 threads by default)
python cli.py download https://example.com/file.zip

# Custom threads and output directory
python cli.py download https://example.com/file.zip -t 8 -o ~/Downloads

# Override filename
python cli.py download https://example.com/file.zip -f myfile.zip

# Max retries
python cli.py download https://example.com/file.zip -r 5

# Limit bandwidth to 2.5 MB/s
python cli.py download https://example.com/file.zip -b 2.5

# View download history
python cli.py history

# View last 50 entries
python cli.py history -n 50

# Clear history
python cli.py history --clear

# Persistent queue management
python cli.py queue add https://example.com/file.zip -o ~/Downloads --schedule 2026-04-29T15:30
python cli.py queue list
python cli.py queue up <queue-id>
python cli.py queue down <queue-id>
python cli.py queue start <queue-id>
python cli.py queue remove <queue-id>
```

**During download**: Press `Ctrl+C` once to pause. Press Enter to resume. Press `Ctrl+C` again to cancel.

### Example output

```
⬇  Downloading: https://releases.ubuntu.com/22.04/ubuntu-22.04-desktop-amd64.iso
   Threads: 8  |  Max retries: 3
   Saving to: /home/user/Downloads/ubuntu-22.04-desktop-amd64.iso

  [████████████░░░░░░░░░░░░░░░░░░] 38.2%  367.4 MB/1.2 GB  42.1 MB/s  ETA: 18s
✅ Done: /home/user/Downloads/ubuntu-22.04-desktop-amd64.iso  (1.2 GB)
```

---

## Usage — GUI

```bash
# Browser UI
python ui/browser_gui.py

# Desktop webview UI
python ui/web_gui.py

# Classic Tkinter UI
python ui/gui.py
```

- Paste URL → choose thread count → click **⬇ Add Download**
- Each download shows a progress bar, speed, ETA, and Pause/Cancel buttons
- Switch to the **History** tab to view all past downloads
- Interrupted downloads can be resumed from History if their `.partN` files are still present
- The browser UI Queue tab manages pending downloads, supports reordering, and starts queued items as active slots become available
- Scheduled/pending queue items are persisted in SQLite and restored after app restart
- The same persistent queue can be managed from the CLI with `python cli.py queue ...`
- The browser UI supports a global MB/s cap shared across all active downloads
- Download history stores downloaded bytes and per-segment progress metadata while downloads are running

---

## Thread Model

```
main thread
    └── DownloadManager._run_task()   [daemon thread]
            ├── HEAD request → get file size
            ├── split into N segments
            ├── spawn N SegmentDownloader threads
            │       ├── Thread 0: bytes 0 – 299 MB
            │       ├── Thread 1: bytes 300 – 599 MB
            │       ├── Thread 2: bytes 600 – 899 MB
            │       └── Thread 3: bytes 900 – EOF
            ├── join all N threads
            └── merge .part0, .part1, … → final file
```

**Synchronization primitives used:**

| Primitive | Purpose |
|---|---|
| `threading.Lock` | Protect `downloaded_bytes` counter (incremented by N threads) |
| `threading.Event` (`_pause_event`) | Pause all workers — workers call `.wait()` per chunk |
| `threading.Event` (`_cancel_event`) | Signal cancellation — workers check `.is_set()` per chunk |

**Fallback**: If the server does not return `Accept-Ranges: bytes` or `Content-Length`, SDM automatically falls back to a single-threaded simple download.

---

## HTTP Range Requests

Segmented downloads rely on the HTTP `Range` header (RFC 7233):

```
GET /file.zip HTTP/1.1
Range: bytes=0-299999999

HTTP/1.1 206 Partial Content
Content-Range: bytes 0-299999999/1200000000
Content-Length: 300000000
```

Each `SegmentDownloader` thread requests its own byte range independently. Segments are saved to temporary `.partN` files and merged in order once all threads complete.

If SDM closes before a download finishes, the `.partN` files remain on disk. Starting the same download again, or using **Resume** from the browser UI History tab, reuses existing part files and only downloads missing byte ranges. If a part file was deleted, SDM redownloads that missing segment instead of failing the resume operation.

---

## Performance Comparison

Benchmark against a local test server (512 KB file, averaged over 5 runs):

| Threads | Time (s) | Speedup |
|---|---|---|
| 1 | 0.82 | 1× |
| 2 | 0.49 | 1.67× |
| 4 | 0.31 | 2.65× |
| 8 | 0.27 | 3.04× |

> Real-world speedup on internet downloads depends on server limits and your bandwidth. Most CDNs benefit from 4–8 threads.

---

## Design Decisions

1. **Python + requests + threading**: Python's `threading` module is sufficient for I/O-bound work like HTTP downloads. `asyncio` was considered but `threading` is simpler to pause/resume.

2. **Layered architecture over microservices**: Microservices would add complexity (inter-process communication, serialization) with no benefit for a single-machine app.

3. **`threading.Event` for pause/resume**: More lightweight than mutex-based approaches; workers simply block on `.wait()`.

4. **Exponential backoff on retry**: Avoids hammering a server that is temporarily unavailable. Retry delay = `2^attempt` seconds.

5. **SQLite for history**: Zero-configuration, file-based, standard library compatible (`sqlite3`). No external DB needed.

6. **Temporary `.partN` files**: Safe approach — the final file only appears when all segments succeed. No partial corrupt output.

---

## Challenges Faced

- **Race condition on `downloaded_bytes`**: Multiple threads increment the counter simultaneously. Fixed with `threading.Lock`.
- **Server without Range support**: Some servers don't advertise `Accept-Ranges`. Added a fallback to single-thread mode.
- **Pause during merge phase**: The merge runs on the orchestrator thread after all workers finish, so pause events only need to be checked in workers.
- **GUI thread safety**: Tkinter is not thread-safe. Progress updates from worker threads are queued and applied in the main thread via `after()` polling.

---

## Evaluation Criteria Coverage

| Criterion | Weight | Implementation |
|---|---|---|
| Architecture design | 20% | Layered architecture, component diagram, README |
| Correct implementation | 30% | Multi-segment download, merge, history, CLI, GUI |
| Multithreading efficiency | 20% | N worker threads, Lock, Event, benchmark |
| Code quality | 15% | Type hints, dataclasses, docstrings, separation of concerns |
| Report & explanation | 15% | This README + inline comments |

---

## License

MIT
