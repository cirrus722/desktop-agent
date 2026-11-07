# 项目地图

- `agent/loop.py` — ReAct 循环、工具注册、系统提示装配。
- `agent/tools.py` — read / write / edit / bash 四个内置工具与路径沙箱。
- `agent/mcp_client.py` — stdio JSON-RPC MCP 客户端，工具名带 `mcp__` 前缀。
- `agent/skills.py` — `skills/` 目录发现、catalog 与 `use_skill` 按需加载。
- `agent/agents_md.py` — 全局与项目 `AGENTS.md` 的分层注入。
- `agent/hooks.py` — PreToolUse 拦截与 PostToolUse 自动测试。
- `agent/reviewer.py` — 只读 reviewer 子 agent，只回最终摘要。
- `server/main.py` — FastAPI、SSE、WebSocket、中断与会话历史。
- `ui/` — 三栏界面，不依赖前端构建工具。
- `app/inspect.py` — 生成 `skill_report.json` 与 `custom_report.json`。
- `skills/` — 可带走的流程文件；每个目录一个技能。
- `workspace/` — 智能体的沙箱工作区与演示项目。

# 构建与测试

```text
python -m app.selftest
python -m app.inspect skills --out skill_report.json
python -m app.inspect custom --out custom_report.json
python -m agent.loop "修复 stats.py"
```

没有 API Key 时使用 `LLM_PROVIDER=mock`；真实模型走 `.env`，不要把 Key 写进仓库。

# 代码风格

- 标准库优先；需要新依赖先说明理由，并同步更新 `pyproject.toml`。
- 错误一律显式返回或抛出，不用 `except: pass`。
- 新增工具必须同时实现 schema、execute 分支和一次失败路径测试。
- 面向模型的文字要短、具体，写清触发条件与完成判据。

# 坑

- `workspace/` 是沙箱根目录，所有文件路径都要经过 `ToolBox.resolve()`。
- `mcp.json` 里的 `python` 会在运行时替换成当前解释器，避免 PATH 不一致。
- 技能正文只在 `use_skill` 后进入上下文；不要把全文塞进 system prompt。
- 写 `.env`、`.pem`、`.key` 会被 hook 拒绝；真实密钥从环境变量读取。
- `ENABLE_SKILLS=0` 与 `ENABLE_HOOKS=0` 必须保持 v1.0 可运行。
