"""承認ゲート（#3295）: 質問の区分・回答ごとのルート・回答の証拠・MVV 判定の前の機械の検査。

承認ゲートは工程のステップ（`ndf.listen`）で、`ndf progress next` が待ちに入った行を `gate` で返す（#3092 の決定 7）。
このパッケージはその質問と回答の形を持ち、公開の名前はここから引く（サブコマンドの表の `answer` はここを、`progress` は `progress_cli` を指す）。

- 質問・回答ごとのルート・承認資料（`asking`）: 質問（`Question`）は区分（設計・リリース。表 `workflow/progress.json` の
  `gates`）・ステートマシンと工程・質問の記録の id・見る対象（PR の URL と head の SHA・承認資料の sha256）を持つ。ルート（`routes`）は
  回答の語ごとに `ndf answer` のそのまま打てるコマンドを持ち、承認ゲートで止まった結果 JSON は区分・PR の URL・質問の id・
  ルートを機械で読める形で返す（3390-2）。承認資料は未確認の点（#2444）と出すバージョン（#2554）を持つ
- 回答（`answering`。`ndf answer`）: 質問の id・結果・変更要求の指摘・回答した者・冪等のキー・見た対象を受け、事象 `answered` に残す。
  回答を読む前に今の対象と照らし、違えば待ちへ戻す（`recheck`。3364-2）。承認の後に加わった範囲が文書だけなら、リリース
  承認ゲートを出し直さない（#3020）。外した課題はレーンの順番表の次の行へ入れ（決定 99）、スプリントの完了と同じトランザクションで
  レーンの行を終える（`lane_env`。決定 17）
- レーンのリリースのリクエスト（`release`）: リリース承認ゲートの待ちでリクエストを出し、リクエストへの回答を承認ゲートの回答にする
- 回答の記録（`records`）: 回答を `gate-rejections.jsonl` へ写し（#3136）、conductor が読む回答の一覧（#2934・#3174）と
  承認で見た head（#2147）を返す
- MVV 判定の前の機械の検査（`checks`）: 6 つの検査（PR 本文のセクションの中身は #2853）と、承認ゲートを省けるかの判定（#3017）・判定の記録（#2950）
- 工程を手で扱うサブコマンド（`finishing`）: `ndf progress finish`・`check`・`retry`
- サブコマンドのエントリポイント（`entry`）: `ndf answer`（`main`）と `ndf progress`（`progress_main`。エントリポイントは `progress_cli`）
"""

from __future__ import annotations

from ndf.progress.gate.answering import (
    ACTORS,
    DocsOnly,
    Observe,
    Post,
    answer,
    git_docs_only,
    github_observe,
    github_post,
    lane_env,
    never_docs_only,
    no_observe,
    no_post,
    recheck,
    reread,
    seen_material,
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
    mvv_lines,
    question,
    question_id,
    request_id,
    routes,
    text_sha256,
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
    check_claimed_sections,
    check_review_holds,
    check_mode,
    check_mvv_decl,
    check_mvv_hash,
    may_skip,
    mvv_decls,
    note,
    refusals,
    sections,
    well_formed,
)
from ndf.progress.gate.entry import _cmd_work, ask, main, present, progress_main, work_argv
from ndf.progress.gate.records import answers, approved_head, record_answer, rejection_row, rejections, rejections_path
from ndf.progress.gate.release import request_release, take_reply
from ndf.contracts.worker_kinds import work_kinds

__all__ = [
    "ACTORS",
    "CHECKS",
    "MATERIAL_DIR",
    "REJECTING",
    "RELEASE_KIND",
    "TOOL",
    "Answer",
    "Changed",
    "DocsOnly",
    "MvvFacts",
    "Observe",
    "Post",
    "Question",
    "Target",
    "_cmd_work",
    "answer",
    "github_post",
    "no_post",
    "answers",
    "approved_head",
    "ask",
    "check_approval_record",
    "check_boundary",
    "check_claimed_sections",
    "check_review_holds",
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
    "mvv_lines",
    "never_docs_only",
    "no_observe",
    "note",
    "present",
    "progress_main",
    "question",
    "question_id",
    "recheck",
    "record_answer",
    "reread",
    "refusals",
    "rejection_row",
    "rejections",
    "rejections_path",
    "request_id",
    "request_release",
    "routes",
    "sections",
    "seen_material",
    "take_reply",
    "text_sha256",
    "unconfirmed",
    "waiting",
    "well_formed",
    "work_argv",
    "work_kinds",
    "write_material",
]
