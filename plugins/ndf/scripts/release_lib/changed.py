"""`release-steps.py changed-plugins` の読み書き（#1752）。上げ幅の規則と承認資料の節の形は `others.py` が持ち、
ここは PR・課題・マージのコミットの読み取りと承認資料のファイルの読み書きを持つ。出す（emit）のは呼び手である。"""

from __future__ import annotations

import json
from pathlib import Path

import gh_parts
from release_lib import others
from step_result import StepError, git, result


def issue_text(root, n):
    """課題の本文と読めない理由（読めれば None）。"""
    r = gh_parts.view_json("issue", n, "body", cwd=str(root))
    if r.returncode:
        return None, r.stderr.strip()[:200] or f"gh issue view {n} が失敗"
    try:
        return (json.loads(r.stdout or "null") or {}).get("body") or "", None
    except (ValueError, AttributeError):
        return None, "出力を読めない"


def merge_files(root, oid):
    """マージのコミットの第 1 親との差分のパス。読めなければ None。"""
    p = git(root, "diff", "--name-only", f"{oid}^1", oid, check=False)
    return None if p.returncode else p.stdout.split()


def candidate_rows(root, prs, pending, view_pr, skipped):
    """版に含む PR の材料から、差分にあってまだ上げていないプラグインごとに上げ幅の候補を出す。"""
    mats = others.gather_prs(prs, view_pr, lambda n: issue_text(root, n), lambda oid: merge_files(root, oid), skipped)
    try:
        return [others.candidate(n, old, mats) for n, old in pending.items()]
    except ValueError as e:
        raise StepError(f"版を読めない: {e}", 2)


def approval_path(root, value):
    path = Path(value)
    return path if path.is_absolute() else root / path


def read_approval(path):
    if not path.is_file():
        raise StepError(f"承認資料 {path} が無い（先に approval-facts を走らせる）")
    return path.read_text(encoding="utf-8").split("\n")


def decided_rows(path, pending, already=()):
    """承認資料の表の行のうち本番で上げるもの。読めない・合わないなら 1 で止める（PATCH へ倒さない。I1・I2）。
    already（前のタグから版が変わった行）の HEAD の版が表の「上げた後の版」と同じ行は、上げ終えたものとして外す。"""
    try:
        table = others.read_section("\n".join(read_approval(path)))
        tos = others.decided_versions(table, pending, {r.name: r.to for r in already})
    except ValueError as e:
        raise StepError(f"承認資料の上げ幅を読めない: {e}")
    return [r for r in table if r.name in tos]


def write_others(path, lines, rows):
    """承認資料の「版を上げる他のプラグイン」の節を書き、上げる行があれば同意の行を足す。"""
    others.put_section(lines, others.OTHERS_HEADING, others.section_lines(rows))
    if any(r.level != others.ALREADY for r in rows):
        others.add_consent(lines)
    path.write_text("\n".join(lines), encoding="utf-8")


def plugin_items(rows):
    return [
        {"kind": "plugin", "name": r.name, "result": "bump", "from": r.current, "to": r.to, "level": r.level.lower(), "basis": r.basis}
        for r in rows
    ]


def set_levels(tool, path, sets):
    """承認ゲート 2 で決めた上げ幅（`<名前>=<上げ幅>`）で承認資料の表の行を書き直し、結果を返す。"""
    lines = read_approval(path)
    try:
        rows = others.read_section("\n".join(lines))
        done = [others.set_level(rows, *(s.split("=", 1) if "=" in s else (s, ""))) for s in sets]
    except ValueError as e:
        raise StepError(str(e), 2)
    write_others(path, lines, rows)
    return result(tool, "ok", f"承認資料の上げ幅を {len(done)} 件書き直した", plugin_items(done), {"approval": str(path)})
