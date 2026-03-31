# local-kimi-api

一个本地中转服务，对外暴露 OpenAI 兼容接口，对内直接调用本机 VS Code 的 Kimi Code 内置 CLI。这样第三方软件拿到的是你自定义的本地 API key，但真正执行请求的仍然是你已经登录的 Kimi Code 能力。

## 这套方案的本质

- 第三方软件调用你的本地 HTTP 服务
- 本地 HTTP 服务拉起 Kimi Code 自带的 `kimi.exe`
- `kimi.exe` 复用你本机已有的登录态，向 Kimi 发起请求
- 服务把结果包装成 OpenAI 兼容格式返回

这不是官方 API，也不会生成真正的云端 API key。

它更准确的说法是：

- 一个本地代理
- 一个伪 API 层
- 一个把 VS Code 里 Kimi 能力转成 HTTP 接口的适配器

## 目录结构

```text
local-kimi-api/
  app/
    providers/
    config.py
    main.py
    models.py
  .env.example
  requirements.txt
  start.py
```

## 安装

```powershell
cd Z:\AI-NOVEL\97-local-kimi-api\local-kimi-api
Z:/AI-NOVEL/97-local-kimi-api/.venv/Scripts/python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

然后修改 `.env`：

```env
LOCAL_API_KEY=local-kimi-demo-key
UPSTREAM_PROVIDER=kimi_cli
UPSTREAM_BASE_URL=
UPSTREAM_API_KEY=
UPSTREAM_MODEL=kimi-auto
KIMI_CLI_PATH=
KIMI_CLI_WORK_DIR=
KIMI_CLI_PASSTHROUGH_MODEL=false
MAX_CONCURRENT_REQUESTS=2
MAX_QUEUE_WAIT_SECONDS=30
RATE_LIMIT_MAX_REQUESTS=60
RATE_LIMIT_WINDOW_SECONDS=60
LOG_DIR=logs
SESSION_STORE_PATH=
REQUEST_TIMEOUT_SECONDS=120
HOST=127.0.0.1
PORT=8000
```

默认情况下，程序会自动从你安装的 VS Code 扩展目录里找到 Kimi Code 自带的 `archive.zip`，解压出 `kimi.exe` 并调用。

如果你想手工指定 CLI 路径，就填 `KIMI_CLI_PATH`。

如果你希望 Kimi 把某个目录当成工作区上下文，就填 `KIMI_CLI_WORK_DIR`。

`UPSTREAM_MODEL` 在 CLI 模式下只是给下游客户端看的本地模型别名，默认不会透传给 `kimi.exe`。这是为了避免第三方软件传一个 OpenAI 风格模型名，结果把 CLI 调挂。

并发和限流相关配置：

- `MAX_CONCURRENT_REQUESTS`: 同时最多跑几个 Kimi CLI 请求
- `MAX_QUEUE_WAIT_SECONDS`: 队列里最多等多久，超时返回 503
- `RATE_LIMIT_MAX_REQUESTS`: 一个窗口内最多允许多少请求
- `RATE_LIMIT_WINDOW_SECONDS`: 限流窗口长度
- `LOG_DIR`: 请求日志和 session 状态文件目录
- `SESSION_STORE_PATH`: 自定义 session 状态文件路径，留空时默认写到 `logs/sessions.json`

## 启动

```powershell
cd Z:\AI-NOVEL\97-local-kimi-api\local-kimi-api
Z:/AI-NOVEL/97-local-kimi-api/.venv/Scripts/python.exe start.py
```

推荐直接使用一键脚本：

```powershell
.\start.ps1
```

停止服务：

```powershell
.\stop.ps1
```

查看弹窗式状态监控：

```powershell
.\health-status.ps1
```

一键打开 Dashboard：

```powershell
.\open-dashboard.ps1
```

总控菜单入口：

```powershell
.\control.ps1
```

可直接在菜单里选择启动、停止、查看状态弹窗、打开 Dashboard。

也支持直接指定动作：

```powershell
.\control.ps1 -Action start
.\control.ps1 -Action stop
.\control.ps1 -Action status
.\control.ps1 -Action dashboard
```

日志目录会按日期自动分文件夹存储，例如：

```text
logs/
  2026-04-01/
    access.log
    audit.log
    service.out.log
    service.err.log
    service.pid
    sessions.json
```

## 接口

### 健康检查

```http
GET /health
```

### 模型列表

```http
GET /v1/models
Authorization: Bearer 你的本地 API key
```

### 聊天补全

```http
POST /v1/chat/completions
Authorization: Bearer 你的本地 API key
Content-Type: application/json
```

请求体示例：

```json
{
  "model": "kimi-auto",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "你好，做个自我介绍"}
  ],
  "stream": false
}
```

说明：

- `messages` 会在本地被整理成一段完整对话提示词，再交给 `kimi.exe`
- `model` 在默认配置下只是兼容字段，不会被传给 `kimi.exe`
- 如果你传 `extra_body.session_id`，服务会把它映射到 Kimi CLI 的 `--session`
- 如果你不传 `session_id`，但传了 `user`，服务会为这个 `user` 生成一个稳定 session
- 当前最稳定的是非流式调用
- `stream=true` 已兼容 SSE 返回，但底层仍是单轮完成后再回传，不是真正逐 token 流

## 会话复用

如果你要让第三方软件像真正 API 一样复用一个长期会话，可以在请求体里加：

```json
{
  "model": "kimi-auto",
  "messages": [
    {"role": "user", "content": "记住暗号：青石桥"}
  ],
  "extra_body": {
    "session_id": "demo-session-001"
  }
}
```

下一次请求继续带同一个 `session_id` 即可。你既可以发送完整历史，也可以只发本轮新增消息；代理会把会话状态持久化到 `logs/sessions.json`。

非流式响应里还会额外返回：

- `session_id`
- `local_kimi.session_mode`

响应头也会带：

- `X-Local-Kimi-Session-Id`
- `X-Local-Kimi-Session-Mode`

## 第三方软件如何接

多数支持 OpenAI API 的软件，只需要填这三个值：

- Base URL: `http://127.0.0.1:8000/v1`
- API Key: 你在 `.env` 里定义的 `LOCAL_API_KEY`
- Model: `.env` 里的 `UPSTREAM_MODEL`

也就是说，对下游软件来说，它看到的就是一个普通 OpenAI 兼容接口；对你来说，背后实际跑的是本机 Kimi Code CLI。

## curl 测试

```powershell
curl http://127.0.0.1:8000/health

curl http://127.0.0.1:8000/v1/chat/completions `
  -H "Authorization: Bearer local-kimi-demo-key" `
  -H "Content-Type: application/json" `
  -d '{"model":"kimi-auto","messages":[{"role":"user","content":"你好"}]}'
```

## 已验证的关键点

- 已确认本机安装了 `moonshot-ai.kimi-code` 扩展
- 已确认扩展内置 `kimi.exe`
- 已确认 `kimi.exe --quiet -p "你好，回复一个词：收到"` 能直接返回结果

这说明当前机器上，CLI 已经可以复用你的登录态，方案是成立的。

## 日志与队列

- 请求访问日志写在 `logs/access.log`
- session 状态默认写在 `logs/sessions.json`
- 当并发超过 `MAX_CONCURRENT_REQUESTS` 时，请求会进入队列等待
- 当等待时间超过 `MAX_QUEUE_WAIT_SECONDS` 时，接口返回 503
- 当单个 API key 在窗口内超过 `RATE_LIMIT_MAX_REQUESTS` 时，接口返回 429
- Dashboard 的统计会从 `LOG_DIR` 下所有历史 `audit.log` 累加读取，因此总请求数、总 token、近 7 天曲线不会因为服务重启而清零
- `/health`、`/dashboard`、`/api/dashboard/stats` 这类监控和面板自刷新请求不会计入业务请求统计

## 实现原理

1. 服务收到 `/v1/chat/completions` 请求
2. 校验你自定义的 `LOCAL_API_KEY`
3. 结合 `session_id` 和本地 session 存储，决定是发完整消息还是只发增量消息
4. 本地启动 `kimi.exe --session ... -p "..."`
5. 读取 stdout
6. 包装成 OpenAI 兼容 JSON 返回

## 局限

- 这不是官方 API，稳定性取决于 Kimi Code CLI 是否保持兼容
- 如果扩展升级后 CLI 参数变化，适配层要跟着改
- 当前 `stream=true` 是兼容模式，不是原生 token 流
- `stream=true` 仍然是兼容流，不是原生 token 流
- session 持久化目前是单机本地文件存储，不适合多实例横向扩展

## 后续可扩展方向

- 增加基于 CLI session 的会话复用
- 增加 `/v1/embeddings` 的本地兼容占位层
- 增加请求日志、限流和并发队列
- 增加一个独立的 CLI 守护进程，避免每次请求都重新拉起 `kimi.exe`
