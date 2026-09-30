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
| リリース差分 | — | 版と版の間（タグからタグまで）の変更 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| インライン実行 | — | 仕事を渡す実行方式の 1 つ。いまの会話の文脈で行う | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| オーケストレーター | — | `cross-refactoring` と `cross-review` で、公開・生成物の同期を持ち、担当を回す側。3 層では conductor に当たる | 進行側、レビューを回す側 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| レッドライン | — | MVV 判定が「従う」でも利用者の承認を省かない操作。NDF の共通原則の `C<番号>`（NDF が持つ）・プロジェクト MVV の `P<番号>`（プロジェクトが足す）・スプリント MVV の `R<番号>` の 3 層 | 越えない線 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 実行条件 | — | プランを流す前に打つコマンド。`skip_code` を返せば worktree を作らずに完了とする | 実行の条件 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 流出不具合 | — | マージ済みの変更に見つかった不具合。直した Pull Request が触った領域を記録し、トリガーに数える | 逃げた不具合 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 検査の記録 | `check_log` | check-trigger.py が検査の事象（eval・check・escape）を 1 行ずつ追記する jsonl（checks/<owner>__<repo>.jsonl）。トリガーの判定と stats が読む | — | — | — |
| 重点領域 | — | `pace: fast` の領域のうち、触った Pull Request の点数を重くするもの | 共通層 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 承認ゲート | `approval_gate` | 人手の承認を求める点。設計 Pull Request のマージ（ゲート 1）と本番の系へ届く操作（ゲート 2）の 2 つだけ | 関門 | `gate` | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 承認資料 | — | 承認を求めるときに示すもの。対象を開くためのものと、承認の判断に使うものの 2 層を持つ | 提示物 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| エビデンス | — | 承認資料のうち機械で作れる部分と、conductor が確かめて足した事実。MVV 判定へ渡す | 事実の材料 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリース | `release` | 変更を利用者へ届く形で公開すること。その工程とフェーズの名前でもある | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリースプラン | — | リリースの手順をステップの列として持つプラン | 配布の計画 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリースコマンド | — | リポジトリが `.ndf/release.json` に宣言し、`release` がリリースの段階に合わせて走らせる 1 つのコマンド | 配布のコマンド | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 検証リリース | — | 開発版と分かる版数での公開か、検証環境への反映 | 検証への配布 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| インストール確認 | — | 隔離した HOME で ref からプラグインをインストールし、版と中身が ref と一致するかを確かめる | 導入確認 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 導入の確認 | — | `.ndf/pace.json` の `<節>.verify` に書かれたコマンドを、ベースブランチの先頭で走らせること。pace: fast / auto で、経路 promote では承認ゲート 2 の前に、経路 merge だけのときは検査の後に走り、終了コードを承認資料へ載せ、出力は所有者だけが読めるログへ分ける | — | — | — |
| リリース完了の確認 | — | 公開が済んだことを、リリース先の状態から読み取れる値 | 完了の事実 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スタックしたチェック | — | 実行が終わったのに pending のまま残った CI のチェック | 取り残されたチェック | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リファクタリング | — | 振る舞いを変えずに構造を直す工程 | 構造改善 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| コードレビュー | — | 実装の差分をレビューし、新しい指摘が出なくなるまで直す工程 | 実装レビュー | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リファクタリング計画 | — | `cross-refactoring` が採る改善項目を決め、見送った提案と理由を残す出力 | 改修計画 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| バッファ | — | `cross-refactoring` の見積りで、想定最大時間から経過を引いた後に残しておく時間 | 予備時間 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ラウンドテスト | `round_test` | `cross-refactoring` で、`--scope` のテストの置き場所を走らせるコマンド | ラウンドのテスト | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| グレード | — | `cross-refactoring` が候補ごとに付ける適用の価値（high / medium / low） | 等級 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 指摘ファイル | — | `cross-review` の担当が書く、指摘の全件と総評のファイル | 指摘のファイル | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 進捗記録 | — | 工程に入った時点で 1 回打つ記録。課題の本文の「進行」も同じ 1 回で更新される | 進行の記録、記録のコマンド | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| シグナルファイル | — | ラッパーへ知らせるファイル（`next.json` と `stop`） | 合図 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| アナウンス | — | ndf-next のブロックの直前にそのまま置く 1 文 | 告知 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| アイドル | — | シグナルファイル・会話の記録・利用者の入力が動かない秒数。この秒数がたつまでラッパーは `/exit` を入力しない | 静止 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| パススルー | — | ラッパーを挟まず、本物の claude をそのまま起動すること | 素通し | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 進捗ログ | `progress` | プランの実行中に 1 行 1 つの JSON で追記する記録（`progress.jsonl`） | 途中の報告 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| フェーズレポート | — | supervisor（またはプラン）が最後に返す報告 | フェーズの報告 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| worktree | `worktree` | 開発の変更を行う git worktree。課題ごとに 1 つ切る | 作業ツリー | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| メインディレクトリ | — | リポジトリを clone したディレクトリ | 主ディレクトリ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ベースブランチ | — | worktree の分岐元と Pull Request の宛先 | 起点のブランチ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 安定版と実験版 | — | NDF の変更の 2 つの経路（stable / experimental）。実験版の置き場は `experimental/` | 安定と試行 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 即時修正 | — | 4 つの条件（`development-workflow` の SKILL.md の「即時修正」）を満たす不具合を、起票せず、その場のプランで直すこと | その場で直す | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 棚卸し | — | 既存の課題の本文・マイルストーン・ラベルを現状に合わせ、着手の順位を決め直すこと。スクラムのバックログリファインメントに当たる | 手入れ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 文言固定テスト | — | リポジトリで追跡している .md を読み、その文字列・見出し・表の並びを照合するテスト。書かない | — | — | `docs/specifications/cross-refactoring-round-tests-and-assess.md` |
| 手順 | — | 1 つの Skill の中で順に通す作業の単位。cross-refactoring の提案・リファクタリング計画・テスト追加・実装・検証/修正の 5 つ、document-restructuring の測る・並べ替える・整える・測り直すの 4 つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 範囲テスト | — | 変更が触った範囲に限って走らせるテスト | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| suite の種別 | `kind` | テストの宣言（.ndf/project.json の test.suites[]）の 1 件がテスト（test）か静的解析（lint。整形の検査を含む）か。キーは kind。書かなければ test | — | — | — |
| 起動の失敗 | — | テストのコマンドのプロセスを起動できない、またはシェルが終了コード 126 / 127 を返したこと。テストが落ちたこととは別に扱う | — | — | — |
| 危険フラグ | `danger` | cross-refactoring で、範囲テストでは覆えない変更（D1〜D5）。立てば全体テストを 1 度走らせる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 設計 Pull Request | — | 要求仕様と設計文書だけを載せ、実装を含まない Pull Request。変更ファイルに issues/ の要求・設計・決定の記録を含む | — | — | `docs/specifications/ndf-design-phase.md` |
| 正本 | `source` | その事柄の定義を持つ唯一の文書。食い違ったときはこれを正とする | — | — | `docs/specifications/doc-consistency-checks.md` |
| conductor | `conductor` | 人間と対話しているセッション。スプリントを持ち、承認ゲートで人間へ問えるのはこの層だけ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| supervisor | `supervisor` | 1 つのフェーズを通すサブエージェント。人間へ問わない | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
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
| 最終工程 | — | その実行で最後に通る工程。振り返りを通るなら retrospective、通らずリリース後テストを通るなら release-verification | 終わりの工程 | — | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| ピーク使用量 | — | 応答ごとの入力トークンの合計の最大 | 最大充填 | — | `docs/specifications/ndf-context-window-metrics.md` |
| 合成応答 | — | API を呼ばずに Claude Code が記録へ書いた応答（message.model が <synthetic>） | 合成の応答 | — | `docs/specifications/ndf-context-window-metrics.md` |
| 設計文書 | — | design が作る成果物。issues/ 配下に置く Markdown | — | — | `docs/specifications/ndf-design-phase.md` |
| 企画承認 | — | documentation モードのゲート 1。構成案と体裁設計を載せた設計 Pull Request のマージ | — | — | `docs/specifications/ndf-documentation-mode.md` |
| 制作物承認 | — | documentation モードのゲート 2。production が真の提出先への操作 | — | — | `docs/specifications/ndf-documentation-mode.md` |
| 下書き先 | — | production が偽の提出先。承認の前に書き込んでよい唯一の場所 | — | — | `docs/specifications/ndf-documentation-mode.md` |
| 実行計画 | — | スプリントの課題を、バンドル・工程ごとの依存・触る箇所・着手できる時点で並べた表。オーケストレーターが持つ | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| バンドル | `bundle` | 1 本の設計 Pull Request で決める課題の集合 | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 課題グループ | — | マイルストーンの説明に書く、触る場所の見込みと依存で分けた課題の集合。実行計画のバンドルの初期値 | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 変更重複 | — | 2 つの Pull Request が同じファイルを触ること。節（見出し）・関数の単位で程度を分ける | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 並行度 | — | 対象の Pull Request のうち、2 本以上が同時に開いていた時間の割合 | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
| 予備メモリ | — | オーケストレーターと同じ VM に常駐する他のプロセスの変動のために空けておくメモリ | — | — | `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` |
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
| ステージ | `pipeline_stage` | パイプラインの中のプランのグループ 1 つ。中はキューで並列に流し、前のステージがすべて完了したときだけ次のステージが流れる。new sprint はステージごとにプランを書き出す（設計・ゲート 1・実装・検査・開発版・本番） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スイッチポイント | — | フェーズの中で supervisor を替える点。収束ループの前で hook が決める | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 3 層 | — | 工程を conductor → supervisor → worker の順に起動して通す形 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 最小構成の `claude -p` | — | Tool と指示を絞った 1 回の判断。judge のステップと MVV 判定が使う | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 実行レベル | — | 仕事を渡す実行方式の LLM の使い方の水準。レベル 1 = スクリプト、レベル 2 = 分類の判断、レベル 3 = インライン実行の LLM | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| モード | `mode` | 変更の目的物で決める工程の振り分け。上から operation / documentation / standard / legacy-refactor / light | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| pace | `pace` | モードとは別の軸で、工程をどう通すかを決める。ウォーターフォールで各工程を検証し人の承認を取る normal、normal の承認だけを MVV 判定にする auto、実践投入の中で検証しながら MVV で自動に進める fast の 3 つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| pace: auto | `auto` | 進め方の 1 つ。工程は normal と同じで、承認ゲート 1・2 だけを MVV 判定で自動にする。「従う」でレッドラインが無いときだけ通し、ほかは利用者へ戻す | — | — | `plugins/ndf/skills/development-workflow/references/pace.md` |
| 実践投入 | — | pace: fast の検証の場所。実装の Pull Request が develop（開発版のチャネル）へ入り、開発版として配布され使われること。fast の検査はその後にトリガーで通る | — | — | `plugins/ndf/skills/development-workflow/references/pace.md` |
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
| ゲート 1 | — | 設計 Pull Request のマージ。文書では企画承認に当たる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ゲート 2 | — | 本番系へ届く操作（本番へのリリースと operation の実行）。文書では制作物承認に当たる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 開発版 | — | ベースブランチ（develop）に載るチャネルと、そこへ出す接尾辞付きの版。マージされた変更がそのまま載る。手動反映の本番系の形では、production: false の行（検証の環境）へ届くことも開発版に当たる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 本番 | — | 利用者が現に使っているチャネル・環境・外部サービス（本番系）。プラグインのリリースでは本番のブランチ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 正式版 | — | 本番チャネルへ出す、接尾辞の無い版。出したらリリースタグを打つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 検査 | — | リファクタリング・コードレビュー・完了判定・Pull Request を通すフェーズ。fast ではトリガーが立ったときだけ、前回の検査からの差分に流す。コードレビューだけは開発版ごとに流す（--review-only） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| チェック | — | 機械が合否を返すもの。CI のジョブと、mvv-gate.py・doc-lint.py などのスクリプト | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 完了判定 | — | コマンドの証跡で完了を判定する工程。スクラムの完了の定義に当たる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 全体テスト | — | リポジトリ全体を範囲にするテストか静的解析。テストは宣言の suites[].command、無ければ雛形の {paths} を . にしたもの（その旨を注記に残す）。静的解析は宣言の command、無ければ雛形の {paths} を範囲のパスで埋めたもの（範囲のパスが無ければ組まず、その旨を注記に残す） | 全体のテスト | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| コメントのスナップショット | — | cross-review が取る既存コメントの一覧。2 ラウンド目以降は取り直す | 既存コメントのスナップショット | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| doc-lint | — | 追加した Markdown の行に、検討の痕跡・課題番号の由来・比較の語が無いかを見るチェック | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 引継ぎ文書 | `handoff` | 会話を切って再開するための文書。「今の会話の進み」（プランごとの行の表）と「次に実行するコマンド」の節をスクリプトが書く | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ndf-next | — | 次のセッションの最初の入力を置く、情報文字列 ndf-next の囲みのコードブロック。最後の応答に 1 つだけ置く | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| step / alive / worker / attention | — | 進捗ログの行の種類。ステップの切り替わり・動きの無い間の生存・worker の進み・conductor の判断が要る出来事（止まった・承認ゲート・同じ失敗の繰り返し・judge のステップで stop が出そう） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| done | — | キューが終わったときに書く結果の JSON。wait は done か attention の行まで待つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 結果 JSON | `step_result` | 手順のスクリプトが返す 1 行の JSON。status で読む | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 実装計画 | — | implementation-plan が issues/ に書く、実装の前の計画 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 確定仕様化 | — | 完了した実装計画を docs/ の確定仕様へ書き直す工程 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 振り返り | — | 進め方で変えることを記録し、起票の取りこぼしを拾う工程 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ライブラリ | `library` | 2 つ以上のスクリプトが使う関数とクラスの置き場。今の plugins/ndf/scripts/lib/ に当たる。pace.json の「共通層」（点数を重くする領域）とは別 | — | — | — |
| エントリポイント | `entry_point` | Skill・プラン・hook・宣言・利用者が呼ぶスクリプトのパスと副命令と引数と出力の形 | — | — | — |
| 移行ステップ | `migration_step` | 設計の決める移行の順序の 1 つ分。1 本の PR で閉じる。プランの「ステップ」とは別 | — | — | — |
| 使用量の帳簿 | `usage_ledger` | claude -p の 1 回の呼び出しごとに、版・プラン・ステップ・usage を 1 行で追記する jsonl（usage/<owner>__<repo>.jsonl） | — | — | — |
| 構造チェック | `structure_check` | テストを除くスクリプトの行数と、本体の同じ関数・同じ名前で本体の違う関数を構文木で数える継続的統合のチェック | — | — | — |
| プロジェクトの宣言 | — | リポジトリの根の `.ndf/` に置く、そのプロジェクトの形（言語・テスト・CI・ブランチ・配布・課題の正本など）を表すファイルの集まり。機能ごとの JSON と、解析が書く `project.json` からなる | — | — | — |
| 解析 | — | プロジェクトの宣言を作るために、リポジトリと CI を決定論で測り、測った値から宣言の中身を決めること | — | — | — |
| 不明 | — | 解析が測れなかった・決められなかった項目の値。ai-plugins の値で埋めない | — | — | — |
| 測った値 | — | 解析の測定が、ファイル・git・`gh` の出力から決定論で決めた項目の値 | — | — | — |
| 問い | — | 解析の測定が値を 1 つに決められず、候補と根拠を付けて conductor へ渡す項目 | — | — | — |
| 答え | — | 解析の問いごとに conductor が選んだ値か不明。書き出しが形を確かめて宣言へ書く | — | — | — |
| 入力の指紋 | — | 解析が読んだファイルの、HEAD の木での blob の SHA と、ブランチの構成の組。プロジェクトの宣言の新しさの判定に使う | — | — | — |
| テストの戦略 | `strategy` | 範囲テストの走らせ方・全体テストの置き場（手元か CI か）・落ちたテストの見分け方の組。local-full / local-scoped-ci-whole / round-only の 3 つ。宣言の test.strategy か、同じ関数が所要から導く（#1334） | — | — | — |
| 範囲テストの雛形 | `scope_command` | {paths} を引用の外の 1 字句として含むテストのコマンド（宣言の scope_command か、{paths} を含む引数）。{paths} をシェルの引用で守った対象の並びへ置き換え、シェルで走らせる | — | — | — |
| JUnit の置き場 | `junit` | テストのコマンドが JUnit XML を書くファイルの、作業ディレクトリからの相対パス（宣言の suites[].junit）。NDF はコマンドへ引数を足さず、このファイルを読む | — | — | — |
| スプリント | `sprint` | 1 回のリリースとして出す課題と Pull Request のセット。版数を持つプロジェクトでは 1 つの版になる。期間ではなく、1 回のリリースとして出す中身で切る。工程はスプリント単位で 1 回ずつ通し、モードもスプリントで 1 つにする | ミッション | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スプリント状態ファイル | `sprint_state` | スプリントのプラン・done・承認ゲートの記録・MVV・版を持つファイル（パスは呼ぶ側が決め、手順書の例は `sprint-state.json`。目録 `sprint.json` とは別のファイル） | ミッション状態ファイル、ミッションの状態 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スプリントブランチ | — | 課題の Pull Request を集め、ベースブランチへの Pull Request をスプリントで 1 本にするブランチ（`sprint/<名前>`） | ミッションブランチ、ミッションのブランチ | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スプリント課題 | — | スプリントに含まれる Pull Request の本文が、閉じる語で指す課題 | ミッション課題、ミッションの課題 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スプリント MVV | — | スプリント単位の MVV。プロジェクト MVV の範囲での具体化 | ミッション MVV | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 自動反映の本番チャネル | `auto_production_branch` | 本番チャネルのうち、マージ（push）で本番系への反映が自動で始まるもの。宣言の delivery に kind: auto で本番チャネルを branch に持つ行があるときに当たる。そこへのマージは承認ゲート 2 に当たる | — | — | — |
| 手動反映の本番系 | `manual_production` | 配布の宣言の行のうち、production: true と宣言され、kind: manual（担い手が手で起こす）のもの。そこへ届ける操作の前に承認ゲート 2 を掛ける。自動反映の本番チャネルと対になる | — | — | — |
| 載った版 | — | PR が載った正式版。CHANGELOG の版の節 → マージの時刻の後の最初の正式版のタグ → 行の時刻の後の最初の正式版のタグ、の順で決める。計測で版を寄せる規則 | — | — | — |
| 動いた版 | — | 呼び出しや会話を動かした NDF の版（使用量の帳簿の ndf_version・会話の Skill の置き場の版） | — | — | — |
| 耐久ワークフロー | `durable_workflow` | DBOS の @DBOS.workflow の関数の 1 回の実行。ID を持ち、落ちた後の起動が記録から続ける。プラン 1 本・キュー 1 本・drive 1 回・投稿の項目 1 件がそれぞれ 1 つ | — | — | — |
| 耐久ステップ | `durable_step` | 耐久ワークフローの中で出力を SQLite へ記録する単位（DBOS の @DBOS.step）。記録のある耐久ステップは続けるときに流し直さない。プランのステップとは別の語 | — | — | — |
| 耐久キュー | `durable_queue` | DBOS の register_queue で作る、同時の本数と分割を持つ待ち行列。supervise.py queue（キュー）とは別の語 | — | — | — |
| 耐久の記録 | `durable_store` | 1 回の起動が持つ DBOS の SQLite のファイル（~/.local/state/ndf/dbos/<種類>-<鍵>.sqlite） | — | — | — |
| 実行の鍵 | `run_key` | 起動を識別する文字列（run-<プランのパスの sha256 の先頭 12 字> など）。耐久の記録のファイル名と executor_id に使う | — | — | — |
| 実行の回 | `attempt` | 同じ実行の鍵の中で、耐久ワークフローを頭から流した 1 回。ID の末尾の番号 | — | — | — |
| 資源のタグ | `resource_tag` | ステップが使う、共有する外部の枠の名前（今は graphql だけ） | — | — | — |
| 資源の枠 | `resource_limit` | 資源のタグごとの、同時に流せるステップの本数の上限（.ndf/supervise.json の queue.resources） | — | — | — |
| 重なりの組 | `overlap_pair` | 同じステージの 2 本のプランで、触るファイルが包含で重なるか同じ共有の一覧に当たるもの。同時には流さない | — | — | — |
| 共有の一覧 | `shared_list` | 複数のプランが同じ行の並びへ書き足すファイル（索引など）。.ndf/supervise.json の queue.shared に書く | — | — | — |

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
| リトライ可否 | — | 同じ担当を同じ条件で起動し直せば解ける起動結果か（relaunch_same_agent） | 起動し直しの可否 | — | `docs/specifications/cross-review-launch-outcome.md` |
| 結果ファイル | `result_file` | 担当 CLI が書く判定の要約の JSON（<stem>-result.json） | — | — | `docs/specifications/cross-review-launch-outcome.md` |
| 監視結果ファイル | `monitor_outcome` | 監視が起動 1 回ごとに状態と理由を書く JSON（<stem>-monitor.json） | 監視の結果ファイル | — | `docs/specifications/cross-review-launch-outcome.md` |
| 監視ログ | — | 起動結果を追記だけで積む記録（monitor-outcomes.jsonl） | 監視の記録 | — | `docs/specifications/cross-review-launch-outcome.md` |
| ランタイム | `runtime` | エージェントの CLI の種類。claude / codex / agy / kiro の 4 つで、並びは固定（ALL_RUNTIMES） | — | — | `docs/specifications/cross-review-participants-and-seats.md` |
| ホスト | `host` | 収束ループを起動している CLI のランタイム（host） | — | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 参加者プール | — | Skill ごとに決まる参加者の出発点。cross-review と cross-refactoring で共通の claude / codex / kiro とホスト（default_pool） | 参加の母集合 | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 参加者 | `participant` | 参加者プールに --include の者を加え、--exclude の者を除いた一覧。認証確認の対象 | — | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 利用可能な参加者 | — | 参加者のうち認証確認を通った者（participants.available） | 使える者 | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 認証確認 | — | 確認コマンドを走らせ、止めずに結果だけを返す参加者ごとの確認（probe_auth） | 認証の確認 | — | `docs/specifications/cross-review-participants-and-seats.md` |
| スロット | `slot` | 1 ラウンドで 1 つの CLI プロセスが占める枠 | — | — | `docs/specifications/cross-review-participants-and-seats.md` |
| フォールバック | `fallback` | 利用可能な参加者が 2 者に満たないとき、足りない分を埋める参加者（participants.fallback） | 埋め合わせ | — | `docs/specifications/cross-review-participants-and-seats.md` |

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
| 投稿キュー | `post_queue` | 送る前に投稿を積み、上限で送れなければ残す仕組み（lib/post_queue.py）。スプリント 2c の後、項目は耐久の記録に置く | 投稿の待ち行列 | — | `docs/specifications/cross-review-writes-to-conductor.md` |
| 重複投稿 | — | 送ろうとした投稿と同じものとして、すでに Pull Request にある投稿 | 先客 | — | `docs/specifications/cross-review-writes-to-conductor.md` |
| 総評 | — | レビュー本体に書く文章（body）。インラインのコメントとは別に置く | — | — | `docs/specifications/cross-review-writes-to-conductor.md` |
| drive の状態 | `drive_state` | 収束ループの drive.py が Pull Request ごとに持つ状態ファイル（drive-pr<N>.json / drive-rf<ID>.json）。stage と init_vars を持つ。スプリント 2c の Q3・Q4 で耐久の記録へ移して廃止する | — | — | — |
| 指摘の基準 | — | 指摘として出してよいものを決める 4 つの条件（利用者が普通に使う経路の誤動作・レッドラインに触れるもの・レビューの重点・実装を違えさせる設計の食い違い）。当たるものが major 以上になる | — | — | — |
| レビューの重点 | — | プロジェクトが .ndf/review.json で宣言した、指摘の基準 3 に使う観点。宣言が無ければ基準 3 は無い | — | — | — |
| 見送りの返信 | — | 修正担当が minor / nit の指摘を直さずに閉じるときに書く返信。雛形から組み、理由の種類（見送りの種類の名前）と直す条件（使って困る場面が出たら直す）を定型文で書く | — | — | — |
| 見送りの種類 | — | 見送りの返信の括弧に書く、指摘の基準に当たらない理由の 5 分類（waive_kind） | — | — | — |
| 最終スイープ | — | 収束ループを抜けた後に /ndf:fix を通し、open thread を 0 にする工程 | — | — | — |

## NDF の cross-refactoring（`ndf-cross-refactoring`）

想定最大時間に収まるリファクタリング計画を立てて通す語。改善候補・改善項目・配分テーブル・最終ゲート

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| マージ処理 | — | 担当の結果やコミットをオーケストレーターが受け入れるコマンド（merge-proposals / merge-plan / merge-tests / merge-implement / merge-fix / merge-final-fix）。フェーズの「取り込み」とは別 | — | — | `docs/specifications/cross-refactoring-apply-intake.md` |
| 最終ゲート | `final_gate` | 全体テストか CI で合否を判定する、cross-refactoring の最後の手順（final-gate）。承認ゲートとは別 | — | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 最終ゲート修正 | — | 最終ゲートの失敗を直す起動と、そのマージ処理（final-fix / merge-final-fix）。改善項目に属さない | 最終ゲートの修正 | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
| 必須トレーラー | — | 担当がコミットメッセージのトレーラーとして書く Item-Id / Impl-Runtime / Impl-Model。最終ゲート修正は Item-Id を除く 2 つ | 必須の記名 | — | `docs/specifications/cross-refactoring-apply-intake.md` |
| 帰属トレーラー | — | 実行環境がコミットメッセージの末尾へ足すトレーラー（Co-Authored-By: / Claude-Session:） | 帰属の段落 | — | `docs/specifications/cross-refactoring-apply-intake.md` |
| プロダクションコード | — | コードの拡張子を持ち、テストの置き場所でないファイル（production_code_changes） | 本番コード | — | `docs/specifications/cross-refactoring-round-tests-and-assess.md` |
| 想定最大時間 | — | 利用者が与える所要の上限（分、--budget-minutes）。リファクタリング計画はこの中に収まるように立てる | — | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 実装担当 | — | リファクタリング計画・テスト追加・実装・修正・最終ゲート修正を通して担う 1 ランタイム（--implementer） | — | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 改善候補 | — | 提案を path + symbol + smell のキーで集約したもの。リファクタリング計画へ渡す（candidates[]） | — | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 改善項目 | — | リファクタリング計画が採った改善候補。I-001 の形の ID を持ち、1 改善項目 = 1 コミット（テストを足す項目は 2 コミット） | — | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 配分テーブル | — | 種類ごとの 1 件あたりの所要（分）。履歴の直近からリファクタリング計画のたびに集計し、保存しない（plan.table） | — | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 着手期限 | — | 実装・テスト追加でその改善項目に着手してよい最後の時刻（start_deadline） | 着手の締め切り | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 完了期限 | — | 着手期限にその改善項目の見積りを足した時刻。マージ処理はコミットの時刻をこれと比べる | 完了の締め切り | — | `docs/specifications/cross-refactoring-time-budget.md` |
| 変更したファイル | — | 判定の起点から変わったファイルのうち、worktree に残るもの。起点は判定ごとに決まる。項目の範囲テストではその項目のコミット（実装と修正）、最終ゲートでは着手前の HEAD、`test-run.py whole` では merge-base である。そのうち静的解析の suite の `paths` に当たるものが、その suite の範囲になる | — | — | — |
| フレーキー / 既存失敗 / 変更起因 | — | 全体テスト（着手前・危険フラグ・最終ゲート）で落ちたテストの 3 つの分類（flaky / preexisting / caused。着手前は baseline_test.existing_failures（既存失敗）、危険フラグは whole_test、最終ゲートは final_gate.checks[] の記録に書く）。ID は JUnit から読み、落ちたファイルだけを HEAD と着手前の HEAD で走らせ直して分ける | 元からの失敗 | — | `docs/specifications/cross-refactoring-verify-and-final-gate.md` |
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
| 積み直しの起点 | — | 取り消しで git reset --hard する先。未公開の範囲で最も古い「消すコミット」の親で、公開した地点より前には置かない | — | — | — |

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
| 登録済みアカウント | — | 利用者が登録のコマンドで認証情報を預けた claude のアカウント。アカウントの切り替えの候補になる | — | — | — |
| アカウントの切り替え | — | 次に起動する claude（ラッパーの次の区間・worker のやり直し）を、別の登録済みアカウントの認証で起動すること。動いているプロセスの認証は替えない | — | — | — |
| 使用率 | — | api/oauth/usage が返す five_hour / seven_day の utilization と、limits[] の weekly_scoped の percent（%）。推論を呼ばずに読む。切り替えの閾値と比べるのはその最大 | — | — | — |
| 支出上限 | — | 追加利用の支出の上限（individual spend limit）。extra_usage.spend_limit_reached が真か、spend.percent が 100 以上か、spend.severity が critical なら達したとする | — | — | — |
| 従量の接続 | — | 利用上限の無い、使った分だけ費用が掛かる claude の接続（Bedrock か Anthropic API の API キー）。中身は利用者が宣言し、登録済みアカウントがすべて上限のときだけ使う | — | — | — |
| アカウントの置き場 | — | 登録済みアカウントごとの設定ディレクトリを並べた ${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/accounts/。書くのは lib/claude_accounts.py だけ | — | — | — |
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
| アカウントのスコープ | — | 登録済みアカウントのトークンに付いた権限の並び。アカウントの置き場の .credentials.json の claudeAiOauth.scopes で、子へは CLAUDE_CODE_OAUTH_SCOPES（空白区切り）で渡す | — | — | — |

## NDF のリリース（`ndf-release`）

release が走らせるリリースの種別・リリースコマンド・公開操作・リリース記録の語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | 正本 |
| --- | --- | --- | --- | --- | --- |
| リリース記録 | — | release が Pull Request へ残す記録のブロック。段階・版・スプリントの Pull Request を持つ | リリースの記録 | — | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| リリース種別 | `release_type` | リリースの種類。production（本番リリース）/ verification（検証リリース） | — | — | `docs/specifications/ndf-release-steps-and-token-usage-snapshot.md` |
| 公開操作 | — | release の手順 4 で行う操作。レジストリへの公開・配備先への反映・署名した配布物の設置・ストアへの提出 | 公開の操作 | — | `plugins/ndf/skills/release/references/completion-check.md` |
| リリースの経路 | `release_route` | 変更が本番系へ届く道筋の種類。template（release.form の雛形で組む）/ merge（マージで反映）/ manual（手で反映）/ none（届けない）。release.form があればそれ、無ければ宣言の delivery から決まる。リリースの形とは別の軸 | — | — | — |
| 昇格の Pull Request | `promotion_pr` | ベースブランチから本番チャネルへ変更を入れる Pull Request | — | — | — |
| 昇格のプラン | — | リリースの経路 promote のための「本番」のステージのプラン（plan_promote）。昇格の Pull Request を作り、承認ゲート 2 の後にマージする | — | — | — |

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
| 起票先 | — | gh issue create が issue を作るリポジトリ | — | — | `plugins/ndf/skills/out-of-scope/references/issue-target.md` |
| 上流リポジトリ | — | NDF の Skill・エージェント・hook の実体を持つリポジトリ | 配布元のリポジトリ | — | `plugins/ndf/skills/out-of-scope/references/issue-target.md` |
| 開発対象リポジトリ | — | NDF を使って開発している側のリポジトリ。gh repo view が返すもの | 開発対象のリポジトリ | — | `plugins/ndf/skills/out-of-scope/references/issue-target.md` |
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
| 到達の確認 | — | コンテナの中から worktree の探りの印を読み、テストのコンテナが worktree を見ているかを確かめること | — | — | — |
| ツールのパス | `tool_paths` | CLI が起動したツール（MCP サーバーなど）が、利用者の操作なしに worktree の中で書き換える既知のパス。既定（`.serena/project.yml` と `.serena/serena_config.yml`）と `.ndf/worktree.json` の `tool_paths`（`add` / `remove`）で決まる。検査とコミットの対象から外す | — | — | — |

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
