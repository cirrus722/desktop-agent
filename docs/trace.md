# trace.md — 一次完整运行记录

- 时间：2026-10-27
- 任务：`修复 stats.py 里 mean 对空列表的处理`
- 提供方：LLM_PROVIDER=mock（剧本模式，无需 Key；真实 Key 时流程完全一致，只是模型输出不可预演）
- 采集方式：`curl -N http://127.0.0.1:8765/api/events`（关掉前端，事件流照样完整可见）

## 事件时间线

### step 1 · read（内置工具）

```json
{"name": "read", "arguments": {"path": "stats.py"}}
```

```
stats.py 共 22 行，显示第 1–22 行
    9  def mean(nums):
   10      if not nums:
   11          return 0
   ...
```

### step 2 · edit（内置工具）—— 故意失败：不唯一

```json
{"name": "edit", "arguments": {"path": "stats.py", "old": "return 0", "new": "return 0.0"}}
```

```
错误：old 在 stats.py 中出现 2 次，不唯一 —— 请补充上下文，不许猜
```

> 唯一性检查把不明确的修改挡了回去：`return 0` 同时是 mean 的返回值和
> variance 里 `return 0.0` 的子串。模型看到错误后补充上下文重试 —— 这正是
> "出错时不会乱改"的证据。

### step 3 · edit（内置工具）—— 补充上下文后成功

```json
{"name": "edit", "arguments": {"path": "stats.py",
  "old": "def mean(nums):\n    if not nums:\n        return 0\n",
  "new": "def mean(nums):\n    if not nums:\n        return 0.0\n"}}
```

```diff
--- stats.py
+++ stats.py
@@ -9,5 +9,5 @@
 def mean(nums):
     if not nums:
-        return 0
+        return 0.0
     total = 0
     for n in nums:
```

### step 4 · bash（内置工具）—— 跑测试验证

```json
{"name": "bash", "arguments": {"command": "python test_stats.py", "timeout": 20}}
```

```
exit_code: 0
stdout:
Ran 6 tests ... OK
```

### step 5 · mcp__filesystem-echo__echo（MCP 工具，source=mcp）

```json
{"name": "mcp__filesystem-echo__echo", "arguments": {"text": "MCP 链路正常"}}
```

```
echo: MCP 链路正常
```

> MCP 工具的名字带 `mcp__<server>__<tool>` 前缀，注册表里 source 字段为
> `mcp:filesystem-echo` —— 和内置工具在代码里一眼可区分。

### 最终回答

> 已完成：mean 对空列表返回 0.0（浮点，保持类型一致），测试全部通过。MCP echo 调用成功。

## 另外两次手工验证

1. **真中断**：后台起 `bash("sleep 30")`，1.5s 后调 `interrupt()` →
   进程 6.5s 内退出（`box.running_proc.poll() is not None`）。
   中断不是转圈，是杀进程 + 停循环。
2. **路径越界**：`read("../../etc/passwd")` →
   `错误：路径越界：../../etc/passwd 不在工作区内`。
   模型可以"想"读任何文件，`resolve()` + 前缀校验把它挡在目录外。
