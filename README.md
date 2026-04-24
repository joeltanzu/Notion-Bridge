# Notion Bridge

Bidirectional sync between a local folder of Markdown files and a Notion workspace. Edit in your editor or in Notion — changes are detected and merged automatically.

![Notion Bridge](Notion%20Bridge.jpeg)

## Features

- Two-way sync between local Markdown and Notion pages
- Conflict detection when both sides change — you choose which version wins
- Deletion recovery — surfaced for manual resolution instead of silent data loss
- Notion database support — row pages are discovered and synced automatically
- No metadata pollution — local files are plain Markdown; sync state lives in SQLite

## Requirements

- macOS (Apple Silicon or Intel)
- Python 3.9+
- Node.js 18+
- A Notion integration token ([create one](https://www.notion.so/my-integrations))

## Installation

```bash
git clone https://github.com/joeltan/notion-bridge
cd notion-bridge

pip install -r requirements.txt

cd frontend && npm install && cd ..
```

## Usage

```bash
python3 main.py
```

Open the app, paste your Notion integration token, and select a local folder to sync.

```bash
# With DevTools
NB_DEBUG=1 python3 main.py
```

## How it works

On each sync cycle, Notion Bridge compares `last_edited_time` from Notion against a local SQLite database (`~/.notion-bridge/sync.db`). Pages newer on Notion are pulled and converted to Markdown; locally modified files are converted back to Notion blocks and pushed. If both sides changed since the last known baseline, a conflict panel surfaces for manual resolution.

## Development

```bash
# Run tests
pytest -q backend/tests

# Dev build
./build.sh

# Standalone release bundle
./build.sh release
open "dist/Notion Bridge.app"
```

## Stack

**Backend** — Python, PyWebView, notion-client, watchdog, SQLite

**Frontend** — React, TypeScript, Vite, Tailwind CSS, TanStack Query, Zustand
