"""Prompt registry: loads prompts/*.md files with frontmatter (§26).

File format:
    ---
    id: answer_question
    version: 1
    purpose: ...
    ---
    prompt body with {placeholders}

Placeholders are filled with str.format-style substitution via safe_format
(missing keys are left visible instead of raising, so a prompt edit can
never take the app down).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import PROMPTS_DIR

_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


@dataclass
class Prompt:
    id: str
    version: str
    body: str
    meta: dict

    def render(self, **kwargs) -> str:
        out = self.body
        for k, v in kwargs.items():
            out = out.replace("{" + k + "}", str(v))
        return out


_cache: dict[str, Prompt] = {}


def get_prompt(prompt_id: str) -> Prompt:
    if prompt_id in _cache:
        return _cache[prompt_id]
    path = PROMPTS_DIR / f"{prompt_id}.md"
    raw = Path(path).read_text(encoding="utf-8")
    meta: dict = {}
    body = raw
    m = _FRONT_RE.match(raw)
    if m:
        body = raw[m.end():]
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    p = Prompt(id=meta.get("id", prompt_id), version=str(meta.get("version", "1")),
               body=body.strip(), meta=meta)
    _cache[p.id] = p
    return p
