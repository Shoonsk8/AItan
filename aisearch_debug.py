"""Lightweight debug logging — emits to stderr AND mirrors to an optional
in-app QPlainTextEdit panel. Used to trace operations like canvas snap
cascades, search/dup scan worker progress, bake pipeline steps, etc.

Usage:
    from aisearch_debug import dbg, set_panel
    dbg("snap_chain start key=E children=[CLIP_E]")

When a panel is set via set_panel(qpte), each dbg call also appends to it.
"""
import sys
import time
from collections import deque

_BUFFER = deque(maxlen=500)
_PANEL = None


def set_panel(panel):
    global _PANEL
    _PANEL = panel
    if panel is not None:
        # Replay buffered lines so the panel isn't empty when first shown
        for line in _BUFFER:
            try:
                panel.appendPlainText(line)
            except Exception:
                break


def dbg(msg):
    ts_now = time.time()
    line = f"[{time.strftime('%H:%M:%S', time.localtime(ts_now))}.{int((ts_now % 1) * 1000):03d}] {msg}"
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass
    _BUFFER.append(line)
    p = _PANEL
    if p is not None:
        # Panel updates must happen on the GUI thread — calling
        # appendPlainText from a worker thread segfaults Qt, and queuing
        # every worker call onto the GUI thread floods the event loop and
        # makes preview navigation lag (renders queued behind a backlog of
        # appendPlainText events). Live-mirror only when we're already on
        # the GUI thread; worker-thread lines stay in stderr + _BUFFER.
        try:
            from PyQt6.QtCore import QThread
            if QThread.currentThread() is p.thread():
                p.appendPlainText(line)
        except Exception:
            pass
