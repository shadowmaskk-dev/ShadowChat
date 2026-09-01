"""
ShadowChat application package.

Modules, by concern:
    config      -- constants, .env loading, on-disk paths
    providers   -- provider adapters (Groq, Gemini, Anthropic, GPT) + registry
    memory      -- shared conversation history + context-compaction summary
    storage     -- persistent named-chat save/load/list/delete (plain JSON)
    commands    -- slash-command parsing and handling
    ui          -- terminal I/O: Rich console, prompt_toolkit session, output
    core        -- the run loop that ties everything together

Import order matters for avoiding cycles: config -> providers -> ui -> memory
-> {commands, storage} -> core. Nothing above ui/memory in that chain imports
back down into it.
"""
