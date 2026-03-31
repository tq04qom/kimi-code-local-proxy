from collections.abc import AsyncIterator
from typing import Protocol

from app.models import ChatCompletionRequest


class ProviderExecutionError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ChatProvider(Protocol):
    async def chat_completions(self, request: ChatCompletionRequest) -> dict:
        ...

    async def stream_chat_completions(self, request: ChatCompletionRequest) -> AsyncIterator[bytes]:
        ...

    def get_response_headers(self) -> dict[str, str]:
        ...
