"""スプリントの設計のプランのステップ（入口・PR・レビュー・用語チェック・ゲート）。`sprint_waves` の plan_sprint_design が並べる。"""

from __future__ import annotations

import shlex

from supervise_lib.paths import DESIGN_READY, SELF, SPEC_COPY_PY, SYNC_DESIGN_BODY


DESIGN_GLOSSARY_NOTE = "{state_dir}/work/glossary-candidates.md"  # 設計のプランが起こした用語集の候補の語
RULE_DESIGN = (
    "設計の cross-review は上限 3 ラウンドで打ち切る（収束を待たない）。上限に達して未解決の指摘が 0 件なら、収束と同じ"
    "経路（直しの取り込み → 仕様のコピーの照合 → 用語チェック）を通って gate。未解決が残れば解決のフロー（直し → 再レビュー）を"
    "回し、解決できない（review-settled の判定）か駆動そのものが失敗したら gate。"
)
# 解決のフローの「解決できない」の判定の上限: 直しの成功の回数・直しの費用（ドル）・最初の直しから後の秒（#2388 の決定 3）
RESOLVE_LIMITS = "--max-attempts 2 --max-cost 1.5 --max-seconds 3600"


def review_steps(n: int, copy: str, push: str) -> list:
    """設計のプランのレビューのまとまり。全ペースの設計のプランが共有する（#2388 の決定 4）。cross-review が上限到達
    （終了コード 6）で終わったら review-settled が未解決の件数を読み、0 件なら sync-review、残れば解決のフロー
    （resolve-sync → resolve → push-resolve → review の打ち直し）、解決できなければ ready（承認ゲート 1 の判断）へ回す。"""
    run = {"type": "run", "stage": "ドキュメントレビュー", "timeout": 120}
    sync = "git fetch -q && git merge -q --ff-only '@{u}'"
    settled = f"python3 {shlex.quote(str(SELF))} review-settled --state {{state_dir}}/state.json --step review --fix-step resolve {RESOLVE_LIMITS}"
    fix = "cross-review が上限のラウンドで終わり、未解決の指摘が残った。review の報告（結果の items[0].report）が示す未解決の指摘を直してコミットする（push しない）。直せない指摘は理由を PR のスレッドへ返信する。"
    return [
        # --max-rounds を渡さない。設計の分類の既定（3 ラウンド・前のラウンドからの変更だけ）で回る。駆動の失敗は ready へ回す
        {**run, "id": "review", "type": "drive", "drive": "cross-review", "kind": "ドキュメントレビュー", "timeout": 3600, "args": "{pr}"}
        | {"on_exit": {"6": "review-settled"}, "on_fail": "ready", "next": "sync-review"},
        {
            **run,
            "id": "review-settled",
            "cmd": settled,
            "on_exit": {"1": "resolve-sync", "2": "ready"},
            "on_fail": "ready",
            "next": "sync-review",
        },
        {**run, "id": "resolve-sync", "cmd": sync, "on_fail": "ready", "next": "resolve"},
        {"id": "resolve", "type": "work", "kind": "修正", "stage": run["stage"], "inputs": ["review"], "timeout": 1800, "prompt": fix}
        | {"on_fail": "ready", "next": "push-resolve"},
        {**run, "id": "push-resolve", "timeout": 300, "cmd": push, "on_fail": "ready", "next": "review"},
        # レビューの直しは別の場所から PR のブランチへ push される。追いついてから語を見て push する
        {**run, "id": "sync-review", "cmd": sync, "next": "requirements-recheck"},
        # レビューの直しの後にも写しと課題の本文（正）の一致を見る。写しだけを直した直しを送らずに止める
        # （on_fail を置かない。受け入れ条件の変更は課題の本文を直す利用者の判断で、修正担当の手直しで済ませない。#1769）
        {**run, "id": "requirements-recheck", "cmd": f"{SPEC_COPY_PY} check {n} {copy}", "next": "survey-check"},
    ]


def glossary_steps(glossary_check: str, push: str) -> list:
    """設計のプランの用語チェックのまとまり（glossary-check / fix-glossary / glossary-recheck / push-glossary）。"""
    return [
        # 用語チェック。当たりは 1 回だけ直し、残った当たりは設計承認ゲートの提示へ載せる
        {
            "id": "glossary-check",
            "type": "run",
            "stage": "ドキュメントレビュー",
            "timeout": 120,
            "cmd": glossary_check,
            "on_fail": "fix-glossary",
            "next": "push-glossary",
        },
        {
            "id": "fix-glossary",
            "type": "work",
            "kind": "修正",
            "stage": "ドキュメントレビュー",
            "inputs": ["glossary-check"],
            "timeout": 1800,
            "next": "glossary-recheck",
            "prompt": "用語チェックの当たりを直す。未登録の語は用語集へ足す（source は空にし、pending_source に"
            "設計文書のパスを書く）か、用語集の語へ言い換える。廃止した語は"
            "用語集の語へ言い換える。用語集を変えたら `glossary.py render` で文書を作り直し、コミットする"
            "（push しない）。利用者が採るかを決めるべき語は直さずに「判断が要る」と報告する。",
        },
        {
            "id": "glossary-recheck",
            "type": "run",
            "stage": "ドキュメントレビュー",
            "timeout": 120,
            "cmd": glossary_check,
            "on_fail": "push-glossary",
            "next": "push-glossary",
        },
        {"id": "push-glossary", "type": "run", "stage": "ドキュメントレビュー", "timeout": 300, "cmd": push, "next": "ready"},
    ]


def design_entry_steps(a, n: int, copy: str) -> list[dict]:
    """設計の工程の入口: 用語集の検査・要求の確認・要求・設計のステップ。"""
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


def design_pr_steps(a, n: int, doc: str) -> list[dict]:
    """設計 PR を作り、本文の「決めたこと」を確かめるステップ。"""
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


def design_gate_steps() -> list[dict]:
    """Draft を外し、設計承認ゲートへ渡すかを決めるステップ。"""
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
