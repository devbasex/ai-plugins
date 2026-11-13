# #1142 の詳細設計: 定数からデータへ

設計文書 `issues/issue-1142-design-data.md` の「構成要素」と「データ構造」と「移行の順序」を、ファイルと関数の粒度へ下ろした文書である。書き方は `plugins/ndf/skills/design/references/detailed-design.md` にある。全体の設計は [issue-1142-design.md](issue-1142-design.md) にある。要求は [issue-1142-requirements.md](issue-1142-requirements.md) にある。

**この主題は後方互換を持たない。** 形を変える定数・状態ファイル・検査の結果は、最終の形へ 1 回で変える。古い形を読む分岐と別名は、その事実を移す子 Issue の PR で消す。テストは下の「単体テストの観点」から作り直し、置き換えた範囲の古いテストは同じ PR で消す。

**子 Issue の分け方**: 設計文書の移行ステップ DT1〜DT8 を、下の 8 つの子 Issue（DC1〜DC8）に組み直した。各行の「役割 1 文」の末尾の `（DC<番号>）` が、そのファイルを触る子 Issue である。

| 子 Issue | 移行ステップ | 先に要るもの |
| --- | --- | --- |
| DC1 事実の表の読み手 | DT1 の前半 | 無し |
| DC2 重複の検査と基準 | DT1 の後半・DT7 | #2996・DC1 |
| DC3 ランタイムの表 | DT2 | DC1 |
| DC4 収束ループのフェーズ・指摘の重さ・テストの印 | DT3 | DC1 |
| DC5 閾値 | DT4 | DC3（`hook_lib/wait_notify.py` が重なる） |
| DC6 宣言の置き場・環境変数名・PR 本文の見出し・指示書 | DT5 | DC1 |
| DC7 工程とフェーズ・モード・進め方・ブランチの接頭辞・承認ゲート | DT6 | #3092 の実装・DC5（`hook_lib/token_guard.py`）・DC6（`glossary.py`） |
| DC8 子 Issue の後の残り | DT8 | #2990・#2992・#2993・#2994・#2995 と DC1 |

- **事実を移す子 Issue（DC3〜DC8）は DC2 を待たない。** 移した後に DC2 が基準を作れば、移した組は初めから基準に無い。DC2 の後に移す子 Issue は、自分が直した組の基準の行を同じ PR で消す
- **DT7 は DC2 に吸収した。** #3144 の実装が、設計文書の種類を `contracts/design-docs.json` と `lib/doc_kinds.py` へ移し終えた。`scripts/measure/const-facts.py --json` の結果に、`-design.md` を持つ組が 0 件である（2026-11-13、HEAD 7fc42b7b8）。基準を作る DC2 の時点で消す行が無い
- **E9（測り直し）は子 Issue にしない。** DC8 の後に `python3 scripts/measure/const-facts.py --json` を実行し、前後の数を #1142 のコメントへ残す

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
pyproject.toml / uv.lock                         (DC1: グループ jsonschema)
.github/workflows/script-structure.yml           (DC2: ndf-contracts-check に uv)
docs/specifications/ndf-entrypoint-contracts.md  (DC2)
scripts/
├── check-ndf-contracts.py                       (DC2)
├── const-fact-baseline.json *                   (DC2)
├── measure/const-facts.py                       (DC2: コアを呼ぶだけ)
├── contracts_lib/
│   ├── const_facts.py *                         (DC2: 組を拾うコア)
│   ├── const_baseline.py *                      (DC2: 基準の読み書きと照合)
│   ├── facts_decl.py *                          (DC2: 表の形・外部キー・逆引き)
│   ├── ratchet.py                               (DC2: 基準の行の増加)
│   └── render.py                                (DC2)
└── tests/test_const_facts.py * / test_const_baseline.py * / test_facts_decl.py *
plugins/ndf/
├── pyproject.toml / uv.lock                     (DC1)
├── contracts/
│   ├── facts/ *  fact.schema.json runtimes.json loop-phases.json severities.json test-markers.json
│   │             places.json instruction-files.json branches.json gates.json
│   ├── forms/pr-body.json *                     (DC6)
│   ├── limits.json                              (DC5)
│   └── index.md                                 (DC2 以降。生成)
├── scripts/lib/facts.py * / facts.sh *          (DC1)
├── scripts/lib/limits.py / doc_kinds.py / deps.py (DC1)
├── scripts/supervise.py                        (DC1: deps.require に jsonschema)
└── scripts/ と skills/*/scripts/ の読み手        (DC3〜DC8)
```

依存の向き: 事実の表（`contracts/facts/*.json`）はデータだけを持ち、何も参照しない。実行時の読み手 `lib/facts.py` は表だけを読み、NDF のほかのモジュールを import しない（標準ライブラリだけ）。`lib/facts.sh` は `lib/facts.py` を子のプロセスで呼ぶだけである。NDF の読み手は `lib/facts.py` か `lib/facts.sh` を通して表を引き、表は読み手を知らない。検査の側（`scripts/contracts_lib/`）は、`lib/facts.py` を読み手として読み込む（`limits_decl._reader` と同じ形）。プラグインの側から `scripts/` は参照しない。向きは「表 ← 読み手 ← 検査」の一方向で、循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `pyproject.toml` | 変更 | グループ `jsonschema = ["jsonschema==4.26.0"]` を足す（全体テストとリポジトリ直下の検査が使う）（DC1） | 無し |
| `uv.lock` | 変更 | 上の固定（DC1） | `pyproject.toml` |
| `plugins/ndf/pyproject.toml` | 変更 | 同じグループを足す（デプロイ先で答えを確かめ直す経路が使う。設計文書の決定 46）（DC1） | 無し |
| `plugins/ndf/uv.lock` | 変更 | 上の固定（DC1） | `plugins/ndf/pyproject.toml` |
| `plugins/ndf/scripts/lib/deps.py` | 変更 | `GROUPS` に `"jsonschema": ["jsonschema"]` を足す（DC1） | 無し |
| `plugins/ndf/scripts/supervise.py` | 変更 | 最初の 1 回の `deps.require(...)` に `"jsonschema"` を足す（DC1）。グループを登録しただけでは使うグループに選ばれず、scripted-flow の SF6 の判断の呼び出し（`supervise_lib/judge_call.py`）がデプロイ先で import できない | `plugins/ndf/scripts/lib/deps.py` |
| `plugins/ndf/scripts/lib/facts.py` | 新設 | 事実の表の唯一の実行時の読み手と、シェル向けの副命令（DC1） | `plugins/ndf/contracts/facts/` |
| `plugins/ndf/scripts/lib/facts.sh` | 新設 | シェルの読み手 `ndf_fact_ids`・`ndf_fact_value`（DC1） | `plugins/ndf/scripts/lib/facts.py` |
| `plugins/ndf/contracts/facts/fact.schema.json` | 新設 | 表の共通の形（JSON Schema draft 2020-12）（DC1） | 無し |
| `plugins/ndf/scripts/lib/limits.py` | 変更 | 宣言の置き場を `facts.contract_path` で引く（置き場の探し方を 1 か所にする）（DC1） | `plugins/ndf/scripts/lib/facts.py` |
| `plugins/ndf/scripts/lib/doc_kinds.py` | 変更 | 同上（DC1） | `plugins/ndf/scripts/lib/facts.py` |
| `plugins/ndf/scripts/tests/test_facts.py` | 新設 | `lib/facts.py` の単体テスト（DC1） | `plugins/ndf/scripts/lib/facts.py` |
| `plugins/ndf/scripts/tests/test_facts_sh.py` | 新設 | `lib/facts.sh` を bash で source して確かめる（DC1） | `plugins/ndf/scripts/lib/facts.sh` |
| `scripts/contracts_lib/const_facts.py` | 新設 | 定数とテストから重複の組を拾うコア（計測と検査が共有）（DC2） | #2996 の解析のキャッシュ |
| `scripts/contracts_lib/const_baseline.py` | 新設 | 重複の基準の読み込み・形の検査・今の組との照合（DC2） | `scripts/const-fact-baseline.json` |
| `scripts/contracts_lib/facts_decl.py` | 新設 | 表の形（`jsonschema`）・外部キー・表の `id` と定数の照合・逆引きの節（DC2） | `plugins/ndf/scripts/lib/facts.py`・`jsonschema` |
| `scripts/contracts_lib/limits_decl.py` | 変更 | 呼び出しを集める `_Calls` を、import の名前と関数の名前の組を引数で受ける形にし、`facts_decl` と共有する（DC2） | 無し |
| `scripts/contracts_lib/ratchet.py` | 変更 | 1 ファイルの基準の行の増加を merge-base と比べる関数を足す（DC2） | 無し |
| `scripts/contracts_lib/render.py` | 変更 | 逆引きに節「事実の表」を足す（DC2） | `scripts/contracts_lib/facts_decl.py` |
| `scripts/check-ndf-contracts.py` | 変更 | 規則 `const-fact`・`fact-table` を足し、検査と `--render` の前に `jsonschema` を用意する（DC2）。DC6 が 契約の抽出が `env_num` で読む環境変数と relay の受け渡しのファイルを拾う（#2434） | 上の 4 つ・`plugins/ndf/scripts/lib/deps.py` |
| `scripts/const-fact-baseline.json` | 新設 | 重複の基準（DC2） | 無し |
| `scripts/measure/const-facts.py` | 変更 | コア `const_facts.measure` を呼ぶだけにする。出力は変えない（DC2） | `scripts/contracts_lib/const_facts.py` |
| `.github/workflows/script-structure.yml` | 変更 | ジョブ `ndf-contracts-check` に `astral-sh/setup-uv` を足す（`deps.require` が uv で起動し直す）（DC2） | 無し |
| `docs/specifications/ndf-entrypoint-contracts.md` | 変更 | 規則 2 つと基準のファイルの形を足す（DC2） | 無し |
| `plugins/ndf/contracts/index.md` | 変更 | 生成し直す（節「事実の表」）（DC2・DC3〜DC8） | `scripts/check-ndf-contracts.py` |
| `scripts/tests/test_const_facts.py` | 新設 | コアの単体テスト（DC2） | `scripts/contracts_lib/const_facts.py` |
| `scripts/tests/test_const_baseline.py` | 新設 | 基準と増加の単体テスト（DC2） | `scripts/contracts_lib/const_baseline.py`・`scripts/contracts_lib/ratchet.py` |
| `scripts/tests/test_facts_decl.py` | 新設 | 表の形・外部キー・逆引きの単体テスト（DC2） | `scripts/contracts_lib/facts_decl.py` |
| `plugins/ndf/contracts/facts/runtimes.json` | 新設 | ランタイムの表（DC3） | 無し |
| `plugins/ndf/scripts/lib/assignment.py` | 変更 | `ALL_RUNTIMES`・`HOST_RUNTIMES`・`DEFAULT_RUNTIMES`・`HOST_ENV_HINTS` を表から導く（DC3） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/auth.py` | 変更 | `AUTH_PROBES`・`MODEL_PROBES`・`DEFAULT_MODEL_ARGS` を表の列から導く（DC3） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/usage_capture.py` | 変更 | `CLI_RUNTIMES` を表から導く（DC3） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/usage_ledger.py` | 変更 | `RUNTIMES` を表の `id` と帳簿だけの記録元 `jev` から導く（DC3） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/models.py` | 変更 | `OBSERVABLE_RUNTIMES` を列 `usage_observable` から導く（DC3） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/procs.py` | 変更 | `RUNTIME_NAMES` を `id` と `executable` の和から導く（DC3） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/wait_notice.py` | 変更 | `_CLASSIFIERS` のキーを列 `hook_events` と突き合わせる（DC3） | `lib/facts.py` |
| `plugins/ndf/scripts/hook.py` | 変更 | `RUNTIMES` を列 `hook_events` から導く（DC3） | `lib/facts.py` |
| `plugins/ndf/scripts/hook_lib/wait_notify.py` | 変更 | 同上（DC3） | `lib/facts.py` |
| `plugins/ndf/scripts/training-optout.py` | 変更 | `RUNTIMES` を表の `id` から導く（DC3） | `lib/facts.py` |
| `plugins/ndf/skills/external-ai/scripts/external-ai.py` | 変更 | `RUNTIMES`・`EXECUTABLE` を表から導く（DC3） | `lib/facts.py` |
| `plugins/ndf/skills/cross-review/scripts/review_lib/participants.py` | 変更 | `LEGACY_AGENTS`・別名 `AGENTS` と、`participants` を持たない状態ファイルの分岐を消す（DC3） | `lib/assignment.py` |
| `plugins/ndf/skills/cross-review/scripts/review_lib/commands/report.py` | 変更 | `LEGACY_AGENTS` への戻りを消す（DC3） | 無し |
| `plugins/ndf/skills/pr-review/scripts/pr-review-steps.py` | 変更 | `DELEGATES` を列 `delegate` から導く（DC3） | `lib/facts.py` |
| `plugins/ndf/scripts/tests/test_runtimes_table.py` | 新設 | ランタイムの表から導く値の単体テスト（DC3） | 上の読み手 |
| `plugins/ndf/contracts/facts/loop-phases.json` | 新設 | 収束ループのフェーズの表。`id` は `limits.json` の `monitor.phase.<id>` を指す（DC4） | `plugins/ndf/contracts/limits.json` |
| `plugins/ndf/contracts/facts/severities.json` | 新設 | 指摘の重さの表（DC4） | 無し |
| `plugins/ndf/contracts/facts/test-markers.json` | 新設 | テストの置き場と名前の印の表（cross-review と cross-refactoring が読む）（DC4） | 無し |
| `plugins/ndf/scripts/lib/body_findings.py` | 変更 | `SEVERITIES` を表から導く（DC4） | `lib/facts.py` |
| `plugins/ndf/skills/cross-review/scripts/review_lib/findings.py` | 変更 | `_SEVERITY_RANK` を列 `rank` から導く（DC4） | `lib/facts.py` |
| `plugins/ndf/skills/cross-review/scripts/review_lib/categories.py` | 変更 | `TEST_PATH_MARKERS`・`TEST_NAME_MARKERS` を表から導く（DC4） | `lib/facts.py` |
| `plugins/ndf/skills/cross-review/scripts/review_lib/ci.py` | 変更 | `CI_FAILED_CONCLUSIONS`・`CHECK_RUNS_*` を消し、持ち主 `lib/gh_checks.py` を import する（DC4） | `plugins/ndf/scripts/lib/gh_checks.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/assignee_launch.py` | 変更 | `CODING_PHASES`・`FIX_PHASES` を列から導く（DC4） | `lib/facts.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/drive.py` | 変更 | `ALIVE_PHASES`・`RESUME_AS` を列から導く（DC4） | `lib/facts.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/phases.py` | 変更 | `PHASE_NAMES` を列 `launch` から導く（DC4） | `lib/facts.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/reassign.py` | 変更 | `STEPS`・`RELAUNCH_IN_PLACE`・`UNDO_STEPS` を列から導く（DC4） | `lib/facts.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/safety_net.py` | 変更 | `CODING_PHASES` を消し `assignee_launch` と同じ列で引く（DC4） | `lib/facts.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/vocabulary.py` | 変更 | `SEVERITY_ORDER`・`SEVERITIES` を表から導く（DC4） | `lib/facts.py` |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/pathkinds.py` | 変更 | テストの印を表から導く（DC4） | `lib/facts.py` |
| `plugins/ndf/skills/fix/scripts/fix-steps.py` | 変更 | `SEVERITIES`・`FIX_SEVERITIES`・`WAIVE_SEVERITIES` を列 `blocking` から導く（DC4） | `lib/facts.py` |
| `plugins/ndf/skills/pr-review/scripts/pr_review_findings.py` | 変更 | `SEVERITIES`・`BLOCKING` を表から導く（DC4） | `lib/facts.py` |
| `plugins/ndf/scripts/mode-materials.py` | 変更 | `TEST_FORMS` を表から導く（DC4） | `lib/facts.py` |
| `plugins/ndf/scripts/tests/test_loop_tables.py` | 新設 | フェーズ・重さ・テストの印から導く値の単体テスト（DC4） | 上の読み手 |
| `plugins/ndf/contracts/limits.json` | 変更 | 方針 `output.tail-lines`・`record.days`・`error.excerpt`・`testenv.reclaim`・`review.body-match` を足す（DC5） | 無し |
| `plugins/ndf/scripts/hook_lib/token_guard.py` | 変更 | `TAIL_LINES`・`RECORD_DAYS` を方針から引く（DC5） | `lib/limits.py` |
| `plugins/ndf/scripts/hook_lib/wait_notify.py` | 変更 | `RECORD_DAYS` を方針から引く（DC5） | `lib/limits.py` |
| `plugins/ndf/scripts/issue-file.py` | 変更 | `ERROR_EXCERPT` を方針から引く（DC5） | `lib/limits.py` |
| `plugins/ndf/scripts/issue-items.py` | 変更 | 同上（DC5） | `lib/limits.py` |
| `plugins/ndf/scripts/relay_lib/version_dir.py` | 変更 | `FOREIGN_WINDOW` を消し `run_owner.WINDOW` の方針を引く（DC5） | `lib/limits.py` |
| `plugins/ndf/scripts/lib/run_owner.py` | 変更 | `WINDOW` を方針から引く（DC5） | `lib/limits.py` |
| `plugins/ndf/scripts/lib/post_queue.py` | 変更 | `BODY_MATCH_CHARS` を方針から引く（DC5） | `lib/limits.py` |
| `plugins/ndf/scripts/lib/finding_match.py` | 変更 | `OSCILLATION_BODY_CHARS` を同じ方針から引く（DC5） | `lib/limits.py` |
| `plugins/ndf/scripts/lib/container_reach.py` | 変更 | `RECLAIM_LIMIT` を方針から引く（DC5） | `lib/limits.py` |
| `plugins/ndf/scripts/supervise_lib/verify_steps.py` | 変更 | `RECLAIM_LIMIT` を消し方針を引く（DC5） | `lib/limits.py` |
| `plugins/ndf/scripts/tests/test_limits_moved.py` | 新設 | 移した方針を引く読み手の単体テスト（DC5） | `lib/limits.py` |
| `plugins/ndf/contracts/facts/places.json` | 新設 | `.ndf` と `.worktrees` の置き場の表（DC6） | 無し |
| `plugins/ndf/contracts/facts/instruction-files.json` | 新設 | 指示書のファイルの表（DC6） | 無し |
| `plugins/ndf/contracts/forms/pr-body.json` | 新設 | PR 本文の見出しの形（DC6） | 無し |
| `plugins/ndf/scripts/hook_lib/worktree.py` | 変更 | `DECLARATION_FILE`・`WORKTREE_DIR` を `facts.setting_path`・`places` から引く（DC6） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/worktree-common.sh` | 変更 | `WT_DECLARATION_FILE`・`WT_DECLARATION_LOCAL_FILE`・`WT_WORKTREE_DIR` を `lib/facts.sh` で引く（DC6） | `lib/facts.sh` |
| `plugins/ndf/scripts/lib/worktree_deps.py` | 変更 | `DECLARATION_FILE` を `facts.setting_path` で引く（DC6） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/worktree_base.py` | 変更 | `WORKTREES` を `places` から引く（DC6） | `lib/facts.py` |
| `plugins/ndf/scripts/glossary.py` | 変更 | `DECLARATION` を `facts.setting_path` で引く（DC6） | `lib/facts.py` |
| `plugins/ndf/scripts/merged_lib/base_sync.py` | 変更 | `GLOSSARY_DECL` を同上（DC6） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/diff_kinds.py` | 変更 | `GLOSSARY_DECL` を同上（DC6） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/assignee_env.py` | 変更 | `NAME_ENV`・`KEY_NAME_ENV`・`BACK_ENV` を消し、持ち主を import する（DC6） | `lib/claude_accounts.py`・`lib/claude_api_keys.py` |
| `plugins/ndf/scripts/pr-steps.py` | 変更 | `CHANGES_HEADING`・`MIGRATION_HEADING` を `pr-body.json` から引く（DC6） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/pr_mode.py` | 変更 | PR 本文の印を `pr-body.json` から引く（DC6） | `lib/facts.py` |
| `plugins/ndf/scripts/instructions_lib/declaration.py` | 変更 | `DEFAULT_FILES` を表から導く（DC6） | `lib/facts.py` |
| `plugins/ndf/scripts/project_lib/fingerprint.py` | 変更 | `INPUT_NAMES` の指示書を表から導く（DC6） | `lib/facts.py` |
| `plugins/ndf/scripts/project_lib/measure_repo.py` | 変更 | `INSTRUCTION_FILES` を表から導く（DC6） | `lib/facts.py` |
| `plugins/ndf/scripts/project_lib/mvv_collect.py` | 変更 | 同上（DC6） | `lib/facts.py` |
| `plugins/ndf/scripts/tests/test_places_and_forms.py` | 新設 | 置き場・指示書・PR 本文の見出しを引く読み手の単体テスト（DC6） | 上の読み手 |
| `plugins/ndf/contracts/facts/branches.json` | 新設 | ブランチの接頭辞の表（DC7） | 無し |
| `plugins/ndf/contracts/facts/gates.json` | 新設 | 承認ゲートとラベルの表（DC7） | 無し |
| `plugins/ndf/contracts/workflow/stages/` | 変更 | 工程の文書の `metadata.ndf.phase` にフェーズを書く（#3092 の文書）（DC7） | `plugins/ndf/scripts/lib/workflow_def.py` |
| `plugins/ndf/scripts/lib/workflow_def.py` | 変更 | 閉じた語彙 `VOCABULARY` の `metadata.ndf` のキーに `phase` を足す（#3191 が新設。語彙に無いキーは登録で断られる）（DC7） | 無し |
| `plugins/ndf/contracts/workflow/paces.json` | 新設 | 進め方の表（#3092 の置き場）（DC7） | 無し |
| `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh` | 変更 | `WF_DESIGN_PREFIX`・`WF_APPROVAL_LABEL`・`WF_PACES` を `lib/facts.sh` で引く。`WF_STAGE_MATRIX`・`WF_MODES`・`WF_MODE_HEIGHT` は #3092 が消す（DC7） | `lib/facts.sh` |
| `plugins/ndf/scripts/lib/transcript_agents.py` | 変更 | `STEP_PHASES`・`POSTS` を工程の文書から導く（DC7） | `lib/workflow_def.py`（#3092） |
| `plugins/ndf/scripts/hook_lib/token_guard.py` | 変更 | `STAGE_PREFIXES` を工程の文書のフェーズから導く（DC7） | `lib/workflow_def.py` |
| `plugins/ndf/scripts/lib/pace.py` | 変更 | `DEFAULT_MODES` をモードの文書から導く（DC7） | `lib/workflow_def.py` |
| `plugins/ndf/scripts/glossary.py` | 変更 | `DESIGN_MODES` を工程「設計」を持つモードとして導く（DC7） | `lib/workflow_def.py` |
| `plugins/ndf/scripts/lib/branch_flow.py` | 変更 | `SPRINT_PREFIX`・`RELEASE_PREFIX` を `branches` から引く（DC7） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/pr_mode.py` | 変更 | `SPRINT_PREFIX` を `branches` から引く（DC7） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/legacy_names.py` | 変更 | `BRANCH_PREFIXES` の新しい側を `branches` から引く（DC7） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/gate_rejections.py` | 変更 | 承認ゲートの名前を `gates` から引く（DC7） | `lib/facts.py` |
| `plugins/ndf/scripts/mvv-gate.py` | 変更 | `GATES` のキーを `gates` から引く（DC7） | `lib/facts.py` |
| `plugins/ndf/scripts/supervise_lib/design_merge.py` | 変更 | `LABEL` を `gates` の列 `label` から引く（DC7） | `lib/facts.py` |
| `plugins/ndf/scripts/check-trigger.py` | 変更 | `SKIP_BRANCHES` の `release/` を `branches` から引く（DC7） | `lib/facts.py` |
| `plugins/ndf/scripts/pr-body-decisions.sh` | 変更 | `DESIGN_BRANCH_PREFIX` を `lib/facts.sh` で引く（DC7） | `lib/facts.sh` |
| `plugins/ndf/scripts/sprint-state.py` | 変更 | `PACES` を `paces` から引く（DC7） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/sprint_mvv.py` | 変更 | `MVV_PACES` を列 `mvv_gate` から導く（DC7） | `lib/facts.py` |
| `plugins/ndf/skills/cross-review/scripts/classifications.py` | 変更 | `DESIGN_BRANCH_PREFIX` を `branches` から引く（DC7） | `lib/facts.py` |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md` | 変更 | 工程とフェーズの表を消し、工程の文書を指す（DC7） | 無し |
| `plugins/ndf/scripts/tests/test_stage_phases.py` | 新設 | 工程の文書から導くフェーズ・モード・接頭辞の単体テスト（DC7） | 上の読み手 |
| `plugins/ndf/scripts/lib/sprint_loop_record.py` | 変更 | `TOOL` を `sprint-state.py` の持ち主から import する（DC8、#2994 の後） | `plugins/ndf/scripts/sprint-state.py` の `TOOL` の持ち主 |
| `plugins/ndf/scripts/release_lib/others.py` | 変更 | PR 本文の見出しを `pr-body.json` から引く（DC8、#2995 の後） | `lib/facts.py` |
| `plugins/ndf/scripts/release_lib/carry.py` | 変更 | `CONSENT_HEADING` を同上（DC8） | `lib/facts.py` |
| `plugins/ndf/scripts/release_lib/materials.py` | 変更 | `RUNTIME_NAMES` を列 `label` から導く（DC8） | `lib/facts.py` |
| `plugins/ndf/scripts/lib/parallel_switch.py` | 変更 | `TAIL_LINES` を方針から引く（DC8、#2992 の後） | `lib/limits.py` |
| `plugins/ndf/scripts/lib/parallel_lane.py` | 変更 | `RUNNING` を `lib/durable.py` から import する（DC8） | `lib/durable.py` |
| `plugins/ndf/scripts/lib/parallel_ledger.py` | 変更 | `RUNNING`・`LOST` を `lib/durable.py` から導く（DC8） | `lib/durable.py` |
| `plugins/ndf/scripts/lib/durable.py` | 変更 | `RUNNING`・`FINISHED` を持ち主として公開する（DC8、#2990 の後） | 無し |
| `plugins/ndf/scripts/supervise_lib/decl.py` | 変更 | `GLOSSARY_DECL` を `facts.setting_path` で引く（DC8、#2993 の後）。DC6 が テストのコマンドを `.ndf/project.json` の `test.suites` から読む（#2011） | `lib/facts.py` |
| `plugins/ndf/scripts/tests/test_owners_after_children.py` | 新設 | DC8 の読み手が持ち主から引く値の単体テスト（DC8） | 上の読み手 |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/scope.py` | 変更 | 範囲の種類を表から引き、関数単位の範囲を組む（DC4、#2557） | 無し |
| `plugins/ndf/scripts/project-decl.py` | 変更 | 計測値を追跡しない置き場へ分けて書く（DC6、#2010） | 無し |

## クラスと関数の構造

型の関係は設計文書の「構成要素」の図を指し、ここでは描き直さない。DC3〜DC8 の読み手は、最上位の定数の代入を表からの導出に置き換えるだけで、関数のシグネチャ・責務を変えないものは名前を挙げない（下の表の「最上位の代入」）。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/facts.py` | モジュール `facts`・型 `Table`・例外 `FactsError` | `contract_path`・`use_dir`・`table`・`rows`・`ids`・`row`・`column`・`setting_path`・`_validate`・`_parse_where`・`main`（すべて新設） |
| `plugins/ndf/scripts/lib/facts.sh` | シェルの関数 | `ndf_fact_ids`・`ndf_fact_value`（新設） |
| `plugins/ndf/scripts/lib/limits.py` | モジュール `limits` | 最上位の代入 `DECLARATION`（変更） |
| `plugins/ndf/scripts/lib/doc_kinds.py` | モジュール `doc_kinds` | 最上位の代入 `DECLARATION`（変更） |
| `scripts/contracts_lib/const_facts.py` | モジュール `const_facts`・型 `Const`・`Group` | `lit`・`py_consts`・`sh_consts`・`test_scan`・`scan`・`groups`・`table_hits`・`group_key`・`measure`（すべて新設。`lit` ほか 5 つは `scripts/measure/const-facts.py` から移す） |
| `scripts/contracts_lib/const_baseline.py` | モジュール `const_baseline`・例外 `BaselineError` | `load`・`compare`・`plan_violations`・`violations`（新設） |
| `scripts/contracts_lib/facts_decl.py` | モジュール `facts_decl` | `load_tables`・`row_violations`・`ref_violations`・`references`・`violations`・`render_section`（新設） |
| `scripts/contracts_lib/limits_decl.py` | モジュール `limits_decl` | `_Calls.__init__`・`_aliases`（変更: 名前を引数で受ける） |
| `scripts/contracts_lib/ratchet.py` | モジュール `ratchet` | `rows_added`（新設） |
| `scripts/contracts_lib/render.py` | モジュール `render` | `render`（変更） |
| `scripts/check-ndf-contracts.py` | `Checker`・モジュール | `Checker.run`（変更）・`_baseline_ratchet`（新設）・`main`（変更） |
| `scripts/measure/const-facts.py` | エントリポイント | `main`（変更）。`lit`・`py_consts`・`sh_consts`・`test_scan`・`scan`・`groups` は消す（コアへ移す） |
| `plugins/ndf/skills/cross-review/scripts/review_lib/participants.py` | モジュール | `_round_reviewers`・`_no_result_agents`・`_round_passes`（変更）。`LEGACY_AGENTS`・`AGENTS` は消す |
| `plugins/ndf/skills/cross-review/scripts/review_lib/commands/report.py` | モジュール | `_print_round_summary`（変更） |
| `plugins/ndf/scripts/lib/assignment.py` ほか DC3 の 13 本 | モジュール | 最上位の代入だけ（ファイル配置の表の役割の列） |
| DC4〜DC8 の読み手 | モジュール | 最上位の代入だけ（同上）。`review_lib/ci.py` は `CI_FAILED_CONCLUSIONS`・`CHECK_RUNS_PER_PAGE`・`CHECK_RUNS_MAX_PAGES` を消して `gh_checks` の名前を使う |
| `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh` | シェル | 最上位の代入 `WF_DESIGN_PREFIX`・`WF_APPROVAL_LABEL`・`WF_PACES`（変更） |
| `plugins/ndf/scripts/lib/worktree-common.sh` | シェル | 最上位の代入 `WT_DECLARATION_FILE`・`WT_DECLARATION_LOCAL_FILE`・`WT_WORKTREE_DIR`（変更） |


## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `facts.contract_path` | `plugins/ndf/scripts/lib/facts.py` | 新設 | `contract_path(rel: str) -> Path` | 契約のファイルの置き場を、リリースしたプラグインとバージョンディレクトリの 2 つの候補から決める | `rel` は `contracts/` からの相対 | あるほうの候補を返す。どちらにも無ければプラグインの候補を返す（読む側が無いことを報告する） | ファイルの有無を見る |
| `facts.use_dir` | `plugins/ndf/scripts/lib/facts.py` | 新設 | `use_dir(path: Path \| None) -> None` | 表の置き場を差し替える（テスト用）。`None` でプラグインの置き場へ戻す | 無し | 以後の `table` は `path` の下を読み、キャッシュを空にする | 無し |
| `facts.table` | `plugins/ndf/scripts/lib/facts.py` | 新設 | `table(name: str) -> Table` | 表 `facts/<name>.json` を 1 プロセスに 1 回読んで返す | `name` は表の名前（`^[a-z][a-z0-9-]*$`） | 同じ `name` の 2 回目からはキャッシュを返す。返す `Table` は `_validate` を通っている | ファイルを読む |
| `facts.rows` | `plugins/ndf/scripts/lib/facts.py` | 新設 | `rows(name: str, **where) -> tuple[dict, ...]` | 条件（列 = 値）に合う行を表の並びで返す | `where` のキーは表の列 | 返す行はすべて `where` の各列の値に等しい。並びは表の `rows` の並び | ファイルを読む（`table` を通す） |
| `facts.ids` | `plugins/ndf/scripts/lib/facts.py` | 新設 | `ids(name: str, **where) -> tuple[str, ...]` | 条件に合う行の `id` を表の並びで返す | 同上 | `ids(n, **w) == tuple(r["id"] for r in rows(n, **w))` | ファイルを読む（`table` を通す） |
| `facts.row` | `plugins/ndf/scripts/lib/facts.py` | 新設 | `row(name: str, id: str) -> dict` | `id` の行を返す | 無し | 返す行の `id` は引数の `id` | ファイルを読む（`table` を通す） |
| `facts.column` | `plugins/ndf/scripts/lib/facts.py` | 新設 | `column(name: str, col: str) -> dict[str, Any]` | 列 `col` を持つ行の `id` → 値の対応を返す | `col` は表の列 | 列の値を持たない行（任意の列）は含まない。並びは表の並び | ファイルを読む（`table` を通す） |
| `facts.setting_path` | `plugins/ndf/scripts/lib/facts.py` | 新設 | `setting_path(name: str) -> str` | `.ndf/` の設定ファイルの相対パスを、置き場の表と設定の契約から作る | `name` は `contracts/settings/<name>.json` がある設定 | `places` の `ndf` の `path` と設定の契約の `file` を `/` でつないだ文字列を返す | ファイルを読む |
| `facts._validate` | `plugins/ndf/scripts/lib/facts.py` | 新設 | `_validate(raw: Any, where: str) -> Table` | 実行時に要る最小の形（`version`・`rows` の各行の文字列の `id`）だけを確かめる | 無し | 返す `Table` の `rows` は dict の並びで、`id` は文字列 | 無し |
| `facts._parse_where` | `plugins/ndf/scripts/lib/facts.py` | 新設 | `_parse_where(args: list[str]) -> dict[str, Any]` | シェルから渡された `列=値` を条件に直す（`true`・`false` は真偽、数字だけは整数、ほかは文字列） | 各要素が `=` を含む | キーは `=` の左、値は右の解釈 | 無し |
| `facts.main` | `plugins/ndf/scripts/lib/facts.py` | 新設 | `main(argv: list[str]) -> int` | 副命令 `ids <表> [列=値]...`・`value <表> <id> <列>` を標準出力へ改行区切りで返す | 無し | 終了コード 0 で値を出すか、1 で理由を標準エラーへ 1 行出す | ファイルを読む・標準出力 |
| `ndf_fact_ids` | `plugins/ndf/scripts/lib/facts.sh` | 新設 | `ndf_fact_ids <表> [<列>=<値>...]` | `facts.py ids` を 1 回呼び、値を改行区切りで標準出力へ出す | `python3` がある | 成功なら 0 と値、失敗なら 1 と標準エラーの理由 | 外部コマンド（`python3`） |
| `ndf_fact_value` | `plugins/ndf/scripts/lib/facts.sh` | 新設 | `ndf_fact_value <表> <id> <列>` | `facts.py value` を 1 回呼び、1 つの値を出す | 同上 | 同上 | 外部コマンド（`python3`） |
| `const_facts.lit` | `scripts/contracts_lib/const_facts.py` | 新設 | `lit(node: ast.AST) -> str \| int \| float \| tuple[str, ...] \| dict \| None` | 定数の右辺を文字列・数・文字列の集まり・文字列をキーに持つ dict として読む | 無し | 読めない形は `None` | 無し |
| `const_facts.py_consts` | `scripts/contracts_lib/const_facts.py` | 新設 | `py_consts(tree: ast.Module, rel: str) -> list[Const]` | Python のモジュールの最上位の大文字の定数を拾う | `tree` は解析済み | 返す `Const` は `lit` が読めた値だけ | 無し |
| `const_facts.sh_consts` | `scripts/contracts_lib/const_facts.py` | 新設 | `sh_consts(text: str, rel: str) -> list[Const]` | シェルの最上位の大文字の代入（配列・タブ区切りの表を含む）を拾う | 無し | 値に `$` を含む代入は拾わない | 無し |
| `const_facts.test_scan` | `scripts/contracts_lib/const_facts.py` | 新設 | `test_scan(tree: ast.Module, rel: str, by_set: dict, names: set[str]) -> list[Group]` | テストの一致の確認・定義の文字列の読み・値のコピーを拾う | `by_set` は `groups` が返したもの | 返す組の種類は `test-bind`・`test-parse`・`test-literal` | 無し |
| `const_facts.scan` | `scripts/contracts_lib/const_facts.py` | 新設 | `scan(root: Path, parse: Callable[[Path], ast.Module \| None]) -> tuple[list[Const], list[tuple[ast.Module, str]]]` | `plugins/ndf` の下の定数とテストの構文木を集める | `parse` は #2996 の解析のキャッシュ（解析できないファイルは `None`） | テストと `experimental/` と `.worktrees/` の定数を含まない | ファイルを読む |
| `const_facts.groups` | `scripts/contracts_lib/const_facts.py` | 新設 | `groups(consts: list[Const]) -> tuple[list[Group], dict]` | 定数から `same-set`〜`same-num` の組を作る | 無し | 返す組は 2 つ以上のファイルにまたがる | 無し |
| `const_facts.table_hits` | `scripts/contracts_lib/const_facts.py` | 新設 | `table_hits(consts: list[Const], tables: dict[str, tuple[str, ...]]) -> list[Group]` | 表の `id` を 2 つ以上含む集まり（`table-set`）と、4 文字以上で表の `id` に等しい文字列（`table-str`）を拾う（I-DT1） | `tables` は表の名前 → `id` の並び | 組の `values` は当たった `id`、`at` は定数 1 つ | 無し |
| `const_facts.group_key` | `scripts/contracts_lib/const_facts.py` | 新設 | `group_key(g: Group) -> str` | 行番号を含まない組のキーを作る | 無し | `<種類>\|<ファイル>:<名前>` を `\|` で辞書順に並べた文字列。テストの組は `<種類>\|<ファイル>\|<名前の並び>` | 無し |
| `const_facts.measure` | `scripts/contracts_lib/const_facts.py` | 新設 | `measure(root: Path, parse) -> dict` | 計測のスクリプトの結果（`items`・`metrics`・`summary`）を作る | 無し | 今の `scripts/measure/const-facts.py --json` と同じ形と同じ数 | ファイルを読む（`scan` を通す） |
| `const_baseline.load` | `scripts/contracts_lib/const_baseline.py` | 新設 | `load(path: Path) -> list[dict]` | 基準のファイルを読み、行のキーの形を確かめる | 無し | 返す行はすべて `key`・`kind`・`fact`・`plan` を文字列で持つ | ファイルを読む |
| `const_baseline.compare` | `scripts/contracts_lib/const_baseline.py` | 新設 | `compare(found: Counter[str], rows: list[dict]) -> tuple[list[str], list[dict]]` | 今の組のキーと基準の行を多重集合として突き合わせる | 無し | 1 つ目は基準より多いキー（新しい重複）、2 つ目は今のツリーより多い基準の行（使われない行） | 無し |
| `const_baseline.plan_violations` | `scripts/contracts_lib/const_baseline.py` | 新設 | `plan_violations(rows: list[dict]) -> list[dict]` | 扱いの形（`移す: DC<1〜8>` か `移さない: <空でない理由>`）を確かめる（I-DT5）。移す先は子 Issue の番号で書き、設計文書の移行ステップ（`DT<番号>`）は受けない | 無し | 形の違う行ごとに 1 件 | 無し |
| `const_baseline.violations` | `scripts/contracts_lib/const_baseline.py` | 新設 | `violations(root: Path, parse) -> list[tuple[str, str, str, str]]` | 規則 `const-fact` の違反（新しい重複・使われない行・扱いの無い行）を `(種類, パス, 名前, 理由)` で返す | 無し | 違反の `name` は組のキー | ファイルを読む |
| `facts_decl.load_tables` | `scripts/contracts_lib/facts_decl.py` | 新設 | `load_tables(root: Path) -> tuple[dict[str, dict], list[tuple]]` | `contracts/facts/*.json` を読み、`fact.schema.json` で形を確かめる | `jsonschema` が import できる | 形の通った表だけを返し、通らない表は違反にする | ファイルを読む |
| `facts_decl.row_violations` | `scripts/contracts_lib/facts_decl.py` | 新設 | `row_violations(name: str, t: dict) -> list[tuple]` | 列の宣言から行のスキーマを作り、各行を確かめ、`id` の重なりを見る（I-DT2） | `t` は `load_tables` を通った表 | 違反の `name` は `<表>:<id>` | 無し |
| `facts_decl.ref_violations` | `scripts/contracts_lib/facts_decl.py` | 新設 | `ref_violations(root: Path, tables: dict) -> list[tuple]` | 列の `ref`（`limits.json#<接頭辞>` か `facts/<表>`）の先に値があるかを見る（I-DT3） | 無し | 相手に無い値ごとに 1 件 | ファイルを読む |
| `facts_decl.references` | `scripts/contracts_lib/facts_decl.py` | 新設 | `references(root: Path, files: list[str], parse) -> dict[str, list[str]]` | `facts.ids("<表>")` などの呼び出しと `ndf_fact_ids <表>` から、表 → 読む関数を集める | 無し | 名前を文字列で書いていない呼び出しはキー `None` に入る | ファイルを読む |
| `facts_decl.violations` | `scripts/contracts_lib/facts_decl.py` | 新設 | `violations(root: Path, files: list[str], parse) -> list[tuple[str, str, str, str]]` | 規則 `fact-table` の違反（形・重なり・外部キー・文字列で書いていない表の名前・読まれない表）を返す | 無し | 違反は `(fact-table, パス, <表>:<行>, 理由)` | ファイルを読む |
| `facts_decl.render_section` | `scripts/contracts_lib/facts_decl.py` | 新設 | `render_section(root: Path, files: list[str], parse) -> list[str]` | 逆引きの節「事実の表」（表・説明・持ち主・読む関数）の行を作る | 無し | 表の名前の辞書順 | ファイルを読む |
| `limits_decl._Calls.__init__` | `scripts/contracts_lib/limits_decl.py` | 変更 | `__init__(self, aliases: set[str], consts: dict[str, str], calls: tuple[str, ...] = CALLS)` | 集める関数の名前を引数で受ける（既定は今の `policy`・`seconds`・`value`） | 無し | `facts_decl` が `ids`・`rows`・`row`・`column` を渡して同じ走査を使える | 無し |
| `limits_decl._aliases` | `scripts/contracts_lib/limits_decl.py` | 変更 | `_aliases(tree: ast.Module, module: str = "limits") -> set[str]` | import の名前を引数で受ける | 無し | `module` を import した別名の集合を返す | 無し |
| `ratchet.rows_added` | `scripts/contracts_lib/ratchet.py` | 新設 | `rows_added(root: Path, sha: str, rel: str, now: list[dict], same: Callable[[dict], tuple]) -> list[dict]` | 1 ファイルの基準の行が merge-base より増えた分を返す。消した行と `same` が等しい行は置き換えとして数えない（I-DT4c） | `sha` は `base_ref` が返した merge-base | merge-base にファイルが無ければ空 | 外部コマンド（`git show`） |
| `render.render` | `scripts/contracts_lib/render.py` | 変更 | `render(c: Contracts, root: Path \| None = None, parse=None) -> str` | 逆引きに「事実の表」の節を足す | 無し | `root` があれば「上限の宣言」の後に「事実の表」を置く | ファイルを読む |
| `Checker.run` | `scripts/check-ndf-contracts.py` | 変更 | `run(self) -> tuple[list[dict], dict]` | 既存の違反に `const-fact` と `fact-table` を足す | 無し | `metrics` に `fact_tables`・`const_groups`・`baseline_rows` を足す | ファイルを読む |
| `_baseline_ratchet` | `scripts/check-ndf-contracts.py` | 新設 | `_baseline_ratchet(root: Path, base: str, state: str) -> list[dict]` | 基準の行の増加を `const-fact` の違反にする | `state == "checked"` のときだけ比べる | merge-base の検査が `const-fact` を知らなければ空 | 外部コマンド（`git`） |
| `main` | `scripts/check-ndf-contracts.py` | 変更 | `main(argv: list[str] \| None = None) -> int` | 検査の経路と `--render` の経路の前で `deps.require("jsonschema")` を呼ぶ（`--render` も `facts_decl.render_section → load_tables` で同じスキーマ検査を使うため。`--draft` は表を読まないので呼ばない） | 無し | 終了コードの意味は変えない（0・1・2・3。依存の欠けは `deps` の 69） | 外部コマンド（`uv` で起動し直す） |
| `main` | `scripts/measure/const-facts.py` | 変更 | `main() -> None` | 引数を読み、`const_facts.measure` の結果を今と同じ形で出す | 無し | `--json` の出力の形と数が変わらない | ファイルを読む・標準出力 |
| `_round_reviewers` | `plugins/ndf/skills/cross-review/scripts/review_lib/participants.py` | 変更 | `_round_reviewers(st: dict, round_no: int) -> list[str]` | ラウンドの担当を、記録・`only`・`participants` の順で決める。`host` だけの状態と何も無い状態の分岐を消す | 状態ファイルは `participants` を持つ | `participants` も `only` もラウンドの記録も無ければ `AssignmentError` | 無し |
| `_no_result_agents` | `plugins/ndf/skills/cross-review/scripts/review_lib/participants.py` | 変更 | `_no_result_agents(round_entry: dict, only: str \| None, reviewers: list[str]) -> list[str]` | 結果の無い担当を返す。`reviewers` を必須にし、`AGENTS` への戻りを消す | 呼ぶ側は `_round_reviewers` の値を渡す | 返す担当は `reviewers` の部分集合 | 無し |
| `_round_passes` | `plugins/ndf/skills/cross-review/scripts/review_lib/participants.py` | 変更 | 今の引数のうち `reviewers: list[str]` を必須にする | ラウンドが通ったかを担当ごとの判定で決める。`AGENTS` への戻りを消す | 同上 | 判定は `reviewers` の担当だけから決まる | 無し |
| `_print_round_summary` | `plugins/ndf/skills/cross-review/scripts/review_lib/commands/report.py` | 変更 | `_print_round_summary(rounds: list) -> None` | ラウンドの `reviewers` が無い記録を `LEGACY_AGENTS` で埋めず、担当の列を `—` にする | 無し | 出力の列の数は変えない | 標準出力 |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `facts.contract_path` | どちらの候補にも無い | リリース対象の欠け | `Path.is_file` | 既定値で続ける（プラグインの候補を返し、読む側が `FactsError` にする） | 無し | 無し。リリース対象を直して再実行 |
| `facts.table` | ファイルが無い・JSON でない | リリース対象の欠け・手での編集の誤り | `open`・`json.loads` | 止める（`FactsError`。理由に表のパス） | 読み手のモジュールの import で上がり、エントリポイントはスタックトレースと理由を出して終了コード 1。表はリリース対象の一部で、壊れた表は CI の `fact-table` が先に落とす。既定値で続けない（値の欠けた読み手が黙って一部を外す） | 無し。表を直して再実行 |
| `facts.table` | 形が違う（`version` が 1 でない・行に `id` が無い） | 同上 | `_validate` | 止める（`FactsError`） | 同上 | 同上 |
| `facts.rows` | `where` の列が表に無い | 読み手の書き誤り | `table.columns` との照合 | 止める（`FactsError`。表と列の名前） | 同上 | 読み手を直す。CI の `fact-table` は文字列の列名を検査しないため、単体テストが拾う |
| `facts.ids` | `rows` と同じ | 同上 | `rows` | 呼び出し元へ返す（`FactsError` をそのまま） | 同上 | 同上 |
| `facts.row` | `id` の行が無い | 読み手の書き誤り・表の行の削除 | 行の照合 | 止める（`FactsError`） | 同上 | 同上 |
| `facts.column` | 列が表に無い | 読み手の書き誤り | `table.columns` との照合 | 止める（`FactsError`） | 同上 | 同上 |
| `facts.setting_path` | 設定の契約が無い・`places` に `ndf` が無い | リリース対象の欠け・設定名の誤り | `open`・`row` | 止める（`FactsError`） | 同上 | 同上 |
| `facts.main` | 副命令・引数の誤り・`FactsError` | 呼ぶシェルの誤り・表の誤り | `argparse` と各関数 | 止める | 標準エラーに理由 1 行、終了コード 1（`limits.py` の CLI と同じ） | 無し。再実行できる |
| `ndf_fact_ids` | `python3` が無い・`facts.py` が 1 で終わる | 環境・表の誤り | 子のプロセスの終了コード | 呼び出し元へ返す（`return 1`） | 標準エラーに `facts.py` の理由。source する側（`workflow-common.sh`・`worktree-common.sh`）は理由を出して source を `return 1` で終え、hook は終了コード 1（Claude Code では操作を止めない誤り） | 無し。直して再実行 |
| `ndf_fact_value` | 同上 | 同上 | 同上 | 同上 | 同上 | 同上 |
| `const_facts.scan` | 解析できないファイル | 構文の誤り | `parse` が `None` を返す | 既定値で続ける（そのファイルを飛ばす。構文の誤りは `ruff` が落とす） | 無し | 無し |
| `const_facts.scan` | 読めないファイル | 権限・消えたファイル | `read_text` の `OSError` | 既定値で続ける（飛ばす） | 無し | 無し |
| `const_facts.measure` | `scan` と同じ | 同上 | `scan` | 既定値で続ける | 無し | 無し |
| `const_baseline.load` | ファイルが無い・JSON でない・行の形が違う | 手での編集の誤り | `open`・`json.loads`・キーの照合 | 止める（`BaselineError`） | `check-ndf-contracts.py` が `stopped` と理由、終了コード 2 | 基準のファイルを直して再実行 |
| `const_baseline.violations` | `load` の失敗 | 同上 | `load` | 呼び出し元へ返す（`BaselineError`） | 同上 | 同上 |
| `facts_decl.load_tables` | 表が JSON でない・スキーマに合わない | 表の書き誤り | `json.loads`・`jsonschema.Draft202012Validator` | 既定値で続ける（その表を外し、`fact-table` の違反にする） | 違反の一覧、終了コード 1 | 表を直して再実行 |
| `facts_decl.load_tables` | `fact.schema.json` が無い・スキーマとして誤り | リリース対象の欠け | `open`・`check_schema` | 止める（`ContractError`） | `stopped`、終了コード 2 | 直して再実行 |
| `facts_decl.ref_violations` | `limits.json` を読めない | 宣言の誤り | `limits_decl.load_policies` の理由 | 既定値で続ける（外部キーの違反を出さず、`limits-shape` に任せる） | `limits-shape` の違反、終了コード 1 | 同上 |
| `facts_decl.references` | 解析できない・読めないファイル | 構文の誤り | `parse` の `None`・`OSError` | 既定値で続ける（飛ばす） | 無し | 無し |
| `facts_decl.violations` | `load_tables` の止める失敗 | 上と同じ | `load_tables` | 呼び出し元へ返す | 上と同じ | 上と同じ |
| `facts_decl.render_section` | 表を読めない | 表の書き誤り | `load_tables` | 既定値で続ける（節に「表を読めない: <理由>」を書く。`limits_decl.render_section` と同じ） | 逆引きの節に理由 | 表を直して `--render` |
| `ratchet.rows_added` | merge-base に基準のファイルが無い・JSON でない | 基準を足す変更そのもの | `git show` の失敗・`json.loads` | 既定値で続ける（増加を数えない） | 無し | 無し |
| `render.render` | `facts_decl.render_section` と同じ | 同上 | 同上 | 既定値で続ける | 同上 | 同上 |
| `Checker.run` | `BaselineError`・`ContractError` | 上と同じ | `const_baseline.violations`・`facts_decl.violations` | 呼び出し元へ返す | `main` が `stopped`、終了コード 2 | 直して再実行 |
| `_baseline_ratchet` | `git` が起点を決められない | 浅いクローン・起点の無いブランチ | `ratchet.base_ref` の `RatchetError` | 呼び出し元へ返す（CI の中では止める、手元では飛ばす。今の `_ratchet_and_core` と同じ） | CI は `stopped`、終了コード 3 | `fetch-depth: 0` で取り直す |
| `main` | `jsonschema` を用意できない | uv が無い・ネットワークが無い | `deps.require` | 止める | `deps` の文面と終了コード 69 | 手で入れて再実行 |
| `_print_round_summary` | ラウンドの記録に `reviewers` が無い | `participants` を持つ前の書き手が作った状態ファイル | 行の読み | 既定値で続ける（担当の列を `—`） | 要約の表の 1 行 | 無し |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `const_baseline.compare` | キーの今の数 > 基準の行の数 | 差の数だけ「新しい重複」（I-DT4a） |
| `const_baseline.compare` | キーの今の数 < 基準の行の数 | 差の数だけ「使われない行」（I-DT4b） |
| `const_baseline.compare` | 等しい | 違反なし |
| `const_baseline.plan_violations` | `plan` が `移す: DC<1〜8>` | 通す |
| `const_baseline.plan_violations` | `plan` が `移さない: ` の後に 1 文字以上 | 通す |
| `const_baseline.plan_violations` | それ以外（空・別の書式・`移す:` の後が `DC<1〜8>` でない。`移す: DT2` も含む） | 「扱いの無い行」（I-DT5） |
| `ratchet.rows_added` | 今の行のキーが merge-base にある（多重集合で数える） | 数えない |
| `ratchet.rows_added` | 無い。merge-base から消えた行に `same`（種類・事実・ファイルを除いた定数の名前の並び）が等しい行がある | 置き換えとして数えず、消えた側を 1 つ減らす（I-DT4c） |
| `ratchet.rows_added` | 無い。置き換えにも当たらない | 増えた行 |
| `_baseline_ratchet` | `state` が `initial` か `skipped` | 比べない |
| `_baseline_ratchet` | merge-base の検査が `"const-fact"` を知らない（`ratchet.base_knows`） | 比べない（基準を入れる DC2 そのもの） |
| `_baseline_ratchet` | それ以外 | `rows_added` の結果を `const-fact` の違反（`baseline-added`）にする |
| `facts._parse_where` | 値が `true` / `false` | 真偽 |
| `facts._parse_where` | 値が `-?[0-9]+` | 整数 |
| `facts._parse_where` | それ以外 | 文字列（空の文字列を含む） |
| `facts.rows` | `where` が空 | 全行 |
| `facts.rows` | 列が任意で、行がその列を持たない | その行は条件に合わない |
| `facts_decl.ref_violations` | `ref` が `limits.json#<接頭辞>` | `<接頭辞><値>` が方針にあるか。列が `id` なら行の `id` を値にする |
| `facts_decl.ref_violations` | `ref` が `facts/<表>` | 値が相手の表の `id` にあるか。相手の表が無ければ 1 件の違反 |
| `facts_decl.ref_violations` | それ以外の `ref` | スキーマの `pattern` が先に落とすため来ない |
| `_round_reviewers` | ラウンドの記録に `reviewers` があり、判定済みか宣言の内 | その値 |
| `_round_reviewers` | `only` がある | `[only]` |
| `_round_reviewers` | `participants` がある | 輪番の席 |
| `_round_reviewers` | どれも無い | `AssignmentError`（状態ファイルを作り直す案内） |

- 繰り返しの終わる条件: `const_facts.groups` の組の列挙は定数の並び（有限）の 2 重の繰り返しで終わる。`rows_added` は今の行の並びを 1 回なめる
- 計算量の注意: `groups` の `subset`・`affix` は集まりを持つ定数の数 n に対して n² である（今の n は約 600、手元で `const-facts.py` 全体が 3.5 秒。`time python3 scripts/measure/const-facts.py --json`、2026-11-13）。`table_hits` は表の `id` を集合にして定数ごとに O(要素数)。検査の全体の時間は DC2 の CI で測り、#2996 の 1 回の解析の後の増分を PR に書く

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `Table` | 型（NamedTuple: `name`・`summary`・`owner`・`columns`・`rows`） | `plugins/ndf/scripts/lib/facts.py` | — |
| `FactsError` | 型（例外） | `plugins/ndf/scripts/lib/facts.py` | — |
| `FACTS_DIR` | 定数 | `plugins/ndf/scripts/lib/facts.py` | `"facts"`（`contract_path` の下の表の置き場） |
| `VERSION` | 定数 | `plugins/ndf/scripts/lib/facts.py` | `1`（表の形式の版。決定 28 と同じく NDF の版と切り離す。表ごとの `version` はこの値と一致する） |
| 表の共通の形 | 設定値 | `plugins/ndf/contracts/facts/fact.schema.json` | 最上位は `version`・`summary`・`owner`・`columns`・`rows` だけ（`additionalProperties: false`）。列の名前は `^[a-z][a-z0-9_]*$`、列の型は `boolean`・`string`・`integer`・`array`（文字列の配列）、任意の列は `optional: true`、`ref` は `^(limits\.json#[a-z.-]+\|facts/[a-z][a-z0-9-]*)$`。`rows` の各行は文字列の `id` を持つ |
| ランタイムの表 | 設定値 | `plugins/ndf/contracts/facts/runtimes.json` | 行は `claude`・`codex`・`agy`・`kiro` の順（輪番の再現性のため `assignment.ALL_RUNTIMES` の順を保つ）。列は `default_seat`（真: claude・codex・kiro）・`hook_events`（真: claude・codex・kiro）・`usage_observable`（真: claude・codex）・`delegate`（真: codex・agy）・`executable`（kiro だけ `kiro-cli`）・`label`（`Claude Code`・`Codex`・`Antigravity`・`Kiro`）・`host_env`（`HOST_ENV_HINTS` の環境変数名の配列）・`auth_probe`・`model_probe`・`model_args`（任意） |
| 収束ループのフェーズの表 | 設定値 | `plugins/ndf/contracts/facts/loop-phases.json` | 行は `limits.json` の `monitor.phase.<id>` の 11 個。列 `id` の `ref` は `limits.json#monitor.phase.`。列は `launch`（`phases.PHASE_NAMES` の 8 個）・`coding`（add-tests・implement・fix・final-fix・flaky-fix）・`fix`（fix・final-fix）・`alive`（plan・add-tests・implement・fix）・`reassign`・`relaunch_in_place`・`undo`・`resume_as`（任意。fix → verify、final-fix → final） |
| 指摘の重さの表 | 設定値 | `plugins/ndf/contracts/facts/severities.json` | 行は `critical`・`major`・`minor`・`nit`。列は `rank`（3・2・1・0）・`blocking`（critical・major）・`refactor`（critical・major・minor。cross-refactoring の尺度） |
| テストの印の表 | 設定値 | `plugins/ndf/contracts/facts/test-markers.json` | 列 `where`（`dir` か `name`）。値は今の `TEST_PATH_MARKERS`・`TEST_NAME_MARKERS` の和 |
| 置き場の表 | 設定値 | `plugins/ndf/contracts/facts/places.json` | `ndf` → `.ndf`、`worktrees` → `.worktrees`（列 `path`） |
| 指示書の表 | 設定値 | `plugins/ndf/contracts/facts/instruction-files.json` | `AGENTS.md`・`CLAUDE.md`・`KIRO.md`・`.claude/CLAUDE.md`・`GEMINI.md`・`.github/copilot-instructions.md`。列 `declared_default`（真: 先頭の 3 つ）・`undeclared_check`（真: AGENTS.md・CLAUDE.md）・`measure`・`mvv` |
| ブランチの接頭辞の表 | 設定値 | `plugins/ndf/contracts/facts/branches.json` | `sprint/`・`design/`・`release/`・`check/`。列 `kind`・`skip_trigger`（真: release/・check/） |
| 承認ゲートの表 | 設定値 | `plugins/ndf/contracts/facts/gates.json` | `design`（列 `label` = `design-approved`）・`release` |
| 進め方の表 | 設定値 | `plugins/ndf/contracts/workflow/paces.json`（#3092 の置き場） | `normal`・`fast`・`auto`。列 `mvv_gate`（真: fast・auto） |
| PR 本文の見出し | 設定値 | `plugins/ndf/contracts/forms/pr-body.json` | `changes` = `## 利用者向けの変化`・`migration` = `## 移行の手順`・`consent` = `## 同意を求めること` |
| 移した閾値 | 設定値 | `plugins/ndf/contracts/limits.json` | `output.tail-lines` 200・`record.days` 7・`error.excerpt` 300・`testenv.reclaim` 330・`review.body-match` は今の `BODY_MATCH_CHARS` の値・`owner.window` は今の `run_owner.WINDOW` の値（名前の最終形は DC5 で `limits.json` の命名に合わせる） |
| `Const` | 型（dict: `file`・`name`・`line`・`seq`・`str`・`num`） | `scripts/contracts_lib/const_facts.py` | 今の `measure/const-facts.py` の `const()` と同じ形 |
| `Group` | 型（dict: `kind`・`values`・`at` か `file`・`line`・`names`） | `scripts/contracts_lib/const_facts.py` | 同上 |
| `MIN_STR`・`AFFIX_MIN` | 定数 | `scripts/contracts_lib/const_facts.py` | 4・6（今の値。`measure/const-facts.py` からは消す） |
| `KINDS` | 定数 | `scripts/contracts_lib/const_facts.py` | 9 種類（`same-set`〜`test-literal`）に `table-set`・`table-str` を足した 11 種類。計測の出力の並びもこれを使う |
| 基準の形 | 設定値 | `scripts/const-fact-baseline.json` | `{"version": 1, "rows": [{"key", "kind", "fact", "plan"}]}`。同じ `key` の行を複数持ってよい（多重集合） |
| `BASELINE` | 定数 | `scripts/check-ndf-contracts.py` | `"scripts/const-fact-baseline.json"` |
| `WHOLE` | 定数 | `scripts/check-ndf-contracts.py` | 今の値に `BASELINE` を足す（基準を変えたらツリー全体を数える） |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| hook・エントリポイント・レーンの supervise が同時に表を読む | `contracts/facts/*.json` | 取らない（読むだけ。書くのは PR だけ） | 読むだけで冪等 |
| 1 つのプロセスの中の複数の読み手 | `facts` のモジュールのキャッシュ | 取らない（スレッドから書き換えない。`use_dir` はテストだけが呼ぶ） | キャッシュは同じ内容を返す |
| 事実を移す子 Issue の PR が並列に流れる（DC3・DC4・DC6） | `scripts/const-fact-baseline.json`・`plugins/ndf/contracts/index.md` | 取らない。基準は行の削除だけで、行の並びをキーの辞書順に固定するため、別の行を消す 2 つの PR は git の 3 方向マージで衝突しない。`index.md` は生成物で、後からマージする PR が `--render` で作り直す | 生成は冪等。衝突したら `--render` と基準の行の削除をやり直す |
| DC2 の後に定数を別のファイルへ移す子 Issue（#2990〜#2995） | `scripts/const-fact-baseline.json` | 同上。行のファイルを書き換えるだけ（I-DT4c） | 同上 |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `facts.main` | 値（改行区切り）・失敗の理由 1 行 | 標準出力・標準エラー | 表の全体 |
| `ndf_fact_ids` | `facts.py` の標準エラーをそのまま | 標準エラー | 無し |
| `Checker.run` | `const-fact`・`fact-table` の違反（`kind`・`path`・`name`・`detail`） | 結果 JSON の `items`・標準エラーの 1 行ずつ（今の形） | 秘密を持つ値は無い（表は NDF の事実だけ。`contracts/env/` の値は読まない） |
| `main`（`check-ndf-contracts.py`） | `metrics.fact_tables`・`const_groups`・`baseline_rows` | 結果 JSON の `metrics` | 同上 |
| `main`（`measure/const-facts.py`） | 今と同じ一覧と 1 行 JSON | 標準出力 | 同上 |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `facts.contract_path` | `limits.DECLARATION`・`doc_kinds.DECLARATION` の 2 つの候補の探し方 | `grep -rn '_LIB_DIR.parents\[1\] / "contracts"' plugins/ndf/scripts/lib` が `limits.py`・`doc_kinds.py` の 2 ファイル（4 行） | 同じ探し方を `facts.contract_path` へ移し、2 つはそれを呼ぶ（置き場の探し方を 1 か所にする） |
| `facts.use_dir` | `limits.use`・`doc_kinds.use_contract` | `grep -n "^def use" plugins/ndf/scripts/lib/limits.py plugins/ndf/scripts/lib/doc_kinds.py` | 形を倣う。対象のファイルが違うため関数は分ける |
| `facts.table` | `limits.read_table`・`doc_kinds` の読み込み | `grep -n "^def read_table" plugins/ndf/scripts/lib/limits.py` | 使えない: 2 つは自分の宣言の形を検査する。1 プロセスに 1 回読む形（`functools.lru_cache` ではなく置き場ごとの dict のキャッシュ。`use_dir` で空にするため）を倣う |
| `facts.rows` | `pydantic`（`lib/schema.py`） | 設計文書の LIB3・`grep -n "pydantic" plugins/ndf/scripts/lib/schema.py` | 使えない: hook の経路で外部パッケージを読まない（I-DT6） |
| `facts.ids` | `facts.rows` | 同じモジュール | 使う |
| `facts.row` | `facts.rows` | 同じモジュール | 使う |
| `facts.column` | `facts.rows` | 同じモジュール | 使う |
| `facts.setting_path` | `scripts/contracts_lib/model.py` の `_load_settings` | `grep -n "_load_settings" scripts/contracts_lib/model.py` | 使えない: 検査の側（`scripts/`）で、プラグインから参照しない。読むのは設定の契約の `file` の 1 キーだけ |
| `facts._validate` | `doc_kinds._validate`・`limits.validate` | `grep -n "def _validate\|def validate" plugins/ndf/scripts/lib/doc_kinds.py plugins/ndf/scripts/lib/limits.py` | 使えない: 形が違う。最小の検査だけを持ち、全体の形は CI の `jsonschema` に任せる（決定 47） |
| `facts._parse_where` | `limits._parse_cli_options` | `grep -n "_parse_cli_options" plugins/ndf/scripts/lib/limits.py` | 使えない: 受ける引数の形が違う |
| `facts.main` | `limits.main`・`limits._run_cli` | `sed -n 308,343p plugins/ndf/scripts/lib/limits.py` | 形（副命令・終了コード 1・理由 1 行）を倣う |
| `ndf_fact_ids` | `launch-cli.sh` の `limits.py cli-timeout` の呼び方・`step-result.sh` の読み | `grep -n "limits.py" plugins/ndf/scripts/lib/launch-cli.sh`・`grep -n "IFS= read" plugins/ndf/scripts/lib/*.sh` | 呼び方を倣う。bash 3.2 に `mapfile` が無いため `while IFS= read -r` で読む（`step-result.sh` の 70 行の注記と同じ） |
| `ndf_fact_value` | `ndf_fact_ids` | 同じファイル | 使う（副命令だけが違う） |
| `const_facts.lit` | `scripts/measure/const-facts.py` の `lit` | `grep -n "^def " scripts/measure/const-facts.py` | 使う（移す） |
| `const_facts.py_consts` | 同 `py_consts` | 同上 | 使う（移し、解析を引数の構文木で受ける形に直す） |
| `const_facts.sh_consts` | 同 `sh_consts`・`sh_value`。`scripts/contracts_lib/extract_sh.py` | 同上・`grep -n "^def " scripts/contracts_lib/extract_sh.py` | `measure` 側を移す。`extract_sh.py` は引数と環境変数の読みを拾うもので、定数の代入を読まない |
| `const_facts.test_scan` | 同 `test_scan`・`names_in`・`bound` | 同上 | 使う（移す） |
| `const_facts.scan` | 同 `scan`・`is_test`。`limits_decl.code_files` | 同上・`grep -n "def code_files" scripts/contracts_lib/limits_decl.py` | `measure` 側を移す。`code_files` は `.sh` とテストを含まないため使えない |
| `const_facts.groups` | 同 `groups` | 同上 | 使う（移す） |
| `const_facts.table_hits` | `groups` の `member` の照合 | 同上 | 使えない: 照合の相手が定数でなく表の `id`。要素の集合を引く形だけを倣う |
| `const_facts.group_key` | `measure` の `ref`（行番号を含む） | `grep -n "^def ref" scripts/measure/const-facts.py` | 使えない: 行番号を含む。キーは行番号を外す |
| `const_facts.measure` | `measure/const-facts.py` の `main` の集計 | 同上 | 使う（集計を移し、`main` は出力だけを持つ） |
| `const_baseline.load` | `scripts/lib/allowlist.py` の `load_allow` | `grep -n "^def " scripts/lib/allowlist.py` | 使えない: 1 項目 1 ファイルの例外リストを読む。基準は 1 ファイルの行（決定 50） |
| `const_baseline.compare` | `collections.Counter` | 標準ライブラリ | 使う（多重集合の差） |
| `const_baseline.plan_violations` | 無し | `grep -rn "移さない:" scripts plugins/ndf/scripts` は `lib/state_checkout.py` の docstring の 1 件だけで、扱いの書式を読む部品は無い | 自前で書く（正規表現 1 つ） |
| `const_baseline.violations` | `limits_decl.violations` の戻り値の形 | `grep -n "def violations" scripts/contracts_lib/limits_decl.py` | 形 `(種類, パス, 名前, 理由)` を倣う |
| `facts_decl.load_tables` | `jsonschema` 4.26.0 の `Draft202012Validator` | 設計文書の LIB1・`uv run --frozen --project . python -c "import jsonschema"` は DC1 の後に実行する（今は依存に無い。`grep -n jsonschema pyproject.toml plugins/ndf/pyproject.toml` が 0 件） | 使う |
| `facts_decl.row_violations` | `jsonschema` | 同上 | 使う（列の宣言から行のスキーマを組んで検査する） |
| `facts_decl.ref_violations` | `limits_decl.load_policies` | `grep -n "def load_policies" scripts/contracts_lib/limits_decl.py` | 使う（方針の表を引く） |
| `facts_decl.references` | `limits_decl.references`・`_Calls` | `sed -n 70,113p scripts/contracts_lib/limits_decl.py` | 形を倣う。呼ぶ名前（`ids`・`rows`・`row`・`column`）と import の名前（`facts`）が違うため、`_Calls` に名前の組を渡せる形へ一般化して共有する（DC2 の中で `limits_decl._Calls` を引数付きにする） |
| `facts_decl.violations` | `limits_decl.violations` | 同上 | 形を倣う |
| `facts_decl.render_section` | `limits_decl.render_section` | 同上 | 形を倣う |
| `ratchet.rows_added` | `ratchet.added`・`_git`・`base_knows` | `sed -n 1,87p scripts/contracts_lib/ratchet.py` | `_git` と置き換えの数え方（消した数だけ相殺する `Counter`）を使う。`added` は 1 項目 1 ファイルの名前で比べるため、1 ファイルの行を比べる関数を足す |
| `_baseline_ratchet` | `_ratchet_and_core`・`ratchet.base_ref`・`ratchet.initial`・`ratchet.base_knows` | `sed -n 416,433p scripts/check-ndf-contracts.py` | 使う（起点と状態は `_ratchet_and_core` が決めたものを受ける） |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `facts.contract_path` | プラグインの置き場の表を返す | バージョンディレクトリの形（`lib/` と並ぶ `contracts/`）だけにあるときはそちら | 両方に無いときはプラグインの候補（続く `table` が `FactsError`） |
| `facts.table` | 一時ディレクトリの表を読む。2 回目は読まない（ファイルを消してもキャッシュを返す） | 行 0 件の表 | ファイルが無い・JSON でない・`version` が 2・行に `id` が無い でそれぞれ `FactsError`（理由に表のパス） |
| `facts.rows` | 真偽の列の条件で絞る・2 つの条件の組 | 条件なしで全行・任意の列を持たない行は合わない | 表に無い列で `FactsError` |
| `facts.ids` | 表の並びを保つ | 合う行 0 件で空の tuple | `rows` と同じ |
| `facts.row` | `id` の行 | — | 無い `id` で `FactsError` |
| `facts.column` | `id` → 値 | 任意の列で値の無い行を含まない | 無い列で `FactsError` |
| `facts.setting_path` | `worktree.json` で `.ndf/worktree.json` | — | 契約の無い設定名で `FactsError` |
| `facts._parse_where` | `host=true` が真偽、`rank=2` が整数 | `x=` が空の文字列 | `=` を持たない要素は `main` が終了コード 1 |
| `facts.main` | `ids runtimes default_seat=true` が改行区切り | 合う行 0 件で空の出力と終了コード 0 | 知らない副命令・表の誤りで終了コード 1 と理由 1 行 |
| `ndf_fact_ids` | `bash`（3.2 の構文だけ）で source して値の並びを配列へ読む | 値 0 件で空 | `python3` を `PATH` から外すと 1 を返す。表の誤りで 1 と理由 |
| `ndf_fact_value` | 1 つの値 | — | 無い `id` で 1 |
| `const_facts.lit` | 文字列・数・文字列の tuple・`frozenset([...])`・文字列をキーに持つ dict | 空の tuple は `None` | 真偽の値・式は `None` |
| `const_facts.py_consts` | 大文字の定数を拾う | 注釈付きの代入 | 小文字の名前・`lit` が読めない値を拾わない |
| `const_facts.sh_consts` | `$'a\tb'` の表の 1 列目・配列・複数行の引用 | `readonly`・`declare -r` の付いた代入 | `$` を含む値を拾わない |
| `const_facts.test_scan` | 2 つのモジュールの定数を確かめる assert を `test-bind` で拾う | 同じモジュールの 2 つの定数は拾わない | 構文の誤りのファイルは `scan` が飛ばす |
| `const_facts.scan` | テストと `experimental/` の定数を含まない | `.worktrees/` の下を含まない | 解析できないファイルを飛ばす |
| `const_facts.groups` | 2 つのファイルの同じ集合で `same-set`、部分で `subset`、接尾辞で `affix` | 同じファイルの 2 つの定数は組にしない | — |
| `const_facts.table_hits` | 表の `id` を 2 つ含む集まりで `table-set` | 3 文字の `id`（`agy`）に等しい文字列は拾わない（4 文字未満） | — |
| `const_facts.group_key` | 定数の行を 10 行ずらしてもキーが変わらない | `at` の並びを入れ替えても同じキー | — |
| `const_facts.measure` | 小さなツリーで数と種類ごとの組を返す。`main --json` の出力のキーが今と同じ | 組 0 件のツリー | — |
| `const_baseline.load` | 行を読む | 行 0 件 | 無いファイル・JSON でない・キーの欠けで `BaselineError` |
| `const_baseline.compare` | 同じ多重集合で違反なし | 同じキーが今 2・基準 1 で新しい重複 1 件、今 1・基準 2 で使われない行 1 件 | — |
| `const_baseline.plan_violations` | `移す: DC3`・`移さない: 別の事実（CI の結論と投稿キュー）` を通す | `移さない: ` の後が空白だけ | 空・`移す: DT2`・`あとで` で 1 件ずつ |
| `const_baseline.violations` | 基準どおりのツリーで違反なし | — | 基準に無い組を足したツリー・基準の組を直したツリー・扱いの無い行・`load` の失敗 |
| `facts_decl.load_tables` | 形の通った表を返す | 表 0 件 | 最上位に知らないキー・列の型が語彙の外で違反。`fact.schema.json` が無いと `ContractError` |
| `facts_decl.row_violations` | 列どおりの行 | 任意の列を持たない行は通す | 型の違う値・宣言の無い列を持つ行・重なる `id` |
| `facts_decl.ref_violations` | `loop-phases` の行がすべて `monitor.phase.` にある | `facts/<表>` で相手の `id` にある | 相手の方針に無い行・相手の表が無い |
| `facts_decl.references` | `facts.ids("runtimes")` と `ndf_fact_ids runtimes` を拾う | `import facts as f` の別名 | 名前を変数で渡す呼び出しはキー `None` |
| `facts_decl.violations` | 違反なし | — | どこからも読まれない表・名前を文字列で書いていない呼び出し・形の違反 |
| `facts_decl.render_section` | 表ごとに 1 行と読む関数 | 表 0 件 | 読めない表で理由の 1 行 |
| `ratchet.rows_added` | merge-base と同じ行で 0 件 | 定数のファイルだけを書き換えた行は数えない。消した数より 1 つ多く足すと 1 件 | merge-base に基準のファイルが無いと 0 件 |
| `render.render` | 「上限の宣言」の後に「事実の表」 | `root` が無ければ節を出さない | — |
| `Checker.run` | 違反の無いツリーで `metrics` に 3 つの数 | — | 基準を壊すと `main` が終了コード 2 |
| `_baseline_ratchet` | merge-base より行の増えた基準で `baseline-added` | merge-base の検査が `const-fact` を知らないと 0 件 | 起点が決まらないと CI で終了コード 3 |
| `main`（`check-ndf-contracts.py`） | 検査の経路と `--render` で `deps.require("jsonschema")` を 1 回呼ぶ | `--draft` では呼ばない | `deps` が 69 を返す経路は `deps` のテストが持つ（ここでは呼ばれたことだけを見る） |
| `main`（`measure/const-facts.py`） | `--json` の 1 行のキーと `metrics` のキーが DC2 の前と同じ | `--limit 1` | — |
| `_round_reviewers` | 記録・`only`・`participants` の順 | 判定済みのラウンドは宣言の外でも記録を返す | `participants` も `only` も記録も無い状態で `AssignmentError` |
| `_print_round_summary` | `reviewers` のある記録 | `reviewers` の無い記録で担当の列が `—` | — |

DC3〜DC8 の読み手の観点は、子 Issue ごとに新しいテストのファイル 1 本（ファイル配置の表の `test_*.py`）へまとめる。観点は共通で、次の 3 つである。

- 正常: 表に行を 1 つ足した一時の置き場（`facts.use_dir`）で、読み手の最上位の値にその行が現れる（要求「値を足すときに直すファイルが 1 つ」）
- 境界値: 条件の列で外れる行は現れない（例: `default_seat` が偽の `agy` は `assignment.DEFAULT_RUNTIMES` に無い）。並びは表の並び（`ALL_RUNTIMES` の輪番が変わらない）
- 異常: 表を壊すと読み手の import が `FactsError` で止まる（既定値で続けない）

置き換えた範囲の古いテストは同じ PR で消す。消すものは次のとおりで、ファイルの残りのテストは振る舞いを確かめるため残す（文字列の集まりで書いた期待値は表から引く形へ直す）。

| 子 Issue | 消す古いテスト |
| --- | --- |
| DC2 | 無し（計測のスクリプトにテストは無い。`ls scripts/tests \| grep -i const` が 0 件） |
| DC3 | `plugins/ndf/skills/cross-review/tests/test_state_review_pool.py` の `test_a_host_only_state_keeps_reviewers_other_than_the_host`・`test_state_without_a_host_keeps_the_two_named_reviewers`（消す分岐のテスト）。`test-literal` でランタイムの文字列の集まりを持つテスト 79 か所（多いのは `cross-review/tests/test_state_review_pool.py` 15・`cross-refactoring/tests/test_init.py` 14・`cross-review/tests/test_state_resume_args.py` 12・`scripts/tests/test_lib_assignment.py` 10）のうち、定数の値そのものを確かめる assert |
| DC4 | `plugins/ndf/skills/cross-review/tests/test_measure.py:687` の一致の確認。重さとフェーズの文字列の集まりだけを確かめる assert |
| DC5 | `plugins/ndf/scripts/tests/test_relay.py:2885`・`plugins/ndf/skills/cross-review/tests/test_queue_idempotency.py:52`・`plugins/ndf/skills/worktree/tests/test_testenv_reclaim.py:862` の一致の確認 |
| DC6 | 置き場・指示書・見出しの文字列の集まりだけを確かめる assert |
| DC7 | `plugins/ndf/scripts/tests/test_transcript_agents.py` の `test_the_step_names_match_the_phase_table` と `phase_steps_from_table`。`plugins/ndf/skills/development-workflow/tests/test_document_rebuild_stage.py:37`・`test_documentation_mode.py:28・62`・`test_operation_mode.py:50・56・94` の定義の文字列を読むテスト（#3092 の工程とモードの文書のテストへ寄せる） |
| DC8 | 持ち主へ寄せた定数の一致を確かめる assert（DC8 の着手の時点で `const-facts.py --json` の `test-bind`・`test-literal` から拾う） |
