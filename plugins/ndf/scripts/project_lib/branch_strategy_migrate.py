"""branch_strategy_migrate.py: 旧い 4 か所の宣言をセクション `branch_strategy` へ移す計画を作る（#3672 の決定 10・E6）。

移す行の表（`data/ndf-settings-renames.json`）のとおりに値を移し、旧いキーを消す差分を作る。旧いキーを読むのは、
このモジュールと旧い宣言の検査（`lib/branch_strategy_legacy.py`）だけである（I8）。

旧い宣言に無いキー（承認の受け方・マージの主体と方式・版数の置き場・CD など）は、取り込み方に合う代表のリポジトリの
種類の既定の行から埋め、種類は `custom` にする。CD は旧い `delivery` の行のうち本番チャネルで Release を作る行の
ワークフローから、版数の置き場はリリースの形から決める。移した後のセクションが組み合わせの規則に落ちれば書かない。
"""

from __future__ import annotations

import copy
from pathlib import Path

import branch_strategy as bs
import branch_strategy_legacy as legacy
from project_lib import branch_strategy_write as bw

# 取り込み方 → 足りないキーを埋める既定の行
FILL_KIND = {"merge_base_branch": "web-develop", "release_branch": "mobile", "trunk": "web-trunk"}
FILL_KIND_BY_FORM = {"package-plugin": "plugin", "document": "docs"}


def _section_rel(pointer: str) -> str:
    return pointer[len("/" + bs.SECTION) :]


def _cd_of(deliveries) -> dict | None:
    for d in deliveries or []:
        wf = (d.get("result") or {}).get("workflow") if isinstance(d, dict) else None
        if wf and d.get("publishes_release") and d.get("channel") in (None, "production"):
            return {"workflow": wf}
    return None


def _fill(section: dict, presets: dict, existing: dict | None = None) -> dict:
    """移した値に、足りないキーを埋める。`existing` は途中で止まった移行が先に書いたセクション（打ち直しで値を落とさない）。"""
    rs = section.get("release_strategy") or (existing or {}).get("release_strategy", "merge_base_branch")
    form = (section.get("release") or {}).get("form") or ((existing or {}).get("release") or {}).get("form")
    kind = FILL_KIND_BY_FORM.get(form, FILL_KIND[rs]) if rs == "trunk" else FILL_KIND[rs]
    row = presets["kinds"][kind]
    out = copy.deepcopy(existing) if isinstance(existing, dict) and existing else copy.deepcopy(row["section"])
    out["release_strategy"] = rs
    out["kind"] = "custom"
    for key, value in section.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(value)
        else:
            out[key] = value
    branches = out["branches"]
    if rs == "trunk":
        branches["base"] = branches["production"]
    rel = out["release"]
    if form == "package-plugin" and "version_store" not in (section.get("release") or {}) and not existing:
        rel["version_store"] = {"kind": "plugin"}
    files = rel.pop("_version_files", None)
    if files and rel["version_store"]["kind"] != "none":
        rel["version_store"]["files"] = files
    rel["cd"] = _cd_of(rel.get("deliveries"))
    with_cd = presets["kinds"][FILL_KIND_BY_FORM.get(form, kind)]["with_cd"]
    rel["tag"], rel["github_release"] = (with_cd["tag"], with_cd["github_release"]) if rel["cd"] else (None, False)
    if rel["version_store"]["kind"] == "none":
        rel["tag"], rel["github_release"] = None, False
    dev = out.pop("_dev_workflow", None)
    if dev is None and existing:
        dev = next((c.get("workflow") for c in existing.get("channels", []) if c.get("name") == "dev"), None)
    out["channels"] = [{"name": "stable", "ref": branches["production"]}]
    if dev:
        out["channels"].append({"name": "dev", "ref": branches["base"], "workflow": dev})
    if out["merge"].get("admin"):
        out["merge"]["method"] = "direct"
    return out


def plan_migration(root: Path, moves: list[legacy.Move]) -> tuple[bw.WritePlan, list[dict]]:
    """旧い 4 か所の値をセクションへ移し、旧いキーを消す計画と、表に無い値への問いを返す。"""
    ndf = Path(root) / ".ndf"
    docs = {fid: bw._read_decl(ndf / name) for fid, name in legacy.FILES.items()}
    docs = {k: v for k, v in docs.items() if v is not None}
    found = {(f.file, f.pointer) for f in legacy.find_legacy(docs, moves)}
    present = [m for m in moves if m.file in docs and legacy.has_pointer(docs[m.file], m.old)]
    if not present:
        return bw.WritePlan(digest=bs.strategy_digest({"diff": [], "before": {}}), rebuild=lambda: plan_migration(root, moves)[0]), []
    after = {fid: copy.deepcopy(doc) for fid, doc in docs.items()}
    project = after.setdefault("project", {"version": 1})
    moved: dict = {}
    questions = []
    for m in present:
        if m.new is None:
            continue
        value = bw._get(docs[m.file], m.old)
        if m.values:
            if value not in m.values:
                questions.append({"pointer": _section_rel(m.new), "why": "insufficient", "candidates": sorted(set(m.values.values())),
                                  "evidence": [{"source": "repo", "ref": f".ndf/{legacy.FILES[m.file]}:{m.old}", "value": str(value)}]})
                continue
            value = m.values[value]
        if m.transform == "dev_channel":
            moved["_dev_workflow"] = value
        elif m.transform == "version_files":
            moved.setdefault("release", {})["_version_files"] = [{"path": p} for p in value or []]
        else:
            bw._set(moved, _section_rel(m.new), copy.deepcopy(value))
    if questions:
        return bw.WritePlan(), questions
    for m in sorted(present, key=lambda m: -len(m.old)):
        _remove_pointer(after[m.file], m.old)
    if not any(m.new is not None for m in present) and bs.SECTION in project:
        # remove の行だけが当たった（使わなくなったキー）。セクションは今のまま
        names = {legacy.FILES[fid]: doc for fid, doc in after.items()}
        return bw.plan_files(root, names, lambda: plan_migration(root, moves)[0]), []
    section = _fill(moved, bw.load_presets(), project.get(bs.SECTION))
    failed = bs.validate_section(section)
    if failed:
        where = [{**f, "legacy": sorted(f"{a}:{b}" for a, b in found)} for f in failed]
        return bw.WritePlan(failed=where), []
    project[bs.SECTION] = section
    names = {legacy.FILES[fid]: doc for fid, doc in after.items()}
    return bw.plan_files(root, names, lambda: plan_migration(root, moves)[0]), []


def _remove_pointer(doc: dict, pointer: str) -> None:
    toks = [t.replace("~1", "/").replace("~0", "~") for t in pointer.split("/")[1:]]
    cur = doc
    for t in toks[:-1]:
        if not isinstance(cur, dict) or t not in cur:
            return
        cur = cur[t]
    if isinstance(cur, dict):
        cur.pop(toks[-1], None)
