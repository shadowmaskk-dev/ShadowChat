"""
Configuration: constants, .env loading, and the on-disk locations ShadowChat
reads/writes (input history, saved chats). Nothing here depends on any other
shadowchat_app module -- this is the base of the import graph.
"""

import os

from dotenv import load_dotenv

SYSTEM_PROMPT = "You are ShadowChat, a helpful assistant inside ShadowMaskkYT's ShadowOS ecosystem. Be concise and direct."

# Persona name -> system prompt. Central store: switching personas (see
# commands._handle_persona) only ever picks a different value out of this
# dict for the *next* turn's system prompt -- it never touches
# memory.history or memory's running summary, so conversation memory is
# never duplicated, rewritten, or reset by a persona switch. "default" is
# exactly SYSTEM_PROMPT, so a session that never touches /persona behaves
# identically to before personas existed.
PERSONAS = {
    "default": SYSTEM_PROMPT,
    "developer": (
        "You are ShadowChat in developer mode, part of ShadowMaskkYT's ShadowOS "
        "ecosystem. Assume a technical audience. Prioritize correct, runnable code "
        "and precise terminology over hand-holding; default to code blocks for "
        "anything code-related; explain tradeoffs briefly; skip disclaimers a "
        "working developer wouldn't need."
    ),
    "research": (
        "You are ShadowChat in research mode, part of ShadowMaskkYT's ShadowOS "
        "ecosystem. Reason carefully and show your work: weigh evidence, flag "
        "uncertainty and open questions, and distinguish well-established facts "
        "from your own inference. Favor thorough, well-structured answers over "
        "brevity when the topic warrants it."
    ),
    "concise": (
        "You are ShadowChat in concise mode, part of ShadowMaskkYT's ShadowOS "
        "ecosystem. Answer in as few words as possible without losing correctness. "
        "Prefer a single sentence or a short list over a paragraph. Skip preamble, "
        "caveats, and disclaimers unless they're essential to the answer."
    ),
}

# Every slash command ShadowChat understands. Used for tab-completion (see
# ui.make_session) -- adding a command here doesn't wire it up by itself,
# it still needs a handler in commands.dispatch.
COMMANDS = [
    "/model", "/models", "/persona", "/status", "/context", "/new", "/clear",
    "/save", "/load", "/chats", "/delete", "/rename", "/help", "/exit",
]

# Context-compaction budget (see memory.compact_if_needed).
CONTEXT_CONFIG = {
    "max_tokens": 6000,           # compact once summary + history estimate exceeds this
    "keep_recent_messages": 12,   # always keep this many most-recent messages verbatim
    "summary_max_tokens": 500,    # cap the length (and cost) of each summarization call
}

# Retry policy for temporary provider failures (see providers._connect).
# Deliberately small and simple: a couple of retries with exponential
# backoff is enough to ride out a flaky connection or a brief provider
# outage without making a stuck request feel like it hung forever.
RETRY_CONFIG = {
    "max_attempts": 3,   # total tries per request: 1 initial + up to 2 retries
    "base_delay": 0.5,   # seconds; doubles each retry (0.5s, then 1.0s)
}

# This file lives in shadowchat_app/; the app root (next to shadowchat.py,
# .env, .gitignore) is one directory up. Anchoring every on-disk path there
# means behavior doesn't depend on the current working directory the user
# happens to launch from.
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_ROOT = os.path.dirname(_PACKAGE_DIR)

# Persistent input history file -> up/down arrow recalls past input across sessions
HISTORY_DIR = os.path.join(_APP_ROOT, ".shadowchat_history")
os.makedirs(HISTORY_DIR, exist_ok=True)
HISTORY_FILE = os.path.join(HISTORY_DIR, "input_history")

# Persistent conversation storage -- one plain JSON file per named chat, see storage.py
SAVES_DIR = os.path.join(_APP_ROOT, ".shadowchat_chats")
os.makedirs(SAVES_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------
# Looks for a .env file in the app root (not the current working directory)
# and loads any keys it finds into os.environ.
#
# override=False is deliberate: if a variable is already set in the real
# environment (exported in the shell, injected by Termux's ~/.bashrc, set
# by a process manager, etc.), that value wins and .env is not allowed to
# clobber it. .env only fills in whatever the environment didn't already
# provide. A missing .env file is fine too -- load_dotenv() is a no-op in
# that case, and ShadowChat falls back to whatever's already in the
# environment exactly as before.
# ---------------------------------------------------------------------------

_ENV_FILE = os.path.join(_APP_ROOT, ".env")
load_dotenv(dotenv_path=_ENV_FILE, override=False)
