"""スプリントの設計のプランのステップのうち、書く・設計 PR を出す・設計承認ゲートへ渡す部分（`sprint_waves.plan_sprint_design` が並べる）。"""

from __future__ import annotations

import functools
import shlex
import subprocess

from supervise_lib.decl import DeclError
from supervise_lib.paths import DESIGN_READY, DESIGN_SURVEY_PY, REVIEW_FOCUS, SELF, SPEC_COPY_PY, SYNC_DESIGN_BODY, WORKFLOW_COMMON
from supervise_lib.prompts import DESIGN_FIX_PROMPT, DESIGN_REVIEW_PROMPT

DESIGN_GLOSSARY_NOTE = "{state_dir}/work/glossary-candidates.md"  # 設計のプランが起こした用語集の候補の語
# 設計単体のレビューの結果（#3144 の決定 11）。4 段と pr の段の append がここから引く。コミットしない
DESIGN_REVIEW_NOTE = "{state_dir}/work/design-review.md"
DETAIL_STAGE = "詳細設計"


@functools.lru_cache(maxsize=None)
def _stage_class(lib: str, mode: str, stage: str) -> str:
    """`wf_stage_class <mode> <stage>` の区分（R / C / -）。引けなければ `DeclError`。"""
    try:
        p = subprocess.run(
            ["bash", "-c", 'source "$1" && wf_stage_class "$2" "$3"', "_", lib, mode, stage],
            capture_output=True, text=True,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError) as exc:
        raise DeclError(f"工程の分類を読めない: {exc}") from None
    got = p.stdout.strip()
    if p.returncode != 0 or got not in ("R", "C", "-"):
        why = (p.stderr.strip() or f"モード {mode} の工程「{stage}」が無い")[:300]
        raise DeclError(f"工程の分類を読めない: {why}（{lib}）")
    return got


def detail_design_runs(mode: str) -> bool:
    """工程の分類でモード `mode` の「詳細設計」が必須（R）か。モードを定数に写さず `WF_STAGE_MATRIX` から引く（#3144 の決定 5）。"""
    return _stage_class(str(WORKFLOW_COMMON), mode, DETAIL_STAGE) == "R"


def detail_steps(n: int) -> list[dict]:
    """設計単体のレビュー → 直し（指摘があれば）→ 詳細設計の 4 段（#3144 の決定 4）。最後の段は rebuild へ進む。"""
    detail = f"issues/issue-{n}-design-detail.md"
    return [
        # 設計文書だけを 1 つの AI がレビューする。cross-review にしない（Pull Request をもう 1 組出さない。決定 11）
        {
            "id": "design-review",
            "type": "work",
            "kind": "設計のレビュー",
            "stage": "設計",
            "timeout": 1800,
            "prompt": DESIGN_REVIEW_PROMPT.format(n=n, focus=REVIEW_FOCUS),
            "next": "design-review-check",
        },
        # 「- 無し」の 1 行なら 0 で詳細設計へ、指摘の表だけなら 1 で直しへ、ファイルが無ければ 2（run の失敗の扱い）
        {
            "id": "design-review-check",
            "type": "run",
            "stage": "設計",
            "timeout": 60,
            "cmd": f"grep -qx -- '- 無し' {DESIGN_REVIEW_NOTE}",
            "on_exit": {"1": "design-fix"},
            "next": "detail-design",
        },
        # 直した後にレビューを打ち直さない。直した設計は後の cross-review が詳細設計と合わせて見る
        {
            "id": "design-fix",
            "type": "work",
            "kind": "修正",
            "stage": "設計",
            "timeout": 1800,
            "prompt": DESIGN_FIX_PROMPT.format(n=n),
            "next": "detail-design",
        },
        {
            "id": "detail-design",
            "type": "work",
            "full": True,
            "kind": DETAIL_STAGE,
            "stage": DETAIL_STAGE,
            "issues": True,
            "timeout": 3600,
            "prompt": f"/ndf:design #{n} 詳細設計。設計文書（直した後のもの）の上に、詳細設計の文書 {detail} を 1,000 行以下で"
            "書く（超える主題は設計を 2 本に分けると報告して止まる）。書いたら "
            f"`{DESIGN_SURVEY_PY} check --issue {n} --require-detail` を打ち、detail_* の指摘を直す。工程「設計」は記録し直さない。"
            "コミットする（push しない）。",
            "next": "rebuild",
        },
    ]


def writing_steps(a, n: int, copy: str) -> list[dict]:
    """用語集の入口の検査・要求・設計文書を書くステップ。詳細設計を通るモードでは `detail_steps` を続ける。"""
    detail = detail_design_runs(a.mode)
    design_note = "詳細設計は後の段が書くため、ここでは書かない（設計文書の突き合わせで止める）。" if detail else ""
    steps = [
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
            f"{design_note}コミットする（push しない）。",
            "next": "design-review" if detail else "rebuild",
        },
    ]
    return steps + detail_steps(n) if detail else steps


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
            "append": [DESIGN_GLOSSARY_NOTE, DESIGN_REVIEW_NOTE],  # 設計単体のレビューの結果（無いモードでは足さない）
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
