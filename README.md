# Notion Bridge

> Bidirectional sync between local Markdown files and Notion pages, built for people who want their notes to stay portable.

![version](https://img.shields.io/badge/version-1.0.0-00d4aa?style=flat-square)
![platform](https://img.shields.io/badge/platform-macOS-lightgrey?style=flat-square)
![python](https://img.shields.io/badge/python-3.9+-blue?style=flat-square)
![frontend](https://img.shields.io/badge/frontend-React%20%2B%20TypeScript-61dafb?style=flat-square)
![sync](https://img.shields.io/badge/sync-local--first-6f42c1?style=flat-square)

---

![Notion Bridge app](Notion%20Bridge.jpeg)

## What it does

Notion Bridge keeps a local folder of Markdown files in sync with a Notion workspace. You can edit in your preferred text editor or in Notion, then let the app detect what changed and move the updates across.

The goal is simple: keep Notion useful without trapping your writing inside Notion.

## Features

- Two-way sync between local Markdown files and Notion pages
- Notion database support, including discovery of row pages
- Conflict detection when both local and Notion versions change
- Manual conflict resolution so the app does not silently overwrite your work
- Deletion recovery surfaced for review instead of immediate data loss
- Clean local Markdown files with no sync metadata injected into the content
- SQLite-backed sync state stored outside your note folder
- Keychain support for local credential handling
- Modern desktop interface with vault setup, file tree, activity feed, settings, sync preview, and conflict panels

## Requirements

- macOS on Apple Silicon or Intel
- Python 3.9+
- Node.js 18+
- A Notion integration token from [notion.so/my-integrations](https://www.notion.so/my-integrations)

## Getting started

### 1. Install dependencies

```bash
git clone https://github.com/joeltanzu/Notion-Bridge.git
cd Notion-Bridge

python3 -m pip install -r requirements.txt

cd frontend
npm install
cd ..
```

### 2. Launch the app

```bash
python3 main.py
```

For development with browser inspection:

```bash
NB_DEBUG=1 python3 main.py
```

### 3. Connect a vault

1. Create or copy a Notion integration token.
2. Share the relevant Notion page or database with that integration.
3. Open Notion Bridge and add a local folder as a vault.
4. Choose the Notion root page or database to sync.
5. Run a preview, review the proposed changes, then sync.

## How sync works

Notion Bridge compares Notion's `last_edited_time` with a local SQLite sync database stored under `~/.notion-bridge/sync.db`.

- If Notion is newer, the page is pulled and converted to Markdown.
- If the local file is newer, Markdown is converted back into Notion blocks and pushed.
- If both sides changed after the last baseline, a conflict panel asks you to choose the winning version.
- If something was deleted, the app surfaces the deletion for manual resolution.

This keeps the local files plain while still giving the sync engine enough memory to make careful decisions.

## Development

```bash
# Backend tests
pytest -q backend/tests

# Frontend build
cd frontend && npm run build

# Development app bundle
./build.sh

# Standalone release bundle
./build.sh release
open "dist/Notion Bridge.app"
```

## Project structure

| Path | Purpose |
|---|---|
| `backend/` | Sync engine, Notion adapter, filesystem adapter, converters, and API bridge |
| `frontend/` | React, TypeScript, Vite, Tailwind, and desktop UI components |
| `main.py` | pywebview entry point for the macOS app |
| `setup.py` | py2app bundle configuration |
| `build.sh` | Frontend and app-bundle build script |

## Built with

- Python, pywebview, notion-client, watchdog, SQLite, keyring, pydantic
- React, TypeScript, Vite, Tailwind CSS, TanStack Query, Zustand, Radix UI, lucide-react
- py2app for macOS packaging
