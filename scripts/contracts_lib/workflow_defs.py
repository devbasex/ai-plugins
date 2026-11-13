"""工程の定義（`plugins/ndf/contracts/workflow/`）の CI の検査（#3092 の I7〜I11）。

- 5 つのモードの定義を配布物の読み手（`plugins/ndf/scripts/lib/workflow_def.py` の `load_definition`）で読み、
  標準のスキーマ・語彙・入出力規約の参照・順序の決まりの誤りを指摘にする。検査の規則を 2 つに分けない
- 借りた標準のスキーマの本文の sha256 が `$comment` に書いた値と違えば落とす（取得元から差し替えずに書き換えた形）
- モードの工程の並びが `development-workflow` の工程表のそのモードの列（`—` でない行）と同じかを見る（I11）
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

WORKFLOW = "plugins/ndf/contracts/workflow"
READER = "plugins/ndf/scripts/lib/workflow_def.py"
SKILL_MD = "plugins/ndf/skills/development-workflow/SKILL.md"
TABLE_SECTION = "モードごとに起動する Skill"
ABSENT = "—"


def _reader(root: Path):
    spec = importlib.util.spec_from_file_location("ndf_workflow_def_for_contracts", root / READER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass が自分のモジュールを引くため
    spec.loader.exec_module(mod)
    return mod


def _md(root: Path):
    lib = str(root / "plugins/ndf/scripts/lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import md

    return md


def stage_columns(skill_md: str, md=None) -> dict[str, list[str]]:
    """工程表の本文から、モードごとの `—` でない行名の並びを取り出す。表が無ければ空の辞書。"""
    md = md or _md(Path(__file__).resolve().parents[2])
    sec = md.section_named(skill_md, TABLE_SECTION)
    if sec is None:
        return {}
    tables = md.tables_in(skill_md, [sec])
    if not tables:
        return {}
    t = tables[0]
    modes = [h.strip().strip("`") for h in t.header[1:]]
    out: dict[str, list[str]] = {m: [] for m in modes}
    for row in t.rows:
        name = row[0].strip().strip("`")
        for m, cell in zip(modes, row[1:]):
            if cell.strip() != ABSENT:
                out[m].append(name)
    return out


def check_table_match(snaps: dict, columns: dict[str, list[str]]) -> list[dict]:
    """モードの工程の並びと工程表の列が同じであることを確かめる（I11）。"""
    out = []
    for mode, snap in snaps.items():
        want = columns.get(mode)
        have = snap.stage_names
        if want is None:
            out.append(_v(mode, f"工程表に {mode} の列が無い"))
            continue
        for name in [n for n in want if n not in have]:
            out.append(_v(mode, f"工程表の {name} が定義に無い"))
        for name in [n for n in have if n not in want]:
            out.append(_v(mode, f"定義の {name} が工程表に無い"))
        common_want = [n for n in want if n in have]
        common_have = [n for n in have if n in want]
        if common_want != common_have:
            out.append(_v(mode, f"工程の順が工程表と違う（定義: {' → '.join(common_have)}）"))
    return out


def _v(name: str, detail: str, path: str = WORKFLOW) -> dict:
    return {"kind": "workflow-def", "path": path, "name": name, "result": "violation", "detail": detail}


def check_workflow_defs(root: Path) -> list[dict]:
    """5 つのモードの定義を読み、検査と工程表との突き合わせを通す。指摘の一覧を返す。"""
    try:
        wd = _reader(root)
    except (OSError, ImportError) as e:
        return [_v("reader", f"{READER} を読めない（{e}）", READER)]
    out: list[dict] = []
    base = root / WORKFLOW
    try:
        raw = json.loads((base / wd.DSL_SCHEMA).read_text(encoding="utf-8"))
        if f"sha256={wd.body_sha256(raw)}" not in raw.get("$comment", ""):
            out.append(_v(wd.DSL_SCHEMA, "借りたスキーマの本文の sha256 が $comment と違う（取得元から差し替える）"))
    except (OSError, json.JSONDecodeError) as e:
        out.append(_v(wd.DSL_SCHEMA, f"読めない（{e}）"))
    snaps = {}
    for mode in wd.MODES:
        try:
            snaps[mode] = wd.load_definition(mode, base)
        except wd.WorkflowDefError as e:
            out += [_v(mode, err, f"{WORKFLOW}/modes/{mode}.json") for err in e.errors]
    try:
        text = (root / SKILL_MD).read_text(encoding="utf-8")
    except OSError as e:
        return out + [_v("工程表", f"{SKILL_MD} を読めない（{e}）", SKILL_MD)]
    columns = stage_columns(text, _md(root))
    if not columns:
        return out + [_v("工程表", f"「{TABLE_SECTION}」の表が無い", SKILL_MD)]
    return out + check_table_match(snaps, columns)
