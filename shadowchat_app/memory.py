"""
Shared conversation memory: the raw message history plus a running summary
of everything older than the retained window (context-compaction), so the
full transcript is never sent forever.

`history` is a module-level mutable list -- other modules import this
module (not individual names from it) and mutate `memory.history` in
place (`.append`, `.clear`, `[:] = ...`), the same pattern the original
single-file version used for its module-level state. The running summary
is *not* exposed as a plain module attribute, because plain strings get
reassigned (not mutated in place); a `from memory import conversation_summary`
elsewhere would silently go stale the moment this module reassigns it. Use
get_summary()/set_summary() instead, from any module.
"""

from . import providers
from . import ui
from .config import CONTEXT_CONFIG

# Each entry: {"role": "user" | "assistant", "content": "..."}
history = []

_conversation_summary = ""

# Tracks whether we've already warned that the retained verbatim window
# alone exceeds the token budget (see compact_if_needed). Reset by reset()
# and whenever a real compaction succeeds, so the warning can fire again if
# the condition recurs rather than being a one-shot for the whole session.
_recent_overflow_warned = False

SUMMARIZER_SYSTEM_PROMPT = (
    "You compress conversation history. You will be given an optional previous "
    "summary and a block of older conversation turns. Produce ONE updated summary "
    "that preserves: (1) important facts and information shared, (2) decisions "
    "made, (3) stated preferences, (4) ongoing or unfinished tasks and their "
    "status. Be concise and factual -- plain prose or tight bullet points, no "
    "commentary, no meta remarks about being a summary."
)


def get_summary():
    return _conversation_summary


def set_summary(text):
    global _conversation_summary
    _conversation_summary = text or ""


def clear_overflow_warning():
    global _recent_overflow_warned
    _recent_overflow_warned = False


def reset():
    """Wipe history and summary state. Used by /clear and /new."""
    global _conversation_summary
    history.clear()
    _conversation_summary = ""
    clear_overflow_warning()


def _estimate_tokens(text):
    """Rough, provider-agnostic token estimate (~4 characters/token). Each
    provider tokenizes differently, so this is only used to decide *when* to
    compact, not billed anywhere -- close enough for that purpose."""
    return max(1, len(text) // 4)


def _messages_token_estimate(messages):
    return sum(_estimate_tokens(m.get("content", "")) for m in messages)


def history_token_estimate():
    return _messages_token_estimate(history)


def summary_token_estimate():
    return _estimate_tokens(_conversation_summary)


def context_token_estimate():
    return summary_token_estimate() + history_token_estimate()


def compact_if_needed(provider, model):
    """Summarize everything except the most recent messages once the tracked
    conversation crosses CONTEXT_CONFIG['max_tokens']. Mutates `history` and
    the running summary in place. Safe to call every turn -- it's a no-op
    until the budget is actually exceeded, and if the summarization call
    itself fails, history is left untouched (compaction is retried next turn
    rather than losing messages or crashing the chat loop). This runs
    through the *same* adapter interface as a normal chat turn -- whichever
    provider/model is currently active does the summarizing -- so it needs
    no provider-specific code and never depends on a second key."""
    global _conversation_summary, _recent_overflow_warned

    keep = CONTEXT_CONFIG["keep_recent_messages"]
    if len(history) <= keep:
        # Nothing old enough to fold into the summary. Usually a genuine
        # no-op -- but if the retained window itself is already at/over
        # budget (e.g. a few very long pasted messages), silently doing
        # nothing would mean sending an oversized request with no signal
        # to the user. Warn once; re-arm once the condition clears so it
        # can fire again later if it recurs.
        if context_token_estimate() >= CONTEXT_CONFIG["max_tokens"]:
            if not _recent_overflow_warned:
                ui.console.print(
                    f"[yellow]\u26a0 context: the {len(history)} retained message(s) are "
                    f"~{context_token_estimate()} tokens on their own, at/over the "
                    f"{CONTEXT_CONFIG['max_tokens']}-token compaction threshold. There's "
                    f"nothing older left to summarize -- keep_recent_messages is a hard "
                    f"floor. Consider /clear, or avoid pasting very long blocks.[/yellow]"
                )
                _recent_overflow_warned = True
        else:
            _recent_overflow_warned = False
        return
    if context_token_estimate() < CONTEXT_CONFIG["max_tokens"]:
        return

    to_summarize = history[:-keep]
    recent = history[-keep:]

    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in to_summarize)
    prior = f"Previous summary:\n{_conversation_summary}\n\n" if _conversation_summary else ""
    prompt = (
        f"{prior}Older conversation turns to fold into the summary:\n\n{transcript}\n\n"
        f"Produce the updated running summary now."
    )

    try:
        new_summary = providers.PROVIDER_CONFIG[provider]["adapter"](
            model,
            [{"role": "user", "content": prompt}],
            system=SUMMARIZER_SYSTEM_PROMPT,
            max_tokens=CONTEXT_CONFIG["summary_max_tokens"],
        )
    except (providers.ShadowChatAPIError, RuntimeError):
        return  # try again next turn instead of losing or corrupting history

    _conversation_summary = new_summary.strip()
    history[:] = recent
    _recent_overflow_warned = False
    ui.console.print(
        f"[dim]\u21ba context compacted: folded {len(to_summarize)} older message(s) "
        f"into the running summary, kept the last {len(recent)} verbatim.[/dim]"
    )
