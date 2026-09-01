#!/usr/bin/env python3
"""
ShadowChat - part of the ShadowOS ecosystem
A terminal AI chatbot with toggleable providers (Groq, Gemini, Anthropic, GPT)
and one shared conversation memory across all of them.

This file is just the entry point -- the implementation lives in the
shadowchat_app package (see shadowchat_app/__init__.py for the module map).

Setup:
    pip install -r requirements.txt

Set whichever provider(s) you plan to use as environment variables -- either
export them directly, or copy .env.example to .env next to this script and
fill it in (auto-loaded at startup via python-dotenv; already-exported shell
variables always take priority over .env):
    export GROQ_API_KEY="..."
    export GEMINI_API_KEY="..."
    export ANTHROPIC_API_KEY="..."
    export OPENAI_API_KEY="..."

Run:
    python shadowchat.py
"""

from shadowchat_app.core import main

if __name__ == "__main__":
    main()
