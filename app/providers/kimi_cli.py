import asyncio
import json
import locale
import os
import tempfile
import time
import uuid
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from hashlib import sha1

from app.config import Settings
from app.models import ChatCompletionRequest, ChatMessage
from app.providers.base import ProviderExecutionError
from app.runtime import ServiceRuntime, SessionStore


class KimiCLIProvider:
    def __init__(self, settings: Settings, session_store: SessionStore, runtime: ServiceRuntime):
        self._settings = settings
        self._session_store = session_store
        self._runtime = runtime
        self._cli_path = self._resolve_cli_path()
        self._work_dir = Path(settings.kimi_cli_work_dir or os.getcwd())
        self._response_headers: dict[str, str] = {}

    def _resolve_cli_path(self) -> Path:
        if self._settings.kimi_cli_path:
            cli_path = Path(self._settings.kimi_cli_path)
            if not cli_path.exists():
                raise ProviderExecutionError(f"Configured KIMI_CLI_PATH does not exist: {cli_path}", status_code=500)
            return cli_path

        archives = sorted(
            Path.home().glob(".vscode/extensions/moonshot-ai.kimi-code-*/bin/kimi/archive.zip"),
            reverse=True,
        )
        if not archives:
            raise ProviderExecutionError(
                "Cannot find bundled Kimi CLI. Install the Kimi Code VS Code extension first, or set KIMI_CLI_PATH.",
                status_code=500,
            )

        archive = archives[0]
        version = archive.parent.joinpath("manifest.json")
        cli_version = "bundled"
        if version.exists():
            manifest = json.loads(version.read_text(encoding="utf-8"))
            cli_version = manifest.get("version", cli_version)

        extract_dir = Path(tempfile.gettempdir()) / "local-kimi-api" / f"kimi-cli-{cli_version}"
        cli_exe = extract_dir / "kimi" / "kimi.exe"
        if not cli_exe.exists():
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as zip_file:
                zip_file.extractall(extract_dir)

        if not cli_exe.exists():
            raise ProviderExecutionError(f"Bundled Kimi CLI was extracted but kimi.exe was not found: {cli_exe}", status_code=500)
        return cli_exe

    def _render_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                    else:
                        parts.append(json.dumps(item, ensure_ascii=False))
                else:
                    parts.append(str(item))
            return "\n".join(part for part in parts if part)
        return json.dumps(content, ensure_ascii=False)

    def _render_prompt(self, messages: list[ChatMessage]) -> str:
        prompt_parts: list[str] = [
            "下面是一次通过本地 OpenAI 兼容代理转发的对话。",
            "请严格按照多轮消息中的角色关系继续回复最后一条 user 消息，不要复述提示词本身。",
        ]
        for message in messages:
            role = message.role.upper()
            name_suffix = f" ({message.name})" if message.name else ""
            prompt_parts.append(f"[{role}{name_suffix}]\n{self._render_content(message.content)}")
        prompt_parts.append("[ASSISTANT]\n")
        return "\n\n".join(prompt_parts)

    def _build_command(self, request: ChatCompletionRequest, output_format: str) -> list[str]:
        command = [str(self._cli_path), "--work-dir", str(self._work_dir), "-p", self._render_prompt(request.messages)]
        if output_format == "text":
            command.insert(1, "--quiet")
        else:
            command[1:1] = ["--print", "--output-format", output_format]
        session_id = self._resolve_session_id(request)
        command.extend(["--session", session_id])
        model = request.model if self._settings.kimi_cli_passthrough_model else ""
        if model:
            command.extend(["--model", model])
        return command

    def _resolve_session_id(self, request: ChatCompletionRequest) -> str:
        internal_session_id = request.extra_body.get("_local_session_id")
        if internal_session_id:
            return str(internal_session_id)
        explicit_session_id = request.extra_body.get("session_id") or request.extra_body.get("conversation_id")
        if explicit_session_id:
            return str(explicit_session_id)
        if request.user:
            digest = sha1(request.user.encode("utf-8")).hexdigest()[:16]
            return f"user-{digest}"
        return f"chat-{uuid.uuid4().hex}"

    async def _prepare_request(self, request: ChatCompletionRequest) -> tuple[ChatCompletionRequest, str, list[ChatMessage], bool, str]:
        session_id = self._resolve_session_id(request)
        messages, reused, session_mode = await self._session_store.prepare_messages(session_id, request.messages)
        extra_body = dict(request.extra_body)
        extra_body["_local_session_id"] = session_id
        prepared_request = request.model_copy(update={"messages": messages, "extra_body": extra_body})
        return prepared_request, session_id, list(request.messages), reused, session_mode

    async def _run_cli(self, request: ChatCompletionRequest, output_format: str) -> str:
        process = await asyncio.create_subprocess_exec(
            *self._build_command(request, output_format),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            message = self._decode_bytes(stderr).strip() or self._decode_bytes(stdout).strip()
            raise ProviderExecutionError(f"Kimi CLI request failed: {message}")
        return self._decode_bytes(stdout).strip()

    def _decode_bytes(self, payload: bytes) -> str:
        encodings = [locale.getpreferredencoding(False), "utf-8", "gbk", "cp936"]
        for encoding in encodings:
            if not encoding:
                continue
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        return payload.decode("utf-8", errors="replace")

    def _response_payload(self, request: ChatCompletionRequest, content: str, session_id: str, session_mode: str) -> dict[str, Any]:
        usage = self._runtime.estimate_chat_tokens(request.messages, content)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model or self._settings.upstream_model,
            "session_id": session_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "local_kimi": {
                "session_id": session_id,
                "session_mode": session_mode,
                "usage_mode": "estimated",
            },
            "usage": usage,
        }

    async def chat_completions(self, request: ChatCompletionRequest) -> dict[str, Any]:
        prepared_request, session_id, request_messages, _, session_mode = await self._prepare_request(request)
        content = await self._run_cli(prepared_request, "text")
        await self._session_store.record_response(session_id, request_messages, content)
        self._response_headers = {
            "X-Local-Kimi-Session-Id": session_id,
            "X-Local-Kimi-Session-Mode": session_mode,
        }
        return self._response_payload(request, content, session_id, session_mode)

    async def stream_chat_completions(self, request: ChatCompletionRequest) -> AsyncIterator[bytes]:
        prepared_request, session_id, request_messages, _, session_mode = await self._prepare_request(request)
        merged_content = await self._run_cli(prepared_request, "text")
        await self._session_store.record_response(session_id, request_messages, merged_content)
        self._response_headers = {
            "X-Local-Kimi-Session-Id": session_id,
            "X-Local-Kimi-Session-Mode": session_mode,
        }
        response = self._response_payload(request, merged_content, session_id, session_mode)
        chunk = {
            "id": response["id"],
            "object": "chat.completion.chunk",
            "created": response["created"],
            "model": response["model"],
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": merged_content},
                    "finish_reason": None,
                }
            ],
        }
        done_chunk = {
            "id": response["id"],
            "object": "chat.completion.chunk",
            "created": response["created"],
            "model": response["model"],
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")
        yield f"data: {json.dumps(done_chunk, ensure_ascii=False)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

    def get_response_headers(self) -> dict[str, str]:
        return dict(self._response_headers)
