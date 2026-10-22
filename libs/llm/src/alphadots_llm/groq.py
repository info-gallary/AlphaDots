"""Groq adapter: a thin, bounded client for Groq's OpenAI-compatible chat completions API.

Deliberately raw ``httpx`` rather than a vendor SDK — one dependency, a request shape we
fully control, and a transport that is trivial to point at a mock server in tests. See
https://console.groq.com/docs/api-reference#chat-create for the wire format.
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from alphadots_llm.adapter import ChatMessage, ChatResult, LlmError, LlmErrorReason
from alphadots_llm.pricing import TokenRate, estimate_cost_usd

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"


class GroqAdapter:
    provider = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        rate: TokenRate | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("GroqAdapter requires a non-empty api_key")
        self.model = model
        self._rate = rate
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        json_mode: bool,
        max_tokens: int,
        timeout_seconds: float,
    ) -> ChatResult:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient()
        try:
            try:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers={"authorization": f"Bearer {self._api_key}"},
                    timeout=timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                raise LlmError(
                    LlmErrorReason.TIMEOUT, f"Groq request timed out after {timeout_seconds}s"
                ) from exc
            except httpx.HTTPError as exc:
                raise LlmError(LlmErrorReason.HTTP_ERROR, f"Groq request failed: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code == httpx.codes.UNAUTHORIZED:
            raise LlmError(LlmErrorReason.AUTH_ERROR, "Groq rejected the API key (401)")
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise LlmError(LlmErrorReason.RATE_LIMITED, "Groq rate-limited this request (429)")
        if response.status_code >= httpx.codes.BAD_REQUEST:
            # Groq's error body may include provider-specific detail (e.g. an unknown
            # model name); surface a bounded amount of it rather than the raw body.
            raise LlmError(
                LlmErrorReason.HTTP_ERROR,
                f"Groq returned {response.status_code}: {response.text[:300]}",
            )

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmError(
                LlmErrorReason.INVALID_RESPONSE,
                f"Groq response did not match the expected shape: {exc}",
            ) from exc

        return ChatResult(
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=body.get("model", self.model),
            cost_usd=estimate_cost_usd(self._rate, input_tokens, output_tokens),
        )
