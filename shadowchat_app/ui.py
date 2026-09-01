"""
Terminal UI: the Rich console, the prompt_toolkit input session, and all
output formatting. This module holds no application/conversation state --
it only renders what it's given and reads what the user types.
"""

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from . import config
from . import providers

console = Console()


def make_session():
    """Build the interactive input session: tab-completes commands,
    provider names, and persona names, and persists input history across
    runs (arrow keys)."""
    completer = WordCompleter(
        config.COMMANDS + providers.PROVIDERS + list(config.PERSONAS), ignore_case=True
    )
    return PromptSession(
        history=FileHistory(config.HISTORY_FILE),
        auto_suggest=AutoSuggestFromHistory(),
        completer=completer,
        complete_while_typing=True,
    )


def print_banner(current, model):
    console.print("[bold magenta]=== ShadowChat (ShadowOS) ===[/bold magenta]")
    console.print(f"Active model: [bold]{current}[/bold] ({model})")


def print_help():
    console.print(
        "\n[bold]Commands[/bold]\n"
        f"  [cyan]/model[/cyan] <{'|'.join(providers.PROVIDERS)}>            switch provider, keep its current model\n"
        "  [cyan]/model[/cyan] <provider> <model>       switch provider and set its model\n"
        "  [cyan]/models[/cyan]                          list providers, active model, and defaults\n"
        "  [cyan]/persona[/cyan]                         list personas (default/developer/research/concise)\n"
        "  [cyan]/persona[/cyan] <name>                  switch persona, conversation memory preserved\n"
        "  [cyan]/status[/cyan]                          active provider/model, message count, context, key status\n"
        "  [cyan]/context[/cyan]                         detailed estimated token usage vs. the context limit\n"
        "  [cyan]/new[/cyan]                             start a fresh, unsaved conversation\n"
        "  [cyan]/clear[/cyan]                           wipe conversation memory (asks to confirm)\n"
        "  [cyan]/clear[/cyan] --force                  wipe immediately, no confirmation\n"
        "  [cyan]/save[/cyan] <name>                     save the current conversation to disk\n"
        "  [cyan]/load[/cyan] <name>                     load a saved conversation (replaces current)\n"
        "  [cyan]/chats[/cyan]                            list saved conversations\n"
        "  [cyan]/delete[/cyan] <name>                   delete a saved conversation\n"
        "  [cyan]/rename[/cyan] <name>                   rename the active saved conversation\n"
        "  [cyan]/help[/cyan]                            show this message\n"
        "  [cyan]/exit[/cyan]                            quit ShadowChat\n"
        "\n[dim]Tip: arrow keys move the cursor / recall history. Tab completes commands.\n"
        "Older messages are auto-summarized as the conversation grows -- see /models "
        "for context status. Chat names: letters, numbers, - and _ only.[/dim]\n"
    )


def thinking_status(current, model):
    """Context manager: shows a spinner while a provider call is in flight.
    For a streaming reply this only covers the setup phase -- key lookup,
    opening the connection, and (if triggered) a blocking context-compaction
    call -- since Rich only supports one Live display per console at a
    time, and stream_reply's own Live display takes over once actual tokens
    start arriving."""
    return console.status(f"[dim]{current} ({model}) is thinking...[/dim]", spinner="dots")


def stream_reply(current, chunks):
    """Render a streaming reply live as it arrives: prints the provider's
    colored header once, then re-renders the accumulated text as Markdown
    each time a new chunk comes in. Returns the full reply text once
    `chunks` is exhausted.

    If `chunks` raises partway through (e.g. providers.ShadowChatAPIError
    from a dropped connection), whatever was rendered so far stays on
    screen -- Live always finalizes its last frame on the way out, even on
    an exception -- and the exception then propagates to the caller.
    Callers should treat a raised exception here the same as the
    non-streaming path: no complete reply, don't record a partial one."""
    color = providers.PROVIDER_CONFIG.get(current, {}).get("color", "white")
    console.print(f"[bold {color}]{current}>[/bold {color}]")
    text = ""
    # refresh_per_second caps how often the terminal actually redraws, not
    # how often we call .update() -- fine to update on every chunk.
    with Live("", console=console, refresh_per_second=12, transient=False) as live:
        for chunk in chunks:
            text += chunk
            live.update(Markdown(text))
    console.print()
    return text


def print_error(message):
    console.print(f"[red][error] {message}[/red]")
