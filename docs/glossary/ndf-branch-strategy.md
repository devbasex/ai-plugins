# 用語集: NDF のブランチ戦略（`ndf-branch-strategy`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

ブランチ戦略の設定がベースブランチ・スプリントブランチ・リリース方式・レビューの単位・承認の受け方を決めるときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| ベースブランチ | — | Pull Request のマージ先で、スプリントどうしを結合して試すブランチ。開発版のチャネルを兼ねることがある。設定の `base_branch`。worktree とスプリントブランチの分岐元はスプリントの起点が決める | 起点のブランチ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スプリントブランチ | — | 課題の Pull Request を集め、ベースブランチへの Pull Request をスプリントで 1 本にするブランチ（`sprint/<名前>`） | ミッションブランチ、ミッションのブランチ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ブランチ運用の設定 | — | 旧い設定の 1 つ。スプリントの起点・ベースブランチへ入れる時点・文書の Pull Request のマージ先・本番へのリリース方式を書いた `.ndf/worktree.json` の `branch_flow`。ブランチ戦略の設定（`.ndf/project.json` のセクション `branch_strategy`）へ移し、残っていると読む部品が止まる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スプリントの起点 | — | スプリントブランチと worktree を切る元のブランチ。ベースブランチか本番チャネルをチャネルの名前で指す。ブランチ運用の設定が決め、無ければベースブランチ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 結合ブランチ | — | ベースブランチを、スプリントブランチを入れてスプリントどうしの組み合わせを試す役割で呼ぶときの名前。開発版のチャネルを兼ねることがある | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリースブランチ | — | 本番チャネルへ入れる変更を集め、バージョンを上げるブランチ（`release/v<バージョン>`） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 本番へのリリース方式 | — | 本番チャネルへ何をどう入れるか。ベースブランチを丸ごと出すか、本番チャネルから切ったリリースブランチへ選んだリリースの単位を集めるか。ブランチ運用の設定が決める。リリースの組み方（まとめたリリースを組むか）とは別の軸 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 本番チャネルの設定 | — | 本番チャネルのブランチの origin 上の先頭にあるブランチ運用の設定（`origin/<本番チャネル>:.ndf/worktree.json`）。本番チャネル宛の検査とスプリントに属さないリリースのプランが読む | — | — | `docs/specifications/ndf-branch-flow-declaration.md` |
| 開始時の記録 | — | スプリントの目録（`sprint.json`）の `ブランチ運用` に、スプリントを始めたときに書いたブランチ運用の設定の値。スプリントの後の工程はこれを読み、設定を読み直さない | — | — | `docs/specifications/ndf-branch-flow-declaration.md` |
| 文書の Pull Request のマージ先 | — | 設計 PR と確定仕様化の PR のマージ先。ベースブランチかスプリントブランチで、ブランチ運用の設定が決める | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリースの単位 | — | 選んで集めるリリース方式で、リリースが選ぶ 1 つのブランチ。本番チャネルに含まれていないコミットを持つスプリントブランチ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| レビューの単位 | — | 1 本の PR にまとめ、1 回のコードレビューを掛ける変更のまとまり。実装計画の欄が決め、実行計画の実装の行 1 つ・実装のプラン 1 本・単位の PR 1 本になる（旧い意味の PR の種類ごとのレビューの組は #3881 が扱う） | — | — | `docs/specifications/ndf-review-units-and-refactoring-timing.md` |
| 単位の PR | — | レビューの単位 1 つを載せ、スプリントブランチへ出す PR。実装のプランがこの PR に cross-review を収束まで掛ける | — | — | `docs/specifications/ndf-review-units-and-refactoring-timing.md` |
| 単位のファイル | — | 実装計画と組の JSON ファイル。タスクとレビューの単位の SSOT で、実装計画の単位の表はここから作る | — | — | `issues/issue-3702-design.md` |
| 実行順 | — | レビューの単位を始める順の番号。先に要る単位の実行順の最大に 1 を足した値で、同じ値の単位は並列に流せる | — | — | `issues/issue-3702-design.md` |
| 分け方の案 | — | 大きさの目安や上限を超えたレビューの単位を、タスクの依存の順に区切り直した案 | — | — | `issues/issue-3702-design.md` |
| つなぎ目 | — | スプリントの PR の差分のうち、すべての単位の PR をスプリントブランチへマージした時点（集めた時点）の後に入ったコミット。工程「コードレビュー」はここだけをレビューし、ここにコードのファイルが無ければ工程を飛ぶ | — | — | `docs/specifications/ndf-review-units-and-refactoring-timing.md` |
| 集めた時点 | `gathered` | すべての単位の PR をスプリントブランチへマージした時点のスプリントブランチのコミット。工程「実装」の出力 `gathered` に書き、工程「コードレビュー」の起点になる | — | — | `docs/specifications/ndf-review-units-and-refactoring-timing.md` |
| PR の種類 | — | NDF のプランが出す PR の分け方。実装 PR・スプリントの PR・リリースブランチの PR・設計 PR・確定仕様化の PR の 5 つ | — | — | `plugins/ndf/skills/worktree/references/declaration.md` |
| スプリントの PR | — | スプリントブランチからベースブランチへ出す、検査のプランの PR | — | — | `docs/specifications/ndf-branch-flow-review-units.md` |
| 対象の差分 | — | レビューに渡す差分の取り方。PR の差分全体・未検査の差分・衝突の解消の差分・掛けないの 4 つ | — | — | `plugins/ndf/skills/worktree/references/declaration.md` |
| 未検査の差分 | — | 前回の検査の後に入った差分。最後の検査の範囲（#3126）と同じ取り方 | — | — | `docs/specifications/ndf-branch-flow-review-units.md` |
| 掛けるレビュー | — | レビューの単位のうち、PR に掛ける検査の組。cross-review・cross-refactoring・振る舞いの確認の 3 つから選ぶ | — | — | `plugins/ndf/skills/worktree/references/declaration.md` |
| リリース方式 | `release_strategy` | スプリントの変更を本番チャネルへ入れる方法。ブランチ戦略の設定の `branch_strategy.release_strategy`。`merge_base_branch` はベースブランチを丸ごと、`release_branch` は選んだスプリントブランチだけをリリースブランチへ集めて入れ、`trunk` はスプリントブランチを本番ブランチへ直接入れる | 取り込み方 | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
| 環境ブランチ | — | 検証や本番の環境へ載せるブランチ（`qa/*`・`staging/*`・`release/*` など）。`/ndf:cherry-pick-pr` の引数で指し、短命ブランチの Pull Request のマージ先になる | — | — | — |
| 短命ブランチ | — | 環境ブランチの HEAD から切り、選んだコミットだけを cherry-pick して環境ブランチ宛の Pull Request を出すためのブランチ（`<元のブランチ>-for-<環境ブランチのスラッシュ以降>`）。マージした後に消す | — | — | — |
| ブランチ戦略の一覧 | — | 選べるブランチ戦略（merge_base_branch・release_branch・trunk）の一覧 | 取り込み方の表、取り込み方の行 | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
| 検証ブランチ | — | マージの前に変更を試すためのブランチ（preview ブランチ） | 検証用ブランチ | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
| 一時マージコミット | — | merge queue が必須のチェックに通すために作る一時のマージコミット（merge group） | 一時のコミット | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
| merge queue への追加 | — | 承認済みのコミットを指定して PR を merge queue へ入れること（enqueue） | キューへの投入 | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
| マージ先 | — | PR をマージする先のブランチ | 宛先 | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
| ブランチ戦略の設定 | — | リポジトリのブランチ戦略・リリース・マージの方法論を表す、1 つの設定ファイルの 1 つのセクション。スプリントの起点・PR のマージ先・結合のしかた・リリースの形と版数の保存先・チャネル・承認を取る場所・マージの主体と方式・マージ前に試す場・CD の有無を持つ | — | — | — |
| 承認前の試用 | — | リリース承認ゲートの前に、変更を入れて使えるところ。ブランチ戦略の設定の値から、開発版のデプロイ・版上げの PR の head からのインストール・verify の試す場のどれかに決まる。pace: auto / fast を使ってよい条件になる | — | — | — |
| 旧い設定 | — | ブランチ戦略の設定へ移す前の、.ndf/worktree.json・.ndf/project.json・.ndf/release.json などに分かれたブランチ戦略・マージ・リリースのキー。移行のコマンドと旧い設定の検査だけが読む | — | — | — |
| 設定の CRUD | — | ブランチ戦略の設定を作る・読む・更新する・消すの主体と時点。作る・更新する・消すは同じ Skill かスクリプトが行い、人やエージェントが手で書かない | — | — | — |
| 値の根拠 | — | 既存リポジトリでブランチ戦略の設定の値を決めた事例（PR・ブランチ・タグ・Release・ruleset・CI のワークフロー）か文書の箇所。番号・パス・行で指し、設定の作成の結果に残す | — | — | — |
| 代表のリポジトリの種類の既定 | — | 代表のリポジトリの種類（web-develop・web-trunk・mobile・docs・plugin）ごとのブランチ戦略の設定の値の行。branch-strategy-presets.json に持ち、新規のリポジトリで設定を作るときに書く | — | — | — |
| 承認の対象 | — | リリース承認ゲートで承認する PR。release_pr（版上げの PR）か production_pr（本番ブランチ宛ての各 PR）で、ブランチ戦略の設定の approval.target が決める | — | — | — |
| 承認の受け方 | — | リリースの承認をどこで受けるか。gate（リリース承認ゲート）か review（GitHub のレビューの承認をゲートの回答として読む）で、ブランチ戦略の設定の approval.via が決める | — | — | — |
