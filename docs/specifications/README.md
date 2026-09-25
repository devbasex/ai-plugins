# 仕様書

実装完了後の plan / issue / report から、現行コードと一致する as-is 仕様として整理した文書を配置する。

| 仕様書 | 内容 |
|---|---|
| [issues-derived-specifications.md](issues-derived-specifications.md) | `issues/` 配下の完了済み issue / plan / report 由来仕様の索引 |
| [ndf-knowledge-and-kiro.md](ndf-knowledge-and-kiro.md) | NDF 知識構造（`AGENTS.md` と版と配布の正本、README の役割）、Serena 分離、Kiro CLI 対応（installer の導入先の検査を含む） |
| [ndf-skill-inventory/](ndf-skill-inventory/01-ledger-and-criteria.md) | Skill ごとの利用実績と、維持・統合・削除・発動改善の判定（3 本） |
| [ndf-design-phase.md](ndf-design-phase.md) | 設計工程を置いた経緯と決定（表から導ける値の数え直しを含む）。手順は `design` の SKILL.md が正 |
| [ndf-workflow-unit-and-gates.md](ndf-workflow-unit-and-gates.md) | モードを判定する単位、起動時の作業ツリーの宣言の確認、承認の関門と設計 Pull Request の本文の突き合わせ、実行証跡の gate と本文の語の分割。手順は `development-workflow` の SKILL.md が正 |
| [ndf-workflow-blockers.md](ndf-workflow-blockers.md) | 工程が止まる 2 か所の直しと push の退避。手順は各 Skill の SKILL.md が正 |
| [ndf-documentation-mode.md](ndf-documentation-mode.md) | ビジネス文書のワークフロー（`documentation` モード）。3 つの軸と 2 つの関門。手順は各 Skill の `SKILL.md` が正 |
| [doc-consistency-checks.md](doc-consistency-checks.md) | リンクの検査（見出しへの参照・`issues/` の走査・インラインコード）と、版と配布の正本の検査 J（版数の例を例どうしで比べる規則）。書き方は正本と検査スクリプトが正 |
| [ndf-worktree-declaration-and-entry-points.md](ndf-worktree-declaration-and-entry-points.md) | 宣言を共有と個人に分ける重ね合わせ、追従を既定で止めること、読めない宣言と `init` の終了コード、編集の案内が読む書き込み先の抽出。手順は `worktree` の SKILL.md と `references/declaration.md` が正 |
| [ndf-testenv-lock-and-registry.md](ndf-testenv-lock-and-registry.md) | テスト環境の排他の判定を陳腐化の規則へ揃えたこと、台帳へ書けなかったときの扱い。手順は `worktree` の `references/test-execution.md` が正 |
| [ndf-issue-upkeep-root-cause.md](ndf-issue-upkeep-root-cause.md) | 溜まった課題を根本原因の場所で直す判定（ルートコーズ）と、構造の判断の担い手。親 issue とサブイシューの実測、マイルストーンを連番で読む理由。手順は `issue-upkeep` の SKILL.md と `references/` が正 |
| [cross-review-evidence-based.md](cross-review-evidence-based.md) | 証拠ベースのレビューと効果の測定。状態ファイルの契約と決定の理由。手順は `cross-review` の `SKILL.md` が正 |
| [cross-review-launch-outcome.md](cross-review-launch-outcome.md) | 起動 1 回の結末の語彙（理由 9 語）と起動し直しの可否、利用上限と CLI の上限の検知、プロセスグループでの起動と停止。手順は `cross-review` の `SKILL.md` と `docs/` が正 |
| [cross-review-participants-and-seats.md](cross-review-participants-and-seats.md) | 使える者だけで収束ループを始める共通層（認証の確認を止めない形・参加の母集合と足す者／外す者・毎ラウンド 2 席の埋め方・席の名前・cross-refactoring の実装担当の決め方 `choose_implementer`）と、再開で渡した引数の反映。手順は `cross-review` の `SKILL.md` と `docs/` が正 |
| [cross-review-round-inputs.md](cross-review-round-inputs.md) | cross-review のラウンドで担当が受け取るもの。出し切りの指示、テストと背景の処理を起動しない指示（cross-refactoring の適用担当はテストを前景で待つ）、既存コメントの控えをラウンドの開始ごとに取り直すこと（`fetch-pr-comments.sh --strict`）、設計 Pull Request の分類と観点、収束の判定・振動の検知を変えない理由。手順は `cross-review` の `SKILL.md` と `docs/` が正 |
| [cross-review-writes-to-conductor.md](cross-review-writes-to-conductor.md) | GitHub と git への書き込み（レビューの投稿・返信・決着・修正のまとめ・修正の送信）をレビューを回す側だけが行うこと、担当が書く 2 つのファイルと改名の順序、二度書かない照合、差分の外を指す指摘の退避、途中で止まったときの立て直し、起動し直しを初回と同じ経路へ通すこと。手順は `cross-review` の `SKILL.md` と `docs/` が正 |
| [cross-refactoring-time-budget.md](cross-refactoring-time-budget.md) | cross-refactoring を想定最大時間（`--budget-minutes`）に収める 1 回の計画実行（#933）。5 フェーズ、引数と廃止した 5 引数、実装担当の決め方、候補の切り出し、計画の取り込み、控え（`plan.reserve` の 4 キー）と締め切り、時間の上限の表（`state["limits"]`）と段の監視の上限、Jev の使い方と D5 の前倒し、配分テーブルと履歴、状態ファイルの版 2、報告。式と係数は `cross-refactoring` の `docs/02-plan-and-implement.md` の「締め切り」が正 |
| [cross-refactoring-verify-and-final-gate.md](cross-refactoring-verify-and-final-gate.md) | cross-refactoring のテストの追加と実装の取り込み、項目ごとの限ったテストによる検証と修正、危険の印（D1〜D5）と全体のテストの 1 回、落ちたテストだけでの見分け（揺れ・元からの失敗・変更が原因）、項目の単位の取り消し、最終ゲートと最終ゲートの修正の打ち切り（1 回目は時計で打ち切らない）、履歴へ追記する時点（#933）。手順は `cross-refactoring` の `docs/` が正 |
| [cross-refactoring-apply-intake.md](cross-refactoring-apply-intake.md) | 担当が結果を残さない起動を進行を止めずに受けること（結末を値で読む・取り込みごとの結果ファイルの扱い）、最終ゲートの修正の結果なしの取り消しと記録、帰属の段落の後ろから必須の記名を読むこと。適用ラウンドの開き直しと試行の上限は #933 で無くなった。手順は `cross-refactoring` の `SKILL.md` と `docs/` が正 |
| [cross-refactoring-participants.md](cross-refactoring-participants.md) | cross-refactoring の参加者（codex / kiro とホストを既定に足す者／外す者で変える・確認を通らない者を外して続ける）、既定の実装担当、再開で渡した引数の反映、呼び手の無くなった共通層の旧関数の削除。適用の輪番は #933 で廃止した。手順は `cross-refactoring` の `SKILL.md` と `docs/` が正 |
| [cross-refactoring-round-tests-and-assess.md](cross-refactoring-round-tests-and-assess.md) | cross-refactoring の範囲のテスト（`--round-test`）を限ったテストの元にし全体のテストを着手前・危険の印の最大 1 回・最終ゲートに限ること、`init` の関門と案内、打ち切りが子の終了で戻ること、構造改善を飛ばしてよいかの判定（`refactor.py assess`）と飛ばした記録、テストの追加と実装で `.md` の文言固定テストを採らないこと、リポジトリに文言固定テストを置かない分類の規則。手順は `cross-refactoring` の `SKILL.md` と `development-workflow` の `references/` が正 |
| [ndf-cleanup-and-bundle-closing.md](ndf-cleanup-and-bundle-closing.md) | 後片付けが止まる条件（git の拒否だけ）、実行前確認の要否を決める 3 つの問い、ミッションの課題を終わりの工程で閉じる条件と結果の 4 値、配布の記録の形と読み方。手順は `merged` / `progress-tracking` / `release` の SKILL.md が正 |
| [ndf-agent-layers-unattended-run.md](ndf-agent-layers-unattended-run.md) | `development-workflow` の工程を conductor / supervisor / worker の 3 層で通す運転。フェーズ 5 つ、報告の 2 段、続けさせる回数、上限（429）で中断した層の再開。手順は `development-workflow` の `references/agent-layers.md` が正 |
| [ndf-context-window-metrics.md](ndf-context-window-metrics.md) | 会話の記録から context window を 3 層で測る部品（`transcript_agents.py`）の値の取り方と、`skill-stats --agents` の 4 つの表と印。値の取り方はこの文書が正 |
| [ndf-execution-plan-and-parallel-capacity.md](ndf-execution-plan-and-parallel-capacity.md) | 並列の実行計画（依存を工程の対で書く・重なりの 3 区分・開いている間はコミットしない）、マイルストーンの組、メモリで見る本数（`parallel-measure.py`）。手順は `issue-plan-strategy` と `development-workflow` の `references/` が正 |
| [ndf-instruction-files-check.md](ndf-instruction-files-check.md) | エージェント向け指示書の検査（`instructions-check.py`）。宣言 `.ndf/instructions.json` で決まる判定の強さ、即時読み込みと出た版の段落の判定、扱いの印、観点の調べ直し。呼び方と宣言の書き方は `release` の `references/instruction-files.md` が正 |
| [ndf-release-steps-and-token-usage-snapshot.md](ndf-release-steps-and-token-usage-snapshot.md) | リポジトリが宣言する配布の段（`.ndf/release.json` と `release-steps.py`）の選び方・変更の判定・終了コードと、正式版ごとのトークン消費の記録（`token-usage-snapshot.py`）の前の記録・名前・節・差の大きい版。呼び方と宣言の書き方は `release` の `references/release-steps.md`、記録の読み取りは `docs/metrics/ndf-token-usage/README.md` が正 |
| [test-monitor-env-isolation.md](test-monitor-env-isolation.md) | テストの実行中だけ監視の上限を指す環境変数（接頭辞 `MONITOR_`）をリポジトリの根の共通の前提で外すこと、外す時点と戻す時点、根の設定ファイルで基準のディレクトリを固定すること |
| [ndf-token-waits-and-context-cut.md](ndf-token-waits-and-context-cut.md) | 待つ間の問い合わせ（前景の `sleep` の待ち・変わらないファイルの読み直し）と、文脈が上限を超えた conductor の工程の起動を止める hook（`token-guard.sh`）の判定・記録の形・入出力の契約、引き継ぎの 1 行、4 ランタイムの扱い。規約は `development-workflow` の `references/waiting.md` と `context-window.md` が正 |
| [ndf-worker-agent-and-skill-excerpts.md](ndf-worker-agent-and-skill-excerpts.md) | サブエージェントに Skill 本文を読ませない仕組み。記録のコマンド 1 行（`projects-sync.sh` が issue の本文と盤面へ同時に残す）の契約、worker のエージェント定義 `ndf:worker`（Skill と Agent のツールを外す）、抜粋の形と上限、仕事を分ける器と小さな作業の線引き。規約は `development-workflow` の `references/agent-layers.md` / `work-vessels.md` と `skills/EXCERPTS.md` が正 |
| [ndf-relay-segment-restart.md](ndf-relay-segment-restart.md) | 区間の切れ目で claude を起動し直すラッパー（`relay.py`）。`ndf-next` のブロックを印へ写す Stop hook、擬似端末の子としての起動と素通しの条件、静まり・上限・空回り・停止の印、作業ディレクトリと記録の形、ラッパーの下で文脈量の拒否を止め続けること。利用者向けの案内は `development-workflow` の `references/relay.md`、次のコマンドの形は `context-window.md` が正 |
| [ndf-relay-install-and-restart.md](ndf-relay-install-and-restart.md) | ラッパーの明示の導入（`/ndf:install-wrapper` と `relay.py` の `install` / `uninstall` / `status`）・写しとラッパーの rc の置き場所（devbase で永続化される `~/.claude/ndf/`）・SessionStart の `startup`（在る写しの置き直しと 10.17.4〜10.17.6 の自動の囲みの 1 度だけの知らせ）、`/ndf:restart`、関門を越えない守り（質問の印・`question.lock`・`/exit` の 1 回の write）、2 つ目以降の区間への起動の方針の引数の引継ぎ、送り込みの実測と不変条件。手順は `install-wrapper` / `restart` の `SKILL.md` と `relay.md` が正 |
| [ndf-relay-segment-notice.md](ndf-relay-segment-notice.md) | 区間の切れ目の告知（`relay.py notice`）。1 行目の判定（`is-child` と同じ）と 2 行目の告知の文面、秒数（静まりの秒数そのもの・偶数丸め・0/負/`nan`/`inf` の扱い）、conductor・`/ndf:restart`・文脈量の hook での使い方、区間の切れ目の再起動は関門ではなく承認を挟まない規則。規則は `development-workflow` の `context-window.md` と `SKILL.md` が正 |
| [mcp-serena-language-servers.md](mcp-serena-language-servers.md) | mcp-serena の言語サーバ（#818）。起動定義（`serena-agent==1.7.0` の固定・ランタイム別の文脈）、対応表 `languages.json`、`serena-lsp.py` の `detect` / `configure` / `check` / `hook` の引数・終了コード・JSON、`project.yml` の 3 キーの行単位の書き換えと設定済みの印 `mcp_serena_excluded`、SessionStart の通知と PreToolUse の誘導・自動許可、数の記録、Claude Code の診断を公式 LSP プラグインに分担させる決定。手順は `language-servers` の SKILL.md が正 |

Skill の挙動仕様はここに置かない。Skill に関する詳細は対象 Skill の `SKILL.md` を参照する。
