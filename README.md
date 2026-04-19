# Notion Bridge

A macOS desktop app that keeps a local folder of Markdown files in sync with a Notion workspace. Edit locally in any editor, or edit in Notion — changes are detected and merged automatically.

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.14 recommended
- Node.js 18+
- A Notion integration token ([create one here](https://www.notion.so/my-integrations))

## Setup

```bash
# 1. Clone
git clone https://github.com/joeltan/notion-bridge
cd notion-bridge

# 2. Python dependencies
pip install -r requirements.txt

# 3. Frontend
cd frontend
npm install
cd ..
```

## Running (development)

```bash
python3 main.py

# With debug DevTools:
NB_DEBUG=1 python3 main.py
```

## Tests

```bash
pytest -q backend/tests
```

## Building the macOS app

```bash
# Fast dev build
./build.sh

# Standalone release bundle
./build.sh release

open "dist/Notion Bridge.app"
```

## How sync works

Notion Bridge tracks every synced page in a local SQLite database (`~/.notion-bridge/sync.db`). On each sync cycle it:

1. **Pulls** — fetches `last_edited_time` from Notion; if newer than stored, downloads and converts blocks to Markdown
2. **Pushes** — hashes local file content; if changed since last sync, converts Markdown to Notion blocks and uploads
3. **Conflicts** — if both sides changed since the last known baseline, surfaces a conflict panel where you choose which version wins
4. **Deletion recovery** — deleted-local and deleted-in-Notion cases are surfaced for resolution instead of being silently treated as synced
5. **Database traversal** — Notion database containers are traversed so row pages can be discovered and synced as pages

Local Markdown files are plain text — no hidden metadata comments. Sync state lives entirely in SQLite.

## Repo hygiene

- `README.md` and `CHANGELOG.md` are project docs and should stay tracked.
- `SESSION_NOTES.md`, `SYNC_FIXES_SUMMARY.md`, and other local scratch/reference notes are intentionally gitignored.
- `build/`, `dist/`, caches, and local virtualenv/build directories are gitignored.
- If a local-only note was previously committed, `.gitignore` alone will not untrack it; remove it from the index first before pushing.

## Project structure

```
├── main.py                  # Entry point (PyWebView + asyncio)
├── build.sh                 # Preferred build entrypoint
├── setup.py                 # py2app packaging config
├── requirements.txt
├── icon.icns                # App icon (macOS bundle)
├── icon.iconset/            # Source icon PNGs
├── scripts/
│   └── make_icon.py         # Regenerates icon.iconset + icon.icns from source JPEG
├── backend/
│   ├── api.py               # JS↔Python bridge (pywebview js_api)
│   ├── adapters/
│   │   ├── fs_adapter.py    # File read/write helpers
│   │   ├── notion_adapter.py# Notion API client wrapper
│   │   └── watcher.py       # File-system event watcher (watchdog)
│   ├── converters/
│   │   ├── notion_to_md.py  # Notion blocks → Markdown
│   │   └── md_to_notion.py  # Markdown → Notion blocks
│   └── sync/
│       ├── engine.py        # Core sync logic
│       ├── state.py         # SQLite state DB
│       └── conflict.py      # Conflict detection helpers
└── frontend/                # Vite + React + TypeScript UI
    └── src/
```
