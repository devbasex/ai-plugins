"""stage_skills.py: 工程から worker へ届ける Skill を決める（#2376）。

工程と Skill の組は同じディレクトリの `stage-skills.json`（Skill の対応表）にだけ置く。ここは表を読み、
ステップから届ける Skill を決め、プランの欄を照らし、本文の節を組む。どれも副作用を持たず、読めないときは
`SkillsError` で知らせる。

- 対応表に置ける Skill は `rule_skills`（規則の Skill）に載る名前だけである。工程 Skill は置けない
- ステップの `skills` は対応表の値を置き換え、`skills_add` はその後ろへ足す（重なる名前は 1 つにする）
"""

from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
TABLE = HERE / "stage-skills.json"
SKILLS = HERE.parents[1] / "skills"
FIELDS = ("skills", "skills_add")
HEADING = "## このステップで従う Skill"
FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)


class SkillsError(Exception):
    """対応表か SKILL.md を読めない。`what` は読めなかった名前かファイル。"""

    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


def skill_path(name: str, skills: Path | None = None) -> Path:
    return (skills or SKILLS) / name / "SKILL.md"


def _names(value) -> bool:
    return isinstance(value, list) and all(isinstance(n, str) and n for n in value)


def load_table(path: Path | None = None) -> dict:
    """対応表を読む。形が誤っているか、値に `rule_skills` に無い名前があれば `SkillsError`。"""
    path = path or TABLE
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SkillsError(f"対応表を読めない: {path}（{e}）") from e
    rules, stages = (data.get("rule_skills"), data.get("stages")) if isinstance(data, dict) else (None, None)
    if not _names(rules) or not isinstance(stages, dict) or not all(_names(v) for v in stages.values()):
        raise SkillsError(f"対応表の形が誤っている: {path}")
    bad = sorted({n for v in stages.values() for n in v if n not in rules})
    if bad:
        raise SkillsError(f"対応表の値が規則の Skill でない: {', '.join(bad)}（{path}）")
    return data


def resolve(step: dict, table: dict) -> list[str]:
    """ステップへ届ける Skill の名前（I4）。`skills` があればそれ、無ければ工程の行（無ければ空）に `skills_add` を足す。"""
    base = step.get("skills")
    if base is None:
        base = table["stages"].get(step.get("stage") or "", [])
    out: list[str] = []
    for n in [*base, *(step.get("skills_add") or [])]:
        if n not in out:
            out.append(n)
    return out


def plan_errors(steps, table_path: Path | None = None, skills: Path | None = None) -> list[str]:
    """プランの `skills`・`skills_add` 欄の誤り（I3）。1 件ごとに `<ステップの id>: <理由>` を返す。"""
    found = [(s, f) for s in steps for f in FIELDS if f in s]
    if not found:
        return []
    try:
        rules = load_table(table_path)["rule_skills"]
    except SkillsError as e:
        return [f"{s.get('id')}: 欄を照らせない（{e.what}）" for s, _ in found]
    errs = []
    for step, field in found:
        sid, value = step.get("id"), step[field]
        if not _names(value):
            errs.append(f"{sid}: {field} は Skill の名前の配列でない")
            continue
        for n in value:
            if n not in rules:
                if skill_path(n, skills).is_file():
                    errs.append(f"{sid}: {n} は規則の Skill でない（工程 Skill の手順は起動指示の「手順」へ抜粋で渡す）")
                else:
                    errs.append(f"{sid}: {n} の SKILL.md が無い")
    return errs


def render(names: list[str], skills: Path | None = None) -> str:
    """本文の節（system プロンプトの後ろへ入れる形）。0 件なら空文字。SKILL.md を読めなければ `SkillsError`。"""
    if not names:
        return ""
    parts = [HEADING]
    for n in names:
        f = skill_path(n, skills)
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as e:
            raise SkillsError(f"SKILL.md が無い: {f}") from e
        parts.append(f"### {n}\n置き場: {f}（本文の相対パスはこのファイルから読む）\n\n{FRONTMATTER.sub('', text, 1).strip()}")
    return "\n\n".join(parts)
