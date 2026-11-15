# 環境変数

<!-- ndf check --write-spec が生成する。手で直さない（正本は各節の出典） -->

NDF が読む環境変数と、NDF が子のプロセスへ渡す環境変数である。正本は `contracts/domains/env.json`。公開は、公開する面の表のエントリポイントの `env` に載る変数である。値が秘密の変数は値をログ・結果・記録へ出さない

| 名前 | 設定する者 | 型 | 秘密か | 意味 | 公開か |
| --- | --- | --- | --- | --- | --- |
| `ANTHROPIC_API_KEY` | ndf | string | 秘密 | 子の claude へ渡した API キー | 内部（予告なく変えてよい） |
| `CLAUDECODE` | runtime | flag | — | Claude Code の中で動いている印（ホストの推定）。中継が起動するセッションへ渡さない（残ると claude が入れ子の起動を断る） | 内部（予告なく変えてよい） |
| `CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION` | ndf | string | — | 中継が起動する claude へ false を渡し、入力欄のサジェストを止める（人の入力と取り違えない） | 内部（予告なく変えてよい） |
| `CLAUDE_CODE_SESSION_ID` | runtime | string | — | Claude Code の今のセッションの ID。判定の記録と再起動の行をセッションに結ぶ。中継が起動するセッションへ親の ID を渡さない | 内部（予告なく変えてよい） |
| `CLAUDE_CONFIG_DIR` | user | path | — | Claude Code の設定の置き場。中継の複製（ndf/relay.py）をこの下へ置く。無ければ ~/.claude | 内部（予告なく変えてよい） |
| `CLAUDE_PLUGIN_ROOT` | runtime | path | — | プラグインの根。無ければ読み手の位置から求める。ホストの推定では Claude Code の印 | 内部（予告なく変えてよい） |
| `CODEX_HOME` | runtime | path | — | Codex の設定の置き場（ホストの推定と使用量の読み取り） | 内部（予告なく変えてよい） |
| `CODEX_PLUGIN_ROOT` | runtime | path | — | Codex のプラグインの根（ホストの推定） | 内部（予告なく変えてよい） |
| `GH_TOKEN` | user | string | 秘密 | GitHub の API のトークン | 公開 |
| `GITHUB_EVENT_PATH` | ci | path | — | GitHub Actions の事象の JSON。検査が PR 本文を読む | 内部（予告なく変えてよい） |
| `GITHUB_REPOSITORY` | ci | string | — | GitHub Actions が渡す owner/repo | 内部（予告なく変えてよい） |
| `GIT_ASKPASS` | user | path | — | git の認証の問い合わせ先。VS Code の窓の中の値は窓を閉じると固まるため、中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `GOOGLE_WORKSPACE_CLI_TOKEN` | user | string | 秘密 | gws（Google Workspace の CLI）へ渡すトークン。あれば gws の認証の確かめは、gws auth status が auth_method: none を返しても認証済みとする。無ければ gws auth status の credential_source で決める | 内部（予告なく変えてよい） |
| `HOME` | user | path | — | 利用者のホームディレクトリ。worktree の書き込み先の判定が ~ で始まるパスを解くときと、学習の結果のキャッシュの置き場を求めるときに読む。無ければ ~ を空として解く | 内部（予告なく変えてよい） |
| `KIRO_AGENT` | runtime | string | — | Kiro のエージェントの名前（ホストの推定） | 内部（予告なく変えてよい） |
| `KIRO_PLUGIN_ROOT` | runtime | path | — | Kiro のプラグインの根（ホストの推定） | 内部（予告なく変えてよい） |
| `NDF_CALLER_PLUGIN_ROOT` | ndf | path | — | bin/ndf が子へ自分の根を渡す前に写した、呼び出し元の CLAUDE_PLUGIN_ROOT（空なら無かった）。ほかのプラグインの根なら旧い状態の置き場を退避しない。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `NDF_CLAUDE_ACCOUNT` | user | string | — | 子の claude を起動する登録済みのアカウントの名前 | 内部（予告なく変えてよい） |
| `NDF_CLAUDE_API_KEY_GENERATION` | ndf | integer | — | 子へ渡した API キーの世代。入れ子の監督が読む | 内部（予告なく変えてよい） |
| `NDF_CLAUDE_API_KEY_NAME` | ndf | string | — | 子へ渡した API キーの名前。入れ子の監督が読む | 内部（予告なく変えてよい） |
| `NDF_CLAUDE_KEY_ACCOUNT` | ndf | string | — | API キーで起動した子が、キーを外したときに戻る登録済みのアカウントの名前 | 内部（予告なく変えてよい） |
| `NDF_FACTORY_RUN` | service | flag | — | サービスの実行ジョブの中で動いている印 | 公開 |
| `NDF_HOOK_OFF` | user | string | — | 止める guard の名前をカンマでつなぐ。all で全部を止める。無ければ全部の guard が働く。閾値はこの変数で受けず、上限の宣言から読む | 内部（予告なく変えてよい） |
| `NDF_ISSUE_REQUEST_FILE` | service | path | — | 作業のリポジトリと別の宛先への起票を、承認資料を書かずに依頼の 1 行（{repo, title, body, labels} の JSON）として足すファイル。サービスが起票するかを決める。無ければ承認資料で止まる | 公開 |
| `NDF_LOOP_EXCLUDE` | service | string | — | 開発ループの選定から、そのループの 1 回だけ外す課題の番号（, か空白で区切る）。同じリポジトリの別の実行が持つ課題をサービスが渡す。無ければ外さない | 公開 |
| `NDF_MVV_STATE_DIR` | service | path | — | 判断の記録（ask.jsonl）と承認ゲートの差し戻しの記録（gate-rejections.jsonl）の置き場。無ければ状態の置き場 | 公開 |
| `NDF_ORGANIZER_DIR` | ndf | path | — | 外のオーガナイザーとファイルでやり取りする置き場。あればこのセッションは外のオーガナイザーのレーン | 公開 |
| `NDF_ORGANIZER_LANE` | ndf | string | — | 外のオーガナイザーのレーンの名前。NDF_ORGANIZER_DIR と組で渡す | 公開 |
| `NDF_PROGRESS_DB` | service | string | 秘密 | 進行表の DB の URL（sqlite:/// か postgresql://。PostgreSQL の URL は認証情報を含むため秘密）。無ければチェックアウトの置き場の progress.sqlite。Factory は PostgreSQL の URL を入れる | 公開 |
| `NDF_RELAY_DIR` | ndf | path | — | 中継のラッパーが子のセッションへ渡す区間のやり取りの置き場。hook が次の区間の印と問い合わせをここへ書く。無ければ中継の外 | 内部（予告なく変えてよい） |
| `NDF_RELAY_HUB_WAIT` | user | seconds | — | 頼む側がリレーハブの答えを待つ上限。無ければ上限の宣言の値 | 内部（予告なく変えてよい） |
| `NDF_RELAY_MAX_NAMED` | user | integer | — | 名前つきのラッパーの数の上限（レーン数の上限）。無ければマシンの設定 | 内部（予告なく変えてよい） |
| `NDF_RELAY_MAX_STARTS` | user | integer | — | 1 日に起動し直す回数の上限。無ければ上限の宣言の値 | 内部（予告なく変えてよい） |
| `NDF_RELAY_NAME` | user | string | — | 名前つきのラッパーの名前。無ければリレーハブが t1・t2… を付ける | 内部（予告なく変えてよい） |
| `NDF_RELAY_PLAN` | ndf | string | — | リレーハブが spawn --plan で起動したラッパーの並列の計画の名前 | 内部（予告なく変えてよい） |
| `NDF_RELAY_ROLE` | ndf | string | — | リレーハブが spawn --role で起動したラッパーの役割 | 内部（予告なく変えてよい） |
| `NDF_RELAY_SPAWNED` | ndf | flag | — | リレーハブが tmux で起動したラッパーの印 | 内部（予告なく変えてよい） |
| `NDF_RELAY_STOP_WAIT` | user | seconds | — | 名前つきの stop がラッパーの終わりを待つ上限。無ければ上限の宣言の値 | 内部（予告なく変えてよい） |
| `NDF_RELAY_TMUX` | user | path | — | リレーハブが呼ぶ tmux の実行ファイル。無ければ PATH の tmux | 内部（予告なく変えてよい） |
| `NDF_RUNTIME_SESSION` | user | string | — | ランタイムのセッションを <pid>:<開始時刻> で指定する（テストと CI）。無ければ祖先のプロセスから探す | 内部（予告なく変えてよい） |
| `NDF_STATE_HOME` | user | path | — | 状態の置き場の根。置き場は <値>/<プラグイン名>（XDG_STATE_HOME と同じ読み方。入れ替えの前に旧い NDF と同じ値を渡されても、旧い置き場を退避しない）。無ければ XDG_STATE_HOME の下 | 公開 |
| `NDF_UPKEEP_STATE_DIR` | user | path | — | 棚卸しの状態（候補・順位・却下・見積・付け直しの前の状態・深さの記録）の置き場。無ければメインディレクトリの .ndf/backlog/state | 内部（予告なく変えてよい） |
| `NDF_USAGE_DIR` | user | path | — | 使用量の帳簿（usage.jsonl）の置き場。hook から書いてもスクリプトから書いても同じ置き場になる。無ければ状態の置き場の usage | 内部（予告なく変えてよい） |
| `NDF_VERSION` | service | string | — | サービスの実行ジョブが入れた NDF の版（開発版は <version>@<コミットの SHA>） | 公開 |
| `SLACK_BOT_TOKEN` | user | string | 秘密 | Slack の通知のトークン。無ければ通知しない | 内部（予告なく変えてよい） |
| `SLACK_CHANNEL_ID` | user | string | — | Slack の通知の宛先のチャンネル | 内部（予告なく変えてよい） |
| `UV_PROJECT_ENVIRONMENT` | ndf | path | — | bin/ndf と hook の依存の環境の用意が uv へ渡す、依存の環境の置き場（uv が読む）。NDF は読まない | 内部（予告なく変えてよい） |
| `VSCODE_GIT_ASKPASS_EXTRA_ARGS` | user | string | — | VS Code の git 認証の部品。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `VSCODE_GIT_ASKPASS_MAIN` | user | path | — | VS Code の git 認証の部品。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `VSCODE_GIT_ASKPASS_NODE` | user | path | — | VS Code の git 認証の部品。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `VSCODE_GIT_IPC_HANDLE` | user | path | — | VS Code の git 認証の通り道。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `XDG_BIN_HOME` | user | path | — | PATH のエントリポイント（ndf-relay）を置く置き場。無ければ ~/.local/bin | 内部（予告なく変えてよい） |
| `XDG_STATE_HOME` | user | path | — | XDG の状態の置き場。無ければ ~/.local/state | 内部（予告なく変えてよい） |
