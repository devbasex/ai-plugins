"""PR 本文の ndf-next の検査の印（規則 I2・port。#3343）を pr のステップで扱う（#3529）。

印の行は conductor が足すもので、pr のステップがプランの値から組み直す本文には無い。書き直すたびに今の本文から
引き継ぎ（`carry_marks`）、印が要るのに無ければ PR を出した後に止めて気づかせる（`missing_marks`）。印と判定の決まりは検査の
規則（`RULES` の `invariants`）が持ち、`plugins/ndf-next/src/ndf/checks/invariants.py` の `old_plugin_diff` と同じに読む。
規則のファイルが無いリポジトリでは何もしない。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# 検査の規則（リポジトリの根から）。親の親の親が新しいプラグインの根
RULES = Path("plugins/ndf-next/src/ndf/checks/rules.json")
NEW_PLUGIN = RULES.parents[3].as_posix()
KEYS = ("old_plugin", "immediate_fix_mark", "port_mark", "no_port_mark")
MARKS = KEYS[1:]
ISSUE_REF = re.compile(r"#\d+")


def marks_conf(cwd) -> dict | None:
    """規則の `invariants`（印と旧いプラグインのパス）。ファイルが無い・読めない・欠けていれば None。"""
    try:
        got = json.loads((Path(cwd) / RULES).read_text(encoding="utf-8")).get("invariants")
    except (OSError, ValueError, AttributeError):
        return None
    ok = isinstance(got, dict) and all(isinstance(got.get(k), str) and got[k] for k in KEYS)
    return got if ok else None


def mark_lines(body: str, c: dict) -> list[str]:
    """本文の印の行（前後の空白を落とし、重ねない）。"""
    marks = tuple(c[k] for k in MARKS)
    return list(dict.fromkeys(l.strip() for l in (body or "").splitlines() if l.strip().startswith(marks)))


def carry_marks(body: str, old: str, c: dict, footer: str) -> str:
    """今の本文 `old` の印の行のうち `body` に無いものを、署名 `footer` の前へ足す。"""
    have = set(mark_lines(body, c))
    extra = [l for l in mark_lines(old, c) if l not in have]
    if not extra:
        return body
    at = body.rfind(footer)
    head, tail = (body[:at], body[at:]) if at >= 0 else (body, "")
    return head.rstrip() + "\n\n" + "\n\n".join(extra) + "\n\n" + (tail or "")


def _mark_reason(body: str, mark: str) -> str:
    return next((r for r in (l[len(mark) :].strip() for l in body.splitlines() if l.strip().startswith(mark)) if r), "")


def missing_marks(body: str, c: dict, files: list[str]) -> list[tuple[str, str]]:
    """差分 `files` に対して本文に足りない印（(規則, 書く行の形)）。検査の I2・port と同じ条件で数える。"""
    body = "\n".join(l.strip() for l in (body or "").splitlines())
    old = [f for f in files if f.startswith(c["old_plugin"] + "/")]
    if not old:
        return []
    out = []
    if not (ISSUE_REF.search(_mark_reason(body, c["port_mark"])) or _mark_reason(body, c["no_port_mark"])):
        out.append(("port", f"「{c['port_mark']} #<Issue 番号>」か「{c['no_port_mark']} <理由>」"))
    if any(f.startswith(NEW_PLUGIN + "/") for f in files) and not _mark_reason(body, c["immediate_fix_mark"]):
        out.append(("I2", f"「{c['immediate_fix_mark']} <理由>」"))
    return out


def marked_diff(cwd, base: str, c: dict) -> list[str]:
    """起点 `origin/<base>` との merge-base からの差分のうち、旧いプラグインと新しいプラグインの下のファイル。"""
    p = subprocess.run(
        ["git", "diff", "--name-only", f"origin/{base}...HEAD", "--", c["old_plugin"] + "/", NEW_PLUGIN + "/"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return [l for l in p.stdout.splitlines() if l] if p.returncode == 0 else []
