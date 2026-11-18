# Skill と起動スクリプト

<!-- ndf check --write-spec が生成する。手で直さない（SSOT は各節の出典） -->

配る Skill（`manifests/skills.json` の行で `skills/<名前>/SKILL.md` があるもの）と、公開する面に載る Skill の入出力である。SSOT は公開する Skill の引数と環境変数がエントリポイントの表、ほかは `contracts/domains/skill-io.json`。引数の形は正規表現で、どれか 1 つに全体が一致する

## Skill

| Skill | 公開か | 引数の形 | /goal の終わりの条件 | 読む設定 | 読む環境変数 | 結果 | 意味 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `backlog-refinement` | 内部（予告なく変えてよい） | （引数なし）<br>`.*` | — | `backlog`・`issue-items`・`project` | — | none | 開いている課題を読み、重複と古いものを整理して優先順位を付ける |
| `cherry-pick-pr` | 内部（予告なく変えてよい） | `[^ ]+` | — | `worktree` | — | none | マージ済みの修正を環境ブランチへ cherry-pick する Pull Request を作る。引数は環境ブランチの名前 |
| `chrome-cdp` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 開発コンテナから利用者の Chrome へ CDP で繋ぎ、ログインと目視の確かめをする |
| `cross-refactoring` | 内部（予告なく変えてよい） | （引数なし）<br>`.*` | — | `estimate`・`project` | — | report | 複数の AI ランタイムで同じ範囲をリファクタリングし、テストの通るものを採る |
| `cross-review` | 内部（予告なく変えてよい） | （引数なし）<br>`.*` | — | `project` | — | report | 複数の AI ランタイムで Pull Request をレビューし、指摘が無くなるまで修正と再レビューを回す |
| `decision-request` | 内部（予告なく変えてよい） | 引数を受けない | — | `ask` | — | none | 人へ判断を求める前に、経緯・根拠・選択肢を示す手順 |
| `deploy` | 内部（予告なく変えてよい） | `[^ ]+` | — | `worktree` | — | none | 環境ブランチへのデプロイの Pull Request を作る。引数は環境ブランチの名前 |
| `design` | 内部（予告なく変えてよい） | 引数を受けない | — | `backlog` | — | none | 領域ごとの設計の観点と手順 |
| `development-workflow` | 公開 | `#[1-9][0-9]*`<br>`--loop( [0-9A-Za-z][0-9A-Za-z_-]*)?( --milestones ([^ "]+\|"[^"]+")\| --budget-(laps\|usd\|hours) ([0-9]+(\.[0-9]+)?\|default))*( -- 終わり: .+)?` | — | `ask`・`backlog`・`context`・`glossary`・`handoff`・`mvv`・`mvv-body`・`pace`・`parallel`・`project`・`projects`・`release`・`supervise`・`worktree`・`worktree-local` | `NDF_FACTORY_RUN`・`NDF_VERSION`・`NDF_STATE_HOME`・`NDF_LOOP_EXCLUDE`・`NDF_ISSUE_REQUEST_FILE`・`NDF_MVV_STATE_DIR`・`NDF_PROGRESS_DB`・`NDF_ORGANIZER_DIR`・`NDF_ORGANIZER_LANE`・`GH_TOKEN` | block | 課題を指して、要求から本番のリリースまでの開発の工程を回す |
| `docker-container-access` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | コンテナへの接続と localhost の経路の失敗を調べる |
| `document-drafting` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 文書の下書きを書く手順 |
| `document-rebuild` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 既存の文書を作り直す手順 |
| `document-sources` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 文書の材料の集め方と出典の残し方 |
| `document-style` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 文書の書き方の規則 |
| `document-systems` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 文書の保存先（Google Drive・Notion・Confluence・SharePoint・リポジトリ）ごとの読み書きの手順を持つ |
| `estimate` | 内部（予告なく変えてよい） | （引数なし）<br>`.*` | — | `estimate` | — | none | 課題か作業の説明から、作業の量と費用を見積もる |
| `external-ai` | 内部（予告なく変えてよい） | 引数を受けない | — | `runtime-policy` | — | none | 外部の AI ランタイム（codex・agy・kiro）を呼ぶ手順 |
| `fix` | 内部（予告なく変えてよい） | （引数なし）<br>`.*` | — | `review` | — | none | Pull Request のレビューの指摘を振り分けて直し、戻り値ファイルを書く |
| `google-workspace` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | Google Workspace（Drive・Docs・Sheets）を読み書きする手順 |
| `implementation-plan` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 設計から実装のプランを作る手順 |
| `install-wrapper` | 内部（予告なく変えてよい） | `install`<br>`uninstall`<br>`status` | — | — | — | none | Claude Code の中継（ラッパー）を入れる・外す・状態を見る |
| `investigation-rules` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 調べた手段を示さずに「無い」と書かないための規則 |
| `issue-plan-strategy` | 内部（予告なく変えてよい） | （引数なし）<br>`.*` | — | `handoff`・`parallel` | — | none | 課題を読み、並べて進める実装のプランを立てる |
| `layout-review` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 画面の配置をレビューする手順 |
| `logging-guidelines` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | ログのレベルの選び方と、秘密と個人の情報をログに出さない規則 |
| `merged` | 内部（予告なく変えてよい） | （引数なし）<br>`[1-9][0-9]*` | — | `worktree` | — | none | マージ済みの Pull Request の作業場所とブランチを片付ける。引数は Pull Request の番号 |
| `ml-model-structure` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 機械学習の学習と推論のコードの置き方 |
| `ndf-policies` | 内部（予告なく変えてよい） | 引数を受けない | — | `worktree` | — | none | ブランチの運用と Pull Request の運用の基準（知識として読むだけ） |
| `notion-writing` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | Notion のページの書き方 |
| `official-skills-autoloader` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 公式の Skill を必要なときに読み込む |
| `out-of-scope` | 内部（予告なく変えてよい） | 引数を受けない | — | `issue-items` | — | none | 範囲外の指摘を課題として起票する |
| `plan-to-spec` | 内部（予告なく変えてよい） | 引数を受けない | — | `glossary` | — | none | 出したスプリントのプランを確定した仕様の文書へ移す |
| `pr` | 内部（予告なく変えてよい） | （引数なし）<br>`.*` | — | `worktree` | — | none | 今のブランチから Pull Request を作る |
| `pr-review` | 内部（予告なく変えてよい） | （引数なし）<br>`.*` | — | — | — | report | Pull Request かブランチをレビューし、承認か変更の要求を判定する |
| `pr-tests` | 内部（予告なく変えてよい） | （引数なし）<br>`[1-9][0-9]*` | — | — | — | none | Pull Request の本文のテストの計画を流し、結果を Pull Request のコメントに書く。引数は Pull Request の番号（無ければ今のブランチの Pull Request） |
| `problem-solving` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 不具合の根本原因を切り分ける手順 |
| `progress-tracking` | 内部（予告なく変えてよい） | （引数なし）<br>`.*` | — | — | — | none | スプリントのステートマシンから工程の進み具合を読み、スプリントを閉じる |
| `qa-security-scan` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | セキュリティの検査を流して結果をまとめる |
| `quality-gates` | 内部（予告なく変えてよい） | 引数を受けない | — | `project` | — | none | テスト・静的解析・型の検査をまとめて流す |
| `refactoring` | 内部（予告なく変えてよい） | 引数を受けない | — | `code-metrics` | — | none | 言語ごとのリファクタリングの手順 |
| `release` | 内部（予告なく変えてよい） | 引数を受けない | — | `instructions`・`project`・`release`・`supervise`・`worktree` | — | none | バージョンを作り、開発版と本番へリリースする |
| `release-verification` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | リリースしたバージョンを実際に使って受け入れ条件を確かめる |
| `requirements-design` | 内部（予告なく変えてよい） | 引数を受けない | — | `glossary`・`issue-items` | — | none | 課題から要求と受け入れ条件をまとめる |
| `restart` | 内部（予告なく変えてよい） | （引数なし）<br>`.*` | — | `handoff` | — | block | 再開のコマンドで claude を再起動する（中継の下では応答の末尾の ndf-next ブロックで切り替える） |
| `retrospective` | 内部（予告なく変えてよい） | 引数を受けない | — | `worktree` | — | none | スプリントの振り返りを書く |
| `skill-stats` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | Skill の使われ方を集計する |
| `statusline` | 内部（予告なく変えてよい） | `status`<br>`set`<br>`restore` | — | — | — | none | Claude Code の statusline を切り替える・戻す・状態を見る |
| `tdd-cycle` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | テストを先に書いて実装する手順 |
| `worktree` | 内部（予告なく変えてよい） | 引数を受けない | — | `project`・`worktree`・`worktree-local` | — | none | 作業場所（git worktree）を作り、依存を用意する |

## 応答の末尾の報告

| 結果の行の項目 | 値 | 環境変数 |
| --- | --- | --- |
| `結果` | `判断が要る`・`完了`・`できなかった` | `NDF_FACTORY_RUN` |

## 起動スクリプト

利用者と AI が打つ `bin/` のスクリプトである（SSOT は `contracts/domains/launchers.json`）。`ndf` のサブコマンドは [サブコマンドの引数と結果](commands.md) にある。

| 名前 | 渡し先 | 意味 |
| --- | --- | --- |
| `ndf` | ndf | 依存の環境を用意し、ndf <サブコマンド> [引数...] をサブコマンドの表の main へ渡す。結果 JSON を標準出力の最後の 1 行に出す |
| `ndf-claude` | claude | このプラグインだけを読む claude を起動する。引数はそのまま claude の後ろへ渡す |
