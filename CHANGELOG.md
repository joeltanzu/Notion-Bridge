# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- Bundle anyio package for httpx compatibility

### Fixed

#### 1. Infinite Conflict Loop (local → Notion push)
**Problem**: When resolving a conflict by choosing "Keep Local", the push would re-check for conflicts and create a new conflict, causing an infinite loop.

**Root Cause**: `resolve_conflict("local")` called `push_file()` which re-checked for conflicts before pushing.

**Fix**: Added `force=True` parameter to `push_file()` that skips conflict re-check. When resolving conflicts, now passes `force=True` to bypass this check.

**Files Changed**:
- `backend/sync/engine.py`: 
  - Added `force: bool = False` parameter to `push_file()`
  - Modified conflict detection to skip when `force=True`
  - Updated `resolve_conflict("local")` to call `push_file(vault, path, force=True)`

#### 2. Exclude Root Page from Syncing
**Problem**: The root Notion page (the "folder") was being synced to local, cluttering the folder.

**Root Cause**: `walk_page_tree()` includes all pages including the root.

**Fix**: Added filter to skip `page_id == vault.notion_root_id` in sync plan generation.

**Files Changed**:
- `backend/sync/engine.py`:
  - Added skip in `generate_sync_plan()` loop
  - Added skip in `detect_notion_changes()` loop

#### 3. anyio Missing from Bundle
**Problem**: "No module named 'anyio_backends'" error when adding new vault.

**Root Cause**: py2app excluded anyio package, but httpx depends on it at runtime.

**Fix**: Added "anyio" to packages list in `setup.py`.

**Files Changed**:
- `setup.py`: Added "anyio" to packages

#### 4. Vault Init Failures Silent
**Problem**: Vaults that failed to start watchers/poll loops did so silently.

**Fix**: Added logging for vault init failures in `set_window()`.

**Files Changed**:
- `backend/api.py`: Added warning log for init failures

#### 5. poll_vault Stuck in "syncing"
**Problem**: If an exception occurred during polling, vault status would remain "syncing".

**Fix**: Added try/except in `poll_vault()` to reset status to "idle" on error.

**Files Changed**:
- `backend/sync/engine.py`: Added exception handling in poll_vault()

#### 6. Event Listener Memory Leak
**Problem**: Frontend event listeners were never cleaned up, causing memory leaks.

**Fix**: Refactored event handlers to named functions and added cleanup in useEffect return.

**Files Changed**:
- `frontend/src/lib/events.ts`: Refactored to named handlers + cleanup function
- `frontend/src/main.tsx`: Added cleanup return in useEffect

#### 7. handle_fs_event Silent Return
**Problem**: When vault not found in file event handler, returned silently.

**Fix**: Added warning log when vault not found.

**Files Changed**:
- `backend/sync/engine.py`: Added warning log

#### 8. _own_writes Memory Growth
**Problem**: `_own_writes` set could grow unbounded over time.

**Fix**: Added max size limit (1000) with FIFO eviction.

**Files Changed**:
- `backend/sync/engine.py`: Added size limit

### Changed

#### Poll Loop Now Pulls Changes
**Change**: Poll loop now runs `poll_vault()` instead of just `detect_notion_changes()`, enabling automatic syncing rather than just detecting.

**Files Changed**:
- `backend/api.py`: Changed `_poll_forever()` to call `poll_vault()`

---

## [1.0.0] - Initial Release

- Initial Notion Bridge app release