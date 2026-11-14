"""承認ゲート（#3295）: 問いの区分・答えごとの行き先・答えの証拠・MVV 判定の前の機械の検査。

承認ゲートは工程のステップ（`ndf.listen`）で、`ndf progress next` が待ちに入った行を `gate` で返す（#3092 の決定 7）。
このパッケージはその問いと答えの形を持ち、公開の名前はここから引く（副命令の表の `answer`・`progress` もここを指す）。

- 問い・答えごとの行き先・承認資料（`asking`）: 問い（`Question`）は区分（設計・リリース。表 `workflow/progress.json` の
  `gates`）・進行表と工程・問いの記録の id・見る対象（PR の URL と head の SHA・承認資料の sha256）を持つ。行き先（`routes`）は
  答えの語ごとに `ndf answer` のそのまま打てるコマンドを持ち、承認ゲートで止まった結果 JSON は区分・PR の URL・問いの id・
  行き先を機械で読める形で返す（3390-2）。承認資料は未確認の点（#2444）と出す版（#2554）を持つ
- 答え（`answering`。`ndf answer`）: 問いの id・結果・差し戻しの指摘・答えた者・冪等の鍵・見た対象を受け、事象 `answered` に残す。
  答えを読む前に今の対象と照らし、違えば待ちへ戻す（`recheck`。3364-2）。承認の後に加わった範囲が文書だけなら、リリース
  承認ゲートを出し直さない（#3020）。外した課題はレーンの順番表の次の行へ入れ（決定 99）、スプリントの完了と同じトランザクションで
  レーンの行を終える（`lane_env`。決定 17）
- レーンのリリースの依頼（`release`）: リリース承認ゲートの待ちで依頼を出し、依頼への答えを承認ゲートの答えにする
- 答えの記録（`records`）: 答えを `gate-rejections.jsonl` へ写し（#3136）、conductor が読む答えの一覧（#2934・#3174）と
  承認で見た head（#2147）を返す
- MVV 判定の前の機械の検査（`checks`）: 5 つの検査と、承認ゲートを省けるかの判定（#3017）・判定の記録（#2950）
- 副命令のエントリポイント（`entry`）: `ndf answer` と `ndf progress`
"""

from __future__ import annotations

from ndf.progress.gate.answering import (
    ACTORS,
    DocsOnly,
    Observe,
    answer,
    git_docs_only,
    github_observe,
    lane_env,
    never_docs_only,
    no_observe,
    recheck,
)
from ndf.progress.gate.asking import (
    MATERIAL_DIR,
    REJECTING,
    RELEASE_KIND,
    TOOL,
    Answer,
    Question,
    Target,
    describe,
    gate_result,
    material,
    material_path,
    material_sha256,
    question,
    question_id,
    request_id,
    routes,
    unconfirmed,
    waiting,
    write_material,
)
from ndf.progress.gate.checks import (
    CHECKS,
    Changed,
    MvvFacts,
    check_approval_record,
    check_boundary,
    check_mode,
    check_mvv_decl,
    check_mvv_hash,
    may_skip,
    mvv_decls,
    note,
    refusals,
    well_formed,
)
from ndf.progress.gate.entry import WORK_KINDS, _cmd_work, ask, main, present, progress_verbs, work_argv
from ndf.progress.gate.records import answers, approved_head, record_answer, rejection_row, rejections, rejections_path
from ndf.progress.gate.release import request_release, take_reply

__all__ = [
    "ACTORS",
    "CHECKS",
    "MATERIAL_DIR",
    "REJECTING",
    "RELEASE_KIND",
    "TOOL",
    "WORK_KINDS",
    "Answer",
    "Changed",
    "DocsOnly",
    "MvvFacts",
    "Observe",
    "Question",
    "Target",
    "_cmd_work",
    "answer",
    "answers",
    "approved_head",
    "ask",
    "check_approval_record",
    "check_boundary",
    "check_mode",
    "check_mvv_decl",
    "check_mvv_hash",
    "describe",
    "gate_result",
    "git_docs_only",
    "github_observe",
    "lane_env",
    "main",
    "material",
    "material_path",
    "material_sha256",
    "may_skip",
    "mvv_decls",
    "never_docs_only",
    "no_observe",
    "note",
    "present",
    "progress_verbs",
    "question",
    "question_id",
    "recheck",
    "record_answer",
    "refusals",
    "rejection_row",
    "rejections",
    "rejections_path",
    "request_id",
    "request_release",
    "routes",
    "take_reply",
    "unconfirmed",
    "waiting",
    "well_formed",
    "work_argv",
    "write_material",
]
