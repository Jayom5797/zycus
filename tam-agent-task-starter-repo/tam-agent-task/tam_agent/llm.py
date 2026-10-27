"""LLM client with multi-key round-robin and a clean provider abstraction.

Providers (set LLM_PROVIDER in .env):
  groq       default. Free, fast, OpenAI-compatible. Reads one OR MANY API keys
             and rotates them round-robin, advancing on rate-limit (HTTP 429).
  ollama     local / offline fallback. No API key needed.
  openai     needs OPENAI_API_KEY and `pip install openai`.
  anthropic  needs ANTHROPIC_API_KEY and `pip install anthropic`.

Key configuration for Groq (any one of these; checked in this order):
  GROQ_API_KEYS=key1,key2,key3      comma-separated, scales to any number
  GROQ_API_KEY_1=... GROQ_API_KEY_2=...   numbered
  GROQ_API_KEY=...                  single key

Design notes (for the walkthrough):
  * The round-robin raises the effective rate limit: N keys ~= N x throughput.
  * On a 429 we rotate to the next key and retry; if every key is rate-limited
    in one pass we back off briefly, then try again. This keeps a long review
    run from dying on a single key's per-minute cap.
  * Keys live in .env only. .env is gitignored and never committed.
"""
import json
import os
import time
import urllib.error
import urllib.request
from itertools import cycle
from pathlib import Path

# --- minimal .env loader (no extra dependency) -----------------------------
_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.split("#", 1)[0].strip()
        if "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())


class LLMError(RuntimeError):
    """Raised with an actionable message when a provider is misconfigured or unreachable."""


def _collect_groq_keys() -> list[str]:
    """Gather Groq keys from env in priority order; de-duplicate, keep order."""
    keys: list[str] = []
    multi = os.getenv("GROQ_API_KEYS", "")
    if multi:
        keys += [k.strip() for k in multi.split(",") if k.strip()]
    # numbered keys GROQ_API_KEY_1, GROQ_API_KEY_2, ...
    for k, v in os.environ.items():
        if k.startswith("GROQ_API_KEY_") and v.strip():
            keys.append(v.strip())
    single = os.getenv("GROQ_API_KEY", "")
    if single:
        keys.append(single.strip())
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


_DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-120b",
    "ollama": "llama3.1:8b",
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4o-mini",
}


class LLMClient:
    def __init__(self, provider: str | None = None, model: str | None = None):
        self.provider = (provider or os.getenv("LLM_PROVIDER", "groq")).lower()
        self.model = model or os.getenv("LLM_MODEL") or _DEFAULT_MODELS.get(self.provider, "")
        self.calls = 0  # reported per ticket for cost/latency
        # token instrumentation (guardrail S3). None where the provider does not
        # expose counts - we never invent token numbers.
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.last_usage: dict | None = None

        # Groq: build a round-robin iterator over all configured keys.
        self._groq_keys: list[str] = []
        self._groq_cycle = None
        if self.provider == "groq":
            self._groq_keys = _collect_groq_keys()
            if not self._groq_keys:
                raise LLMError(
                    "LLM_PROVIDER=groq but no key found. Set GROQ_API_KEYS=key1,key2 "
                    "(or GROQ_API_KEY) in .env. Get a free key at https://console.groq.com"
                )
            self._groq_cycle = cycle(self._groq_keys)

    # -- usage instrumentation ---------------------------------------------
    def _record_usage(self, usage: dict | None) -> None:
        """Accumulate prompt/completion tokens when the provider reports them."""
        self.last_usage = usage
        if usage:
            self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)

    # -- public API ---------------------------------------------------------
    def chat(self, messages: list[dict], system: str | None = None, max_tokens: int = 1024,
             temperature: float = 0.0) -> str:
        """messages: [{"role": "user"|"assistant", "content": str}] -> assistant text."""
        self.calls += 1
        if self.provider == "groq":
            return self._chat_groq(messages, system, max_tokens, temperature)
        if self.provider == "ollama":
            return self._chat_ollama(messages, system, max_tokens, temperature)
        if self.provider == "openai":
            return self._chat_openai(messages, system, max_tokens, temperature)
        if self.provider == "anthropic":
            return self._chat_anthropic(messages, system, max_tokens, temperature)
        raise LLMError(f"Unknown LLM_PROVIDER: {self.provider!r}. Use groq|ollama|openai|anthropic.")

    # -- Groq (OpenAI-compatible) with round-robin --------------------------
    def _chat_groq(self, messages, system, max_tokens, temperature) -> str:
        url = "https://api.groq.com/openai/v1/chat/completions"
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        body = json.dumps({
            "model": self.model, "messages": msgs,
            "max_tokens": max_tokens, "temperature": temperature,
        }).encode()

        # Groq free tier is limited by TOKENS-PER-MINUTE (not request count), so
        # when every key is rate-limited the right move is to wait out the ~60s
        # window, not give up. We rotate keys first (cheap), and only sleep after
        # a full sweep finds all keys limited. `retry-after` is honoured when sent.
        n = len(self._groq_keys)
        attempts = n * 6          # generous: enough to ride out a couple of windows
        last_err = None
        sweep_waits = 0
        for i in range(attempts):
            key = next(self._groq_cycle)
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                # Groq's edge (Cloudflare) rejects urllib's default UA with 403/1010.
                "User-Agent": "tam-agent/1.0",
            })
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.loads(r.read())
                self._record_usage(data.get("usage"))
                return data["choices"][0]["message"]["content"] or ""
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 429:
                    retry_after = e.headers.get("retry-after")
                    # rotate to the next key immediately; only wait after a full sweep
                    if (i + 1) % n == 0:
                        sweep_waits += 1
                        wait = float(retry_after) if retry_after else min(5 * sweep_waits, 30)
                        time.sleep(wait)
                    continue
                if 500 <= e.code < 600:
                    time.sleep(1)
                    continue
                detail = e.read().decode(errors="ignore")[:300]
                raise LLMError(f"Groq HTTP {e.code}: {detail}") from e
            except urllib.error.URLError as e:
                last_err = e
                raise LLMError(
                    f"Cannot reach Groq ({e.reason}). Check your network, or switch "
                    f"LLM_PROVIDER=ollama in .env for offline use."
                ) from e
        raise LLMError(
            f"All {len(self._groq_keys)} Groq key(s) exhausted or rate-limited after "
            f"{attempts} attempts. Add more keys to GROQ_API_KEYS in .env. Last error: {last_err}"
        )

    # -- Ollama (local/offline fallback) ------------------------------------
    def _chat_ollama(self, messages, system, max_tokens, temperature) -> str:
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        body = json.dumps({
            "model": self.model, "messages": msgs, "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }).encode()
        req = urllib.request.Request(f"{host}/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read())["message"]["content"]
        except urllib.error.URLError as e:
            raise LLMError(
                f"Cannot reach Ollama at {host} ({e.reason}). Start it with `ollama serve` "
                f"and `ollama pull {self.model}`, or switch LLM_PROVIDER=groq in .env."
            ) from e

    # -- OpenAI -------------------------------------------------------------
    def _chat_openai(self, messages, system, max_tokens, temperature) -> str:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise LLMError("LLM_PROVIDER=openai needs `pip install openai`.") from e
        if not os.getenv("OPENAI_API_KEY"):
            raise LLMError("LLM_PROVIDER=openai needs OPENAI_API_KEY in .env.")
        client = OpenAI()
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        resp = client.chat.completions.create(model=self.model, messages=msgs,
                                               max_tokens=max_tokens, temperature=temperature)
        u = getattr(resp, "usage", None)
        if u is not None:
            self._record_usage({"prompt_tokens": getattr(u, "prompt_tokens", 0),
                                "completion_tokens": getattr(u, "completion_tokens", 0)})
        return resp.choices[0].message.content or ""

    # -- Anthropic ----------------------------------------------------------
    def _chat_anthropic(self, messages, system, max_tokens, temperature) -> str:
        try:
            import anthropic
        except ImportError as e:
            raise LLMError("LLM_PROVIDER=anthropic needs `pip install anthropic`.") from e
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise LLMError("LLM_PROVIDER=anthropic needs ANTHROPIC_API_KEY in .env.")
        client = anthropic.Anthropic()
        resp = client.messages.create(model=self.model, max_tokens=max_tokens,
                                       temperature=temperature, system=system or "",
                                       messages=messages)
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
