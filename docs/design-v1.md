# report.md — 设计取舍报告

按作业要求回答三件事：架构、打包、再加一个工具。

## 一、架构

```
┌────────────────────────── pywebview 壳（桌面窗口，有标题栏）─────────┐
│  ui/index.html + app.js      ← 只是订阅者之一                         │
└───────────────▲────────────────────────────────────────────────────┘
                │ WebSocket（页面用）
┌───────────────┴──────────── FastAPI（127.0.0.1:8765）───────────────┐
│  POST /api/message   起线程跑 agent                                  │
│  GET  /api/events    SSE —— curl -N 也能看的事件流                   │
│  WS   /ws            双工：发消息 + 收事件                            │
│  POST /api/stop      真中断：置位 cancel + 杀 bash 进程               │
│  GET  /api/tree|file 左栏文件树 / 中栏代码视图的数据源                 │
│         EventBus（一个发布者，N 个订阅者）                            │
└───────────────▲────────────────────────────────────────────────────┘
                │ emit() 事件
┌───────────────┴──────────── agent/（可脱离界面单独跑）───────────────┐
│  loop.py    ReAct 循环：决策→动作→观察→再决策                        │
│             三道护栏：max_steps / cancel_event / 请求与命令超时        │
│  tools.py   read / write / edit / bash + 路径沙箱(resolve+前缀校验)   │
│  llm.py     anthropic / openai 兼容 / mock 三后端，统一 ask() 接口    │
│  mcp_client.py  纯标准库 JSON-RPC over stdio（initialize→list→call） │
│             MCP 工具命名 mcp__<server>__<tool>，注册表带 source 字段  │
└─────────────────────────────────────────────────────────────────────┘
```

### 为什么 agent 不依赖 UI（这是评审第一眼看的）

`agent/` 对 FastAPI、webview 零依赖，`python -m agent.loop "任务"` 就能跑。
事件通过构造时注入的 `emit` 回调往外吐 —— 循环不知道也不关心对面是窗口、
是 SSE 还是 print。这样加 MCP 时只改 agent 内部的工具注册表，前端
因为事件里带了 `source` 字段，一行没改就显示出了 MCP 徽章。

### 关键取舍

1. **事件流走后端总线，不走前端转发。** EventBus 是唯一的发布点，
   SSE / WS / 页面都是订阅者。所以"关掉前端 curl -N 还能看到事件流"
   不是额外做的功能，是架构的自然结果。
2. **edit 报错而不是猜。** 出现 0 次 / ≥2 次都报错（见 trace.md step 2），
   模型收到错误文本后补充上下文重试。模糊匹配和整文件重写各试过一版，
   都会造成"改一个字符整个文件重排"，直接否掉。
3. **路径沙箱在工具层不在提示词层。** `resolve()` + `commonpath` 前缀校验，
   `../../etc/passwd` 直接被拒。提示词会被绕过，代码不会。
4. **bash 输出截断 + 超时。** 超过 16KB 截断，默认 30s 超时，防 pip install
   卡死整个循环；非 0 退出码正常返回，模型能看到 stderr 自救。
5. **上下文控制。** 工具结果进历史前截到 8KB；read 分页并在结果里声明
   "还有 N 行"，模型自己决定要不要续读。

## 二、打包

选 PyInstaller，理由与完整步骤见 `docs/build.md`。一句话版本：
项目是纯 Python，Electron/Tauri 引入 Node/Rust 工具链对两周工期是负资产；
`--windowed --add-data "ui;ui"` 一条命令出包，资源路径用 `sys._MEIPASS`
语义推导，可写目录不依赖 cwd。

## 三、如果只能再加一个工具

**加 `grep`（工作区内容搜索，返回 文件:行号:匹配行，上限 100 条）。**

理由：

1. `bash` 理论上能做搜索，但模型要自己拼 `findstr`/`grep` 语法，
   Windows 上还分裂成 findstr / find / PowerShell Select-String 三套。
   一个受控的 grep 把"搜索"从三选一的猜变成一个确定动作。
2. 它直接服务 edit 的前置条件：edit 要求 old 唯一，模型需要先找到
   所有出现位置。现在的做法是 read 全文件再自己找 —— 大文件上又慢又贵。
   grep 一发出去，"不唯一"报错的次数会显著下降。
3. 为什么不是别的：
   - **diff 预览工具**：已经在事件流里实现了（tool_result 带 diff 字段），不需要；
   - **git 专用工具**：bash 够用，而且包一层 git 反而让模型以为不能直接跑 git 命令；
   - **python 执行工具**：和 bash 重复，工具越多模型选错概率越高。

### 最不满意的地方

LLM 调用是非流式的：模型思考 30 秒时界面只能显示"运行中"。
正确做法是接 SSE 流式输出、token 级转发到前端。两周内先把护栏和
工具语义做扎实，流式留给 v1.1 —— 这是本项目最诚实的欠账。
