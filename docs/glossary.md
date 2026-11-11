# 用語集

この文書は `docs/glossary/glossary.json` から `glossary.py render` で作る。手で直さない。

## プロジェクトの用語集（`project-glossary`）

各プロジェクトが持つユビキタス言語。語・意味・コンテキスト・廃止した語・正本

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| ユビキタス言語 | — | プロジェクトの関係者とエージェントが、要求・設計・コードで同じ意味に使う語の集まり | — | — | `docs/specifications/ndf-ubiquitous-language.md` |
| 用語集 | `glossary` | ユビキタス言語を持つ構造化ファイル（正）と、そこから作る人が読む Markdown の文書。どちらもプロジェクトのリポジトリに置く | — | — | `docs/specifications/ndf-ubiquitous-language.md` |
| コンテキスト | `context` | 語の意味が 1 つに決まる範囲（境界づけられたコンテキスト） | — | — | `docs/specifications/ndf-ubiquitous-language.md` |
| ドメインモデルの節 | — | 設計文書の先頭に置く節。変更が属するコンテキスト・変える集約とその持ち主・不変条件・ドメインイベントを書く | — | — | `docs/specifications/ndf-ubiquitous-language.md` |
| 廃止した語 | `deprecated` | 用語集で別の語へ置き換えた語。文書に出たら落とす | — | — | `docs/specifications/ndf-ubiquitous-language.md` |
| 不変条件 | — | 集約がいつも満たす条件。設計のドメインモデルの節に書き、実装の前にテストにする | — | — | `docs/specifications/ndf-ubiquitous-language.md` |
| 未登録の語 | `unregistered` | 用語として書かれているのに用語集に無い語。見出しが「用語」の節の表の 1 列目に書いた語を指す | — | — | `docs/specifications/ndf-ubiquitous-language.md` |
| 用語集の取り込み | — | ベースブランチの取り込みで衝突した用語集を、共通の祖先・自分の側・相手の側の 3 つの版から語の単位で合わせ、文書を作り直して取り込みを終えること。`glossary.py merge` が行う | — | — | `docs/specifications/ndf-glossary-merge.md` |
| 語の衝突 | — | 用語集の取り込みで、両側が同じ語（コンテキストと語の組）を違う中身にしたこと、または片側が消して片側が書き換えたこと。コンテキストの `id` でも同じ。スクリプトは解かずに止まる | — | — | `docs/specifications/ndf-glossary-merge.md` |
| agy に配る定義 | — | `plugins/ndf/dev.agy/agents/` の下にあり agy が読むエージェント定義。`plugins/ndf/agents/` から生成した写しで、`model` だけが agy の tier 名に変わる。手で直さない | — | — | `docs/specifications/runtime-plugin-distribution.md` |
| model の対応表 | — | Claude Code のエージェント定義の `model` の値（型の名前）から agy の tier 名への対応。`scripts/lib/agy_agents.py` が持つ | — | — | `docs/specifications/runtime-plugin-distribution.md` |

## NDF の開発ワークフロー（`ndf-workflow`）

NDF が提供する工程・承認ゲート・モード・ステップの語。development-workflow/references/glossary.md が持つ

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 用語集の設定 | `glossary_config` | `.ndf/glossary.json`。用語集の置き場・形式・チェックの対象を持つ | 用語集の宣言 | `declaration` | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 用語チェック | — | `glossary.py check`。用語集の形と、文書の追加した行の語を見る | 語のチェック | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| モデルレビュー | — | 設計 PR のレビューの 1 ラウンド目。ドメインモデルの節だけを見る | モデルの段 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 詳細レビュー | — | 設計 PR のレビューの 2 ラウンド目以降。確定したモデルを前提に残りを見る | 詳細の段 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 仕様のコピー | — | 課題の本文にある要求を、設計 PR と一緒にコミットする `issues/` のファイル | 仕様の写し | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| パイプライン | `pipeline` | キューが `--then` でつないだプランの列（実装 → 検査 → コードレビュー → 開発版 → 本番）。列の 1 つ分がステージ | チェイン | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| セッション | `session` | ラッパーが起動する claude の 1 回の起動（1 つの会話）。番号を付けて「セッション 4」と呼ぶ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| プラン | `plan` | `supervise.py` が流す 1 本の JSON（`plan.json`）。フェーズの手順をステップの列として持つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| キュー | `queue` | プランを空いた枠へ順に流す `supervise.py queue`。終わると結果を done へ書く | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| キューの続き | — | `supervise.py queue --resume <done>`。前の起動のステージの並びを読み、完了していないプランを終えたステップから続け、後続のステージも流す | — | — | `docs/specifications/ndf-supervise-queue-resume-and-gate-evidence.md` |
| リリース差分 | — | 版と版の間（タグからタグまで）の変更 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| インライン実行 | — | 仕事を渡す実行方式の 1 つ。いまの会話の文脈で行う | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| オーケストレーター | — | `cross-refactoring` と `cross-review` で、公開・生成物の同期を持ち、担当を回す側。3 層では conductor に当たる | 進行側、レビューを回す側 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| レッドライン | — | MVV 判定が「従う」でも利用者の承認を省かない操作。NDF の共通原則の `C<番号>`（NDF が持つ）・プロジェクト MVV の `P<番号>`（プロジェクトが足す）・スプリント MVV の `R<番号>` の 3 層 | 越えない線 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 人へ残す判断 | — | 設計文書のうち、レッドラインに当たるため設計者（AI）が決めずに承認ゲートで人が決める判断。判断ごとに当たるレッドラインの番号となぜ当たるかを書く。番号を挙げられない判断は人へ残さず、設計者が決める | — | — | `docs/specifications/ndf-design-ai-decisions-and-mvv-gate.md` |
| 実行条件 | — | プランを流す前に打つコマンド。`skip_code` を返せば worktree を作らずに完了とする | 実行の条件 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 流出不具合 | — | マージ済みの変更に見つかった不具合。直した Pull Request が触った領域を記録し、トリガーに数える | 逃げた不具合 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 検査の記録 | `check_log` | check-trigger.py が検査の事象（eval・check・escape）を 1 行ずつ追記する jsonl（checks/<owner>__<repo>.jsonl）。トリガーの判定と stats が読む | — | — | — |
| 重点領域 | — | `pace: fast` の領域のうち、触った Pull Request の点数を重くするもの | 共通層 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 承認ゲート | `approval_gate` | 人手の承認を求める点。設計承認ゲート（設計 Pull Request のマージ）とリリース承認ゲート（本番の系へ届く操作）の 2 つだけ | 関門 | `gate` | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 承認資料 | — | 承認を求めるときに示すもの。対象を開くためのものと、承認の判断に使うものの 2 層を持つ | 提示物 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| エビデンス | — | 承認資料のうち機械で作れる部分と、conductor が確かめて足した事実。MVV 判定へ渡す | 事実の材料 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリース | `release` | 本番へのデプロイ。フェーズの名前でもある。工程の名前は「デプロイ」である。ベースブランチから本番チャネルへ変更を入れることもリリースと呼び、道筋の違いはリリースの経路の値が持つ | 配布、本番の配布、昇格 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリースプラン | — | リリースの手順をステップの列として持つプラン | 配布の計画、昇格のプラン | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリースコマンド | — | リポジトリが `.ndf/release.json` に宣言し、`release` がリリースの段階に合わせて走らせる 1 つのコマンド | 配布のコマンド | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| インストール確認 | — | 隔離した HOME で ref からプラグインをインストールし、版と中身が ref と一致するかを確かめる | 導入確認 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 導入の確認 | — | `.ndf/pace.json` の `<節>.verify` に書かれたコマンドを、ベースブランチの先頭で走らせること。pace: fast / auto で、リリースの Pull Request で届くときはリリース承認ゲートの前に、経路 merge だけのときは検査の後に走り、終了コードを承認資料へ載せ、出力は所有者だけが読めるログへ分ける | — | — | — |
| リリース完了の確認 | — | 公開が済んだことを、リリース先の状態から読み取れる値 | 完了の事実 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スタックしたチェック | — | 実行が終わったのに pending のまま残った CI のチェック | 取り残されたチェック | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リファクタリング | — | 振る舞いを変えずに構造を直す工程 | 構造改善 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| コードレビュー | — | 実装の差分をレビューし、新しい指摘が出なくなるまで直す工程 | 実装レビュー | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リファクタリング計画 | — | `cross-refactoring` が採る改善項目を決め、見送った提案と理由を残す出力 | 改修計画 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| バッファ | — | `cross-refactoring` の見積りで、想定最大時間から経過を引いた後に残しておく時間。最終ゲートの全体テスト・修正 1 回・最終ゲート修正 1 回と、最終ゲートと兼ねられないときだけ危険フラグの全体テスト | 予備時間 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ラウンドテスト | `round_test` | `cross-refactoring` で、`--scope` のテストの置き場所を走らせるコマンド | ラウンドのテスト | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| グレード | — | `cross-refactoring` が候補ごとに付ける適用の価値（high / medium / low） | 等級 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 指摘ファイル | — | レビューする者（`cross-review` の担当・`pr-review` の LLM か外部 AI）が書く、指摘の全件と総評のファイル | 指摘のファイル | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 進捗記録 | — | 工程に入った時点で 1 回打つ記録。リリース後テストだけは、受け入れ条件ごとの記録を投稿してその工程を終えた時点で打つ。課題の本文の「進行」も同じ 1 回で更新される | 進行の記録、記録のコマンド | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| シグナルファイル | — | ラッパーへ知らせるファイル（`next.json` と `stop`） | 合図 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| アナウンス | — | ndf-next のブロックの直前にそのまま置く 1 文 | 告知 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| アイドル | — | シグナルファイル・会話の記録・利用者の入力が動かない秒数。この秒数がたつまでラッパーは `/exit` を入力しない | 静止 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| パススルー | — | ラッパーを挟まず、本物の claude をそのまま起動すること | 素通し | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 進捗ログ | `progress` | プランの実行中に 1 行 1 つの JSON で追記する記録（`progress.jsonl`） | 途中の報告 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| フェーズレポート | — | supervisor（またはプラン）が最後に返す報告 | フェーズの報告 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| worktree | `worktree` | 開発の変更を行う git worktree。課題ごとに 1 つ切る | 作業ツリー | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| メインディレクトリ | — | リポジトリを clone したディレクトリ | 主ディレクトリ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 宣言を探す木 | — | supervise.py new が .ndf/ の宣言を探すディレクトリの並び。--worktree のパス → --repo → --worktree のパスの /.worktrees/ より前 → 今のディレクトリの順。既定ブランチは、この並びで最初に存在する git の木から解く | — | — | — |
| ベースブランチ | — | worktree の分岐元と Pull Request の宛先 | 起点のブランチ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 安定版と実験版 | — | NDF の変更の 2 つの経路（stable / experimental）。実験版の置き場は `experimental/` | 安定と試行 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 即時修正 | — | 4 つの条件（`development-workflow` の SKILL.md の「即時修正」）を満たす不具合を、起票せず、その場のプランで直すこと | その場で直す | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 棚卸し | — | 既存の課題の本文・マイルストーン・ラベルを現状に合わせ、着手の順位を決め直すこと。スクラムのバックログリファインメントに当たる。工程表の行名（進捗記録の stage の値）でもある | 手入れ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 文言固定テスト | — | リポジトリで追跡している .md を読み、その文字列・見出し・表の並びを照合するテスト。書かない | — | — | `docs/specifications/cross-refactoring-round-tests-and-assess.md` |
| 手順 | — | 1 つの Skill の中で順に通す作業の単位。cross-refactoring の提案・リファクタリング計画・テスト追加・実装・検証/修正の 5 つ、document-rebuild の決める（文書形式・文書の目的・言語）・測る・組み直す・整える・測り直すの 5 つ（手順 0〜4） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 範囲テスト | — | 変更が触った範囲に限って走らせるテスト | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| suite の種別 | `kind` | テストの宣言（.ndf/project.json の test.suites[]）の 1 件がテスト（test）か静的解析（lint。整形の検査を含む）か。キーは kind。書かなければ test | — | — | — |
| 起動の失敗 | — | テストのコマンドのプロセスを起動できない、またはシェルが終了コード 126 / 127 を返したこと。テストが落ちたこととは別に扱う | — | — | — |
| 危険フラグ | `danger` | cross-refactoring で、範囲テストでは覆えない変更（D1〜D5）。立てば全体テストを 1 度走らせる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 設計 Pull Request | — | 要求仕様と設計文書だけを載せ、実装を含まない Pull Request。変更ファイルに issues/ の要求・設計・決定の記録を含む | — | — | `docs/specifications/ndf-design-phase.md` |
| 正本 | `source` | その事柄の定義を持つ唯一の文書。食い違ったときはこれを正とする | — | — | `docs/specifications/doc-consistency-checks.md` |
| conductor | `conductor` | スプリントを持ち、プランを流す役割。人へ問えるかは置かれた場所で決まる（計画の外のセッションは自分で問い、レーンは問いをオーガナイザーへ送る） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| supervisor | `supervisor` | 1 つのフェーズを通す役割。supervise.py が主に担い、supervise.py で対応できないとき（プランの雛形が無いときなど）だけサブエージェント（ndf:supervisor）が代行する。人間へ問わない | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| worker | `worker` | 1 つの作業（調査・修正・検証・集計）を行うサブエージェント。別のサブエージェントを起動しない | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| フェーズ | `phase` | supervisor 1 つ（またはプラン 1 本）が通す、連続する工程のグループ。設計・実装・検査・取り込み・仕上げ・リリースの 6 つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 作業種別 | — | worker 1 つが行う作業の分類。調査 / 修正 / 検証 / 集計、どれにも当たらなければその他 | 作業の種類 | — | `docs/specifications/ndf-agent-layers-unattended-run.md` |
| context window | — | 1 回の会話が保持する文脈の全体と、その量 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| レートリミット中断 | — | 利用上限（429）で層が途中で終わること。記録の ending が rate_limit | 上限の中断 | — | `docs/specifications/ndf-context-window-metrics.md` |
| リセット時刻 | — | 利用上限が解ける時刻（quotaLimits.resetsAt、resets_at） | 解除時刻 | — | `docs/specifications/ndf-context-window-metrics.md` |
| 自動継続 | — | リセット時刻に Claude Code が conductor へ積む入力（origin.kind が auto-continuation） | 自動の継続 | — | `docs/specifications/ndf-agent-layers-unattended-run.md` |
| 親エージェント | — | その記録を起動したエージェント。.meta.json の toolUseId でたどる（parent_agent_id） | 起動元 | — | `docs/specifications/ndf-context-window-metrics.md` |
| 実行前確認 | — | Skill の手順の途中で、操作の対象を示して利用者の同意を得ること。承認ゲートとは別 | — | — | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| 取り消せる操作 | — | 失う状態を git 自身が拒むか、事後の手段（ハッシュからの復元・Restore branch・reopen）で元へ戻せる操作。実行前確認なしで進める | — | — | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| 最終工程 | — | 棚卸しの前にその実行で最後に通る工程。振り返りを通るなら振り返り（retrospective）、通らずリリース後テストを通るならリリース後テスト（release-verification）、どちらも通らなければデプロイ（release） | 終わりの工程 | — | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| ピーク使用量 | — | 応答ごとの入力トークンの合計の最大 | 最大充填 | — | `docs/specifications/ndf-context-window-metrics.md` |
| 合成応答 | — | API を呼ばずに Claude Code が記録へ書いた応答（message.model が <synthetic>） | 合成の応答 | — | `docs/specifications/ndf-context-window-metrics.md` |
| 設計文書 | — | design が作る成果物。issues/ 配下に置く Markdown | — | — | `docs/specifications/ndf-design-phase.md` |
| 企画承認 | — | documentation モードの設計承認ゲート。構成案と体裁設計を載せた設計 Pull Request のマージ | — | — | `docs/specifications/ndf-documentation-mode.md` |
| 制作物承認 | — | documentation モードのリリース承認ゲート。production が真の提出先への操作 | — | — | `docs/specifications/ndf-documentation-mode.md` |
| 下書き先 | — | production が偽の提出先。承認の前に書き込んでよい唯一の場所 | — | — | `docs/specifications/ndf-documentation-mode.md` |
| 実行計画 | — | スプリントの課題を、バンドル・工程ごとの依存・触る箇所・着手できる時点で並べた表。オーケストレーターが持つ | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| バンドル | `bundle` | 1 本の設計 Pull Request で決める課題の集合 | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 課題グループ | — | マイルストーンの説明に書く、触る場所の見込みと依存で分けた課題の集合。実行計画のバンドルの初期値 | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 変更重複 | — | 2 つの Pull Request が同じファイルを触ること。節（見出し）・関数の単位で程度を分ける | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 並行度 | — | 対象の Pull Request のうち、2 本以上が同時に開いていた時間の割合 | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 予備メモリ | — | 同じ VM に常駐する他のプロセスの揺れのために空けておくメモリ。既に動いているオーケストレーターの本体と担当は MemAvailable から引かれているため含めない | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 設定 | — | リポジトリ側に置く .ndf/<名前>.json。無ければその機能は既定の動きだけになるか、何も動かない。「<対象>の設定」の形で呼ぶ（指示書チェックの設定・リリースの設定・ボードの設定・worktree の設定・用語集の設定）。git で追跡するものを共有設定、追跡しないものを個人設定と呼ぶ | — | — | `docs/specifications/ndf-instruction-files-check.md` |
| コピー | — | 元のファイルをそのまま別の場所へ置いたもの。ラッパーの relay.py（版は横の relay.version。新しい版を古い版で置き直さない）・マイルストーンの説明から作る mvv.md・承認資料の issues/approval-*.md | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| カットポイント | — | context window を切ってよい 4 点。3 層ではフェーズの境になる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ラッパー | `relay` | 利用者の claude を包んで常駐し、ndf-next のブロックを拾って /exit・プラグインの更新・次のセッションの起動を行う（Claude Code だけ） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ポーリング | — | 待つ間に、状態を確かめる呼び出しを繰り返すこと | — | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| フォアグラウンド Bash | — | run_in_background を付けずに実行する Bash。終わるまで呼び出しが返らない | 前景の Bash | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| コンテキスト量 | — | 1 回の API 呼び出しで読んだトークン数（input_tokens + cache_read_input_tokens + cache_creation_input_tokens） | 文脈量 | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 工程 Skill | — | development-workflow の工程表が起動する Skill | 工程の Skill | — | `docs/specifications/ndf-worker-agent-and-skill-excerpts.md` |
| 中間通知 | — | 背景の処理を残したまま応答を終えたサブエージェントについて、親へ届く 1 回目の通知 | 途中の通知 | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 報告コピー | — | worker が起動指示の置き場所のファイルの末尾へ書く作業の報告の節。最後の応答の報告と同じ中身 | 報告のコピー | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 完了マーカー | — | worker が報告コピーを書き終えた後に作る空のファイル（<置き場所>.done） | 完了の目印 | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 待ちの雛形 | — | waiting.md が持つ、背景で起動して条件が成り立つまで待つ until ループのコマンド。成り立てば 0、上限に達すれば 124 で終わり、exit=<終了コード> を出す | — | — | `plugins/ndf/skills/development-workflow/references/waiting.md` |
| 横断 Skill | — | 工程表に載らず、どの工程からも呼ばれる Skill（progress-tracking / out-of-scope など） | 工程の外の Skill | — | `docs/specifications/ndf-worker-agent-and-skill-excerpts.md` |
| 抜粋 | — | 呼ぶ側が要る部分だけを Skill の本文から取り出したもの | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 実行方式 | — | 仕事を渡す先の実行の形。インライン実行 / サブエージェント / CLI 実行 / 最小構成の claude -p / スクリプト | — | — | `docs/specifications/ndf-worker-agent-and-skill-excerpts.md` |
| 固定費 | — | 作業を始める前に context window が既に埋まっている分（指示・規約・Tool の定義）。実行を 1 つ起こすたびに掛かる | — | — | `plugins/ndf/skills/development-workflow/references/context-window.md` |
| スケルトン | — | SKILL.md の実行の節に置く bash。利用者はこれをコピーして起動する | 骨組み | — | `docs/specifications/ndf-workflow-blockers.md` |
| 通過記録 | — | 通過工程を課題ごとに残したファイル | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 本番チャネル | — | リリースした版が常用する利用者へ届くブランチ | 本番のチャネル | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| 本番系 | — | 利用者が現に使っているリリースのチャネル・環境・外部サービス。チャネルは本番系の一種 | 本番の系 | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| 必須ルール | — | 分割と並行の手法を担当へ任せたうえで、それでも守る規則 | — | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| プラグインルート | — | リリースした先で scripts/ と skills/ が並ぶディレクトリ | — | — | `plugins/ndf/scripts/lib/README.md` |
| 収束ループ | — | 新しい指摘が出なくなるまで回すレビューと修正。リファクタリング・コードレビュー・ドキュメントレビューが持つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 委譲 | — | 別の文脈（サブエージェント・別の CLI プロセス）へ作業を渡し、結果だけを受け取ること | — | — | `plugins/ndf/skills/development-workflow/references/context-window.md` |
| 実作業 | — | ピーク使用量から固定費を引いた分。その会話が実際に読み書きした量（work） | — | — | `plugins/ndf/skills/development-workflow/references/context-window.md` |
| ボード | — | 進行を記録する GitHub Projects のプロジェクト 1 つ。設定が無ければ何も動かない | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 通過工程 | — | ある課題について、進捗記録が実際に書かれた工程の集合 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 承認ラベル | — | 人間が設計を承認したことを表す Pull Request のラベル。無ければ hook が設計 Pull Request のマージを拒む | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| マイルストーン | `milestone` | 着手の順序を表す単位。スプリントはこの中から切り出す | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 範囲外の課題 | — | この変更の受け入れ条件にも直す対象にも入らない課題。見つけたその場で issue にする | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 再開コマンド | — | 新しい会話の最初に入力すれば、その工程から再開できるコマンド。ndf-next のブロックの中身 | 再開用のコマンド、引継ぎの 1 行 | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 工程 | `stage` | 工程表（モードごとに起動する Skill の表）の 1 行。要求と受け入れ条件・設計・実装・リリースなど | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ステップ | `step` | プランの steps の 1 要素。型は run / work / drive / judge / pr の 5 つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ステージ | `pipeline_stage` | パイプラインの中のプランのグループ 1 つ。中はキューで並列に流し、前のステージがすべて完了したときだけ次のステージが流れる。new sprint はステージごとにプランを書き出す（設計・設計承認ゲート・実装・検査・開発版・本番） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スイッチポイント | — | フェーズの中で supervisor を替える点。収束ループの前で hook が決める | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 3 層 | — | スプリントの中の工程を conductor → supervisor → worker の順に起動して通す形。ロール層のうち conductor・supervisor・worker の 3 つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 最小構成の `claude -p` | — | Tool と指示を絞った 1 回の判断。judge のステップと MVV 判定が使う | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 実行レベル | — | 仕事を渡す実行方式の LLM の使い方の水準。レベル 1 = スクリプト、レベル 2 = 分類の判断、レベル 3 = インライン実行の LLM | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| モード | `mode` | 変更の目的物で決める工程の振り分け。上から operation / documentation / standard / legacy-refactor / light | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| pace | `pace` | モードとは別の軸で、工程をどう通すかを決める。ウォーターフォールで各工程を検証し人の承認を取る normal、normal の承認だけを MVV 判定にする auto、実践投入の中で検証しながら MVV で自動に進める fast の 3 つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| pace: auto | `auto` | 進め方の 1 つ。工程は normal と同じで、設計承認ゲート・リリース承認ゲートだけを MVV 判定で自動にする。「従う」でレッドラインが無いときだけ通し、ほかは利用者へ戻す | — | — | `plugins/ndf/skills/development-workflow/references/pace.md` |
| 実践投入 | — | pace: fast の検証の場所。実装の Pull Request が develop（開発版のチャネル）へ入り、開発版としてデプロイされ使われること。fast の検査はその後にトリガーで通る | — | — | `plugins/ndf/skills/development-workflow/references/pace.md` |
| resume | `resume` | new sprint のマニフェストで then_of のステージが持つ、そのステージから最後までを流す queue のコマンド。承認ゲートで止まった後に続きを流す | — | — | `plugins/ndf/skills/development-workflow/references/pace.md` |
| MVV | — | Mission / Vision / Value。プロジェクト MVV とスプリント MVV の 2 層があり、下位は上位の範囲で具体化する | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| MVV 判定 | — | 承認ゲートのエビデンスが NDF の共通原則・プロジェクト MVV・スプリント MVV に従うかの判定。auto と fast では「従う」でレッドラインが無いときだけ承認ゲートを省き、記録を残す。normal では助言の MVV 判定として承認資料に載せ、承認は人が行う | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 助言の MVV 判定 | — | 承認ゲートの記録（by: mvv）は書かないが、mvv-gate.jsonl へ判定の行（pace 付き）を書き、判定・理由・根拠の項目を承認資料へ載せる MVV 判定（mvv-gate.py check --advise）。normal の承認ゲートの前に走り、承認するのは人である | — | — | `plugins/ndf/skills/development-workflow/references/pace.md` |
| 上位の原則 | — | 「人を守り、人の発展を支える」。NDF の共通原則の最上位の 1 文 | — | — | — |
| プロジェクト MVV | — | プロジェクト全体の Mission / Vision / Value と固有の必ず承認が要る操作（`P<番号>`）。`.ndf/` に宣言し、利用者が承認する。判断の基準の上位 | — | — | — |
| MVV の版 | — | プロジェクト MVV の承認のたびに 1 ずつ上がる番号。改訂の理由と前の版との差分を伴う | — | — | — |
| MVV の改訂 | — | プロジェクト MVV の本文を変え、利用者の承認で新しい版にすること | — | — | — |
| 判断の地点 | — | NDF が LLM の判断を挟む場所（承認ゲートの判定・レビューの指摘と修正の可否・リファクタリングの提案の採否・judge のステップ・範囲外の起票の 3 択） | — | — | — |
| MVV 候補 | — | 材料から書いたプロジェクト MVV の案。2 案以上と分かれる点を利用者へ示す | — | — | — |
| 傾向モード | — | 履歴が育っていないプロジェクトで、README・指示書・依頼文の傾向から MVV 候補を出す抽出の形 | — | — | — |
| MVV の照合 | — | 本文（候補・改訂案・スプリント MVV）が NDF の共通原則とプロジェクト MVV に従うかを「従う / 反する疑い / 判定できない」の 3 択で判定すること。従う以外は人へ戻す | — | — | — |
| MVV の節 | — | 判断の地点へ渡す塊。NDF の共通原則の本文全体を先頭に置き、承認済みのプロジェクト MVV の本文か「MVV なし」とその理由、行動の 2 択と根拠の指示を続ける | — | — | — |
| 根拠の項目 | — | 判断の記録と設計の決定の記録に残す MVV の項目の番号（Mission / Vision / Value 3 / C4 / P1 / R2）。スプリント MVV の項目は頭に「スプリント」を付ける（スプリント Value 4。R の番号はそのまま）。MVV が無ければ「MVV なし」、返されなければ「根拠なし」 | — | — | `plugins/ndf/skills/development-workflow/references/project-mvv.md` |
| 覆し | — | 承認ゲートで人の答えが直前の MVV 判定と食い違ったこと。「反する疑い」「判定できない」を人が通すと override_pass、「従う」を人が差し戻すと override_reject。MVV の改訂の兆候に数える | — | — | — |
| NDF の共通原則 | — | NDF を使うすべてのプロジェクトに効く原則。NDF が持ち、利用側は上書きできない。上位の原則・優先順位・AI の行動の 2 択・判断の記録・人と AI の対話・必ず承認が要る操作 C1〜C8 を含む | — | — | — |
| 改訂の兆候 | — | 人が AI の判断を覆した回数（「従う」を退けた・「反する疑い」を通した）・「判定できない」の回数・流出不具合の件数。現行の版のもとで数え、宣言の閾値を超えたら改訂を提案する | — | — | — |
| トリガー | `trigger` | fast でリファクタリングとコードレビューを流す条件。点数・行数・流出不具合・経過時間・最終の 5 つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| judge のステップ | — | 結果ファイルと規則の抜粋だけを渡し、次のステップを LLM に決めさせるステップ。Tool を持たない | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 決定 | — | judge のステップが返す、次に取る手 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 開発版 | — | ベースブランチ（develop）に載るチャネルと、そこへ出す接尾辞付きの版。マージされた変更がそのまま載る。手動反映の本番系の形では、production: false の行（検証の環境）へ届くことも開発版に当たる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 本番 | — | 利用者が現に使っているチャネル・環境・外部サービス（本番系）。プラグインのリリースでは本番のブランチ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 正式版 | — | 本番チャネルへ出す、接尾辞の無い版。出したらリリースタグを打つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 検査 | — | リファクタリング・コードレビュー・完了判定・Pull Request を通すフェーズ。fast ではトリガーが立ったときだけ、前回の検査からの差分に流す。コードレビューだけは開発版ごとに流す（--review-only） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| チェック | — | 機械が合否を返すもの。CI のジョブと、mvv-gate.py・doc-lint.py などのスクリプト | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 完了判定 | — | コマンドの証跡で完了を判定する工程。スクラムの完了の定義に当たる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 全体テスト | — | リポジトリ全体を範囲にするテストか静的解析。テストは宣言の suites[].command、無ければ範囲テストのテンプレートの {paths} を . にしたもの（その旨を注記に残す）。静的解析は宣言の command、無ければテンプレートの {paths} を範囲のパスで埋めたもの（範囲のパスが無ければ組まず、その旨を注記に残す） | 全体のテスト | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 消したテスト | — | 着手前に走っていて、着手後の結果（走った・skip・deselect）のどれにも無く、テストの台帳に載るテストの nodeid。計測のスクリプトの diff が、着手後の木にその定義が残っていないことを照合する対象 | — | — | `docs/specifications/ndf-whole-test-duration.md` |
| 現状固定テスト | — | 構造を変える前の出力をそのまま期待値にしたテスト。構造を変える前後で同じ出力が出ることを縛る（英語の characterization test） | — | — | `plugins/ndf/skills/refactoring/references/characterization-tests.md` |
| コメントのスナップショット | — | cross-review が取る既存コメントの一覧。2 ラウンド目以降は取り直す | 既存コメントのスナップショット | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| doc-lint | — | 追加した Markdown の行に、検討の痕跡・課題番号の由来・比較の語が無いかを見るチェック | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 引継ぎ文書 | `handoff` | 会話を切って新しいセッションで続けるための文書。メインディレクトリの .ndf/handoff/<名>.md に置き、コミットしない。1 本が 1 つの引継ぎの対象を受け持つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 引継ぎの対象 | — | 引継ぎ文書 1 本が受け持つ仕事のまとまり（並列の計画・開発ループ・スプリント・マイルストーン・課題のどれか）。名の頭（parallel- / loop- / sprint- / milestone- / issue-）になる | — | — | — |
| 引継ぎの履歴 | — | 引継ぎ文書の本体から終わった項目を移す文書。本体と同じ場所の <名>-history.md | — | — | — |
| ndf-next | — | 次のセッションの最初の入力を置く、情報文字列が ndf-next のコードブロック。最後の応答に 1 つだけ置く | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| step / alive / worker / attention | — | 進捗ログの行の種類。ステップの切り替わり・動きの無い間の生存・worker の進み・conductor の判断が要る出来事（止まった・承認ゲート・同じ失敗の繰り返し・judge のステップで stop が出そう） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| done | — | キューが終わったときに書く結果の JSON。wait は done か attention の行まで待つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 結果 JSON | `step_result` | 手順のスクリプトが返す 1 行の JSON。status と終了コードで読み、成功は ok だけ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 実装計画 | — | implementation-plan が issues/ に書く、実装の前の計画 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 確定仕様化 | — | 完了した実装計画を docs/ の確定仕様へ書き直す工程 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 振り返り | — | 進め方で変えることを記録し、起票の取りこぼしを拾う工程 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ライブラリ | `library` | 2 つ以上のスクリプトが使う関数とクラスの置き場。今の plugins/ndf/scripts/lib/ に当たる。pace.json の「共通層」（点数を重くする領域）とは別 | — | — | — |
| エントリポイント | `entry_point` | Skill・プラン・hook・宣言・利用者が呼ぶスクリプトのパスと副命令と引数と出力の形 | — | — | — |
| 移行ステップ | `migration_step` | 設計の決める移行の順序の 1 つ分。1 本の PR で閉じる。プランの「ステップ」とは別 | — | — | — |
| 使用量の帳簿 | `usage_ledger` | NDF が起動したエージェントの呼び出し 1 回ごとに、版・呼び出し元・モデル・使用量を 1 行で追記する jsonl。ランタイムごと（Claude・codex・kiro・agy・jev。jev は帳簿だけのランタイムで、席順の ALL_RUNTIMES には入れない）のディレクトリに、リポジトリごとのファイルを置く（<帳簿のディレクトリ>/<ランタイム>/<owner>__<repo>.jsonl） | — | — | `docs/specifications/ndf-usage-ledger-by-runtime.md` |
| 構造チェック | `structure_check` | テストを除くスクリプトの行数と、本体の同じ関数・同じ名前で本体の違う関数を構文木で数える継続的統合のチェック。ファイルを指定すると、検査は木全体で行い、合否を指定したファイルに関わる違反だけで決める | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| プロジェクトの宣言 | — | リポジトリの根の `.ndf/` に置く、そのプロジェクトの形（言語・テスト・CI・ブランチ・デプロイ・課題の正本など）を表すファイルの集まり。機能ごとの JSON と、解析が書く `project.json` からなる | — | — | — |
| 解析 | — | プロジェクトの宣言を作るために、リポジトリと CI を決定論で測り、測った値から宣言の中身を決めること | — | — | — |
| 不明 | — | 解析が測れなかった・決められなかった項目の値。ai-plugins の値で埋めない | — | — | — |
| 測った値 | — | 解析の測定が、ファイル・git・`gh` の出力から決定論で決めた項目の値 | — | — | — |
| 問い | — | 解析の測定が値を 1 つに決められず、候補と根拠を付けて conductor へ渡す項目 | — | — | — |
| 答え | — | 解析の問いごとに conductor が選んだ値か不明。書き出しが形を確かめて宣言へ書く | — | — | — |
| ジョブを飛ばした run | — | CI の成功した run のうち、conclusion が skipped のジョブを 1 つ以上含むもの。宣言の解析は CI の所要を測る代表に採らない | — | — | `docs/specifications/project-decl-ci-representative-run.md` |
| 代表の run | — | 宣言の解析がワークフローごとに壁時計とテストの所要を測る 1 件の run。取得した候補（ワークフローごとに新しい順の成功の run を最大 5 件）のうち、ジョブを飛ばしていない最新のもの | — | — | `docs/specifications/project-decl-ci-representative-run.md` |
| 入力の指紋 | — | 解析が読んだファイルの、HEAD の木での blob の SHA と、ブランチの構成の組。プロジェクトの宣言の新しさの判定に使う | — | — | — |
| テストの戦略 | `strategy` | 範囲テストの走らせ方・全体テストの置き場（手元か CI か）・落ちたテストの見分け方の組。local-full / local-scoped-ci-whole / round-only の 3 つ。宣言の test.strategy か、同じ関数が所要から導く（#1334） | — | — | — |
| 範囲テストのテンプレート | `scope_command` | {paths} を引用の外の 1 字句として含むテストのコマンド（宣言の scope_command か、{paths} を含む引数）。{paths} をシェルの引用で守った対象の並びへ置き換え、シェルで走らせる。{paths} には、改修の範囲テストではテストのファイルのパス、CI に任せる戦略の着手前のテストではテストの置き場所（ディレクトリかファイル）、静的解析では変更したファイルのパスが入る | 範囲テストの雛形 | — | `docs/specifications/cross-refactoring-scope-template-probe.md` |
| JUnit の置き場 | `junit` | テストのコマンドが JUnit XML を書くファイルの、作業ディレクトリからの相対パス（宣言の suites[].junit）。NDF はコマンドへ引数を足さず、このファイルを読む | — | — | — |
| スプリント | `sprint` | 1 回のリリースとして出す課題と Pull Request のセット。版数を持つプロジェクトでは 1 つの版になる。期間ではなく、1 回のリリースとして出す中身で切る。工程はスプリント単位で 1 回ずつ通し、モードもスプリントで 1 つにする | ミッション | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スプリント状態ファイル | `sprint_state` | スプリントのプラン・done・承認ゲートの記録・MVV・版を持つファイル（パスは呼ぶ側が決め、手順書の例は `sprint-state.json`。目録 `sprint.json` とは別のファイル） | ミッション状態ファイル、ミッションの状態 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 開発ループ | — | スプリントを 1 つ終えるたびに、棚卸しの結果から次のスプリントを選んで続けて始める進め方。引数で切り替え、止まる条件に当たるまで繰り返す | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| ループの 1 回 | — | 開発ループの 1 回分。次のスプリントの選定・スプリント 1 つ・棚卸しと止まる条件の判定からなり、スプリントと 1 対 1 | 周回 | — | `docs/specifications/ndf-workflow-development-loop.md` |
| 選定 | — | 開発ループの 1 回で、実装する課題を棚卸しの順位と記録から選ぶ処理。選ばなかった課題には落選の理由を残す | — | — | `docs/specifications/ndf-loop-select-prerequisites.md` |
| 前提の課題 | — | ある課題が依存する、別の open の課題。棚卸しが記録した依存と並列の組の依存から順位の算出が合成した辺の先にある。閉じるまで、依存する課題は選定で選ばれない | — | — | `docs/specifications/ndf-loop-select-prerequisites.md` |
| ループの予算 | — | 開発ループが使ってよい量の上限。ループの回数・費用・経過時間で数える。どの単位も宣言に数を書いたときだけ効き、書かない・null は無制限である | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| 止まる条件 | — | 開発ループを止める、機械で判定できる条件。予算を超えた・同じ失敗が続いた・着手できる課題が無い・MVV の外の課題しか残らない・利用者が止めた | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| ループの記録 | — | 開発ループの 1 回ごとの選定・承認ゲートの判定・版・費用と秒・止まる条件の判定を持つ記録。メインディレクトリの .ndf/loop/<名>/loop.json が正本で、laps.md はそこから描画する | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| スプリントブランチ | — | 課題の Pull Request を集め、ベースブランチへの Pull Request をスプリントで 1 本にするブランチ（`sprint/<名前>`） | ミッションブランチ、ミッションのブランチ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スプリント課題 | — | 「スプリントを閉じる」が閉じる対象にする課題。スプリントの Pull Request の本文が GitHub の closing keywords（PR 本文に `Closes #123` のように書くと、マージ時にその課題を自動で閉じる機能）か閉じる予定の参照で指す課題と、--issues で渡した課題の和 | ミッション課題、ミッションの課題 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| closing keywords | `closing_words` | GitHub の機能。Pull Request の本文に `Closes #123` のように書くと、マージのときにその課題を自動で閉じる。キーワードは Closes / Fixes / Resolves と活用形で、課題の参照が続く。GitHub を使う人なら誰でも知っている機能ではないため、文書では使う箇所ごとに説明を添える | 閉じる語 | — | `docs/specifications/ndf-sprint-close-referenced-issues.md` |
| 参照だけの課題 | `referenced_only` | スプリントの Pull Request の題か本文が番号で参照し、スプリント課題に入らない開いた課題 | — | — | `docs/specifications/ndf-sprint-close-referenced-issues.md` |
| スプリント MVV | — | スプリント単位の MVV。プロジェクト MVV の範囲での具体化 | ミッション MVV | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 自動反映の本番チャネル | `auto_production_branch` | 本番チャネルのうち、マージ（push）で本番系への反映が自動で始まるもの。宣言の delivery に kind: auto で本番チャネルを branch に持つ行があるときに当たる。そこへのマージはリリース承認ゲートに当たる | — | — | — |
| 手動反映の本番系 | `manual_production` | リリースの設定の行のうち、production: true と宣言され、kind: manual（担い手が手で起こす）のもの。そこへ届ける操作の前にリリース承認ゲートを掛ける。自動反映の本番チャネルと対になる | — | — | — |
| 載った版 | — | PR が載った正式版。CHANGELOG の版の節 → マージの時刻の後の最初の正式版のタグ → 行の時刻の後の最初の正式版のタグ、の順で決める。計測で版を寄せる規則 | — | — | — |
| 動いた版 | — | 呼び出しや会話を動かした NDF の版（使用量の帳簿の ndf_version・会話の Skill の置き場の版） | — | — | — |
| 耐久ワークフロー | `durable_workflow` | DBOS の @DBOS.workflow の関数の 1 回の実行。ID を持ち、落ちた後の起動が記録から続ける。プラン 1 本・キュー 1 本・drive 1 回・投稿の項目 1 件がそれぞれ 1 つ | — | — | — |
| 耐久ステップ | `durable_step` | 耐久ワークフローの中で出力を SQLite へ記録する単位（DBOS の @DBOS.step）。記録のある耐久ステップは続けるときに流し直さない。プランのステップとは別の語 | — | — | — |
| 耐久キュー | `durable_queue` | DBOS の register_queue で作る、同時の本数と分割を持つ待ち行列。supervise.py queue（キュー）とは別の語 | — | — | — |
| 耐久の記録 | `durable_store` | 1 回の起動が持つ DBOS の SQLite のファイル（~/.local/state/ndf/dbos/<種類>-<鍵>.sqlite） | — | — | — |
| 実行の鍵 | `run_key` | 起動を識別する文字列（run-<プランのパスの sha256 の先頭 12 字> など）。耐久の記録のファイル名と executor_id に使う | — | — | — |
| 実行の回 | `attempt` | 同じ実行の鍵の中で、耐久ワークフローを頭から流した 1 回。ID の末尾の番号 | — | — | — |
| 結果を変える引数 | `result_args` | cross-review・cross-refactoring の drive が `init` へ渡す引数のうち、置き場・出力だけを変えるものを除いた残り。完了の記録に残し、打ち直しで今回と比べる | — | — | `docs/specifications/cross-drive-rerun-pause-and-denials.md` |
| 資源のタグ | `resource_tag` | ステップが使う、共有する外部の枠の名前（今は graphql だけ） | — | — | — |
| 資源の枠 | `resource_limit` | 資源のタグごとの、同時に流せるステップの本数の上限（.ndf/supervise.json の queue.resources） | — | — | — |
| 重なりの組 | `overlap_pair` | 同じステージの 2 本のプランで、触るファイルに等しいファイルの宣言を持つか、同じ共有の一覧に当たるもの。ディレクトリの宣言は判定に使わない。同時には流さない | — | — | — |
| 偽の重なり | — | 重なりの組と判定された 2 本のプランのうち、両方の実装の PR が実際に変えたファイルに共通するものが 1 つも無い組 | — | — | — |
| ディレクトリの宣言 | — | 触るファイルの 1 行で、末尾が / のもの。設計の結果のステージは作業場所の既存のディレクトリをこの形に揃える。重なりの組の判定と除外の行に使わない | — | — | — |
| 共有の一覧 | `shared_list` | 複数のプランが同じ行の並びへ書き足すファイル（索引など）。.ndf/supervise.json の queue.shared に書く | — | — | — |
| 除外の行 | `concurrent_rule` | queue が実装の担当の指示文の末尾に足す 1 行。担当を起動する時点で走っている他のプランの触るファイルのうち、ディレクトリでないものを変えないよう求める | — | — | `docs/specifications/ndf-supervise-queue-concurrent-files-and-prerequisites.md` |
| 前提のプラン | `after` | プランの JSON の `前提のプラン` に名前で挙げたプラン。その結果が完了でなければ、queue は挙げた側のプランを流さない | — | — | `docs/specifications/ndf-supervise-queue-concurrent-files-and-prerequisites.md` |
| スプリントの雛形 | — | `supervise.py new sprint` がステージごとのプランを作る関数（`sprint_waves.py` の `plan_sprint_*` / `plan_fast_*`） | — | — | — |
| 単発の雛形 | — | `supervise.py new impl / fix / check` が 1 本のプランを作る関数（`templates.py`） | — | — | — |
| 取り込んだ課題 | — | 受け入れ内容を別の課題の実装へ含めると決めた課題。設計の結果の表か `new sprint --absorb` で決める。実装のプランは `実行の条件` で飛ばして実行しない。`--absorb` で決めたときは自分の設計のプランを持たない | — | — | `docs/specifications/ndf-supervise-queue-resume-and-gate-evidence.md` |
| 承認の証跡 | — | 承認ゲートで利用者が承認したことを示す、プランの外で残った記録。スプリント状態ファイルの承認ゲートの記録（`--by user --outcome approved`）・承認ラベル・承認資料の承認の記録のどれか。プランのステップは承認の証跡があるときだけ、承認を前提にした操作へ進む | — | — | `docs/specifications/ndf-supervise-queue-resume-and-gate-evidence.md` |
| 手動確認の行 | — | 要求の検証手段の表で、項目が「手動確認」で始まり、2 列目が「無し」の形（「無し」「なし」「不要」「ない」「-」「—」「―」の単独か、直後に括弧・句読点・空白が続く形）でない行。項目が「手動確認（マージ前）」ならマージ前、それ以外はリリース後テストに確かめる | — | — | — |
| 記録の PR | — | リリース記録（## リリース記録）を置いた Pull Request。まとめの close が --record-pr に受け、リリース後テストが記録を書く先。まとめを流し始める時点に解決する（#2230） | — | — | `docs/specifications/ndf-sprint-close-record-pr-at-run-time.md` |
| 記録の置き場が分からない | — | まとめの close が読むリリース記録の Pull Request が分からない状態。sprint-close.py --record-pr unknown で渡し、課題を開いたまま残す。まとめのプランは組まず（#2230）、手で打つ入口と前の版で組んだプランだけが渡す | — | — | `docs/specifications/ndf-sprint-misjudgments-unknown-record-and-manual-none.md` |
| 設計の結果 | — | 設計文書の「設計の結果」の節の表。課題ごとに扱い（実装する / 取り込む / 閉じる）・取り込み先・触るファイルを書き、実装のステージの前に機械が読む | — | — | — |
| 決定の見出し | — | 設計文書の囲みの外にある、決定 1 件を表す見出しの行。`## 決定の記録` で始まる H2 の節の `### ` の行か、H1 に「決定の記録」を含み `## 決定の記録` の節を持たない文書の `## 決定 <数字>…` / `### 決定 <数字>…` の行。pr-body-decisions.sh が設計 PR の本文の「決めたこと」へ写す | — | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| 決定を分けたファイル | — | 設計文書から決定の記録の節だけを別に置いたファイル。名前は `issue-<番号>-design-decisions.md` | — | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| 決定を読めない文書 | — | 設計 PR で変更した .md のうち、`## 決定の記録` で始まる H2 を持つか決定を分けたファイルであるのに、決定の見出しが 0 件のもの。文書自身の名前が `-design-decisions.md` で終わらないときに限り、決定を分けたファイルの名前だけを示す節は除く。pr-body-decisions.sh の check / sync が 1 で止まる | — | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| hook の上限 | — | ランタイムの hook の定義（hooks/*.json・dev.agy/hooks.json）の timeout。過ぎるとランタイムが hook を打ち切る | — | — | `docs/specifications/cross-review-reopen-and-hook-deadline.md` |
| 実装の上限 | `impl_timeout` | プランの impl のステップ（worker の実装）の経過時間の上限（秒）。.ndf/supervise.json の impl_timeout か supervise.py new の --impl-timeout に書いたときだけ効き、書かなければ無い | — | — | `docs/specifications/ndf-supervise-step-budgets.md` |
| hook の締め切り | — | NDF の hook が自分で決める 1 回の実行の時間の上限（3.5 秒）。hook の上限より短く、過ぎたら残りの判定を飛ばして通し、標準エラーへ 1 行残す | — | — | `docs/specifications/cross-review-reopen-and-hook-deadline.md` |
| 適用範囲 | — | 設計した変更が働く範囲。このリポジトリだけで働くのか利用先のリポジトリでも働くのか、プロジェクトごとに違うものを設定か引数のどちらで受けるのか。設計文書の節と承認資料の行に書く | — | — | `docs/specifications/ndf-design-phase.md` |
| あるべき姿の根拠 | — | 変更後の形が適切だと言える根拠。外部の一次情報・実測・利用者の指示の原文のどれか。設計文書の節と承認資料の行に書き、無ければ「無し（理由）」。MVV の根拠の項目とは別のもの | — | — | `docs/specifications/ndf-design-phase.md` |
| ジョブの識別子 | — | 継続的統合のジョブを指す `<ワークフローのファイルのパス>#<job id>`。`#<job id>` を省くとそのファイルのジョブすべてを指す。宣言の suites[].ci_jobs と test.ci_exempt が使う | — | — | `docs/specifications/cross-refactoring-lint-suite-and-ci-coverage.md` |
| 除外したジョブ | `ci_exempt` | 手元の検証で走らせないと宣言したジョブ。宣言の test.ci_exempt に理由と組で書く | — | — | `docs/specifications/cross-refactoring-lint-suite-and-ci-coverage.md` |
| 必須のチェック | — | ブランチの ruleset の required_status_checks に context の名前で載ったチェック。落ちるか結果が無いとマージを塞ぐ | — | — | `docs/specifications/ci-required-checks-reconcile.md` |
| チェックの名前 | — | ジョブの結果が Pull Request に載るときの名前。ジョブの name:（無ければ job id）に matrix の値が付いたもの。必須のチェックはこの名前で照合する | — | — | `docs/specifications/ci-required-checks-reconcile.md` |
| 必須にしないジョブ | — | 必須のチェックとの突き合わせで差に数えないと、理由と組で宣言したジョブ | — | — | `docs/specifications/ci-required-checks-reconcile.md` |
| 突き合わせのチェック | — | ワークフローのジョブから決まるチェックの名前と、宛先のブランチの必須のチェックと、必須のチェックの宣言を突き合わせ、差があれば落ちるチェック | — | — | `docs/specifications/ci-required-checks-reconcile.md` |
| 追加待ちのチェック | — | 必須にすると決めたが、ruleset へ足す承認（リリース承認ゲート）を待っているチェック。突き合わせのチェックは差に数えない | — | — | `docs/specifications/ci-required-checks-reconcile.md` |
| 依存の欠け | — | 手順のスクリプトが deps.require で外部パッケージを用意できずに止まったこと。終了コード 69 で終わり、status は stopped。「飛ばしてよい」とも「前提が無い（3）」とも読まない | — | — | `docs/specifications/ndf-deps-missing-exit-code.md` |
| 置き換わった失敗 | — | PR の先頭のコミットで、同じチェック（`workflowName` と `name` の組）により新しい項目があるときの古い項目の失敗。CI のチェックの結論に数えない | — | — | `docs/specifications/ndf-ci-wait-superseded-failures-and-infra-wait.md` |
| 基盤待ち | — | Runner が付かずに取り消された CI のジョブ（Runner が付かなかった取り消し）があり、ほかに中身の失敗が無い状態。修正へ回さず、再実行して待ち直す。待ち切れなければ `merge-when-green` は終了コード 75 で止まる | — | — | `docs/specifications/ndf-ci-wait-superseded-failures-and-infra-wait.md` |
| Runner が付かなかった取り消し | — | 結論が `cancelled` で、ジョブのステップが 0 件、Runner の名前が空の CI のジョブ。GitHub Actions の障害や Runner 不足で起き、REST のジョブの照会で見分ける | — | — | `docs/specifications/ndf-ci-wait-superseded-failures-and-infra-wait.md` |
| push 前の検査 | — | `core.hooksPath` の `pre-push` フックが `git push` の前に打つ検査。不合格なら push が拒まれる。中身はプロジェクトごとに違う | — | — | `docs/specifications/ndf-sprint-acceptance-check-and-prepush-fix.md` |
| push の修正 | — | 実装のプランで、push 前の検査の不合格を直してコミットし、`pr` のステップを打ち直させる修正の worker（`fix-push` のステップ） | — | — | `docs/specifications/ndf-sprint-acceptance-check-and-prepush-fix.md` |
| 1 本の重さ | — | 担当 1 つ（worktree 1 つ分）が使う cgroup の anon の量。実測（per_lane_observed_mib）と、本数の判定に使った値（per_lane_used_mib）を分けて出す | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| anon の起点 | — | 実行計画の開始時（動いている本数が 0）に測った cgroup の anon。1 本の重さを測る差の基準 | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 判定の区分 | — | parallel-measure.py capacity が本数を決めた理由の区分。grow（足してよい）/ hold（今の本数を超えて足さない）/ shrink（1 本減らす） | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 未解決のスレッド | — | Pull Request のレビュースレッドのうち、解決済みの印が付いていないもの。`gh_parts.py pr-info --with threads` が返す | — | — | `docs/specifications/pr-review-steps-script.md` |
| 本来の判定 | `intent` | レビューの指摘から決めた event（APPROVE / REQUEST_CHANGES / COMMENT）。自分の Pull Request へ COMMENT で送っても結果に残し、収束や報告はこちらを読む | — | — | `docs/specifications/pr-review-steps-script.md` |
| レビューの文脈ファイル | — | `pr-review-steps.py collect` が書く、レビューの対象・差分の在りか・未解決のスレッド（位置と最初のコメントの本文）・指摘ファイルの書き方をまとめたファイル。外部 AI へのプロンプトはこれに観点と委譲の決まりを足して組む | — | — | `docs/specifications/pr-review-steps-script.md` |
| 仕様適合 | — | レビューの第 1 段。受け入れ条件・不変条件・対象範囲・テストが仕様を表すかを見る。満たさない指摘は指摘ファイルで段 `spec` を持つ | — | — | `docs/specifications/pr-review-steps-script.md` |
| 設計との突き合わせ | — | 完了判定で、設計に名前の出るテスト・既存の確定仕様と変更履歴・設計の決定を実装と照らし、食い違いを残すこと。確認 (a)(b)(c) の 3 つからなる | — | — | `docs/specifications/quality-gates-design-match.md` |
| 独立に求めた値 | — | 段階 4 のうち実データに対する結合確認で動かすコードを通さずに、設計・要求の段階で実データについて求めた件数・金額・行数などの値。出力から逆算した値は当たらない | — | — | `plugins/ndf/skills/quality-gates/references/definition-of-done.md` |
| 独立に求めた値との突き合わせ | — | standard の完了判定で、段階 4 のうち実データに対する結合確認の出力を独立に求めた値と比べ、出所・期待値・出力の値・一致したかを証跡に残すこと。値が無ければ「突き合わせる値が無い」と書き、実データに対する結合確認が無ければ「対象が無い」と書く。フィクスチャで回す結合テストの出力は比べない。設計との突き合わせ (a)(b)(c) には含めない | — | — | `plugins/ndf/skills/quality-gates/references/definition-of-done.md` |
| 設計に名前の出るテスト | — | 設計文書の「決定の記録」「テスト設計」「構成要素」に、バッククォートで囲んで書かれたテストファイルのパス・<パス>::<名前>・「テスト設計」の test で始まる識別子 | — | — | `docs/specifications/quality-gates-design-match.md` |
| 既存の確定仕様 | — | 比べるベースブランチとの merge-base の時点で確定仕様の置き場にあった文書。同じ差分で足した確定仕様は含まない | — | — | `docs/specifications/quality-gates-design-match.md` |
| 設計の決定 | — | 設計文書の「決定の記録」の 1 件（### 決定 N）。judge のステップが返す「決定」とは別の語 | — | — | `docs/specifications/quality-gates-design-match.md` |
| 設計と違う点 | — | 実装 PR の本文の節。設計の決定・設計に名前の出るテスト・既存の確定仕様・受け入れ条件と実装の違いを、箇所・違い・理由の行で並べる | — | — | `docs/specifications/quality-gates-design-match.md` |
| 受け入れ条件の番号 | — | 課題の本文の `## 受け入れ条件` の節で、コードブロックの外にある行頭のチェックボックスの行を上から数えた順（1 から）。リリース後テストの表の `受け入れ条件` の列は、セルの頭に `<番号>.` で書く | — | — | `docs/specifications/ndf-sprint-close-acceptance-criteria.md` |
| 受け入れ条件の印 | — | 受け入れ条件の番号に合格の行がそろった課題を閉じるとき、sprint-close.py がその行のチェックボックスへ付ける `[x]` | — | — | `docs/specifications/ndf-sprint-close-acceptance-criteria.md` |
| 起票者の推定 | — | 課題の題・本文に、利用者の指示の原文に無く起票者（課題を書いた AI）が文書・コード・経緯から推して足した条件。設計承認ゲートで確かめるまで利用者の決定として扱わない。扱い（受け入れ条件・前提・含まない・採らない）を書いた推定は設計者が決めた判断として読み、MVV 判定が承認ゲートを省いたときはその判定が確かめた記録になる | — | — | `docs/specifications/ndf-design-ai-decisions-and-mvv-gate.md` |
| 承認ゲートの差し戻し | — | 承認ゲートで利用者が承認せず、対象を直すよう戻したこと。MVV 判定の有無を問わず 1 回を 1 件と数える。MVV 判定との食い違いである覆しとは別の数 | — | — | `docs/specifications/ndf-gate-rejections-and-filer-assumptions.md` |
| 差し戻しの記録 | — | 承認ゲートの差し戻しを 1 回 1 行で追記する JSONL（NDF_MVV_STATE_DIR の gate-rejections.jsonl）。pace と状態ファイルの有無によらず唯一の置き場で、覆しの記録とは別のファイル | — | — | `docs/specifications/ndf-gate-rejections-and-filer-assumptions.md` |
| 状態の置き場 | — | `supervise.py run` の `--state-dir`（省けば `<プラン>-state/`）。`progress.jsonl`・`report.md`・`state.json` を置き、`--from` の再開が前の実行の記録を読む | — | — | `docs/specifications/ndf-supervise-resume-pr.md` |
| 呼び出し元 | — | エージェントの呼び出しを起動した Skill またはスクリプト（帳簿の行の source）と、そのステップの役割（kind）の組。表示は source:kind（例: supervise:work・cross-review:review） | — | — | `docs/specifications/ndf-usage-ledger-by-runtime.md` |
| 起動の共有処理 | — | LLM の CLI と Jev を起動する処理を集めた 4 か所（launch-cli.sh・supervise_lib/claude.py の call_claude・lib/auth.py のモデルの確認・lib/jev.py の _post）。起動の後に使用量の帳簿へ 1 行を書く | — | — | `docs/specifications/ndf-usage-ledger-by-runtime.md` |
| Claude の帳簿の全体 | — | Claude の使用量の帳簿の claude -p の行（usage）とサブエージェントの行に、行ごとの modelUsage の和から usage と親を同じくするサブエージェントの行を引いた残り（hook の効かない claude -p の中のサブエージェント）と、会話の記録だけの claude -p の会話（セッション ID が帳簿に無いもの）を足した値。同じ呼び出しは 1 回だけ数える。トークン削減の効果を測る主な値 | — | — | `docs/specifications/ndf-usage-ledger-by-runtime.md` |
| 会話の記録だけの呼び出し | — | 使用量の帳簿に行が無く、Claude Code の会話の記録（~/.claude/projects/）にだけ残る claude -p の会話とサブエージェント。帳簿ができる前の分と、NDF のコードの外で打った claude -p が当たる | — | — | `docs/specifications/ndf-usage-ledger-by-runtime.md` |
| ステップの上限 | — | プランの 上限。1 本の実行で流すステップの数の上限で、超えると「止まった」で終わる。ループの歯止め | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| 修正のループ | — | 検査のステップが落ちて修正の work へ行き、同じ検査へ戻ってくる遷移の輪 | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| 遅れの見張り | `slow` | ステップが想定の時間を超えたときに一次の調査を流し、待つ・打ち切る・再試行するを決める仕組み | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| 前の実行 | — | run --from で再開したとき、今の実行より前に同じプランを流した実行。--from を付けない最後の実行の始まりより後の記録を指す | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| 失敗の遷移 | — | ステップが落ちたときの行き先。on_fail・on_section_missing・on_exit の 0 以外の終了コードの行き先 | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| ループを通らない最長の経路 | — | プランの並びで後ろのステップへ行く遷移だけをたどった、最も長い経路。数はステップの数で、流し始めるステップを含む | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| worker のセッションの記録 | — | 会話を残す claude -p（Skill を回す work のステップ）が CLAUDE_CONFIG_DIR の projects の下へ書き足していく 1 本の JSONL。遅れの見張りが大きさの伸びを進みに数える | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| 戻りの輪 | — | 修正の work を含まず、kind が「戻り」のステップを含む遷移の輪。戻りの上限の回数だけ回るものとして上限に数える | — | — | `docs/specifications/ndf-supervise-plan-limits-and-slow-watch.md` |
| ベースブランチの取り込み | — | ベースブランチの先端を作業ブランチへマージのコミットで取り込み、衝突を解いてから合成の上で検査を続けるプランのステップ。用語集だけの衝突は用語集の取り込みで解き、ほかは修正の worker へ回す | — | — | `docs/specifications/ndf-supervise-base-sync.md` |
| 補正 | — | conductor が雛形の書いたプランへ手で足した指示や値。プランを書き直す側は消さない | — | — | `docs/specifications/ndf-supervise-impl-prompt-format-and-transient-failures.md` |
| 整形 | — | 規則を変えずにコードの形だけを揃えるコマンド。何を打つかはプロジェクトの宣言が持つ | — | — | `docs/specifications/ndf-supervise-impl-prompt-format-and-transient-failures.md` |
| 一時的な失敗 | — | GitHub や git の呼び出しが 0 以外で終わり、打ち直せば通りうる失敗（5xx・接続の切断）。打ち直しても残れば、読めなかったとして止まる（#1941） | — | — | `docs/specifications/ndf-supervise-impl-prompt-format-and-transient-failures.md` |
| ランナー | — | 作業を動かす 1 台。スタンドアロンではマシンそのもの、Factory を使う形では実行を受け取る手元のマシンか ECS のタスク。Factory の実行ジョブの中のプログラムはランナーのエージェントと呼ぶ | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| 依存グループ | — | lib/deps.py の GROUPS と pyproject.toml の extra に並ぶ、外部パッケージの用途ごとのまとまり（procs・locks など）。deps.require へ名前で渡す | — | — | `docs/specifications/ndf-deps-require-groups-check.md` |
| 未宣言のグループ | — | モジュールから import をたどって届く外部パッケージの依存グループのうち、そのモジュールの中で覆う deps.require が無いもの。そのモジュールを読む側が引き受ける | — | — | `docs/specifications/ndf-deps-require-groups-check.md` |
| 任意の依存 | — | 無ければ代わりの手段へ落ちる import。except ImportError / ModuleNotFoundError で受けるか、同じ関数の中で find_spec / deps.importable で確かめ、確かめが成り立ったときだけ通る枝で読む | — | — | `docs/specifications/ndf-deps-require-groups-check.md` |
| 作業の報告 | `work_report` | worker が応答の最後に書く「## 作業の報告」の節。人と conductor が読む。成否は作業の結果ファイルで決め、この節の文からは決めない（#2337） | — | — | `docs/specifications/ndf-supervise-work-step-web-tools-and-report-stop.md` |
| 起こし直し | `resume_prompt` | supervise の full のステップで、作業の結果ファイルが無いまま応答が終わったとき、同じ会話（--resume）へ続けるよう送ること。3 回まで（#2337） | — | — | `docs/specifications/ndf-supervise-work-step-web-tools-and-report-stop.md` |
| 所要の記録 | — | `.ndf/project.json` の `test_duration.measured` にある全体テストの所要（秒。w）。出所は ndf-record → ci-junit → ci-steps の順に採る。宣言の解析が書き、プランの作成と test-run.py が読む | — | — | `docs/specifications/ndf-supervise-test-limits-from-whole-runs.md` |
| 全体テストの実測 | — | 同じリポジトリで先に走った全体テストの壁時計の秒。所要の記録とは別に残し、後のプランと test-run.py が予算なしのテストの上限を決めるときに読む（#1631） | — | — | `docs/specifications/ndf-supervise-test-limits-from-whole-runs.md` |
| テストの上限 | `limits` | test_strategy.limits が返す init_test_timeout・test_timeout・whole_timeout（秒）。ステップの timeout はこれに with_margin の余裕を足した値で、テストの上限には余裕を足さない | — | — | `docs/specifications/ndf-supervise-test-limits-from-whole-runs.md` |
| 全体テストの鍵 | `whole_key` | 戦略の全体テストのコマンドの集合から作る識別子（whole_key）。全体テストの実測をコマンドごとに分け、コマンドの違う実測を混ぜない（#1631） | — | — | `docs/specifications/ndf-supervise-test-limits-from-whole-runs.md` |
| 予算の既定 | — | 開発ループの予算の単位ごとの既定の値。リポジトリの .ndf/pace.json の loop.budget の値で、宣言が無い単位は無制限。既定へ戻す指示を受けた単位はこの値へ戻る | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 既定へ戻す指示 | — | 開発ループの予算の単位に、数の代わりに渡す値 default（`--budget-usd default`）。NDF が反映の時点の予算の既定でその単位を上書きする | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| 予算の履歴 | — | 開発ループの記録が持つ、予算の上書きの記録（`budget_history`）。予算が変わったときだけ、時刻・前の値・後の値の 1 行を足す | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 人の判断待ちの行 | — | 開発ループの記録が持つ、人の答え（承認・差し戻し・直して出し直す）を待つ課題の行（`waiting`）。`sprint-state.py loop answer` で答えを書く。着手の前の課題の状態を指す「人の判断待ち」とは別の語 | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| 問い合わせの区分 | `ask_category` | AskUserQuestion の metadata.source に AI が付ける値。ndf-gate:<番号>（承認ゲート）・ndf-ask:redline:<番号>（レッドライン。宣言で外せない）・ndf-ask:<名前>（宣言が許す区分）の 3 つの形。hook は質問の中身を読まず、この値が許された区分かだけを見る（#2206） | — | — | `docs/specifications/ndf-ask-guard.md` |
| 問い合わせの宣言 | `ask_declaration` | プロジェクトの .ndf/ask.json。人へ回してよい問い合わせの区分と hook の働き方（enforce / advise / off）を書く（#2206） | — | — | `docs/specifications/ndf-ask-guard.md` |
| 代わりの判断 | `proxy_decision` | hook が問い合わせを止めた後か、手順が「AI が決める」とした場面で AI が自分で下した判断。問い・選んだ答え・理由を問い合わせの記録へ書く（#2206） | — | — | `docs/specifications/ndf-ask-guard.md` |
| 問い合わせの記録 | `ask_ledger` | 状態の置き場の ask.jsonl。hook の判定の行と代わりの判断の行を追記だけで持ち、振り返りと Factory のランナーのエージェントが読む（#2206） | — | — | `docs/specifications/ndf-ask-guard.md` |
| 試行 | — | 同じステップを流し直したときの 1 回ごとの実行と、その結果 JSON。プランの実行の記録の NN-<ステップ>.out の 1 つが 1 試行に当たる | — | — | `docs/specifications/ndf-step-result-gaps-and-reading.md` |
| 欠けの残る結果 | — | 手順は終わったが、出すはずの材料・記録の一部が無いか、目標（収束など）に届かなかった結果。stopped と終了コード 6 と gaps で返し、ok で返さない | — | — | `docs/specifications/ndf-step-result-gaps-and-reading.md` |
| 結果の置き場 | — | 読む側が結果 JSON や材料を探す場所（プランの実行の記録・記録のファイル）。置き場が無いことと、材料が 0 件であることを分けて扱う | — | — | `docs/specifications/ndf-step-result-gaps-and-reading.md` |
| 欠け | `gaps` | 結果 JSON の gaps の 1 件。出すはずだったのに出せなかった材料・記録、届かなかった目標を、種類・名前・理由で書く | — | — | `docs/specifications/ndf-step-result-gaps-and-reading.md` |
| ステップの延長 | — | drive のステップの子孫（駆動と、駆動が起動した init）が自分の見込みの秒を延長のファイル（NDF_STEP_EXTEND_FILE の指すパス）へ書き、エンジンがその試行の締め切りを with_margin を足して後ろへずらすこと。前へはずらさない | — | — | — |
| 過程の項目 | `process` | 結果 JSON の process。スクリプトが実行したこと（コマンド・操作）と、その結果の要旨を順に並べる | — | — | `docs/specifications/ndf-step-result-gaps-and-reading.md` |
| 判断の項目 | `decisions` | 結果 JSON の decisions。スクリプトが決めたことと、その根拠を並べる | — | — | `docs/specifications/ndf-step-result-gaps-and-reading.md` |
| 最終工程の記録 | — | 最終工程を終えたことを示す、GitHub に残った記録。デプロイはリリース記録の `段階:`、リリース後テストはその課題の行、振り返りは課題の本文の `振り返り: <コメントの URL>` の行のうち、指すコメントの識別子が記録の PR の最後のリリース記録のコメントの識別子以上のもの（その実行の振り返り）。sprint-close.py が閉じる前に確かめる | — | — | `plugins/ndf/skills/progress-tracking/SKILL.md` |
| 閉じる予定の参照 | — | Pull Request の本文に `最終工程の後に閉じる: #<番号>` と書く形。GitHub は閉じず、sprint-close.py がスプリント課題として集める。宛先が既定ブランチの Pull Request では、NDF の手順が closing keywords をこの形へ置き換える | — | — | `plugins/ndf/skills/progress-tracking/SKILL.md` |
| 版の差し込み | — | 前の版の NDF が組んだリリースプランが、具体的な版の代わりに持つ `{release_version}`（出す版）と `{release_base}`（その接尾辞を外した版）。プランの型の移行関数が版の参照へ読み替える（#2344） | — | — | — |
| 版の記録 | — | 解決の控えのうち、版の参照の行。決めた・確かめた版と、決めた根拠と、決め直しの履歴を持ち、流し直しはこれを読む。前の版の NDF が書いた `version.json` は移行関数でこの行へ読み替える（#2344） | — | — | — |
| 版の決め直し | — | 開発版の流し直しで、版の記録の版が自分のデプロイのマージより前に別のスプリントに出ていたとき、次に空いている版を決め、解決の控えの版の行をその版へ上書きし、前の版と理由を控えの履歴に残すこと。プランのファイルは書き換えない。明示の版と、自分のデプロイのマージの後には行わない（#2344） | — | — | — |
| 明示の版 | — | `new sprint` / `new close` の `--version`・`--prod`、`new release` の `--version` で渡した版。プランを作る時点で決まり、キューはその版がまだ出ていないかだけを確かめる | — | — | — |
| 実行の核 | `run_core` | 入口・ステップが必ず通る共通の実行の仕組み。入力の解決・記録・lease・型の確認・再試行と上限・結果の変換を引き受け、ステップは仕事の中身だけを書く（#2335） | — | — | — |
| 契約の一覧 | `contract_registry` | エントリポイントごとの入力（引数・環境変数・.ndf/ の設定のキー・標準入力）・出力・書く状態・終了コードと、受け渡しのファイル・設定・環境変数の書き手と読み手と置き場を持つ、リポジトリで 1 つの宣言 | — | — | `docs/specifications/ndf-entrypoint-contracts.md` |
| 原因の型 | — | 課題を原因で分けた A〜I の区分。A 失敗の判定・B 記録とログ・C 回復と再開・D 待ちと上限・E 解決の時点・F 並行の衝突・G 機能と方針・H 文書・I その他（#2335） | — | — | — |
| 柱 | — | 大方針の子で、根本原因の仕組みごとに直す課題。#2335 では P0〜P8（#2336〜#2344）（#2315） | — | — | — |
| 機械用のブロック | — | 課題・PR の本文に埋めた、機械が値を読むための <!-- ndf:... --> の JSON（#2335） | — | — | — |
| 実行の識別子 | `run_id` | 1 回の入口の実行に実行の核が振る識別子。記録・持ち主の記録・耐久の記録をつなぐ（#2335） | — | — | — |
| 実行の文脈 | `run_context` | 実行の核が本体へ渡す 1 つの値。解決した入力・実行の識別子・期限・記録の書き口・再試行・型の読み書きを持つ（#2335） | — | — | — |
| 実行の記録 | `run_log` | 1 回の起動（子の実行を含む）の始まりから終わりまでを、実行の識別子つきで書く JSON Lines の行の並び。既定の出力先はファイル（#2335・#2338） | — | — | — |
| 起点の実行 | — | 親の実行の識別子を持たない実行。実行の記録のファイル名になる（#2338） | — | — | — |
| 子の実行 | — | 実行の核を通る入口が子プロセスとして起こした、核を通る入口の実行。親の実行の識別子を持ち、親と同じ実行の記録へ書く（#2338） | — | — | — |
| 診断 | — | スクリプトが人へ向けて標準エラーへ出す文（警告・進み具合・失敗の理由）。結果 JSON とは別の出力で、実行の記録にも残す（#2338） | — | — | — |
| 出力先 | — | 実行の記録の行を書く先。既定はファイルで、1 つの設定で別の出力先（Factory の CloudWatch Logs など）へ差し替える（#2338） | — | — | — |
| 全文ファイル | — | 実行の記録の 1 行の上限を超える文字列を、切らずに全文で置くファイル。実行の記録と同じ日付のディレクトリに置き、行にはパス・文字数・sha256 を残す（#2338） | — | — | — |
| 上限の宣言 | `limits_declaration` | 上限の秒・再試行の回数・同じ原因の上限を、方針の名前ごとに持つ 1 つのファイル（plugins/ndf/contracts/limits.json。#2335） | — | — | — |
| 方針の名前 | — | 上限の宣言の 1 行を指す名前。入口の契約と部品の呼び出しが、値でなくこの名前で上限を引く（#2343） | — | — | `docs/specifications/ndf-limits-declaration-and-retry-policy.md` |
| 原因の鍵 | — | 失敗が前回と同じ原因かを見る値。例外の型と、数字・時刻・パスを伏せた失敗の理由の文から作る（#2343） | — | — | `docs/specifications/ndf-limits-declaration-and-retry-policy.md` |
| 再試行 | — | 失敗した同じ呼び出しを、同じプロセスの中で間隔を置いて打ち直すこと。条件が揃うまで問い合わせる待ちとは別である（#2343） | — | — | `docs/specifications/ndf-limits-declaration-and-retry-policy.md` |
| ステップの打ち直し | — | プランの判定役（judge）が、失敗したステップを同じステップのまま流し直すと返すこと（#2343） | — | — | `docs/specifications/ndf-limits-declaration-and-retry-policy.md` |
| 眠りのループ | — | 本体（入れ子の関数を除く）で sleep を呼ぶ for / while のループ。再試行と待ちの両方が当たり、lib/waits.py の外では構造チェックが落とす（#2343） | — | — | `docs/specifications/ndf-limits-declaration-and-retry-policy.md` |
| 最後の照会 | — | 待ちの次の眠りが上限を超えるとき、残りの秒だけ眠った後に上限の時刻に打つ 1 回の照会（#2343） | — | — | `docs/specifications/ndf-limits-declaration-and-retry-policy.md` |
| 名前の対応表 | `names` | 同じ意味の値の正しい名前と、読むだけ受ける旧い名前の対応（plugins/ndf/contracts/names.json。#2335） | — | — | — |
| 受け渡しのファイル | `handoff_file` | あるエントリポイントが書き、別のエントリポイントか Skill の手順が読むファイル（状態・記録・結果）。1 つのエントリポイントの中で作って消す一時ファイルは含まない | — | — | `docs/specifications/ndf-entrypoint-contracts.md` |
| 例外リスト | `allow_list` | 構造チェックとリポジトリ前提の検査が違反として数えない今の外れを 1 項目 1 ファイルで持つ置き場。チェックごとに置く（scripts/script-structure-allow/・scripts/contract-allow/・scripts/repo-assumptions-allow/）。違反に当たらなくなった項目は落ち、減る方向にだけ変える | 許可の一覧 | — | `docs/specifications/ndf-entrypoint-contracts.md` |
| 移行の読み | `migration_read` | 1 か所へ寄せた設定の値を、新しい置き場に無いときだけ古い置き場から読むこと。契約の一覧の設定ファイルの項目に migrate_from として書く | — | — | `docs/specifications/ndf-entrypoint-contracts.md` |
| 設定の置き場 | `setting_place` | .ndf/ の設定ファイルを読む木。worktree（呼ばれた木。チームの設定）か main（メインディレクトリ。個人の設定）で、契約の一覧の設定ファイルの項目の place が決める | — | — | `docs/specifications/ndf-entrypoint-contracts.md` |
| Skill の対応表 | — | 工程（ステップの stage）から、そのステップの worker へ届ける Skill の一覧を引く表。NDF の scripts/lib/stage-skills.json の 1 か所に置き、プランの worker の起動と ndf:worker の起動指示の両方が読む（#2376） | — | — | `docs/specifications/ndf-worker-stage-skills.md` |
| Skill の渡し方 | — | Skill を worker へ届ける形。プランの worker には本文を system プロンプトへ入れ（本文）、ndf:worker には SKILL.md の絶対パスを起動指示の「読む Skill」に書く（パス。#2376） | — | — | `docs/specifications/ndf-worker-stage-skills.md` |
| 読む Skill | — | supervisor が ndf:worker の起動指示に書く項目。その作業で読む規則の Skill の名前と SKILL.md の絶対パスを並べ、worker はここに挙がった SKILL.md だけを読む（#2376） | — | — | `docs/specifications/ndf-worker-stage-skills.md` |
| 読む Skill の guard | — | supervisor が ndf:worker を起動する Agent の呼び出しを PreToolUse の hook で見て、「読む Skill」の欄が無い起動と、Skill の対応表を引かずに Skill を挙げた起動を止める判定。止めた理由に正本の「supervisor → worker」の節を添える（#2399） | — | — | `docs/specifications/ndf-supervisor-worker-skills-guard.md` |
| 規則の Skill | — | 手順を持たず、書き方・記録の仕方のような規則だけを持つ Skill（document-style・logging-guidelines など）。Skill の対応表に置けるのはこれだけで、工程 Skill は置かない（#2376） | — | — | `docs/specifications/ndf-worker-stage-skills.md` |
| 差分の棚卸し | — | 開発ループでは数回のループの 1 回に 1 回（既定 3 回。`.ndf/backlog.json` の `depths.diff.every`）、ループの外では毎スプリントのまとめで行う棚卸しの深さ。振り返りの範囲の変更で食い違った本文の訂正・直ったのに開いたままの課題を閉じる・閉じた親の子の見直し・その範囲で起票した課題（振り返りの範囲の始まりの時刻より後に作られた課題）への見積の段階の付与だけを行う（#2315・#2914） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 仕上げのプラン | — | `supervise.py new close` が組むプラン（記録の `フェーズ` の値は「まとめ」）。確定仕様化・リリース後テスト・振り返り・課題を閉じる・棚卸しのステップを持つ（#2914） | — | — | `docs/specifications/ndf-sprint-close-plan-cost.md` |
| 走らなかったステップ | — | 起動の判定などの終了コードで飛ばされ、プランの実行で通らなかったステップ。フェーズレポートの「通ったステップ」の後に、ステップの id と飛ばした判定の要約を 1 行で並べる（#2914） | — | — | `docs/specifications/ndf-sprint-close-plan-cost.md` |
| 見直し | — | 数スプリントに 1 回行う棚卸しの深さ。後回しを除く open の課題の枝刈り・順位付け・マイルストーンの再編と、ニーズ・シーズの調査からの起票を行う（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 大整理 | — | 十数スプリントに 1 回行う棚卸しの深さ。未完了の課題すべてを原因の型で分け、Issue 単位では直らない根本原因を探し、見つかれば大方針を立てて課題とマイルストーンを組み直す。後回しの再評価を含む（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 後回しの再評価 | — | 後回しのマイルストーンの課題を、前倒し・やらない・据え置きに分けること。大整理の一部（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| ニーズの調査 | — | 宣言した利用の記録と市場・競合から、利用者が求めている変更を探すこと（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| シーズの調査 | — | 宣言した技術の構成・ライブラリの競合・周辺環境の変化から、取り入れられる変更を探すこと（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 調査の記録 | — | 調査の項目ごとに出典と調べた日時を持つ結果。見直しのフェーズだけが読んで起票に使う（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 材料の宣言 | — | プロジェクトがニーズとシーズの調査で読む先（プロダクトの種類と利用者・利用の記録と読む範囲・市場と競合・技術の構成・ライブラリの競合・周辺環境・後回しのマイルストーン）を書いたもの（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 見直しの持ち主 | — | 見直しのフェーズ（待ち時間の調査を含む）を受け持つ 1 つのセッション。宣言をロックの中で取り、終わったら解放する（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 大方針 | — | 大整理で見つけた根本原因を直す親の Issue。柱と受け入れ条件を持つ（例 #2335）（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| リレーハブ | `relay_hub` | 同じマシンで最初に起動したラッパーが兼ねるプロセス。名前つきのラッパーの登録・起動・同時に動かす数の判定・一覧だけを持ち、判断を持たず、各ラッパーの状態機械には触れない | 統括役 | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| 名前つきのラッパー | `named_relay` | リレーハブに名前で登録され、tmux の 1 つの window で動く relay.py run の 1 本。カットポイントで区間を切り替えても同じ名前で続く | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| 人の起動 | `human_launch` | 端末で利用者が打った claude が、リレーハブへ by: terminal で頼んだ起動（端末の 2 本目）。上限で断らず、上限の数にも入れない | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| AI の起動 | `ai_launch` | 依頼の by が spawn・restart・block:<作業ディレクトリ> の起動。リレーハブが上限で判定する | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| 端末の結び付け | `attach` | 利用者の端末を、名前つきのラッパーの tmux の window へ attach すること。端末を閉じても window の中のラッパーは動き続ける | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| 登録簿 | `relay_registry` | リレーハブだけが書く、名前つきのラッパーの名前と cwd（起動元のリポジトリ）・最初の引数・tmux のセッションの表（registry.json。作業ディレクトリのパスは持たない） | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| 起動の依頼 | `spawn_request` | 頼む側がリレーハブの受付箱へ置く 1 件のファイル。起動・やり直しの種類と名前・作業ディレクトリ・引数を持つ | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| 起動のブロック | `spawn_block` | conductor の応答に置く、情報文字列が ndf-spawn <名前> の囲み。中身が最初の入力になる | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| 並列の計画 | — | 計画の置き場のリポジトリに置き、同時に進めるレーンの一覧と、レーンに共通の決まりを書いた 1 つの文書（レーンは別のリポジトリでもよい）。オーガナイザーが読む。名前（計画名）を持つ | — | — | `plugins/ndf/skills/issue-plan-strategy/references/parallel-plan.md` |
| レーン | — | 並列の計画の下で、名前つきのラッパー 1 本が進める開発の流れ。最初の入力は開発ループかスプリントの 1 つ。レーンのリポジトリは計画の置き場と違ってもよい（別のリポジトリのレーン）。名前は役割を含めず、役割は計画の宣言の入力で読む。物理層のラッパーとフロー層の流れの組 | ライン | — | `plugins/ndf/skills/issue-plan-strategy/references/parallel-plan.md` |
| オーガナイザー | — | --parallel <計画名> で起動した conductor の役割。レーンの承認待ちを依頼として受けて問うか自分で決め、答えを送る（設計の承認ではレーンのリポジトリで設計 Pull Request をマージする）。レーンの起動・再起動・監視とリリースのまとめは耐久ワークフローが行う。人との窓口は計画ごとにオーガナイザー 1 つ。リレーハブとは別。並列の計画のまとめ役はオーケストレーターと呼ばない。開発者ごとの手元のマシンでは手元で起動しない | — | — | `docs/specifications/ndf-parallel-durable-lane-launch.md` |
| リリースの組み方 | — | リリースの依頼を受けたときに、まとめたリリースをどう扱うか。組む・組まない・組めないの 3 つがあり、宣言から決まる | 配布の組み方 | — | — |
| 組み直しの依頼 | — | 組めなかったリリースの依頼を、人が「直してから組み直す」と答えた後に耐久ワークフローが受けている依頼へ入れ直したもの。元の依頼の答えは変えない | — | — | — |
| 一時レーン | — | 持ち主のいない共有の作業（まとめたリリースプランの実行・その失敗の対応・止まったレーンの保留したループの 1 回の残り）のために耐久ワークフローが起動するラッパー（<計画名>-rel<連番>、役割 helper）。同時に 1 本までで、作業を終えると自分で止まる | — | — | `docs/specifications/ndf-parallel-durable-lane-launch.md` |
| 別のリポジトリのレーン | — | 並列の計画の置かれたリポジトリと違うリポジトリで動く、計画のレーン。問いと承認はオーガナイザーを通し、リリースは自分のリポジトリのチャネルへ出す | — | — | `plugins/ndf/skills/development-workflow/references/parallel.md` |
| 計画の外のセッション | — | 動いている計画があるマシンで、計画のレーンでもオーガナイザーでもないのに開発ループかスプリントを始めるセッション。基本不許可で、必要ならレーン数の上限を変えて計画へレーンを足す | — | — | `plugins/ndf/skills/development-workflow/references/parallel.md` |
| レーン数の上限 | — | 同時に動かせる名前つきのラッパーの数。値はマシンの設定（<状態の根>/relay/hub/limits.json。relay.py cap が書く）→ NDF_RELAY_MAX_NAMED → 既定 3 の順で決まり、リレーハブと計画の検査が同じ値を読む。オーガナイザーは数えず、一時レーンは 1 本だけ超えられる | — | — | `plugins/ndf/skills/development-workflow/references/parallel.md` |
| 計画の置き場 | — | 並列の計画を作ったリポジトリのメインディレクトリ（計画ごとのファイルはその下の .ndf/parallel/<計画名>/ に置く）。計画の所在の一覧は、このメインディレクトリの絶対パスで持つ。依頼・答えの記録のキーは計画の置き場と計画名から求め、その値は <計画の置き場>/.ndf/parallel/<計画名> の絶対パスのまま変えない | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| レーンのリポジトリ | — | レーンが開発するリポジトリのメインディレクトリ。計画の宣言のレーンの repo で指し、省くと計画の置き場のリポジトリ | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| 計画の所在の一覧 | — | マシンにある計画の置き場を指す一覧（$XDG_STATE_HOME/ndf/parallel/homes/）。別のリポジトリのセッションが、レーンの判定と計画の外のセッションの判定に使う | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| 例外の起動 | — | 計画の外のセッションが、理由を添えた明示の指定（--outside-plan <理由>）で開発ループかスプリントを始めること。理由はマシンの記録に残る | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| 保留したループの 1 回 | — | 並列の計画のループのレーンで、答えを待たずに締めたループの 1 回（リリース待ち・質問待ち）。課題は締め直すまで選定から外し、答えの後に残りの工程を流して 1 回だけ締め直す | — | — | `plugins/ndf/skills/development-workflow/references/loop.md` |
| 質問待ち | — | 作業の途中の問いを問いの hook がオーガナイザーへ送り、止まったプランとステップを残して締めたループの 1 回の結果 | — | — | `plugins/ndf/skills/development-workflow/references/loop.md` |
| 再起動の方針 | — | conductor が工程境界で会話を切るかを決める規則の組。現行方式と試行方式の 2 つ | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 現行方式 | — | 運用上限 200K を超えたら次の工程を止め、4 つのカットポイントではコンテキスト量に関わらず ndf-next を出す再起動の方針。既定 | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 試行方式 | — | カットポイントを再起動を判断できる境界として扱い、判断開始の値・運用上限・容量で継続か再起動かを決める再起動の方針。明示的に選んだときだけ働く | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 判断開始の値 | — | 試行方式で、これを超えたら工程境界で継続と再起動を比べ始めるコンテキスト量（初期値 200K） | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 運用上限 | — | これを超えたら次の工程への進入を止めるコンテキスト量（現行方式 200K・試行方式の初期値 400K）。ツール呼び出しの途中では止めない | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 容量の余白 | — | 実行環境の窓の上限（または自動圧縮が始まる量）から、次の作業・ツール結果・出力と進行の保存のために空けておく量 | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 再起動の理由 | — | 工程境界で会話を切った・止めたときの理由。容量・運用上限・費用・独立性・手動・現行方式のカットポイントと、オーガナイザーの長い待ちの 7 つ | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 復元の区間 | — | 再起動した新しいセッションが、引継ぎ文書を読み始めてから最初の工程を起動するまで | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 復元の量 | — | 再起動した新しいセッションが「復元完了」を記録した時点の文脈の量。引継ぎ文書と、状態を戻すために読んだ文書の分を含む。試行方式の費用の見込みの式の Q | — | — | `docs/specifications/ndf-context-continue-or-cut-measurement.md` |
| 計測の条件 | — | 続けるか切るかを比べるときの 2 つの設定。「続ける」は試行方式で運用上限を窓 − 容量の余白まで上げた設定、「閾値で切る」は試行方式で判断開始の値と運用上限を候補の閾値にした設定 | — | — | `docs/specifications/ndf-context-continue-or-cut-measurement.md` |
| 計測の回 | — | 計測の条件 1 つで、フェーズの起動からその後の承認ゲートの答えを受けた最初の呼び出しまでを 1 回として数えたもの | — | — | `docs/specifications/ndf-context-continue-or-cut-measurement.md` |
| 待ちの前に切る規則 | — | 承認ゲートか人の判断の問いを出す直前に、文脈が決めた量を超えていれば会話を切り、新しいセッションで問いを出し直す規則 | — | — | `docs/specifications/ndf-context-continue-or-cut-measurement.md` |
| 境界の判定 | — | 工程の起動 1 回ごとに出す継続 / 再起動 / 拒否と、その帯・理由・使った値の組。1 件ずつ記録に残る | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 適用値 | — | 優先順位を当てて解決した、そのセッションで効く再起動の方針・判断開始の値・運用上限・容量の余白と、それぞれの出所 | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 作業の識別子 | — | 複数のセッションを 1 つの作業として結ぶ鍵。起動の引数かスプリントに含まれる課題番号の組（#829 #830） | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 費用の見込み | — | 判断の帯で、続けた場合と再起動した場合の次の工程までの費用を、記録から較正した中央値で比べた値 | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| 引継ぎの区間 | — | conductor が引継ぎ文書を書き始める前に記録した「引継ぎ開始」の時点から、そのセッションの最後の行まで | — | — | `docs/specifications/ndf-context-restart-policy.md` |
| プランの参照 | — | プランが値の代わりに持つ、値の取り先の名前（次に空いている開発版・課題のリリース記録を置いた PR・前のプランが出した PR・承認したコミットと照らすブランチの先端）。流す直前に実行の核が値へ解決する（#2344） | — | — | — |
| 解決の記録 | — | 1 回の実行がプランの参照ごとに解決した値と出所。実行の記録の行として残り、プランのファイルへは書き戻さない（#2344） | — | — | — |
| 承認時の値 | — | 承認ゲートで人か MVV 判定が承認資料で見た値（承認したコミット・版）。流す直前に解決した値と照らし、食い違えば流さずに人へ戻す（#2344） | — | — | — |
| 解決の控え | — | 流し直しでも同じ値を使う参照（版）の解決した値と決め直しの履歴を、プランの状態ディレクトリに残すファイル（`refs.json`）（#2344） | — | — | — |
| プランのハッシュ | — | プランの型で読んだ中身を、キーを並べ替えた JSON にして取った sha256。完了したプランが変わったかを測る（#2344） | — | — | — |
| 呼び手 | — | 関数かエントリポイントを呼ぶ、テストの外のファイル（Skill の本文・雛形・hook・CI・ほかのスクリプト）。テストだけが呼ぶものは呼び手 0 と数える | — | — | — |
| 残す関数の一覧 | — | 関数の棚卸し（uvx vulture）が呼び手 0 と見るが、vulture に見えない呼ばれ方などで残す関数を、理由の種類と理由と定義のあるファイルを 1 行ずつ添えて持つ一覧。置き場は scripts/ndf-vulture-keep.txt。構造チェックの例外リストとは別のもの | — | — | — |
| 外部の調査 | — | 設計の判断の前に、業界の事実上の標準・仕様、主要な製品の解き方、使える既存のライブラリとマネージドサービスを一次情報で調べ、設計文書に残す手順（#2519）。棚卸しのシーズの調査とは別のもの | — | — | `plugins/ndf/skills/design/references/external-survey.md` |
| 自前で作る決定 | — | 既存のライブラリ・サービス・標準の実装を使わず、プロジェクトの中で作ると決めた決定の記録。決定の作り方の行に「自前で作る」を含むもので見分ける（#2519） | — | — | `plugins/ndf/skills/design/references/decisions.md` |
| 確かめられなかったこと | — | 外部の調査で、公開の資料が無い・取得できなかった・上限で止めた対象と、試した手段（URL か検索の語）と理由を並べた設計文書の欄（#2519） | — | — | `plugins/ndf/skills/design/references/external-survey.md` |
| 作り方 | — | 設計文書の決定の記録の中の 1 行。その決定が機能をどう得るか（自前で作る / ライブラリやサービスを使う / 標準に倣う、またはその組み合わせ）を書く（#2519） | — | — | `plugins/ndf/skills/design/references/decisions.md` |
| 比べた候補 | — | 設計文書の決定の記録の中の 1 行。外部の調査の表で比べた行の番号（STD<数> / PRD<数> / LIB<数>）を並べる（#2519） | — | — | `plugins/ndf/skills/design/references/decisions.md` |
| プランの進行の記録 | — | supervise のプランのステップごとの終了コード・秒・ドルを 1 行ずつ残す記録（`<状態のディレクトリ>/progress.jsonl`）。開発ループのまとめのプランでは `.ndf/loop/<名>/lap-<番号>/close*/` の下にある（#2315） | — | — | — |
| 承認した先端 | — | 設計の承認を問うた時点の設計 Pull Request の先頭のコミット。承認した設計のマージはこの先端のときだけ行う | — | — | — |
| マージの結果 | — | 承認した設計 Pull Request を宛先へマージする 1 回の試みの結果。マージした・マージ済み・先端が動いた・印が無い・検査が緑でない・衝突・宛先が本番・失敗のどれか | — | — | — |
| 答えの後の整理 | — | オーガナイザーが答えを送った後、待つ間に届いた依頼を取り直し、除くものを除いて残りをまとめて問う手順 | — | — | — |
| 本体 | — | エントリポイントの仕事の中身を持つ関数。実行の核が呼び、結果 JSON か終了コードを返す（#2337） | — | — | — |
| 核の対象外 | — | 実行の核を通さないエントリポイントの印。hook の定義から起動されるか、ランタイムが直接起動して標準出力をそのまま使うもの。契約の一覧の core に理由と根拠を書く（#2337） | — | — | — |
| 作業の結果ファイル | — | worker が報告を終える前に書く、結果 JSON の形の 1 行のファイル。supervise と supervisor はこのファイルの status だけで作業の成否を決める（#2337） | — | — | — |
| フェーズの結果 | — | supervise の 1 つのプランの実行の終わり方を結果 JSON の形で書いたファイル（状態ディレクトリの phase-result.json）。supervise.py run の終了コードはこの status から決まる（#2337） | — | — | — |
| 扱い | — | 設計の結果の列。課題ごとに `実装する` / `取り込む` / `閉じる` のどれかを書く | — | — | `docs/specifications/ndf-design-results-docs-and-cell-format.md` |
| 取り込み先 | — | 設計の結果の列。扱いが `取り込む` の課題を、どの課題の実装へ含めるかを課題の番号で書く | — | — | `docs/specifications/ndf-design-results-docs-and-cell-format.md` |
| 触るファイル | — | 設計の結果の列。扱いが `実装する` の課題が変えるファイルの一覧。実装のプランの触る範囲になる | — | — | `docs/specifications/ndf-design-results-docs-and-cell-format.md` |
| 食い違い | — | 同じ課題について、2 本の設計文書が違う設計の結果（扱い・取り込み先・触るファイルのどれか）を書いていること。設計の結果のステージは食い違いを見つけると止まり、人へ戻す | — | — | `docs/specifications/ndf-design-results-docs-and-cell-format.md` |
| 出し直しの指摘 | — | 設計承認ゲートで人が「直して出し直す」と答えたときに `--note` に渡したファイルの全文。レビューの担当が書く指摘ファイルとは別のもの | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 出し直しの指摘の履歴 | — | 課題の本文に置く、出し直しの指摘を回ごとに追記する節。人の判断待ちの節を書き直しても消えない | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 指摘への対応の表 | — | 出し直しの設計 PR の本文に置く表。出し直しの指摘の項目ごとに、直した / 直せない、直した箇所か直せない理由を 1 行で持つ | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 設計のプラン | — | 開発ループの 1 回が課題ごとに組む、設計のフェーズのプラン（`sprint/<番号>-design-<課題>.json`）。要求・設計・レビュー・設計承認ゲートのステップを持つ | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 答えの種類 | — | 人が承認ゲートや問いに返す答えの区分。承認・直して出し直す・やらない・予算・止めるの 5 つ | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 自動の出し直し | — | 出し直しの設計が出し直しの指摘の項目を指摘への対応の表に示していないとき、人へ問わずに開発ループが自分で付ける「直して出し直す」の答え。届いていない項目を名指しする。人の出し直しの 1 回につき 1 回まで | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 出し直しの指摘の入力 | — | 開発ループがループの 1 回ごとに、同じリポジトリのすべてのループの記録から、課題の全回の出し直しの指摘を書き出したファイル。回はループの名前と回数の組で識別する。設計のプラン・MVV 判定・指摘への対応の表の突き合わせが読む | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 設計の承認の依頼 | — | 並列の計画のレーンが、設計承認ゲートの判断をオーガナイザーへ求めて送る依頼。設計 PR の先頭の SHA を持つ | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 取り込みのコミット | — | 起点ブランチを設計 PR のブランチへ取り込むマージコミット。これだけで先頭が動いた設計 PR は、承認の依頼を送り直さない | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 前のループ | — | 同じリポジトリの開発ループのうち、並列の計画のレーンでないもの。その答えがまだの人の判断待ちは、オーガナイザーの問いの一覧に出す | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 当たらなかった答え | — | オーガナイザーの答えのうち、持ち主のループの記録の行へ当てられなかったもの。答えは耐久の記録に残し、区分 unmatched の問いとしてオーガナイザーの問いの一覧に出す | — | — | `docs/specifications/ndf-loop-human-answer-delivery.md` |
| 共有の環境 | — | NDF のスクリプトとラッパーが使う Python の環境。置き場は ~/.cache/ndf/venv/<版>（NDF_DEPS_VENV で変えられる）。プラグインの版ごとに 1 つで、同じ版のスクリプトとラッパーが共に使う | — | — | `docs/specifications/ndf-shared-venv-and-relay-ls-failure.md` |
| 一覧の失敗の問い | — | 状態の一覧の失敗が停滞の区切りより長く続いたときに、並列の計画のオーガナイザーの耐久ワークフローが自分で立てる問い（kind は observe）。一覧の終了コードと標準エラーの末尾 1 行を持ち、一覧が成功に戻れば外れる | — | — | `docs/specifications/ndf-shared-venv-and-relay-ls-failure.md` |
| 状態の一覧 | — | ラッパーの relay.py ls --json が返す、名前つきのラッパーごとの状態（running・awaiting_approval・stopped など）と最後に動いた時刻の一覧。オーガナイザーはこれでレーンの状態を観測する | — | — | `docs/specifications/ndf-shared-venv-and-relay-ls-failure.md` |
| 設計承認ゲート | — | 設計 Pull Request のマージの前に人の承認を求める承認ゲート。documentation モードでは企画承認に当たる | 承認ゲート 1、ゲート 1、関門 1 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリース承認ゲート | — | 本番系へ届く操作（本番へのリリース・operation の実行・自動反映の本番チャネルへのマージ）の前に人の承認を求める承認ゲート。documentation モードでは制作物承認に当たる | 承認ゲート 2、ゲート 2、関門 2 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 人へ出す文 | — | Skill・references・エージェントの定義の本文と、スクリプト・hook が人へ見せる文（問い・承認資料・報告・案内・エラーの文）。機械が読む識別子と対になる | — | — | `docs/specifications/ndf-approval-gate-names.md` |
| 機械が読む識別子 | — | スクリプトが値の一致で読む文字列（問いの区分・契約の種類の値・記録の名前・ラベルなど）。人へ出す文と対になる | — | — | `docs/specifications/ndf-approval-gate-names.md` |
| 書いた日の記録 | — | 書いた日時点の内容を残し、以後の構成変更に追随しない文書。発表資料・記事・アーカイブ・開発履歴・変更履歴の出した版の項・issues/ の作業記録 | — | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| リリース待ち | — | リリースの依頼を送り、答えを待たずに締めたループの 1 回の結果。まとめはまだ流していない | 配布待ち | — | `plugins/ndf/skills/development-workflow/references/loop.md` |
| リリースの依頼 | — | レーンがリリース承認ゲートの手前で、リリースをオーガナイザーへ頼むために送る 1 件のメッセージ（parallel.py request）。テンプレートで出すリポジトリのレーンだけが送る | 配布の依頼 | — | `plugins/ndf/skills/development-workflow/references/parallel.md` |
| 課題の差し込み | — | リリースの依頼のプランのコマンドが、課題の番号の代わりに持つ `{plan_issues}`。ステップを流す時点に、プランの `課題` の空白区切りで埋まる | — | — | `plugins/ndf/skills/development-workflow/references/parallel.md` |
| まとめたリリース | — | オーガナイザーが複数のレーンの Pull Request を 1 つの版として出すリリース。提示のプランと本番のプランからなる | まとめた配布 | — | — |
| 利用先のリポジトリ | — | NDF を導入して使っているリポジトリ | 配布先のリポジトリ | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| 旧い値の読み替え | — | 版を上げる前に書かれた記録の値を、読む時点で新しい値へ変換すること | — | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| 禁止語 | — | 用語集の設定が、検査する範囲で新しく書くことを禁じた語 | — | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| 無進捗の打ち切り | — | 実装のステップで、進みの観測が無進捗の許容時間を越えて途絶えたときだけ打ち切ること | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 無進捗の許容時間 | `impl_stall_seconds` | 実装のステップで、最後の進みの観測から無進捗の打ち切りまでに待つ秒。.ndf/supervise.json の impl_stall_seconds か supervise.py new の --impl-stall で決め、無ければ 1800 秒 | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 進みの観測 | — | 実装のステップの間に、会話の記録の伸び・進捗ログの行・コミットの増加のどれかを見つけること | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| ループの 1 回の費用の内訳 | — | ループの 1 回の費用を、プランの claude -p・同じプランの中の別の起動・USD に換算できない起動に分けた記録。ループの 1 回の費用の和は、使用量の帳簿の和とスプリントの報告の和の大きい方とする | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 実行環境の調査 | — | 動く場所・CPU のコア数・メモリ・同時に動くレーンとプランの数を調べ、ループの 1 回で選ぶ課題の数の候補を出す工程 | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 答え待ちの依頼 | — | オーガナイザーに届いて、まだ答えていない依頼。設計の承認・リリース承認ゲート・承認ゲートの外の問い・耐久ワークフローが自分で立てた問いの 4 種類 | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 計画の閾値 | — | 計画全体の答え待ちの依頼の件数の閾値。既定は 8 で、これを超えると計画のすべてのレーンを止める | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| レーンの閾値 | — | 1 つのレーンが送った答え待ちの依頼の件数の閾値。既定は 3 で、これを超えるとそのレーンを止める | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 答え待ちの停止 | — | 答え待ちの依頼が閾値を超えたため、レーンに新しい承認を生む作業を始めさせない状態。件数が閾値以下へ戻ると解ける | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 止める指示のファイル | — | 耐久ワークフローが今の答え待ちの停止の範囲を書き、レーンが区切りで読むファイル（answer-hold.json）。停止が無ければファイルも無い | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 再開の印 | — | 答え待ちの停止が解けたら起こすレーンを示すファイル（resume/<レーン>.mark.json）。レーンが止まるとき、または停止の間に答えを受けた持ち主へ置く | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| 分割した文書 | — | 1 つの課題の設計を複数ファイルに分けたときの、先頭以外の設計文書（`issue-<番号>-design-<名前>.md`） | — | — | `docs/specifications/ndf-design-results-docs-and-cell-format.md` |
| セルの形の誤り | — | 設計の結果の「実装する」の行の触るファイルのセルが、定めた書式から外れていること。設計の結果のステージが止まる | — | — | `docs/specifications/ndf-design-results-docs-and-cell-format.md` |
| 照合 | — | スプリント MVV などの本文が、共通原則とプロジェクト MVV に従うかを LLM に判定させること。判定は「従う」「反する疑い」「判定できない」「読めない」の 4 つで、本文は書き換えない | — | — | `docs/specifications/ndf-sprint-mvv-vet-reuse.md` |
| 照合の入力 | — | 1 回の照合で LLM へ渡す全体。判定の指示・共通原則・プロジェクト MVV・判定の決まり・本文の種類の名前・照合する本文からなる。1 字でも違えば別の入力である | — | — | `docs/specifications/ndf-sprint-mvv-vet-reuse.md` |
| 照合の記録 | — | 照合のたびに 1 行を足す JSONL のファイル（project-mvv.jsonl）。機械ごとに 1 つある | — | — | `docs/specifications/ndf-sprint-mvv-vet-reuse.md` |
| 物理層 | — | 並行開発の実行の入れ物の軸。マシン → ラッパー → セッション → プロセスの順に入れ子になる | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| ロール層 | — | 並行開発の役割の軸。オーガナイザー → conductor → supervisor → worker の順に仕事を渡す | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| フロー層 | — | 並行開発の仕事の流れの軸。レーン → ループ → スプリント → フェーズ → 作業の順に入れ子になる | — | — | `docs/specifications/ndf-parallel-other-repo-lanes-and-outside-plan.md` |
| 今の起動 | — | 一時レーンでは、耐久ワークフローが最後に打った起動。通常のレーンでは、ラッパーが最後に起動した回 | — | — | `docs/specifications/ndf-parallel-durable-lane-launch.md` |
| レーンの再起動 | — | 止まった、または動かないレーンを `relay.py restart` で起動し直すこと。問いの選択肢の「起こし直す」がこれに当たる | — | — | — |
| 停滞の問い | — | 動かないレーンについて耐久ワークフローが立てる問い（`kind: stall`）。一覧の時刻が区切りより前のとき、開発ループが止まったとき、再起動を断られてレーンが止まったときに立つ | — | — | — |
| 途中停止の問い | — | リリースの途中で止まった一時レーンについて耐久ワークフローが立てる問い（`kind: helper`） | — | — | — |
| 問いの取り下げ | — | 耐久ワークフローが、答えを待たずに自分の問いを問いの一覧から外すこと | — | — | — |
| 再起動の試み | — | レーンの再起動を 1 回打ったこと。起動できた回も断られた回も 1 回と数え、統括役の記録に時刻と結果を残す | — | — | — |
| 計画の台帳 | — | 1 つの並列の計画を続けるのに要る状態を、計画の置き場に持つ記録。受けた行を足す ledger.jsonl と、台帳のチェックポイントからなる | — | — | — |
| 耐久ワークフローの作り直し | — | 失われた計画で、耐久ワークフローを新しく始めて計画の台帳から状態を戻すこと | — | — | — |
| 台帳のチェックポイント | — | 耐久ワークフローが自分の状態（答えた依頼・リリースの記録・起動したレーン）と読み終えた位置を書く、計画の置き場の 1 ファイル。書くたびに全体を置き換える | — | — | — |
| 失われた計画 | — | 閉じていないのに、耐久ワークフローが動いていない計画。ERROR か、耐久の記録が無い状態を指す | — | — | — |
| 耐久の記録の退避 | — | 失われた計画の耐久の記録のファイルを、別の名前へ移すこと。同じ計画の新しい記録を同じ置き場で開くために行う | — | — | — |
| 読み込まれたプラグイン | — | 起動したセッションに Claude Code が読み込んだ NDF。--plugin-dir で指したディレクトリか、導入したプラグインのキャッシュのディレクトリで、起動時の init の plugins の ndf の path に出る | — | — | `docs/specifications/ndf-supervisor-docs-guard.md` |
| 規約の文書 | — | supervisor が worker の起動指示を組むために読む、NDF の Skill の SKILL.md と references/ の下の文書（development-workflow の references/agent-layers.md、design の references/design-template.md など） | — | — | `docs/specifications/ndf-supervisor-docs-guard.md` |
| 規約の文書の guard | — | supervisor が Read と Bash で読み込まれたプラグインの外の規約の文書を読もうとしたとき、PreToolUse の hook で止め、読み込まれたプラグインの中の同じ文書の絶対パスを理由で返す判定（#2813） | — | — | `docs/specifications/ndf-supervisor-docs-guard.md` |
| 文書のファイル | — | 差分のうち、文章として読まれるファイル。既定は Markdown と、用語集の宣言が指す用語集のファイルで、プロジェクトの設定（.ndf/project.json の docs）で変えられる | — | — | `docs/specifications/ndf-diff-kinds-and-docs-only-checks.md` |
| コードのファイル | — | 差分のうち文書のファイルでないもの。スクリプト・設定・テスト・ワークフローを含む | — | — | `docs/specifications/ndf-diff-kinds-and-docs-only-checks.md` |
| テストが読む文書 | — | 文書のファイルのうち、プロジェクトの設定でテストとの対応を宣言したもの（用語集・Skill の本文など） | — | — | `docs/specifications/ndf-diff-kinds-and-docs-only-checks.md` |
| 振る舞いの確認 | — | Skill の本文・エージェント定義・手順書を変えたとき、AI がその手順どおりに動くかを実際に起動して確かめること | — | — | `docs/specifications/ndf-diff-kinds-and-docs-only-checks.md` |
| 振る舞いを決める文書 | — | 文書のファイルのうち、AI の振る舞いを決めるもの（Skill の本文・手順書・エージェント定義）。既定は skills/ の下の Markdown と agents/ の直下の Markdown | — | — | `docs/specifications/ndf-diff-kinds-and-docs-only-checks.md` |
| 機械的なターン | — | AI が判断を伴わずに行う Tool の呼び出し。出力の読み解き・状態の照合・スクリプトを 1 行打つ・手書きの待ち・本文の転記 | — | — | `docs/specifications/ndf-mechanical-turns-to-scripts.md` |
| ターンの型 | — | 機械的なターンを、何をしたか（変換・照合・往復・待ち・転記）と打ったコマンドで分けた分類 | — | — | `docs/specifications/ndf-mechanical-turns-to-scripts.md` |
| 手順の連続実行 | — | 判断の要らない NDF の手順のスクリプトを、結果 JSON が ok の間だけ続けて打ち、gate か stopped で止まって理由を返すこと（supervise.py advance） | — | — | `docs/specifications/ndf-mechanical-turns-to-scripts.md` |
| 業務時間 | — | 利用者が承認ゲートへ答えられる時間帯。進め方の宣言に、時刻帯・曜日・始まりと終わりの時刻で書く。時間外は承認の依頼を溜め、業務時間の始めにまとめて示す | — | — | `docs/specifications/ndf-work-during-approval-wait-and-business-hours.md` |
| 新しい承認を生む作業 | — | 設計のステップ（設計承認ゲートの依頼を生む）を含む作業。ループのレーンではループの 1 回のプランに設計承認ゲートを含む作業、1 スプリントのレーンでは開始のステップから設計を書くステップへ行けるプラン。答え待ちの停止の範囲に入ったレーンは始めない | — | — | `docs/specifications/ndf-work-during-approval-wait-and-business-hours.md` |
| 承認の要らない作業 | — | 新しい承認を生む作業以外の作業。開発ループの中では承認した設計の実装と答えの届いた保留したループの 1 回の残り（ループは設計を承認していない課題にモードに依らず設計のステップを置く）、1 スプリントのレーンでは設計 PR のマージ・実装・検査・リリースのプランがこれに当たる | — | — | `docs/specifications/ndf-work-during-approval-wait-and-business-hours.md` |
| 次に始めるプラン | — | 1 スプリントのレーンのスプリントで、ステージの並びの先頭から見て、結果が完了でも承認ゲートでもないプランを持つ最初のステージの、そのプランの集まり。答え待ちの停止の範囲で答えを受けたレーンを起こすかを決める | — | — | `docs/specifications/ndf-work-during-approval-wait-and-business-hours.md` |
| 溜めた依頼 | — | 業務時間外にオーガナイザーが利用者へ問わずに持っておく、設計の承認と問いの依頼。業務時間の始めにまとめて示す | — | — | `docs/specifications/ndf-work-during-approval-wait-and-business-hours.md` |
| 判定表 | — | development-workflow の「上から順に条件を判定する」の表。順・モード・該当条件の 3 列を持ち、最初に当たった行のモードを採る | — | — | `docs/specifications/ndf-sprint-mode-from-judgment-basis.md` |
| 判定の根拠 | — | 1 つの課題について、判定表の当たった行（順とモード）・当たった条件の文・触るファイルの一覧の組 | — | — | `docs/specifications/ndf-sprint-mode-from-judgment-basis.md` |
| 判定の材料 | — | 判定の根拠を選ぶためにスクリプトが集める事実。触るファイルの候補・公開インタフェースの変更の有無・対象のテストの有無 | — | — | `docs/specifications/ndf-sprint-mode-from-judgment-basis.md` |
| スプリントのモード | — | 1 つのスプリントに掛けるモード。スプリントの課題ごとの判定のうち、判定表で最も上の行のモード | — | — | `docs/specifications/ndf-sprint-mode-from-judgment-basis.md` |
| 判定の根拠のファイル | — | conductor が課題ごとの判定の根拠を書き、開発ループの loop sprint に --judgments で渡す JSON のファイル | — | — | `docs/specifications/ndf-sprint-mode-from-judgment-basis.md` |
| モードの決め直し | — | ループの 1 回から課題を外したとき、残った課題の判定の根拠からスプリントのモードを決め直すこと | — | — | `docs/specifications/ndf-sprint-mode-from-judgment-basis.md` |
| 立ち上がりの確認 | — | 耐久ワークフローが起動かレーンの再起動を打った後に、レーンの状態の一覧でそのレーンが動いている（または打った時刻より後に動いた）と読めるまで、決まった間隔で一覧を読むこと。上限は既定 120 秒 | — | — | `docs/specifications/ndf-parallel-durable-lane-launch.md` |
| 起動の再試行 | — | 立ち上がりの確認で立ち上がらなかった起動かレーンの再起動を、耐久ワークフローがもう 1 回だけ打つこと。2 回目も立ち上がらなければ区分 spawn の問いを立てる | — | — | `docs/specifications/ndf-parallel-durable-lane-launch.md` |
| 手順どおりの停止 | — | 一時レーンが、リリース承認ゲートの依頼か settle を送った後に自分のラッパーを止めること。区分 helper の問いの対象にしない | — | — | `docs/specifications/ndf-parallel-durable-lane-launch.md` |
| 長い待ち | — | オーガナイザーが次の知らせまで待つ見込みの時間が長く、状況報告を続けるよりセッションを切り替えた方が入力の費用が小さいと parallel.py wait が判定する待ち | — | — | — |
| 状況報告 | — | parallel.py wait が上限で戻ったときに、オーガナイザーがレーンごとの様子を利用者へ 1 行ずつ出す報告 | — | — | — |
| 切り替えの判定 | — | parallel.py wait が上限で戻るときに出す「切り替える」「報告して待つ」の判定と、その材料（metrics.switch） | — | — | — |
| プロンプトキャッシュの寿命 | — | 会話の入力がプロンプトキャッシュに残る時間。最後に使われた時点から数える | — | — | — |
| 出し直しの提示 | — | まとめたリリースで、承認の答えの時点でベースブランチが承認したコミットより進んでいたとき、加わった範囲を添えて同じリリース承認ゲートをもう 1 回出す提示（represent: true） | — | — | — |
| 加わった範囲 | — | まとめたリリースで、提示 1 で承認したコミットから承認の答えの時点のベースブランチの先頭までに入った変更 | — | — | — |
| 文書だけの範囲 | — | 加わった範囲のファイルを宣言 docs で分けて、コードのファイルと振る舞いを決める文書が 0 件、文書のファイルが 1 件以上の範囲。出し直しの提示を省く条件 | — | — | — |
| 出し直しを省く | — | 加わった範囲が文書だけの範囲のとき、出し直しの提示を出さず、新しい範囲で確定してリリースへ進むこと | — | — | — |
| 確かめ直しの範囲 | — | まとめたリリースで出し直しを省いたとき、提示 1 のコミットから提示 2 の承認資料を作った時点のコミットまでに入った変更。判定の後にベースブランチが進めば加わった範囲より広くなる。引き継いだ承認を記録する前にこの範囲が文書だけかを確かめる | — | — | — |
| 引き継いだ承認 | — | まとめたリリースで出し直しを省いたとき、提示 1 への承認を提示 2 の承認として答えの記録に書いたもの。何を引き継いだかを記録の what に残す | — | — | — |
| 触る場所の見込み | — | 設計の前に、課題の変更が触ると見込んだリポジトリの中のパスの集合。確定した触るファイルは設計の結果が持つ | — | — | — |
| 見込みの出所 | — | 触る場所の見込みをどこから出したか（設計の結果・課題グループの表・課題の本文・見込みなし） | — | — | — |
| 同時に動く本数 | — | スプリントの実装のステージの経過のあいだに、同時に動いていた実装のプランの本数の時間平均 | — | — | — |
| 順位の逆転 | — | 開発ループの選定で、順位の高い課題を触る場所の見込みの重なりを理由に後ろへ回し、順位の低い課題を先に選ぶこと | — | — | — |
| 逆転の幅 | — | 開発ループの選定で、残りの枠の数を超えて並べ替えの対象にする候補の件数。0 なら順位の逆転は起きない | — | — | — |

## NDF の Slack 通知（`ndf-notification`）

利用者の手を待つ時点・その種類・通知の本文と復帰先

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 待ち通知 | — | 利用者の回答か承認が無いと進まない時点で、Slack へ送る知らせ | 待ちの通知 | — | — |
| 回答待ち | — | 利用者に問いへの答え（選択・情報・指示）を求めている待ち | — | — | — |
| 承認待ち | — | 利用者に操作の許可（ツールの実行・プラン・マージ・リリースなど）を求めている待ち | — | — | — |
| 待ちのキー | — | 1 つの待ちを見分ける値。Claude Code では transcript の最後の user の項目の `uuid`（利用者が答える・許可すると変わる） | 待ちの鍵 | — | — |
| 復帰先 | — | 通知から当該セッションへ復帰する手段。ホスト名・cwd の行と、作れればセッションの URL か再開のコマンド | 戻り先 | — | — |

## NDF の外部 CLI 委譲（`ndf-agent-cli`）

cross-review / cross-refactoring が参加者の CLI を選び、起動し、監視し、結果を読むときの語。ランタイム・ホスト・参加者プール・起動結果

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 起動結果 | `launch_outcome` | 担当 CLI の起動 1 回の終わり方。監視の状態と理由、結果ファイルの有無と読めるかを合わせて持つ（LaunchOutcome） | 結末 | — | `docs/specifications/cross-review-launch-outcome.md` |
| リトライ可否 | — | 同じ担当を同じ条件で起動し直せば解ける理由か（assignment.relaunch_same_agent）。起動結果ではなく振り替えの規則が持つ | 起動し直しの可否 | — | `docs/specifications/cross-review-launch-outcome.md` |
| 結果ファイル | `result_file` | 担当 CLI が書く判定の要約の JSON（<stem>-result.json） | — | — | `docs/specifications/cross-review-launch-outcome.md` |
| 監視結果ファイル | `monitor_outcome` | 監視が起動 1 回ごとに状態と理由を書く JSON（<stem>-monitor.json） | 監視の結果ファイル | — | `docs/specifications/cross-review-launch-outcome.md` |
| 監視ログ | — | 起動結果を追記だけで積む記録（monitor-outcomes.jsonl） | 監視の記録 | — | `docs/specifications/cross-review-launch-outcome.md` |
| ランタイム | `runtime` | エージェントの CLI の種類。claude / codex / agy / kiro の 4 つで、並びは固定（ALL_RUNTIMES） | — | — | `docs/specifications/cross-review-participants-and-seats.md` |
| ホスト | `host` | 収束ループを起動している CLI のランタイム（host） | — | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 参加者プール | — | Skill ごとに決まる参加者の出発点。cross-review と cross-refactoring で共通の claude / codex / kiro とホスト（default_pool） | 参加の母集合 | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 参加者 | `participant` | 参加者プールに --include の者を加え、--exclude の者を除いた一覧。認証確認の対象 | — | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 利用可能な参加者 | — | 参加者のうち参加の確認を通った者（participants.available） | 使える者 | — | `docs/specifications/cross-participant-admission-check.md` |
| 認証確認 | — | 参加の確認の最初の種類。確認コマンド（AUTH_PROBES）を走らせ、認証を通っているかだけを見る | 認証の確認 | — | `docs/specifications/cross-participant-admission-check.md` |
| スロット | `slot` | 1 ラウンドで 1 つの CLI プロセスが占める枠 | — | — | `docs/specifications/cross-review-participants-and-seats.md` |
| フォールバック | `fallback` | 利用可能な参加者が 2 者に満たないとき、足りない分を埋める参加者（participants.fallback） | 埋め合わせ | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 実測値 | `observed_model` | CLI の出力かランタイムのセッションの記録から取った、実際に動いたモデル名。状態ファイルの observed | — | — | — |
| 指定値 | — | --model で CLI へ渡したモデル名。状態ファイルの requested。未指定なら null | — | — | — |
| 主たるモデル | — | 1 回の起動で複数のモデルが動いたとき、実測値として記録する 1 つ | — | — | — |
| 分離 | — | 何が動いたか分からない実行を、モデル別の集計に入れず理由だけを報告すること | — | — | — |
| 起動の記録 | — | 起動 1 回の開始の時刻・作業ディレクトリ・ランタイムを、起動が自分で書いたファイル（<stem>-launch.json） | — | — | — |
| 取れなかった理由 | — | 実測値が null のときに残す符号（no_record / ambiguous / no_model_field / unsupported / unreadable）。状態ファイルの unobserved | — | — | — |
| ランタイムの宣言 | — | .ndf/runtimes.json。NDF が CLI として起動してよいランタイムの一覧（allowed）と、cross-review の固定の組（review_seats）を持つ。無ければ制限しない | — | — | — |
| レビューの組 | — | cross-review の 1 ラウンドの 2 つのスロットのランタイムを辞書順に + でつないだもの（claude+claude・claude+codex など）。組ごとの集計の単位 | — | — | — |
| 固定の組 | `review_seats` | ランタイムの宣言の review_seats。cross-review の毎ラウンドのスロットを交代させずにこの 2 つにする | — | — | — |
| 担当 | — | レビュー・提案・実装を受け持つ CLI の起動の単位。ランタイムと、claude ではアカウントで決まる | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 結果なし | — | 担当が結果ファイルを残さずに終わったこと（judge の verdict = no_result、cross-refactoring の failed_attempts） | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 利用上限 | — | 担当の CLI が使用量の上限に当たって止まったこと（起動結果の理由 usage_limit） | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 振り替え | — | 結果なしの担当の役割を、同じ実行の中で別の担当へ移すこと。同じ担当で起動し直すことは含まない | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 結果なしの記録 | `no_results` | 状態ファイルの no_results。結果なしの 1 回ごとに、担当・理由・振り替えの規則の答え（relaunch / reassign / abort）・振り替え先を追記だけで積む | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 外した担当 | — | 結果なしの記録から導く、同じ実行の残りで割り当てないランタイム（claude ではアカウント） | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 最小の呼び出し | — | 担当を起動するときと同じモデルの指定で、短い固定の問いを 1 回投げて応答が返るかを見る確認。認証の確認に続けて行う | — | — | `docs/specifications/cross-participant-admission-check.md` |
| 既定のモデル | — | 利用者の CLI の設定も引数の指定も無いときに、CLI が使うモデル | — | — | `docs/specifications/cross-participant-admission-check.md` |
| 参加の確認 | — | 参加者ごとに、認証確認・最小の呼び出し・（設定のモデルを引けなければ）既定のモデルでの引き直しを順に行い、担当に入れるかと理由を決めること（auth.probe_auth が走らせ、assignment.admit が判断する） | — | — | `docs/specifications/cross-participant-admission-check.md` |
| モデルを引けない | `model_unavailable` | 担当の CLI が、指定か設定のモデルを使えないと返したこと（参加の確認と起動結果の理由 model_unavailable） | — | — | `docs/specifications/cross-participant-admission-check.md` |
| 認証の失効 | `auth_expired` | 認証の状態確認は通るが、更新トークンの失効などで実行のときに認証を作り直せないこと（参加の確認と起動結果の理由 auth_expired） | — | — | `docs/specifications/cross-participant-admission-check.md` |
| 既定のモデルへの切り替え | `default_models` | 引数で明示していないモデルを引けない CLI を、既定のモデルで担当に入れること（participants.default_models） | — | — | `docs/specifications/cross-participant-admission-check.md` |
| 相手役 | — | cross-review のレビュー担当、cross-refactoring の提案・実装の担当として、ホストから CLI プロセスで起動されるランタイム（#2231） | — | — | `docs/specifications/ndf-distribution-runtimes.md` |

## NDF の cross-review（`ndf-cross-review`）

Pull Request のレビューを CLI へ委譲し、新しい指摘が出なくなるまで回す収束ループの語。指摘・反証・総評・修正担当

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 指摘 | `finding` | 担当が出した 1 件の主張（review_findings[] の 1 要素） | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 反証条件 | — | 何が成り立てばその指摘を棄却できるか（falsification） | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 検証手順 | — | 指摘を実行できる形で確かめる手順（suggested_check） | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 反証 | — | 提案者以外の担当が、各指摘へ返す 1 つの値 | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 指摘区分 | — | 1 件の指摘を分ける 6 つの分類（classification） | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 集約方式 | — | 効果の測定で指摘を採る規則。single / majority / proposed / oracle の 4 つ | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 代表指摘 | — | 統合した組で判定が読む 1 件。統合された側は merged_into を持つ | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 新しい指摘 | — | 直前のラウンドの指摘と一致しない、そのラウンドの指摘。収束ループはこれが 0 件になるまで回す | — | — | `docs/specifications/cross-review-round-inputs.md` |
| 修正担当 | — | 指摘を直してコミットするサブエージェント（/ndf:fix を実行する） | 修正の担当 | — | `docs/specifications/cross-review-writes-to-conductor.md` |
| 投稿キュー | `post_queue` | 送る前に投稿を積み、上限か一時的な失敗で送れなければ残す仕組み（lib/post_queue.py。一時的な失敗は #1843）。スプリント 2c の後、項目は耐久の記録に置く | 投稿の待ち行列 | — | `docs/specifications/cross-review-writes-to-conductor.md` |
| 重複投稿 | — | 送ろうとした投稿と同じものとして、すでに Pull Request にある投稿 | 先客 | — | `docs/specifications/cross-review-writes-to-conductor.md` |
| 総評 | — | レビュー本体に書く文章（body）。インラインのコメントとは別に置く | — | — | `docs/specifications/cross-review-writes-to-conductor.md` |
| 総評だけの指摘 | — | インラインのスレッドを持たず、総評にだけ書かれた指摘（review_findings[] の posted_to が body のもの）。差分の外を指す指摘と、HTTP 422 で総評へ移した指摘がこれになる | — | — | `docs/specifications/cross-review-body-findings.md` |
| 決着 | — | 指摘が直した・見送った・却下したのどれかに振り分けられ、取り込み（修正のラウンドの merge-fix か最終スイープの verify-sweep）が記録したこと。スレッドの指摘ではスレッドの解決も含む | — | — | `docs/specifications/cross-review-body-findings.md` |
| 決着の記録 | — | 状態ファイルの body_settlements[] の 1 要素。総評だけの指摘 1 件の振り分け（fixed / deferred / waived / separate_pr / rejected）と、記録したラウンドと経路（修正のラウンドの merge-fix か最終スイープの verify-sweep か）を持ち、追記だけで書き換えない | — | — | `docs/specifications/cross-review-body-findings.md` |
| drive の状態 | `drive_state` | 収束ループの drive.py が Pull Request ごとに持つ状態ファイル（drive-pr<N>.json / drive-rf<ID>.json）。stage と init_vars を持つ。スプリント 2c の Q3・Q4 で耐久の記録へ移して廃止する | — | — | — |
| 指摘の基準 | — | 指摘として出してよいものを決める番号つきの条件。既定は 1 利用者が普通に使う経路の誤動作・2 レッドラインに触れるもの（外せない基準）・3 レビューの重点（宣言があるときだけ）・4 実装を違えさせる設計の食い違い。レビューのプロファイルは 2・3 以外の番号を差し替える。当たるものが major 以上になる | — | — | `docs/specifications/cross-review-review-profile.md` |
| レビューの重点 | — | プロジェクトが .ndf/review.json で宣言した、指摘の基準 3 に使う観点。宣言が無ければ基準 3 は無い | — | — | — |
| 見送りの返信 | — | 修正担当が minor / nit の指摘を直さずに閉じるときに書く返信。雛形（既定か、レビューのプロファイルの reply）の {inner} に見送りの種類の名前を入れて組む。既定の雛形は直す条件（使って困る場面が出たら直す）を定型文で書く | — | — | `docs/specifications/cross-review-review-profile.md` |
| 見送りの種類 | — | 見送りの返信の括弧に書く、指摘の基準に当たらない理由の分類（waive_kind）。既定は 5 分類で、レビューのプロファイルの waive_kinds が差し替える | — | — | `docs/specifications/cross-review-review-profile.md` |
| レビューのプロファイル | `review_profile` | プロジェクトが .ndf/review.json に書く、指摘の基準・書かないもの・見送りの種類・見送りの返信・自動レビュー観点の差し替えの組。version 2 では profile に 1 つ、version 3 では profiles に名前つきで複数を書き、PR ごとに 1 つが効く。書いた鍵だけが既定を差し替える。version 3 の最上位の criteria・not_written は、どのプロファイルを選んでも（選ばなかったときは既定の組にも）足す。文中では「プロファイル」と略す | — | — | `docs/specifications/cross-review-review-profile.md` |
| プロファイルの選択 | — | PR の変更ファイルのパスから、その PR に効かせるレビューのプロファイルを 1 つ決めること。宣言の profiles の先頭から見て、変更ファイルのどれかに paths が当たる最初のものを選び、paths を持たないものはどの PR にも当たる。選んだ名前を init の出力・レビューの状態ファイル・report の要約に残す | — | — | `docs/specifications/cross-review-review-profile.md` |
| 自動レビュー観点 | — | cross-review が変更ファイルの分類（自動カテゴリ）から選び、レビューの両スロットへ渡す観点の文。--focus と --extra-instructions-file の観点はこの後ろに足す | — | — | `docs/specifications/cross-review-review-profile.md` |
| 外せない基準 | — | 指摘の基準 2（秘密・認証認可・利用者のデータ・戻せない操作）。レビューのプロファイルがあっても消せず、番号 2 のまま出る | — | — | `docs/specifications/cross-review-review-profile.md` |
| 最終スイープ | — | 収束ループを抜けた後に /ndf:fix を通し、open thread を 0 にする工程 | — | — | — |
| レビューの状態ファイル | — | cross-review が <worktree>/.cross_review/cross-review-pr<PR>-state.json に置く、ラウンドの履歴（rounds）と終わり方（final）の記録 | — | — | `docs/specifications/cross-review-reopen-and-hook-deadline.md` |
| 修正の記録 | — | 修正の工程が作る戻り値ファイル（fix-pr<PR>-result.json）を merge-fix がレビューの状態ファイルへ取り込んだもの。ホストが自分で直したときは state.py record-fix が同じ形で作る。修正必須のラウンドの次のラウンドを始める条件 | — | — | `docs/specifications/cross-review-reopen-and-hook-deadline.md` |
| ラウンドを足す | — | final が確定したレビューの状態ファイルの final を外し、履歴を残したまま次の番号のラウンドから収束ループを続けること。state.py init の再開の経路が行い、reopens に 1 件残す | — | — | `docs/specifications/cross-review-reopen-and-hook-deadline.md` |
| 最終ステータス | `review_status` | cross-review の収束の終わり方を表す 1 語（approved / unverified / final の値 / unknown）。loop_drive.review_status が状態ファイルから決める | — | — | — |
| 回答ファイル | — | 駆動が止まりで示す items[0].result_file。止まりへの答えを書き、同じコマンドの打ち直しが読む。担当 CLI の結果ファイルとは別 | — | — | — |
| 席 | — | cross-review の 1 ラウンドの 2 つの担当の枠。名前は kiro / kiro-2 の形（SEAT_PATTERN） | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 一時的な失敗 | — | 送り直せば届く見込みのある投稿の失敗（HTTP 500・502・503・504、`(HTTP nnn)` の無い本文なしの応答、ネットワークの失敗）。上限とは別に見分け、投稿キューに残して後で流す（#1843） | — | — | — |
| 投稿待ち | — | 担当の結果は記録へ取り込んであり、投稿だけが投稿キューに残っている状態。結果なし（NO_RESULT）とは別である（#1843） | — | — | — |
| レビュー範囲 | — | そのラウンドで担当が見る差分。base と head の SHA の組と、その間の変更ファイル | — | — | `docs/specifications/cross-review-review-scope.md` |
| 読んだファイル数 | — | 担当が結果ファイルの read_files に書く、レビュー範囲のうち差分を読んだファイルの数。担当の自己申告 | — | — | `docs/specifications/cross-review-review-scope.md` |
| 範囲の照合 | — | 判定で、通した担当の読んだファイル数をレビュー範囲のファイル数と突き合わせ、半分に満たない・測れない担当の「通した」を収束に数えないこと | — | — | `docs/specifications/cross-review-review-scope.md` |

## NDF の cross-refactoring（`ndf-cross-refactoring`）

想定最大時間に収まるリファクタリング計画を立てて通す語。改善候補・改善項目・配分テーブル・最終ゲート

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| マージ処理 | — | 担当の結果やコミットをオーケストレーターが受け入れるコマンド（merge-proposals / merge-plan / merge-tests / merge-implement / merge-fix / merge-final-fix）。フェーズの「取り込み」とは別 | — | — | `docs/specifications/cross-refactoring-apply-intake.md` |
| 最終ゲート | `final_gate` | 全体テストか CI で合否を判定する、cross-refactoring の最後の手順（final-gate）。承認ゲートとは別 | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 最終ゲート修正 | — | 最終ゲートの失敗を直す起動と、そのマージ処理（final-fix / merge-final-fix）。改善項目に属さない | 最終ゲートの修正 | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 必須トレーラー | — | 担当がコミットメッセージのトレーラーとして書く Item-Id / Impl-Runtime / Impl-Model。最終ゲート修正は Item-Id を除く 2 つ | 必須の記名 | — | `docs/specifications/cross-refactoring-apply-intake.md` |
| 帰属トレーラー | — | 実行環境がコミットメッセージの末尾へ足すトレーラー（Co-Authored-By: / Claude-Session:） | 帰属の段落 | — | `docs/specifications/cross-refactoring-apply-intake.md` |
| プロダクションコード | — | コードの拡張子を持ち、テストの置き場所でないファイル（production_code_changes）。名前がテストの形でもテストの関数を持たない .py は、tests/ などのテスト用ディレクトリの配下になければ含む | 本番コード | — | `docs/specifications/cross-refactoring-round-tests-and-assess.md` |
| テストの関数 | — | Python のファイルで、名前が test で始まる関数かメソッドの定義（async def を含む）。pytest が既定で集める形。名前がテストの形の .py は、これを持つときだけテストのファイルになる | — | — | `docs/specifications/cross-refactoring-scope-template-probe.md` |
| 実装担当 | — | リファクタリング計画・テスト追加・実装・修正・最終ゲート修正を通して担う 1 ランタイム（--implementer） | — | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 改善候補 | — | 提案を path + symbol + smell のキーで集約したもの。リファクタリング計画へ渡す（candidates[]） | — | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 改善項目 | — | リファクタリング計画が採った改善候補。I-001 の形の ID を持ち、Item-Id の同じコミットを何件でも持てる（テストを足すコミットは 1 件） | — | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 配分テーブル | — | 種類ごとの 1 件あたりの所要（分）。履歴の直近からリファクタリング計画のたびに集計し、保存しない（plan.table） | — | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 変更したファイル | — | 判定の起点から変わったファイルのうち、worktree に残るもの。起点は判定ごとに決まる。項目の範囲テストではその項目のコミット（実装と修正）、最終ゲートでは着手前の HEAD、`test-run.py whole` では merge-base である。そのうち静的解析の suite の `paths` に当たるものが、その suite の範囲になる | — | — | — |
| フレーキー / 既存失敗 / 変更起因 | — | 全体テスト（着手前・危険フラグ・最終ゲート）で落ちたテストの 3 つの分類（flaky / preexisting / caused。着手前は baseline_test.existing_failures（既存失敗）、危険フラグは whole_test、最終ゲートは final_gate.checks[] の記録に書く）。ID は JUnit から読み、落ちたファイルだけを HEAD と比較の木で走らせ直して分ける。フレーキーと既存失敗は項目の合否に数えず、フレーキーはその実行の中で直す | 元からの失敗 | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 指標 | — | `cross-refactoring` が提案の前に対象範囲のコードを測定ツールで測った値。関数ごとの循環的複雑度（Python では認知的複雑度も）、ファイルごとの大きさ、行数、重複の箇所 | — | — | — |
| 指標のファイル | — | 提案の前に 1 回だけ作り、参加者の全員が読む指標の測定の結果 | — | — | — |
| 測定ツール | — | 言語ごとに指標を測る外部のコマンド（Ruff・complexipy・lizard・symilar・jscpd など） | — | — | — |
| 測定の宣言 | — | 言語ごとの測定ツールをプロジェクトが置き換える `.ndf/code-metrics.json` | — | — | — |
| 根拠の値 | — | 提案が根拠にした指標の値。提案の JSON の `evidence` に書き、採否には使わない | — | — | — |
| 重複の箇所 | — | 対象範囲の中で、同じコードが最小の行数（8 行）以上続く 2 か所以上の組。Python は symilar、ほかの言語は jscpd が見つける | — | — | — |
| 取り消しの判定 | — | 改善項目ごとに取り消したかと、起点から HEAD までの各コミットを残すか・消すか・戻すか・公開してよいかを 1 か所で決める処理。取り消しの経路と push の直前の照合が同じものを呼ぶ | — | — | — |
| 残留コミット | — | 見放した（STALLED・結果なし）実装担当のプロセスが、オーケストレーターの判定の後に積んだコミット | — | — | — |
| 途中の push | — | cross-refactoring で、最終ゲートへ入るより前の push | — | — | — |
| 残すコミット | — | 取り消しの判定が残すと決めたコミット。取り消されていない改善項目に記録されたコミット・受け入れた最終ゲート修正のコミット・オーケストレーターが状態ファイルに記録したコミットのどれかで、公開してよいのはこれだけである | — | — | — |
| 公開した地点 | — | cross-refactoring のオーケストレーターが最後に push した HEAD。まだ push していなければ plan.base_sha。これより前のコミットは取り消しで書き換えない | — | — | — |
| 原因の項目 | — | 全体テストか範囲テストで変更起因として落ちたテストを、その変更で落とした改善項目。危険フラグの有無とは関係しない | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 巻き込まれた項目 | — | 範囲テストが落ち、原因の項目が自分以外だけに決まった改善項目。取り消さず、原因が片づいてから走らせ直す | — | — | — |
| 公開前の静的解析 | — | 最終ゲート修正の取り込みで、push の前に、修正のコミットが変えたファイルへ当てる静的解析の範囲テスト | — | — | — |
| 原因の手がかり | `basis` | 原因の項目を決めた根拠。path（落ちたテストの出力に項目の変えたファイルのパスが現れた）・isolate（項目を外した走らせ直しで通った）・undetermined（どちらでも決まらない）の 3 つ | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| リファクタリング計画のコメント | — | cross-refactoring が対象の Pull Request に 1 件だけ置き、結果の出口のたびに同じものを編集するリファクタリング計画の記録。目印 `<!-- cross-refactoring plan rf<ID> -->` で引き当てる | — | — | `docs/specifications/cross-refactoring-plan-comment-at-exits.md` |
| 結果の出口 | — | リファクタリング計画ができた後に、cross-refactoring のスクリプトがその時点の結果を確定させて終了コードを返す地点（push の成功と失敗・最終ゲートの判定・打ち切り・取り消し・中断・finalize） | — | — | `docs/specifications/cross-refactoring-plan-comment-at-exits.md` |
| 未確認 | `unconfirmed` | 取り消されずに残ったが、最終ゲートが passed になっていない改善項目の状態。採用とは数えない | — | — | `docs/specifications/cross-refactoring-plan-comment-at-exits.md` |
| プランの外の取り消し | — | cross-refactoring のスクリプトが終わった後に、conductor が改善項目のコミットを git revert で取り消すこと | — | — | `docs/specifications/cross-refactoring-plan-comment-at-exits.md` |
| 公開の結果 | `publication` | 最後に試みた head ブランチへの push の結果（通った地点の SHA か、落ちた理由）。状態ファイルの publication に残す | — | — | `docs/specifications/cross-refactoring-plan-comment-at-exits.md` |
| 未公開の改善項目 | `unpublished` | 手元の HEAD が、公開した地点から到達できないコミットを持つこと。結果 JSON の metrics.unpublished | — | — | `docs/specifications/cross-refactoring-plan-comment-at-exits.md` |
| 宣言に無いジョブ | — | 継続的統合のジョブのうち、どの suite の ci_jobs にも除外したジョブにも当たらないもの。cross-refactoring の init が知らせ、状態ファイルの ci_coverage.undeclared に残す | — | — | `docs/specifications/cross-refactoring-lint-suite-and-ci-coverage.md` |
| 単独起動 | — | --workflow-step を付けずに呼んだ cross-refactoring の起動。最終ゲートが cross-review の承認収束になる | — | — | — |
| 対象のリポジトリ | — | cross-refactoring が Pull Request を取り、作業ディレクトリを作るリポジトリ。打った場所（現在のディレクトリ）が属する git の worktree の根で決まる | — | — | — |
| 提案担当 | — | cross-refactoring の propose で改善の提案を出す参加者 | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 着手前のテスト | — | `cross-refactoring` の init が改修の前に 1 回走らせるテスト。CI に任せる戦略では --scope のテストの置き場所の範囲テスト、ほかは全体テスト（round-only はラウンドテストも） | — | — | — |
| 着手前のテストの初回の上限 | — | 同じ範囲の打ち切りと実測が配分の履歴に無いときの、着手前のテストの上限（秒）。max(0.10·B, 3·s) で、s は手元の全体テストの直近の実測 m → 宣言の w の順に採る（どちらも無ければ 0.10·B） | — | — | — |
| 時間の枠の不足 | — | 着手前のテストが上限で打ち切られたか、明示した想定最大時間が想定最大時間の下限に届かないこと。init は SKIP を出して止まり、駆動はリファクタリングを飛ばした欠けの残る結果を返す | — | — | — |
| 範囲テストの所要 | — | CI に任せる戦略で、着手前に走らせる範囲テストの所要の見込み（秒）。同じ範囲の履歴の実測 → 手元の全体テストの直近の実測 m → 宣言の w の順に採る | — | — | — |
| 直さなかった項目 | — | cross-refactoring で、結果を残して終わった修正の起動の後、修正の範囲に自分の Item-Id のコミットが 1 つも無い改善項目。次の検証の最初に取り消す | — | — | `docs/specifications/cross-refactoring-failed-item-rules.md` |
| 確かめ直し | — | cross-refactoring で、最終ゲートより前の取り消しで HEAD が変わった後、残った改善項目を新しい HEAD の範囲テストで判定し直すこと | — | — | `docs/specifications/cross-refactoring-failed-item-rules.md` |
| 新しいファイル | — | cross-refactoring の実行で、リファクタリング計画の起点（plan.base_sha）のツリーに無かったパス。置き場所によらず範囲の中として扱う | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 呼び手の書き換え | — | cross-refactoring で、範囲の外の既存のファイルのうち、項目が変えた名前の読み込み・呼び出しだけを書き換える変更。ハンクごとに機械で判定し、通したファイルは最終ゲートのレビューへ引き継ぐ | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 変えた名前 | — | cross-refactoring の判定の単位のコミットが、範囲の中のファイルと新しいファイルで変えた行に現れる識別子と、作った・消した・移したパスの語幹とディレクトリ名から、ありふれた語を除いたもの（修正・最終ゲート修正の単位では、その項目（最終ゲート修正なら採った全項目）の実装・修正のコミットが変えた名前も含める） | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 判定の単位 | — | cross-refactoring の取り込みが 1 度に同じ規則へ掛けるコミットの組。実装は項目ごとの全コミット、修正は修正の範囲の項目ごと、最終ゲート修正は修正の全コミット | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 代表の兆候 | — | cross-refactoring の提案の smell に観点の識別子が書かれたとき、写す先の兆候 | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| ありふれた語 | — | cross-refactoring の呼び手の書き換えの判定で、リファクタリング計画の起点のツリーのコードのファイルの 20% 以上かつ 3 ファイル以上に現れる識別子。変えた名前から除き、追加の行に現れてよい | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 止まっていた時間 | — | `cross-refactoring` を中断してから、init か drive.py を打ち直して再開するまでの時間。リファクタリング計画の前の中断を含む。再開の時刻 − 最後の動きの時刻で測り、想定最大時間に数えない | — | — | `docs/specifications/cross-refactoring-implementation-window-and-readopt.md` |
| 最後の動きの時刻 | — | `cross-refactoring` の作業ディレクトリ（`.cross_refactoring/`）の直下のファイルのうち、最も新しい更新時刻。状態ファイル・CLI のログ・テストのログ・結果ファイルと、心拍のファイルを含む。止まっていた時間の起点 | — | — | `docs/specifications/cross-refactoring-implementation-window-and-readopt.md` |
| 心拍のファイル | — | `cross-refactoring` の作業ディレクトリ直下の `cross-refactoring-rf<ID>-alive`。出力の有無によらず、CLI の監視とテストの実行が待っている間、心拍の間隔（15 秒。見回りの間隔に依らない）ごとに更新時刻を今にする。中断で止まると更新も止まり、最後の動きの時刻が稼働の終わりを指す | — | — | `docs/specifications/cross-refactoring-implementation-window-and-readopt.md` |
| 採り直し | — | `cross-refactoring` で検証の後に時間が残ったとき、budget で見送った候補と実装の終わりまでにコミットが無かった改善項目から、残った時間に入るものを順位の順に採り、テストの追加・実装・検証をもう 1 巡回すこと。計画し直さない | — | — | `docs/specifications/cross-refactoring-implementation-window-and-readopt.md` |
| 見送った改善項目 | — | リファクタリング計画が採った改善項目のうち、表示の状態が見送りのもの（実装の終わりまでにコミットが無く採り直しにも入らなかった not_done・足したテストが落ちた test_failed）。結果 JSON の metrics.deferred は、この数に時間（budget）で見送り項目に入っていない候補の数を足す（#1944） | — | — | `docs/specifications/cross-refactoring-pr-gate-and-skip-counts.md` |
| 見送った提案 | `deferred_items` | 計画に入らなかった提案と見送った改善項目を合わせたもの。理由を 1 つ持つ。状態ファイルの deferred_items | — | — | `docs/specifications/cross-refactoring-pr-gate-and-skip-counts.md` |
| テストの無い宣言 | — | .ndf/project.json の test があり、unknown と strategy を持たず、種別・雛形の誤りが無く、どの suite も command と scope_command のどちらも持たないもの（suite が 0 個のときを含む）。解析が「テストが無い」と測った {"suites": []} を含む。cross-review の最終スイープは検証のコマンドを名指ししない | — | — | `docs/specifications/cross-review-no-test-declaration-sweep.md` |
| テンプレートの試走 | — | cross-refactoring の init が着手前のテストの後に、範囲テストのテンプレートへテストのファイルを最大 2 本入れて 1 回走らせ、{paths} に入るパスを受けるかを見分けること。不備か判定できなければ改修の前に止まる | — | — | `docs/specifications/cross-refactoring-scope-template-probe.md` |
| 実装の予算 | — | `--budget-minutes` の値（既定 60 分）。採る改善項目の見積り（テストの追加と実装の和）の合計の上限で、選定にだけ使う。締め切りを導かない | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 実装時間 | — | 改善項目のテスト追加・実装・修正で担当の CLI が動いた時間。提案・選定・テストの実行・CI の待ちは含めない | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 予算の延長 | — | グレード high の候補の見積りの和が実装の予算を超え 2 倍以下のとき、実効の予算をその和まで増やすこと | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| リファクタリングの成立 | — | 改善項目が、振る舞いを変えずに、計画の前提にした構造の上で直し切れる見込みがあること。成立しない例: 振る舞いを変えないと直せない・前提の構造が実在しない | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 無関係な失敗 | — | 着手前の木（取り込んだベースブランチを含む）でも落ちるテストの失敗。改善項目の合否に数えない | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 停滞 | — | 修正の後の落ちたテストの集合が、前の回と同じであること | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 振動 | — | 修正の後の落ちたテストの集合が、前に見た集合へ戻ること | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 安全網の上限 | — | コーディングの CLI（テストの追加・実装・修正・最終ゲート修正）の起動ごとの秒の累計に置く上限。既定は実効の予算 × 2。異常の検知にだけ使い、当たっても取り消さずに止めて人へ渡す | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 実効の予算 | — | 予算の延長を当てた後の実装の予算。延ばさなければ実装の予算と同じ | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 比較の木 | — | HEAD から、取り消していない改善項目と最終ゲート修正のコミットをすべて外した木。着手の後に取り込んだベースブランチの変更を含む。そこでも落ちるテストを無関係な失敗として見分ける | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| フレーキーの直し | — | cross-refactoring がその実行の中でフレーキーと見分けたテストを、実装担当に 1 テストにつき 1 回だけ直させる工程（flaky-fix）。直しは Flaky-Fix のトレーラーを持つテストだけのコミットで、項目の合否と実装時間に数えない | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 差し戻し | — | 改善項目の中の手順の違反（記名の欠け・期待値の欠け・文言を固定するテスト・計画に無いコミット）を、項目を取り消さずに担当へ直させること | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 落ちたテストの材料 | — | 落ちたテストの ID・ログの置き場・走らせ直すコマンドの組。JUnit を読めないときもログから組み、修正と成立の判定へ渡す | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |
| 判定の担い手 | — | 成立の判定と判定し直しを行うランタイム。参加者のうち実装担当でない者を、ホスト → 参加者の順で選ぶ | — | — | `docs/specifications/cross-refactoring-implementation-budget.md` |

## NDF のラッパー（`ndf-relay`）

ラッパーがセッションを切り替え、シェルの設定へ組み込まれるときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 管理ブロック | — | シェルの設定の # >>> ndf relay >>> から # <<< ndf relay <<< までの行 | — | — | `docs/specifications/ndf-relay-install-and-restart.md` |
| 自動追加ブロック | — | 10.17.4〜10.17.6 の SessionStart hook が足した管理ブロック。状態ディレクトリの rc-added に載り、rc-user に載らない | 自動の囲み | — | `docs/specifications/ndf-relay-install-and-restart.md` |
| 状態ディレクトリ | `state_dir` | ${XDG_STATE_HOME:-~/.local/state}/ndf/relay/。記録（rc-added など）と作業ディレクトリを持つ | 状態の親 | — | `docs/specifications/ndf-relay-install-and-restart.md` |
| 質問シグナルファイル | — | 作業ディレクトリの question。質問が表示されているあいだ在る | 質問のシグナルファイル | — | `docs/specifications/ndf-relay-install-and-restart.md` |
| 入力注入 | — | ラッパーが子の擬似端末へ /exit 以外の入力を書くこと | 送り込み | — | `docs/specifications/ndf-relay-install-and-restart.md` |
| 起動オプション | — | 最初のセッションの claude の引数のうち、セッションごとに変わらないもの（--dangerously-skip-permissions など） | 起動の方針の引数 | — | `docs/specifications/ndf-relay-install-and-restart.md` |
| 停止シグナルファイル | — | ラッパーに次のセッションを起動させないために置く空のファイル（stop） | 停止のシグナルファイル | — | `docs/specifications/ndf-relay-segment-restart.md` |
| 再起動ループ | — | シグナルファイルを書いて終わったセッションが短い時間で続き、進まずに起動だけが重なる状態。ラッパーは次のセッションを起動しない | — | — | `docs/specifications/ndf-relay-segment-restart.md` |
| ラッパーのバージョンディレクトリ | `relay_version_dir` | ~/.claude/ndf/ に版ごとに置く、ラッパーの relay_lib/ と使うライブラリのコピー。relay.current が使うバージョンディレクトリを指す | ラッパーの束 | `relay_bundle` | — |
| ランチャー | `relay_launcher` | ~/.claude/ndf/relay.py（プラグインの scripts/relay.py と同じバイト列）。使うバージョンディレクトリを選び、relay_lib を読み込んで起動するだけのエントリポイント | — | — | — |
| ラッパーの入れ替え | `relay_reexec` | セッションの切り替えで子の claude が居ない間に、動いているラッパーのプロセスが、PID と端末を保ったまま別のラッパーのバージョンディレクトリのコードで起動し直すこと | — | — | — |
| 入れ替えの申し送り | `relay_handover` | ラッパーの入れ替えの直前に作業ディレクトリへ書く、次のセッションの起動の入力とラッパーの状態のファイル（handover.json）。入れ替えた後のラッパーが読んで消す | — | — | — |
| 登録済みアカウント | — | 利用者が登録のコマンドで認証情報を預けた claude のアカウント。アカウントの切り替えの候補になる | — | — | — |
| アカウントの切り替え | — | 次に起動する claude（ラッパーの次の区間・worker のやり直し）を、別の登録済みアカウントの認証で起動すること。動いているプロセスの認証は替えない | — | — | — |
| 使用率 | — | api/oauth/usage が返す five_hour / seven_day の utilization と、limits[] の weekly_scoped の percent（%）。推論を呼ばずに読む。切り替えの閾値と比べるのはその最大 | — | — | — |
| 支出上限 | — | 追加利用の支出の上限（individual spend limit）。extra_usage.spend_limit_reached が真なら達したとする。spend.percent ≥ 100・spend.severity = critical は表示の補足だけに使う（#2036） | — | — | — |
| 従量の接続 | — | 利用上限の無い、使った分だけ費用が掛かる claude の接続（Bedrock か Anthropic API の API キー）。中身は利用者が宣言し、登録済みアカウントがすべて上限か認証できないときに使う | — | — | — |
| アカウントの置き場 | — | 登録済みアカウントごとの設定ディレクトリを並べた <共有の設定ディレクトリ>/ndf/accounts/。NDF の側で書くのは lib/claude_accounts.py とその部品だけで、認証ファイルと .claude.json は claude 自身も書く | — | — | — |
| 上限シグナルファイル | — | 子の claude の応答が API の失敗で終わったときに、StopFailure hook がラッパーの作業ディレクトリへ書く limit.json | — | — | — |
| 切り替えの閾値 | — | 今のアカウントの使用率がこれを超えたら、次のカットポイントで別の登録済みアカウントへ替える値（NDF_ACCOUNT_SWITCH_AT、既定 90%） | — | — | — |
| 従量の接続の宣言 | — | 従量の接続で起動する子へ足す変数の並び（KEY=VALUE を空白区切り）。環境変数 NDF_SUPERVISE_CLAUDE_FALLBACK か、relay の置き場に保存した宣言が持ち、環境変数があればそちらだけが効く。ラッパーと supervise.py が同じものを読む | — | — | — |
| relay の置き場 | — | relay.py が登録と設定を置くディレクトリ ${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/。アカウントの置き場と、保存した従量の接続の宣言を持つ | — | — | — |
| 登録の途中の状態 | — | OAuth の account add を 2 回の呼び出しに分けたとき、1 回目（認可の URL を出す）と 2 回目（認可コードで登録を終える）の間に relay の置き場へ残す状態。期限を過ぎたら捨てる | — | — | — |
| 待機中のログイン | — | OAuth の account add の 1 回目で起動し、認可コードを受け取るまで生かしておく claude auth login のプロセス。登録の途中の状態が持ち、標準入力の FIFO でコードを受ける | — | — | — |
| 呼べるかの確認 | — | 従量の接続の宣言を保存する前に、その変数で Bedrock の Claude を 1 回呼び、通らなければ理由を 4 つの区分（認証切れ・権限が無い・地域で使えない・モデルが有効でない）で示すこと。通ったものだけを保存する | — | — | — |
| 残りの量 | — | 登録済みアカウントが上限に達するまでに使える量の見積り。枠ごとの「枠の大きさ ×（1 − 使用率 / 100）」の最小（USD 換算）。保存せず、選ぶ・一覧を出すたびに求める | — | — | — |
| 枠の大きさ | — | 1 つの枠（5 時間の枠・週の枠・モデル別の週の枠）が 0% から 100% になるまでに使える量（USD 換算）。支出上限とは別の量 | — | — | — |
| 枠の大きさの対応表 | — | rateLimitTier から 5 時間の枠と週の枠の大きさを引く表（claude_accounts.CAPACITY） | — | — | — |
| 枠の大きさの宣言 | — | 利用者が登録済みアカウントごとに書く枠の大きさ（account.json の capacity）。対応表の値より先に効く | — | — | — |
| モデル別の週の枠 | — | 使用量の応答の limits[] のうち kind が weekly_scoped のもの。特定のモデル（例: Fable）だけの週の上限 | — | — | — |
| claude.ai のコネクタ | — | 利用者が claude.ai で接続した MCP サーバー（Slack・Notion・Google Drive など）。Claude Code は起動時に claude.ai から一覧を取り、claude mcp list に claude.ai <名前> として並べる | — | — | — |
| アカウントのスコープ | — | 登録済みアカウントのトークンに付いた権限の並び。認証ファイルの claudeAiOauth.scopes で、claude が認証ファイルから自分で読む（環境変数では渡さない） | — | — | — |
| アカウントの設定ディレクトリ | `account_dir` | 登録済みアカウントごとのディレクトリ（アカウントの置き場の <名前>/）。認証ファイルと登録の記録を実体で持ち、そのアカウントで起動する claude の CLAUDE_CONFIG_DIR になる | — | — | — |
| 共有の設定ディレクトリ | — | ラッパーを通さずに起動した claude が使う設定ディレクトリ（${CLAUDE_CONFIG_DIR:-~/.claude}）。会話の記録・プラグイン・設定の正を持ち、登録済みアカウントの claude の中ではアカウントの設定ディレクトリの symlink の参照先になる | — | — | — |
| アカウント固有の項目 | — | アカウントの設定ディレクトリに実体で持ち、共有の設定ディレクトリへの symlink にしない項目（認証ファイル・.claude.json・更新の排他・NDF の記録など） | — | — | — |
| 共有する設定の部分 | — | .claude.json のうち、共有の設定ディレクトリの側を正とする projects と mcpServers | — | — | — |
| 同期の控え | — | 前の同期で両側へ書いた共有する設定の部分の写しと、その時点のアカウント側の userID（アカウントの設定ディレクトリの .ndf-shared-base.json） | — | — | — |
| 更新の排他 | — | Claude Code がトークンを更新するときに設定ディレクトリの中に作る排他（.oauth_refresh.lock のディレクトリ） | — | — | — |
| 認証の失敗の観測 | — | 子の claude の応答が認証の失敗で終わったことの記録（account.json の auth_failed）。後の使用量の取得が成功するか 1 時間で解ける | — | — | — |
| 条件の Skill | — | `/goal` の条件の先頭の語が `/<プラグイン>:<Skill>` の形で名指しする Skill（例: `/ndf:development-workflow`） | — | — | `docs/specifications/ndf-relay-goal-skill-and-prose-wait.md` |
| 承認待ちの文 | — | `AskUserQuestion` を呼ばずに、応答の本文で利用者の承認・判断を待つと書いた文。判定は `lib/wait_notice.py` の `classify_reply_wait` で、回答待ちか承認待ちに当たる文を指す。待ち通知の `classify_text` より狭く、操作の案内（再起動・インストールなど）は待ちに数えない | — | — | `docs/specifications/ndf-relay-goal-skill-and-prose-wait.md` |
| 承認待ちの差し戻し | — | ラッパーの直接の子の応答が承認待ちの文で終わったとき、Stop hook が Stop を 1 度止め、`AskUserQuestion` で出し直すよう伝えること | — | — | `docs/specifications/ndf-relay-goal-skill-and-prose-wait.md` |
| 既定のログイン | — | 共有の設定ディレクトリ（${CLAUDE_CONFIG_DIR:-~/.claude}。親から引き継いだ CLAUDE_CONFIG_DIR の指定を含む）のログイン。登録済みアカウントでも従量の接続でもなく、relay の区間のアカウントが None のときに子が親から引き継いで使う | — | — | `docs/specifications/ndf-relay-auth-failure.md` |
| 再登録が要る | `needs_relogin` | 登録の記録の needs_relogin が真の登録済みアカウントの状態。アカウントの切り替えの候補から外れ、relay.py account add で登録し直すまで戻らない | — | — | `docs/specifications/ndf-relay-auth-failure.md` |
| tmux の専用サーバー | — | 中継のレーンを動かす tmux サーバー（tmux -L ndf-relay）。利用者の既定の tmux サーバーとは別である | — | — | `docs/specifications/ndf-relay-lane-parent-session-env.md` |
| 親のセッションの環境 | — | 動いている claude のセッションとそのラッパーに固有の環境変数。別のプロセスへ継がれると、そのプロセスを親のセッションの中にいると誤らせる | — | — | `docs/specifications/ndf-relay-lane-parent-session-env.md` |
| アカウントの選定 | — | ラッパーが区間を起動するときに、指定・残量・上限から使うアカウントを決める手順 | — | — | `docs/specifications/ndf-relay-lane-parent-session-env.md` |
| サブスクリプション | — | 利用者が Anthropic と結ぶ Claude の契約（Max・Ultra・Team）。利用上限（5 時間・週）と月の API クレジットを持つ。supervise.py の「プラン」とは別の物 | — | — | `docs/specifications/ndf-relay-api-keys.md` |
| API クレジット | — | サブスクリプションにリンクした Console 組織へ毎月付く API の残高。利用上限と別枠で、未使用分は請求の周期の終わりで消える | — | — | `docs/specifications/ndf-relay-api-keys.md` |
| 登録済み API キー | — | 利用者が `relay.py account` の副命令で預けた Anthropic の API キー。名前と、結び付いた登録済みアカウントを持ち、worker の `claude -p` にだけ使う | — | — | `docs/specifications/ndf-relay-api-keys.md` |
| 残高切れ | — | API キーの要求が "credit balance is too low" で失敗した状態。そのキーはその 1 回の起動の中でだけ候補から外れる | — | — | `docs/specifications/ndf-relay-api-keys.md` |
| API キーの使い方の宣言 | — | 登録済み API キーを worker にいつ使うかを決める 1 つの設定。値は「使わない」「常に使う」「上限に近いとき使う」で、既定は「使わない」 | — | — | `docs/specifications/ndf-relay-api-keys.md` |
| API キーの登録簿 | — | 登録済み API キーと API キーの使い方の宣言を持つ、アカウントの置き場の 1 つのファイル（0600） | — | — | `docs/specifications/ndf-relay-api-keys.md` |
| 起動先の決定 | — | 1 回の起動について、どのアカウントか、どの API キーか、待つかを決めた結果。当たった規則の名前と根拠の値を持つ | — | — | — |
| 5 時間の枠の無いアカウント | — | 使用率の応答に 5 時間の枠が無い登録済みアカウント。`relay.py account list` の 5 時間の列が「-」になる | — | — | — |
| 週の使用率 | — | 週の枠とモデル別の週の枠の使用率のうち最も大きい値 | — | — | — |
| 閾値 T | — | API キーへ逃がすかと、5 時間の枠のあるアカウントを先に選ぶかを決める週の使用率の閾値。既定値は 85% | — | — | — |
| 月の額 | — | API キーごとに宣言する、月に付与されるクレジットの額（ドル） | — | — | — |
| 週の利用枠 | — | API キーごとの、直近 7 日に使ってよい額。月の額 × 7 ÷ 30 | — | — | — |
| 使用の割合 | — | API キーの直近 7 日の使用額 ÷ その API キーの週の利用枠 | — | — | — |
| 区切り | — | relay がもともと起動し直す時点（フェーズの境・文脈の量による切り替え）。戻るためだけに作らない | — | — | — |
| 選ぶ順の組 | — | アカウントを 4 つに分けた選ぶ順の単位。5 時間の枠を先に使う組・週だけの枠の組・閾値を超えた組・残量不明の組の順に選ぶ | — | — | — |
| 観測のスナップショット | — | 1 回の起動先の決定の入力。時刻・宣言・各アカウントの観測・各 API キーの使用額・除外する名前を、決定の時点の値で固めたもの | — | — | — |
| 切り替えの余裕 | — | 今のアカウントが最良の候補と同じ選ぶ順の組にあれば替えない、という切り替えを抑える条件 | — | — | — |

## NDF のリリース（`ndf-release`）

release が走らせるリリースの種別・リリースコマンド・公開操作・リリース記録の語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| リリース記録 | — | release が Pull Request へ残す記録のブロック。段階・版・スプリントの Pull Request に、CD を読んだときは CD の結果を持つ | リリースの記録、配布の記録 | — | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| リリース種別 | `release_type` | リリースの種類。production（本番リリース）/ verification（開発版のデプロイ） | — | — | `docs/specifications/ndf-release-steps-and-token-usage-snapshot.md` |
| 公開操作 | — | release の手順 4 で行う操作。レジストリへの公開・配備先への反映・署名したアーティファクトの設置・ストアへの提出 | 公開の操作 | — | `plugins/ndf/skills/release/references/completion-check.md` |
| リリースの経路 | `release_route` | 変更が本番系へ届く道筋の種類。template（release.form の雛形で組む）/ merge（マージで反映）/ manual（手で反映）/ none（届けない）。release.form があればそれ、無ければ宣言の delivery から決まる。リリースの形とは別の軸 | 配布の経路 | — | — |
| CD | — | 継続的デリバリー（Continuous Delivery）。ブランチへのマージを起点に、環境への反映を NDF の工程の外の仕組みが行うこと。delivery の kind: auto で宣言する | — | — | — |
| CD の案内 | — | CD が無いリポジトリで、工程がデプロイに入ったときに出す案内。CD の設定と方式の記録を促す。承認ゲートではない | — | — | `docs/specifications/ndf-release-via-cd.md` |
| 手動反映の理由 | — | delivery の手動の行に書く、あえて CD を置かない理由。これがある行には CD の案内を出さない | — | — | `docs/specifications/ndf-release-via-cd.md` |
| リリースの Pull Request | — | 本番チャネルへ変更を入れる Pull Request。リリースの雛形では `release/v<版>` が、経路 merge ではベースブランチが head になる | 昇格の Pull Request | — | `docs/specifications/ndf-release-pr-and-merge-route-terms.md` |
| リリースの承認資料 | — | リリース承認ゲートで利用者か MVV 判定が見る承認資料。承認したコミットの欄を持つ。経路 merge では、この欄の SHA がリリースの Pull Request の先端のときだけ本番チャネルへマージする。リリースの雛形では、承認したコミットから比べた先端までに版上げなど許可した差分のほかが無いことを確かめ、比べた先端をマージする | 昇格の承認資料 | — | `docs/specifications/ndf-release-pr-and-merge-route-terms.md` |
| 他のプラグイン | — | リリースの設定の release.plugin 以外で、前のタグからの差分にファイルがあるプラグイン。release-steps.py changed-plugins が列挙する | — | — | `docs/specifications/ndf-release-other-plugin-levels-and-migration.md` |
| 上げ幅 | — | 版数のどの桁を上げるか（MAJOR / MINOR / PATCH）。セマンティックバージョニングに従う | — | — | `docs/specifications/ndf-release-other-plugin-levels-and-migration.md` |
| 移行の手順 | — | 利用者が新しい版へ移るときに自分で行う操作（引数の置き換え・認証のし直し・導入し直しなど）。PR 本文の「移行の手順」の節が正 | — | — | `docs/specifications/ndf-release-other-plugin-levels-and-migration.md` |
| 互換なしの印 | — | 要求の「影響」の表の「公開インタフェース」の行に書く、互換の経路を持たないことを示す語（互換なし / 互換の経路は持たない）。release-steps.py changed-plugins が上げ幅の候補を MAJOR にする材料 | — | — | `docs/specifications/ndf-release-other-plugin-levels-and-migration.md` |
| 承認したコミット | — | リリース承認ゲートの承認資料が対象にした、ベースブランチの先端のコミットの SHA（40 桁）。本番チャネルへ入れてよい中身の上限を示す | — | — | `docs/specifications/ndf-release-approved-commit.md` |
| 比べた先端 | — | リリースが承認したコミットと比べた時点の、本番チャネルへ出すブランチの先端のコミットの SHA（リリースの雛形では `release/v<版>`、経路 merge ではベースブランチ）。本番チャネルへマージしてよいのはこのコミットだけである | — | — | `docs/specifications/ndf-release-approved-commit.md` |
| 確かめたコミット | — | 導入の確認を走らせた、ベースブランチの先頭のコミットの SHA（40 桁）。導入の確認の承認資料に書かれ、pace: fast / auto でリリースの Pull Request をマージしてよい先端はこのコミットに限る | — | — | `docs/specifications/ndf-release-approved-commit.md` |
| 承認の外のコミット | — | 承認したコミットから比べた先端までに入ったコミットのうち、この版のデプロイの PR のものでないもの。リリース対象（`plugins/`・`.claude-plugin/`）に触れるものがあればリリースは承認ゲートで止まる | — | — | `docs/specifications/ndf-release-approved-commit.md` |
| 承認の記録 | — | 承認資料の欄の 1 つ。リリース承認ゲートを承認したときの承認したコミットの SHA と、承認した者・時刻を持つ。承認資料を書き直すと消え、承認し直すまでリリースは通らない | — | — | `docs/specifications/ndf-release-approved-commit.md` |
| 更新情報の節 | — | plugin の README の `## 更新情報` の節。版を名乗らず、CHANGELOG.md の版の節を参照する固定の案内だけを持つ。リリースの手順（bump・changelog・notes）は書かない | — | — | `docs/specifications/ndf-release-readme-update-info.md` |
| 版の節 | — | CHANGELOG.md の `## [<plugin> <基底の版>]` の見出しから次の `## ` までの節。その版の利用者向けの変化と移行の手順の全件の正本 | — | — | `docs/specifications/ndf-release-readme-update-info.md` |
| 範囲の終点 | — | 版の節と承認資料の「含む PR」に並べる PR の範囲の、新しい側の端のコミット。出す版のコミット（リリースのブランチの先頭か、承認に出すコミット）を指す | — | — | — |
| リリースタグ | — | 正式版を出したときに本番チャネルのコミットへ打つタグ `<プラグイン>--v<版>`。リリースタグのある版の節は工程から書き換えない | — | — | — |
| 子の箇条 | — | PR 本文の箇条書きで、前の項目より字下げした箇条。親の項目の一部として読む | — | — | `docs/specifications/ndf-release-readme-update-info.md` |
| リリース後テストの記録 | — | release-verification が Pull Request へ残す、見出し `## リリース後テスト` から `合否:` までのブロック。`対象の版:` の行と課題ごとの表を持つ | — | — | `docs/specifications/ndf-production-release-record.md` |
| 課題の表 | — | リリース後テストの記録のうち、見出しに `課題` と `結果` の両方の列を持つ表。スプリントの課題を閉じる判定とリリースの承認資料が、課題ごとの合否をここから読む | — | — | `docs/specifications/ndf-sprint-close-acceptance-criteria.md` |
| 補足の表 | — | リリース後テストの記録のうち、課題の表でない表（例: 主なテストの実行の表）。課題を閉じる判定にも承認資料にも使わない | — | — | `docs/specifications/ndf-sprint-close-acceptance-criteria.md` |
| 版数を上げないリリース | — | 版数を変えず、既定ブランチへのマージかコミットの反映で本番へ届けるリリース | 版数を上げない配布 | — | `docs/specifications/ndf-production-release-record.md` |
| 範囲の PR | — | 直前の正式版のタグから範囲の終点までに、ベースブランチへ merge commit か squash merge で入った PR と、スプリント経由の PR の和。`--prs` で渡した番号は、マージコミットが範囲の終点の祖先であるものだけを含む。版上げの PR と、PR でない番号（squash の件名の `(#N)` が課題を指すもの）は含まない | — | — | `docs/specifications/ndf-release-approval-included-prs.md` |
| 版上げの PR | — | head のブランチが `release/` で始まる PR。版数と CHANGELOG だけを変え、中身の変更を持たない | — | — | `docs/specifications/ndf-release-approval-included-prs.md` |
| 合格の行 | — | リリース後テストの表で、`結果` の列が `合格` で始まる行 | — | — | `docs/specifications/ndf-sprint-close-acceptance-criteria.md` |
| 手で足した節 | — | 承認資料の `## ` の節のうち、デプロイのステップ（approval-facts・notes --approval・changed-plugins --approval）が書く見出しに当たらないもの。作り直しの後も見出しと中身が残る | — | — | `plugins/ndf/skills/release/references/release-steps.md` |
| 置き換える箇条 | — | 後の PR の本文の `## 置き換える箇条` の節に書く行。同じ版に含む前の PR の「利用者向けの変化」の箇条を指し、その箇条を CHANGELOG と承認資料の「配る中身」から除く | — | — | `plugins/ndf/skills/release/references/release-steps.md` |
| スプリント経由の PR | — | 範囲の PR のうち、merge commit でベースブランチへ入った PR の 2 つ目の親の側（スプリントブランチ）へ merge commit で入った PR。変化と移行の手順はスプリント PR の本文が集めて持つ | — | — | `docs/specifications/ndf-release-approval-included-prs.md` |
| 承認の材料 | — | 承認資料が集める 5 つの材料。レビュー（cross-review のラウンド数と見送った指摘）・リファクタリング（採用と取り消しの件数）・全体テスト（コマンド・合否・件数）・設計文書の未確認・未実施のリリース後テスト | — | — | `docs/specifications/ndf-release-approval-materials.md` |
| マニフェスト | — | plugins/ndf/manifests/<ランタイム>-skills.txt。ランタイムごとに配る Skill の一覧で、デプロイの唯一の基準（#2231） | — | — | `docs/specifications/ndf-distribution-runtimes.md` |
| 利用者の設定ディレクトリ | — | 利用者の Claude Code の設定ディレクトリ（`CLAUDE_CONFIG_DIR`、無ければ `~/.claude`）と Codex の設定ディレクトリ（`CODEX_HOME`、無ければ `~/.codex`）。リリース後テストはこの中の導入済みのプラグインと取得元の登録を変えない | — | — | — |
| 導入の操作 | — | プラグインの導入・更新と、取得元の登録・更新（`claude plugin install` / `update` / `marketplace add` / `marketplace update`、`codex plugin add` / `marketplace add`）。リリース後テストの worker は利用者の設定ディレクトリでこれを打たない | — | — | — |
| 利用者の設定の控え | — | 利用者の設定ディレクトリの、導入済みのプラグインと取得元の登録を鍵と値の組で書き出したもの。リリース後テストの前に取り、後で今の状態と比べる | — | — | — |
| 対象の版 | — | リリース後テストの記録の `対象の版:` の行に書く版。全角の括弧から後ろは注記として読まない。先頭の小文字の `v` の有無は同じ版として扱う | — | — | — |
| 本番の版 | — | リリース記録の `版:` の行の `→` の右に書く、本番へ出した版。全角の括弧から後ろは注記として読まない。読み取った値は先頭の `v` を付けない形にそろえる | — | — | — |
| 版の表記の揺れ | — | 同じ版を指す書き方の違いのうち、リリース記録とリリース後テストの記録の読み取りが吸収するもの。先頭の小文字の `v` の有無だけを指す | — | — | — |
| デプロイ | — | 開発・本番を問わず、環境へ変更を届けること。本番へのデプロイだけをリリースと呼ぶ。工程の名前でもある | 配布 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| 開発版のデプロイ | — | 開発版のチャネルへ版を出すことと、検証の環境への反映。フェーズの名前でもある | 検証リリース、検証への配布、開発版の配布 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| デプロイなし | — | リリース記録の段階の値。どの環境へも届けずに工程を終えたこと | 配布なし | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| リリース対象 | — | リリースで利用者へ届くファイルの集まり。ai-plugins では plugins/ と .claude-plugin/ | 配布物 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| アーティファクト | — | ビルドや署名で作る、公開操作で届ける生成物（パッケージ・署名したバイナリなど） | — | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| 導入先のランタイム | — | マーケットプレイスのプラグインを導入できる形で届けるランタイム。#2231 の後は Claude Code と codex で、相手役のランタイムとは別に決まる（#2231） | 配布先のランタイム | — | `docs/specifications/ndf-distribution-runtimes.md` |
| 利用者向けの変化なし | — | PR の本文の `## 利用者向けの変化` が「無し」（「なし」も同じ）だけを持つこと。承認資料の「含む PR」の行に印を付け、CHANGELOG の版の節と「配る中身」には載せない。節の無い本文（題名で代える）とは別に扱う | 配布物の変化なし | — | `docs/specifications/ndf-release-approval-included-prs.md` |
| リリースの設定 | — | `.ndf/` に書く、そのリポジトリのリリースの宣言（`.ndf/supervise.json` の release と `.ndf/release.json`） | 配布の宣言 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| リリースの形 | — | リリースの設定の release.form が表す、版の出し方の雛形の種類 | 配布の形 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| リリースの手順 | — | 版を上げてから公開するまでの手順。`release` が持つ | 配布の手順 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| 取得元 | — | プラグインを取得するマーケットプレイスの登録が指す先 | 配布元 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| CD の結果 | — | 本番チャネルへのマージの後に NDF が読んだ CD の状態。成功・失敗・未確定の 3 つで、リリース記録に残す | — | — | `docs/specifications/ndf-release-via-cd.md` |
| 結果の読み方 | — | CD の結果を読む手段の宣言。delivery の行の result に書き、GitHub Actions のワークフローの名前かコマンドで指す | — | — | `docs/specifications/ndf-release-via-cd.md` |
| 公開物 | — | 正式版ごとのリリースタグと GitHub Release の組 | — | — | `docs/specifications/ndf-release-via-cd.md` |
| 反映失敗 | — | リリース記録の段階の値の 1 つ。本番チャネルへマージしたが CD の結果が失敗で、スプリントを閉じられない状態 | — | — | `docs/specifications/ndf-release-via-cd.md` |

## NDF の指示書チェック（`ndf-instructions`）

instructions-check.py が見る指示書・スコープ・読み込み量・リリース済み版の語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 指示書 | — | エージェントが読む規約の文書。既定の名前は AGENTS.md / CLAUDE.md / KIRO.md | — | — | `docs/specifications/ndf-instruction-files-check.md` |
| ルート指示書 | — | スコープの根に置かれた指示書。セッションのたびに読まれる | 根の指示書 | — | `docs/specifications/ndf-instruction-files-check.md` |
| サブディレクトリ指示書 | — | サブディレクトリに置かれた指示書。その場所で作業したときに読まれる | 配下の指示書 | — | `docs/specifications/ndf-instruction-files-check.md` |
| @インポート | — | コードの外に書いた @<パス>。参照先の内容がそのまま読み込まれる | 即時読み込みの参照、即時読み込み | — | `docs/specifications/ndf-instruction-files-check.md` |
| リリース済み版 | — | リポジトリがリリース済みとして宣言した版。取り出し方（変更履歴の文書・タグ）は設定が決める | 出た版 | — | `docs/specifications/ndf-instruction-files-check.md` |
| 読み込み量 | — | ルート指示書と、そこから @ でたどった先の合計バイト数 | 読み込みの量 | — | `docs/specifications/ndf-instruction-files-check.md` |
| 指示書スコープ | — | 指示書の置き場所の区分。プロジェクト / ユーザー / プラグイン | — | — | `docs/specifications/ndf-instruction-files-check.md` |

## NDF の課題の棚卸し（`ndf-issue-upkeep`）

backlog-refinement と out-of-scope が課題を分類し、起票するときの語。区分・現象レイヤー・修正レイヤー・起票先

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 区分 | — | backlog-refinement が課題ごとに決める 8 つ（そのまま・追記が要る・書き直しが要る・閉じてよい・やらない・重複・ルートコーズ・要判断） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 現象レイヤー | — | 課題が実際に現れている場所。ファイル・クラス・レイヤーのいずれかで書く | — | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| 修正レイヤー | — | 原因を直すべき場所。その責務を持つべき場所までさかのぼる | — | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| ルートコーズ | — | 修正レイヤーが現象レイヤーと違う課題に付ける区分。現れている場所では直さない | — | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| クラスタ | — | 同じ修正レイヤーを指す課題の集合 | — | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| 親 issue | — | クラスタの修正レイヤーを直すために新しく作る課題 | — | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| 子 issue | — | クラスタに属する既存の課題。親 issue を作った後も閉じない | — | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| 修正方針 | — | 修正レイヤーへの直し方。移動 / 統合 / 新設 / 向きの修正 / 分離 の 5 つ | 採る手 | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| やらない | — | 課題そのものは成り立つが、抱える費用が直す費用を下回ると決める区分の値 | — | — | `plugins/ndf/skills/backlog-refinement/SKILL.md` |
| 再検討条件 | — | 「やらない」で閉じた課題を再び考える条件 | 再燃の条件 | — | `plugins/ndf/skills/backlog-refinement/SKILL.md` |
| 起票先 | — | issue-file.py create が課題を作るリポジトリ | — | — | `plugins/ndf/skills/out-of-scope/references/issue-target.md` |
| 由来 | — | 範囲外の課題を見つけた元。PR #<番号> か issue #<番号> の形で書く。Pull Request がまだ無ければ起点の issue | — | — | `plugins/ndf/skills/out-of-scope/SKILL.md` |
| 本文の骨格 | — | 範囲外の課題の本文が持つ見出し。Issue の記載項目の定義（plugins/ndf/scripts/data/issue-items.json）の項目名で書き、種類ごとの必須の項目・起票者の推定・由来を含む | — | — | `docs/specifications/out-of-scope-issue-file.md` |
| 提示の要約値 | — | 承認資料に載せた内容から作る sha256。issue-file.py の create では起票先・題・本文・ラベル、note では打つ先・番号・1 行から作り、同意の後の --approved に渡して、示した内容と打つ内容が同じことを確かめる | — | — | `docs/specifications/out-of-scope-issue-file.md` |
| 上流リポジトリ | — | NDF の Skill・エージェント・hook の実体を持つリポジトリ | 配布元のリポジトリ | — | `plugins/ndf/skills/out-of-scope/references/issue-target.md` |
| 開発対象リポジトリ | — | NDF を使って開発している側のリポジトリ。gh repo view が返すもの | 開発対象のリポジトリ | — | `plugins/ndf/skills/out-of-scope/references/issue-target.md` |
| 品質の枠 | — | 棚卸しの順位付けで、品質向上の Issue（宣言したラベルの Issue）に先に割り当てる容量の割合。最低の件数を持ち、残りの容量を今の順位で埋める。.ndf/backlog.json の quality 節で宣言したときだけ効く | — | — | `docs/specifications/ndf-workflow-development-loop.md` |
| プロダクトバックログ | — | open の課題を着手の順に並べた全体。マイルストーンの順（上の層）→ マイルストーンの中の順位（下の層）の辞書順で読む | — | — | — |
| スプリントバックログの候補 | — | 1 つのマイルストーンの中の課題を順位で並べたもの。次のスプリントはその先頭から容量の分を切り出す | — | — | — |
| 遅延コスト | — | 課題の着手を遅らせると失う量の相対値。実害の大きさ・時間的重要度・リスク低減の 3 つの見積の段階の和。略して CoD | — | — | — |
| 実害の目安 | — | 実害の大きさの見積の段階の帯を影響範囲の区分で決める表。既定は全体の停止・データの損失・安全機構の欠落が 13〜20、一部の機能の誤動作・手戻りが 5〜8、表示や終了コードの誤り（結果は正しい）が 2〜3、どれにも当たらなければ 1。帯の中の値はアンカー（段階を比べる基準の課題）と比べて決める。宣言で変えられる | — | — | — |
| 退避策の状態 | — | 退避策・回避策が実際に止まるのを防いでいるか（防いでいる / 部分的 / 無い）。防いでいるときだけ実害の大きさとリスク低減を下げる根拠になる。実害の大きさを下げてよいのは実害の目安の区分を 1 つ下げるまでである | — | — | — |
| WSJF | — | 遅延コストを大きさの見積の段階で割った値。人が実施する前提の一般的な形で、順位の値の大きさの指数が 1 のときに当たる | — | — | — |
| 見積の段階 | — | 遅延コストの 3 つの要素と大きさに付ける相対値。宣言の尺度（既定は 1, 2, 3, 5, 8, 13, 20）の中から選ぶ。大きさは AI エージェントが実施したときの費用（所要時間・LLM の費用・変更量）で付け、人の工数では付けない | — | — | — |
| 切り出しの境界 | — | マイルストーンの中の順位を、大きい課題の枠に当てた 1 切れを除いて先頭から、大きさの見積の段階の和が容量の残りに収まるまで取ったときの、最後の課題 | — | — | — |
| 前倒しの候補 | — | 後ろのマイルストーンか未設定にあり、遅延コストが直近のマイルストーンの切り出しの境界の課題より余白以上に高い課題 | — | — | — |
| 後ろ倒しの候補 | — | 直近のマイルストーンのうち、同じマイルストーンの課題が依存していない課題の中で遅延コストが最も低い 1 件。前倒しの候補の最小の遅延コスト以上のものと、順序で次のマイルストーンが無いときは出さない。前倒しの候補と対にして示す | — | — | — |
| 容量 | — | 1 つのスプリントへ切り出す大きさの見積の段階の和の上限。宣言か引数で受け、既定値を持たない | — | — | — |
| 余白 | — | 前倒しの候補にするために、遅延コストが切り出しの境界の課題を上回るべき尺度の段階の数。既定は 1 | — | — | — |
| 自動の閾値 | — | 前倒しを承認なしに反映するために、実害の大きさかリスク低減のどちらかが届くべき段階。既定は 8 | — | — | — |
| バックログの宣言 | — | 尺度・余白・自動の閾値・容量・被依存の対応表・ラベルの下限・大きさの指数・大きい課題の閾値と枠の割合・実害の目安・アンカーを持つ `.ndf/backlog.json` | — | — | — |
| 順位の値 | — | 遅延コストを大きさの見積の段階の α 乗で割った値（α は大きさの指数）。マイルストーンの中の順位を決める | — | — | — |
| 大きさの指数 | — | 順位の値で大きさの見積の段階に掛ける指数 α。0 以上 1 以下で、既定は 0.3（AI エージェントが実施する前提）。1 で WSJF、0 で遅延コストだけになる | — | — | — |
| 大きい課題 | — | 大きさの見積の段階が宣言の閾値（既定は尺度の上から 2 番目。既定の尺度では 13）以上の課題 | — | — | — |
| 大きい課題の枠 | — | 容量のうち枠の割合（既定 0.5）の分を、マイルストーンの中で遅延コストが最大の大きい課題の 1 切れへ先に当てる取り決め。容量が無い回は出さない | — | — | — |
| 分割が要る | — | 大きい課題の枠に当たったが、枠に収まるサブイシューが無く課題そのものも枠を超える状態。報告に出し、自動で分割しない | — | — | — |
| 人の判断待ち | — | 着手の前に人の判断（needs-decision・承認ゲート）を要する状態。費用に数えず、順位の値を変えずに印として出す | — | — | — |
| 候補の上限 | — | 1 回の棚卸しで区分を決める候補の数の上限。candidates の --limit、.ndf/backlog.json の candidate_limit、既定 10 の順に決まる | — | — | `plugins/ndf/skills/backlog-refinement/SKILL.md` |
| 持ち越し | — | 候補の上限を超えたため、その回で区分を決めずに残した候補。candidates の metrics.deferred に番号だけが載る | — | — | `plugins/ndf/skills/backlog-refinement/SKILL.md` |
| 振り返りの範囲 | — | 1 回の振り返りと差分の棚卸しが扱うループの 1 回の並び。まだどの範囲にも入っていない見送ったループの 1 回（深さの記録の `diff` の `not_due` の行）と、今のループの 1 回からなる。ループの外では今のスプリントだけで、見送ったループの 1 回は次に開発ループで走らせる回まで残る。深さの記録の `diff` の `due` の行の `values.scope` に残る（#2914） | — | — | `docs/specifications/ndf-sprint-close-plan-cost.md` |
| 起動の判定 | — | 棚卸しの深さごとに、その回で走らせるかを決める判定。`upkeep.py depth due <深さ>` が終了コード 0（起動する）か 3（起動しない）を返し、深さの記録へ判定の行を残す（#2315・#2914） | — | — | `docs/specifications/ndf-sprint-close-plan-cost.md` |
| 棚卸しの深さ | — | 1 回の棚卸しが扱う範囲。差分の棚卸し・見直し・大整理の 3 つ。`upkeep.py` の `--depth diff\|review\|overhaul`（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 調査の項目 | — | 調査を小さく分けた単位。側面と対象の組（例: `seeds.library:pytest`）。1 項目が 1 つの調査の記録になる（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 付け直しの記録 | — | 大整理が課題の親とマイルストーンを変える前に残す、課題ごとの前の状態。`upkeep.py revert` が読んで戻す（#2315） | — | — | `docs/specifications/ndf-backlog-refinement-depths.md` |
| 深さの記録 | — | 棚卸しの深さごとの起動の判定と実行を 1 行ずつ残す記録。メインディレクトリの `.ndf/backlog/depths.jsonl` で、コミットしない。差分の棚卸しの行は、棚卸しのステップの秒・ドル・言語モデルの名前を持つ（#2315） | — | — | — |
| Issue の種類 | — | 不具合・機能・調査・運用など、記載すべき項目の組を決める Issue の区分 | — | — | `docs/specifications/ndf-issue-items.md` |
| 記載項目 | — | Issue の本文（または課題の置き場の欄）に書く 1 つの事柄。例: 受け入れ条件・再現手順・担当者 | — | — | `docs/specifications/ndf-issue-items.md` |
| 必須の項目 | — | その種類の Issue で、欠けると起票の前の検査で止まる項目 | — | — | `docs/specifications/ndf-issue-items.md` |
| 該当時の項目 | — | 条件に当たるときだけ書く項目。当たる条件を定義に書く | — | — | `docs/specifications/ndf-issue-items.md` |
| 記載の水準 | — | 記載項目に何を書けば満たしたことになるかの基準 | — | — | `docs/specifications/ndf-issue-items.md` |
| 記載項目の定義 | — | 種類ごとの記載項目・区分・記載の水準をまとめた 1 か所の正本 | — | — | `docs/specifications/ndf-issue-items.md` |
| 起票の前の検査 | — | AI が Issue を作る前に、本文が記載項目の定義を満たすかを判定する検査 | — | — | `docs/specifications/ndf-issue-items.md` |
| 起票の後の検査 | — | 作られた open の Issue の本文が記載項目の定義を満たすかを判定する検査 | — | — | `docs/specifications/ndf-issue-items.md` |
| 課題の置き場 | — | Issue を管理するシステム（GitHub・Redmine・リポジトリの中の markdown など）。.ndf/project.json の issues が示す | — | — | `docs/specifications/ndf-issue-items.md` |
| 一致の検査 | — | 記載項目の定義と、フォーム・out-of-scope の骨格・仕様のテンプレートが食い違っていないかを確かめる検査。継続的統合で動く | — | — | `docs/specifications/ndf-issue-items.md` |
| 工程が足す節 | — | 起票の後に工程が Issue の本文へ書き足す節（進行・承認ゲートの答え・閉じた理由・再発など）。起票者は書かない | — | — | `docs/specifications/ndf-issue-items.md` |
| 置き場ごとの表し方 | — | 記載項目を、課題の置き場ごとにどの欄・節・ラベルで表すかの対応 | — | — | `docs/specifications/ndf-issue-items.md` |
| 棚卸しの実行 | — | 棚卸しを 1 回流すまとまり。持ち主を持つ実行は owner claim が返した識別子（run）で区別する | — | — | — |
| 棚卸しの記録の置き場 | — | upkeep.py が記録を置くディレクトリ。$NDF_UPKEEP_STATE_DIR（既定は一時ディレクトリの ndf/backlog-refinement/）の下にリポジトリごとに置き、回をまたぐ記録と、実行の識別子を渡さない棚卸しの回（共有の置き場）と、実行ごとの置き場を持つ | — | — | — |
| 候補の記録 | — | candidates が書く記録（candidates.json）。対象の件数・経路ごとの件数・open の件数を持ち、report と depth record が読む | — | — | — |
| 棚卸しの回 | — | 1 回の candidates から始まり、続く apply・rank・report が読み書きする候補・反映・順位の記録のまとまり。実行の識別子を渡した candidates は、その実行の回を実行ごとの置き場に始める | — | — | — |
| 実行ごとの置き場 | — | 棚卸しの記録の置き場の下に、実行の識別子ごとに置く棚卸しの回のディレクトリ（runs/<run>/） | — | — | — |

## NDF の worktree（`ndf-worktree`）

worktree の設定・セッション開始 hook・テスト環境の割り当ての語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| worktree レジストリ | `registry` | 割り当て・ポート・基準のタグ・公開の記録を持つ JSON（.git/ndf/worktree-registry.json） | — | — | `docs/specifications/ndf-testenv-lock-and-registry.md` |
| 共有設定 | — | .ndf/worktree.json。git で追跡する | 共有の宣言 | — | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| 個人設定 | — | .ndf/worktree.local.json。git で追跡しない | 個人の宣言 | — | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| 実効設定 | — | wt_declaration が返す、共有設定に個人設定を反映した JSON | 重ね合わせた宣言 | — | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| 追従 | — | セッション開始 hook がメインディレクトリの HEAD を git checkout --detach で動かすこと（follow_branch） | — | — | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| セッション開始 hook | — | worktree-session.sh。Claude Code / Codex の SessionStart、Kiro の agentSpawn などで動く | 開始時の hook | — | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| 開発 worktree | — | 人が変更を加えて Pull Request にする worktree。ブランチを持つ | 開発用の worktree | — | `plugins/ndf/skills/worktree/SKILL.md` |
| レビュー worktree | — | cross-review と cross-refactoring が一時的に使う worktree。システムの一時ディレクトリに置く | レビュー用の worktree | — | `plugins/ndf/skills/worktree/SKILL.md` |
| 依存物 | — | パッケージマネージャが入れる、追跡されないディレクトリ（`vendor/`・`node_modules/`・`.venv` など）。worktree には最初から無い | — | — | — |
| 依存の用意 | — | worktree を作った直後、または未用意の既存 worktree を使う前（使い回し・手で打つやり直し）に、宣言に従って依存物を worktree で使える状態にすること。手段（コマンド・参照・複製）は問わない | — | — | — |
| 用意の印 | — | 依存の用意が済んだことを示す、worktree ごとの git ディレクトリの `ndf-deps` ファイル。使い回すときはこれを見てやり直しを省く | — | — | — |
| テスト環境 | — | 1 つの worktree に対応して立ち上げる、テスト実行に必要なサービスの一式。番号で識別され、他の worktree とポート・データ保存先・ネットワークを共有しない | — | — | — |
| テスト環境の用意 | — | 到達の確認が通らないときに、テスト環境を起こして到達を確かめ直すこと | — | — | — |
| テスト環境のスロット | — | テスト環境へ割り当てる 0〜63 の番号。worktree ごとのポートの計算に使う | — | — | `docs/specifications/ndf-testenv-reclaim.md` |
| テスト環境の割り当て | — | worktree とテスト環境の環境名・スロット・ポートの対応。worktree レジストリの 1 行で、解放の時刻が空の間は有効である | — | — | `docs/specifications/ndf-testenv-reclaim.md` |
| 残った割り当て | — | 解放の時刻が空で、worktree のディレクトリが無く、git worktree lock もされていない割り当て | — | — | `docs/specifications/ndf-testenv-reclaim.md` |
| テスト環境の回収 | — | 残った割り当てのテスト環境を compose の定義なしで破棄し、解放の時刻を書くこと | — | — | `docs/specifications/ndf-testenv-reclaim.md` |
| 見えない位置の割り当て | — | worktree のパスがメインディレクトリの下に無く、親のディレクトリが無いか、親のディレクトリがメインディレクトリと別のファイルシステムにある割り当て。回収を打った位置から存在を確かめられないため、残った割り当てとして判定しない | — | — | `docs/specifications/ndf-testenv-reclaim.md` |
| 回収の予約 | — | 環境名ごとのロック。回収が環境名の共有を確かめてから解放を終えるまで持ち、割り当ての追加と使い始めが短く取って通る | — | — | `docs/specifications/ndf-testenv-reclaim.md` |
| 到達の確認 | — | コンテナの中から worktree の探りの印を読み、テストのコンテナが worktree を見ているかを確かめること | — | — | — |
| ツールのパス | `tool_paths` | CLI が起動したツール（MCP サーバーなど）が、利用者の操作なしに worktree の中で書き換える既知のパス。既定（`.serena/project.yml` と `.serena/serena_config.yml`）と `.ndf/worktree.json` の `tool_paths`（`add` / `remove`）で決まる。検査とコミットの対象から外す | — | — | — |
| 残る状態 | — | NDF の実行が作り、その実行が終わった後もディスクに残るもの（レビュー worktree・状態ファイル・一時ファイル・投稿キューの項目）（#2229） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| 状態の持ち主 | — | 残る状態を作った実行。持ち主が生きている間は、別の実行がその状態を消さない（#2229） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| 状態の寿命 | — | 残る状態を片付けてよくなる条件（持ち主のプロセスの終わり・Pull Request が閉じたこと・送り終えたこと など）。状態の種類ごとに 1 つ決める（#2229） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| 以前の実行 | — | 今の実行より前に同じ Pull Request・同じリポジトリで回った、終わった実行。supervise.py の run --from の「前の実行」とは別（#2229） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| 持ち主の記録 | — | 持ち主の鍵ごとに 1 つ、状態の置き場の leases/<リポジトリ>/ に置く記録。実行の識別子・入口・開始・最後に動いた時刻・終わった時刻・持ち主のプロセス（pid・開始時刻・ホスト名）を持つ。片付けが持ち主の生存を判定するときに読む（#2229・#2342） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| 寿命の窓 | — | 持ち主の記録が終わっていなくても、最後に動いた時刻からこの長さを過ぎた持ち主を死んでいるとみなす長さ。持ち主の部品（leases/）の記録では別のホストの記録にだけ使い、同じホストの生死は pid と開始時刻で決める。値は持ち主の契約の window_seconds に置く。レビュー worktree は 24 時間（#2229・#2342）、見直しの持ち主は既定 15 分（#2315） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| 片付け | — | 状態の一覧に従い、持ち主の終わった残る状態を外し、残したものと理由を返す処理（run_state.sweep_states）。git が拒めば止まる操作だけで行う（#2229） | — | — | `docs/specifications/ndf-run-state-owner-and-sweep.md` |
| 持ち主の鍵 | `lease_key` | 持ち主の記録を取り合う単位。入口の契約が、解決した入力から作る形を宣言する（例 cross-refactoring:pr=2301。#2335） | — | — | — |
| 持ち主の契約 | — | 入口の契約の行が持つ lease の宣言。副命令ごとに鍵の形・持ち主の単位・寿命の窓・返す副命令かを決める。見直しの持ち主が実行時に取る記録（持ち主の宣言）とは別の物である（#2342） | — | — | `docs/specifications/ndf-lease-stage-and-concurrent-runs.md` |
| 持ち主の単位 | — | 持ち主の記録を誰が持つか。run は入口の 1 回の実行で、実行の終わりで返す。session はランタイムのセッション（Claude Code・Codex のプロセス）で、返す入口が呼ばれるかセッションが終わるまで持つ（#2342） | — | — | `docs/specifications/ndf-lease-stage-and-concurrent-runs.md` |

## mcp-serena（`mcp-serena`）

mcp-serena が言語サーバーを選び、インストールを確かめるときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| インストールチェック | — | 公式 LSP プラグイン・本体・追加のチェックが揃っているかを見ること（check） | 導入のチェック | — | `docs/specifications/mcp-serena-language-servers.md` |

## playwright-kit のテスト計画（`playwright-kit`）

探索的テストとテスト設計技法の語。page role・oracle・HTSM など業界の定訳をそのまま使う

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| page role | — | ページの機能的な役割（LP / list / item / edit / form など）。URL や SEO の構造とは別 | — | — | `plugins/playwright-kit/skills/playwright-planning/docs/README.md` |
| oracle | — | 不具合だと判定する根拠（仕様・過去版・標準・ユーザーの期待・内部の一貫性など） | — | — | `plugins/playwright-kit/skills/playwright-planning/docs/README.md` |
| FEW HICCUPPS | — | oracle の 11 の軸（Familiarity, Explainability, World, History ほか） | — | — | `plugins/playwright-kit/skills/playwright-planning/docs/README.md` |
| HTSM | — | テスト戦略を Mission / Environment / Product Elements / Quality Criteria の 4 つで組む Heuristic Test Strategy Model | — | — | `plugins/playwright-kit/skills/playwright-planning/docs/README.md` |
| SFDIPOT | — | Product Elements の 7 因子（Structure / Function / Data / Interfaces / Platform / Operations / Time） | — | — | `plugins/playwright-kit/skills/playwright-planning/docs/README.md` |
| EP / BVA | — | 同値分割（Equivalence Partitioning）と境界値分析（Boundary Value Analysis） | — | — | `plugins/playwright-kit/skills/playwright-planning/docs/README.md` |

## このリポジトリの開発の検査（`repo-dev-checks`）

ai-plugins の開発で formatter と静的解析を手元と CI で走らせるときの語。NDF が利用者へ配る振る舞いではない

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 一括の整形 | — | リポジトリの全対象へ formatter を掛け、結果を 1 つのコミットにしたもの。.git-blame-ignore-revs に載せる単位 | — | — | — |
| 検査のコマンド | — | 手元で formatter の確認と静的解析を、CI と同じ版・同じ設定で走らせる 1 つのコマンド | — | — | — |
| 一括の自動修正 | — | リポジトリの全対象へ ruff check --fix を掛け、結果を 1 つのコミットにしたもの。一括の整形と同じく .git-blame-ignore-revs に載せる | — | — | — |
| 抑止 | — | 静的解析の指摘を、行のコメント（# noqa・# shellcheck disable=）か設定の除外で出さなくすること。理由を添える | — | — | — |
| 古いバイトコード | — | Python が、書き換えたソースを、前の内容から作った .pyc で読むこと。.pyc はソースの更新時刻（秒）と大きさだけで新しさを照らすため、同じ秒に同じ大きさで書き換えると見分けられない | — | — | `docs/specifications/cross-refactoring-test-stale-bytecode.md` |
| git を使うテストの土台 | — | cross-refactoring のテストで、git のリポジトリの作業ディレクトリ・pytest の起動口・状態ファイルを用意する補助（crossref_helpers.build_git_flow） | — | — | `docs/specifications/cross-refactoring-test-stale-bytecode.md` |
| CLI の版の一覧 | — | スモークの像へ入れる 4 ランタイムの CLI の版数と取得先と checksum を書いたファイル（tests/runtime-smoke/cli-versions.env）。版を上げるときに書き換える唯一の場所 | — | — | `docs/specifications/runtime-smoke-cli-versions.md` |
| 前提のコマンドの一覧 | `required_commands` | 根の conftest.py が持つ、テストの置き場所（パス、またはパスに :: でつないだファイルの中のクラス）ごとに要る外部コマンドの表。収集した項目に当たるコマンドが PATH に無ければ、読み飛ばしの指定が無い限り収集を失敗させる | — | — | — |
| リポジトリ前提の検査 | — | リリース対象の Skill とスクリプトに ai-plugins にしか無い語が無いかを見る CI のジョブ `skill-repo-assumptions-check` | — | — | `docs/specifications/ndf-repo-assumptions-check.md` |
| 既知の当たり | — | リポジトリ前提の検査に当たるが、NDF 自身の置き場を読むなど違反でない箇所か、行き先の課題が直すまで残す箇所。語とファイルの組ごとに当たりの数を例外リストへ宣言する | — | — | `docs/specifications/ndf-repo-assumptions-check.md` |
| import の輪 | — | NDF のスクリプトのモジュールの import を有向グラフにしたときの強連結成分。最上位の import だけで輪になるものを実行時の輪、関数の中の import などを含めて初めて輪になるものを遅延の輪と呼ぶ | — | — | `docs/specifications/ndf-scripts-import-cycles.md` |
| 一番下の層 | — | NDF のスクリプトのうち、他のモジュールから最も多く読まれる共通ライブラリ。結果を出す層 step_result がこれに当たる | — | — | `docs/specifications/ndf-scripts-import-cycles.md` |
| 終了コードの表 | — | NDF のスクリプトが使う終了コードの値と見出しの一覧。lib/exit_codes.py が持ち、結果を出す層 step_result が同じ名前で公開する | — | — | `docs/specifications/ndf-scripts-import-cycles.md` |

## NDF の学習の設定（`ndf-training-optout`）

LLM のアカウントが入力を学習に使う設定かを確かめ、NDF のセッションの開始で Off にするときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 学習の設定 | — | アカウントの入力を学習に使うかの設定。claude は「Help improve our AI models」（grove_enabled）、codex は ChatGPT の「Improve the model for everyone」（training_allowed）と Codex の環境の学習（codex_training_allowed・codex_training_allowed_v2）。true なら入力を学習に使う | — | — | — |
| 学習の設定の確認 | — | 学習の設定を読み、1 行の JSON で返すこと（check）。書き換えない | — | — | — |
| 学習の設定の書き換え | — | NDF のセッションの開始で、true の学習の設定を false にすること | — | — | — |
| 書き換えの無効化 | — | 利用者が環境変数 NDF_TRAINING_OPTOUT=0 で学習の設定の書き換えを止めること。確認（check）は止めない | — | — | — |
| OAuth でない接続 | — | claude_accounts.FOREIGN_AUTH_ENV の変数（ANTHROPIC_API_KEY・ANTHROPIC_AUTH_TOKEN・CLAUDE_CODE_USE_BEDROCK・CLAUDE_CODE_USE_VERTEX・CLAUDE_CODE_USE_FOUNDRY）のどれかが空でない起動。学習の設定が無く、読みも書きも送らない。ndf-relay の「従量の接続」より範囲が広い | — | — | — |
| ChatGPT でないログイン | — | codex の auth.json の auth_mode が chatgpt でない起動（API キーのログイン）。ChatGPT のアカウントの学習の設定が無く、読みを送らない | — | — | — |

## NDF の Google Workspace の操作（`ndf-google-workspace`）

google-workspace の Skill が Google Workspace（Drive・Docs など）を操作するときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| gws | — | Google Workspace CLI（npm の @googleworkspace/cli）。Google の公式サポート外の CLI で、NDF の Google Workspace の操作はすべてこれで行う | — | — | `docs/specifications/ndf-google-workspace-gws.md` |
| gws の状態 | — | google-workspace の確認のスクリプト（gws-check.py）が返す 4 つの値（authenticated / missing / unauthenticated / uninstallable）。状態ごとに次の手が 1 つに決まる | — | — | `docs/specifications/ndf-google-workspace-gws.md` |

## NDF の工数の見積もり（`ndf-estimate`）

AI エージェントが実施する前提で、作業の A・H・L・C を類似の実績から見積もり、予測と実績を突き合わせるときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| AI の実行量 | — | 作業の調査・設計・実装・検証・修正で、AI エージェントが動いた時間の合計。並列に動いた分は足す（A） | — | — | `docs/specifications/ndf-estimate.md` |
| 人の作業時間 | — | 要件の判断・説明・レビュー・手動の確認・修正への介入に、人が手を動かした時間。判断を待つ時間は含まない（H） | — | — | `docs/specifications/ndf-estimate.md` |
| 完了までの時間 | — | 着手から完了条件に届くまでの経過時間。並列・依存・CI・承認の待ちを含む（L） | — | — | `docs/specifications/ndf-estimate.md` |
| 利用費用 | — | 作業に使った LLM と実行環境の費用（USD）。サブスクリプションの枠の消費は別に記録する（C） | — | — | `docs/specifications/ndf-estimate.md` |
| 実行の構成 | — | 実績を比べる前提になる、モデル・ハーネス・レビューの方式・テストの方式・並列の数の組 | — | — | `docs/specifications/ndf-estimate.md` |
| 類似の実績 | — | 完了条件・6 項目・実行の構成が近い、過去の作業の A・H・L・C の実測 | — | — | `docs/specifications/ndf-estimate.md` |
| 未校正 | — | 類似の実績が無く、想定の手順と既知のテストの所要から出した暫定の見込みであることの印 | — | — | `docs/specifications/ndf-estimate.md` |
| 見積もりの記録 | — | 見積もりの時点の 6 項目・実行の構成・予測した A・H・L・C を残したもの。後で実績と突き合わせる | — | — | `docs/specifications/ndf-estimate.md` |
| 実績の記録 | — | supervise の実行ごとに、始まり・ステップの終わり・実行の終わりを追記する行の列。終わり方（成功・承認ゲート・失敗・取り消し）と秒・USD・実行の構成を持ち、終わりの行の無い実行は、実行中（`step.pid` が生きている）と取り込んだ実行（`run_start` の `imported` が真）を除いて中断として数える | — | — | `docs/specifications/ndf-estimate.md` |
| 着手前の 6 項目 | — | 要件の確定度・必要な情報の取得・変更の波及・検証のしやすさ・環境の準備・手戻りの見込み。それぞれ軽・重・判定できないの 3 値で、加点して時間へ換算せず、類似の実績を選ぶ材料と説明に使う | — | — | `docs/specifications/ndf-estimate.md` |
| 見積もりの完了条件 | — | 見積もる作業がどこまで終われば完了かを、supervise のフェーズの名前（設計・実装・検査・開発版のデプロイ・リリース）で表したもの | — | — | `docs/specifications/ndf-estimate.md` |
| 尺度を超える | — | A の見込みと Size 1 の基準との比が、尺度の最大を超えたときの Size の値。最大へ潰さず、分割を検討する印にする | — | — | `docs/specifications/ndf-estimate.md` |
| Size 1 の基準 | — | Size を A の見込みから丸めるときの単位の秒。宣言が無ければ、実績のある課題のうち A の小さい 4 分の 1 の中央値 | — | — | `docs/specifications/ndf-estimate.md` |

## NDF のスライドの作成（`ndf-slide`）

スライドを生成スクリプトで作り、Google Slides へ変換して確かめるときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| トーン＆マナー | — | スライドの書体・色・文字サイズ・著作権表示・ロゴの値の組。プロジェクトごとに差し替える | — | — | `docs/specifications/ndf-slide-generation.md` |
| ページの枠組み | — | 見出し・罫線・ロゴ・著作権表示・ページ番号の位置を固定した、ページを作る共通の関数（page / box / note / tbl / section） | — | — | `docs/specifications/ndf-slide-generation.md` |
| 生成スクリプト | — | pptx を出力するスクリプト（pptxgenjs の build-deck.js）。スライドの正本になる | — | — | `docs/specifications/ndf-slide-generation.md` |
| 汎用テンプレート | — | 会社色を持たないトーン＆マナーとページの枠組みを持つ、生成スクリプトの出発点 | — | — | `docs/specifications/ndf-slide-generation.md` |
| 構成メモ | — | スライドのページごとの番号・見出し・役割・根拠の表と、構成の考え方を書いた文書 | — | — | `docs/specifications/ndf-slide-generation.md` |
| 台本 | — | ページごとの見出しと読み上げの原稿。配分秒数は見出しに付けてよい（任意）。スピーカーノートへ入れる | — | — | `docs/specifications/ndf-slide-generation.md` |
| トーン＆マナーのファイル | — | プロジェクトのトーン＆マナーの値を持つ JSON（slides/theme.json）。書いた項目だけが汎用テンプレートの既定値を上書きする | — | — | `docs/specifications/ndf-slide-generation.md` |
| 尺 | — | 台本の読み上げにかかる時間。読み上げの字数を 350 字/分で割って出す | — | — | `docs/specifications/ndf-slide-generation.md` |

## NDF の文書の組み直し（`ndf-document`）

書き上げた文書を文書形式と文書の目的に合わせて組み直すときの語。document-rebuild が持つ

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| 文書形式 | — | 文書を読ませる形。リポジトリの Markdown・文書・ページ・スライド・表計算。置き場（Notion・Confluence・Google Drive などのシステム）とは別の軸 | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 文書の目的 | — | 読み手にその文書で何をしてもらうか・何を渡すかの種類（決議依頼・議論・アジェンダ・小説・エッセイ・ブログ・作業レポート・論文など） | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 共通ルール | — | 文書形式と文書の目的に関わらず、組み直した文書が満たす決まり | — | — | `docs/specifications/ndf-document-rebuild.md` |
| キーワード | — | 文書の題・見出しと、本文で繰り返し使い、読み手がその文書の主題として受け取る語 | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 確定事項を伝える文書 | — | 確定した設計・報告書のように、結論が既に決まっている文書 | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 議論用の文書 | — | 仕様案・提案書・論点の整理のように、まだ決まっていないことを読み手と決めるための文書 | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 形式別ガイド | — | 文書形式 1 つについて、文章量の単位と目安・題と見出しの付け方・呼び出し先を持つ document-rebuild の参照ファイル（references/format-<形式>.md） | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 目的別ガイド | — | 文書の目的 1 つについて、構成・文章量・見分ける手がかりを持つ document-rebuild の参照ファイル（references/purpose-<目的>.md） | — | — | `docs/specifications/ndf-document-rebuild.md` |
| 読み手役 | — | 組み直した文書と想定する読み手の説明だけを渡され、会話や経緯の文脈を持たずに読んで、要点の言い直し・分からなかった語と箇所・納得できなかった主張を返す者（サブエージェントか external-ai で呼ぶ別の CLI） | — | — | `plugins/ndf/skills/document-rebuild/SKILL.md` |

## Neural Development Factory（`ndf-factory`）

Neural Development Factory（別のリポジトリのサービス）が NDF を呼ぶときの API・入出力の規約の語。規約は plugins/ndf/contracts/ が持つ

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| Neural Development Factory | — | NDF を Web UI から操作できる Software Factory のサービスとして提供するときの名前。プラグインとしての NDF と区別するときに使う | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| ランナーのエージェント | — | 1 つの実行ジョブの中で利用者のリポジトリを取り込み、NDF を導入して conductor を動かし、NDF の書くファイルを進捗の事象へ翻訳して送るプログラム | — | — | `docs/specifications/ndf-ask-guard.md` |
| 実行ジョブ | — | 実行基盤が 1 回起動するコンテナ 1 つ分の実行。スプリントの開始か承認の答えで始まり、次の承認の依頼・止まった・終わったのどれかで終わる | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 起票の依頼 | — | 実行ジョブの中で NDF が別のリポジトリへの起票を決めたとき、ランナーのエージェントが進捗の事象 issue.requested でサービスへ送る依頼。起票先・題・本文・種類のラベルを持ち、ランナーのエージェントは自分では起票しない | — | — | — |
| 起票の依頼の結果 | — | 起票の依頼 1 件ごとに 1 件残る進捗の事象 issue.request_resolved。サービスが起票した Issue の参照か、下書きへ回した理由を持つ | — | — | — |
| 区間 | — | 1 つの実行ジョブの中でランナーのエージェントが起動する claude -p の 1 回分。conductor の ndf-next で次の区間へ移る | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| リリースの実行 | — | Factory のオーガナイザーが、まとめたリリース 1 つのために起こすスプリントの実行。範囲のどの実行でもなく、承認資料を作り、リリースを流し、結果を返して終わる。同じリポジトリで同時に 1 本まで | 配布の実行 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
