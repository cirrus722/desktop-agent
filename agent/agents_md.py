"""AGENTS.md 的分层发现与注入。

顺序固定为：全局（用户目录）→ 项目（从仓库根到当前工作目录）。项目级
说明更具体，因此放在后面，模型读到时不会被更早的通用说明覆盖。
"""

from __future__ import annotations

import os
from pathlib import Path

MAX_AGENTS_BYTES = 12_000


def _read_text(path: Path) -> tuple[str, int]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    if len(raw) > MAX_AGENTS_BYTES:
        text = text[:MAX_AGENTS_BYTES] + "\n\n[已截断：AGENTS.md 超过 12000 字节]"
    return text, len(raw)


def _global_path() -> Path | None:
    override = os.environ.get("AGENT_GLOBAL_AGENTS")
    if override:
        p = Path(override).expanduser()
        return p if p.is_file() else None
    home = Path.home()
    for candidate in (home / ".agents" / "AGENTS.md", home / ".codex" / "AGENTS.md"):
        if candidate.is_file():
            return candidate
    return None


def _project_paths(workspace: Path) -> list[Path]:
    found: list[Path] = []
    current = workspace.resolve()
    for parent in [current, *current.parents]:
        candidate = parent / "AGENTS.md"
        if candidate.is_file():
            found.append(candidate)
    return list(reversed(found))


def load_agents_md(workspace: str | Path) -> dict:
    workspace = Path(workspace).resolve()
    entries: list[tuple[str, Path, str, int]] = []
    global_file = _global_path()
    if global_file:
        text, size = _read_text(global_file)
        entries.append(("global", global_file, text, size))
    for path in _project_paths(workspace):
        text, size = _read_text(path)
        label = "project" if path.parent == workspace else "project-parent"
        entries.append((label, path, text, size))

    sections = []
    for layer, path, text, _size in entries:
        sections.append(f"### {layer} · {path}\n{text.strip()}")
    prompt = ""
    if sections:
        prompt = "以下是自动注入的 AGENTS.md（全局 → 项目）：\n\n" + "\n\n".join(sections)

    layers: list[str] = []
    for layer, _path, _text, _size in entries:
        normalized = "project" if layer.startswith("project") else layer
        if normalized not in layers:
            layers.append(normalized)
    return {
        "found": bool(entries),
        "bytes": sum(size for _layer, _path, _text, size in entries),
        "layers": layers,
        "paths": [str(path) for _layer, path, _text, _size in entries],
        "text": prompt,
    }
