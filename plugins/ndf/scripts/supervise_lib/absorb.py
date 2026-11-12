"""`new sprint --absorb <課題>:<取り込み先>`（#1426 の決定 3・8。I7・I8）。

取り込みは進め方ごとのステージの組み立ての中で扱わず、組み立てた後のプランへ 1 か所で当てる。プランは名前
（`design-<N>`・`impl-<N>`。課題のまとまりでは代表の番号）で引く。取り込んだ課題は設計のプランを持たず、実装のプランは
`取り込み先` と `実行の条件` で飛ぶ（複数件のまとまりにいる課題は、プランを飛ばさずに `課題` から外す）。取り込み先のプランのうち存在するもの（`--design` なら設計と実装、`--issue` なら実装）の
`課題` に取り込んだ課題を含める。取り込むかは引数を渡す人か conductor が決める（AI は選ばない）。
"""

from __future__ import annotations

import argparse
import re

from supervise_lib.procedures import absorbed_condition

PAIR = re.compile(r"^(\d+):(\d+)$")


def absorb_arg(text: str) -> str:
    """argparse の type: `<課題>:<取り込み先>`（整数の組）。形はそのまま返す（起動の形へ写すため）。"""
    if not PAIR.match(text.strip()):
        raise argparse.ArgumentTypeError(f"--absorb は <課題>:<取り込み先> で渡す: {text}")
    return text.strip()


def pairs_of(a) -> list[tuple[int, int]]:
    """(取り込んだ課題, 取り込み先) の並び。"""
    return [(int(m.group(1)), int(m.group(2))) for v in getattr(a, "absorb", None) or [] if (m := PAIR.match(v))]


def absorbed_issues(a) -> list[int]:
    return [m for m, _ in pairs_of(a)]


def absorb_refusal(a) -> str | None:
    """I8: 取り込み先が --design にも --issue にも無い・--issue に無い（実装のプランが無い）・取り込む課題が --design にある・
    同じ番号なら理由を返す。"""
    design, issues = set(a.design or []), set(a.issue or [])
    for m, h in pairs_of(a):
        if m == h:
            return f"--absorb {m}:{h}: 取り込む課題と取り込み先が同じ"
        if m in design:
            return f"--absorb {m}:{h}: 取り込む課題 #{m} が --design にある（設計のプランを持たない）"
        if h not in design and h not in issues:
            return f"--absorb {m}:{h}: 取り込み先 #{h} が --design にも --issue にも無い"
        if h not in issues:  # 実装のプランは --issue の課題だけに作られ、取り込んだ課題の実装は飛ぶ
            return f"--absorb {m}:{h}: 取り込み先 #{h} が --issue に無い（取り込んだ受け入れ内容を実装するプランが無い）"
        if h in absorbed_issues(a):
            return f"--absorb {m}:{h}: 取り込み先 #{h} も取り込む課題になっている"
    return None


def add_absorbed(a) -> None:
    """組み立ての前に、取り込んだ課題を --issue へ足す（実装のプランを書き、スプリント PR の閉じる行に入れる）。"""
    for m, _ in pairs_of(a):
        if m not in a.issue:
            a.issue.append(m)


def _with(issues: list, m: int) -> list:
    return issues + [m] if m not in issues else issues


def include_in_design(design: dict, h: int, m: int, note: str) -> None:
    """設計のプラン（代表 `h`）の `課題` へ `m` を足し、要求と設計の指示へ `note` を足す（取り込みと課題のまとまり。#3081）。"""
    if m in (design.get("課題") or [h]):
        return
    design["課題"] = _with(design.get("課題") or [h], m)
    for step in design.get("steps") or []:
        if step.get("id") in ("requirements", "design"):
            step["prompt"] = f"{step.get('prompt') or ''}{note}"


def bundle_leads(a) -> dict[int, int]:
    """課題 → その課題を実装するプランの番号（課題のまとまりの代表。#3081）。まとまりの記録が無い課題は載らない。"""
    return {n: b.lead() for b in getattr(a, "bundles", None) or [] for n in b.issues}


def apply_absorb(a, waves: list[dict]) -> None:
    """組み立てたステージのプランへ取り込みを書く（I7）。プランは課題を実装するプラン（まとまりなら代表の番号）で引く。
    取り込む課題が複数件のまとまりにいるときは、プランを飛ばさずにその課題だけを `課題` から外す。"""
    plans = {k: p for w in waves for k, p in (w.get("plans") or {}).items()}
    lead = bundle_leads(a)
    for m, h in pairs_of(a):
        to = lead.get(h, h)
        note = f"取り込んだ課題 #{m} の受け入れ内容も #{h} に含める（#{m} の本文も読む）。"
        design = plans.get(f"design-{to}")
        if design is not None:
            include_in_design(design, to, m, note)
        host = plans.get(f"impl-{to}")
        if host is not None:
            host["課題"] = _with(host.get("課題") or [to], m)
        mine = plans.get(f"impl-{lead.get(m, m)}")
        if mine is None or mine is host:
            continue
        rest = [i for i in mine.get("課題") or [m] if i != m]
        if rest:
            mine["課題"] = rest
        else:
            mine["取り込み先"] = h
            mine["実行の条件"] = absorbed_condition(m, h)
