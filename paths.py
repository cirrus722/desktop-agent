r"""源码运行与安装版运行共用的路径/环境准备。

源码模式：一切都在项目目录，保持原开发体验。
安装版：程序只读资源在 PyInstaller 的 _internal，用户配置放 %APPDATA%\DesktopAgent，
工作区放“文档\DesktopAgent\workspace”，并首次把示例文件复制过去。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "DesktopAgent"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_installed() -> bool:
    """安装版布局：app 目录的上一级有 runtime/python/python.exe。"""
    if is_frozen() or os.environ.get("DESKTOP_AGENT_INSTALLED") == "1":
        return True
    here = Path(__file__).resolve().parent
    return (here.parent / "runtime" / "python" / "python.exe").is_file()


def is_packaged() -> bool:
    return is_frozen() or is_installed()


def resource_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def config_dir() -> Path:
    override = os.environ.get("DESKTOP_AGENT_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if not is_packaged():
        return resource_dir()
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / APP_NAME


def env_file() -> Path:
    return config_dir() / ".env"


def session_dir() -> Path:
    return config_dir() / ".session"


def workspace_dir() -> Path:
    override = os.environ.get("AGENT_WORKSPACE")
    if override:
        return Path(override).expanduser().resolve()
    if not is_packaged():
        return resource_dir() / "workspace"
    return Path.home() / "Documents" / APP_NAME / "workspace"


def bundled_python() -> Path | None:
    if not is_packaged():
        return None
    current = Path(sys.executable).resolve()
    if current.name.lower() == "pythonw.exe":
        current = current.with_name("python.exe")
    if current.name.lower() == "python.exe" and (current.parent / "python312.dll").is_file():
        return current
    exe_dir = Path(sys.executable).resolve().parent
    for candidate in (
        exe_dir / "runtime" / "python" / "python.exe",
        resource_dir() / "runtime" / "python" / "python.exe",
    ):
        if candidate.is_file():
            return candidate
    return None


def _copy_missing(src: Path, dst: Path):
    if not src.exists() or dst.exists():
        return
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def prepare_runtime() -> dict:
    """创建用户目录、首次铺示例工作区，并把自带 Python 放进 PATH。"""
    cfg = config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    session_dir().mkdir(parents=True, exist_ok=True)

    ws = workspace_dir()
    ws.mkdir(parents=True, exist_ok=True)
    if is_packaged():
        seed = resource_dir() / "workspace"
        if seed.is_dir():
            for item in seed.iterdir():
                _copy_missing(item, ws / item.name)
        for name in ("skills", "examples", "mcp.json", "AGENTS.md"):
            _copy_missing(resource_dir() / name, ws / name)
        os.environ["AGENT_PROJECT_ROOT"] = str(ws)
    else:
        os.environ["AGENT_PROJECT_ROOT"] = str(resource_dir())

    example_env = resource_dir() / ".env.example"
    if not env_file().exists() and example_env.is_file():
        shutil.copy2(example_env, env_file())

    py = bundled_python()
    if py:
        os.environ["DESKTOP_AGENT_PYTHON"] = str(py)
        os.environ["PATH"] = str(py.parent) + os.pathsep + os.environ.get("PATH", "")

    os.environ["AGENT_WORKSPACE"] = str(ws)
    return {"config": cfg, "workspace": ws, "python": py}
