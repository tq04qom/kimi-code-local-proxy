import asyncio
import base64
from datetime import datetime, timedelta
import json
import logging
import re
import time
from collections import defaultdict, deque
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.models import ChatMessage


def _configure_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("local_kimi_api")
    target_path = (log_dir / "access.log").resolve()
    for handler in list(logger.handlers):
        handler_path = getattr(handler, "baseFilename", None)
        if handler_path and Path(handler_path).resolve() != target_path:
            logger.removeHandler(handler)
            handler.close()
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_dir / "access.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def _configure_audit_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("local_kimi_api_audit")
    target_path = (log_dir / "audit.log").resolve()
    for handler in list(logger.handlers):
        handler_path = getattr(handler, "baseFilename", None)
        if handler_path and Path(handler_path).resolve() != target_path:
            logger.removeHandler(handler)
            handler.close()
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    file_handler = logging.FileHandler(log_dir / "audit.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> tuple[bool, int]:
        now = time.time()
        async with self._lock:
            bucket = self._buckets[key]
            cutoff = now - self._window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= self._max_requests:
                retry_after = max(1, int(bucket[0] + self._window_seconds - now))
                return False, retry_after

            bucket.append(now)
            return True, 0


class SessionStore:
    def __init__(self, file_path: Path):
        self._file_path = file_path
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        if self._file_path.exists():
            try:
                self._sessions = json.loads(self._file_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._sessions = {}

    async def prepare_messages(
        self,
        session_id: str,
        messages: Sequence[ChatMessage],
    ) -> tuple[list[ChatMessage], bool, str]:
        current = [message.model_dump(exclude_none=True) for message in messages]
        async with self._lock:
            record = self._sessions.get(session_id)
            if not record:
                return list(messages), False, "new-session"

            previous = record.get("messages", [])
            prefix_len = 0
            max_prefix = min(len(previous), len(current))
            while prefix_len < max_prefix and previous[prefix_len] == current[prefix_len]:
                prefix_len += 1

            if prefix_len > 0:
                delta = current[prefix_len:]
                mode = "prefix-delta"
            else:
                delta = current
                mode = "incremental"

            if not delta and current:
                delta = current[-1:]
                mode = "retry-last-message"

            return [ChatMessage.model_validate(item) for item in delta], True, mode

    async def record_response(
        self,
        session_id: str,
        request_messages: Sequence[ChatMessage],
        response_content: str,
    ) -> None:
        messages = [message.model_dump(exclude_none=True) for message in request_messages]
        messages.append({"role": "assistant", "content": response_content})
        payload = {
            "messages": messages,
            "updated_at": int(time.time()),
        }
        async with self._lock:
            self._sessions[session_id] = payload
            self._file_path.write_text(json.dumps(self._sessions, ensure_ascii=False, indent=2), encoding="utf-8")


class ServiceRuntime:
    def __init__(self, settings: Settings):
        log_root = Path(settings.log_dir)
        date_dir = datetime.now().strftime("%Y-%m-%d")
        log_dir = log_root / date_dir
        session_store_path = Path(settings.session_store_path) if settings.session_store_path else log_dir / "sessions.json"
        self.log_root = log_root
        self.log_dir = log_dir
        self.logger = _configure_logger(log_dir)
        self.audit_logger = _configure_audit_logger(log_dir)
        self.audit_log_path = log_dir / "audit.log"
        self.semaphore = asyncio.Semaphore(max(1, settings.max_concurrent_requests))
        self.rate_limiter = InMemoryRateLimiter(settings.rate_limit_max_requests, settings.rate_limit_window_seconds)
        self.session_store = SessionStore(session_store_path)
        self.max_queue_wait_seconds = settings.max_queue_wait_seconds
        self.started_at = time.time()

    def _iter_audit_log_paths(self) -> list[Path]:
        candidates: list[Path] = []
        if self.log_root.exists():
            candidates.extend(path for path in self.log_root.rglob("audit.log") if path.is_file())
        fallback_path = self.audit_log_path
        if fallback_path.exists() and fallback_path not in candidates:
            candidates.append(fallback_path)
        return sorted({path.resolve() for path in candidates})

    def _is_metrics_excluded_path(self, path: str) -> bool:
        return path in {"/", "/health", "/dashboard", "/api/dashboard/stats"}

    def _decode_http_body(self, payload: bytes) -> dict[str, Any]:
        if not payload:
            return {"text": "", "encoding": "utf-8"}

        for encoding in ("utf-8", "gbk", "cp936"):
            try:
                return {"text": payload.decode(encoding), "encoding": encoding}
            except UnicodeDecodeError:
                continue

        return {
            "base64": base64.b64encode(payload).decode("ascii"),
            "encoding": "base64",
        }

    def log_http_exchange(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        query: str,
        client: str,
        status_code: int,
        duration_ms: int,
        request_headers: dict[str, str],
        request_body: bytes,
        response_headers: dict[str, str],
        response_body: bytes,
    ) -> None:
        metrics = self._calculate_metrics(path=path, request_body=request_body, response_body=response_body)
        entry = {
            "timestamp": int(time.time()),
            "request_id": request_id,
            "method": method,
            "path": path,
            "query": query,
            "client": client,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "request": {
                "headers": request_headers,
                "body": self._decode_http_body(request_body),
            },
            "response": {
                "headers": response_headers,
                "body": self._decode_http_body(response_body),
            },
            "metrics": metrics,
        }
        self.audit_logger.info(json.dumps(entry, ensure_ascii=False))

    def _render_content_text(self, content: Any) -> str:
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
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False)
        return str(content)

    def estimate_text_tokens(self, text: str) -> int:
        cjk_tokens = len(re.findall(r"[\u4e00-\u9fff]", text))
        non_cjk_text = re.sub(r"[\u4e00-\u9fff]", " ", text)
        latin_like_tokens = len(re.findall(r"[A-Za-z0-9_]+|[^\s]", non_cjk_text))
        return max(1, cjk_tokens + latin_like_tokens)

    def estimate_chat_tokens(self, messages: Sequence[dict[str, Any]] | Sequence[ChatMessage], completion_text: str = "") -> dict[str, int]:
        prompt_tokens = 0
        for raw_message in messages:
            if isinstance(raw_message, ChatMessage):
                message = raw_message.model_dump(exclude_none=True)
            else:
                message = dict(raw_message)
            role = str(message.get("role", ""))
            content = self._render_content_text(message.get("content", ""))
            prompt_tokens += 4 + self.estimate_text_tokens(role) + self.estimate_text_tokens(content)
        completion_tokens = self.estimate_text_tokens(completion_text) if completion_text else 0
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def _calculate_metrics(self, path: str, request_body: bytes, response_body: bytes) -> dict[str, Any]:
        if path != "/v1/chat/completions":
            return {}

        request_info = self._decode_http_body(request_body)
        response_info = self._decode_http_body(response_body)
        request_text = request_info.get("text", "")
        response_text = response_info.get("text", "")

        request_payload: dict[str, Any] = {}
        response_payload: dict[str, Any] = {}
        model = ""
        session_id = ""

        try:
            request_payload = json.loads(request_text) if request_text else {}
        except json.JSONDecodeError:
            request_payload = {}

        model = str(request_payload.get("model", ""))

        try:
            response_payload = json.loads(response_text) if response_text and not response_text.startswith("data: ") else {}
        except json.JSONDecodeError:
            response_payload = {}

        local_kimi_info = response_payload.get("local_kimi", {}) if isinstance(response_payload, dict) else {}
        usage = response_payload.get("usage", {}) if isinstance(response_payload, dict) else {}
        if usage and isinstance(usage, dict) and usage.get("total_tokens", 0) and local_kimi_info.get("usage_mode") != "estimated":
            return {
                "token_mode": "reported",
                "model": response_payload.get("model") or model,
                "session_id": response_payload.get("session_id", ""),
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
                "total_tokens": int(usage.get("total_tokens", 0)),
            }

        completion_text = ""
        if response_payload:
            choices = response_payload.get("choices", [])
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message", {})
                if isinstance(message, dict):
                    completion_text = self._render_content_text(message.get("content", ""))
            session_id = str(response_payload.get("session_id", ""))
        elif response_text.startswith("data: "):
            fragments: list[str] = []
            for line in response_text.splitlines():
                if not line.startswith("data: "):
                    continue
                chunk_payload = line[6:].strip()
                if not chunk_payload or chunk_payload == "[DONE]":
                    continue
                try:
                    chunk = json.loads(chunk_payload)
                except json.JSONDecodeError:
                    continue
                if not session_id:
                    session_id = str(chunk.get("session_id", ""))
                for choice in chunk.get("choices", []):
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta", {})
                    if isinstance(delta, dict):
                        content = delta.get("content")
                        if isinstance(content, str):
                            fragments.append(content)
            completion_text = "".join(fragments)

        estimated = self.estimate_chat_tokens(request_payload.get("messages", []), completion_text)
        return {
            "token_mode": "estimated",
            "model": response_payload.get("model") or model,
            "session_id": session_id,
            **estimated,
        }

    def build_dashboard_stats(self) -> dict[str, Any]:
        now = datetime.now()
        today = now.date()
        past_week = [today - timedelta(days=index) for index in range(6, -1, -1)]
        past_week_map = {day.isoformat(): {"date": day.isoformat(), "requests": 0, "tokens": 0} for day in past_week}
        hourly_map = {hour: {"hour": f"{hour:02d}:00", "requests": 0, "tokens": 0} for hour in range(24)}
        recent_requests: deque[dict[str, Any]] = deque(maxlen=20)

        total_requests = 0
        total_chat_requests = 0
        total_tokens = 0
        total_success = 0
        today_tokens = 0
        today_requests = 0
        average_duration = 0.0
        duration_total = 0

        for audit_log_path in self._iter_audit_log_paths():
            with audit_log_path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    path = str(entry.get("path", ""))
                    excluded_from_metrics = self._is_metrics_excluded_path(path)
                    status_code = int(entry.get("status_code", 0))
                    duration_ms = int(entry.get("duration_ms", 0))
                    if not excluded_from_metrics:
                        total_requests += 1
                        duration_total += duration_ms
                        if 200 <= status_code < 400:
                            total_success += 1

                    timestamp = datetime.fromtimestamp(int(entry.get("timestamp", 0) or 0))
                    date_key = timestamp.date().isoformat()
                    metrics = entry.get("metrics", {}) or {}
                    if not metrics and path == "/v1/chat/completions":
                        request_body_text = entry.get("request", {}).get("body", {}).get("text", "")
                        response_body_text = entry.get("response", {}).get("body", {}).get("text", "")
                        metrics = self._calculate_metrics(
                            path=path,
                            request_body=request_body_text.encode("utf-8"),
                            response_body=response_body_text.encode("utf-8"),
                        )
                    tokens = int(metrics.get("total_tokens", 0) or 0)

                    if not excluded_from_metrics and date_key in past_week_map:
                        past_week_map[date_key]["requests"] += 1
                        past_week_map[date_key]["tokens"] += tokens

                    if not excluded_from_metrics and timestamp.date() == today:
                        today_requests += 1
                        today_tokens += tokens
                        hourly_map[timestamp.hour]["requests"] += 1
                        hourly_map[timestamp.hour]["tokens"] += tokens

                    if path == "/v1/chat/completions":
                        total_chat_requests += 1
                        total_tokens += tokens

                    request_body_text = entry.get("request", {}).get("body", {}).get("text", "")
                    prompt_preview = ""
                    try:
                        request_payload = json.loads(request_body_text) if request_body_text else {}
                        messages = request_payload.get("messages", [])
                        if messages:
                            prompt_preview = self._render_content_text(messages[-1].get("content", ""))[:120]
                    except json.JSONDecodeError:
                        prompt_preview = request_body_text[:120]

                    if not excluded_from_metrics:
                        recent_requests.appendleft(
                            {
                                "request_id": entry.get("request_id", ""),
                                "time": timestamp.strftime("%H:%M:%S"),
                                "method": entry.get("method", ""),
                                "path": path,
                                "status_code": status_code,
                                "duration_ms": duration_ms,
                                "tokens": tokens,
                                "token_mode": metrics.get("token_mode", "none"),
                                "model": metrics.get("model", ""),
                                "prompt_preview": prompt_preview,
                            }
                        )

        if total_requests:
            average_duration = round(duration_total / total_requests, 1)

        return {
            "service": {
                "status": "running",
                "started_at": int(self.started_at),
                "uptime_seconds": int(time.time() - self.started_at),
            },
            "totals": {
                "requests": total_requests,
                "chat_requests": total_chat_requests,
                "success_rate": round((total_success / total_requests) * 100, 1) if total_requests else 0.0,
                "average_duration_ms": average_duration,
                "total_tokens": total_tokens,
                "today_requests": today_requests,
                "today_tokens": today_tokens,
            },
            "curves": {
                "daily": list(past_week_map.values()),
                "hourly": [hourly_map[hour] for hour in range(24)],
            },
            "recent_requests": list(recent_requests),
        }


@lru_cache(maxsize=1)
def get_runtime() -> ServiceRuntime:
    return ServiceRuntime(get_settings())
