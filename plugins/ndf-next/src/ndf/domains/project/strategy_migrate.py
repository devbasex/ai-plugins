"""旧い 4 か所の宣言をセクション `branch_strategy` へ移す計画を作る（#3672 の決定 10・E6。#3681）。

移す行の表（`contracts/data/ndf-settings-renames.json`）のとおりに値を移し、旧いキーを消す差分を作る。旧いキーの名前は
コードに書かず、表だけを読む。旧いキーを読むのは、このモジュールと旧い宣言の検査（`core/branch_strategy_legacy.py`）
だけである（I8）。

旧い宣言に無いキー（承認の受け方・マージの主体と方式・版数の置き場・CD など）は、取り込み方に合う代表のリポジトリの
種類の既定の行から埋め、種類は `custom` にする。CD は旧いリリースの経路の行のうち本番チャネルで Release を作る行のワークフローから、
版数の置き場はリリースの形から決める。移した後のセクションが組み合わせの規則に落ちれば書かない。
10 系の `project_lib/branch_strategy_migrate.py` と同じ責務を 11.0 の置き場で持つ。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Optional

from ndf.core import branch_strategy as bs
from ndf.core import branch_strategy_legacy as legacy
from ndf.core import json_pointer
from ndf.domains.project import strategy_write as bw
from ndf.domains.project.items import obj, seq
from ndf.domains.project.strategy_measure import default_branch

# 取り込み方 → 足りないキーを埋める既定の行
FILL_KIND = {"merge_base_branch": "web-develop", "release_branch": "mobile", "trunk": "web-trunk"}
FILL_KIND_BY_FORM = {"package-plugin": "plugin", "document": "docs"}


def _section_rel(pointer: str) -> str:
    return pointer[len("/" + bs.SECTION) :]


def _cd_of(deliveries: Any) -> Optional[dict]:
    for d in seq(deliveries):
        wf = obj(obj(d).get("result")).get("workflow")
        if wf and d.get("publishes_release") and d.get("channel") in (None, "production"):
            return {"workflow": wf}
    return None


def _implied_strategy(declared: dict, default: Optional[str]) -> str:
    """旧い宣言に取り込み方が無いときの取り込み方。省いたブランチ名を既定ブランチで補った結果、ベースブランチと本番ブランチが
    同じなら trunk（旧い処理は既定ブランチで取り込みと本番を兼ねた。merge_base_branch にすると R1 で移行が止まる）。
    両方を書いて同じ名前にした宣言は補っていないため、組み合わせの規則で止める。"""
    if "base" in declared and "production" in declared:
        return "merge_base_branch"
    base = declared["base"] if "base" in declared else default
    production = declared["production"] if "production" in declared else default
    return "trunk" if base and base == production else "merge_base_branch"


def _fill_kind(rs: str, form: Optional[str]) -> str:
    """足りないキーを埋める種類の行。trunk はリリースの形で選び、ほかは取り込み方で選ぶ。"""
    return FILL_KIND_BY_FORM[form] if rs == "trunk" and form in FILL_KIND_BY_FORM else FILL_KIND[rs]


def _fill_release(rel: dict, section: dict, form: Optional[str], kind: str, presets: dict, existing: dict) -> None:
    """版数の置き場・CD・タグ・GitHub Release を決める。"""
    if form == "package-plugin" and "version_store" not in obj(section.get("release")) and not existing:
        rel["version_store"] = {"kind": "plugin"}
    files = rel.pop("_version_files", None)
    if files and rel["version_store"]["kind"] != "none":
        rel["version_store"]["files"] = files
    rel["cd"] = _cd_of(rel.get("deliveries"))
    with_cd = presets["kinds"][FILL_KIND_BY_FORM[form] if form in FILL_KIND_BY_FORM else kind]["with_cd"]
    rel["tag"], rel["github_release"] = (with_cd["tag"], with_cd["github_release"]) if rel["cd"] else (None, False)
    if rel["version_store"]["kind"] == "none":
        rel["tag"], rel["github_release"] = None, False


def _channels(branches: dict, dev: Optional[str]) -> list[dict]:
    """本番チャネルと、開発版のワークフローがあれば開発版チャネル。"""
    channels = [{"name": "stable", "ref": branches["production"]}]
    if dev:
        channels.append({"name": "dev", "ref": branches["base"], "workflow": dev})
    return channels


def _fill(section: dict, presets: dict, existing: Optional[dict] = None, default: Optional[str] = None) -> dict:
    """移した値に、足りないキーを埋める。`existing` は途中で止まった移行が先に書いたセクション（打ち直しで値を落とさない）。

    `default` はリポジトリの既定ブランチ。旧い宣言で省いたベースブランチ・本番ブランチは、旧い処理がこの値で解決して
    いたため、プリセットのブランチ名より先にこれで補う（宣言の書き換えで本番を変えない）。"""
    existing = existing if isinstance(existing, dict) else {}
    declared = {**obj(existing.get("branches")), **obj(section.get("branches"))}
    rs = section.get("release_strategy") or existing.get("release_strategy") or _implied_strategy(declared, default)
    form = obj(section.get("release")).get("form") or obj(existing.get("release")).get("form")
    kind = _fill_kind(rs, form)
    out = copy.deepcopy(existing) if existing else copy.deepcopy(presets["kinds"][kind]["section"])
    out["release_strategy"] = rs
    out["kind"] = "custom"
    for key, value in section.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(value)
        else:
            out[key] = value
    branches = out["branches"]
    for key in ("base", "production"):
        if default and key not in declared:
            branches[key] = default
    if rs == "trunk":
        branches["base"] = branches["production"]
    _fill_release(out["release"], section, form, kind, presets, existing)
    dev = out.pop("_dev_workflow", None)
    if dev is None and existing:
        dev = next((c.get("workflow") for c in seq(existing.get("channels")) if c.get("name") == "dev"), None)
    out["channels"] = _channels(branches, dev)
    if out["merge"].get("admin"):
        out["merge"]["method"] = "direct"
    return out


def _implied_trunk_admin_question(
    moved: dict, existing: Optional[dict], section: dict, docs: dict, present: list[legacy.Move]
) -> Optional[dict]:
    """取り込み方を補って trunk にした結果、管理者の迂回（`merge.admin`）が真なら、宣言を組まずに問う 1 件を返す。
    迂回は `merge.method` を direct にし、trunk は merge-queue か strict だけを受ける（R2）ため、組むと移行が止まる。
    取り込み方を書いた宣言は補っていないため問わず、組み合わせの規則で止める。"""
    if moved.get("release_strategy") or obj(existing).get("release_strategy"):
        return None
    if section["release_strategy"] != "trunk" or section["merge"].get("admin") is not True:
        return None
    evidence = [
        {"source": "repo", "ref": f"{bs.DECL_DIR}/{legacy.FILES[m.file]}:{m.old}", "value": str(json_pointer.get(docs[m.file], m.old))}
        for m in present
        if m.new is not None and _section_rel(m.new) in ("/merge/admin", "/branches/base", "/branches/production")
    ]
    return {
        "pointer": "/release_strategy",
        "why": "conflict",
        # 候補は `migrate --answers` へそのまま渡せる形（セクションの pointer → 値）。`<…>` は答える人が埋める値
        "candidates": [
            {"/release_strategy": "trunk", "/merge/admin": False},
            {"/release_strategy": "merge_base_branch", "/branches/base": "<本番ブランチと別のベースブランチ>"},
        ],
        "evidence": evidence,
    }


def _value_question(m: legacy.Move, value: Any) -> dict:
    """値の対応表に無い旧い値への問い 1 件。"""
    return {
        "pointer": _section_rel(str(m.new)),
        "why": "insufficient",
        "candidates": sorted(set(m.values.values())),
        "evidence": [{"source": "repo", "ref": f"{bs.DECL_DIR}/{legacy.FILES[m.file]}:{m.old}", "value": str(value)}],
    }


def _put_moved(moved: dict, m: legacy.Move, value: Any) -> None:
    """transform に合わせて、移す値を置く。"""
    if m.transform == "dev_channel":
        moved["_dev_workflow"] = value
    elif m.transform == "version_files":
        moved.setdefault("release", {})["_version_files"] = [{"path": p} for p in seq(value)]
    else:
        json_pointer.put(moved, _section_rel(str(m.new)), copy.deepcopy(value))


def _collect_moved(docs: dict, present: list[legacy.Move]) -> tuple[dict, list[dict]]:
    """旧いキーの値を移す先へ集める。値の対応表に無い値は問いにする。"""
    moved: dict = {}
    questions = []
    for m in present:
        if m.new is None:
            continue
        value = json_pointer.get(docs[m.file], m.old)
        if m.values:
            if value not in m.values:
                questions.append(_value_question(m, value))
                continue
            value = m.values[value]
        _put_moved(moved, m, value)
    return moved, questions


def _by_name(after: dict) -> dict:
    """ファイルの ID → 中身を、`.ndf/` の下のファイル名 → 中身へ写す。"""
    return {legacy.FILES[fid]: doc for fid, doc in after.items()}


def _read_docs(root: Path) -> dict:
    """`.ndf/` の 4 ファイルの中身（ファイルの ID → 中身。無いファイルは入れない）。"""
    ndf = Path(root) / bs.DECL_DIR
    docs = {fid: bw.read_decl_file(ndf / name) for fid, name in legacy.FILES.items()}
    return {k: v for k, v in docs.items() if v is not None}


def _strip_legacy(docs: dict, present: list[legacy.Move]) -> dict:
    """旧いキーを消した後の中身（ファイルの ID → 中身）。`project` は無ければ空の宣言で作る。子のキーから先に消す。"""
    after = {fid: copy.deepcopy(doc) for fid, doc in docs.items()}
    after.setdefault("project", {"version": 1})
    for m in sorted(present, key=lambda m: -len(m.old)):
        json_pointer.remove(after[m.file], m.old)
    return after


def _build_section(
    root: Path, moved: dict, existing: Optional[dict], docs: dict, present: list[legacy.Move], found: set
) -> tuple[dict, list[dict], list[dict]]:
    """移した値からセクションを組み、`(セクション, 問い, 落ちた規則)` を返す。問いか落ちた規則があれば書かない。"""
    section = _fill(moved, bw.load_presets(), existing, default_branch(root))
    asked = _implied_trunk_admin_question(moved, existing, section, docs, present)
    if asked:
        return section, [asked], []
    failed = bs.validate(section)
    where = [{**f, "legacy": sorted(f"{a}:{b}" for a, b in found)} for f in failed]
    return section, [], where


def plan_migration(root: Path, moves: list[legacy.Move], answers: Optional[dict] = None) -> tuple[bw.WritePlan, list[dict]]:
    """旧い 4 か所の値をセクションへ移し、旧いキーを消す計画と、表に無い値への問いを返す。

    `answers` は問いへの答え（セクションの pointer → 値。`migrate --answers`）。移した値を上書きし、答えた pointer は問わない。"""
    answers = obj(answers)
    docs = _read_docs(root)

    def rebuild() -> bw.WritePlan:
        return plan_migration(root, moves, answers)[0]

    found = {(f.file, f.pointer) for f in legacy.find_legacy(docs, moves)}
    present = [m for m in moves if m.file in docs and json_pointer.has(docs[m.file], m.old)]
    if not present:
        return bw.WritePlan(digest=bw.EMPTY_DIGEST, rebuild=rebuild), []
    moved, questions = _collect_moved(docs, present)
    questions = [q for q in questions if q["pointer"] not in answers]
    for pointer, value in answers.items():
        json_pointer.put(moved, pointer, copy.deepcopy(value))
    if questions:
        return bw.WritePlan(), questions
    after = _strip_legacy(docs, present)
    project = after["project"]
    if not any(m.new is not None for m in present) and bs.SECTION in project:
        # remove の行だけが当たった（使わなくなったキー）。セクションは今のまま
        return bw.plan_files(root, _by_name(after), rebuild), []
    section, asked, failed = _build_section(root, moved, project.get(bs.SECTION), docs, present, found)
    if asked:
        return bw.WritePlan(), asked
    if failed:
        return bw.WritePlan(failed=failed), []
    project[bs.SECTION] = section
    return bw.plan_files(root, _by_name(after), rebuild), []
