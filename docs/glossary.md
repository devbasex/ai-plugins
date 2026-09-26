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
| オーケストレーター | `cross-refactoring` と `cross-review` で、公開・生成物の同期を持ち、担当を回す側。3 層では conductor に当たる | 進行側、レビューを回す側 | `plugins/ndf/skills/development-workflow/references/glossary.md` |
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
| 進捗記録 | 工程に入った時点で 1 回打つ記録。課題の本文の「進行」も同じ 1 回で更新される | 進行の記録、記録のコマンド | `plugins/ndf/skills/development-workflow/references/glossary.md` |
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
| 文言固定テスト | リポジトリで追跡している .md を読み、その文字列・見出し・表の並びを照合するテスト。書かない | — | `docs/specifications/cross-refactoring-round-tests-and-assess.md` |
| 手順 | 1 つの Skill の中で順に通す作業の単位。cross-refactoring の提案・リファクタリング計画・テスト追加・実装・検証/修正の 5 つ、document-restructuring の測る・並べ替える・整える・測り直すの 4 つ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 範囲テスト | 変更が触った範囲に限って走らせるテスト | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 危険フラグ | cross-refactoring で、範囲テストでは覆えない変更（D1〜D5）。立てば全体テストを 1 度走らせる | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 設計 Pull Request | 要求仕様と設計文書だけを載せ、実装を含まない Pull Request。変更ファイルに issues/ の要求・設計・決定の記録を含む | — | `docs/specifications/ndf-design-phase.md` |
| 正本 | その事柄の定義を持つ唯一の文書。食い違ったときはこれを正とする | — | `docs/specifications/doc-consistency-checks.md` |
| conductor | 人間と対話しているセッション。ミッションを持ち、承認ゲートで人間へ問えるのはこの層だけ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| supervisor | 1 つのフェーズを通すサブエージェント。人間へ問わない | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| worker | 1 つの作業（調査・修正・検証・集計）を行うサブエージェント。別のサブエージェントを起動しない | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| フェーズ | supervisor 1 つ（またはプラン 1 本）が通す、連続する工程のグループ。設計・実装・検査・取り込み・仕上げ・リリースの 6 つ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 作業種別 | worker 1 つが行う作業の分類。調査 / 修正 / 検証 / 集計、どれにも当たらなければその他 | 作業の種類 | `docs/specifications/ndf-agent-layers-unattended-run.md` |
| context window | 1 回の会話が保持する文脈の全体と、その量 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| レートリミット中断 | 利用上限（429）で層が途中で終わること。記録の ending が rate_limit | 上限の中断 | `docs/specifications/ndf-context-window-metrics.md` |
| リセット時刻 | 利用上限が解ける時刻（quotaLimits.resetsAt、resets_at） | 解除時刻 | `docs/specifications/ndf-context-window-metrics.md` |
| 自動継続 | リセット時刻に Claude Code が conductor へ積む入力（origin.kind が auto-continuation） | 自動の継続 | `docs/specifications/ndf-agent-layers-unattended-run.md` |
| 親エージェント | その記録を起動したエージェント。.meta.json の toolUseId でたどる（parent_agent_id） | 起動元 | `docs/specifications/ndf-context-window-metrics.md` |
| 実行前確認 | Skill の手順の途中で、操作の対象を示して利用者の同意を得ること。承認ゲートとは別 | — | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| 取り消せる操作 | 失う状態を git 自身が拒むか、事後の手段（ハッシュからの復元・Restore branch・reopen）で元へ戻せる操作。実行前確認なしで進める | — | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| ミッション | 1 つの版として出す課題と Pull Request のセット。工程はミッション単位で 1 回ずつ通し、モードもミッションで 1 つにする | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ミッション課題 | ミッションに含まれる Pull Request の本文が、閉じる語で指す課題 | ミッションの課題 | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| 最終工程 | その実行で最後に通る工程。振り返りを通るなら retrospective、通らずリリース後テストを通るなら release-verification | 終わりの工程 | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| ピーク使用量 | 応答ごとの入力トークンの合計の最大 | 最大充填 | `docs/specifications/ndf-context-window-metrics.md` |
| 合成応答 | API を呼ばずに Claude Code が記録へ書いた応答（message.model が <synthetic>） | 合成の応答 | `docs/specifications/ndf-context-window-metrics.md` |
| 設計文書 | design が作る成果物。issues/ 配下に置く Markdown | — | `docs/specifications/ndf-design-phase.md` |
| 企画承認 | documentation モードのゲート 1。構成案と体裁設計を載せた設計 Pull Request のマージ | — | `docs/specifications/ndf-documentation-mode.md` |
| 制作物承認 | documentation モードのゲート 2。production が真の提出先への操作 | — | `docs/specifications/ndf-documentation-mode.md` |
| 下書き先 | production が偽の提出先。承認の前に書き込んでよい唯一の場所 | — | `docs/specifications/ndf-documentation-mode.md` |
| 実行計画 | ミッションの課題を、バンドル・工程ごとの依存・触る箇所・着手できる時点で並べた表。オーケストレーターが持つ | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| バンドル | 1 本の設計 Pull Request で決める課題の集合 | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 課題グループ | マイルストーンの説明に書く、触る場所の見込みと依存で分けた課題の集合。実行計画のバンドルの初期値 | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 変更重複 | 2 つの Pull Request が同じファイルを触ること。節（見出し）・関数の単位で程度を分ける | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 並行度 | 対象の Pull Request のうち、2 本以上が同時に開いていた時間の割合 | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 予備メモリ | オーケストレーターと同じ VM に常駐する他のプロセスの変動のために空けておくメモリ | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 設定 | リポジトリ側に置く .ndf/<名前>.json。無ければその機能は既定の動きだけになるか、何も動かない。「<対象>の設定」の形で呼ぶ（指示書チェックの設定・リリースの設定・ボードの設定・worktree の設定・用語集の設定）。git で追跡するものを共有設定、追跡しないものを個人設定と呼ぶ | — | `docs/specifications/ndf-instruction-files-check.md` |
| コピー | 元のファイルをそのまま別の場所へ置いたもの。ラッパーの relay.py（版は横の relay.version。新しい版を古い版で置き直さない）・マイルストーンの説明から作る mvv.md・承認資料の issues/approval-*.md | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| カットポイント | context window を切ってよい 4 点。3 層ではフェーズの境になる | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ラッパー | 利用者の claude を包んで常駐し、ndf-next のブロックを拾って /exit・プラグインの更新・次のセッションの起動を行う（Claude Code だけ） | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ポーリング | 待つ間に、状態を確かめる呼び出しを繰り返すこと | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| フォアグラウンド Bash | run_in_background を付けずに実行する Bash。終わるまで呼び出しが返らない | 前景の Bash | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| コンテキスト量 | 1 回の API 呼び出しで読んだトークン数（input_tokens + cache_read_input_tokens + cache_creation_input_tokens） | 文脈量 | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 工程 Skill | development-workflow の工程表が起動する Skill | 工程の Skill | `docs/specifications/ndf-worker-agent-and-skill-excerpts.md` |
| 中間通知 | 背景の処理を残したまま応答を終えたサブエージェントについて、親へ届く 1 回目の通知 | 途中の通知 | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 報告コピー | worker が起動指示の置き場所のファイルの末尾へ書く作業の報告の節。最後の応答の報告と同じ中身 | 報告のコピー | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 完了マーカー | worker が報告コピーを書き終えた後に作る空のファイル（<置き場所>.done） | 完了の目印 | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 横断 Skill | 工程表に載らず、どの工程からも呼ばれる Skill（progress-tracking / out-of-scope など） | 工程の外の Skill | `docs/specifications/ndf-worker-agent-and-skill-excerpts.md` |
| 抜粋 | 呼ぶ側が要る部分だけを Skill の本文から取り出したもの | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 実行方式 | 仕事を渡す先の実行の形。インライン実行 / サブエージェント / CLI 実行 / 最小構成の claude -p / スクリプト | — | `docs/specifications/ndf-worker-agent-and-skill-excerpts.md` |
| 固定費 | 作業を始める前に context window が既に埋まっている分（指示・規約・Tool の定義）。実行を 1 つ起こすたびに掛かる | — | `plugins/ndf/skills/development-workflow/references/context-window.md` |
| スケルトン | SKILL.md の実行の節に置く bash。利用者はこれをコピーして起動する | 骨組み | `docs/specifications/ndf-workflow-blockers.md` |
| 通過記録 | 通過工程を課題ごとに残したファイル | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 本番チャネル | リリースした版が常用する利用者へ届くブランチ | 本番のチャネル | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| 本番系 | 利用者が現に使っているリリースのチャネル・環境・外部サービス。チャネルは本番系の一種 | 本番の系 | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| 必須ルール | 分割と並行の手法を担当へ任せたうえで、それでも守る規則 | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| プラグインルート | リリースした先で scripts/ と skills/ が並ぶディレクトリ | — | `plugins/ndf/scripts/lib/README.md` |
| 収束ループ | 新しい指摘が出なくなるまで回すレビューと修正。リファクタリング・コードレビュー・ドキュメントレビューが持つ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 委譲 | 別の文脈（サブエージェント・別の CLI プロセス）へ作業を渡し、結果だけを受け取ること | — | `plugins/ndf/skills/development-workflow/references/context-window.md` |
| 実作業 | ピーク使用量から固定費を引いた分。その会話が実際に読み書きした量（work） | — | `plugins/ndf/skills/development-workflow/references/context-window.md` |
| ボード | 進行を記録する GitHub Projects のプロジェクト 1 つ。設定が無ければ何も動かない | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 通過工程 | ある課題について、進捗記録が実際に書かれた工程の集合 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 承認ラベル | 人間が設計を承認したことを表す Pull Request のラベル。無ければ hook が設計 Pull Request のマージを拒む | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| マイルストーン | 着手の順序を表す単位。ミッションはこの中から切り出す | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 範囲外の課題 | この変更の受け入れ条件にも直す対象にも入らない課題。見つけたその場で issue にする | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 再開コマンド | 新しい会話の最初に入力すれば、その工程から再開できるコマンド。ndf-next のブロックの中身 | 再開用のコマンド、引継ぎの 1 行 | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 工程 | 工程表（モードごとに起動する Skill の表）の 1 行。要求と受け入れ条件・設計・実装・リリースなど | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ステップ | プランの steps の 1 要素。型は run / work / drive / judge / pr の 5 つ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ステージ | パイプラインの中のプランのグループ 1 つ。中はキューで並列に流し、前のステージがすべて完了したときだけ次のステージが流れる。new mission はステージごとにプランを書き出す（設計・ゲート 1・実装・検査・開発版・本番） | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スイッチポイント | フェーズの中で supervisor を替える点。収束ループの前で hook が決める | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 3 層 | 工程を conductor → supervisor → worker の順に起動して通す形 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 最小構成の `claude -p` | Tool と指示を絞った 1 回の判断。judge のステップと MVV 判定が使う | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 実行レベル | 仕事を渡す実行方式の LLM の使い方の水準。レベル 1 = スクリプト、レベル 2 = 分類の判断、レベル 3 = インライン実行の LLM | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| モード | 変更の目的物で決める工程の振り分け。上から operation / documentation / standard / legacy-refactor / light | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| pace | モードとは別の軸で、工程をどう通すかを決める。既定の normal と、承認ゲートと検査の時機を変える fast | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| MVV | ミッションの Mission / Vision / Value。マイルストーンの説明からコピーし、利用者が 1 回承認する。fast でゲート 1・2 の事前の許可になる | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| MVV 判定 | 承認ゲートのエビデンスが MVV に従うかの判定。「従う」でレッドラインが無いときだけ承認ゲートを省き、記録を残す | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| トリガー | fast でリファクタリングとコードレビューを流す条件。点数・行数・流出不具合・経過時間・最終の 5 つ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| judge のステップ | 結果ファイルと規則の抜粋だけを渡し、次のステップを LLM に決めさせるステップ。Tool を持たない | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 決定 | judge のステップが返す、次に取る手 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ゲート 1 | 設計 Pull Request のマージ。文書では企画承認に当たる | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ゲート 2 | 本番系へ届く操作（本番へのリリースと operation の実行）。文書では制作物承認に当たる | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 開発版 | ベースブランチ（develop）に載るチャネルと、そこへ出す接尾辞付きの版。マージされた変更がそのまま載る | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 本番 | 利用者が現に使っているチャネル・環境・外部サービス（本番系）。プラグインのリリースでは本番のブランチ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 正式版 | 本番チャネルへ出す、接尾辞の無い版。出したらリリースタグを打つ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 検査 | リファクタリング・コードレビュー・完了判定・Pull Request を通すフェーズ。fast ではトリガーが立ったときだけ、前回の検査からの差分に流す。コードレビューだけは開発版ごとに流す（--review-only） | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| チェック | 機械が合否を返すもの。CI のジョブと、mvv-gate.py・doc-lint.py などのスクリプト | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 完了判定 | コマンドの証跡で完了を判定する工程 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 全体テスト | リポジトリ全体を範囲にするテスト | 全体のテスト | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| コメントのスナップショット | cross-review が取る既存コメントの一覧。2 ラウンド目以降は取り直す | 既存コメントのスナップショット | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| doc-lint | 追加した Markdown の行に、検討の痕跡・課題番号の由来・比較の語が無いかを見るチェック | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 引継ぎ文書 | 会話を切って再開するための文書。「今の会話の進み」（プランごとの行の表）と「次に実行するコマンド」の節をスクリプトが書く | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ndf-next | 次のセッションの最初の入力を置く、情報文字列 ndf-next の囲みのコードブロック。最後の応答に 1 つだけ置く | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| step / alive / worker / attention | 進捗ログの行の種類。ステップの切り替わり・動きの無い間の生存・worker の進み・conductor の判断が要る出来事（止まった・承認ゲート・同じ失敗の繰り返し・judge のステップで stop が出そう） | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| done | キューが終わったときに書く結果の JSON。wait は done か attention の行まで待つ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 結果 JSON | 手順のスクリプトが返す 1 行の JSON。status で読む | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 実装計画 | implementation-plan が issues/ に書く、実装の前の計画 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 確定仕様化 | 完了した実装計画を docs/ の確定仕様へ書き直す工程 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 振り返り | 進め方で変えることを記録し、起票の取りこぼしを拾う工程 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |

## NDF の Slack 通知（`ndf-notification`）

利用者の手を待つ時点・その種類・通知の本文と復帰先

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 待ち通知 | 利用者の回答か承認が無いと進まない時点で、Slack へ送る知らせ | 待ちの通知 | — |
| 回答待ち | 利用者に問いへの答え（選択・情報・指示）を求めている待ち | — | — |
| 承認待ち | 利用者に操作の許可（ツールの実行・プラン・マージ・リリースなど）を求めている待ち | — | — |
| 待ちのキー | 1 つの待ちを見分ける値。Claude Code では transcript の最後の user の項目の `uuid`（利用者が答える・許可すると変わる） | 待ちの鍵 | — |
| 復帰先 | 通知から当該セッションへ復帰する手段。ホスト名・cwd の行と、作れればセッションの URL か再開のコマンド | 戻り先 | — |

## NDF の外部 CLI 委譲（`ndf-agent-cli`）

cross-review / cross-refactoring が参加者の CLI を選び、起動し、監視し、結果を読むときの語。ランタイム・ホスト・参加者プール・起動結果

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 起動結果 | 担当 CLI の起動 1 回の終わり方。監視の状態と理由、結果ファイルの有無と読めるかを合わせて持つ（LaunchOutcome） | 結末 | `docs/specifications/cross-review-launch-outcome.md` |
| リトライ可否 | 同じ担当を同じ条件で起動し直せば解ける起動結果か（relaunch_same_agent） | 起動し直しの可否 | `docs/specifications/cross-review-launch-outcome.md` |
| 結果ファイル | 担当 CLI が書く判定の要約の JSON（<stem>-result.json） | — | `docs/specifications/cross-review-launch-outcome.md` |
| 監視結果ファイル | 監視が起動 1 回ごとに状態と理由を書く JSON（<stem>-monitor.json） | 監視の結果ファイル | `docs/specifications/cross-review-launch-outcome.md` |
| 監視ログ | 起動結果を追記だけで積む記録（monitor-outcomes.jsonl） | 監視の記録 | `docs/specifications/cross-review-launch-outcome.md` |
| ランタイム | エージェントの CLI の種類。claude / codex / agy / kiro の 4 つで、並びは固定（ALL_RUNTIMES） | — | `docs/specifications/cross-review-participants-and-seats.md` |
| ホスト | 収束ループを起動している CLI のランタイム（host） | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 参加者プール | Skill ごとに決まる参加者の出発点。cross-review は claude / codex / kiro とホスト、cross-refactoring は codex / kiro とホスト（review_pool / refactor_pool） | 参加の母集合 | `docs/specifications/cross-review-participants-and-seats.md` |
| 参加者 | 参加者プールに --include の者を加え、--exclude の者を除いた一覧。認証確認の対象 | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 利用可能な参加者 | 参加者のうち認証確認を通った者（participants.available） | 使える者 | `docs/specifications/cross-review-participants-and-seats.md` |
| 認証確認 | 確認コマンドを走らせ、止めずに結果だけを返す参加者ごとの確認（probe_auth） | 認証の確認 | `docs/specifications/cross-review-participants-and-seats.md` |
| スロット | 1 ラウンドで 1 つの CLI プロセスが占める枠 | — | `docs/specifications/cross-review-participants-and-seats.md` |
| フォールバック | 利用可能な参加者が 2 者に満たないとき、足りない分を埋める参加者（participants.fallback） | 埋め合わせ | `docs/specifications/cross-review-participants-and-seats.md` |

## NDF の cross-review（`ndf-cross-review`）

Pull Request のレビューを CLI へ委譲し、新しい指摘が出なくなるまで回す収束ループの語。指摘・反証・総評・修正担当

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 指摘 | 担当が出した 1 件の主張（review_findings[] の 1 要素） | — | `docs/specifications/cross-review-evidence-based.md` |
| 反証条件 | 何が成り立てばその指摘を棄却できるか（falsification） | — | `docs/specifications/cross-review-evidence-based.md` |
| 検証手順 | 指摘を実行できる形で確かめる手順（suggested_check） | — | `docs/specifications/cross-review-evidence-based.md` |
| 反証 | 提案者以外の担当が、各指摘へ返す 1 つの値 | — | `docs/specifications/cross-review-evidence-based.md` |
| 指摘区分 | 1 件の指摘を分ける 6 つの分類（classification） | — | `docs/specifications/cross-review-evidence-based.md` |
| 集約方式 | 効果の測定で指摘を採る規則。single / majority / proposed / oracle の 4 つ | — | `docs/specifications/cross-review-evidence-based.md` |
| 代表指摘 | 統合した組で判定が読む 1 件。統合された側は merged_into を持つ | — | `docs/specifications/cross-review-evidence-based.md` |
| 新しい指摘 | 直前のラウンドの指摘と一致しない、そのラウンドの指摘。収束ループはこれが 0 件になるまで回す | — | `docs/specifications/cross-review-round-inputs.md` |
| 修正担当 | 指摘を直してコミットするサブエージェント（/ndf:fix を実行する） | 修正の担当 | `docs/specifications/cross-review-writes-to-conductor.md` |
| 投稿キュー | 送る前に投稿を積み、上限で送れなければ残す仕組み（lib/post_queue.py） | 投稿の待ち行列 | `docs/specifications/cross-review-writes-to-conductor.md` |
| 重複投稿 | 送ろうとした投稿と同じものとして、すでに Pull Request にある投稿 | 先客 | `docs/specifications/cross-review-writes-to-conductor.md` |
| 総評 | レビュー本体に書く文章（body）。インラインのコメントとは別に置く | — | `docs/specifications/cross-review-writes-to-conductor.md` |

## NDF の cross-refactoring（`ndf-cross-refactoring`）

想定最大時間に収まるリファクタリング計画を立てて通す語。改善候補・改善項目・配分テーブル・最終ゲート

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| マージ処理 | 担当の結果やコミットをオーケストレーターが受け入れるコマンド（merge-proposals / merge-plan / merge-tests / merge-implement / merge-fix / merge-final-fix）。フェーズの「取り込み」とは別 | — | `docs/specifications/cross-refactoring-apply-intake.md` |
| 最終ゲート | 全体テストか CI で合否を判定する、cross-refactoring の最後の手順（final-gate）。承認ゲートとは別 | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 最終ゲート修正 | 最終ゲートの失敗を直す起動と、そのマージ処理（final-fix / merge-final-fix）。改善項目に属さない | 最終ゲートの修正 | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 必須トレーラー | 担当がコミットメッセージのトレーラーとして書く Item-Id / Impl-Runtime / Impl-Model。最終ゲート修正は Item-Id を除く 2 つ | 必須の記名 | `docs/specifications/cross-refactoring-apply-intake.md` |
| 帰属トレーラー | 実行環境がコミットメッセージの末尾へ足すトレーラー（Co-Authored-By: / Claude-Session:） | 帰属の段落 | `docs/specifications/cross-refactoring-apply-intake.md` |
| プロダクションコード | コードの拡張子を持ち、テストの置き場所でないファイル（production_code_changes） | 本番コード | `docs/specifications/cross-refactoring-round-tests-and-assess.md` |
| 想定最大時間 | 利用者が与える所要の上限（分、--budget-minutes）。リファクタリング計画はこの中に収まるように立てる | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 実装担当 | リファクタリング計画・テスト追加・実装・修正・最終ゲート修正を通して担う 1 ランタイム（--implementer） | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 改善候補 | 提案を path + symbol + smell のキーで集約したもの。リファクタリング計画へ渡す（candidates[]） | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 改善項目 | リファクタリング計画が採った改善候補。I-001 の形の ID を持ち、1 改善項目 = 1 コミット（テストを足す項目は 2 コミット） | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 配分テーブル | 種類ごとの 1 件あたりの所要（分）。履歴の直近からリファクタリング計画のたびに集計し、保存しない（plan.table） | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 着手期限 | 実装・テスト追加でその改善項目に着手してよい最後の時刻（start_deadline） | 着手の締め切り | `docs/specifications/cross-refactoring-time-budget.md` |
| 完了期限 | 着手期限にその改善項目の見積りを足した時刻。マージ処理はコミットの時刻をこれと比べる | 完了の締め切り | `docs/specifications/cross-refactoring-time-budget.md` |
| フレーキー / 既存失敗 / 変更起因 | 危険フラグで走らせた全体テストが落ちたときの 3 つの分類（whole_test.flaky / preexisting / caused） | 元からの失敗 | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |

## NDF のラッパー（`ndf-relay`）

ラッパーがセッションを切り替え、シェルの設定へ組み込まれるときの語

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 管理ブロック | シェルの設定の # >>> ndf relay >>> から # <<< ndf relay <<< までの行 | — | `docs/specifications/ndf-relay-install-and-restart.md` |
| 自動追加ブロック | 10.17.4〜10.17.6 の SessionStart hook が足した管理ブロック。状態ディレクトリの rc-added に載り、rc-user に載らない | 自動の囲み | `docs/specifications/ndf-relay-install-and-restart.md` |
| 状態ディレクトリ | ${XDG_STATE_HOME:-~/.local/state}/ndf/relay/。記録（rc-added など）と作業ディレクトリを持つ | 状態の親 | `docs/specifications/ndf-relay-install-and-restart.md` |
| 質問シグナルファイル | 作業ディレクトリの question。質問が表示されているあいだ在る | 質問のシグナルファイル | `docs/specifications/ndf-relay-install-and-restart.md` |
| 入力注入 | ラッパーが子の擬似端末へ /exit 以外の入力を書くこと | 送り込み | `docs/specifications/ndf-relay-install-and-restart.md` |
| 起動オプション | 最初のセッションの claude の引数のうち、セッションごとに変わらないもの（--dangerously-skip-permissions など） | 起動の方針の引数 | `docs/specifications/ndf-relay-install-and-restart.md` |
| 停止シグナルファイル | ラッパーに次のセッションを起動させないために置く空のファイル（stop） | 停止のシグナルファイル | `docs/specifications/ndf-relay-segment-restart.md` |
| 再起動ループ | シグナルファイルを書いて終わったセッションが短い時間で続き、進まずに起動だけが重なる状態。ラッパーは次のセッションを起動しない | — | `docs/specifications/ndf-relay-segment-restart.md` |

## NDF のリリース（`ndf-release`）

release が走らせるリリースの種別・リリースコマンド・公開操作・リリース記録の語

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| リリース記録 | release が Pull Request へ残す記録のブロック。段階・版・ミッションの Pull Request を持つ | リリースの記録 | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| リリース種別 | リリースの種類。production（本番リリース）/ verification（検証リリース） | — | `docs/specifications/ndf-release-steps-and-token-usage-snapshot.md` |
| 公開操作 | release の手順 4 で行う操作。レジストリへの公開・配備先への反映・署名した配布物の設置・ストアへの提出 | 公開の操作 | `plugins/ndf/skills/release/references/completion-check.md` |

## NDF の指示書チェック（`ndf-instructions`）

instructions-check.py が見る指示書・スコープ・読み込み量・リリース済み版の語

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 指示書 | エージェントが読む規約の文書。既定の名前は AGENTS.md / CLAUDE.md / KIRO.md | — | `docs/specifications/ndf-instruction-files-check.md` |
| ルート指示書 | スコープの根に置かれた指示書。セッションのたびに読まれる | 根の指示書 | `docs/specifications/ndf-instruction-files-check.md` |
| サブディレクトリ指示書 | サブディレクトリに置かれた指示書。その場所で作業したときに読まれる | 配下の指示書 | `docs/specifications/ndf-instruction-files-check.md` |
| @インポート | コードの外に書いた @<パス>。参照先の内容がそのまま読み込まれる | 即時読み込みの参照、即時読み込み | `docs/specifications/ndf-instruction-files-check.md` |
| リリース済み版 | リポジトリがリリース済みとして宣言した版。取り出し方（変更履歴の文書・タグ）は設定が決める | 出た版 | `docs/specifications/ndf-instruction-files-check.md` |
| 読み込み量 | ルート指示書と、そこから @ でたどった先の合計バイト数 | 読み込みの量 | `docs/specifications/ndf-instruction-files-check.md` |
| 指示書スコープ | 指示書の置き場所の区分。プロジェクト / ユーザー / プラグイン | — | `docs/specifications/ndf-instruction-files-check.md` |

## NDF の課題の棚卸し（`ndf-issue-upkeep`）

issue-upkeep と out-of-scope が課題を分類し、起票するときの語。区分・現象レイヤー・修正レイヤー・起票先

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| 区分 | issue-upkeep が課題ごとに決める 8 つ（そのまま・追記が要る・書き直しが要る・閉じてよい・やらない・重複・ルートコーズ・要判断） | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 現象レイヤー | 課題が実際に現れている場所。ファイル・クラス・レイヤーのいずれかで書く | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| 修正レイヤー | 原因を直すべき場所。その責務を持つべき場所までさかのぼる | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| ルートコーズ | 修正レイヤーが現象レイヤーと違う課題に付ける区分。現れている場所では直さない | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| クラスタ | 同じ修正レイヤーを指す課題の集合 | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| 親 issue | クラスタの修正レイヤーを直すために新しく作る課題 | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| 子 issue | クラスタに属する既存の課題。親 issue を作った後も閉じない | — | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| 修正方針 | 修正レイヤーへの直し方。移動 / 統合 / 新設 / 向きの修正 / 分離 の 5 つ | 採る手 | `docs/specifications/ndf-issue-upkeep-root-cause.md` |
| やらない | 課題そのものは成り立つが、抱える費用が直す費用を下回ると決める区分の値 | — | `plugins/ndf/skills/issue-upkeep/SKILL.md` |
| 再検討条件 | 「やらない」で閉じた課題を再び考える条件 | 再燃の条件 | `plugins/ndf/skills/issue-upkeep/SKILL.md` |
| 起票先 | gh issue create が issue を作るリポジトリ | — | `plugins/ndf/skills/out-of-scope/references/issue-target.md` |
| 上流リポジトリ | NDF の Skill・エージェント・hook の実体を持つリポジトリ | 配布元のリポジトリ | `plugins/ndf/skills/out-of-scope/references/issue-target.md` |
| 開発対象リポジトリ | NDF を使って開発している側のリポジトリ。gh repo view が返すもの | 開発対象のリポジトリ | `plugins/ndf/skills/out-of-scope/references/issue-target.md` |

## NDF の worktree（`ndf-worktree`）

worktree の設定・セッション開始 hook・テスト環境の割り当ての語

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| worktree レジストリ | 割り当て・ポート・基準のタグ・公開の記録を持つ JSON（.git/ndf/worktree-registry.json） | — | `docs/specifications/ndf-testenv-lock-and-registry.md` |
| 共有設定 | .ndf/worktree.json。git で追跡する | 共有の宣言 | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| 個人設定 | .ndf/worktree.local.json。git で追跡しない | 個人の宣言 | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| 実効設定 | wt_declaration が返す、共有設定に個人設定を反映した JSON | 重ね合わせた宣言 | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| 追従 | セッション開始 hook がメインディレクトリの HEAD を git checkout --detach で動かすこと（follow_branch） | — | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| セッション開始 hook | worktree-session.sh。Claude Code / Codex の SessionStart、Kiro の agentSpawn などで動く | 開始時の hook | `docs/specifications/ndf-worktree-declaration-and-entry-points.md` |
| 開発 worktree | 人が変更を加えて Pull Request にする worktree。ブランチを持つ | 開発用の worktree | `plugins/ndf/skills/worktree/SKILL.md` |
| レビュー worktree | cross-review と cross-refactoring が一時的に使う worktree。システムの一時ディレクトリに置く | レビュー用の worktree | `plugins/ndf/skills/worktree/SKILL.md` |

## mcp-serena（`mcp-serena`）

mcp-serena が言語サーバーを選び、インストールを確かめるときの語

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| インストールチェック | 公式 LSP プラグイン・本体・追加のチェックが揃っているかを見ること（check） | 導入のチェック | `docs/specifications/mcp-serena-language-servers.md` |

## playwright-kit のテスト計画（`playwright-kit`）

探索的テストとテスト設計技法の語。page role・oracle・HTSM など業界の定訳をそのまま使う

| 語 | 意味 | 廃止した語 | 正本 |
| --- | --- | --- | --- |
| page role | ページの機能的な役割（LP / list / item / edit / form など）。URL や SEO の構造とは別 | — | `plugins/playwright-kit/skills/playwright-planning/docs/README.md` |
| oracle | 不具合だと判定する根拠（仕様・過去版・標準・ユーザーの期待・内部の一貫性など） | — | `plugins/playwright-kit/skills/playwright-planning/docs/README.md` |
| FEW HICCUPPS | oracle の 11 の軸（Familiarity, Explainability, World, History ほか） | — | `plugins/playwright-kit/skills/playwright-planning/docs/README.md` |
| HTSM | テスト戦略を Mission / Environment / Product Elements / Quality Criteria の 4 つで組む Heuristic Test Strategy Model | — | `plugins/playwright-kit/skills/playwright-planning/docs/README.md` |
| SFDIPOT | Product Elements の 7 因子（Structure / Function / Data / Interfaces / Platform / Operations / Time） | — | `plugins/playwright-kit/skills/playwright-planning/docs/README.md` |
| EP / BVA | 同値分割（Equivalence Partitioning）と境界値分析（Boundary Value Analysis） | — | `plugins/playwright-kit/skills/playwright-planning/docs/README.md` |
