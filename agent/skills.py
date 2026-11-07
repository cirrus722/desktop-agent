"""技能目录与按需加载。

启动时只把 name + description 放进上下文；SKILL.md 正文只有在模型调用
use_skill(name) 后才注入。这样技能越多，目录成本线性增长，但不会把每一份
流程正文都塞进每一轮请求。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\s]")
_TRIGGER_RE = re.compile(r"[“\"「]([^”\"」]+)[”\"」]")


def estimate_tokens(text: str) -> int:
    """稳定、无第三方依赖的近似 token 计数。

    中文按字、英文按词，标点各算一个。用于比较目录与正文的相对成本，
    不是某个厂商 tokenizer 的精确复刻，但同一份文本的比值是可复现的。
    """
    return len(_TOKEN_RE.findall(text or ""))


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    root: Path
    full_text: str
    has_scripts: bool
    uses_mcp: bool
    triggers: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.body)


def _parse_skill(path: Path) -> Skill:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path} 缺少 YAML frontmatter")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise ValueError(f"{path} 的 frontmatter 没有结束标记")

    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"{path} frontmatter 行无法解析：{line}")
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")

    name = meta.get("name", "").strip()
    description = meta.get("description", "").strip()
    if not name or not description:
        raise ValueError(f"{path} 必须包含 name 与 description")
    if name != path.parent.name:
        raise ValueError(f"{path} 的 name={name!r} 与目录名 {path.parent.name!r} 不一致")

    body = "\n".join(lines[end + 1 :]).strip() + "\n"
    scripts_dir = path.parent / "scripts"
    has_scripts = scripts_dir.is_dir() and any(p.is_file() for p in scripts_dir.rglob("*"))
    return Skill(
        name=name,
        description=description,
        body=body,
        path=path,
        root=path.parent,
        full_text=text,
        has_scripts=has_scripts,
        uses_mcp="mcp__" in body,
        triggers=[t.strip() for t in _TRIGGER_RE.findall(description) if t.strip()],
    )


class SkillRegistry:
    """一个目录里的技能集合。关闭 ENABLE_SKILLS 时退化为空注册表。"""

    def __init__(self, root: str | Path, enabled: bool | None = None):
        self.root = Path(root)
        self.enabled = _env_enabled("ENABLE_SKILLS") if enabled is None else enabled
        self.skills: list[Skill] = []
        self.errors: list[str] = []
        if not self.enabled or not self.root.is_dir():
            return
        for path in sorted(self.root.glob("*/SKILL.md")):
            try:
                self.skills.append(_parse_skill(path))
            except Exception as exc:
                self.errors.append(str(exc))
        self.skills.sort(key=lambda s: s.name)

    def __len__(self) -> int:
        return len(self.skills)

    def get(self, name: str) -> Skill | None:
        return next((s for s in self.skills if s.name == name), None)

    def catalog_text(self) -> str:
        if not self.enabled:
            return ""
        lines = ["技能目录（只加载了 name + description，正文尚未进入上下文）："]
        for skill in self.skills:
            lines.append(f"- {skill.name}: {skill.description}")
        lines.append("需要某个技能的流程时，调用 use_skill(name) 加载正文；不要凭目录名猜步骤。")
        return "\n".join(lines)

    def schemas(self) -> list[dict]:
        if not self.enabled:
            return []
        names = ", ".join(s.name for s in self.skills) or "(无)"
        return [{
            "name": "use_skill",
            "description": (
                "按需加载一个技能的完整流程。技能目录已在 system prompt 中；"
                "当用户请求匹配某个技能的 description 时调用。可用技能：" + names
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "技能目录中的 kebab-case 名称"},
                },
                "required": ["name"],
            },
        }]

    def use_skill(self, name: str) -> dict:
        if not self.enabled:
            return {
                "content": [{"type": "text", "text": "技能系统已关闭（ENABLE_SKILLS=0）。"}],
                "display": "技能系统已关闭",
                "error": True,
            }
        skill = self.get(name)
        if skill is None:
            available = ", ".join(s.name for s in self.skills) or "(无)"
            return {
                "content": [{"type": "text", "text": f"未知技能：{name}。可用技能：{available}"}],
                "display": f"未知技能 {name}",
                "error": True,
            }
        header = f"技能 {skill.name} 已加载。技能目录：{skill.root}\n\n"
        return {
            "content": [{"type": "text", "text": header + skill.body}],
            "display": f"加载技能 {skill.name}（{skill.tokens} tokens）",
            "source": "skill",
            "skill": skill.name,
        }

    def match(self, query: str) -> str | None:
        """给验收试验用的确定性触发器。

        真正的模型触发仍由 description 决定；这里的匹配只复用 description 中
        引号里的显式触发语，避免验收脚本另维护一份关键词表。
        """
        q = (query or "").lower().replace(" ", "")
        best: tuple[int, str] | None = None
        for skill in self.skills:
            score = 0
            for trigger in skill.triggers:
                t = trigger.lower().replace(" ", "")
                if t and t in q:
                    score += len(t)
            if score and (best is None or score > best[0]):
                best = (score, skill.name)
        return best[1] if best else None

    def stats(self) -> dict:
        catalog_tokens = estimate_tokens(self.catalog_text())
        full_tokens = sum(estimate_tokens(s.full_text) for s in self.skills)
        ratio = round(full_tokens / catalog_tokens, 1) if catalog_tokens else 0.0
        return {
            "skills": [
                {"name": s.name, "has_scripts": s.has_scripts, "uses_mcp": s.uses_mcp}
                for s in self.skills
            ],
            "catalog_tokens": catalog_tokens,
            "full_tokens": full_tokens,
            "disclosure_ratio": ratio,
            "errors": self.errors,
        }
