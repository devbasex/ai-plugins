"""upkeep_rank_inputs.py: rank の入力（--scores の見積・記録・前回の表）から課題ごとの見積を採る。GitHub を呼ばない。

upkeep_rank_cmd.py から分けた（ファイルの行数の上限。#2315）。
"""

from __future__ import annotations

from pathlib import Path

import upkeep_rank as R
from step_result import EXIT_UNREADABLE, StepError


def read_scores(path, read_state) -> dict:
    if not path:
        return {}
    got = read_state(Path(path))
    if got is None:
        raise StepError(f"見積の入力が無い: {path}", EXIT_UNREADABLE)
    if not isinstance(got, dict) or not isinstance(got.get("issues", []), list):
        raise StepError('見積の入力は {"version": 1, "issues": [...]} の形で書く', EXIT_UNREADABLE)
    ms = got.get("milestones")
    if ms is not None and (not isinstance(ms, list) or not all(isinstance(t, str) for t in ms)):
        raise StepError("見積の入力の milestones は題名の列で書く", EXIT_UNREADABLE)
    return got


def estimates(issues, scores, stored, previous, cfg, since=None):
    """open の課題ごとの見積。--scores → 記録 → 前回の表 の順に採る。どれにも無い・検証に落ちた課題は外す（I1・I10）。

    `since` を渡すと、どれにも無い課題のうちその時刻以前に作られたものは外さず、3 つ目の戻り値へ番号を返す
    （差分の棚卸しは起点より後に作られた課題にだけ段階を付ける。#2315 の決定 1・決定 6）。"""
    given = {i.get("number"): i for i in scores.get("issues", []) if isinstance(i, dict)}
    prev_rows = {}
    for tab in previous.values():
        for r in tab["rows"]:
            prev_rows.setdefault(r["number"], r)
    ests, excluded, left = {}, [], []
    for n in sorted(issues):
        if n in given:
            raw, band = given[n], True
            if not raw.get("digest"):
                excluded.append({"number": n, "reason": "digest が無い（candidates の値を写す）"})
                continue
        elif str(n) in stored:
            raw = stored[str(n)]
            band = not raw.get("restored") and (raw.get("ubv") or {}).get("impact") is not None
        elif n in prev_rows:
            raw, band = R.restore_from_row(prev_rows[n], cfg), False
        elif since is not None and (issues[n].get("created_at") or "") <= since:
            left.append(n)
            continue
        else:
            excluded.append({"number": n, "reason": "段階が無い（手順 2B で付ける）"})
            continue
        est, why = R.validate_estimate(raw, cfg, check_band=band)
        if est is None:
            excluded.append({"number": n, "reason": why})
        else:
            ests[n] = est
    return ests, excluded, left
