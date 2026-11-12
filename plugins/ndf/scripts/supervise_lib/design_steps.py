"""スプリントの設計のプランのステップのうち、書く・設計 PR を出す・設計承認ゲートへ渡す部分（`sprint_waves.plan_sprint_design` が並べる）。"""

from __future__ import annotations

import shlex

from supervise_lib.paths import DESIGN_READY, SELF, SPEC_COPY_PY, SYNC_DESIGN_BODY

DESIGN_GLOSSARY_NOTE = "{state_dir}/work/glossary-candidates.md"  # 設計のプランが起こした用語集の候補の語


def writing_steps(a, n: int, copy: str) -> list[dict]:
    """用語集の入口の検査・要求・設計文書を書くステップ。"""
    return [
        # 設計の工程の入口の検査（#1111 の I5）。用語集が無ければ worktree の中で起こし、設計 PR と一緒に
        # コミットする（承認ゲートを増やさない。語の採否は設計承認ゲートで利用者が見る）。それ以外の失敗は止まる
        {
            "id": "glossary",
            "type": "run",
            "stage": "設計",
            "timeout": 300,
            "cmd": f"python3 {shlex.quote(str(SELF))} design-glossary --mode {shlex.quote(a.mode)} --root . --out {DESIGN_GLOSSARY_NOTE}",
            "next": "requirements-check",
        },
        # 要求が課題の本文にあり、写しが一致すれば要求のステップを飛ばす
        {
            "id": "requirements-check",
            "type": "run",
            "stage": "要求と受け入れ条件",
            "timeout": 120,
            "cmd": f"{SPEC_COPY_PY} check {n} {copy}",
            "on_fail": "requirements",
            "next": "design",
        },
        {
            "id": "requirements",
            "type": "work",
            "full": True,
            "kind": "要求",
            "stage": "要求と受け入れ条件",
            "issues": True,
            "timeout": 3600,
            "next": "design",
            "prompt": f"/ndf:requirements-design #{n}。人へ問わずに進め、決められない点は前提か未決として課題の本文へ"
            f"書く。本文を書いたら `spec-copy.py write {n} {copy}` で写しを作り、コミットする（push しない）。",
        },
        {
            "id": "design",
            "type": "work",
            "full": True,
            "kind": "設計",
            "stage": "設計",
            "issues": True,
            "timeout": 3600,
            "prompt": f"/ndf:design #{n}。設計文書は 1,000 行以下にする（超える主題は設計を 2 本に分けると報告する）。"
            "コミットする（push しない）。",
            "next": "rebuild",
        },
    ]


def pr_steps(a, n: int, doc: str) -> list[dict]:
    """設計 PR を出し、本文の「決めたこと」を確かめるステップ。"""
    return [
        # glossary のステップが用語集を起こしたときは、候補の語を PR 本文へ載せる（設計承認ゲートの材料）
        {
            "id": "pr",
            "type": "pr",
            "stage": "ドキュメントレビュー",
            "base": a.base,
            "title_doc": doc,
            "title": f"設計: #{n}",  # H1 を読めないときの題
            "summary": f"#{n} の設計（スプリント {a.name}）",
            "append": [DESIGN_GLOSSARY_NOTE],
            # 本文の「決めたこと」を作る時点で入れる。作った後に書き直すと CI を 2 度起動する
            "decisions": True,
            "next": "sync-body",
        },
        # 本文の「決めたこと」を review の前に確かめる。無いと CI の pr-body-decisions が落ち、cross-review が
        # コード関連の CI 失敗として round 1 で中断する（m1483 の PR 1499・1500）。pr のステップが揃えていれば
        # 書き込まない
        {
            "id": "sync-body",
            "type": "run",
            "stage": "ドキュメントレビュー",
            "timeout": 120,
            "cmd": SYNC_DESIGN_BODY,
            "next": "review",
        },
    ]


def gate_steps() -> list[dict]:
    """Draft を外して設計承認ゲートへ渡すステップ。"""
    return [
        # pr のステップは設計 PR を Draft で作る（レビュー中に CI と並べる）。設計承認ゲートの前に Draft を外す。
        # push は直前の push-glossary が済ませ、落ちればプランが止まってここへ来ない
        {"id": "ready", "type": "run", "stage": "ドキュメントレビュー", "timeout": 300, "cmd": DESIGN_READY, "next": "gate"},
        {
            "id": "gate",
            "type": "judge",
            "inputs": ["review", "glossary-recheck"],
            "question": "設計承認ゲート（設計 Pull Request のマージ）へ渡す（gate）か、止める（stop）か。glossary-recheck に"
            "用語チェックの当たりが残っていれば、設計承認ゲートの提示に載せる",
            "choices": ["gate", "stop"],
        },
    ]
