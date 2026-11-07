"""桌面入口：双击图标 → 出窗口（有标题栏，不是 localhost 网页）。

架构：pywebview 只是壳，里面加载的是本机 uvicorn 服务的页面。
后端先于窗口启动 —— 关掉窗口，curl -N 依然能看到事件流。

用法：
  python main.py                 # 桌面窗口模式
  python main.py --server-only   # 纯后端模式（演示 curl -N 事件流用）
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

PORT = 8765
ROOT = Path(__file__).resolve().parent


def start_server():
    import uvicorn
    from server.main import app
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def main():
    server_only = "--server-only" in sys.argv
    t = threading.Thread(target=start_server, daemon=True)
    t.start()

    if server_only:
        print(f"后端已启动：http://127.0.0.1:{PORT}")
        print(f"事件流：    curl -N http://127.0.0.1:{PORT}/api/events")
        print("按 Ctrl+C 退出。")
        t.join()
        return

    import webview
    # 等后端就绪再开窗口，避免白屏
    import time
    import urllib.request
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/tree", timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)
    webview.create_window("Desktop Agent — 你自己的 Cursor", f"http://127.0.0.1:{PORT}",
                          width=1280, height=800, min_size=(960, 600))
    webview.start()


if __name__ == "__main__":
    main()
