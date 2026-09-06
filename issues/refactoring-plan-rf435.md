# 改修計画 — devbasex/ai-plugins #435

`/ndf:cross-refactoring` が提案し、適用した改善項目の記録である。
理由と手順は提案の時点でしか残らないため、公開の直前に書き出している。

- 対象範囲: plugins/ndf/skills/development-workflow, plugins/ndf/scripts/lib
- 着手前のテスト: uv run --with pytest pytest plugins/ndf/skills/development-workflow/tests plugins/ndf/scripts/tests -q

## ラウンド 1（実装 codex / レビュー agy / kiro）

### R1-001 — `plugins/ndf/scripts/lib/monitor.py#monitor_agent`

| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | split_into_pipeline | major | codex / kiro | 取り消し | 2 |

**なぜ**: 1 つの関数（約 210 行）が起動待ち・pidfile 読み・cmdline 検証・codex sentinel 完了・result.json 常駐検知・hard timeout・early error・プロセス終了判定・stall 検知を通しで持つ。各ブロックは独立した終了条件で、それぞれが status を設定して _emit_log 後に return する定型を繰り返す。部分だけをテストできず、監視ループの 1 条件を読むのに全体を追う必要がある。着手前テスト（scripts/tests）は monitor を一切 import しておらず、この関数を通すテストが無いため、抽出の前に現状固定テストが要る。

**手順**: 1. 現状固定テストを先に足す。monitor_agent は時刻・プロセス・ファイルへ副作用を持つため、_pid_alive / _kill_pid / time.sleep / time.monotonic を差し替え、pidfile と result.json を tmp に置いて PIDFILE_BAD・TIMEOUT・OK(result あり)・NO_RESULT・STALLED の各終了経路の status を固定する
2. 起動待ち + pidfile 読み取り（grace ループ〜PIDFILE_BAD 判定）を `_await_pid(paths, status, log_prefix)` として抽出し、失敗時は None を返す
3. codex sentinel 完了検知ブロックを `_codex_sentinel_done(...) -> Optional[AgentStatus]` として抽出
4. result.json 常駐検知ブロックを `_lingering_result_done(...) -> Optional[AgentStatus]` として抽出
5. stall 検知の進捗サイズ集計を `_progress_size(paths)` として抽出
6. 各抽出ごとに現状固定テストを実行し 1 手ずつ緑を確認する。副作用の分離ができず固定テストが書けない場合は SKILL.md の『途中で止める条件』に従い着手しない。見積は先に足す固定テストと、抽出本体・monitor_agent 内の呼び出し置換・引数受け渡しを含む

### R1-002 — `plugins/ndf/skills/development-workflow/scripts/lib/workflow-merge.sh#wf_merge_target`

| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| duplication | extract_method | major | kiro | 採用 | 1 |

**なぜ**: `gh` の直後から `pr` までグローバルオプション（`-R`/`--repo` は値付き、その他の `-*` は 1 語だけ）を読み飛ばして動詞を待つ state machine（state 0->1->2、`-R|--repo`->4->1、`-*` 素通り）が、workflow-common.sh の `_wf_pr_create_body` と workflow-merge.sh の `wf_merge_target` に同一の分岐として二重に書かれている。#427 のレビューで両方に同じ修正（`gh -R <slug> pr` を見落とす）を入れており、片方だけ直すと挙動が食い違う。同じ理由で必ず一緒に変わる重複である。

**手順**: 1. workflow-common.sh に、語のストリームを NUL 区切りで受け取り `gh` から `pr <動詞>` までの前置オプションを消費して「動詞へ到達したか」を返す共通ヘルパ（例 `_wf_seek_gh_verb`）を抽出する。呼び出し側の while ループ本体から state 0/1/2/4 の遷移部分だけを移し、動詞名（create/merge）を引数で受ける
2. `wf_merge_target` の state 0/1/2/4 の case を共通ヘルパの呼び出しへ置き換える。REST 経路（`pulls/<番号>/merge`）と state 3 の番号取り出しは merge 固有なので残す
3. `_wf_pr_create_body` の state 0/1/2/4 の case を同じ共通ヘルパへ置き換える。`--body`/`-F` 等の本文取り出しは create 固有なので残す
4. tests/test_workflow_evidence.py（gh -R 前置・heredoc・複数行本文）と tests/test_workflow_guard.py（merge 判定）を実行し、両経路が緑であることを確認する

### R1-003 — `plugins/ndf/scripts/lib/post_queue.py#posted_match`

| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| conditional_chain | replace_conditional_with_polymorphism | major | codex | 取り消し | 2 |

**なぜ**: kind ごとの照会先と一致条件が if 連鎖に埋め込まれており、request_for や send と同じ種別を増やすたびに複数箇所の条件分岐を同期して直す必要がある

**手順**: 1. 現状固定テストとして、pr-comment、review-post、review-reply、thread-resolve、未知 kind の戻り値を固定する
2. kind から照会関数へ対応する POSTED_MATCH_HANDLERS を定義する
3. 各 kind の一致条件を _match_pr_comment、_match_review_post、_match_review_reply、_match_thread_resolve に抽出する
4. posted_match は item から共通値を取り出し、対応表を引いて handler を呼ぶだけにする
5. 既存の already_posted 経路で戻り値が変わらないことを確認する

### R1-004 — `plugins/ndf/scripts/lib/worktree-common.sh#wt_extract_write_target`

| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | split_into_pipeline | major | codex | 取り消し | 2 |

**なぜ**: 1 関数にヒアドキュメント除去、リダイレクト正規化、トークン列補正、cd 状態追跡、複合コマンド追跡、書き込み先出力が同居しており、局所的な変更でも 800 行超の状態遷移全体を読まないと影響を判断しにくい

**手順**: 1. 現状固定テストとして、cd、subshell、and-or、case、function、heredoc、redirect の代表入力で出力先が変わらないことを追加する
2. ヒアドキュメント除去とリダイレクト印付けを prepare_write_target_words に抽出する
3. リダイレクト対象の解決を resolve_redirection_target に抽出し、必要な words と添字だけを渡す
4. 状態の push/pop 系 helper を関数本体の前へ切り出し、呼び出し側から状態変数を明示して渡す
5. メインループを token preparation、state transition、target emission の順に並ぶ処理の連鎖にする

### R1-005 — `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh#wf_stage_class`

| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| dead_code | remove_dead_code | minor | agy | 採用 | 1 |

**なぜ**: PR #435 でモード一覧から architecture が廃止され、WF_MODES および WF_STAGE_MATRIX の列数が 4 から 3 へ減った。しかし wf_stage_class の内部では IFS=$'\t' read -r name c1 c2 c3 c4 による 4 列目の受領と、case "$column" の 4) printf '%s\n' "$c4" 分岐が残っており、_wf_mode_column は最大 3 までしか返さないため到達不能なデッドコードになっている。

**手順**: 1. wf_stage_class 内の read 行から未使用変数 c4 を削除し、read -r name c1 c2 c3 とする
2. case "$column" から到達不能な分岐 4) printf '%s\n' "$c4" ;; を削除する
3. 既存テストを実行して通過することを確認する

## ラウンド 2（実装 agy / レビュー codex / kiro）

### R2-001 — `plugins/ndf/skills/development-workflow/scripts/lib/workflow-merge.sh#wf_check_merge`

| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | split_into_pipeline | major | agy / kiro | 採用 | 1 |

**なぜ**: 1 関数が「必要コマンドの検査 → リポジトリ名の解決 → 番号ありの問い合わせ／番号なしの問い合わせ（ブランチ取得・一覧取得・先頭抽出・番号再取得の入れ子分岐）→ head の読み取り → 設計接頭辞の判定 → ラベルの有無 → ラベル定義の有無」と直列に進む。特に番号なしの分岐が深く入れ子になり、正常系（ラベルの有無判定）が末尾に埋もれている。段の間で受け渡すのは num と json だけで、各段に名前が付く

**手順**: 1. jq / git / gh の存在検査と失敗時の判定拒否理由生成を _wf_require_merge_tools として抽出する
2. 番号指定時（pulls/<番号>）と番号未指定時（ブランチ名から pulls?head= で検索）の PR 問い合わせと番号・JSON 解決を _wf_resolve_pr_info として抽出する
3. 承認ラベルの有無およびラベル定義（404 判定）の確認を _wf_verify_approval_label として抽出する
4. wf_check_merge 本体を「ツール検査 → PR 解決 → head 判定 → ラベル判定」の直列なパイプライン処理として整える
5. 各手順ごとに test_workflow_guard.py を実行し、既存テストが全て通ることを確認する

### R2-002 — `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh#wf_evidence_report`

| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | agy / kiro | 採用 | 1 |

**なぜ**: 1 関数が 4 つの段階を通しで行う。targets へ 1 巡目で mode を集めて effective/conflict を決め、2 巡目でも同じ state ファイルを wf_state_read で読み直し mode を再解析し、そこで missing の note を組み立て、最後に conflict とラベル案内を含む body を連結する。2 巡目の再読込は 1 巡目と重複しており、note 生成と body 組み立ては別々の関心である。段階に名前が付く（対象の収集 / 課題ごとの記録なし工程の抽出 / 本文の組み立て）

**手順**: 1. targets と modes/effective/conflict を集める 1 巡目を `_wf_collect_targets` として抽出し、targets 一覧と effective と conflict を返す形にする
2. 課題 1 件から記録なし必須工程の note 1 行を作る処理を `_wf_target_note` として抽出し、2 巡目のループ本体をこの呼び出しに置き換える（state ファイルの読み直しはこの中に閉じる）
3. notes/conflict/effective/modes から案内文字列を組み立てる部分を `_wf_compose_evidence_body` として抽出する
4. wf_evidence_report 本体を「収集 → note 生成の反復 → body 組み立て」の 3 呼び出しへ縮める
5. 各手ごとに test_workflow_evidence.py を実行して緑を確認する

### R2-003 — `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh#WF_STAGE_MATRIX`

| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| scattered_config | centralize_configuration | major | codex | 取り消し | 0 |

**なぜ**: 工程名とモード名が WF_STAGE_MATRIX/WF_MODES と projects-common.sh の PJ_STAGES/PJ_MODES に別々の固定値として置かれており、同じ工程語彙を変更するときに片方だけ更新されると工程記録と報告の判定が食い違う

**手順**: 1. workflow-common.sh に工程名とモード名を返す小さな公開関数を置き、WF_STAGE_MATRIX から値を導く
2. projects-common.sh 側の PJ_STAGES/PJ_MODES を固定文字列ではなくその関数の出力から初期化する
3. pj_is_stage/pj_is_mode と wf_is_stage/wf_is_mode の既存入出力が変わらないことを既存の stage_values と workflow_stage_matrix のテストで確認する

## ラウンド 3（実装 kiro / レビュー codex / agy）

### R3-001 — `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh#wf_report`

| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | agy / kiro | レビュー中 | 1 |

**なぜ**: 1 関数が (1) 控えの読み取り (2) frontier までの各工程を present/missing/conditional へ分類する走査 (3) 見出し・記録あり/なし/条件付き・案内文の整形 の 3 段を通しで行う。分類の走査は wf_stage_class を呼びつつ 3 本の配列へ振り分ける独立した段で、既に隣接する _wf_missing_before_pr が同型の走査を別関数へ切り出しており、この関数だけが走査と整形を同居させている。

**手順**: 1. frontier までを走査して各工程を分類する部分（present へ入れるか、mode があれば wf_stage_class で R/C を判定して missing/conditional へ振り分ける while ループ）を _wf_classify_stages として抽出する
2. 抽出関数は recorded 配列・mode・frontier を引数で受け取り、'class<TAB>stage'（present は present、必須は missing、条件付きは conditional）を 1 行 1 件で標準出力へ出す（本ファイルの _wf_missing_before_pr / wf_stages が採る行出力の流儀に合わせる）
3. wf_report 側は抽出関数の出力を読み、タブの左で present/missing/conditional 配列へ bin する短いループへ置き換える
4. 残る整形部（printf の並び）はそのまま wf_report に残し、走査と整形の責務を分ける
5. stage-check.sh report 経由の統合テスト（tests/test_stage_check.py の report、tests/test_workflow_units.py::test_the_report_requires_a_review_for_light、tests/test_workflow_guard.py::test_recording_the_release_reports_the_missing_stages）を実行し、出力が不変であることを確認する

### R3-002 — `plugins/ndf/scripts/lib/models.py#separation_reason`

| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| embedded_business_rule | replace_with_lookup_table | major | codex | 取り消し | 0 |

**なぜ**: 計測から分離する条件と文言が if 分岐に埋め込まれており、assumption_note と is_measurable も同じ判断に依存しているため、ランタイムごとの計測方針を一覧できない

**手順**: 1. 現状固定テストで、kiro auto、未指定の codex/agy、指定ありの codex、observable な claude の戻り値を固定する
2. 分離条件をランタイム別の小さな対応表へ移す
3. separation_reason は対応表を参照して理由を返すだけにする
4. assumption_note と is_measurable が同じ入口を使うことを確認し、既存テストを実行する

### R3-003 — `plugins/ndf/scripts/lib/metrics.py#format_report`

| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | split_into_pipeline | major | codex | 取り消し | 0 |

**なぜ**: 1 つの関数が実装担当表・レビュー担当表・分離ラウンド・指定値代用ラウンド・比較上の注意を順に組み立てており、集計項目の追加時に表示段すべてを同時に読ませる構造になっている

**手順**: 1. 現状固定テストで、impl だけ・reviewer だけ・unmeasured/assumed ありの出力を固定する
2. 実装担当表を _format_impl_section に抽出する
3. レビュー担当表を _format_reviewer_section に抽出する
4. 警告セクションと比較上の注意をそれぞれ独立した段として返す関数へ分ける
5. format_report は各段を順に連結するだけにして既存テストを実行する

### R3-004 — `plugins/ndf/skills/development-workflow/scripts/lib/workflow-merge.sh#wf_merge_target`

| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| deep_nesting | flatten_conditional | minor | agy | レビュー中 | 1 |

**なぜ**: wf_merge_target の state 3 処理において、case "$state" in 3) の直下に case "$tok" in *[!0-9]*) と case "$tok" in */pull/*) の 3 段の case 文が入れ子になっており、正常系である数値トークン判定や URL からの番号抽出の条件分岐が深くネストしている。例外条件やパターンを平坦化してネストを浅くできる。

**手順**: 1. state 3 内のトークン判定を平坦化し、URL 形式（*/pull/*）の抽出と数値判定（*[!0-9]* / *）を 1 階層の case 分岐へ整理する
2. tests/test_workflow_guard.py を実行して番号指定・URL 指定・オプション混在時のマージ判定テストが全て通ることを確認する

### R3-005 — `plugins/ndf/scripts/lib/closing-issues.sh#closing-issues.sh (git URL からの slug 解決ブロック)`

| 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| duplication | consolidate_duplication | minor | kiro | レビュー中 | 1 |

**なぜ**: git の remote.origin.url を <所有者>/<リポジトリ> へ畳む同じ規則（slug=${url%.git}; slug=${slug%/}; slug=${slug##*:} と owner/repo への分解、owner=${owner##*/} まで）が closing-issues.sh のインラインブロックと workflow-common.sh の wf_repo_slug に二重にある。URL の形（git@ / https / 末尾スラッシュ）への対応が変わると片方だけ直され、閉じる先の解決とリポジトリ判定で挙動が食い違う。ただし closing-issues.sh は独立実行を保つ設計（bash 副プロセスとして起動される）のため、共通化は共有ライブラリの source という結合を持ち込む点に注意が要る。

**手順**: 1. slug 解決の規則を 1 つの純関数（例: scripts/lib のごく小さな共通シェル関数、または既存の projects-common.sh 等 3 ランタイム共通で読まれる層）へ寄せる。標準出力へは何も足さず、slug を返すだけにする
2. wf_repo_slug をその関数への委譲に置き換える
3. closing-issues.sh の DEFAULT_REPO 決定ブロックを同じ関数の呼び出しへ置き換える。独立実行の契約を壊さないよう、読み込み失敗時は現状どおり DEFAULT_REPO を空のまま進める分岐を残す
4. closing-issues.sh の現状固定テスト（plugins/ndf/skills/merged/tests/closing_issues_helpers.py 経由）と wf_repo_slug を通る stage-check.sh 系の統合テストを実行し、slug 解決結果が不変であることを確認する。テストの置き場所が対象範囲外（skills/merged/tests）にあるため、共通化のコミットに合わせて --scope へ含める必要がある
