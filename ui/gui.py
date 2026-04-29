#!/usr/bin/env python3
"""
SDM - Simple Download Manager
Tkinter-based graphical user interface
"""

import sys
import os
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.downloader import DownloadManager, DownloadTask, DownloadStatus
from core.history import save_download, update_download, get_history, clear_history


def format_size(b: int) -> str:
    if b == 0:
        return "—"
    for unit in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def format_time(secs) -> str:
    if secs is None:
        return "—"
    if secs < 60:
        return f"{secs:.0f}s"
    m, s = divmod(secs, 60)
    return f"{int(m)}m {int(s)}s"


class DownloadRow:
    def __init__(self, frame: tk.Frame, task: DownloadTask, manager: DownloadManager, remove_cb):
        self.task = task
        self.manager = manager
        self.remove_cb = remove_cb
        self.row_id = None

        self.frame = tk.Frame(frame, bg="#1e1e2e", pady=6, padx=10, relief="flat")
        self.frame.pack(fill="x", pady=3, padx=8)

        # Top row: filename + status
        top = tk.Frame(self.frame, bg="#1e1e2e")
        top.pack(fill="x")

        self.lbl_name = tk.Label(top, text=task.filename, bg="#1e1e2e", fg="#cdd6f4",
                                  font=("Courier New", 10, "bold"), anchor="w")
        self.lbl_name.pack(side="left", fill="x", expand=True)

        self.lbl_status = tk.Label(top, text="⏳ Pending", bg="#1e1e2e", fg="#a6e3a1",
                                    font=("Courier New", 9))
        self.lbl_status.pack(side="right")

        # Progress bar
        self.pb = ttk.Progressbar(self.frame, length=400, mode="determinate", maximum=100)
        self.pb.pack(fill="x", pady=(3, 2))

        # Bottom row: stats + buttons
        bottom = tk.Frame(self.frame, bg="#1e1e2e")
        bottom.pack(fill="x")

        self.lbl_stats = tk.Label(bottom, text="", bg="#1e1e2e", fg="#6c7086",
                                   font=("Courier New", 8))
        self.lbl_stats.pack(side="left")

        btn_style = {"bg": "#313244", "fg": "#cdd6f4", "relief": "flat",
                     "font": ("Courier New", 8), "padx": 6, "pady": 2, "cursor": "hand2"}

        self.btn_pause = tk.Button(bottom, text="⏸ Pause", command=self.toggle_pause, **btn_style)
        self.btn_pause.pack(side="right", padx=2)

        self.btn_cancel = tk.Button(bottom, text="✖ Cancel", command=self.cancel,
                                     bg="#45475a", fg="#f38ba8", relief="flat",
                                     font=("Courier New", 8), padx=6, pady=2, cursor="hand2")
        self.btn_cancel.pack(side="right", padx=2)

        tk.Frame(self.frame, bg="#313244", height=1).pack(fill="x", pady=(6, 0))

    def toggle_pause(self):
        if self.task.status == DownloadStatus.PAUSED:
            self.manager.resume(self.task)
            self.btn_pause.config(text="⏸ Pause")
        elif self.task.status == DownloadStatus.DOWNLOADING:
            self.manager.pause(self.task)
            self.btn_pause.config(text="▶ Resume")

    def cancel(self):
        self.manager.cancel(self.task)

    def update_ui(self):
        status_colors = {
            DownloadStatus.DOWNLOADING: "#a6e3a1",
            DownloadStatus.PAUSED: "#f9e2af",
            DownloadStatus.COMPLETED: "#89b4fa",
            DownloadStatus.FAILED: "#f38ba8",
            DownloadStatus.CANCELLED: "#6c7086",
        }
        status_icons = {
            DownloadStatus.DOWNLOADING: "⬇",
            DownloadStatus.PAUSED: "⏸",
            DownloadStatus.COMPLETED: "✅",
            DownloadStatus.FAILED: "❌",
            DownloadStatus.CANCELLED: "🚫",
            DownloadStatus.PENDING: "⏳",
        }

        color = status_colors.get(self.task.status, "#cdd6f4")
        icon = status_icons.get(self.task.status, "")
        self.lbl_status.config(text=f"{icon} {self.task.status.value.capitalize()}", fg=color)
        self.pb["value"] = self.task.progress

        dl = format_size(self.task.downloaded_bytes)
        total = format_size(self.task.total_size)
        speed = format_size(int(self.task.speed)) + "/s"
        eta = format_time(self.task.eta)
        self.lbl_stats.config(text=f"{dl} / {total}   {speed}   ETA: {eta}   {self.task.progress:.1f}%")


class SDMApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SDM — Simple Download Manager")
        self.geometry("720x600")
        self.configure(bg="#11111b")
        self.resizable(True, True)
        self.minsize(600, 400)

        self.manager = DownloadManager()
        self.rows: list[DownloadRow] = []
        self._setup_style()
        self._build_ui()
        self._tick()

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TProgressbar",
                         troughcolor="#313244",
                         background="#89b4fa",
                         bordercolor="#11111b",
                         lightcolor="#89b4fa",
                         darkcolor="#89b4fa",
                         thickness=8)

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg="#181825", pady=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="⬇  SDM", bg="#181825", fg="#89b4fa",
                  font=("Courier New", 18, "bold")).pack(side="left", padx=16)
        tk.Label(hdr, text="Simple Download Manager", bg="#181825", fg="#6c7086",
                  font=("Courier New", 10)).pack(side="left")

        # Input area
        inp = tk.Frame(self, bg="#11111b", pady=8)
        inp.pack(fill="x", padx=12)

        tk.Label(inp, text="URL", bg="#11111b", fg="#a6adc8",
                  font=("Courier New", 9)).grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.url_var = tk.StringVar()
        url_entry = tk.Entry(inp, textvariable=self.url_var, bg="#1e1e2e", fg="#cdd6f4",
                              insertbackground="#cdd6f4", relief="flat", font=("Courier New", 10),
                              width=50)
        url_entry.grid(row=0, column=1, sticky="ew", padx=4, ipady=4)
        inp.columnconfigure(1, weight=1)

        tk.Label(inp, text="Threads", bg="#11111b", fg="#a6adc8",
                  font=("Courier New", 9)).grid(row=0, column=2, sticky="w", padx=(8, 4))
        self.threads_var = tk.IntVar(value=4)
        tk.Spinbox(inp, from_=1, to=16, textvariable=self.threads_var, width=4,
                    bg="#1e1e2e", fg="#cdd6f4", relief="flat", font=("Courier New", 10),
                    buttonbackground="#313244").grid(row=0, column=3, padx=4, ipady=3)

        self.dest_var = tk.StringVar(value=os.path.expanduser("~/Downloads"))
        tk.Label(inp, text="Save to", bg="#11111b", fg="#a6adc8",
                  font=("Courier New", 9)).grid(row=1, column=0, sticky="w", pady=(4, 0), padx=(0, 6))
        tk.Entry(inp, textvariable=self.dest_var, bg="#1e1e2e", fg="#cdd6f4",
                  insertbackground="#cdd6f4", relief="flat", font=("Courier New", 10)).grid(
            row=1, column=1, sticky="ew", padx=4, pady=(4, 0), ipady=4)
        tk.Button(inp, text="Browse", command=self._browse,
                   bg="#313244", fg="#cdd6f4", relief="flat", font=("Courier New", 8),
                   cursor="hand2").grid(row=1, column=2, padx=(8, 4), pady=(4, 0))

        add_btn = tk.Button(inp, text="⬇  Add Download", command=self._add_download,
                             bg="#89b4fa", fg="#11111b", relief="flat",
                             font=("Courier New", 10, "bold"), padx=12, pady=4, cursor="hand2")
        add_btn.grid(row=1, column=3, padx=4, pady=(4, 0))

        # Tabs
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=8)

        # Downloads tab
        dl_frame = tk.Frame(self.notebook, bg="#11111b")
        self.notebook.add(dl_frame, text="  Downloads  ")

        self.dl_canvas = tk.Canvas(dl_frame, bg="#11111b", highlightthickness=0)
        scrollbar = ttk.Scrollbar(dl_frame, orient="vertical", command=self.dl_canvas.yview)
        self.dl_inner = tk.Frame(self.dl_canvas, bg="#11111b")

        self.dl_inner.bind("<Configure>", lambda e: self.dl_canvas.configure(
            scrollregion=self.dl_canvas.bbox("all")))

        self.dl_canvas.create_window((0, 0), window=self.dl_inner, anchor="nw")
        self.dl_canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        self.dl_canvas.pack(side="left", fill="both", expand=True)

        # History tab
        hist_frame = tk.Frame(self.notebook, bg="#11111b")
        self.notebook.add(hist_frame, text="  History  ")
        self._build_history_tab(hist_frame)

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(self, textvariable=self.status_var, bg="#181825", fg="#6c7086",
                  font=("Courier New", 8), anchor="w", pady=4).pack(fill="x", side="bottom", padx=12)

    def _build_history_tab(self, parent):
        top = tk.Frame(parent, bg="#11111b", pady=6)
        top.pack(fill="x", padx=8)
        tk.Button(top, text="↻ Refresh", command=self._refresh_history,
                   bg="#313244", fg="#cdd6f4", relief="flat", font=("Courier New", 9),
                   cursor="hand2").pack(side="left", padx=4)
        tk.Button(top, text="🗑 Clear All", command=self._clear_history,
                   bg="#45475a", fg="#f38ba8", relief="flat", font=("Courier New", 9),
                   cursor="hand2").pack(side="left", padx=4)

        cols = ("id", "filename", "size", "status", "date")
        self.hist_tree = ttk.Treeview(parent, columns=cols, show="headings", height=15)
        self.hist_tree.heading("id", text="#")
        self.hist_tree.heading("filename", text="Filename")
        self.hist_tree.heading("size", text="Size")
        self.hist_tree.heading("status", text="Status")
        self.hist_tree.heading("date", text="Started")

        self.hist_tree.column("id", width=40, anchor="center")
        self.hist_tree.column("filename", width=240)
        self.hist_tree.column("size", width=90, anchor="center")
        self.hist_tree.column("status", width=100, anchor="center")
        self.hist_tree.column("date", width=160)

        sb = ttk.Scrollbar(parent, orient="vertical", command=self.hist_tree.yview)
        self.hist_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.hist_tree.pack(fill="both", expand=True, padx=8, pady=4)

        self._refresh_history()

    def _refresh_history(self):
        for item in self.hist_tree.get_children():
            self.hist_tree.delete(item)
        for e in get_history(50):
            size_str = format_size(e.total_size) if e.total_size else "—"
            date_str = (e.start_time or "—")[:19]
            icons = {"completed": "✅", "failed": "❌", "cancelled": "🚫"}
            status = icons.get(e.status, "⏳") + " " + e.status
            self.hist_tree.insert("", "end", values=(e.id, e.filename or "—", size_str, status, date_str))

    def _clear_history(self):
        if messagebox.askyesno("Clear History", "Clear all download history?"):
            clear_history()
            self._refresh_history()

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.dest_var.get())
        if d:
            self.dest_var.set(d)

    def _add_download(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please enter a URL to download.")
            return

        task = self.manager.create_task(
            url=url,
            dest_dir=self.dest_var.get(),
            num_threads=self.threads_var.get(),
            on_progress=self._on_progress,
            on_complete=self._on_complete,
            on_error=self._on_error,
        )

        row = DownloadRow(self.dl_inner, task, self.manager, self._remove_row)
        row.row_id = save_download(task)
        self.rows.append(row)
        self.manager.start(task)
        self.url_var.set("")
        self.status_var.set(f"Started: {task.filename}")
        self.notebook.select(0)

    def _on_progress(self, task: DownloadTask):
        for row in self.rows:
            if row.task is task and row.row_id:
                update_download(row.row_id, task)
                break

    def _on_complete(self, task: DownloadTask):
        for row in self.rows:
            if row.task is task and row.row_id:
                update_download(row.row_id, task)
        self._refresh_history()

    def _on_error(self, task: DownloadTask):
        for row in self.rows:
            if row.task is task and row.row_id:
                update_download(row.row_id, task)

    def _remove_row(self, row: "DownloadRow"):
        self.rows.remove(row)
        row.frame.destroy()

    def _tick(self):
        for row in self.rows:
            try:
                row.update_ui()
            except Exception:
                pass
        active = sum(1 for r in self.rows if r.task.status == DownloadStatus.DOWNLOADING)
        if active:
            self.status_var.set(f"{active} download(s) active")
        self.after(300, self._tick)


def main():
    app = SDMApp()
    app.mainloop()


if __name__ == "__main__":
    main()
