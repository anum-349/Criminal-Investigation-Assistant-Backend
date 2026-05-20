"""
services/llm_client.py
─────────────────────────────────────────────────────────────────────────────
Thin wrapper around Ollama's /api/generate endpoint.

Why this exists as a separate module:
    The lead generator should not know HOW to talk to the model — only
    WHAT to ask. This isolates the HTTP layer so we can:
      • Swap Ollama for vLLM/llama.cpp/lmstudio without touching the
        prompt + validation code.
      • Mock the LLM cleanly in unit tests (just stub out `generate`).
      • Centralise retry / timeout / error mapping in one place.

Ollama runs locally on port 11434 by default. The /api/generate endpoint
accepts `format: "json"` to force structured output — Llama's small
quantised models often miss closing braces on long outputs without it,
so we always pass it.

Env vars:
    OLLAMA_HOST       Base URL, default http://localhost:11434
    OLLAMA_MODEL      Model tag, default llama3.1:8b-instruct-q4_K_M
    OLLAMA_TIMEOUT    Generation timeout (seconds), default 60
                      The design doc § 9 expects p99 ~13s on CPU, so 60s
                      gives us a healthy margin before declaring the model
                      stuck.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Iterator, Optional

import requests

log = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────

OLLAMA_HOST    = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL", "llama3.1:8b-instruct-q4_K_M")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))

# Connection-only timeout (separate from total). If Ollama isn't running
# we want to fail fast — 3 seconds is plenty for a localhost connect.
OLLAMA_CONNECT_TIMEOUT = int(os.getenv("OLLAMA_CONNECT_TIMEOUT", "3"))


# ── Exceptions ─────────────────────────────────────────────────────────────

class LLMError(Exception):
    """Base class for everything that can go wrong with the LLM call."""


class LLMUnavailable(LLMError):
    """Ollama itself isn't reachable. Maps to HTTP 503 in the route layer."""


class LLMTimeout(LLMError):
    """The model didn't return inside OLLAMA_TIMEOUT. Maps to HTTP 504."""


class LLMBadResponse(LLMError):
    """The model returned something we can't parse. Maps to HTTP 502."""


# ── Result container ──────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    """One full generation, post-stream-consumption."""
    text: str            # raw concatenated response (may include ```json fences)
    model: str
    duration_ms: int     # wall-clock time to generate
    eval_count: int      # tokens generated (from Ollama's `eval_count`)


# ── Health check ──────────────────────────────────────────────────────────

def is_available() -> bool:
    """Quick liveness probe — used by the lead generator route to return
    503 BEFORE building the (somewhat expensive) context bundle.

    We hit /api/tags rather than /api/version because tags also confirms
    the model registry is reachable; version sometimes returns OK while
    the underlying llama runtime is wedged.
    """
    try:
        r = requests.get(
            f"{OLLAMA_HOST}/api/tags",
            timeout=(OLLAMA_CONNECT_TIMEOUT, 5),
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


def model_is_pulled(model: str = OLLAMA_MODEL) -> bool:
    """Verify the configured model is actually installed locally. Ollama
    will auto-pull on first request, but for a FYP demo we'd rather know
    up-front than have the user wait 5 minutes for a download."""
    try:
        r = requests.get(
            f"{OLLAMA_HOST}/api/tags",
            timeout=(OLLAMA_CONNECT_TIMEOUT, 5),
        )
        if r.status_code != 200:
            return False
        tags = r.json().get("models", [])
        return any(
            m.get("name", "").startswith(model.split(":")[0])
            for m in tags
        )
    except (requests.RequestException, ValueError):
        return False


# ── Core generation ───────────────────────────────────────────────────────

def generate(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = OLLAMA_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    json_mode: bool = True,
) -> LLMResponse:
    """Synchronous one-shot generation. Returns the full response text.

    temperature defaults to 0.2 because lead generation needs to be
    REPRODUCIBLE — same case data should produce the same leads more or
    less. Set higher (~0.7) for brainstorming variants.

    json_mode forces Ollama to constrain output to valid JSON, which is
    critical because every downstream step assumes JSON. Disable only
    for debugging.
    """
    payload = {
        "model":  model,
        "system": system_prompt,
        "prompt": user_prompt,
        "stream": False,
        "options": {
            "temperature":   temperature,
            "num_predict":   max_tokens,
            # Penalty values that produce coherent, non-repetitive output
            # on Llama 3.1 8B per the model card recommendations.
            "repeat_penalty": 1.1,
            "top_p":          0.9,
        },
    }
    if json_mode:
        payload["format"] = "json"

    try:
        r = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json=payload,
            timeout=(OLLAMA_CONNECT_TIMEOUT, OLLAMA_TIMEOUT),
        )
    except requests.ConnectionError as e:
        log.error("Ollama unreachable at %s: %s", OLLAMA_HOST, e)
        raise LLMUnavailable(f"Ollama not reachable at {OLLAMA_HOST}") from e
    except requests.Timeout as e:
        log.error("Ollama generation timed out after %ss", OLLAMA_TIMEOUT)
        raise LLMTimeout(f"Generation exceeded {OLLAMA_TIMEOUT}s") from e
    except requests.RequestException as e:
        log.exception("Ollama request failed")
        raise LLMError(f"Ollama request failed: {e}") from e

    if r.status_code != 200:
        log.error("Ollama returned %s: %s", r.status_code, r.text[:500])
        raise LLMBadResponse(f"Ollama HTTP {r.status_code}: {r.text[:200]}")

    try:
        body = r.json()
    except ValueError as e:
        raise LLMBadResponse(f"Ollama returned non-JSON envelope: {r.text[:200]}") from e

    text = body.get("response", "")
    if not text:
        raise LLMBadResponse("Ollama returned an empty `response` field")

    return LLMResponse(
        text=text,
        model=body.get("model", model),
        duration_ms=int(body.get("total_duration", 0) / 1_000_000),  # ns → ms
        eval_count=int(body.get("eval_count", 0)),
    )


# ── Streaming variant (for SSE/NDJSON endpoint) ────────────────────────────

def generate_stream(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = OLLAMA_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    json_mode: bool = True,
) -> Iterator[str]:
    """Yield partial response chunks as they arrive from Ollama. Used by
    the route handler to stream to the browser via NDJSON.

    NOTE: even with json_mode=True, Ollama streams the JSON token-by-token
    — so the consumer can't json.loads() until the whole thing is in. The
    route handler buffers internally; we expose streaming here to keep
    the connection open and give the UI a "loading…" tick.
    """
    payload = {
        "model":  model,
        "system": system_prompt,
        "prompt": user_prompt,
        "stream": True,
        "options": {
            "temperature":    temperature,
            "num_predict":    max_tokens,
            "repeat_penalty": 1.1,
            "top_p":          0.9,
        },
    }
    if json_mode:
        payload["format"] = "json"

    try:
        with requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json=payload,
            timeout=(OLLAMA_CONNECT_TIMEOUT, OLLAMA_TIMEOUT),
            stream=True,
        ) as r:
            if r.status_code != 200:
                raise LLMBadResponse(f"Ollama HTTP {r.status_code}")

            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except ValueError:
                    log.debug("Skipping non-JSON stream line: %r", line[:80])
                    continue
                piece = chunk.get("response", "")
                if piece:
                    yield piece
                if chunk.get("done"):
                    break

    except requests.ConnectionError as e:
        raise LLMUnavailable(f"Ollama not reachable at {OLLAMA_HOST}") from e
    except requests.Timeout as e:
        raise LLMTimeout(f"Stream exceeded {OLLAMA_TIMEOUT}s") from e
    except requests.RequestException as e:
        raise LLMError(f"Stream failed: {e}") from e


# ── Smoke test (run as module) ─────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not is_available():
        log.error("Ollama not running. Start with `ollama serve`.")
        raise SystemExit(1)
    if not model_is_pulled():
        log.error("Model %s not pulled. Run `ollama pull %s`.", OLLAMA_MODEL, OLLAMA_MODEL)
        raise SystemExit(1)

    resp = generate(
        system_prompt="You output only valid JSON.",
        user_prompt='Return a JSON object with one key "ok" set to true.',
    )
    log.info("Smoke test response in %dms: %s", resp.duration_ms, resp.text)