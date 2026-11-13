# #1142 の詳細設計: 前の企画（スプリント 1〜2c）の残り

**前の企画の設計のうち、まだ実装していない部分をファイルと関数の粒度へ下ろした文書である。** 残りは次の 3 つで、決定 65〜68（決定の記録）の方針で作る。
承認する人は、この 3 つを下の子 Issue（K1〜K3・W1〜W8）の分け方で実装へ進めるかを決める。撤去（旧 K4）は smells の A1a にまとめた（同じファイル `legacy_names.py`・`phase-steps.py`・`bundle-close.py` を消すため。頭の詳細設計 [issue-1142-design-detail.md](issue-1142-design-detail.md) の決定）。流す順序は頭の詳細設計の「実装計画」が持つ。
済んだ部分は書かない。済みと未着手の判定と根拠（PR 番号）は、移行の文書の「実装の状況（2026-11-13）」にある。

| 残り | 中身 | 子 Issue |
| --- | --- | --- |
| スプリント 2b の残り（決定 68） | 汎用の処理の 20 種類のうち、アダプターへ移り切っていない 4 種類（Markdown のコードブロック・パスの照合・コメントの除去・HTTP） | K1〜K3 |
| 撤去（決定 65） | 呼び出し元の無いエントリポイント 2 本と、投稿キューの移行の前のファイルの取り込み | smells の A1a にまとめた（旧 K4） |
| 語の移行（決定 65・67） | 用語集の廃止語を、スクリプトの値・出力・docstring・コメントから外し、禁止語にする | W1〜W8 |

前の企画の設計は次の文書にある。書き方は `plugins/ndf/skills/design/references/detailed-design.md` にある。
#1142 の 3 つの主題（smells・data・scripted-flow）の詳細設計は、それぞれの主題の詳細設計が持つ。

- 設計文書 `issues/issue-1142-design.md`
- [issue-1142-design-migration.md](issue-1142-design-migration.md) ・ [issue-1142-design-libraries.md](issue-1142-design-libraries.md) ・ [issue-1142-design-dbos.md](issue-1142-design-dbos.md)
- [issue-1142-design-modules.md](issue-1142-design-modules.md) ・ [issue-1142-design-decisions.md](issue-1142-design-decisions.md)

**子 Issue の案。** 触るファイルの全体は「ファイル配置」の表と一覧にある（パスは `plugins/ndf/` から。ルートの `scripts/` と `.ndf/` はリポジトリのルートから）。
先に要るものは、触るファイルが重なる 2 つを並列に流さない規則から決めた。リファクタリング計画の 8 件（#2963・#2990〜#2996）を先に要るものから外すか、流す順番は頭の詳細設計の「実装計画」が持つ（8 件は子 Issue に取り込み、別の設計 PR で設計すると 2026-11-13 に決まった）。

| 案の名前 | 中身 1 文 | 先に要るもの | 消す古いテスト | 大きさの目安 |
| --- | --- | --- | --- | --- |
| K1 Markdown のコードブロックの判定をアダプターへ | 節の読み書きと待ち通知の地の文の判定を `md` の上に置き、fence-regex の例外 2 項目を消す | #3153（`ci-watch.py`）・data の DT4（`issue-file.py`）・#2990（cross-review の `drive.py`） | `scripts/tests/test_gh_parts.py` のコードブロックの現状固定のテスト・`scripts/tests/test_wait_notify.py` の地の文の行のテスト | 14 本 |
| K2 パスの照合とコメントの除去をアダプターへ | `pace.matches` を `pathmatch` へ、コメントの除去を pygments へ移し、`comment_mask.py` を消す | K1（cross-review の `state.py`）・#2963・#2993（`supervise.py`）・data の DT6（`pace.py`） | `scripts/tests/test_pace.py` の照合のテスト | 15 本 |
| K3 HTTP をアダプターへ | `jev` と `refresh` の urllib を `notify` の httpx へ移し、urllib.request の例外 2 項目を消す | #2995（`release-steps.py`） | `scripts/tests/test_refresh.py`・`scripts/tests/test_instructions_check.py` の `refresh_lib.fetch` の opener のテスト・`scripts/tests/test_jev.py` の `_post` のテスト | 12 本 |
| （旧 K4 撤去） | smells の A1a にまとめた。中身（呼び出し元の無いエントリポイント 2 本と、投稿キューの移行の前のファイルの取り込みを消す）と下の関数の仕様は変えない | A1a の先に要るもの | `scripts/tests/test_step_scripts.py` の phase-steps の節・`scripts/tests/test_legacy_names.py`・`scripts/tests/test_post_queue.py` の取り込みのテスト（A1a が消す） | A1a に含む |
| W1 プランの実行の語 | 廃止語を替え、フェーズの結果の語と見出しを `report_fields` に集める | 3 主題とリファクタリング計画の子 Issue のうち、一覧のファイルを触るものすべて・K2 | 結果の値の字面を写すテスト（`scripts/tests/test_supervise*.py` ほか 23 本の該当の assert） | 約 70 本 |
| W2 cross-review の語 | 廃止語を替える | 同上・K1・K2・A1a（旧 K4） | 無し（字面を写す assert だけ直す） | 約 30 本 |
| W3 外部 CLI と記録の語 | 同上 | 同上・#2990 | 同上 | 約 18 本 |
| W4 cross-refactoring の語 | 同上（`改修計画` → リファクタリング計画ほか） | 同上・K3 | 同上 | 約 45 本 |
| W5 worktree と hook の語 | 同上（`作業ツリー`・`主ディレクトリ`・`共有の宣言`・`個人の宣言` ほか） | 同上・data の DT5 | 同上 | 約 30 本 |
| W6 ラッパーの語 | 同上（`合図` → シグナルファイルほか） | 同上 | 同上 | 約 13 本 |
| W7 リリースと文書の語 | 同上 | 同上・K3・#2995 | 同上 | 約 45 本 |
| W8 禁止語へ足す | W1〜W7 で外した廃止語を禁止語にする | W1〜W7 | 無し | 1 本 |

**測り直し（E9）はここに書かない。** 頭の設計文書の「3 つの主題をまとめた順序」の規則 5 が最後に流す。

**この文書の語。**

| 語 | この文書での意味 |
| --- | --- |
| アダプター | 外部ライブラリを呼ぶ唯一のモジュール（決定 19。`lib/md.py` など） |
| 廃止語 | 用語集（`docs/glossary/glossary.json`）の `deprecated` に載った語 |
| 禁止語 | `.ndf/glossary.json` の `check.banned.words`。CI の用語チェックが、足した行に現れたら落とす |
| フェーズの結果の語 | フェーズレポートの `結果:` の値（`完了`・`承認ゲート`・`止まった`・`報告なし`） |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設、`-` は削除。パスは `plugins/ndf/` から）:

```text
plugins/ndf/scripts/
├── lib/
│   ├── md.py                 # K1: 行の種類を返す関数を足す
│   ├── gh_sections.py        # K1: コードブロックと見出しの判定を md へ
│   ├── wait_notice.py        # K1: 地の文の行の判定を md へ
│   ├── pace.py               # K2: パスの照合を pathmatch へ
│   ├── pathmatch.py          # K2: docstring の 1 行
│   ├── textparse.py          # K2: コメントの除去を pygments で
│   ├── - comment_mask.py     # K2
│   ├── notify.py             # K3: HTTP の 2 関数
│   ├── jev.py                # K3
│   ├── refresh.py            # K3
│   ├── post_queue.py         # A1a（旧 K4）: 移行の前のファイルの取り込みを外す
│   └── report_fields.py      # W1: フェーズの結果の語の持ち主
├── glossary.py               # K2: コメントの除去の呼び先
├── - phase-steps.py          # A1a（旧 K4）
├── - bundle-close.py         # A1a（旧 K4）
└── （語の移行）               # W1〜W7: 下の一覧
plugins/ndf/contracts/entrypoints/  # A1a（旧 K4）: 消すエントリポイントの契約 2 本と、呼び出し元の一覧
scripts/check-script-structure.py    # A1a（旧 K4）: 取り込みの例外を外す（リポジトリの根）
scripts/script-structure-allow/      # K1・K3: wrapped の例外 4 項目を消す（リポジトリの根）
scripts/contract-allow/              # A1a（旧 K4）: 消すエントリポイントの例外 3 項目（リポジトリの根）
.ndf/glossary.json                   # W8: 禁止語（リポジトリの根）
```

**依存の向き。** エントリポイント → 業務のモジュール（`gh_sections`・`wait_notice`・`pace`・`glossary`・`jev`・`refresh`・
`post_queue`）→ アダプター（`md`・`pathmatch`・`textparse`・`notify`・`durable`）→ 外部ライブラリの一方向である。アダプターは業務の
モジュールを import しない（`notify` が import するのは `limits` だけ）。語の移行は import を足さず、W1 の
`report_fields` を読む側が import する向き（`supervise_lib`・`sprint_loop*`・`parallel*` → `lib/report_fields.py`）だけを足す。
`report_fields` は標準ライブラリだけを import するため循環は起きない。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/lib/md.py` | 変更 | 行ごとのブロックの種類を返す `block_kinds` を足す（K1） | markdown-it-py |
| `plugins/ndf/scripts/lib/gh_sections.py` | 変更 | 節の読み書きのコードブロックと見出しの判定を `md` の結果で行う（K1） | `lib/md.py` |
| `plugins/ndf/scripts/lib/wait_notice.py` | 変更 | 待ちの文を探す地の文の行を `md.block_kinds` で選ぶ（K1） | `lib/md.py` |
| `plugins/ndf/scripts/lib/pace.py` | 変更 | 範囲の照合を `pathmatch.path_matches` で行い、自作の変換を消す（K2） | `lib/pathmatch.py` |
| `plugins/ndf/scripts/lib/pathmatch.py` | 変更 | docstring の `pace.py:glob_re` の 1 行を消す（K2） | pathspec |
| `plugins/ndf/scripts/lib/textparse.py` | 変更 | コードのコメントを pygments の字句で空白にする `mask_comments` を足す（K2） | unidiff・pygments |
| `plugins/ndf/scripts/lib/comment_mask.py` | 削除 | 自作のコメントの走査（`textparse.mask_comments` へ移す）（K2） | 無し |
| `plugins/ndf/scripts/glossary.py` | 変更 | コメントの除去を `textparse.mask_comments` から呼ぶ（K2） | `lib/textparse.py` |
| `plugins/ndf/scripts/lib/notify.py` | 変更 | JSON の POST と本文の取得を httpx で行う 2 関数を足す（K3） | httpx・slack_sdk・python-dotenv |
| `plugins/ndf/scripts/lib/jev.py` | 変更 | 判定の要求を `notify.post_json` で送る（K3） | `lib/notify.py`・`lib/usage_capture.py` |
| `plugins/ndf/scripts/lib/refresh.py` | 変更 | 出典の取得を `notify.fetch_body` で行う（K3） | `lib/notify.py` |
| `plugins/ndf/scripts/lib/post_queue.py` | 変更 | 移行の前のファイルの取り込みと `imported/` を消す（A1a。旧 K4） | `lib/durable.py` |
| `plugins/ndf/scripts/phase-steps.py` | 削除 | 呼び出し元の無い互換のエントリポイント（A1a。旧 K4） | 無し |
| `plugins/ndf/scripts/bundle-close.py` | 削除 | 呼び出し元の無い旧名のエントリポイント（A1a。旧 K4） | 無し |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__phase-steps.py.json` | 削除 | 消すエントリポイントの契約（A1a。旧 K4） | 無し |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__bundle-close.py.json` | 削除 | 消すエントリポイントの契約（A1a。旧 K4） | 無し |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__plan-to-spec-steps.py.json` | 変更 | 呼び出し元の一覧から `phase-steps.py` を消す（A1a。旧 K4） | 無し |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__release-verification-steps.py.json` | 変更 | 呼び出し元の一覧から `phase-steps.py` を消す（A1a。旧 K4） | 無し |
| `scripts/check-script-structure.py` | 変更 | `_no_file_queue` の `_import_legacy` の除外と docstring の 1 文を消す（A1a。旧 K4） | 無し |
| `scripts/script-structure-allow/plugins__ndf__scripts__lib__gh_sections.py--fence-regex--wrapped.json` | 削除 | `gh_sections.py` の fence-regex の例外（K1） | 無し |
| `scripts/script-structure-allow/plugins__ndf__scripts__lib__wait_notice.py--fence-regex--wrapped.json` | 削除 | `wait_notice.py` の fence-regex の例外（K1） | 無し |
| `scripts/script-structure-allow/plugins__ndf__scripts__lib__jev.py--urllib.request--wrapped.json` | 削除 | `jev.py` の urllib.request の例外（K3） | 無し |
| `scripts/script-structure-allow/plugins__ndf__scripts__lib__refresh.py--urllib.request--wrapped.json` | 削除 | `refresh.py` の urllib.request の例外（K3） | 無し |
| `scripts/contract-allow/plugins__ndf__scripts__bundle-close.py--core-entry.json` | 削除 | 消すエントリポイントの例外（A1a。旧 K4） | 無し |
| `scripts/contract-allow/plugins__ndf__scripts__phase-steps.py--args-unparsed.json` | 削除 | 消すエントリポイントの例外（A1a。旧 K4） | 無し |
| `scripts/contract-allow/plugins__ndf__scripts__phase-steps.py--core-entry.json` | 削除 | 消すエントリポイントの例外（A1a。旧 K4） | 無し |
| `plugins/ndf/scripts/lib/report_fields.py` | 変更 | フェーズの結果の語とフェーズレポートの見出しの唯一の置き場（W1） | 無し |
| `.ndf/glossary.json` | 変更 | W1〜W7 で外した廃止語を `check.banned.words` へ足す（W8） | 無し |

W1〜W7 の一覧のファイル（廃止語を生きた語へ替える。値・出力・docstring・コメント）は、下の「W1〜W7 の触るファイル」の表にある。

**K1〜K3 で `deps.require` にアダプターのグループを足すエントリポイント**（構造チェックの `require-groups` が落とすもの。
2026-11-13 にアダプターの import を足したコピーへ `check-script-structure.py` を掛けて列挙した。関数は変えない）:

| グループ | エントリポイント |
| --- | --- |
| `md`（K1） | `scripts/ci-watch.py`・`scripts/issue-file.py`・`scripts/parallel-measure.py`・`scripts/wait-notify.py`・`scripts/lib/gh_parts.py`・`skills/backlog-refinement/scripts/upkeep.py`・`skills/cross-review/scripts/drive.py`・`skills/cross-review/scripts/state.py`・`skills/pr-review/scripts/pr-review-steps.py` |
| `pathmatch`（K2） | `scripts/check-trigger.py`・`scripts/mvv-gate.py`・`scripts/sprint-state.py`・`scripts/supervise.py`・`scripts/lib/review_criteria.py`・`skills/cross-review/scripts/state.py`・`skills/fix/scripts/fix-steps.py`・`scripts/experimental/queue-overlap-replay.py`・`scripts/experimental/selection-replay.py` |
| `notify`（K3） | `scripts/instructions-check.py`・`scripts/release-steps.py`・`skills/cross-refactoring/scripts/refactor.py` |

**W1〜W7 の触るファイル。** 2026-11-13 の develop で、廃止語を用語チェックと同じ正規表現（`glossary_words.word_pattern` のカタカナの境界つき）で数えた。
テストと `experimental/` を除く。`lib/legacy_names.py` と `data/legacy-values.json` は旧名を読むための表なので外す。パスは `plugins/ndf/` から。

| 子 Issue | 本数・か所 | ファイル |
| --- | --- | --- |
| W1 プランの実行 | 57 本・302 か所 | `scripts/check-trigger.py` `scripts/design-match.py` `scripts/lib/closing-issues.sh` `scripts/lib/durable.py` `scripts/lib/parallel_external.py` `scripts/lib/parallel_launch.py` `scripts/lib/parallel_release.py` `scripts/lib/parallel_route.py` `scripts/lib/parallel_run.py` `scripts/lib/parallel_switch.py` `scripts/lib/post_requests.py` `scripts/lib/pr_mode.py` `scripts/lib/project_mvv.py` `scripts/lib/report_fields.py` `scripts/lib/revise_note.py` `scripts/lib/run_state.py` `scripts/lib/sprint_loop.py` `scripts/lib/sprint_loop_gate.py` `scripts/lib/sprint_loop_gate2.py` `scripts/lib/sprint_loop_record.py` `scripts/lib/step_result.py` `scripts/lib/test_strategy.py` `scripts/lib/test_triage.py` `scripts/mvv-gate.py` `scripts/parallel.py` `scripts/project_lib/merge.py` `scripts/refactor-scope.py` `scripts/sprint-close.py` `scripts/supervise.py` `scripts/supervise_lib/` の 27 本（`absorb` `actuals` `claude` `decl` `delivery_templates` `design_stage` `engine` `flow` `new_args` `paths` `plan` `pr` `procedures` `prompts` `queue` `queue_fill` `queue_resume` `release_flow` `release_templates` `slow` `sprint` `sprint_close_steps` `sprint_waves` `state` `steps` `templates` `verify_steps`）`scripts/test-run.py`。値を読む手順: `skills/development-workflow/SKILL.md` `skills/development-workflow/references/agent-layers.md` `skills/development-workflow/references/waiting.md` `skills/development-workflow/references/context-window.md` `skills/development-workflow/references/pace.md` `skills/development-workflow/references/glossary.md` `agents/supervisor.md` `agents/supervisor-waits.md` `contracts/forms/ndf-next-block.json` |
| W2 cross-review | 28 本・134 か所 | `scripts/lib/post_queue.py` `scripts/lib/result_posts.py` `scripts/lib/review_criteria.py` `skills/cross-review/scripts/` の 25 本（`_tmpdir.sh` `classifications.py` `critique-round.sh` `critique.sh` `drive.py` `launch-reviewer.sh` `measure.py` `monitor.py` `review_lib/ci.py` `review_lib/commands/` の 8 本 `review_lib/github.py` `review_lib/participants.py` `review_lib/posts.py` `review_lib/review_focus.py` `review_lib/workspace.py` `rotate-pr.sh` `state.py` `wait-review.sh`） |
| W3 外部 CLI と記録 | 16 本・78 か所 | `scripts/lib/assignment.py` `scripts/lib/auth.py` `scripts/lib/bg-wait.sh` `scripts/lib/launch-cli.sh` `scripts/lib/models.py` `scripts/lib/monitor.py` `scripts/lib/monitor_loop.py` `scripts/lib/monitor_outcome.py` `scripts/lib/monitor_patterns.py` `scripts/lib/monitor_proc.py` `scripts/lib/monitor_types.py` `scripts/lib/run_metrics.py` `scripts/lib/transcript_agents.py` `scripts/lib/usage_aggregate.py` `scripts/parallel-measure.py` `scripts/usage-report.py` |
| W4 cross-refactoring | 42 本・264 か所 | `skills/cross-refactoring/scripts/` の `drive.py` `launch-cli.sh` `prepare-worktrees.sh` `refactor.py` と `refactor_lib/` の 38 本（語の当たるファイルすべて） |
| W5 worktree と hook | 27 本・240 か所 | `scripts/hook.py` `scripts/hook_lib/issue_guard.py` `scripts/hook_lib/token_guard.py` `scripts/hook_lib/worktree.py` `scripts/lib/git-credential.sh` `scripts/lib/hook_python.py` `scripts/lib/projects-common.sh` `scripts/lib/state_checkout.py` `scripts/lib/state_home.py` `scripts/lib/worktree-branch.sh` `scripts/lib/worktree-common.sh` `scripts/lib/worktree-copy.sh` `scripts/lib/worktree-declaration.sh` `scripts/lib/worktree-registry.sh` `scripts/lib/worktree-testenv-sweep.sh` `scripts/lib/worktree_base.py` `scripts/lib/worktree_deps.py` `scripts/progress-record.sh` `scripts/projects-sync.sh` `scripts/state-home.py` `scripts/worktree-deps.sh` `scripts/worktree-guard.sh` `scripts/worktree-localenv.sh` `scripts/worktree-session.sh` `scripts/worktree-setup.sh` `scripts/worktree-testenv.sh` `skills/development-workflow/scripts/lib/workflow-common.sh` |
| W6 ラッパー | 11 本・76 か所 | `scripts/lib/claude_api_keys.py` `scripts/relay_lib/` の `__init__.py` `claude.py` `common.py` `install.py` `mark.py` `record.py` `run.py` `runtime.py` `shellrc.py` `switch.py` |
| W7 リリースと文書 | 42 本・162 か所。`legacy_names.py` を除く | `scripts/instructions-check.py` `scripts/instructions_lib/`（`collect` `declaration` `findings` `versions`）`scripts/lib/`（`assignee_env` `container_reach` `delivery` `formatting` `glossary_lookup` `glossary_words` `jev` `limits` `loop_drive` `proc` `statefile` `waits`）`scripts/merged-steps.py` `scripts/merged_lib/`（`checks` `main_lock` `merge` `probe_table` `pull` `trash`）`scripts/pr-body-decisions.sh` `scripts/pr-steps.py` `scripts/pr_steps_lib/gh_pr.py` `scripts/project-decl.py` `scripts/release-steps.py` `scripts/release-verification-steps.py` `scripts/release_lib/`（`deploy` `materials` `others` `promotion` `step_run` `version_merge`）`skills/backlog-refinement/scripts/upkeep_depth.py` `skills/backlog-refinement/scripts/upkeep_research.py` `skills/external-ai/scripts/external-ai.py` `skills/fix/scripts/fix-steps.py` `skills/pr-review/scripts/pr-review-steps.py` `skills/skill-stats/scripts/skill-stats.py` |

**着手の時点で数え直す。** 上の一覧は 2026-11-13 の値で、先に流れる子 Issueがファイルを分けると変わる。各 W の PR は
着手の時点の develop で同じ数え方をやり直し、その一覧を PR 本文に載せる。

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/md.py` | モジュール `md` | `block_kinds`（新設） |
| `plugins/ndf/scripts/lib/gh_sections.py` | モジュール `gh_sections` | `unfenced_lines`・`_heading_lines`（変更）。`_FENCE`・`_HEADING` を消す |
| `plugins/ndf/scripts/lib/wait_notice.py` | モジュール `wait_notice` | `_prose_lines`（変更）。`_HEADING` を消す |
| `plugins/ndf/scripts/lib/pace.py` | モジュール `pace` | `matches`（変更）。`glob_re` を消す |
| `plugins/ndf/scripts/lib/textparse.py` | モジュール `textparse` | `mask_comments`（新設） |
| `plugins/ndf/scripts/glossary.py` | モジュール `glossary` | import の 1 行だけ（`mask_comments` の呼び先） |
| `plugins/ndf/scripts/lib/notify.py` | モジュール `notify` | `post_json`・`fetch_body`・クラス `FetchError`（新設） |
| `plugins/ndf/scripts/lib/jev.py` | モジュール `jev` | `_post`（変更） |
| `plugins/ndf/scripts/lib/refresh.py` | モジュール `refresh` | `fetch`・`refresh`（変更）。`_set_socket_timeout`・`_timeout_reason`・`FetchTimeout` を消す |
| `plugins/ndf/scripts/lib/post_queue.py` | クラス `Queue` | `_dormant`・`_session`・`paths`（変更）。`_import_legacy` と属性 `_left` を消す |
| `scripts/check-script-structure.py` | モジュール | `_no_file_queue`（変更） |
| `plugins/ndf/scripts/lib/report_fields.py` | モジュール `report_fields` | 定数 `DONE`・`GATE`・`STOPPED`・`NO_REPORT`・`HEADING`（新設）。`reason_shown` は `DONE` を読む（名前だけ） |
| `plugins/ndf/scripts/supervise_lib/state.py` | クラス `RunState` | `write_report`（見出しと値を `report_fields` から引く。名前だけ） |
| `plugins/ndf/scripts/supervise_lib/engine.py`・`flow.py`・`queue.py`・`queue_fill.py`・`queue_resume.py`・`actuals.py` | 各モジュール | `関門` の字面を `report_fields.GATE` へ替える（名前だけ） |
| `plugins/ndf/scripts/lib/sprint_loop.py`・`sprint_loop_gate.py`・`sprint_loop_gate2.py`・`sprint_loop_record.py`・`parallel_external.py`・`durable.py`、`plugins/ndf/scripts/parallel.py` | 各モジュール | 同上（名前だけ） |
| K1〜K3 の表のエントリポイント | 各 `main` | `deps.require(...)` にグループを 1 つ足す（名前だけ） |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `md.block_kinds` | `plugins/ndf/scripts/lib/md.py` | 新設 | `block_kinds(text: str) -> list[str]` | 行ごとに、その行を含むブロックの種類を返す | `deps.require("md")` の後 | 戻り値の長さは `text` の行の数。値は `code`（フェンスとインデントのコード）・`heading`・`quote`・`table`・`other` のどれか。入れ子は外側を優先する（引用の中の表は `quote`） | 無し |
| `gh_sections.unfenced_lines` | `plugins/ndf/scripts/lib/gh_sections.py` | 変更 | `unfenced_lines(lines: list[str]) -> Iterator[tuple[int, str]]` | コードブロックの外の行を返す | 同上 | 返す行は `md.fenced_lines` が偽の行だけ。閉じないコードブロックは文書の終わりまで続く。インデントのコードも外す | 無し |
| `gh_sections._heading_lines` | `plugins/ndf/scripts/lib/gh_sections.py` | 変更 | `_heading_lines(lines: list[str]) -> list[tuple[int, int]]` | コードブロックの外の ATX の見出しの行と段を返す | 同上 | 値は `md.atx_only_headings` の `(line, level)` と同じ並び | 無し |
| `wait_notice._prose_lines` | `plugins/ndf/scripts/lib/wait_notice.py` | 変更 | `_prose_lines(text: str) -> list[tuple[str, bool]]` | 待ちの文を探す地の文の行と、箇条書きの項目かどうかを返す | hook の環境（決定 20）の python で動く | `block_kinds` が `other` の空でない行だけを返す。箇条の印は外す | 無し |
| `pace.matches` | `plugins/ndf/scripts/lib/pace.py` | 変更 | `matches(path: str, patterns) -> bool` | 範囲の宣言のパターンにパスが当たるかを返す | `deps.require("pathmatch")` の後 | 値は `pathmatch.path_matches(path, patterns)` と同じ | 無し |
| `textparse.mask_comments` | `plugins/ndf/scripts/lib/textparse.py` | 新設 | `mask_comments(lines: list[str], suffix: str) -> list[str]` | コードの行のコメントを同じ幅の空白に置き換える | `deps.require("textparse")` の後。`suffix` は `.py`・`.sh`・`.js`・`.ts` のどれか | 戻り値の行の数と各行の長さは入力と同じ。pygments の `Token.Comment` の字だけが空白になる。ほかの拡張子は入力をそのまま返す | 無し |
| `notify.post_json` | `plugins/ndf/scripts/lib/notify.py` | 新設 | `post_json(url: str, body: dict, headers: dict[str, str], timeout: float, transport=None) -> dict \| None` | JSON を 1 回 POST し、応答の JSON のオブジェクトを返す | `deps.require("notify")` の後。`transport` はテストが渡す `httpx.MockTransport` | 応答が 2xx で JSON のオブジェクトならその辞書、ほかは `None`。例外を外へ出さない | ネットワーク |
| `notify.fetch_body` | `plugins/ndf/scripts/lib/notify.py` | 新設 | `fetch_body(url: str, timeout: float, transport=None) -> bytes` | URL の本文を、1 件あたりの総経過時間の上限の内で取得する。リダイレクトは同じ上限の内で追う（httpx の `follow_redirects=True`。httpx は既定で追わないため明示する。今の `urllib.request.urlopen` と同じ振る舞い） | 同上 | 最終の応答が 2xx なら本文の全部を返す。上限を越えた・2xx でない・接続できないときは `FetchError`（理由の日本語 1 行） | ネットワーク |
| `jev._post` | `plugins/ndf/scripts/lib/jev.py` | 変更 | `_post(body: dict[str, Any], timeout: int) -> Optional[dict[str, Any]]` | 判定の要求を 1 回送り、応答の JSON を返す | 呼ぶ側のエントリポイントが `deps.require("notify")` を呼んでいる | 鍵が無ければ送らずに `None`。送ったら帳簿へ 1 行（今のまま） | ネットワーク・使用量の帳簿への追記 |
| `refresh.fetch` | `plugins/ndf/scripts/lib/refresh.py` | 変更 | `fetch(url: str, timeout: float, transport=None) -> FetchResult` | 出典を取得し、指紋か失敗の理由を返す | 同上 | 例外を外へ出さない。失敗は `ok=False` と `error` | ネットワーク |
| `refresh.refresh` | `plugins/ndf/scripts/lib/refresh.py` | 変更 | `refresh(sources: list[dict], timeout: float, transport=None) -> tuple[list[str], int]` | 出典の一覧を取得し直して、表の行と失敗の数を返す | 同上 | 引数の名前を `opener` から `transport` へ替えるだけで、行の形と失敗の数え方は今のまま | ネットワーク |
| `Queue._dormant` | `plugins/ndf/scripts/lib/post_queue.py` | 変更 | `_dormant(self) -> bool` | 耐久の記録がまだ無いかを返す | 無し | 値は「耐久の記録のファイルが無い」だけで決まる。キューのディレクトリの `*.json` を見ない | 無し |
| `Queue._session` | `plugins/ndf/scripts/lib/post_queue.py` | 変更 | `_session(self) -> Iterator[dict[str, Any]]` | 耐久の記録を開き、閉じる | 無し | 開いている間に移行の前のファイルを取り込まない | 耐久の記録（SQLite）を開く |
| `Queue.paths` | `plugins/ndf/scripts/lib/post_queue.py` | 変更 | `paths(self) -> list[pathlib.Path]` | 終わっていない項目の名前のパスを連番の順に返す | 無し | 読めずに残ったファイルを並べない | 耐久の記録（SQLite）を読む |
| `_no_file_queue` | `scripts/check-script-structure.py` | 変更 | `_no_file_queue(cls: ast.AST) -> str \| None` | 投稿キューのクラスがファイルを開く・glob する呼び出しを見つける | 無し | すべてのメソッドを見る（除外の名前を持たない） | 無し |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `notify.post_json` | 通信の失敗・上限の時間を越えた | ネットワーク・相手の停止 | `httpx.HTTPError` を受ける | 既定値で続ける（`None` を返す） | 無し（呼ぶ側が判定を見送る） | 後始末なし。次の呼び出しでそのまま再実行できる |
| `notify.post_json` | 2xx でない・本文が JSON のオブジェクトでない | 鍵の誤り・相手の誤り | 応答の状態と `json.loads` | 既定値で続ける（`None`） | 無し | 同上 |
| `notify.fetch_body` | 総経過時間の上限を越えた | 相手が遅い・送り続ける | 読み取りのたびに単調時計の期限を見る | 呼び出し元へ返す（`FetchError("<N> 秒を越えた")`） | 呼ぶ側の表の 1 行（`待ちを越えた（<N> 秒を越えた）`） | 接続は `with` で閉じる。再実行できる |
| `notify.fetch_body` | 最終の応答が 2xx でない・転送が多すぎる | 相手の誤り・URL の誤り | 応答の状態・`httpx.TooManyRedirects` | 呼び出し元へ返す（`FetchError("HTTP <状態>")`） | 同上（`HTTP 404` など） | 同上 |
| `notify.fetch_body` | 接続できない | 名前の解決・TLS・接続の拒否 | `httpx.TransportError` | 呼び出し元へ返す（`FetchError("接続できない（<型>）")`） | 同上 | 同上 |
| `jev._post` | 鍵が無い | 環境変数 `AI_GATEWAY_API_KEY` が無い | 送る前 | 既定値で続ける（`None`。帳簿へ書かない） | 無し | 無し |
| `jev._post` | 応答が無い | `notify.post_json` が `None` を返した | 戻り値 | 既定値で続ける（`None`。帳簿へは送った 1 行を書く） | 無し | 無し |
| `refresh.fetch` | 取得の失敗 | `notify.FetchError` | `fetch_body` の呼び出し | 既定値で続ける（`FetchResult(ok=False, error=<理由>)`） | 出典の表の行に理由。失敗の数が 1 増え、エントリポイントは今の終了コードの規則のまま | 再実行できる |
| `refresh.refresh` | 1 件以上の取得の失敗 | 同上 | `fetch` の戻り値 | 既定値で続ける（残りを取得する） | 表の行と失敗の数 | 同上 |
| `Queue._session` | 耐久の記録を開けない | DBOS の起動の失敗・ロックの待ちの上限 | `durable.launch` | 呼び出し元へ返す（`durable.DurableError`） | 呼ぶ側の今の扱い（`state.py`・`refactor.py` の結果 JSON の `stopped`） | 記録は残る。同じコマンドで再実行できる |
| `Queue.paths` | 耐久の記録を読めない | 同上 | `_records` | 呼び出し元へ返す | 同上 | 同上 |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `md.block_kinds` | 行が `fence` か `code_block` の区間 | `code` |
| `md.block_kinds` | 行が `heading_open` の区間（ATX・Setext） | `heading` |
| `md.block_kinds` | 行が `blockquote_open` の区間 | `quote`（中の表・コードも `quote`） |
| `md.block_kinds` | 行が `table_open` の区間 | `table` |
| `md.block_kinds` | どれにも当たらない（段落・箇条・空行） | `other` |
| `notify.fetch_body` | 3xx の転送（301・302・303・307・308） | 期限の残りの内で転送先を取りに行き、最終の応答で下の行を判定する。上限の秒数は転送の前後で通しで数える |
| `notify.fetch_body` | 転送の数が httpx の上限（`max_redirects` の既定 20）を越えた | `FetchError("転送が多すぎる")` |
| `notify.fetch_body` | 2xx・期限の内で読み終えた | 本文を返す |
| `notify.fetch_body` | 2xx・読み取りの途中で期限を越えた | `FetchError("<N> 秒を越えた")` |
| `notify.fetch_body` | 最終の応答が 2xx でない | `FetchError("HTTP <状態>")`（本文を読まない） |
| `notify.fetch_body` | 接続の前か途中の通信の失敗 | `FetchError("接続できない（<型>）")` |
| `Queue._dormant` | 耐久の記録のファイルが無い | 真（DBOS を起動せずに空と答える） |
| `Queue._dormant` | 耐久の記録のファイルがある | 偽 |

- 繰り返しの終わる条件: `notify.fetch_body` は本文の終わりか期限で抜ける。1 回の読み取りの上限は httpx の `read` のタイムアウトに残りの秒数を渡して掛ける（接続ごとに `httpx.Timeout` を作り直す）
- 計算量の注意: `md.block_kinds` は文書を 1 回解析する（`md._tokens` の `lru_cache` を使う）。`wait_notice` は transcript の末尾 1 MB だけを渡す今の上限のまま

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `BLOCK_KINDS` | 定数 | `plugins/ndf/scripts/lib/md.py` | `("code", "heading", "quote", "table", "other")` |
| `FetchError` | 型 | `plugins/ndf/scripts/lib/notify.py` | `Exception` の子。`str(e)` が理由の日本語 1 行 |
| `refresh.CHUNK_BYTES` | 定数 | `plugins/ndf/scripts/lib/refresh.py` から `plugins/ndf/scripts/lib/notify.py` へ移す | 65536 |
| 取得と送信の上限の秒数 | 設定値 | `plugins/ndf/contracts/limits.json`（`refresh.http`・`jev.probe`・`jev.ask`。今のまま） | 今の値 |
| `report_fields.DONE`・`GATE`・`STOPPED`・`NO_REPORT` | 定数 | `plugins/ndf/scripts/lib/report_fields.py` | `"完了"`・`"承認ゲート"`・`"止まった"`・`"報告なし"` |
| `report_fields.HEADING` | 定数 | `plugins/ndf/scripts/lib/report_fields.py` | `"## フェーズレポート"` |
| 禁止語 | 設定値 | `.ndf/glossary.json` の `check.banned.words` | 今の 2 語に、W1〜W7 で外した廃止語を足す |

**フェーズの結果の語は `report_fields` だけが持つ。** 今は `関門` の字面が、12 ファイルの定数と比較に散っている。
例は `engine.PHASE_STATUS`・`actuals.OUTCOMES`・`queue_fill.FINISHED`・`queue_resume.RESUMABLE`・`state.PROGRESS_ATTENTION`・`sprint_loop_record.FINISHED` である。W1 はこれらを `report_fields` の定数から引く形にし、語を替えるのを 1 か所にする。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| W1 のマージと、流れているプラン・キュー・並列の計画 | 耐久の記録・`<プラン>-state/`・done のファイルに書いた結果の値（`関門`） | 取らない。#1142 の実装の間はほかの開発を止める（決定 65）ため、W1 のマージの時点で流れているプランを持たない | 古い値を読み替えない（決定 65）。W1 の前に `関門` で止まったプランは、W1 の後に `--from` で頭から流し直す |
| A1a（旧 K4）のマージと、積まれた投稿キュー | キューのディレクトリの `<連番>-*.json` | 取らない | 取り込みを外すため、移行の前のファイルは送られない。2c（PR #1554、2026-09-30）から 6 週を越え、`post_queue.py count` が 0 であることを A1a の PR の確認の手順に入れる |
| hook（`wait_notice`）と同じ時刻の別の hook | 無し（読み取りだけ） | 不要 | 冪等 |
| `refresh.refresh` の取得 | 外部のサイト | 1 件ずつ順に取得する（今のまま） | 冪等。打ち直せる |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `jev._post` | 送った要求 1 回の使用量 | 使用量の帳簿（`usage_capture.record_jev`。今のまま） | 鍵（`AI_GATEWAY_API_KEY`）・要求の本文 |
| `notify.post_json` | 無し（呼ぶ側が扱う） | 無し | 見出し `Authorization` の値 |
| `refresh.refresh` | 出典ごとの結果の行（取得できたか・指紋が一致するか・失敗の理由） | 結果 JSON と表（今のまま） | 本文 |
| `report_fields` を読む側 | フェーズレポートの `結果:` の値 | 標準出力と `<プラン>-state/report.md`・進捗ログの attention の行 | 無し |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `md.block_kinds` | `md.fenced_lines`・`md.headings`・`md.tables`・`md._tokens` | `grep -n '^def ' plugins/ndf/scripts/lib/md.py` | 使う（`_tokens` の 1 回の解析から作る）。`fenced_lines` は `code` だけで、見出し・引用・表を持たないため、行の種類は新設する |
| `textparse.mask_comments` | pygments（`textparse` グループに入っているが未使用）・`lib/comment_mask.py` | `grep -rn pygments plugins/ndf/scripts --include=*.py`（0 件）・`sed -n '/^textparse/p' plugins/ndf/pyproject.toml` | pygments を使う。`comment_mask.py` は自作の走査なので消す |
| `notify.post_json` | `notify.slack_call`（slack_sdk の WebClient）・httpx（`notify` グループに入っているが未使用）・`lib/claude_usage.py` の urllib | `grep -rn 'httpx' plugins/ndf/scripts --include=*.py`・`grep -n 'urllib' -r plugins/ndf/scripts/lib` | httpx を使う。`slack_call` は Slack の API 専用で、任意の URL へ送れない。`claude_usage` はラッパーの環境で動く例外（#1389 の決定 13）で対象の外 |
| `notify.fetch_body` | 同上・`refresh.fetch` の自作の期限の監視 | 同上 | httpx の `stream` と `Timeout` を使う。socket へ期限を渡す自作（`_set_socket_timeout`）は httpx が持つので消す |

## 単体テストの観点

**テストは作り直す（決定 66）。** 置き換えた関数の古いテストは同じ PR で消し、下の観点で書き直す。消す古いテストは
各子 Issueの行の「消す古いテスト」にある。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `md.block_kinds` | 段落・見出し・コードブロック・表・引用が混ざった文書で行ごとの種類 | 閉じないコードブロック・`~~~` の中の ```` ``` ````・引用の中の表・空の文書（長さ 0） | 無し（例外を出さない） |
| `gh_sections.unfenced_lines` | コードブロックの中の `## 見出し` が節の境にならない | CRLF・閉じないコードブロック・インデントのコード | 無し |
| `wait_notice._prose_lines` | 箇条の印が外れ、表・引用・見出し・コードの行が除かれる | 末尾がコードブロックの中で終わる文 | 無し |
| `pace.matches` | `plugins/ndf/scripts/lib/**` が下のファイルに当たる | `*` が `/` をまたがない・`**/.env*` が深い階層に当たる・`docs/` がディレクトリの中身に当たる | 無し |
| `textparse.mask_comments` | `.py` の `#`・`.sh` の語の頭の `#`・`.js` の `//` と `/* */` が空白になる | 文字列の中の `#`・三連引用符をまたぐ行・`.sh` の `${x#y}`・知らない拡張子 | 無し |
| `notify.post_json` | `MockTransport` の 200 と JSON で辞書 | 本文が JSON の配列 | 通信の失敗で `None`・500 で `None`・JSON でない本文で `None` |
| `notify.fetch_body` | 200 の本文を返す。301 の転送の先の 200 の本文を返す | 期限ちょうどで読み終える。転送の後に期限を越える | 期限の超過・404・転送の先の 404・転送が多すぎる・接続の失敗のそれぞれの `FetchError` の文 |
| `jev._post` | 鍵があり応答があれば辞書と帳簿の 1 行 | 鍵が空文字 | 鍵なしで送らない・応答なしで `None` と帳簿の 1 行 |
| `refresh.fetch` | 指紋を返す | 空の本文 | `FetchError` を `ok=False` の理由へ写す |
| `refresh.refresh` | 2 件中 1 件の失敗で表の 2 行と失敗 1 | 出典 0 件 | 全件の失敗 |
| `Queue._dormant` | 記録が無ければ真で DBOS を起動しない | ディレクトリに `<連番>-*.json` が残っていても真 | 無し |
| `Queue._session` | 開いて閉じる | 開いている間の入れ子の呼び出し | 起動の失敗で `DurableError` |
| `Queue.paths` | 連番の順 | 項目 0 件 | 読めない記録で例外 |
| `_no_file_queue` | `Queue` に `glob` を書くと違反 | `_import_legacy` という名前のメソッドでも違反になる | 無し |
| W1〜W7 | 用語チェックを `--file` で各子 Issueの一覧へ掛け、廃止語の当たりが 0 | `legacy_names.py`・`data/legacy-values.json` は数えない | フェーズの結果の語のテストは `report_fields` の定数を読み、字面を写さない |
| W8 | 足した行に禁止語を書いた差分で用語チェックが落ちる | 外した範囲（`docs/archive/**` ほか）では落ちない | 無し |
