# 環境変数

<!-- ndf check --write-spec が生成する。手で直さない（SSOT は各節の出典） -->

NDF が読む環境変数と、NDF が子のプロセスへ渡す環境変数である。SSOT は `contracts/domains/env.json`。公開は、公開する面の表のエントリポイントの `env` に載る変数である。値が秘密の変数は値をログ・結果・記録へ出さない

| 名前 | 設定する者 | 型 | 秘密か | 意味 | 公開か |
| --- | --- | --- | --- | --- | --- |
| `ANTHROPIC_API_KEY` | ndf | string | 秘密 | 子の claude へ渡した API キー。親の環境の値は、登録済みアカウントと従量の接続の子へ渡さない（認証の変数） | 内部（予告なく変えてよい） |
| `ANTHROPIC_AUTH_TOKEN` | user | string | 秘密 | Claude Code が Authorization に載せるトークン（ログインより上の認証の源）。登録済みアカウントと従量の接続の子へ渡さない | 内部（予告なく変えてよい） |
| `ANTHROPIC_FEDERATION_RULE_ID` | user | string | — | 認証のプロファイルのフェデレーションの規則（認証の優先順位でログインより上に来る源）。登録済みアカウントと従量の接続の子へ渡さない | 内部（予告なく変えてよい） |
| `ANTHROPIC_ORGANIZATION_ID` | user | string | — | 認証のプロファイルの組織（認証の優先順位でログインより上に来る源）。登録済みアカウントと従量の接続の子へ渡さない | 内部（予告なく変えてよい） |
| `ANTHROPIC_PROFILE` | user | string | — | 認証のプロファイルの名前（認証の優先順位でログインより上に来る源）。登録済みアカウントと従量の接続の子へ渡さない | 内部（予告なく変えてよい） |
| `CLAUDECODE` | runtime | flag | — | Claude Code の中で動いているマーカー（ホストの推定）。中継が起動するセッションへ渡さない（残ると claude が入れ子の起動を断る） | 内部（予告なく変えてよい） |
| `CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION` | ndf | string | — | 中継が起動する claude へ false を渡し、入力欄のサジェストを止める（人の入力と取り違えない） | 内部（予告なく変えてよい） |
| `CLAUDE_CODE_OAUTH_SCOPES` | user | string | — | Claude Code の OAuth のスコープ。登録済みアカウントと従量の接続の子へ渡さない | 内部（予告なく変えてよい） |
| `CLAUDE_CODE_OAUTH_TOKEN` | user | string | 秘密 | Claude Code の OAuth のトークン（ログインより上の認証の源）。登録済みアカウントと従量の接続の子へ渡さない | 内部（予告なく変えてよい） |
| `CLAUDE_CODE_PLUGIN_CACHE_DIR` | ndf | path | — | Claude Code のプラグインのキャッシュの保存先。登録済みアカウントの子には、親にあればその値、無ければ <共有の設定ディレクトリ>/plugins を入れる（アカウントを替えてもプラグインを入れ直さない） | 内部（予告なく変えてよい） |
| `CLAUDE_CODE_SESSION_ID` | runtime | string | — | Claude Code の今のセッションの ID。判定の記録と再起動の行をセッションに結ぶ。中継が起動するセッションへ親の ID を渡さない | 内部（予告なく変えてよい） |
| `CLAUDE_CODE_USE_ANTHROPIC_AWS` | user | flag | — | AWS 上の Claude Platformを選ぶ提供元の選択の変数（Claude Code が読む）。登録済みアカウントと従量の接続の子へ親の値を渡さない | 内部（予告なく変えてよい） |
| `CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD` | user | flag | — | Google Cloud 上の Claude Platformを選ぶ提供元の選択の変数（Claude Code が読む）。登録済みアカウントと従量の接続の子へ親の値を渡さない | 内部（予告なく変えてよい） |
| `CLAUDE_CODE_USE_BEDROCK` | user | flag | — | Amazon Bedrockを選ぶ提供元の選択の変数（Claude Code が読む）。登録済みアカウントと従量の接続の子へ親の値を渡さない | 内部（予告なく変えてよい） |
| `CLAUDE_CODE_USE_FOUNDRY` | user | flag | — | Microsoft Foundryを選ぶ提供元の選択の変数（Claude Code が読む）。登録済みアカウントと従量の接続の子へ親の値を渡さない | 内部（予告なく変えてよい） |
| `CLAUDE_CODE_USE_MANTLE` | user | flag | — | Bedrock の Mantleを選ぶ提供元の選択の変数（Claude Code が読む）。登録済みアカウントと従量の接続の子へ親の値を渡さない | 内部（予告なく変えてよい） |
| `CLAUDE_CODE_USE_VERTEX` | user | flag | — | Google Vertex AIを選ぶ提供元の選択の変数（Claude Code が読む）。登録済みアカウントと従量の接続の子へ親の値を渡さない | 内部（予告なく変えてよい） |
| `CLAUDE_CONFIG_DIR` | user | path | — | Claude Code の設定の保存先。中継の複製（ndf/relay.py）をこの下へ置く。無ければ ~/.claude。登録済みアカウントの子にはアカウントの設定ディレクトリの絶対パスを入れ、従量の接続と API キーの子には共有の設定ディレクトリを入れ直す | 内部（予告なく変えてよい） |
| `CLAUDE_PLUGIN_ROOT` | runtime | path | — | プラグインの根。無ければ読み手の位置から求める。ホストの推定では Claude Code のマーカー。bin/ndf が自分の根を入れるため、中継が起動するセッションへ渡さない（セッションの hook には Claude Code が入れる） | 内部（予告なく変えてよい） |
| `CODEX_HOME` | runtime | path | — | Codex の設定の保存先（ホストの推定と使用量の読み取り） | 内部（予告なく変えてよい） |
| `CODEX_PLUGIN_ROOT` | runtime | path | — | Codex のプラグインの根（ホストの推定） | 内部（予告なく変えてよい） |
| `GH_TOKEN` | user | string | 秘密 | GitHub の API のトークン | 公開 |
| `GITHUB_EVENT_PATH` | ci | path | — | GitHub Actions の事象の JSON。検査が PR 本文を読む | 内部（予告なく変えてよい） |
| `GITHUB_REPOSITORY` | ci | string | — | GitHub Actions が渡す owner/repo | 内部（予告なく変えてよい） |
| `GIT_ASKPASS` | user | path | — | git の認証の問い合わせ先。VS Code の窓の中の値は窓を閉じると固まるため、中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `GOOGLE_WORKSPACE_CLI_TOKEN` | user | string | 秘密 | gws（Google Workspace の CLI）へ渡すトークン。あれば gws の認証の確かめは、gws auth status が auth_method: none を返しても認証済みとする。無ければ gws auth status で決め、credential_source が none のときと、client_secret.json で auth_method が none のときだけ未認証とする | 内部（予告なく変えてよい） |
| `HOME` | user | path | — | 利用者のホームディレクトリ。worktree の書き込み先の判定が ~ で始まるパスを解くときと、学習の結果のキャッシュの保存先を求めるときに読む。無ければ ~ を空として解く | 内部（予告なく変えてよい） |
| `KIRO_AGENT` | runtime | string | — | Kiro のエージェントの名前（ホストの推定） | 内部（予告なく変えてよい） |
| `KIRO_PLUGIN_ROOT` | runtime | path | — | Kiro のプラグインの根（ホストの推定） | 内部（予告なく変えてよい） |
| `NDF_ACCOUNTS_DIR` | user | path | — | アカウントの登録の保存先（ndf relay account の登録のサブコマンド）。無ければ <共有の設定ディレクトリ>/ndf/accounts（旧いプラグインと同じ保存先） | 内部（予告なく変えてよい） |
| `NDF_ACCOUNT_GENERATION` | ndf | string | — | 登録済みアカウントの子で、起動を確かめた登録の世代（空文字は世代の無い登録）。子の ndf plan がリースと上限の後の替え先の判断に使う。従量の接続と API キーの子には渡さない | 内部（予告なく変えてよい） |
| `NDF_API_KEY_CHECK_MODEL` | user | string | — | API キーの確かめに使うモデル。無ければ claude-haiku-4-5 | 内部（予告なく変えてよい） |
| `NDF_CALLER_PATH` | ndf | string | — | bin/ndf が子へ自分の値を入れる前に写した、呼び出し元の PATH（無ければ写しを持たない）。bin/start.py が自分のパッケージを呼ぶ前に呼び出し元の値へ戻して消す（#4029）。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `NDF_CALLER_PLUGIN_ROOT` | ndf | path | — | bin/ndf が子へ自分の根を渡す前に写した、呼び出し元の CLAUDE_PLUGIN_ROOT（空なら無かった）。bin/start.py が自分のパッケージを呼ぶ前に CLAUDE_PLUGIN_ROOT へ戻して消す（#4029）。残っていれば（bin/ndf を通らずに打った子）ほかのプラグインの根のとき旧い状態の保存先を退避しない。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `NDF_CALLER_PYTHONPATH` | ndf | string | — | bin/ndf が子へ自分の値を入れる前に写した、呼び出し元の PYTHONPATH（無ければ写しを持たない）。bin/start.py が自分のパッケージを呼ぶ前に呼び出し元の値へ戻して消す（#4029）。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `NDF_CALLER_UV_PROJECT_ENVIRONMENT` | ndf | path | — | bin/ndf が子へ自分の値を入れる前に写した、呼び出し元の UV_PROJECT_ENVIRONMENT（無ければ写しを持たない）。bin/start.py が自分のパッケージを呼ぶ前に呼び出し元の値へ戻して消す（#4029）。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `NDF_CALLER_UV_RUN_RECURSION_DEPTH` | ndf | integer | — | bin/ndf が子へ自分の値を入れる前に写した、呼び出し元の UV_RUN_RECURSION_DEPTH（無ければ写しを持たない）。bin/start.py が自分のパッケージを呼ぶ前に呼び出し元の値へ戻して消す（#4029）。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `NDF_CALLER_VIRTUAL_ENV` | ndf | path | — | bin/ndf が子へ自分の値を入れる前に写した、呼び出し元の VIRTUAL_ENV（無ければ写しを持たない）。bin/start.py が自分のパッケージを呼ぶ前に呼び出し元の値へ戻して消す（#4029）。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `NDF_CLAUDE_ACCOUNT` | user | string | — | 子の claude を起動する登録済みのアカウントの名前 | 内部（予告なく変えてよい） |
| `NDF_CLAUDE_API_KEY_GENERATION` | ndf | integer | — | 子へ渡した API キーの世代。入れ子の監督が読む | 内部（予告なく変えてよい） |
| `NDF_CLAUDE_API_KEY_NAME` | ndf | string | — | 子へ渡した API キーの名前。入れ子の監督が読む | 内部（予告なく変えてよい） |
| `NDF_CLAUDE_KEY_ACCOUNT` | ndf | string | — | API キーで起動した子が、キーを外したときに戻る登録済みのアカウントの名前 | 内部（予告なく変えてよい） |
| `NDF_CONTEXT_DIR` | user | path | — | 再起動の方針の判定の記録と費用の見込みの保存先（ndf relay context）。無ければ旧い状態の根の context（旧いプラグインと同じ保存先） | 内部（予告なく変えてよい） |
| `NDF_CONTEXT_LIMIT` | user | integer | — | 文脈の量の運用上限（正の整数）。guard・中継のラッパー・ndf relay context resolve が読む。判断開始の値がこれを上回れば同じ値へ下げる。正の整数でなければ無視する | 内部（予告なく変えてよい） |
| `NDF_FACTORY_RUN` | service | flag | — | サービスの実行ジョブの中で動いているマーカー | 公開 |
| `NDF_HOOK_OFF` | user | string | — | 止める guard の名前をカンマでつなぐ。all で全部を止める。無ければ全部の guard が働く。閾値はこの変数で受けず、上限の設定から読む | 内部（予告なく変えてよい） |
| `NDF_ISSUE_REQUEST_FILE` | service | path | — | 作業のリポジトリと別の起票先への起票を、承認資料を書かずにリクエストの 1 行（{repo, title, body, labels} の JSON）として足すファイル。サービスが起票するかを決める。無ければ承認資料で止まる | 公開 |
| `NDF_LOCK_DIR` | user | path | — | 旧い NDF の Issue の本文を守る錠の保存先。無ければ ~/.claude/<名前>/locks。新しい NDF は読むだけで、状態の保存先の退避がこの保存先を含まないかを確かめる | 内部（予告なく変えてよい） |
| `NDF_LOOP_EXCLUDE` | service | string | — | 開発ループの選定から、そのループの 1 回だけ外す課題の番号（, か空白で区切る）。同じリポジトリの別の実行が持つ課題をサービスが渡す。無ければ外さない | 公開 |
| `NDF_MVV_STATE_DIR` | service | path | — | 判断の記録（ask.jsonl）と承認ゲートの変更要求の記録（gate-rejections.jsonl）の保存先。無ければ状態の保存先 | 公開 |
| `NDF_ORGANIZER_DIR` | ndf | path | — | 外のオーガナイザーとファイルでやり取りする保存先。あればこのセッションは外のオーガナイザーのレーン | 公開 |
| `NDF_ORGANIZER_LANE` | ndf | string | — | 外のオーガナイザーのレーンの名前。NDF_ORGANIZER_DIR と組で渡す | 公開 |
| `NDF_PROGRESS_DB` | service | string | 秘密 | ステートマシンの DB の URL（sqlite:/// か postgresql://。PostgreSQL の URL は認証情報を含むため秘密）。無ければチェックアウトの保存先の progress.sqlite。Factory は PostgreSQL の URL を入れる | 公開 |
| `NDF_RELAY_DIR` | ndf | path | — | 中継のラッパーが子のセッションへ渡すセグメントのやり取りの保存先。hook が次のセグメントのマーカーと問い合わせをここへ書く。無ければ中継の外 | 内部（予告なく変えてよい） |
| `NDF_RELAY_HUB_WAIT` | user | seconds | — | 頼む側がリレーハブの回答を待つ上限。無ければ上限の設定の値 | 内部（予告なく変えてよい） |
| `NDF_RELAY_MAX_NAMED` | user | integer | — | 名前つきのラッパーと稼働中のレーンの同時の数の上限。マシンの設定（ndf relay cap）が無いときに読み、無ければ上限の設定 relay.max-named。1 以上の整数 | 内部（予告なく変えてよい） |
| `NDF_RELAY_NAME` | user | string | — | 名前つきのラッパーの名前。無ければリレーハブが t1・t2… を付ける | 内部（予告なく変えてよい） |
| `NDF_RELAY_PIN` | ndf | string | — | 親の接続の識別。ラッパーが従量の接続と API キーの子にだけ入れ、ほかの子では外す。入れ子の ndf plan の claude -p は、読み直した設定の識別がこの値と同じときだけ起動する（値はキーを含まないハッシュか名前と世代） | 内部（予告なく変えてよい） |
| `NDF_RELAY_PLAN` | ndf | string | — | リレーハブが spawn --plan で起動したラッパーの並列の計画の名前。あればラッパーは子の AskUserQuestion の質問を ndf lanes question で計画のリクエストとして載せる | 内部（予告なく変えてよい） |
| `NDF_RELAY_ROLE` | ndf | string | — | リレーハブが spawn --role で起動したラッパーの役割 | 内部（予告なく変えてよい） |
| `NDF_RELAY_SPAWNED` | ndf | flag | — | リレーハブが tmux で起動したラッパーのマーカー | 内部（予告なく変えてよい） |
| `NDF_RELAY_SPIN` | user | seconds | — | 再起動ループとみなすセグメントの秒。0 で検出を切る。無ければ上限の設定の値 | 内部（予告なく変えてよい） |
| `NDF_RELAY_STOP_WAIT` | user | seconds | — | 名前つきの stop がラッパーの終わりを待つ上限。無ければ上限の設定の値 | 内部（予告なく変えてよい） |
| `NDF_RELAY_TMUX` | user | path | — | リレーハブが呼ぶ tmux の実行ファイル。無ければ PATH の tmux | 内部（予告なく変えてよい） |
| `NDF_RUNTIME_SESSION` | user | string | — | ランタイムのセッションを <pid>:<開始時刻> で指定する（テストと CI）。無ければ祖先のプロセスから探す | 内部（予告なく変えてよい） |
| `NDF_SHARED_CONFIG_DIR` | ndf | path | — | アカウントの子の中で、元の CLAUDE_CONFIG_DIR を持つ（空なら ~/.claude）。アカウントの登録の保存先を決める。登録済みアカウントの子には、親にあれば親の値、無ければ子へ渡す前の CLAUDE_CONFIG_DIR（無ければ空文字）を入れる | 内部（予告なく変えてよい） |
| `NDF_STATE_HOME` | user | path | — | 状態の保存先の根。保存先は <値>/<プラグイン名>（XDG_STATE_HOME と同じ読み方。入れ替えの前に旧い NDF と同じ値を渡されても、旧い保存先を退避しない）。無ければ XDG_STATE_HOME の下 | 公開 |
| `NDF_SUPERVISE_CLAUDE_FALLBACK` | user | string | 秘密 | 従量の接続の設定（KEY=VALUE を空白区切り）。定義されていれば保存した設定（metered.json）より先に読む | 内部（予告なく変えてよい） |
| `NDF_UPKEEP_STATE_DIR` | user | path | — | 棚卸しの状態（候補・順位・却下・見積・付け直しの前の状態・深さの記録）の保存先。無ければメインディレクトリの .ndf/backlog/state | 内部（予告なく変えてよい） |
| `NDF_USAGE_DIR` | user | path | — | 使用量の使用量ログ（usage.jsonl）の保存先。hook から書いてもスクリプトから書いても同じ保存先になる。無ければ状態の保存先の usage | 内部（予告なく変えてよい） |
| `NDF_VERSION` | service | string | — | サービスの実行ジョブが入れた NDF のバージョン（開発版は <version>@<コミットの SHA>） | 公開 |
| `PATH` | user | string | — | 外部 CLI（gh・claude・codex）の実行ファイルを探す道。下限の比べ（cli_floor）が、起動へ渡す環境に無ければこのプロセスの値で、起動と同じ実行ファイルを解く。どちらにも無ければ既定の道（os.defpath） | 内部（予告なく変えてよい） |
| `SLACK_BOT_TOKEN` | user | string | 秘密 | Slack の通知のトークン。無ければ通知しない | 内部（予告なく変えてよい） |
| `SLACK_CHANNEL_ID` | user | string | — | Slack の通知先のチャンネル | 内部（予告なく変えてよい） |
| `UV_PROJECT_ENVIRONMENT` | ndf | path | — | bin/ndf が NDF の依存の環境（<キャッシュ>/ndf/venv/<バージョン>）を指して uv へ渡す。中継が起動するセッションへ渡すと、セッションの中の uv run が NDF の環境を書き換えるため渡さない | 内部（予告なく変えてよい） |
| `UV_RUN_RECURSION_DEPTH` | runtime | integer | — | uv run が入れ子の深さとして入れる。中継が起動するセッションへ渡すと、セッションの中の uv run の深さを誤って数えるため渡さない | 内部（予告なく変えてよい） |
| `VIRTUAL_ENV` | runtime | path | — | uv run が NDF の依存の環境を指して入れる。中継が起動するセッションへ渡すと、セッションの中の uv と python が NDF の環境を使うため渡さない | 内部（予告なく変えてよい） |
| `VSCODE_GIT_ASKPASS_EXTRA_ARGS` | user | string | — | VS Code の git 認証の部品。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `VSCODE_GIT_ASKPASS_MAIN` | user | path | — | VS Code の git 認証の部品。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `VSCODE_GIT_ASKPASS_NODE` | user | path | — | VS Code の git 認証の部品。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `VSCODE_GIT_IPC_HANDLE` | user | path | — | VS Code の git 認証の通り道。中継が起動するセッションへ渡さない | 内部（予告なく変えてよい） |
| `XDG_BIN_HOME` | user | path | — | PATH のエントリポイント（ndf-relay）を置く保存先。無ければ ~/.local/bin | 内部（予告なく変えてよい） |
| `XDG_STATE_HOME` | user | path | — | XDG の状態の保存先。無ければ ~/.local/state | 内部（予告なく変えてよい） |
