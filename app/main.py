from collections.abc import AsyncIterator
import asyncio
import time
import uuid
from typing import AsyncIterator as TypingAsyncIterator

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from app.config import Settings, get_settings
from app.models import ChatCompletionRequest, HealthResponse, ModelCard, ModelListResponse
from app.providers import ChatProvider, KimiCLIProvider, OpenAICompatibleProvider, ProviderExecutionError
from app.runtime import ServiceRuntime, get_runtime

app = FastAPI(title="local-kimi-api", version="0.1.0")


def _normalize_response_chunk(chunk: bytes | str) -> bytes:
    if isinstance(chunk, bytes):
        return chunk
    return chunk.encode("utf-8")


def _dashboard_html() -> str:
        return """<!doctype html>
<html lang=\"zh-CN\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>local-kimi-api Dashboard</title>
    <style>
        :root { --bg:#f4efe6; --panel:rgba(255,255,255,.8); --panel-strong:#fff8ef; --text:#1f1a17; --muted:#6b625a; --line:rgba(58,42,31,.12); --accent:#b14d28; --accent-soft:#e9b87c; --ok:#2f7d4a; --warn:#ad6a1f; --danger:#b13d2e; --shadow:0 18px 60px rgba(81,52,21,.12); }
        * { box-sizing:border-box; }
        body { margin:0; font-family:\"Segoe UI\",\"Microsoft YaHei\",sans-serif; color:var(--text); background:radial-gradient(circle at top left, rgba(233,184,124,.45), transparent 28%),radial-gradient(circle at bottom right, rgba(177,77,40,.15), transparent 25%),linear-gradient(135deg,#f7f2ea,#efe6d8 46%,#f6efe3); min-height:100vh; }
        .shell { width:min(1280px, calc(100vw - 32px)); margin:24px auto 48px; }
        .hero { display:grid; grid-template-columns:1.4fr .9fr; gap:18px; margin-bottom:18px; }
        .panel { background:var(--panel); backdrop-filter:blur(14px); border:1px solid var(--line); border-radius:24px; box-shadow:var(--shadow); }
        .hero-card { padding:26px 28px; position:relative; overflow:hidden; }
        .hero-card::after { content:\"\"; position:absolute; inset:auto -60px -80px auto; width:220px; height:220px; background:radial-gradient(circle, rgba(177,77,40,.18), transparent 70%); border-radius:999px; }
        h1 { margin:0 0 8px; font-size:clamp(28px,4vw,44px); line-height:1; letter-spacing:-.03em; }
        .sub { color:var(--muted); font-size:14px; max-width:64ch; line-height:1.6; }
        .status-pill { display:inline-flex; align-items:center; gap:8px; padding:8px 14px; border-radius:999px; background:rgba(47,125,74,.12); color:var(--ok); font-weight:700; margin-bottom:16px; }
        .dot { width:10px; height:10px; border-radius:50%; background:currentColor; box-shadow:0 0 0 8px rgba(47,125,74,.08); }
        .side-stats { display:grid; gap:14px; padding:18px; }
        .mini,.metric,.chart-panel,.table-panel { padding:18px 20px; }
        .mini { border-radius:20px; background:var(--panel-strong); border:1px solid var(--line); }
        .mini-label,.metric-label,.chart-note,.table-note { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.08em; }
        .mini-value { font-size:28px; font-weight:800; margin-top:8px; }
        .metrics { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; margin-bottom:18px; }
        .metric-value { font-size:clamp(28px,3vw,42px); margin-top:8px; font-weight:800; letter-spacing:-.04em; }
        .metric-foot { margin-top:8px; font-size:13px; color:var(--muted); }
        .grid { display:grid; grid-template-columns:1.2fr 1fr; gap:18px; margin-bottom:18px; }
        .panel-head { display:flex; justify-content:space-between; align-items:flex-end; gap:12px; margin-bottom:16px; }
        .panel-title { margin:0; font-size:20px; }
        .chart-wrap { height:280px; position:relative; }
        canvas { width:100%; height:100%; display:block; }
        table { width:100%; border-collapse:collapse; font-size:14px; }
        thead th { text-align:left; font-size:12px; color:var(--muted); font-weight:700; padding:0 0 10px; border-bottom:1px solid var(--line); }
        tbody td { padding:12px 0; border-bottom:1px solid rgba(58,42,31,.08); vertical-align:top; }
        .status-code { display:inline-flex; min-width:48px; justify-content:center; border-radius:999px; padding:4px 10px; font-weight:700; background:rgba(47,125,74,.1); color:var(--ok); }
        .status-code.warn { background:rgba(173,106,31,.12); color:var(--warn); }
        .status-code.danger { background:rgba(177,61,46,.12); color:var(--danger); }
        .preview { max-width:360px; color:var(--muted); line-height:1.45; word-break:break-word; }
        .toolbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; color:var(--muted); font-size:13px; }
        .mono { font-family:Consolas,\"SFMono-Regular\",monospace; }
        @media (max-width:980px) { .hero,.grid,.metrics { grid-template-columns:1fr; } .preview { max-width:none; } }
    </style>
</head>
<body>
    <div class=\"shell\">
        <section class=\"hero\">
            <div class=\"panel hero-card\">
                <div class=\"status-pill\"><span class=\"dot\"></span><span id=\"service-status\">运行中</span></div>
                <h1>local-kimi-api 控制台</h1>
                <div class=\"sub\">这里看服务是否在线、累计 token、当日 token、近 7 天曲线、今天分时曲线和最近请求。CLI 模式下的 token 为估算值，OpenAI 兼容上游如果返回 usage 则直接采用真实值。</div>
                <div class=\"toolbar\" style=\"margin-top:18px\"><span>面板地址 <span class=\"mono\">/dashboard</span></span><span id=\"uptime\">运行时长 00:00:00</span></div>
            </div>
            <div class=\"side-stats panel\">
                <div class=\"mini\"><div class=\"mini-label\">总请求量</div><div class=\"mini-value\" id=\"mini-requests\">0</div></div>
                <div class=\"mini\"><div class=\"mini-label\">总 Token</div><div class=\"mini-value\" id=\"mini-total-tokens\">0</div></div>
                <div class=\"mini\"><div class=\"mini-label\">今日 Token</div><div class=\"mini-value\" id=\"mini-today-tokens\">0</div></div>
            </div>
        </section>
        <section class=\"metrics\">
            <div class=\"panel metric\"><div class=\"metric-label\">Chat 请求数</div><div class=\"metric-value\" id=\"chat-requests\">0</div><div class=\"metric-foot\" id=\"success-rate\">成功率 0%</div></div>
            <div class=\"panel metric\"><div class=\"metric-label\">今日请求数</div><div class=\"metric-value\" id=\"today-requests\">0</div><div class=\"metric-foot\">只统计自然日</div></div>
            <div class=\"panel metric\"><div class=\"metric-label\">平均耗时</div><div class=\"metric-value\" id=\"avg-duration\">0 ms</div><div class=\"metric-foot\">所有请求平均</div></div>
            <div class=\"panel metric\"><div class=\"metric-label\">最近模型</div><div class=\"metric-value\" id=\"latest-model\">-</div><div class=\"metric-foot\">最后一条 chat 请求</div></div>
        </section>
        <section class=\"grid\">
            <div class=\"panel chart-panel\"><div class=\"panel-head\"><div><h2 class=\"panel-title\">近 7 天 Token 曲线</h2><div class=\"chart-note\">观察整体消耗变化，判断是否有峰值日。</div></div></div><div class=\"chart-wrap\"><canvas id=\"daily-chart\" width=\"640\" height=\"280\"></canvas></div></div>
            <div class=\"panel chart-panel\"><div class=\"panel-head\"><div><h2 class=\"panel-title\">今日分时 Token</h2><div class=\"chart-note\">看今天每小时的活跃时段。</div></div></div><div class=\"chart-wrap\"><canvas id=\"hourly-chart\" width=\"540\" height=\"280\"></canvas></div></div>
        </section>
        <section class=\"panel table-panel\">
            <div class=\"panel-head\"><div><h2 class=\"panel-title\">最近请求</h2><div class=\"table-note\">显示最近 20 条请求，含状态、耗时、token、模型和输入片段。</div></div><div class=\"mono\" id=\"last-refresh\">刷新中...</div></div>
            <table><thead><tr><th>时间</th><th>路径</th><th>状态</th><th>耗时</th><th>Token</th><th>模型</th><th>输入片段</th></tr></thead><tbody id=\"recent-body\"></tbody></table>
        </section>
    </div>
    <script>
        function formatNumber(value) { return new Intl.NumberFormat('zh-CN').format(value || 0); }
        function formatUptime(seconds) { const safe = Math.max(0, Number(seconds || 0)); const hour = String(Math.floor(safe / 3600)).padStart(2, '0'); const minute = String(Math.floor((safe % 3600) / 60)).padStart(2, '0'); const second = String(safe % 60).padStart(2, '0'); return `${hour}:${minute}:${second}`; }
        function drawLineChart(canvas, labels, values, color, fillColor, onClick) {
            const ctx = canvas.getContext('2d'); const width = canvas.width; const height = canvas.height; ctx.clearRect(0, 0, width, height);
            const padding = { top: 18, right: 50, bottom: 36, left: 50 }; const chartWidth = width - padding.left - padding.right; const chartHeight = height - padding.top - padding.bottom; const maxValue = Math.max(...values, 1);
            // 绘制网格线
            ctx.strokeStyle = 'rgba(58,42,31,0.10)'; ctx.lineWidth = 1;
            for (let step = 0; step < 4; step += 1) { const y = padding.top + (chartHeight / 3) * step; ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(width - padding.right, y); ctx.stroke(); }
            // 绘制纵坐标数值
            ctx.fillStyle = '#6b625a'; ctx.font = '11px Segoe UI'; ctx.textAlign = 'right';
            for (let step = 0; step < 4; step += 1) {
                const y = padding.top + (chartHeight / 3) * step;
                const value = Math.round(maxValue * (1 - step / 3));
                ctx.fillText(value >= 10000 ? (value/10000).toFixed(1) + 'w' : (value >= 1000 ? (value/1000).toFixed(1) + 'k' : value), padding.left - 8, y + 3);
            }
            // 绘制横坐标标签
            ctx.fillStyle = '#6b625a'; ctx.font = '12px Segoe UI'; ctx.textAlign = 'center'; labels.forEach((label, index) => { const x = padding.left + (chartWidth / Math.max(labels.length - 1, 1)) * index; ctx.fillText(label, x, height - 12); });
            // 计算数据点坐标
            const points = values.map((value, index) => { const x = padding.left + (chartWidth / Math.max(values.length - 1, 1)) * index; const y = padding.top + chartHeight - (value / maxValue) * chartHeight; return { x, y, value, label: labels[index] }; });
            // 绘制填充区域
            ctx.beginPath(); points.forEach((point, index) => { if (index === 0) ctx.moveTo(point.x, point.y); else ctx.lineTo(point.x, point.y); }); ctx.lineTo(points[points.length - 1].x, height - padding.bottom); ctx.lineTo(points[0].x, height - padding.bottom); ctx.closePath(); ctx.fillStyle = fillColor; ctx.fill();
            // 绘制线条
            ctx.beginPath(); points.forEach((point, index) => { if (index === 0) ctx.moveTo(point.x, point.y); else ctx.lineTo(point.x, point.y); }); ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.stroke();
            // 绘制数据点
            points.forEach((point) => { ctx.beginPath(); ctx.arc(point.x, point.y, 4, 0, Math.PI * 2); ctx.fillStyle = '#fff'; ctx.fill(); ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke(); });
            // 存储点击数据和回调
            canvas._chartData = { points, padding, onClick };
            // 点击事件处理
            if (!canvas._clickBound) {
                canvas.addEventListener('click', function(e) {
                    const rect = canvas.getBoundingClientRect();
                    const clickX = (e.clientX - rect.left) * (canvas.width / rect.width);
                    const clickY = (e.clientY - rect.top) * (canvas.height / rect.height);
                    const data = canvas._chartData;
                    if (!data) return;
                    // 查找最近的数据点
                    let nearest = null, minDist = Infinity;
                    data.points.forEach((p, idx) => {
                        const dx = clickX - p.x, dy = clickY - p.y;
                        const dist = Math.sqrt(dx*dx + dy*dy);
                        if (dist < 30 && dist < minDist) { minDist = dist; nearest = { ...p, index: idx }; }
                    });
                    if (nearest && data.onClick) data.onClick(nearest);
                });
                canvas._clickBound = true;
            }
        }
        function renderRecent(rows) {
            const tbody = document.getElementById('recent-body'); tbody.innerHTML = '';
            rows.forEach((row) => { const tr = document.createElement('tr'); const statusClass = row.status_code >= 500 ? 'danger' : row.status_code >= 400 ? 'warn' : ''; tr.innerHTML = `<td>${row.time}</td><td><div>${row.path}</div><div class=\"mono\" style=\"font-size:12px;color:#6b625a\">${row.request_id}</div></td><td><span class=\"status-code ${statusClass}\">${row.status_code}</span></td><td>${row.duration_ms} ms</td><td>${formatNumber(row.tokens)}<div class=\"chart-note\">${row.token_mode}</div></td><td>${row.model || '-'}</td><td><div class=\"preview\">${(row.prompt_preview || '').replace(/[<>]/g, '')}</div></td>`; tbody.appendChild(tr); });
        }
        async function refresh() {
            const response = await fetch('/api/dashboard/stats', { cache: 'no-store' }); const data = await response.json();
            document.getElementById('service-status').textContent = data.service.status === 'running' ? '运行中' : '异常'; document.getElementById('uptime').textContent = `运行时长 ${formatUptime(data.service.uptime_seconds)}`; document.getElementById('mini-requests').textContent = formatNumber(data.totals.requests); document.getElementById('mini-total-tokens').textContent = formatNumber(data.totals.total_tokens); document.getElementById('mini-today-tokens').textContent = formatNumber(data.totals.today_tokens); document.getElementById('chat-requests').textContent = formatNumber(data.totals.chat_requests); document.getElementById('today-requests').textContent = formatNumber(data.totals.today_requests); document.getElementById('avg-duration').textContent = `${data.totals.average_duration_ms} ms`; document.getElementById('success-rate').textContent = `成功率 ${data.totals.success_rate}%`; document.getElementById('latest-model').textContent = data.recent_requests.length ? (data.recent_requests[0].model || '-') : '-'; document.getElementById('last-refresh').textContent = `最近刷新 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
            drawLineChart(document.getElementById('daily-chart'), data.curves.daily.map((item) => item.date.slice(5)), data.curves.daily.map((item) => item.tokens), '#b14d28', 'rgba(177,77,40,0.12)', function(point) {
                alert(`日期: ${point.label}\nToken 消耗: ${formatNumber(point.value)}`);
            });
            drawLineChart(document.getElementById('hourly-chart'), data.curves.hourly.map((item, index) => index % 3 === 0 ? item.hour : ''), data.curves.hourly.map((item) => item.tokens), '#2f7d4a', 'rgba(47,125,74,0.12)', function(point) {
                alert(`时间: ${point.label}\nToken 消耗: ${formatNumber(point.value)}`);
            });
            renderRecent(data.recent_requests);
        }
        refresh(); setInterval(refresh, 10000);
    </script>
</body>
</html>"""


def get_provider(
    settings: Settings = Depends(get_settings),
    runtime: ServiceRuntime = Depends(get_runtime),
) -> ChatProvider:
    if settings.upstream_provider == "kimi_cli":
        return KimiCLIProvider(settings, runtime.session_store, runtime)
    if settings.upstream_provider == "openai_compatible":
        return OpenAICompatibleProvider(settings)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Unsupported provider: {settings.upstream_provider}",
    )


def verify_local_api_key(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = f"Bearer {settings.local_api_key}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid local API key",
        )


@app.exception_handler(httpx.HTTPStatusError)
async def upstream_http_error_handler(_, exc: httpx.HTTPStatusError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.response.status_code,
        content={
            "error": {
                "message": exc.response.text,
                "type": "upstream_http_error",
                "status_code": exc.response.status_code,
            }
        },
    )


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    runtime = get_runtime()
    request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    request_body = await request.body()
    started_at = time.perf_counter()
    status_code = 500
    client_host = request.client.host if request.client else "unknown"
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-Id"] = request_id

        response_headers = dict(response.headers)
        duration_ms = int((time.perf_counter() - started_at) * 1000)

        if hasattr(response, "body_iterator") and response.body_iterator is not None:
            original_iterator = response.body_iterator

            async def logging_iterator() -> TypingAsyncIterator[bytes]:
                captured = bytearray()
                try:
                    async for chunk in original_iterator:
                        normalized = _normalize_response_chunk(chunk)
                        captured.extend(normalized)
                        yield normalized
                finally:
                    runtime.log_http_exchange(
                        request_id=request_id,
                        method=request.method,
                        path=request.url.path,
                        query=request.url.query,
                        client=client_host,
                        status_code=status_code,
                        duration_ms=duration_ms,
                        request_headers=dict(request.headers),
                        request_body=request_body,
                        response_headers=response_headers,
                        response_body=bytes(captured),
                    )

            response.body_iterator = logging_iterator()
        else:
            response_body = getattr(response, "body", b"") or b""
            runtime.log_http_exchange(
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                query=request.url.query,
                client=client_host,
                status_code=status_code,
                duration_ms=duration_ms,
                request_headers=dict(request.headers),
                request_body=request_body,
                response_headers=response_headers,
                response_body=response_body,
            )
        return response
    finally:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        runtime.logger.info(
            "request_id=%s method=%s path=%s status=%s duration_ms=%s client=%s",
            request_id,
            request.method,
            request.url.path,
            status_code,
            duration_ms,
            client_host,
        )


async def enforce_limits(
    request: Request,
    runtime: ServiceRuntime = Depends(get_runtime),
) -> ServiceRuntime:
    api_key = request.headers.get("Authorization", "anonymous")
    allowed, retry_after = await runtime.rate_limiter.check(api_key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    return runtime


@app.exception_handler(httpx.RequestError)
async def upstream_request_error_handler(_, exc: httpx.RequestError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={
            "error": {
                "message": str(exc),
                "type": "upstream_request_error",
                "status_code": status.HTTP_502_BAD_GATEWAY,
            }
        },
    )


@app.exception_handler(ProviderExecutionError)
async def provider_execution_error_handler(_, exc: ProviderExecutionError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "message": exc.message,
                "type": "provider_execution_error",
                "status_code": exc.status_code,
            }
        },
    )


@app.get("/health", response_model=HealthResponse)
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    upstream_target = settings.upstream_base_url or settings.kimi_cli_path or "auto-detect kimi.exe"
    return HealthResponse(
        status="ok",
        provider=settings.upstream_provider,
        upstream_target=upstream_target,
        model=settings.upstream_model,
    )


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@app.get("/dashboard", include_in_schema=False)
async def dashboard() -> HTMLResponse:
    return HTMLResponse(content=_dashboard_html())


@app.get("/api/dashboard/stats", include_in_schema=False)
async def dashboard_stats(runtime: ServiceRuntime = Depends(get_runtime)) -> JSONResponse:
    return JSONResponse(content=runtime.build_dashboard_stats())


@app.get("/v1/models", response_model=ModelListResponse, dependencies=[Depends(verify_local_api_key)])
async def list_models(settings: Settings = Depends(get_settings)) -> ModelListResponse:
    return ModelListResponse(data=[ModelCard(id=settings.upstream_model)])


@app.post("/v1/chat/completions", dependencies=[Depends(verify_local_api_key)])
async def chat_completions(
    fastapi_request: Request,
    request: ChatCompletionRequest,
    provider: ChatProvider = Depends(get_provider),
    runtime: ServiceRuntime = Depends(enforce_limits),
):
    try:
        await asyncio.wait_for(runtime.semaphore.acquire(), timeout=runtime.max_queue_wait_seconds)
    except TimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Server is busy, queue wait timeout") from exc

    if request.stream:
        async def iterator() -> AsyncIterator[bytes]:
            try:
                async for chunk in provider.stream_chat_completions(request):
                    yield chunk
            finally:
                runtime.semaphore.release()

        response = StreamingResponse(iterator(), media_type="text/event-stream")
        for key, value in provider.get_response_headers().items():
            response.headers[key] = value
        return response

    try:
        result = await provider.chat_completions(request)
    finally:
        runtime.semaphore.release()

    response = JSONResponse(content=result)
    for key, value in provider.get_response_headers().items():
        response.headers[key] = value
    if hasattr(fastapi_request.state, "request_id"):
        response.headers["X-Request-Id"] = fastapi_request.state.request_id
    return response
