"""Model client for Microsoft Foundry (Azure OpenAI) and GitHub Models.

Provider selection (env ``LLM_PROVIDER``):

* ``azure``   - Microsoft Foundry / Azure OpenAI deployment. Uses keyless
                Microsoft Entra ID auth (``DefaultAzureCredential`` /
                managed identity) unless ``AZURE_OPENAI_API_KEY`` is set.
* ``github``  - GitHub Models (free tier, good for local development).
* ``offline`` - no model; agents fall back to deterministic templates.

If ``LLM_PROVIDER`` is unset, ``azure`` is used when ``AZURE_OPENAI_ENDPOINT``
exists, otherwise ``offline``. Every call returns a *receipt* (provider,
model, latency, tokens) so the UI can show where each answer came from.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any


class LLMUnavailable(RuntimeError):
    pass


@dataclass
class Receipt:
    provider: str
    model: str
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 1
    tool_calls: int = 0
    steps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        price_in = os.getenv("PRICE_IN_PER_1M")
        price_out = os.getenv("PRICE_OUT_PER_1M")
        if price_in and price_out:
            d["est_cost_usd"] = round(self.input_tokens * float(price_in) / 1e6
                                      + self.output_tokens * float(price_out) / 1e6, 6)
        return d


class LLMClient:
    def __init__(self, force_offline: bool = False) -> None:
        self.provider = "offline" if force_offline else (os.getenv("LLM_PROVIDER") or (
            "azure" if os.getenv("AZURE_OPENAI_ENDPOINT") else "offline")).lower()
        self.timeout = float(os.getenv("LLM_TIMEOUT_S", "20"))
        self.max_output_tokens = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "450"))
        self._sem = threading.Semaphore(int(os.getenv("LLM_MAX_CONCURRENCY", "4")))
        self._client = None
        self.model = "template"
        self.error: str | None = None
        try:
            if self.provider == "azure":
                self._client, self.model = self._azure()
            elif self.provider == "github":
                self._client, self.model = self._github()
        except Exception as exc:  # pragma: no cover - depends on environment
            self.error = f"{type(exc).__name__}: {exc}"
            self.provider, self._client = "offline", None

    # ------------------------------------------------------------------ providers
    def _azure(self):
        from openai import AzureOpenAI

        endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
        key = os.getenv("AZURE_OPENAI_API_KEY")
        if key:
            client = AzureOpenAI(azure_endpoint=endpoint, api_key=key, api_version=api_version,
                                 timeout=self.timeout, max_retries=1)
        else:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            token_provider = get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
            client = AzureOpenAI(azure_endpoint=endpoint, azure_ad_token_provider=token_provider,
                                 api_version=api_version, timeout=self.timeout, max_retries=1)
        return client, deployment

    def _github(self):
        from openai import OpenAI

        token = os.getenv("GITHUB_MODELS_TOKEN")
        if not token:
            raise LLMUnavailable("GITHUB_MODELS_TOKEN is not set")
        base_url = os.getenv("GITHUB_MODELS_ENDPOINT", "https://models.github.ai/inference")
        model = os.getenv("GITHUB_MODEL", "openai/gpt-4.1-mini")
        return OpenAI(base_url=base_url, api_key=token, timeout=self.timeout, max_retries=1), model

    # ------------------------------------------------------------------ API
    @property
    def live(self) -> bool:
        return self._client is not None

    def status(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model, "live": self.live, "error": self.error}

    def chat(self, messages: list[dict[str, Any]], *, json_mode: bool = True,
             tools: list[dict[str, Any]] | None = None, max_tokens: int | None = None):
        """Single chat completion. Returns (message, usage, latency_ms)."""
        if not self.live:
            raise LLMUnavailable("No model configured (offline mode)")
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_output_tokens,
            "temperature": 0.4,
        }
        if json_mode and not tools:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools
        with self._sem:
            start = time.perf_counter()
            try:
                resp = self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                # Newer reasoning models reject max_tokens/temperature; retry with the new names.
                text = str(exc)
                if "max_tokens" in text or "temperature" in text:
                    kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                    kwargs.pop("temperature", None)
                    resp = self._client.chat.completions.create(**kwargs)
                else:
                    raise
            latency = int((time.perf_counter() - start) * 1000)
        return resp.choices[0].message, resp.usage, latency

    def complete_json(self, system: str, user: str, max_tokens: int | None = None) -> tuple[dict[str, Any], Receipt]:
        msg, usage, latency = self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            json_mode=True, max_tokens=max_tokens)
        receipt = Receipt(self.provider, self.model, latency,
                          getattr(usage, "prompt_tokens", 0) or 0,
                          getattr(usage, "completion_tokens", 0) or 0)
        return parse_json(msg.content or "{}"), receipt


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_client() -> None:
    global _client
    _client = None
