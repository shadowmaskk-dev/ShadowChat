# ShadowChat

ShadowChat is a lightweight terminal AI chatbot for Groq, Google Gemini, Anthropic (Claude), and OpenAI (GPT) — one shared conversation, streamed replies, and local save/load, all from a single Python script.

## Features

- **Multiple providers** — switch between Groq, Gemini, Anthropic, and GPT mid-conversation with `/model`; conversation memory carries over
- **Streaming replies** — responses render live, token by token, as Markdown
- **Automatic context management** — older messages are summarized once a token budget is crossed, so long conversations don't grow forever or blow past a provider's limits
- **Local conversation storage** — save, load, list, rename, and delete named conversations, stored as plain JSON files (no database)
- **Personas** — switch the assistant's system prompt on the fly (`default`, `developer`, `research`, `concise`) without losing conversation history
- **Automatic retry** — temporary network errors and 5xx server errors are retried with a small exponential backoff; invalid keys, bad requests, and rate limits are not
- **Terminal-friendly input** — tab-completion for commands/providers/personas, and persistent input history (arrow keys) across sessions
- **Environment-only API keys** — keys are read only from environment variables or a local `.env` file, never hardcoded or logged

## Requirements

- Python 3.9 or newer
- pip
- Git (to clone the repository)
- An API key for at least one supported provider: [Groq](https://console.groq.com/), [Google Gemini](https://ai.google.dev/), [Anthropic](https://console.anthropic.com/), or [OpenAI](https://platform.openai.com/)

ShadowChat is developed and tested for use on Termux (Android), and also runs on Linux and macOS.

## Installation

### Termux

1. Update packages:
   ```
   pkg update && pkg upgrade
   ```
2. Install Git and Python:
   ```
   pkg install git python
   ```
3. Clone the repository:
   ```
   git clone https://github.com/shadowmaskk-dev/ShadowChat
   ```
4. Enter the project directory:
   ```
   cd ShadowChat
   ```
5. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

The same steps work on Linux and macOS — just use your system's package manager (e.g. `apt`, `brew`) instead of `pkg` for step 1–2.

## API Key Setup

1. Copy the example file to `.env` inside the project directory:
   ```
   cp .env.example .env
   ```
2. Open `.env` and add the API key(s) for the provider(s) you want to use.
3. You only need to fill in the provider(s) you actually plan to use — leave the rest blank.
4. **Never commit your `.env` file.** It's already excluded via `.gitignore`, and should stay that way.

Example `.env` (with placeholder values, not real keys):

```
GROQ_API_KEY=your-groq-key-here
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
```

`.env` is loaded automatically at startup. A key already exported in your shell environment always takes priority over `.env`.

## Running ShadowChat

From inside the project directory:

```
python shadowchat.py
```

## Commands

| Command | Description |
|---|---|
| `/model <provider>` | Switch the active provider (`groq`, `gemini`, `anthropic`, `gpt`), keeping that provider's current model |
| `/model <provider> <model>` | Switch provider and set a specific model for it |
| `/models` | List all providers, the active model for each, and context status |
| `/persona` | List available personas, with the active one marked |
| `/persona <name>` | Switch persona (`default`, `developer`, `research`, `concise`); conversation memory is preserved |
| `/status` | Show active provider/model, message count, estimated context usage, and which providers have a key configured |
| `/context` | Show a detailed breakdown of estimated token usage vs. the context limit |
| `/new` | Start a fresh, unsaved conversation |
| `/clear` | Clear conversation memory (asks for confirmation) |
| `/clear --force` | Clear conversation memory immediately, no confirmation |
| `/save <name>` | Save the current conversation to disk |
| `/load <name>` | Load a saved conversation (replaces the current one) |
| `/chats` | List all saved conversations |
| `/delete <name>` | Delete a saved conversation |
| `/rename <name>` | Rename the currently active saved conversation |
| `/help` | Show the list of commands |
| `/exit` | Quit ShadowChat |

## Project Structure

```
ShadowChat/
├── shadowchat.py         # Entry point — run this to start the app
├── shadowchat_app/
│   ├── config.py         # Constants, .env loading, on-disk paths, personas, retry/context settings
│   ├── providers.py      # Provider adapters (Groq, Gemini, Anthropic, GPT) + registry
│   ├── memory.py         # Conversation history + context-compaction summary
│   ├── storage.py        # Saved-chat JSON files (save/load/list/delete/rename)
│   ├── commands.py       # Slash-command parsing and handling
│   ├── ui.py             # Terminal output (Rich) and input (prompt_toolkit)
│   └── core.py           # Main application loop
├── requirements.txt      # Python dependencies
├── .env.example          # Template for API keys — copy to .env
└── .gitignore
```

- **`shadowchat.py`** just calls into the `shadowchat_app` package — all behavior lives there.
- **`shadowchat_app/core.py`** ties everything together: reads input, dispatches commands, and runs each chat turn (send message, check context, call the provider, stream the reply).
- Each other module owns one concern, matching the list above — providers, memory, storage, commands, and terminal UI are kept separate.

## Conversation and Context Management

During a session, messages are kept in memory as you chat. Once the estimated token usage of the conversation (recent messages plus any existing summary) crosses a configured limit, ShadowChat automatically folds the older messages into a running summary — generated by whichever provider is currently active — while always keeping the most recent messages verbatim. This keeps long conversations from growing without bound or exceeding a provider's context limit. Token usage is estimated with a lightweight, provider-independent heuristic; no extra API calls are made just to measure it. `/status` and `/context` show the current numbers.

Conversations can also be saved to disk with `/save <name>`, stored as a plain JSON file under a `.shadowchat_chats/` folder next to the script — no database involved. A saved file includes the message history, the context summary, the active provider/model, and the active persona, so `/load <name>` restores the conversation exactly as it was. `/chats`, `/rename`, and `/delete` manage saved conversations from there.

## Security

API keys are only ever read from environment variables (including ones loaded from `.env`) — never hardcoded in source code, never written to conversation files, and never printed to the terminal. `.gitignore` excludes `.env` (and any `.env.*` variant except `.env.example`), saved conversation data, input history, and cache files, so none of that can end up committed to the repository.

## License

No license has been specified for this project yet.
