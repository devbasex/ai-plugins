# ラッパーでカットポイントを自動にする（Claude Code だけ）

**`/ndf:development-workflow` のカットポイントで人が行っていた「`/exit`・起動し直し・
次のコマンドの貼り付け」を、ラッパー（`scripts/relay.py`）が行う。** 人が入力するのは承認ゲートの答えだけになる。

例: 設計承認ゲートをまたいで実装へ進む。

1. 利用者がいつもどおり `claude` と打つ（`/ndf:install-wrapper` で入れた関数がラッパーを挟む）。その中で `/ndf:development-workflow #895` を入力する
2. conductor が設計承認ゲートで `AskUserQuestion`（`metadata.source` は `ndf-gate:1`）を出し、利用者が「承認」と答える
3. conductor が設計 Pull Request をマージし、最後の応答に次のブロックを出して応答を終える

   ```ndf-next
   /ndf:development-workflow #895
   ```

4. ラッパーがそのブロックを拾い、claude へ `/exit` を入力して終え、プラグインを更新し、
   区切りの 1 行（`── ndf-relay: 区間 2 ──`）を出して、同じ端末で `claude "<ブロックの中身>"` を起動する

ブロックの形は [context-window.md](context-window.md) の「新しい会話で戻す」が定める。

**目次:** [始め方](#始め方) / [外し方・戻し方](#外し方戻し方) / [ラッパーを挟まない起動](#ラッパーを挟まない起動) / [止め方](#止め方) / [好きな時点で切り替える（/ndf:restart）](#好きな時点で切り替えるndfrestart) / [ラッパーの入れ替え](#ラッパーの入れ替え) / [承認ゲートを越えない守り](#承認ゲートを越えない守り) / [上限](#上限) / [利用上限でアカウントを替えて続ける](#利用上限でアカウントを替えて続ける) / [カットポイントの引継ぎ文書と ndf-next はスクリプトで作る](#カットポイントの引継ぎ文書と-ndf-next-はスクリプトで作る) / [落ちたときの続け方](#落ちたときの続け方) / [fork したセッションで進めたとき](#fork-したセッションで進めたとき) / [セッションをまたいで設定を保つ](#セッションをまたいで設定を保つ) / [記録の読み方](#記録の読み方) / [付則: /goal を付けた場合](#付則-goal-を付けた場合)

## 始め方

**ラッパーを使うかは利用者が決める。** claude の中で `/ndf:install-wrapper` を 1 度打つ。SessionStart hook は
シェルの設定を書かない。

| 置くもの | 中身 |
| --- | --- |
| コピーとコピーの版 | `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/relay.py` と `relay.version` |
| ラッパーの rc | `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/shellrc`。`function claude { ... }` を定義する。関数は呼んだ時点でコピーが在ればラッパーを、無ければ素の `claude` を起こす |
| 読み込みの 1 行 | `[ -f "$HOME/.claude/ndf/shellrc" ] && . "$HOME/.claude/ndf/shellrc"`。`DEVBASE_SHELLRC_DIR` がディレクトリを指せば `$DEVBASE_SHELLRC_DIR/ndf-relay.sh` に置き、無ければ `$SHELL` の設定（bash は `~/.bashrc`（macOS では `~/.bash_profile`）、zsh は `${ZDOTDIR:-~}/.zshrc`）の末尾へブロック（`# >>> ndf relay >>>` 〜 `# <<< ndf relay <<<`）で足す。書く前に `<設定>.ndf-bak-<UTC の時刻>` へコピーを取る |

- **次に開いたシェルから効く**
- 既に `claude` の alias か関数がある（bash では `~/.bash_aliases` とログインシェルの設定 `~/.bash_profile`・`~/.bash_login`・`~/.profile` も見る）・bash と zsh 以外のシェルでは足さず、自分で置く 1 行を示す
- **macOS の bash では `~/.bash_profile` へ足す。** macOS の端末は新しいウィンドウをログインシェルで開き、ログインシェルの bash は `~/.bashrc` を読まない。`~/.bash_profile` が無く `~/.bash_login` か `~/.profile` があるときは、作ると元のファイルが読まれなくなるため足さず、自分で置く 1 行を示す
- `/ndf:install-wrapper status` で、読み込み先・管理ブロックの形・コピーの版と、今のセッションがラッパー経由かを見られる
- **devbase では `~/.claude` が同じアカウントグループのコンテナで共有される。** コピーとラッパーの rc はコンテナを作り直しても残り、導入・取り外しの効果は同じグループの全コンテナに及ぶ

**コピーは SessionStart hook が今の版に保つ。** コピーか 10.17.4〜10.17.6 のコピー（`${XDG_DATA_HOME:-~/.local/share}/ndf/relay.py`）が
在れば、起動ごと（新しい会話・`-c`・`--resume`）に今のプラグインの `relay.py` で置き直す。無いコピーは作らない。
コピーの版が今のプラグインより新しければ置き直さない（同じ `~/.claude` を共有する古い版のコンテナがコピーを
後退させないため）。

### 10.17.4〜10.17.6 から上げた利用者

10.17.4〜10.17.6 の SessionStart hook は、`~/.bashrc` か `~/.zshrc` へ alias の管理ブロックを自動で足していた。次の版では:

- 起動したときに 1 度だけ「10.17.4〜10.17.6 が自動で足したもの。使い続けるなら何もしなくてよい。外すなら `/ndf:install-wrapper uninstall`」と知らせる
- 何もしなければ、その管理ブロックの alias が指す 10.17.4〜10.17.6 のコピー（hook が今の版で置き直す）でラッパーが動く
- `/ndf:install-wrapper` を打つと、管理ブロックの中だけを読み込みの 1 行へ置き換える
- `NDF_RELAY_AUTO` は意味を失った（hook が導入しないため）。置いたままでも害は無い

## 外し方・戻し方

| したいこと | 手段 |
| --- | --- |
| 外す | `/ndf:install-wrapper uninstall`。`~/.bashrc`・`~/.bash_profile`・`~/.zshrc` の管理ブロックを外し（バックアップの後。管理ブロックの外は変えない）、読み込み先のファイル・ラッパーの rc・コピー・10.17.4〜10.17.6 のコピーを消す。開いているシェルでは `unset -f claude`（10.17.4〜10.17.6 の管理ブロックなら `unalias claude`）で外れる |
| 手で外す | 管理ブロックの行を消し、`~/.claude/ndf/` の `relay.py`・`relay.version`・`shellrc` を消す |
| 過去の版へ戻した | `/ndf:install-wrapper` を打ち直す（明示の導入は版を比べずに今の版を置く） |
| `/ndf:install-wrapper` を持たない版（10.17.6 まで）へ戻す | **戻す前に** `/ndf:install-wrapper uninstall` を打つ。戻した後なら管理ブロックと `~/.claude/ndf/` を手で消す |

## ラッパーを挟まない起動

`claude` と打っても、次の起動はラッパーを挟まずに本物の claude をそのまま exec する（パススルー）。
振る舞いも終了コードも直接打ったのと同じである。

| 起動 | 例 |
| --- | --- |
| 非対話 | `claude -p ...`・`--help`・`--version` |
| 副命令 | `claude mcp ...`・`claude doctor` など |
| 端末でない | パイプ・リダイレクト |
| ラッパーの下 | conductor が Bash から起こす `claude -p` |
| 止めた | `NDF_RELAY=0 claude` |

擬似端末を作れない環境（Windows）と、導入済みのプラグインの一覧（`plugin list --json`）から ndf の
名前と版を読めないときは、`ndf-relay: ラッパーを始めない（<理由>）…` の 1 行を出してからパススルーする。

`ndf-next` のブロックを出さない普段の利用では、シグナルファイルが書かれないのでラッパーは何もせず、claude を終えると同じ
終了コードでシェルへ戻る。

## 止め方

| 手段 | 効き目 |
| --- | --- |
| `python3 ~/.claude/ndf/relay.py stop`（別の端末から） | 動いているラッパーすべてに停止シグナルファイルを置く。次のシグナルファイルを受けても `/exit` を入力しない。動いているラッパーが無ければ終了コード 1 |
| `python3 ~/.claude/ndf/relay.py stop <名前>` | 名前つきのラッパー 1 本だけを止める（停止シグナルファイルを置き、tmux のセッションを終える。下の「複数のループを並べる」） |
| `touch <作業ディレクトリ>/stop` | 同じ（1 つのラッパーだけ） |
| セッションの中で `/exit` か Ctrl-C を 2 回 | ラッパーも次のセッションを起動せずに終わる |

副命令の一覧は `relay.py --help` が、1 つの副命令の書式と役割は `relay.py <副命令> --help` が出す（副命令は動かさない。`run --help` は claude の help）。

作業ディレクトリは `${XDG_STATE_HOME:-~/.local/state}/ndf/relay/<時刻>-<pid>-<乱数>/` で、
起動ごとに新しく作る。セッションの中では環境変数 `NDF_RELAY_DIR` が指す。

## 複数のループを並べる（リレーハブ）

例: 同じコンテナで `l20261031`・`lroot`・`lflow` の 3 本の開発ループを回す。1 つ目の端末で `claude` を打つと、
今どおり前景でラッパーが動き、そのラッパーが**リレーハブ**を兼ねる。2 つ目の端末で `NDF_RELAY_NAME=lroot claude` を打つと、
リレーハブが tmux の中に名前つきのラッパー `lroot` を起動し、その端末が結び付く。3 本目は conductor が応答に起動のブロックを置いて始める:

````markdown
```ndf-spawn lflow
/goal .ndf/handoff/loop-lflow.md を読んで続ける
```
````

`relay.py ls` で 3 本の状態と承認待ちが 1 つの表に出て、`relay.py stop lroot` で 1 本だけを止められる。

並列開発をまとめて始めるときは、起動のブロックを並べずに並列の計画を書き（[issue-plan-strategy の parallel-plan.md](../../issue-plan-strategy/references/parallel-plan.md)）、
`--parallel <計画名>` でオーガナイザーに起動させる（[parallel.md](parallel.md)）。リリースがレーンどうしでぶつからない。

| 操作 | 効き目 |
| --- | --- |
| リレーハブが居るときに端末で `claude` | リレーハブに起動を頼み、tmux の名前つきのラッパーへ結び付く。名前は `NDF_RELAY_NAME`、無ければ `t2`・`t3`… |
| `relay.py spawn --name <名前> [--json] -- <最初の入力>` | 端末の結び付いていない名前つきのラッパーを起動する。最初の区間の claude が入力を受け取る |
| 応答の起動のブロック（情報文字列 `ndf-spawn <名前>`、中身が最初の入力） | Stop hook がリレーハブへ依頼を置く。置いた区間は止まらない。1 つの応答に複数置ける |
| `relay.py ls [--json]` | 名前・状態（`running` / `awaiting_approval` / `stopped`）・区間・費用（記録が無ければ空）・最後に動いた時刻・アカウント・端末の結び付き・リレーハブかを出す。`--json` は 1 行の JSON |
| `relay.py attach <名前>` | 打った端末をそのラッパーへ結び付ける。端末を閉じてもラッパーは動き続ける |
| `relay.py restart <名前> [--json]` | 1 本を止め、最後の区間の入力と作業ディレクトリで起動し直す |
| `relay.py stop <名前> [--json]` | 1 本だけを止める。名前を付けなければ今どおり全部 |

- **同時に動かす数の上限**は `NDF_RELAY_MAX_NAMED`（既定 3）。リレーハブ・名前つき・名前の無いラッパーを合わせて数え、
  AI の起動（`relay.py spawn`・`relay.py restart`・起動のブロック）が超えるときは理由（上限と動いている名前）を出して終了コード 3 で断る。
  名前が重なる・名前の形（`^[a-z0-9][a-z0-9_-]{0,31}$`）が違うときも 3
- **端末で打った `claude`（人の起動）は上限で断らず、上限にも数えない。** 上限を超えていたら
  `ndf-relay: 同時に動かすラッパーの上限 3 を超えて起動する（動いている: …）` の 1 行を出して起動し、リレーハブの記録
  （`hub-log.jsonl`）の `started` の行に `over_limit` を残す。一覧（`relay.py ls`）には出る。`relay.py restart` で起動し直すと AI の起動として数える
- **リレーハブが落ちても**名前つきのラッパーは止まらない（tmux のサーバが親）。次に端末で打った `claude` がリレーハブになる。
  リレーハブが `NDF_RELAY_HUB_WAIT`（既定 10 秒）答えなければ、2 本目は 1 行出して今の前景の起動になる
- **起動の確かめ**: リレーハブは `spawn`・`restart` でセッションを作った後、`NDF_RELAY_LAUNCH_CHECK`（既定 2 秒）待ってセッションが残っているかを見る。
  消えていれば登録せず `tmux-failed`・1 を返す
- **tmux が PATH に無い**（`NDF_RELAY_TMUX` で実行ファイルを替えられる）ときは、2 本目は 1 行出して今の前景の起動になる
- **承認待ちの知らせ**は各ラッパーの待ち通知が出し、名前つきのラッパーなら `ラッパー: <名前>（relay.py attach <名前>）` の 1 行が付く
- tmux は専用のソケット `-L ndf-relay` だけを使い、利用者の tmux サーバに触れない。リレーハブの置き場は
  `${XDG_STATE_HOME:-~/.local/state}/ndf/relay/hub/`（登録簿 `registry.json`・起動の依頼 `requests/`・結果 `results/`・
  記録 `hub-log.jsonl`。所有者だけが読み書きできる）

## 好きな時点で切り替える（`/ndf:restart`）

**ラッパーの下で `/ndf:restart [再開用のコマンド]` を打つと、カットポイントと同じ経路で切り替わる。**
claude が再開コマンドを `ndf-next` のブロックで出して応答を終え、ラッパーがアイドルを待ってから
`/exit` → プラグインの更新 → 起動を行う。プラグインの更新を反映したいとき・文脈を切りたいときに使う。ラッパーは最初の起動でも更新してから起動する（更新に失敗したら今の版で起動する）。
引数が無ければ、課題番号・worktree・Pull Request を差し込む定型の 1 文で作る。
ラッパーの外では、手順の 1 行と貼り付ける中身を示すだけで終わる。

## ラッパーの入れ替え

**ラッパー自身も、カットポイントで更新後の版へ入れ替わる。** 子の claude が終わり、プラグインを更新した後、
次のセッションを起動する前に、ラッパーは次の順で進む。

1. 更新後のプラグインの導入先のファイルから、使うべきバージョンディレクトリの名前（`relay-<版>-<digest>`）を計算する。
   動いているものと同じなら何もせず次のセッションを起動する
2. 違えば、更新後のプラグインの `relay.py startup`（SessionStart hook と同じ処理）を子プロセスで打ち、
   バージョンディレクトリと `relay.current` を置かせる（環境の用意を含む。上限は `NDF_RELAY_PREPARE_TIMEOUT`、既定 600 秒）
3. 作業ディレクトリへ入れ替えの申し送り（`handover.json`。次のセッションの起動の入力とセッションの番号など。
   認証情報を持たず、権限は 0600）を書き、新しいバージョンディレクトリの環境の python でランチャーを `os.execve` する
4. 新しい版のラッパーが申し送りを読んで消し、同じ PID・同じ作業ディレクトリのまま次のセッションを起動する。
   セッションの番号は続く

入れ替えないのは次のときである。

| 場合 | 振る舞い |
| --- | --- |
| プラグインのキャッシュの `scripts/relay.py` を直接打って起動したラッパー | 判定しない |
| 入れ替えの仕組みを持たない版（10.17.51 まで）で動いているラッパー | 入れ替わらない。`/exit` してから `claude` を 1 度打ち直すと、以後の更新で入れ替わる |
| 用意・入れ替えに失敗した（下の `reexec_skipped` の `reason`） | 今の版のまま次のセッションを起動し、画面に 1 行出す |
| 入れ替えた後に申し送りを読めない | 次のセッションを起動せず、手で打つ次のコマンドを示して止まる |

動いているラッパーの版は、`/ndf:install-wrapper status` の「動いているラッパー」の行と、`log.jsonl` の
`start` の行の `relay_version_dir` で見られる。

## 承認ゲートを越えない守り

**ラッパーは、質問（`AskUserQuestion`）の答えを代わりに送らない。** 質問の表示中にラッパーが書いた `\r` は
選択肢 1 を決めるため、次のあいだは子の端末へ何も書かない。

| 条件 | 見分け方 |
| --- | --- |
| 質問が表示されている | `AskUserQuestion` の `PreToolUse` hook が作業ディレクトリへ `question` を作り、`PostToolUse` と次の Stop（`mark`）が消す |
| シグナルファイルを書いた後に質問が出た | `PreToolUse` hook が質問の時刻を `asked` へ書く。シグナルファイルの `written_at` 以後なら書かず、次のブロックの無い Stop がシグナルファイルを消す |
| シグナルファイルを書いた後に利用者が入力した・バックグラウンドの処理を起動した | 会話の記録に、シグナルファイルより後の利用者の入力の行か、`run_in_background` が真の Tool の呼び出しの行がある。次の Stop がシグナルファイルを書き直すまで待つ |
| シグナルファイルを書いた後に応答が再開した（目標が未達の判定が無いとき） | 会話の記録に、シグナルファイルより後の `assistant` か `user` の行がある。次の Stop がシグナルファイルを書き直すまで待つ |

**本文で承認を待って終えた応答は、1 度止めて `AskUserQuestion` へ戻す。** ラッパーの直接の子の応答が `AskUserQuestion` を呼ばずに
承認待ちの文（判定は `lib/wait_notice.py` の `classify_reply_wait` で、回答待ちか承認待ちに当たる文。Slack の待ち通知の `classify_text` より狭く、操作の案内は待ちに数えない）で
終わると、Stop hook（`mark`）が Stop を止め、承認ゲートの問いなら `metadata.source` に `ndf-gate:<番号>` を付けて
`AskUserQuestion` で出し直し、区分（[asking.md](asking.md)）に当たらない問いなら自分で決めて記録し続けるよう伝える。`/goal` の判定は本文の待ちでは
止まらないためである。次のときは止めない。

- 止めた直後の、同じセッションの次の Stop（作業ディレクトリの `wait-held.json` で見分ける。`stop_hook_active` は `/goal` の続きで常に真になるため使わない）
- 応答の中で質問を出した（`question` が在る・`asked` の時刻が前の Stop より後）
- 応答が `ndf-next` のブロックを含む・シグナルファイルが保留中（在り、その後に質問が出ていない）
- ラッパーの外の会話と `claude -p` の子

止めるたびに `log.jsonl` へ `prose_wait` の行を足す。判定と記録が失敗しても Stop は止めない。

**シグナルファイルを書いた後は、切り替えを確定とする。** ブロックの無い Stop は、バックグラウンドの処理が動いているときと
シグナルファイルを書いた後に質問が出たときだけシグナルファイルを消し、それ以外は前のシグナルファイルを残す。

- `/exit` と改行は 1 回の write で書く。確かめ直しと write は、質問の hook と同じロック（`question.lock`）の中で行い、書いた後も 1 秒持つ。**ロックを 3 秒以内に取れない質問の hook は、その質問を拒否する**（モデルが呼び直す）
- 書いた `/exit` が質問の答えの後に働く形になったら、質問が消えるまで SIGTERM までの秒を数えない。子が終わった後はシグナルファイルを読み直し、無ければ次のセッションを起動しない
- 守りを変えた版は、ラッパーが次のカットポイントでその版へ入れ替わった後（「ラッパーの入れ替え」）から効く。入れ替わらない場合は、`/exit` でラッパーを抜けて `claude` と打ち直した後から効く

## 上限

| 上限 | 既定 | 変え方 | 超えたとき |
| --- | --- | --- | --- |
| 再起動ループ（セッションが 3 つ続けて、起動から 120 秒未満でカットポイントに達した） | — | — | 3 つ目のカットポイントで次のセッションを起動しない |
| アイドル（シグナルファイル・会話の記録・利用者の入力が動かない秒数。目標が未達の判定の後は会話の記録を数えない） | 5 | `NDF_RELAY_QUIET` | この秒数がたつまで `/exit` を入力しない |

**起動の回数は数えない。** 再起動ループの検出だけが暴走を止める。`NDF_RELAY_SPIN=0` を設定した環境には暴走止めが無い。
環境変数 `NDF_RELAY_MAX_STARTS` は読まない（設定しても誤りにならない）。

**文脈の上限（`NDF_CONTEXT_LIMIT`、既定 200,000）はラッパーの下で強くなる。** ラッパーの直接の子の
conductor では、コンテキスト量の hook が工程へ入る起動を 1 度の通しなしに止め続ける。conductor は動いて
いる supervisor の報告を待ってから、引継ぎ文書を更新し、ブロックを出して終える
（[context-window.md](context-window.md) の「上限を超えたら hook が止める」）。バックグラウンドの処理
（supervisor を含む）が動いているあいだの応答ではシグナルファイルを書かない。

## 利用上限でアカウントを替えて続ける

claude のアカウントを 2 つ以上登録しておくと、利用上限で止まらずに別のアカウントで続く。登録・指定・切り替えの条件と設定は [relay-accounts.md](relay-accounts.md) にある。

## カットポイントの引継ぎ文書と ndf-next はスクリプトで作る

**conductor は引継ぎ文書の「今の会話の進み」の表と `ndf-next` の文面を手で書かない。**
引継ぎ文書の置き場（メインディレクトリの `.ndf/handoff/<名>.md`）・名・節の形・規則は [handoff.md](handoff.md) にある。
`scripts/sprint-state.py` が、スプリント状態ファイル `sprint-state.json`（プランの出力先に置く）から作る。LLM は呼ばない。同じパスに別の形の JSON（前の版の `supervise.py new sprint` が書いた目録など）があると、`init` は上書きせずに終了コード 1 で止まる。

例: セッション 7 の実装 3 本と開発版・本番を流す。

1. プランを作った後に 1 度: `sprint-state.py init ~/.local/state/ndf/sv/r7/sprint-state.json --name <スプリント> --milestone 26 --plan 実装=<plan.json> ... --plan 開発版=<plan.json> --plan 本番=<plan.json> --done <queue の done> --dev <開発版> --prod <本番> --goal @<テンプレート>`（テンプレートは次のセッションの `/goal` の文面。`{heading}`・`{dev}`・`{prod}`・`{milestone}`・`{name}`・`{issues}` を差し込む）。**テンプレートには「`.ndf/handoff/sprint-{name}.md` の続きから」**（スプリント名が `sprint-<名>` の形に合わなければ `.ndf/handoff/milestone-{milestone}.md`）**を差し込みの語で必ず書く。** テンプレートは次のスプリントでも使うため、文書のパスを文字のまま書かない
2. 承認ゲートで承認を得たら: `sprint-state.py gate <sprint-state.json> "リリース承認ゲート" --what "本番 <版>"`
   - `pace: fast` と `pace: auto` のスプリントは、1 に `--pace <値> --milestone <M>`（MVV のコピー元。`--mvv <ファイル>` でもよい）を足し、利用者が
     `mvv.md` を承認した後に `sprint-state.py gate <sprint-state.json> MVV --what <要約>` を打つ。設計承認ゲート・リリース承認ゲートの記録は、MVV 判定が
     通したときは `mvv-gate.py` が `--by mvv --verdict --reasons --log` 付きで書く（`status` の行は「MVV 判定」）
3. カットポイントでは、conductor が引継ぎ文書の「現在地」と「次にやること」を書き直してから、次の順に呼ぶ（`<名>` は `sprint-<スプリント名>` など。`<引継ぎ文書>` は `handoff.py path <名>` の `path`）:
   - `context-policy.py record --event 引継ぎ開始 --work "<課題番号の並び>"`（文書を書き始める前に打つ。引継ぎの区間の起点になる。hook に止められて切るときは hook が打ったので打たない。[context-window.md](context-window.md) の「判定と再起動を記録する」）
   - `handoff.py init <名> --title <表示名>`（無ければテンプレートから作る。あれば変えない）
   - `sprint-state.py update <sprint-state.json> [--done <done>] [--next <plan.json>=<行の「次」>]`（done と報告から状態・PR・秒・費用を埋める。何度走らせても同じ）
   - `sprint-state.py render <sprint-state.json> <引継ぎ文書> --section 今の会話の進み`（節の本文だけを置き換える。新しいセッションなら `--demote 前の会話の進み --heading "今の会話の進み（<時刻>）"` で今の節を下げて新しい節を足す）
   - `sprint-state.py get <sprint-state.json> [--field <JSON Pointer>]`（読むだけ。計画ごとの状態と、最初の終わっていない計画の次の欄を `next` に返す。状態ファイルをその場の Python の 1 行で読み直さない）
   - `sprint-state.py next <sprint-state.json> --doc <引継ぎ文書> --replace`（「次に実行するコマンド」の節を置き換え、同じ `ndf-next` のブロックを最後の応答に出す）
   - `handoff.py check <名> --trim`（「前の会話の進み」を履歴へ移し、節の形と 300 行の上限を確かめる。1 か 5 のときの扱いは [handoff.md](handoff.md) の「作る・更新する」）

**本番へのリリースの後は、カットポイントの呼び出しを本番のキューと同じバックグラウンドの Bash で続けて流す。** 流す前に conductor が `handoff.py init` を打ち、本番の後の「現在地」と「次にやること」を文書へ書く（パイプラインの中では書き直せない）。`check` が 1 か 5 ならパイプラインはブロックを出さずに止まり、conductor が完了の通知で終了コードを受けて扱ってからブロックを出す。リリース承認ゲートの承認から次のセッションの起動までに、conductor が組み立てる文は無くなる。conductor は完了の通知を受けたら `relay.py notice` のアナウンスと、出力の `ndf-next` のブロックをそのまま出す。ラッパーがプラグインを本番の版へ更新し、次のセッションを起動する。本番のキューは、目録（`$O/sprint.json`）の `本番` のステージの `command` に `--done` を足したものである（例の `9` はステージの番号）。

```bash
O=~/.local/state/ndf/sv/r7; M=plugins/ndf/scripts/sprint-state.py; H=plugins/ndf/scripts/handoff.py; N=sprint-<スプリント名>
DOC=$(python3 $H path $N | python3 plugins/ndf/scripts/lib/step_result.py pick --field /items/0/path --raw)
python3 $H init $N --title "<表示名>" >/dev/null &&
python3 plugins/ndf/scripts/supervise.py queue $O/9-release-prod.json --max 3 --done $O/done-9.json >/dev/null &&
python3 plugins/ndf/scripts/supervise.py wait $O/done-9.json --timeout 3600 >/dev/null &&
python3 $M update $O/sprint-state.json >/dev/null &&
python3 $M render $O/sprint-state.json $DOC --demote 前の会話の進み --heading "今の会話の進み（$(date -u +%Y-%m-%d\ %H:%M) UTC まで）" >/dev/null &&
python3 $M next $O/sprint-state.json --doc $DOC --replace >/dev/null &&
python3 $H check $N --trim >/dev/null && sed -n '/^```ndf-next/,/^```$/p' $DOC
```

**次のセッションを止めずに続けるには、`/goal` のテンプレートを特定の課題に縛らない。** テンプレートには「効果の順の残りから次のスプリントを選び、同じパイプラインで流し、最後に同じテンプレートで `ndf-next` を出す」ことを書く。止まるのは 2 つの承認ゲートだけになる。

**再開したセッションの「復元完了」は hook が打つ。** 引継ぎ文書で始まったセッションで、エントリポイントの Skill を除く最初の工程を起動したときに 1 回だけ書き、復元の区間の終わりになる（[context-window.md](context-window.md) の「判定と再起動を記録する」）。conductor は打たない。hook が止めた後の「引継ぎ開始」も、ラッパーの下では hook が打つ。上の 3 の `record --event 引継ぎ開始` は、カットポイントで自分で切るときに打つ。`sprint-state.py next` は `ndf-next` を出すたびに判定「再起動」を記録する（理由は `--reason`。既定は直前の拒否の理由、無ければ現行方式はカットポイント・試行方式は手動）。

`sprint-state.py status <sprint-state.json>` は端末向けに 1 行ずつ（スプリント・状態・次）を出す。
節が見つからないときは文書を変えずに `"status": "stopped"` と理由を返す。

## 落ちたときの続け方

ラッパーが次のセッションを起動しないと決めたときは、`ndf-relay:` で始まる 1 行が画面に出る。

| 1 行 | 状態 | 続け方 |
| --- | --- | --- |
| `次の区間を起動しない（<理由>）。このまま続けるか、/exit して示されたコマンドを手で入力する` | 上限・再起動ループ・停止シグナルファイル。今のセッションは動いたまま | そのまま続けるか、`/exit` してから conductor のブロックの中身で `claude` を起動する |
| `次の区間を起動できない（<理由>）。次のコマンド:` の後に中身 | 更新か起動に失敗し、ラッパーは終わった（終了コード 2） | 表示された中身で `claude` を起動する |

## fork したセッションで進めたとき

例: ラッパーがセッション 2 を子 pid 1883（セッション ID `ba764798`）で起動し、利用者が別のエントリポイントから同じ会話を
続けたため、会話が fork したセッション `38cf7ead`（`claude daemon` → `bg-pty-host … --fork-session`
の下にあり、親はラッパーでない）で進んだ。

**ラッパーは `child.pid` の会話だけを見る。** fork した側にも `NDF_RELAY_DIR` と `NDF_RELAY_DEPTH` は
受け継がれるが、hook を呼んだ claude がラッパーの直接の子でないため、次のとおりラッパーの外と判定される。

- `relay.py is-child` は 1 を返し、`relay.py notice` の 1 行目は `outside` になる
- Stop hook の `mark` はシグナルファイルを書かない。`ndf-next` のブロックを出してもセッションは切り替わらない

**アナウンスは `relay.py notice` の 2 行目が原因と対処を書く。** 外である理由ごとに文が変わる。

| 理由 | 2 行目 |
| --- | --- |
| `NDF_RELAY_DIR` が無い | `/exit してから claude を起動し、下の中身を最初の入力として貼り付ける（/ndf:install-wrapper でラッパーを入れると自動になる）` |
| ラッパーが動いていない | `ラッパーは既に終わっている。/exit してから claude を起動し、下の中身を最初の入力として貼り付ける` |
| ラッパーの直接の子でない（fork したセッション・`bg-pty-host` の下・別のエントリポイント） | `ラッパーは元の会話（子 pid <child.pid>）しか見ていないため、この会話で出した ndf-next は自動では拾われない。元の会話へ戻って同じ ndf-next を出すか、元の会話を /exit してから claude を起動し、下の中身を最初の入力として貼り付ける` |

**続け方は 2 つある。** 元の会話（ラッパーの画面）へ戻って同じ `ndf-next` のブロックを出せば、
ラッパーが切り替える。戻れないときは、元の会話を `/exit` してから `claude` を起動し、ブロックの
中身を最初の入力として貼り付ける。fork した側の会話をラッパーは自動で拾わない。

## セッションをまたいで設定を保つ

**2 つ目以降のセッションは、最初の `claude` に付けた起動オプションを先頭に付け、ブロックの
中身で起動する。** alias（devbase の `--dangerously-skip-permissions` など）や手で付けた
`--model`・`--permission-mode`・`--settings`・`--add-dir`・`--mcp-config`・`--plugin-dir` は
すべてのセッションに効く。**会話ごと・セッションごとの引数は引き継がない。** 最初のプロンプト・`--` 以後・
`-c`/`--continue`・`-r`/`--resume`・`--session-id`・`--fork-session`・`--from-pr`・`--teleport`・
`--cloud`・`-n`/`--name`・`--bg`/`--background`・`--tmux`・`-w`/`--worktree` である。次のセッションは新しい会話を
始めるので、付けると前の会話へ戻るか、同じ ID を 2 度使うか、セッションが端末に出ない。
引数と値の区切りは `claude` と同じ規則で読み、知らない選択肢は値ごと引き継ぐ。

## 記録の読み方

作業ディレクトリの `log.jsonl` に 1 行ずつ残る。

| `event` | いつ | 主なキー |
| --- | --- | --- |
| `start` | セッションを起動した | `section`・`pid`・`command`・`from_session`・`plugin_version`（起動の直前に読んだ版）・`cwd`（シグナルファイルの作業ディレクトリが消えていたら `cwd_fallback` に元の値）・`carried`（2 つ目以降のセッションだけ。ブロックの中身の前に付けた引数）・`account`（登録が 2 つ以上のときだけ。起動したアカウント。`metered` は従量の接続）・`relay_version_dir`（動いているラッパーのバージョンディレクトリ。プラグインのキャッシュから起動したら null。キーが無ければ入れ替えの仕組みを持たない版） |
| `reexec` | ラッパーが更新後の版へ入れ替わった（入れ替えた後の版が、次の `start` の前に書く） | `section`（終わったセッション）・`from`・`to`（バージョンディレクトリの名前）・`prepare_seconds`（用意に掛かった秒）・`seconds`（申し送りを書いてから入れ替わるまで） |
| `reexec_skipped` | 版が違ったが入れ替えなかった | `section`・`reason`（`startup-failed` / `not-placed` / `unreadable` / `no-env` / `no-handover` / `handover-write` / `exec-failed`）・`from`・`to`（分かれば）・`detail`・`seconds` |
| `account` | アカウントを替えた・すべて上限で替えなかった | `section`・`reason`（`five_hour` / `seven_day` / `spend` / `unknown` / `auth` / `threshold` / `recovered` / `pinned` など）・`from`・`to`（替えなかったら null）・`earliest`（最も早く戻るアカウントと時刻）・`keys`（従量の接続へ移ったときの変数の名前）・`usage`（閾値のときの使用率） |
| `metered_invalid` | 区間 1 の起動で、保存した従量の接続の宣言が壊れていた（宣言なしとして扱う） | `section`・`detail`（理由の 1 行） |
| `end` | セッションが終わった | `seconds`（起動からシグナルファイルを書くまで。シグナルファイルなしなら終わりまで）・`ended_by`（`mark` / `no-mark` / `sigterm` / `sigkill`） |
| `prose_wait` | 本文で承認を待って終えた応答の Stop を止めた（「承認ゲートを越えない守り」） | `section`・`kind`（`承認待ち` / `回答待ち`）。本文と抜粋は書かない |
| `stop` | 次のセッションを起動しないと決めた | `reason`（`stop-file` / `spin` / `update-failed` / `start-failed` / `error` / `handover`（入れ替えた後に申し送りを読めない）） |

```bash
cat ~/.local/state/ndf/relay/*/log.jsonl | jq -c 'select(.event == "stop")'
cat ~/.local/state/ndf/relay/*/log.jsonl | jq -c 'select(.event == "prose_wait")'   # 止めた回数
```

入出力の契約と決定の理由は、ai-plugins の確定仕様 `docs/specifications/ndf-relay-segment-restart.md` にある。

## 付則: `/goal` を付けた場合

セッションの最初の入力に `/goal ` を付けると（`/goal /ndf:development-workflow #895`）、その入力は Claude Code の
目標になる。ラッパーは目標が未達のときだけ、付けない場合と違う動きをする。

| 項目 | 振る舞い |
| --- | --- |
| 次のセッションへ引き継ぐ | `ndf-next` のブロックの中身の先頭に `/goal ` を付ける（[context-window.md](context-window.md) の「新しい会話で戻す」） |
| 目標が未達のとき | 判定が止めを拒んで応答が続く。カットポイントでは目標は未達のままなので、ラッパーは切り替える。シグナルファイルを書いた後に目標の判定（`goal_status` の `met: false`）の行があれば、会話の記録の更新をアイドルに数えず、利用者の入力のアイドルだけを待つ。Esc を 1 回書いて応答を止め、1 秒おいて `/exit` を書く。利用者の入力・質問・バックグラウンドの処理の起動があれば切り替えない |
| `/ndf:restart` の引数が無いとき | 目標の入力をそのまま再開コマンドにする |
| 次のセッションで条件の Skill を読み込む | 条件の先頭の語が `/<プラグイン>:<Skill>` のとき（`/goal /ndf:development-workflow #895`）、Claude Code の `/goal` はその Skill を展開しない。そこで `UserPromptSubmit` hook（`hook.py goal-skill`）が、作業の最初の Tool の呼び出しとして Skill ツールでその Skill を条件の残りを引数に読み込むよう案内する。最初の入力と目標の条件は前のセッションと同じ文面のままで、カットポイントの `ndf-next`・利用上限での切り替え（`--resume`）・`/ndf:restart` の再開コマンドのどれでも、ラッパーの外でも同じに働く。Skill が導入されていなければ、読み込まずに目標の条件のまま続ける |
