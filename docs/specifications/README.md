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
| [ndf-cleanup-and-bundle-closing.md](ndf-cleanup-and-bundle-closing.md) | 後片付けが止まる条件（git の拒否だけ）、実行前確認の要否を決める 3 つの問い、まとまりの課題を終わりの工程で閉じる条件と結果の 4 値、配布の記録の形と読み方。手順は `merged` / `progress-tracking` / `release` の SKILL.md が正 |
| [ndf-agent-layers-unattended-run.md](ndf-agent-layers-unattended-run.md) | `/goal` の工程を conductor / supervisor / worker の 3 層で通す運転。持ち場 5 つ、報告の 2 段、続けさせる回数、上限（429）で中断した層の再開。手順は `development-workflow` の `references/agent-layers.md` が正 |
| [ndf-context-window-metrics.md](ndf-context-window-metrics.md) | 会話の記録から context window を 3 層で測る部品（`transcript_agents.py`）の値の取り方と、`skill-stats --agents` の 4 つの表と印。値の取り方はこの文書が正 |
| [ndf-execution-plan-and-parallel-capacity.md](ndf-execution-plan-and-parallel-capacity.md) | 並列の実行計画（依存を工程の対で書く・重なりの 3 区分・開いている間はコミットしない）、マイルストーンの組、メモリで見る本数（`parallel-measure.py`）。手順は `issue-plan-strategy` と `development-workflow` の `references/` が正 |
| [ndf-instruction-files-check.md](ndf-instruction-files-check.md) | エージェント向け指示書の検査（`instructions-check.py`）。宣言 `.ndf/instructions.json` で決まる判定の強さ、即時読み込みと出た版の段落の判定、扱いの印、観点の調べ直し。呼び方と宣言の書き方は `release` の `references/instruction-files.md` が正 |

Skill の挙動仕様はここに置かない。Skill に関する詳細は対象 Skill の `SKILL.md` を参照する。
