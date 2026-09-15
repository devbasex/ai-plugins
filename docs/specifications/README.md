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
| [cross-review-evidence-based.md](cross-review-evidence-based.md) | 証拠ベースのレビューと効果の測定。状態ファイルの契約と決定の理由。手順は `cross-review` の `SKILL.md` が正 |

Skill の挙動仕様はここに置かない。Skill に関する詳細は対象 Skill の `SKILL.md` を参照する。
