"""
Persistent conversation storage: named chats are stored as one plain JSON
file each under config.SAVES_DIR -- no database. Each file captures
everything needed to fully resume: the raw message history, the running
context-compaction summary, which provider was active, and every
provider's currently-selected model (not just the active one, so /model
choices for other providers survive a save/load round-trip too).

This module is pure I/O plus filename safety -- it knows nothing about
sessions, providers, or the terminal. Callers pass in exactly the state to
persist and get back plain data (or a StorageError) to interpret themselves.
"""

import json
import os
import re

from datetime import datetime

from . import config

# Deliberately restrictive: letters, digits, dash, underscore only. Blocks
# path traversal (no /, .., or path separators can ever reach os.path.join)
# and keeps filenames predictable across platforms -- simpler than trying to
# safely escape arbitrary input.
_CHAT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class StorageError(Exception):
    """A save/load/delete operation failed in an expected way. `str(e)` is
    the main, always-safe-to-print message. `.hint` is an optional trailing
    note (e.g. "Type /chats to list saved chats.") that callers may want to
    display without the same error styling as the main message."""

    def __init__(self, message, hint=None):
        self.hint = hint
        super().__init__(message)


def sanitize_name(name):
    """Return `name` if it's a safe chat name, else None."""
    return name if _CHAT_NAME_RE.match(name) else None


def chat_path(safe_name):
    return os.path.join(config.SAVES_DIR, f"{safe_name}.json")


def save_chat(name, *, provider, models, persona, conversation_summary, history):
    """Write the given conversation state to disk under `name`. Returns
    (safe_name, overwriting) on success; raises StorageError otherwise."""
    safe = sanitize_name(name)
    if not safe:
        raise StorageError(
            "Invalid name.",
            hint="Use only letters, numbers, - and _ (no spaces or slashes).",
        )
    if not history:
        raise StorageError("Nothing to save yet -- conversation is empty.")

    path = chat_path(safe)
    overwriting = os.path.exists(path)
    payload = {
        "name": safe,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "provider": provider,
        "models": models,
        "persona": persona,
        "conversation_summary": conversation_summary,
        "history": history,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    except OSError as e:
        raise StorageError(f"Failed to save '{safe}': {e}") from e

    return safe, overwriting


def load_chat(name):
    """Return the parsed JSON dict for saved chat `name`. Raises
    StorageError if the name is invalid, unknown, or unreadable."""
    safe = sanitize_name(name)
    path = chat_path(safe) if safe else None
    if not safe or not os.path.isfile(path):
        raise StorageError(
            f"No saved chat named '{name}'.",
            hint="Type /chats to list saved chats.",
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise StorageError(f"Failed to load '{safe}': {e}") from e


def delete_chat(name):
    """Delete saved chat `name`. Returns the sanitized name on success;
    raises StorageError if the name is invalid, unknown, or undeletable."""
    safe = sanitize_name(name)
    path = chat_path(safe) if safe else None
    if not safe or not os.path.isfile(path):
        raise StorageError(f"No saved chat named '{name}'.")
    try:
        os.remove(path)
    except OSError as e:
        raise StorageError(f"Failed to delete '{safe}': {e}") from e
    return safe


def rename_chat(old_name, new_name):
    """Rename saved chat `old_name` to `new_name`, updating the "name"
    field stored inside the file to match. Returns the new sanitized name
    on success; raises StorageError if either name is invalid, the old
    chat doesn't exist, the names are the same, or a chat already exists
    under the new name (never silently overwrites another saved chat)."""
    old_safe = sanitize_name(old_name)
    old_path = chat_path(old_safe) if old_safe else None
    if not old_safe or not os.path.isfile(old_path):
        raise StorageError(
            f"No saved chat named '{old_name}'.",
            hint="Type /chats to list saved chats.",
        )

    new_safe = sanitize_name(new_name)
    if not new_safe:
        raise StorageError(
            "Invalid name.",
            hint="Use only letters, numbers, - and _ (no spaces or slashes).",
        )
    if new_safe == old_safe:
        raise StorageError(f"'{old_safe}' is already the current name.")

    new_path = chat_path(new_safe)
    if os.path.exists(new_path):
        raise StorageError(
            f"A saved chat named '{new_safe}' already exists.",
            hint="Delete it first or pick another name.",
        )

    try:
        with open(old_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise StorageError(f"Failed to read '{old_safe}': {e}") from e

    data["name"] = new_safe
    try:
        with open(new_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as e:
        raise StorageError(f"Failed to rename '{old_safe}' to '{new_safe}': {e}") from e

    try:
        os.remove(old_path)
    except OSError as e:
        # New file is already written correctly at this point -- the rename
        # substantively succeeded. Surface the leftover-old-file problem
        # without pretending nothing happened.
        raise StorageError(
            f"Renamed to '{new_safe}', but couldn't remove the old file '{old_safe}.json': {e}"
        ) from e

    return new_safe


def list_chats():
    """Return (name, saved_at, provider, message_count) tuples for every
    saved chat, newest saved_at first. A corrupted/unreadable file shows up
    with '?' fields instead of being silently skipped."""
    files = sorted(f for f in os.listdir(config.SAVES_DIR) if f.endswith(".json"))
    rows = []
    for fname in files:
        name = fname[:-5]
        try:
            with open(os.path.join(config.SAVES_DIR, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
            rows.append((
                name,
                data.get("saved_at", "?"),
                data.get("provider", "?"),
                len(data.get("history", [])),
            ))
        except (OSError, json.JSONDecodeError):
            rows.append((name, "?", "?", "?"))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows
