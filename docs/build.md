# build.md — 打包步骤

打包目标：一个双击就出窗口的 .exe（Windows）。方案选 **PyInstaller**：
项目是纯 Python（FastAPI 后端 + pywebview 壳），Electron/Tauri 要引入
Node 工具链，对这个体量是杀鸡用牛刀。

## 1. 安装打包依赖

```bash
pip install pyinstaller==6.11.1
pip install -e .            # 或按 pyproject 装运行依赖
```

## 2. 打包命令

在项目根目录执行：

```bash
pyinstaller --windowed --name DesktopAgent \
  --add-data "ui;ui" \
  --add-data "skills;skills" \
  --add-data "AGENTS.md;." \
  --add-data "mcp.json;." \
  --add-data "examples;examples" \
  main.py
```

注意 Windows 上 `--add-data` 的分隔符是 `;`（Linux/macOS 是 `:`）：
`--add-data "ui;ui"`。技能正文、AGENTS.md 与 MCP 示例 server 必须一起打包，
否则安装包启动后 `use_skill` 会找不到目录。

产物在 `dist/DesktopAgent/DesktopAgent.exe`。

## 3. 打包后路径问题的两件事（都已在代码里处理）

PyInstaller 运行时把资源解到 `sys._MEIPASS` 临时目录，工作目录不等于项目目录：

1. **ui/ 静态资源找不到** —— `server/main.py` 用
   `ROOT = Path(__file__).resolve().parent.parent` 定位，
   PyInstaller 下 `__file__` 指向解包目录，`--add-data "ui;ui"` 把 ui/
   放在同一层级，路径成立。
2. **可写目录** —— 会话文件写进 exe 同级的 `.session/`，工作区默认
   `<项目根>/workspace`；两者都基于 ROOT 推导，不依赖 cwd，
   双击运行（cwd 在系统目录）也不会写错地方。

## 4. 打包后自查（Release 前必做）

- [ ] 双击 dist 里的 exe，出窗口、有标题栏、无 localhost 地址栏
- [ ] 窗口里发一条消息，工具卡片正常出现
- [ ] 中断按钮点了之后 ≤1s 恢复输入状态
- [ ] 把 exe 拷到另一台没装 Python 的机器上再双击一次
- [ ] 确认 exe 体积里没有带上 .env（Key 绝不进包）

## 5. 为什么不选另外两条路

| 方案 | 结论 |
|---|---|
| Electron | 要装 Node + electron-builder，产物 150MB+，构建慢；本项目后端是 Python，还得再架一层进程通信 |
| Tauri | 产物最小，但前端要 Rust 工具链，两周期限里学习成本不值 |
| **PyInstaller（选用）** | 纯 Python 一条命令出包，60MB 左右，与后端同一进程，最不容易出"装完打不开"的问题 |
