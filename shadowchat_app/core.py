"""
Core application loop: reads input, dispatches slash commands, and handles
normal chat turns (send the message, compact context if needed, call the
active provider, print the reply). This is the only module that knows the
overall shape of "one turn of ShadowChat" -- everything else is a building
block it calls.
"""

from dataclasses import dataclass
from typing import Optional

from . import commands
from . import config
from . import memory
from . import providers
from . import ui


@dataclass
class SessionState:
    """Everything about the running session that isn't conversation memory
    itself (that lives in memory.py) or a saved-chat file (that lives on
    disk, via storage.py) -- just which provider/model and persona are
    active right now, and which saved chat (if any) is currently loaded."""
    current: str
    models: dict
    persona: str = "default"
    active_chat_name: Optional[str] = None


def _default_state():
    return SessionState(
        current="gemini",  # default provider
        # Each provider remembers its own selected model, starting at its
        # default. Switching providers with plain "/model <provider>" keeps
        # whatever model was last picked for that provider.
        models={p: cfg["default_model"] for p, cfg in providers.PROVIDER_CONFIG.items()},
        persona="default",
    )


def _handle_chat_turn(user_input, state):
    memory.history.append({"role": "user", "content": user_input})
    try:
        with ui.thinking_status(state.current, state.models[state.current]):
            memory.compact_if_needed(state.current, state.models[state.current])
            persona_prompt = config.PERSONAS.get(state.persona, config.SYSTEM_PROMPT)
            effective_system = persona_prompt
            summary = memory.get_summary()
            if summary:
                effective_system = (
                    f"{persona_prompt}\n\nSummary of earlier conversation (older messages "
                    f"were compacted to save context):\n{summary}"
                )
            # Eager part only: key lookup + opening the connection happen
            # here, still under the spinner. Nothing has been printed for
            # this reply yet, so a bad key or failed connection surfaces
            # exactly like the non-streaming path always did. `chunks` is a
            # generator -- consuming it (below, outside the spinner, via
            # ui.stream_reply) is what actually streams the tokens.
            chunks = providers.PROVIDER_CONFIG[state.current]["stream_adapter"](
                state.models[state.current], memory.history, system=effective_system
            )
        reply = ui.stream_reply(state.current, chunks)
    except RuntimeError as e:
        ui.print_error(e)
        memory.history.pop()  # don't keep a user turn that never got a reply
        return
    except providers.ShadowChatAPIError as e:
        ui.print_error(e)
        memory.history.pop()
        return

    memory.history.append({"role": "assistant", "content": reply})


def main():
    state = _default_state()
    session = ui.make_session()

    ui.print_banner(state.current, state.models[state.current])
    ui.print_help()

    while True:
        try:
            user_input = session.prompt("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            ui.console.print("\n[dim]Exiting ShadowChat.[/dim]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = user_input.split()
            cmd = parts[0].lower()
            keep_going = commands.dispatch(cmd, parts, state, session)
            if not keep_going:
                break
            continue

        _handle_chat_turn(user_input, state)


if __name__ == "__main__":
    main()
