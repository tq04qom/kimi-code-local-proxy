import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import Settings
from app.models import ChatCompletionRequest


class OpenAICompatibleProvider:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._timeout = httpx.Timeout(settings.request_timeout_seconds)
        self._response_headers: dict[str, str] = {}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.upstream_api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, request: ChatCompletionRequest) -> dict[str, Any]:
        payload = request.model_dump(exclude_none=True)
        payload["model"] = request.model or self._settings.upstream_model
        extra_body = payload.pop("extra_body", {})
        payload.update(extra_body)
        return payload

    async def chat_completions(self, request: ChatCompletionRequest) -> dict:
        url = f"{self._settings.upstream_base_url.rstrip('/')}" + "/chat/completions"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, headers=self._headers(), json=self._payload(request))
            response.raise_for_status()
            return response.json()

    async def stream_chat_completions(self, request: ChatCompletionRequest) -> AsyncIterator[bytes]:
        url = f"{self._settings.upstream_base_url.rstrip('/')}" + "/chat/completions"
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                url,
                headers=self._headers(),
                json=self._payload(request),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    yield f"{line}\n".encode("utf-8")

    async def test_connection(self) -> dict[str, Any]:
        url = f"{self._settings.upstream_base_url.rstrip('/')}" + "/models"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
            return response.json()

    def get_response_headers(self) -> dict[str, str]:
        return dict(self._response_headers)
