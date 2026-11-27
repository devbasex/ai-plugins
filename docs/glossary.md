# 用語集

この文書は `docs/glossary/glossary.json` から作る。手で直さない。コンテキストごとのファイルへの索引である。

| コンテキスト | 意味 | 語の数 | ファイル |
| --- | --- | ---: | --- |
| プロジェクトの用語集（`project-glossary`） | 各プロジェクトが持つユビキタス言語。語・意味・コンテキスト・廃止した語・SSOT | 15 | `docs/glossary/project-glossary.md` |
| NDF の開発ワークフロー（`ndf-workflow`） | NDF が提供する工程・承認ゲート・モード・ステップの語。development-workflow/references/glossary.md が持つ | 139 | `docs/glossary/ndf-workflow.md` |
| NDF の並列の計画（`ndf-parallel`） | 並列の計画がレーンを同時に進め、オーガナイザーがレーンのリクエストへ答え、エピックを進めるときの語。レーン・一時レーン・計画のジャーナル・マシンの上限 | 113 | `docs/glossary/ndf-parallel.md` |
| NDF の開発ループと MVV（`ndf-loop`） | 開発ループがスプリントを続けて選び、pace と MVV 判定で承認ゲートを通すときの語。選定・予算・止まる条件・出し直しの指摘・MVV | 50 | `docs/glossary/ndf-loop.md` |
| NDF のプランの実行（`ndf-supervise`） | supervise.py がプランをキューで流し、ステップを進めて結果を残すときの語。プラン・ステップ・ステージ・上限・進捗ログ | 63 | `docs/glossary/ndf-supervise.md` |
| NDF の実行の基盤（`ndf-durable`） | 耐久ワークフロー・ステートマシン・実行の核が、実行を記録し、止まった理由と次のルートを決めるときの語。結果 JSON・停止理由・判断の関数 | 56 | `docs/glossary/ndf-durable.md` |
| NDF の CI とテスト（`ndf-ci-test`） | テストの戦略・範囲テストと全体テスト・CI のチェックの待ちと必須のチェックの突き合わせ・差分の種類の語 | 50 | `docs/glossary/ndf-ci-test.md` |
| NDF の文脈と使用量（`ndf-context`） | context window の量を測り、工程境界で会話を続けるか切るかを決め、使用量を記録するときの語 | 34 | `docs/glossary/ndf-context.md` |
| NDF の設計（`ndf-design`） | 設計 Pull Request・設計文書・設計の結果・決定の記録・外部の調査・代表のリポジトリの語 | 48 | `docs/glossary/ndf-design.md` |
| NDF のブランチ戦略（`ndf-branch-strategy`） | ブランチ戦略の設定がベースブランチ・スプリントブランチ・リリース方式・レビューの単位・承認の受け方を決めるときの語 | 39 | `docs/glossary/ndf-branch-strategy.md` |
| NDF のコードの構造（`ndf-code-structure`） | NDF のスクリプトの構造・エントリポイントとコントラクト・公開する面・依存のバージョン・AI の兆候の検査の語 | 50 | `docs/glossary/ndf-code-structure.md` |
| NDF のプロジェクトの設定（`ndf-project-settings`） | プロジェクトの設定（.ndf/）を解析で作り、選べる値と旧いキーの改名を扱うときの語 | 16 | `docs/glossary/ndf-project-settings.md` |
| NDF の作り直し（`ndf-rebuild`） | #1142 の作り直し（ndf-next）と、旧いプラグインからの入れ替えの語。子の表・振り分け表・11.x の子 | 29 | `docs/glossary/ndf-rebuild.md` |
| NDF の Slack 通知（`ndf-notification`） | 利用者の手を待つ時点・その種類・通知の本文と復帰先 | 3 | `docs/glossary/ndf-notification.md` |
| NDF の外部 CLI 委譲（`ndf-agent-cli`） | cross-review / cross-refactoring が参加者の CLI を選び、起動し、監視し、結果を読むときの語。ランタイム・ホスト・参加者プール・起動結果 | 42 | `docs/glossary/ndf-agent-cli.md` |
| NDF の cross-review（`ndf-cross-review`） | Pull Request のレビューを CLI へ委譲し、新しい指摘が出なくなるまで回す収束ループの語。指摘・反証・総評・修正担当 | 47 | `docs/glossary/ndf-cross-review.md` |
| NDF の cross-refactoring（`ndf-cross-refactoring`） | 想定最大時間に収まるリファクタリング計画を立てて通す語。改善候補・改善項目・配分テーブル・最終ゲート | 73 | `docs/glossary/ndf-cross-refactoring.md` |
| NDF のラッパー（`ndf-relay`） | ラッパーがセッションを切り替え、シェルの設定へ組み込まれるときの語 | 94 | `docs/glossary/ndf-relay.md` |
| NDF のリリース（`ndf-release`） | release が走らせるリリースの種別・リリースコマンド・公開操作・リリース記録の語 | 121 | `docs/glossary/ndf-release.md` |
| NDF の指示書チェック（`ndf-instructions`） | instructions-check.py が見る指示書・スコープ・読み込み量・リリース済みバージョンの語 | 7 | `docs/glossary/ndf-instructions.md` |
| NDF の課題の棚卸し（`ndf-issue-upkeep`） | backlog-refinement と out-of-scope が課題を分類し、起票するときの語。区分・現象レイヤー・修正レイヤー・起票先 | 74 | `docs/glossary/ndf-issue-upkeep.md` |
| NDF の worktree（`ndf-worktree`） | worktree の設定・セッション開始 hook・テスト環境の割り当ての語 | 33 | `docs/glossary/ndf-worktree.md` |
| playwright-kit のテスト計画（`playwright-kit`） | 探索的テストとテスト設計技法の語。page role・oracle・HTSM など業界の定訳をそのまま使う | 6 | `docs/glossary/playwright-kit.md` |
| このリポジトリの開発の検査（`repo-dev-checks`） | ai-plugins の開発で formatter と静的解析を手元と CI で走らせるときの語。NDF が利用者へ配る振る舞いではない | 9 | `docs/glossary/repo-dev-checks.md` |
| NDF の学習の設定（`ndf-training-optout`） | LLM のアカウントが入力を学習に使う設定かを確かめ、学習に使う設定なら claude・codex を起動する前に Off へ書き換えるときの語 | 5 | `docs/glossary/ndf-training-optout.md` |
| NDF の Google Workspace の操作（`ndf-google-workspace`） | google-workspace の Skill が Google Workspace（Drive・Docs など）を操作するときの語 | 3 | `docs/glossary/ndf-google-workspace.md` |
| NDF の工数の見積もり（`ndf-estimate`） | AI エージェントが実施する前提で、作業の A・H・L・C を類似の実績から見積もり、予測と実績を突き合わせるときの語 | 13 | `docs/glossary/ndf-estimate.md` |
| NDF のスライドの作成（`ndf-slide`） | スライドを生成スクリプトで作り、Google Slides へ変換して確かめるときの語 | 8 | `docs/glossary/ndf-slide.md` |
| NDF の文書の組み直し（`ndf-document`） | 書き上げた文書を文書形式と文書の目的に合わせて組み直すときの語。document-rebuild が持つ | 9 | `docs/glossary/ndf-document.md` |
| Neural Development Factory（`ndf-factory`） | Neural Development Factory（別のリポジトリのサービス）が NDF を呼ぶときの API・入出力の規約の語。規約は plugins/ndf/contracts/ が持つ | 13 | `docs/glossary/ndf-factory.md` |
