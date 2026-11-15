# Skill と起動スクリプト

<!-- ndf check --write-spec が生成する。手で直さない（正本は各節の出典） -->

配る Skill（`manifests/skills.json` の行で `skills/<名前>/SKILL.md` があるもの）と、公開する面に載る Skill の入出力である。正本は公開する Skill の引数と環境変数がエントリポイントの表、ほかは `contracts/domains/skill-io.json`。引数の形は正規表現で、どれか 1 つに全体が一致する

## Skill

| Skill | 公開か | 引数の形 | /goal の終わりの条件 | 読む宣言 | 読む環境変数 | 結果 | 意味 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cherry-pick-pr` | 内部（予告なく変えてよい） | `[^ ]+` | — | `worktree` | — | none | マージ済みの修正を環境ブランチへ cherry-pick する Pull Request を作る。引数は環境ブランチの名前 |
| `chrome-cdp` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 開発コンテナから利用者の Chrome へ CDP で繋ぎ、ログインと目視の確かめをする |
| `deploy` | 内部（予告なく変えてよい） | `[^ ]+` | — | `worktree` | — | none | 環境ブランチへのデプロイの Pull Request を作る。引数は環境ブランチの名前 |
| `development-workflow`（skills/ へ移す前） | 公開 | `#[1-9][0-9]*`<br>`--loop( [0-9A-Za-z][0-9A-Za-z_-]*)?( --milestones ([^ "]+\|"[^"]+")\| --budget-(laps\|usd\|hours) ([0-9]+(\.[0-9]+)?\|default))*( -- 終わり: .+)?` | — | — | `NDF_FACTORY_RUN`・`NDF_VERSION`・`NDF_STATE_HOME`・`NDF_LOOP_EXCLUDE`・`NDF_ISSUE_REQUEST_FILE`・`NDF_MVV_STATE_DIR`・`NDF_PROGRESS_DB`・`NDF_ORGANIZER_DIR`・`NDF_ORGANIZER_LANE`・`GH_TOKEN` | — | — |
| `docker-container-access` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | コンテナへの接続と localhost の経路の失敗を調べる |
| `document-systems` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 文書の置き場（Google Drive・Notion・Confluence・SharePoint・リポジトリ）ごとの読み書きの手順を持つ |
| `investigation-rules` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 調べた手段を示さずに「無い」と書かないための規則 |
| `logging-guidelines` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | ログのレベルの選び方と、秘密と個人の情報をログに出さない規則 |
| `ml-model-structure` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | 機械学習の学習と推論のコードの置き方 |
| `ndf-policies` | 内部（予告なく変えてよい） | 引数を受けない | — | `worktree` | — | none | ブランチの運用と Pull Request の運用の基準（知識として読むだけ） |
| `notion-writing` | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | none | Notion のページの書き方 |
| `pr-tests` | 内部（予告なく変えてよい） | （引数なし）<br>`[1-9][0-9]*` | — | — | — | none | Pull Request の本文のテストの計画を流し、結果を Pull Request のコメントに書く。引数は Pull Request の番号（無ければ今のブランチの Pull Request） |

## 応答の末尾の報告

| 結果の行の項目 | 値 | 環境変数 |
| --- | --- | --- |
| `結果` | `判断が要る`・`完了`・`できなかった` | `NDF_FACTORY_RUN` |

## 起動スクリプト

利用者と AI が打つ `bin/` のスクリプトである（正本は `contracts/domains/launchers.json`）。`ndf` の副命令は [副命令の引数と結果](commands.md) にある。

| 名前 | 渡し先 | 意味 |
| --- | --- | --- |
| `ndf` | ndf | 依存の環境を用意し、ndf <副命令> [引数...] を副命令の表の main へ渡す。結果 JSON を標準出力の最後の 1 行に出す |
| `ndf-claude` | claude | このプラグインだけを読む claude を起こす。引数はそのまま claude の後ろへ渡す |
