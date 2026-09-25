# 用語集

この文書は `docs/glossary/glossary.json` から `glossary.py render` で作る。手で直さない。

## プロジェクトの用語集（`project-glossary`）

各プロジェクトが持つユビキタス言語。語・意味・コンテキスト・廃止した語・正本

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| ユビキタス言語 | プロジェクトの関係者とエージェントが、要求・設計・コードで同じ意味に使う語の集まり | — | `docs/specifications/ndf-ubiquitous-language.md` |
| 用語集 | ユビキタス言語を持つ構造化ファイル（正）と、そこから作る人が読む Markdown の文書。どちらもプロジェクトのリポジトリに置く | — | `docs/specifications/ndf-ubiquitous-language.md` |
| コンテキスト | 語の意味が 1 つに決まる範囲（境界づけられたコンテキスト） | — | `docs/specifications/ndf-ubiquitous-language.md` |
| ドメインモデルの節 | 設計文書の先頭に置く節。変更が属するコンテキスト・変える集約とその持ち主・不変条件・ドメインイベントを書く | — | `docs/specifications/ndf-ubiquitous-language.md` |
| 廃止した語 | 用語集で別の語へ置き換えた語。文書に出たら落とす | — | `docs/specifications/ndf-ubiquitous-language.md` |
| 不変条件 | 集約がいつも満たす条件。設計のドメインモデルの節に書き、実装の前にテストにする | — | `docs/specifications/ndf-ubiquitous-language.md` |
| 未登録の語 | 用語として書かれているのに用語集に無い語。見出しが「用語」の節の表の 1 列目に書いた語を指す | — | `docs/specifications/ndf-ubiquitous-language.md` |

## NDF の開発ワークフロー（`ndf-workflow`）

NDF が提供する工程・承認ゲート・モード・ステップの語。development-workflow/references/glossary.md が持つ

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 用語集の設定 | `.ndf/glossary.json`。用語集の置き場・形式・チェックの対象を持つ | 用語集の宣言 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 用語チェック | `glossary.py check`。用語集の形と、文書の追加した行の語を見る | 語のチェック | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| モデルレビュー | 設計 PR のレビューの 1 ラウンド目。ドメインモデルの節だけを見る | モデルの段 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 詳細レビュー | 設計 PR のレビューの 2 ラウンド目以降。確定したモデルを前提に残りを見る | 詳細の段 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 仕様のコピー | 課題の本文にある要求を、設計 PR と一緒にコミットする `issues/` のファイル | 仕様の写し | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| パイプライン | キューが `--then` でつないだプランの列（実装 → 検査 → コードレビュー → 開発版 → 本番）。列の 1 つ分がステージ | チェイン | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| セッション | ラッパーが起動する claude の 1 回の起動（1 つの会話）。番号を付けて「セッション 4」と呼ぶ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| プラン | `supervise.py` が流す 1 本の JSON（`plan.json`）。フェーズの手順をステップの列として持つ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| キュー | プランを空いた枠へ順に流す `supervise.py queue`。終わると結果を done へ書く | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリース差分 | 版と版の間（タグからタグまで）の変更 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| インライン実行 | 仕事を渡す実行方式の 1 つ。いまの会話の文脈で行う | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| オーケストレーター | `cross-refactoring` と `cross-review` で、公開・生成物の同期を持ち、担当を回す側。3 層では conductor に当たる | 進行側 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| レッドライン | 当たれば MVV 判定が「従う」でも利用者の承認を求める範囲 | 越えない線 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 実行条件 | プランを流す前に打つコマンド。`skip_code` を返せば worktree を作らずに完了とする | 実行の条件 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 流出不具合 | マージ済みの変更に見つかった不具合。直した Pull Request が触った領域を記録し、トリガーに数える | 逃げた不具合 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 重点領域 | `pace: fast` の領域のうち、触った Pull Request の点数を重くするもの | 共通層 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 承認ゲート | 人手の承認を求める点。設計 Pull Request のマージ（ゲート 1）と本番の系へ届く操作（ゲート 2）の 2 つだけ | 関門 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 承認資料 | 承認を求めるときに示すもの。対象を開くためのものと、承認の判断に使うものの 2 層を持つ | 提示物 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| エビデンス | 承認資料のうち機械で作れる部分と、conductor が確かめて足した事実。MVV 判定へ渡す | 事実の材料 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリース | 変更を利用者へ届く形で公開すること。その工程とフェーズの名前でもある | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリースプラン | リリースの手順をステップの列として持つプラン | 配布の計画 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリースコマンド | リポジトリが `.ndf/release.json` に宣言し、`release` がリリースの段階に合わせて走らせる 1 つのコマンド | 配布のコマンド | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 検証リリース | 開発版と分かる版数での公開か、検証環境への反映 | 検証への配布 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| インストール確認 | 隔離した HOME で ref からプラグインをインストールし、版と中身が ref と一致するかを確かめる | 導入確認 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリース完了の確認 | 公開が済んだことを、リリース先の状態から読み取れる値 | 完了の事実 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スタックしたチェック | 実行が終わったのに pending のまま残った CI のチェック | 取り残されたチェック | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リファクタリング | 振る舞いを変えずに構造を直す工程 | 構造改善 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| コードレビュー | 実装の差分をレビューし、新しい指摘が出なくなるまで直す工程 | 実装レビュー | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リファクタリング計画 | `cross-refactoring` が採る改善項目を決め、見送った提案と理由を残す出力 | 改修計画 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| バッファ | `cross-refactoring` の見積りで、想定最大時間から経過を引いた後に残しておく時間 | 予備時間 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ラウンドテスト | `cross-refactoring` で、`--scope` のテストの置き場所を走らせるコマンド | ラウンドのテスト | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| グレード | `cross-refactoring` が候補ごとに付ける適用の価値（high / medium / low） | 等級 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 指摘ファイル | `cross-review` の担当が書く、指摘の全件と総評のファイル | 指摘のファイル | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 進捗記録 | 工程に入った時点で 1 回打つ記録。課題の本文の「進行」も同じ 1 回で更新される | 進行の記録 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ミッション状態ファイル | ミッションのプラン・done・承認ゲートの記録・MVV・版を持つファイル（`mission.json`） | ミッションの状態 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| シグナルファイル | ラッパーへ知らせるファイル（`next.json` と `stop`） | 合図 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| アナウンス | ndf-next のブロックの直前にそのまま置く 1 文 | 告知 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| アイドル | シグナルファイル・会話の記録・利用者の入力が動かない秒数。この秒数がたつまでラッパーは `/exit` を入力しない | 静止 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| パススルー | ラッパーを挟まず、本物の claude をそのまま起動すること | 素通し | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 進捗ログ | プランの実行中に 1 行 1 つの JSON で追記する記録（`progress.jsonl`） | 途中の報告 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| フェーズレポート | supervisor（またはプラン）が最後に返す報告 | フェーズの報告 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| worktree | 開発の変更を行う git worktree。課題ごとに 1 つ切る | 作業ツリー | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| メインディレクトリ | リポジトリを clone したディレクトリ | 主ディレクトリ | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ベースブランチ | worktree の分岐元と Pull Request の宛先 | 起点のブランチ | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ミッションブランチ | 課題の Pull Request を集め、ベースブランチへの Pull Request をミッションで 1 本にするブランチ | ミッションのブランチ | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 安定版と実験版 | NDF の変更の 2 つの経路（stable / experimental）。実験版の置き場は `experimental/` | 安定と試行 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 即時修正 | `pace: fast` で、200 行以内の不具合を起票せず、その場のプランで直すこと | その場で直す | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 棚卸し | 既存の課題の本文・マイルストーン・ラベルを現状に合わせること | 手入れ | `plugins/ndf/skills/development-workflow/references/glossary.md` |

## NDF の Slack 通知（`ndf-notification`）

利用者の手を待つ時点・その種類・通知の本文と復帰先

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 待ち通知 | 利用者の回答か承認が無いと進まない時点で、Slack へ送る知らせ | 待ちの通知 | — |
| 回答待ち | 利用者に問いへの答え（選択・情報・指示）を求めている待ち | — | — |
| 承認待ち | 利用者に操作の許可（ツールの実行・プラン・マージ・リリースなど）を求めている待ち | — | — |
| 待ちのキー | 1 つの待ちを見分ける値。Claude Code では transcript の最後の user の項目の `uuid`（利用者が答える・許可すると変わる） | 待ちの鍵 | — |
| 復帰先 | 通知から当該セッションへ復帰する手段。ホスト名・cwd の行と、作れればセッションの URL か再開のコマンド | 戻り先 | — |
