"""役割の印（作業ディレクトリの役割のファイル）の書込と掃除（`relay.py role` の中身。hub_client.cmd_role から呼ぶ）。"""

from __future__ import annotations

import os

from . import hub as hb
from .common import remove, stamp, write_json_atomic


def _clear_role(relay_dir: str | None, path: str | None, plan_dir: str | None) -> int:
    """`plan_dir` が一致するオーガナイザーの印を消し、ラッパーの中なら自分の印も消す。他から消した数を返す。

    自分の印は、`plan_dir` の指定が無いか、印が `plan_dir` を持たない旧い形のときだけ消す。"""
    cleared = _clear_organizers(plan_dir) if plan_dir else 0
    if path and (not plan_dir or "plan_dir" not in (hb.read_role(relay_dir) or {})):  # 自分の旧い形の印も消す
        remove(path)
    return cleared


def _write_role(relay_dir: str, path: str, role: str, plan: str, plan_dir: str | None) -> int:
    """自分の役割の印を書き、オーガナイザーなら同じ `plan_dir` の他の印を消す。消した数を返す。"""
    write_json_atomic(path, {"role": role, "plan": plan, "at": stamp(), **({"plan_dir": plan_dir} if plan_dir else {})})
    return _clear_organizers(plan_dir, keep=relay_dir) if plan_dir and role == "organizer" else 0


def _clear_organizers(plan_dir: str, keep: str | None = None) -> int:
    """`plan_dir` が一致するオーガナイザーの印を、`keep` の作業ディレクトリを除いて消す。消した数を返す。

    `plan_dir` を持たない印は、どのリポジトリの計画か分からないため消さない（決定 13）。"""
    n = 0
    for w in hb.scan_wrappers():
        row = hb.read_role(w.dir) or {}
        if row.get("role") == "organizer" and row.get("plan_dir") == plan_dir and not (keep and os.path.samefile(w.dir, keep)):
            remove(os.path.join(w.dir, hb.ROLE_FILE))
            n += 1
    return n
