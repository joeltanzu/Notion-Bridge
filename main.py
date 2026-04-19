"""
Notion Bridge — entry point.

Starts the asyncio event loop in a background thread, then opens a PyWebView
native window that loads the React frontend. The NotionBridgeAPI instance is
passed as js_api so the frontend can call Python methods via window.pywebview.api.
"""

import asyncio
import os
import threading

import webview

from backend.api import NotionBridgeAPI
from backend.adapters.watcher import VaultWatcher
from backend.sync.engine import SyncEngine
from backend.sync.state import StateDB


# ── Paths ─────────────────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.expanduser("~"), ".notion-bridge", "sync.db")

FRONTEND_DIST = os.path.join(
    os.path.dirname(__file__), "frontend", "dist", "index.html"
)


# Simple placeholder served when the React build doesn't exist yet
_PLACEHOLDER_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Notion Bridge</title>
  <style>
    body { font-family: system-ui, sans-serif; display: flex; align-items: center;
           justify-content: center; height: 100vh; margin: 0; background: #0f0f0f; color: #ccc; }
    .box { text-align: center; }
    h1 { font-size: 1.4rem; margin-bottom: .5rem; color: #fff; }
    p  { font-size: .9rem; opacity: .6; }
    code { background: #222; padding: 2px 6px; border-radius: 4px; font-size: .85rem; }
  </style>
</head>
<body>
  <div class="box">
    <h1>Notion Bridge</h1>
    <p>Frontend not built yet.</p>
    <p>Run <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code></p>
  </div>
</body>
</html>"""


# ── Asyncio loop (background thread) ─────────────────────────────────────────


def _start_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


# ── PyWebView callbacks ───────────────────────────────────────────────────────


def _on_loaded(api: NotionBridgeAPI, window) -> None:
    """Called by PyWebView after the page finishes loading."""
    api.set_window(window)


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    # 1. Shared asyncio loop running in a daemon thread
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=_start_loop, args=(loop,), daemon=True)
    t.start()

    # 2. Core services
    db = StateDB(DB_PATH)
    watcher = VaultWatcher()
    engine = SyncEngine(db)
    api = NotionBridgeAPI(db, engine, loop, watcher)

    # 3. Determine what to load
    if os.path.isfile(FRONTEND_DIST):
        url = f"file://{os.path.abspath(FRONTEND_DIST)}"
        html = None
    else:
        url = None
        html = _PLACEHOLDER_HTML

    # 4. Create PyWebView window
    window = webview.create_window(
        title="Notion Bridge",
        url=url,
        html=html,
        js_api=api,
        width=1100,
        height=720,
        min_size=(800, 560),
        background_color="#0f0f0f",
    )

    # Wire the window reference into api once DOM is ready
    window.events.loaded += lambda: _on_loaded(api, window)

    # 5. Start the GUI (blocks until window is closed)
    webview.start(debug=os.environ.get("NB_DEBUG") == "1")

    # 6. Clean up - run DB checkpoint to merge WAL before exiting
    watcher.stop()
    try:
        db.checkpoint()
    except Exception:
        pass
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=3)


if __name__ == "__main__":
    main()
