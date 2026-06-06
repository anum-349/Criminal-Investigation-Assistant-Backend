
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Iterator, Optional

from dotenv import load_dotenv
import requests

log = logging.getLogger(__name__)

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_TIMEOUT = int(os.getenv("GROQ_TIMEOUT", "60"))

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

OLLAMA_MODEL   = GROQ_MODEL
OLLAMA_TIMEOUT = GROQ_TIMEOUT
OLLAMA_HOST    = "https://api.groq.com"

_CONNECT_TIMEOUT = 10

class LLMError(Exception):
    """Base class for everything that can go wrong with the LLM call."""

class LLMUnavailable(LLMError):
    """Groq API isn't reachable. Maps to HTTP 503."""

class LLMTimeout(LLMError):
    """Model didn't return in time. Maps to HTTP 504."""

class LLMBadResponse(LLMError):
    """Model returned something unparseable. Maps to HTTP 502."""

@dataclass
class LLMResponse:
    text: str
    model: str
    duration_ms: int
    eval_count: int     

def is_available() -> bool:
    """Liveness probe — checks Groq is reachable and key is set."""
    if not GROQ_API_KEY:
        log.warning("GROQ_API_KEY not set in .env")
        return False
    try:
        r = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            timeout=(_CONNECT_TIMEOUT, 10),
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


def model_is_pulled(model: str = GROQ_MODEL) -> bool:
    """Always True for Groq — no local download needed."""
    return True

def generate(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = GROQ_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 512,
    json_mode: bool = True,
) -> LLMResponse:
    """One-shot generation via Groq's OpenAI-compatible API."""

    if not GROQ_API_KEY:
        raise LLMUnavailable("GROQ_API_KEY is not set. Add it to your .env file.")

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature":  temperature,
        "max_tokens":   max_tokens,
        "top_p":        0.9,
    }

    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    start = time.perf_counter()

    try:
        r = requests.post(
            GROQ_API_URL,
            headers=headers,
            json=payload,
            timeout=(_CONNECT_TIMEOUT, GROQ_TIMEOUT),
        )
    except requests.ConnectionError as e:
        log.error("Groq API unreachable: %s", e)
        raise LLMUnavailable(f"Groq API not reachable: {e}") from e
    except requests.Timeout as e:
        log.error("Groq generation timed out after %ss", GROQ_TIMEOUT)
        raise LLMTimeout(f"Groq generation exceeded {GROQ_TIMEOUT}s") from e
    except requests.RequestException as e:
        log.exception("Groq request failed")
        raise LLMError(f"Groq request failed: {e}") from e

    elapsed_ms = int((time.perf_counter() - start) * 1000)

    if r.status_code == 429:
        raise LLMUnavailable("Groq rate limit hit. Wait a moment and retry.")
    if r.status_code == 401:
        raise LLMUnavailable("Groq API key invalid or expired.")
    if r.status_code != 200:
        log.error("Groq returned %s: %s", r.status_code, r.text[:500])
        raise LLMBadResponse(f"Groq HTTP {r.status_code}: {r.text[:200]}")

    try:
        body = r.json()
    except ValueError as e:
        raise LLMBadResponse(f"Groq returned non-JSON: {r.text[:200]}") from e

    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMBadResponse(f"Unexpected Groq response shape: {str(body)[:200]}") from e

    if not text or not text.strip():
        raise LLMBadResponse("Groq returned empty content")

    usage = body.get("usage", {})
    log.info(
        "Groq done in %dms — prompt=%d tokens, completion=%d tokens",
        elapsed_ms,
        usage.get("prompt_tokens", 0),
        usage.get("completion_tokens", 0),
    )

    return LLMResponse(
        text=text,
        model=body.get("model", model),
        duration_ms=elapsed_ms,
        eval_count=usage.get("completion_tokens", 0),
    )

def generate_stream(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = GROQ_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 512,
    json_mode: bool = True,
) -> Iterator[str]:
    """Stream tokens from Groq via SSE."""

    if not GROQ_API_KEY:
        raise LLMUnavailable("GROQ_API_KEY is not set.")

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens":  max_tokens,
        "top_p":       0.9,
        "stream":      True,
    }

    try:
        with requests.post(
            GROQ_API_URL,
            headers=headers,
            json=payload,
            timeout=(_CONNECT_TIMEOUT, GROQ_TIMEOUT),
            stream=True,
        ) as r:
            if r.status_code != 200:
                raise LLMBadResponse(f"Groq HTTP {r.status_code}: {r.text[:200]}")

            for raw_line in r.iter_lines(decode_unicode=True):
                if not raw_line or not raw_line.startswith("data:"):
                    continue
                line = raw_line[5:].strip()   # strip "data: " prefix
                if line == "[DONE]":
                    break
                try:
                    chunk = json.loads(line)
                except ValueError:
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                token_text = delta.get("content", "")
                if token_text:
                    yield token_text

    except requests.ConnectionError as e:
        raise LLMUnavailable(f"Groq not reachable: {e}") from e
    except requests.Timeout as e:
        raise LLMTimeout(f"Groq stream exceeded {GROQ_TIMEOUT}s") from e
    except requests.RequestException as e:
        raise LLMError(f"Groq stream failed: {e}") from e

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if not GROQ_API_KEY:
        log.error("GROQ_API_KEY not set. Add it to .env: GROQ_API_KEY=gsk_xxxx")
        raise SystemExit(1)

    if not is_available():
        log.error("Groq API not reachable. Check internet and API key.")
        raise SystemExit(1)

    log.info("Testing Groq model: %s", GROQ_MODEL)
    resp = generate(
        system_prompt="You output only valid JSON.",
        user_prompt='Return a JSON object with one key "ok" set to true.',
        max_tokens=30,
    )
    log.info("Response in %dms (%d tokens): %s", resp.duration_ms, resp.eval_count, resp.text)