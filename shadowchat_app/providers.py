"""
Provider adapters: each adapter takes a model name plus the message list to
send and returns the assistant's reply as a string. Each is responsible for
translating the shared message format into its own API shape. None of them
read conversation state directly -- callers (a normal chat turn *and*
context-summarization) decide exactly what goes in `messages` and `system`,
which is what lets the same adapters serve both.

Also owns the provider registry (PROVIDER_CONFIG / PROVIDERS): the single
source of truth for which env var holds each provider's key, which function
makes the call, what color it prints in, and its default model. Adding a
new provider or changing a default model means editing only this file.
"""

import json
import os
import time

import requests

from . import config

SYSTEM_PROMPT = config.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------
# Every adapter routes its HTTP call through _post() below, so all four
# providers get identical, consistent error messages. Messages are built
# only from the provider name and status code/exception type -- never from
# request headers or the key itself, so an API key can never leak into a
# printed error.
# ---------------------------------------------------------------------------

class ShadowChatAPIError(Exception):
    """A provider call failed in a well-understood way (bad key, rate limit,
    missing model, provider outage, or no connection). The message is always
    safe to print as-is."""

    def __init__(self, provider, message):
        self.provider = provider
        super().__init__(message)


def _get_key(provider):
    """Look up the API key for a provider via PROVIDER_CONFIG (built below,
    after the adapters). Resolved at call time, so the forward reference is
    safe -- by the time a chat turn actually runs, the module is fully
    loaded.

    os.environ is the *only* source read here -- config.py's .env loading
    (see config.py) just populates os.environ too, so this stays true
    whether a key came from a real shell export or a .env file. No key is
    ever read from a config file value, a hardcoded default, or a command-
    line argument.

    Security note for every adapter below: always send the key via an HTTP
    header (Authorization / x-api-key / x-goog-api-key), never as a URL
    query parameter. A key embedded in the URL ends up inside
    `requests.exceptions.*` objects (they stringify the request URL), which
    are chained onto ShadowChatAPIError via `raise ... from e`; an uncaught
    exception would then print that chain, including the raw URL, straight
    to the terminal. Headers don't have this problem -- request exceptions
    don't include header contents in their string representation."""
    env_var = PROVIDER_CONFIG[provider]["api_key_env"]
    key = os.environ.get(env_var)
    if not key:
        raise RuntimeError(f"{env_var} not set")
    return key


def _describe_status(provider, model, status_code):
    if status_code == 401:
        return (
            f"{provider}: invalid API key (401). Double-check the "
            f"{provider.upper()}_API_KEY value -- it's missing, wrong, or expired."
        )
    if status_code == 403:
        return (
            f"{provider}: permission denied (403). Your API key doesn't have "
            f"access to this model or endpoint (e.g. no billing set up, or the "
            f"model needs a different access tier)."
        )
    if status_code == 404:
        return (
            f"{provider}: model or endpoint not found (404). \"{model}\" may not "
            f"exist, be renamed/retired, or the API URL is wrong. Check /models."
        )
    if status_code == 429:
        return (
            f"{provider}: rate limited (429). Too many requests or over your "
            f"quota -- wait a bit before trying again."
        )
    if status_code >= 500:
        return (
            f"{provider}: server error ({status_code}). This is on {provider}'s "
            f"end, not yours -- try again in a moment."
        )
    return f"{provider}: request failed ({status_code})."


def _is_retryable_status(status_code):
    """Only a 5xx counts as a temporary, worth-retrying failure. Every
    4xx -- 401 (bad key), 403 (no access), 404 (unknown model), 429 (rate
    limited), or any other 4xx (malformed request) -- means repeating the
    exact same request won't succeed, so none of them are retried.

    429 specifically is a deliberate choice, not an oversight: blindly
    retrying a rate limit with our own short fixed backoff (rather than
    honoring whatever the provider actually wants, e.g. a Retry-After
    header) is more likely to make the rate limit worse than to help."""
    return status_code >= 500


def _attempt_post(provider, url, **kwargs):
    """A single request attempt. Returns the response for any status the
    server actually returned, even non-2xx -- the caller (_connect) is the
    one that decides retry policy based on status. Raises
    ShadowChatAPIError immediately if no response came back at all (the
    request never reached the server, or never got a reply)."""
    try:
        return requests.post(url, **kwargs)
    except requests.exceptions.Timeout as e:
        raise ShadowChatAPIError(
            provider, f"{provider}: connection failure -- request timed out. Check your network."
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise ShadowChatAPIError(
            provider, f"{provider}: connection failure -- couldn't reach the server. Check your network."
        ) from e
    except requests.exceptions.RequestException as e:
        raise ShadowChatAPIError(
            provider, f"{provider}: connection failure -- {type(e).__name__}. Check your network."
        ) from e


def _connect(provider, model, url, **kwargs):
    """Shared by _post and _post_stream: opens the HTTP connection and
    raises ShadowChatAPIError for connection failures or a non-2xx status,
    before any body (buffered or streamed) is read. `model` is only used
    for error messages (e.g. which model 404'd).

    Retries a small, fixed number of times with exponential backoff, but
    ONLY for failures that are plausibly temporary: no response came back
    at all (timeout, DNS/connection error, or another transient requests
    exception), or the server responded with a 5xx (its own problem, not
    ours). Anything else -- a bad key, no access, an unknown model, a
    malformed request, or a rate limit -- fails on the very first attempt,
    since retrying wouldn't help (or, for a rate limit, could make things
    worse). See _is_retryable_status for that split.

    This only covers the "attempt to get a response" phase -- it never
    retries a request that has already started streaming a body. Once
    _post_stream hands a response back to one of the _iter_*_sse
    generators, a connection drop mid-stream is that generator's own
    concern (see providers.py's SSE parsers), and is deliberately never
    retried there either: silently resending a message after the user has
    already seen some of a partial reply would be surprising and could
    duplicate content."""
    max_attempts = config.RETRY_CONFIG["max_attempts"]
    base_delay = config.RETRY_CONFIG["base_delay"]

    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = _attempt_post(provider, url, **kwargs)
        except ShadowChatAPIError as e:
            last_error = e  # a connection-level failure -- always retryable
        else:
            if resp.ok:
                return resp
            if not _is_retryable_status(resp.status_code):
                raise ShadowChatAPIError(provider, _describe_status(provider, model, resp.status_code))
            last_error = ShadowChatAPIError(provider, _describe_status(provider, model, resp.status_code))

        if attempt < max_attempts:
            time.sleep(base_delay * (2 ** (attempt - 1)))

    raise last_error


def _post(provider, model, url, **kwargs):
    """POST wrapper for a normal (buffered) request: the full body is
    already loaded onto `resp` when this returns."""
    return _connect(provider, model, url, **kwargs)


def _post_stream(provider, model, url, **kwargs):
    """POST wrapper for a streaming request: same connection/status error
    handling as _post, but opens the connection with stream=True and
    returns the response with its body not yet read. The caller iterates
    it (typically via one of the _iter_*_sse generators below) and is
    responsible for closing it -- each of those generators does so via a
    `with resp:` block."""
    kwargs["stream"] = True
    return _connect(provider, model, url, **kwargs)


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------
# A 2xx status is not a guarantee that the body is valid JSON, or that it
# has the shape a given adapter expects -- providers can return a
# differently-shaped success payload (an error/moderation object with a
# 200 status, an empty choices/candidates list, a field renamed by an API
# update, etc.). Both non-streaming adapters and the streaming SSE parsers
# below route every access to response content through these, instead of
# indexing into resp.json() directly, so a malformed or unexpected
# response always turns into a clear ShadowChatAPIError -- never a raw
# KeyError/IndexError/TypeError/json.JSONDecodeError traceback.
# ---------------------------------------------------------------------------

def _safe_json(provider, resp):
    """Parse `resp` as JSON, raising ShadowChatAPIError (with a short,
    non-sensitive preview of the raw body) instead of letting
    json.JSONDecodeError propagate if the body isn't valid JSON."""
    try:
        return resp.json()
    except ValueError as e:  # json.JSONDecodeError (and requests' own
        # JSONDecodeError variant) are both ValueError subclasses.
        preview = ""
        try:
            preview = resp.text[:200].replace("\n", " ").strip()
        except Exception:
            pass  # best-effort only; never let preview-building itself crash the error path
        detail = f" Raw response started with: {preview!r}" if preview else ""
        raise ShadowChatAPIError(
            provider,
            f"{provider}: response wasn't valid JSON despite a successful status "
            f"-- the provider may be having issues.{detail}",
        ) from e


def _extract_text(provider, data, *path):
    """Walk `data` through a sequence of dict keys / list indices in
    `path`, raising a clear ShadowChatAPIError instead of letting a
    KeyError, IndexError, or TypeError propagate if the response doesn't
    have the exact shape this adapter expects. Also verifies the final
    value is actually a string (not None, a dict, a list, ...) before
    returning it -- an empty string is left alone, since a legitimately
    empty reply isn't a malformed one."""
    node = data
    for step in path:
        try:
            node = node[step]
        except (KeyError, IndexError, TypeError):
            path_desc = ".".join(str(s) for s in path)
            raise ShadowChatAPIError(
                provider,
                f"{provider}: unexpected response shape -- couldn't find '{path_desc}' "
                f"in the response. The provider's API may have changed, or it returned "
                f"an error/empty payload despite a successful status."
            ) from None
    if not isinstance(node, str):
        path_desc = ".".join(str(s) for s in path)
        raise ShadowChatAPIError(
            provider,
            f"{provider}: unexpected response shape -- expected text at '{path_desc}', "
            f"got {type(node).__name__} instead."
        )
    return node


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

def call_groq(model, messages, system=SYSTEM_PROMPT, max_tokens=None):
    key = _get_key("groq")
    payload = {"model": model, "messages": [{"role": "system", "content": system}] + messages}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    resp = _post(
        "groq",
        model,
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=60,
    )
    return _extract_text("groq", _safe_json("groq", resp), "choices", 0, "message", "content")


def call_gpt(model, messages, system=SYSTEM_PROMPT, max_tokens=None):
    key = _get_key("gpt")
    payload = {"model": model, "messages": [{"role": "system", "content": system}] + messages}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    resp = _post(
        "gpt",
        model,
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=60,
    )
    return _extract_text("gpt", _safe_json("gpt", resp), "choices", 0, "message", "content")


def call_anthropic(model, messages, system=SYSTEM_PROMPT, max_tokens=1024):
    key = _get_key("anthropic")
    resp = _post(
        "anthropic",
        model,
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        },
        timeout=60,
    )
    return _extract_text("anthropic", _safe_json("anthropic", resp), "content", 0, "text")


def call_gemini(model, messages, system=SYSTEM_PROMPT, max_tokens=None):
    key = _get_key("gemini")
    # Gemini uses role "model" instead of "assistant", and nests text in parts
    contents = [
        {
            "role": "model" if turn["role"] == "assistant" else "user",
            "parts": [{"text": turn["content"]}],
        }
        for turn in messages
    ]
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
    }
    if max_tokens is not None:
        payload["generationConfig"] = {"maxOutputTokens": max_tokens}
    resp = _post(
        "gemini",
        model,
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key},
        json=payload,
        timeout=60,
    )
    return _extract_text(
        "gemini", _safe_json("gemini", resp), "candidates", 0, "content", "parts", 0, "text"
    )


# ---------------------------------------------------------------------------
# Streaming adapters
# ---------------------------------------------------------------------------
# Each call_*_stream() mirrors its non-streaming call_*() sibling above, but
# returns a generator of text chunks instead of the full reply string, so
# ShadowChat can display tokens as they arrive. The non-streaming adapters
# above are untouched and still used for context-summarization (memory.py),
# which never shows its result to the user, so there's nothing to gain from
# streaming it.
#
# Design note on error timing: each call_*_stream() function does its key
# lookup and opens the HTTP connection (via _post_stream -> _connect, which
# raises on a bad key, no connection, or a non-2xx status) *before*
# returning anything. Only the actual "iterate and parse chunks" part is a
# generator. This matters because a bare `def f(): yield ...` never runs any
# of its body -- not even code before the first `yield` -- until first
# iterated; if the key lookup happened inside the generator, a missing key
# wouldn't raise until the caller had already printed the reply header.
# Splitting eager setup (this function) from lazy parsing (the _iter_*_sse
# helpers) keeps "fails immediately, before any output" behavior identical
# to the non-streaming path.
# ---------------------------------------------------------------------------

def _iter_openai_style_sse(resp, provider):
    """Parses an OpenAI-compatible SSE stream (used by both Groq and GPT)
    into plain text chunks. A malformed frame is skipped rather than
    aborting the whole reply; a connection drop mid-stream is turned into
    ShadowChatAPIError. Always closes `resp`."""
    try:
        with resp:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"]
                    content = delta.get("content") if isinstance(delta, dict) else None
                except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
                    continue  # skip a malformed/unexpected frame rather than aborting the whole reply
                if content:
                    yield content
    except requests.exceptions.RequestException as e:
        raise ShadowChatAPIError(
            provider, f"{provider}: connection dropped mid-response -- {type(e).__name__}. Check your network."
        ) from e


def call_groq_stream(model, messages, system=SYSTEM_PROMPT, max_tokens=None):
    key = _get_key("groq")
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}] + messages,
        "stream": True,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    resp = _post_stream(
        "groq",
        model,
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=60,
    )
    return _iter_openai_style_sse(resp, "groq")


def call_gpt_stream(model, messages, system=SYSTEM_PROMPT, max_tokens=None):
    key = _get_key("gpt")
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}] + messages,
        "stream": True,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    resp = _post_stream(
        "gpt",
        model,
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=60,
    )
    return _iter_openai_style_sse(resp, "gpt")


def call_anthropic_stream(model, messages, system=SYSTEM_PROMPT, max_tokens=1024):
    key = _get_key("anthropic")
    resp = _post_stream(
        "anthropic",
        model,
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "stream": True,
        },
        timeout=60,
    )
    return _iter_anthropic_sse(resp)


def _iter_anthropic_sse(resp):
    """Parses Anthropic's Messages API SSE stream into plain text chunks.
    Only content_block_delta events with a text_delta carry text; the rest
    (message_start, content_block_start/stop, pings, message_delta,
    message_stop) are metadata and ignored. An `error` event mid-stream
    (Anthropic sends these as their own event type, not an HTTP status) is
    turned into ShadowChatAPIError. Always closes `resp`."""
    try:
        with resp:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue  # not a well-formed event object -- skip it

                event_type = obj.get("type")
                if event_type == "content_block_delta":
                    delta = obj.get("delta")
                    if isinstance(delta, dict) and delta.get("type") == "text_delta":
                        text = delta.get("text")
                        if isinstance(text, str) and text:
                            yield text
                elif event_type == "error":
                    err = obj.get("error")
                    message = err.get("message") if isinstance(err, dict) else None
                    raise ShadowChatAPIError("anthropic", f"anthropic: {message or 'stream error'}")
    except requests.exceptions.RequestException as e:
        raise ShadowChatAPIError(
            "anthropic", f"anthropic: connection dropped mid-response -- {type(e).__name__}. Check your network."
        ) from e


def call_gemini_stream(model, messages, system=SYSTEM_PROMPT, max_tokens=None):
    key = _get_key("gemini")
    contents = [
        {
            "role": "model" if turn["role"] == "assistant" else "user",
            "parts": [{"text": turn["content"]}],
        }
        for turn in messages
    ]
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
    }
    if max_tokens is not None:
        payload["generationConfig"] = {"maxOutputTokens": max_tokens}
    resp = _post_stream(
        "gemini",
        model,
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse",
        headers={"x-goog-api-key": key},
        json=payload,
        timeout=60,
    )
    return _iter_gemini_sse(resp)


def _iter_gemini_sse(resp):
    """Parses Gemini's streamGenerateContent SSE output (alt=sse) into
    plain text chunks. Each frame has the same shape as the non-streaming
    response, just one candidate/part fragment at a time. Always closes
    `resp`."""
    try:
        with resp:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                try:
                    text = json.loads(data)["candidates"][0]["content"]["parts"][0]["text"]
                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                    continue
                if isinstance(text, str) and text:
                    yield text
    except requests.exceptions.RequestException as e:
        raise ShadowChatAPIError(
            "gemini", f"gemini: connection dropped mid-response -- {type(e).__name__}. Check your network."
        ) from e


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDER_CONFIG = {
    "groq": {
        "default_model": "llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
        "adapter": call_groq,
        "stream_adapter": call_groq_stream,
        "color": "orange3",
    },
    "gemini": {
        "default_model": "gemini-flash-latest",
        "api_key_env": "GEMINI_API_KEY",
        "adapter": call_gemini,
        "stream_adapter": call_gemini_stream,
        "color": "royal_blue1",
    },
    "anthropic": {
        "default_model": "claude-sonnet-4-5",
        "api_key_env": "ANTHROPIC_API_KEY",
        "adapter": call_anthropic,
        "stream_adapter": call_anthropic_stream,
        "color": "sandy_brown",
    },
    "gpt": {
        "default_model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "adapter": call_gpt,
        "stream_adapter": call_gpt_stream,
        "color": "spring_green3",
    },
}

PROVIDERS = list(PROVIDER_CONFIG.keys())
