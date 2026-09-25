# 中継で区間の切れ目を自動にする（Claude Code だけ）

**`/ndf:development-workflow` の区間の切れ目で人が行っていた「`/exit`・起動し直し・
次のコマンドの貼り付け」を、中継（`scripts/relay.py`）が行う。** 人が入力するのは関門の答えだけになる。

例: 設計の関門をまたいで実装へ進む。

1. 利用者がいつもどおり `claude` と打つ（`/ndf:install-wrapper` で入れた関数が中継を挟む）。その中で `/ndf:development-workflow #895` を入力する
2. conductor が設計の関門で `AskUserQuestion` を出し、利用者が「承認」と答える
3. conductor が設計 Pull Request をマージし、最後の応答に次のブロックを出して応答を終える

   ```ndf-next
   /ndf:development-workflow #895
   ```

4. 中継がそのブロックを拾い、claude へ `/exit` を入力して終え、プラグインを更新し、
   区切りの 1 行（`── ndf-relay: 区間 2 ──`）を出して、同じ端末で `claude "<ブロックの中身>"` を起動する

ブロックの形は [context-window.md](context-window.md) の「新しい会話で戻す」が定める。

## 始め方

**中継を使うかは利用者が決める。** claude の中で `/ndf:install-wrapper` を 1 度打つ。SessionStart hook は
シェルの設定を書かない。

| 置くもの | 中身 |
| --- | --- |
| 写しと写しの版 | `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/relay.py` と `relay.version` |
| 中継の rc | `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/shellrc`。`function claude { ... }` を定義する。関数は呼んだ時点で写しが在れば中継を、無ければ素の `claude` を起こす |
| 読み込みの 1 行 | `[ -f "$HOME/.claude/ndf/shellrc" ] && . "$HOME/.claude/ndf/shellrc"`。`DEVBASE_SHELLRC_DIR` がディレクトリを指せば `$DEVBASE_SHELLRC_DIR/ndf-relay.sh` に置き、無ければ `$SHELL` の設定（bash は `~/.bashrc`、zsh は `${ZDOTDIR:-~}/.zshrc`）の末尾へ囲み（`# >>> ndf relay >>>` 〜 `# <<< ndf relay <<<`）で足す。書く前に `<設定>.ndf-bak-<UTC の時刻>` へ写しを取る |

- **次に開いたシェルから効く**
- 既に `claude` の alias か関数がある（bash では `~/.bash_aliases` も見る）・bash と zsh 以外のシェルでは足さず、自分で置く 1 行を示す
- `/ndf:install-wrapper status` で、読み込み先・囲みの形・写しの版を見られる
- **devbase では `~/.claude` が同じアカウントグループのコンテナで共有される。** 写しと中継の rc はコンテナを作り直しても残り、導入・取り外しの効果は同じグループの全コンテナに及ぶ

**写しは SessionStart hook が今の版に保つ。** 写しか 10.17.4〜10.17.6 の写し（`${XDG_DATA_HOME:-~/.local/share}/ndf/relay.py`）が
在れば、起動ごと（新しい会話・`-c`・`--resume`）に今のプラグインの `relay.py` で置き直す。無い写しは作らない。
写しの版が今のプラグインより新しければ置き直さない（同じ `~/.claude` を共有する古い版のコンテナが写しを
後退させないため）。

### 10.17.4〜10.17.6 から上げた利用者

10.17.4〜10.17.6 の SessionStart hook は、`~/.bashrc` か `~/.zshrc` へ alias の囲みを自動で足していた。次の版では:

- 起動したときに 1 度だけ「10.17.4〜10.17.6 が自動で足したもの。使い続けるなら何もしなくてよい。外すなら `/ndf:install-wrapper uninstall`」と知らせる
- 何もしなければ、その囲みの alias が指す 10.17.4〜10.17.6 の写し（hook が今の版で置き直す）で中継が動く
- `/ndf:install-wrapper` を打つと、囲みの中だけを読み込みの 1 行へ置き換える
- `NDF_RELAY_AUTO` は意味を失った（hook が導入しないため）。置いたままでも害は無い

## 外し方・戻し方

| したいこと | 手段 |
| --- | --- |
| 外す | `/ndf:install-wrapper uninstall`。`~/.bashrc` と `~/.zshrc` の囲みを外し（バックアップの後。囲みの外は変えない）、読み込み先のファイル・中継の rc・写し・10.17.4〜10.17.6 の写しを消す。開いているシェルでは `unset -f claude`（10.17.4〜10.17.6 の囲みなら `unalias claude`）で外れる |
| 手で外す | 囲みの行を消し、`~/.claude/ndf/` の `relay.py`・`relay.version`・`shellrc` を消す |
| 過去の版へ戻した | `/ndf:install-wrapper` を打ち直す（明示の導入は版を比べずに今の版を置く） |
| `/ndf:install-wrapper` を持たない 10.17.6 以前へ戻す | **戻す前に** `/ndf:install-wrapper uninstall` を打つ。戻した後なら囲みと `~/.claude/ndf/` を手で消す |

## 中継を挟まない起動

`claude` と打っても、次の起動は中継を挟まずに本物の claude をそのまま exec する（素通し）。
振る舞いも終了コードも直接打ったのと同じである。

| 起動 | 例 |
| --- | --- |
| 非対話 | `claude -p ...`・`--help`・`--version` |
| 副命令 | `claude mcp ...`・`claude doctor` など |
| 端末でない | パイプ・リダイレクト |
| 中継の下 | conductor が Bash から起こす `claude -p` |
| 止めた | `NDF_RELAY=0 claude` |

擬似端末を作れない環境（Windows）と、導入済みのプラグインの一覧（`plugin list --json`）から ndf の
名前と版を読めないときは、`ndf-relay: 中継を始めない（<理由>）…` の 1 行を出してから素通しする。

`ndf-next` のブロックを出さない普段の利用では、印が書かれないので中継は何もせず、claude を終えると同じ
終了コードでシェルへ戻る。

## 止め方

| 手段 | 効き目 |
| --- | --- |
| `python3 ~/.claude/ndf/relay.py stop`（別の端末から） | 動いている中継すべてに停止の印を置く。次の印を受けても `/exit` を入力しない。動いている中継が無ければ終了コード 1 |
| `touch <作業ディレクトリ>/stop` | 同じ（1 つの中継だけ） |
| 区間の中で `/exit` か Ctrl-C を 2 回 | 中継も次の区間を起動せずに終わる |

作業ディレクトリは `${XDG_STATE_HOME:-~/.local/state}/ndf/relay/<時刻>-<pid>-<乱数>/` で、
起動ごとに新しく作る。区間の中では環境変数 `NDF_RELAY_DIR` が指す。

## 好きな時点で切り替える（`/ndf:restart`）

**中継の下で `/ndf:restart [再開用のコマンド]` を打つと、区間の切れ目と同じ経路で切り替わる。**
claude が再開用のコマンドを `ndf-next` のブロックで出して応答を終え、中継が静まりを待ってから
`/exit` → プラグインの更新 → 起動を行う。プラグインの更新を反映したいとき・文脈を切りたいときに使う。
引数が無ければ、課題番号・作業ツリー・Pull Request を差し込む定型の 1 文で作る。
中継の外では、手順の 1 行と貼り付ける中身を示すだけで終わる。

## 関門を越えない守り

**中継は、質問（`AskUserQuestion`）の答えを代わりに送らない。** 質問の表示中に中継が書いた `\r` は
選択肢 1 を決めるため、次のあいだは子の端末へ何も書かない。

| 条件 | 見分け方 |
| --- | --- |
| 質問が表示されている | `AskUserQuestion` の `PreToolUse` hook が作業ディレクトリへ `question` を作り、`PostToolUse` と次の Stop（`mark`）が消す |
| 印の後に質問が出た | `PreToolUse` hook が質問の時刻を `asked` へ書く。印の `written_at` 以後なら書かず、次のブロックの無い Stop が印を消す |
| 印の後に利用者が入力した・背景の処理を起動した | 会話の記録に、印より後の利用者の入力の行か、`run_in_background` が真の Tool の呼び出しの行がある。次の Stop が印を書き直すまで待つ |
| 印の後に応答が再開した（目標が未達の判定が無いとき） | 会話の記録に、印より後の `assistant` か `user` の行がある。次の Stop が印を書き直すまで待つ |

**印を書いた後は、切り替えを確定とする。** ブロックの無い Stop は、背景の処理が動いているときと
印の後に質問が出たときだけ印を消し、それ以外は前の印を残す。

- `/exit` と改行は 1 回の write で書く。確かめ直しと write は、質問の hook と同じロック（`question.lock`）の中で行い、書いた後も 1 秒持つ。**ロックを 3 秒以内に取れない質問の hook は、その質問を拒否する**（モデルが呼び直す）
- 書いた `/exit` が質問の答えの後に働く形になったら、質問が消えるまで SIGTERM までの秒を数えない。子が終わった後は印を読み直し、無ければ次の区間を起動しない
- **守りが効くのは、中継を起動し直した後からである**（`/exit` で中継を抜けて `claude` と打ち直した後）。動いている中継は古い版の `run` のまま動く

## 上限

| 上限 | 既定 | 変え方 | 超えたとき |
| --- | --- | --- | --- |
| 1 日の起動回数（全部の中継の合計） | 20 | `NDF_RELAY_MAX_STARTS` | 次の区間を起動しない |
| 空回り（区間が 3 つ続けて、起動から 120 秒未満で切れ目に達した） | — | — | 3 つ目の切れ目で次の区間を起動しない |
| 静まり（印・会話の記録・利用者の入力が動かない秒数。目標が未達の判定の後は会話の記録を数えない） | 5 | `NDF_RELAY_QUIET` | この秒数がたつまで `/exit` を入力しない |

**文脈の上限（`NDF_CONTEXT_LIMIT`、既定 200,000）は中継の下で強くなる。** 中継の直接の子の
conductor では、文脈量の hook が工程へ入る起動を 1 度の通しなしに止め続ける。conductor は動いて
いる supervisor の報告を待ってから、引継ぎ文書を更新し、ブロックを出して終える
（[context-window.md](context-window.md) の「上限を超えたら hook が止める」）。背景の処理
（supervisor を含む）が動いているあいだの応答では印を書かない。

## 落ちたときの続け方

中継が次の区間を起動しないと決めたときは、`ndf-relay:` で始まる 1 行が画面に出る。

| 1 行 | 状態 | 続け方 |
| --- | --- | --- |
| `次の区間を起動しない（<理由>）。このまま続けるか、/exit して示されたコマンドを手で入力する` | 上限・空回り・停止の印。今の区間は動いたまま | そのまま続けるか、`/exit` してから conductor のブロックの中身で `claude` を起動する |
| `次の区間を起動できない（<理由>）。次のコマンド:` の後に中身 | 更新か起動に失敗し、中継は終わった（終了コード 2） | 表示された中身で `claude` を起動する |

## fork したセッションで進めたとき

例: 中継が区間 2 を子 pid 1883（セッション `ba764798`）で起動し、利用者が別の入口から同じ会話を
続けたため、会話が fork したセッション `38cf7ead`（`claude daemon` → `bg-pty-host … --fork-session`
の下。中継の直接の子ではない）で進んだ（#1016）。

**中継は `child.pid` の会話だけを見る。** fork した側にも `NDF_RELAY_DIR` と `NDF_RELAY_DEPTH` は
受け継がれるが、hook を呼んだ claude が中継の直接の子でないため、次のとおり中継の外と判定される。

- `relay.py is-child` は 1 を返し、`relay.py notice` の 1 行目は `outside` になる
- Stop hook の `mark` は印を書かない。`ndf-next` のブロックを出しても区間は切り替わらない

**告知は `relay.py notice` の 2 行目が原因と対処を書く。** 外である理由ごとに文が変わる。

| 理由 | 2 行目 |
| --- | --- |
| `NDF_RELAY_DIR` が無い | `/exit してから claude を起動し、下の中身を最初の入力として貼り付ける（/ndf:install-wrapper で中継を入れると自動になる）` |
| 中継が動いていない | `中継は既に終わっている。/exit してから claude を起動し、下の中身を最初の入力として貼り付ける` |
| 中継の直接の子でない（fork したセッション・`bg-pty-host` の下・別の入口） | `中継は元の会話（子 pid <child.pid>）しか見ていないため、この会話で出した ndf-next は自動では拾われない。元の会話へ戻って同じ ndf-next を出すか、元の会話を /exit してから claude を起動し、下の中身を最初の入力として貼り付ける` |

**続け方は 2 つある。** 元の会話（中継の画面）へ戻って同じ `ndf-next` のブロックを出せば、
中継が切り替える。戻れないときは、元の会話を `/exit` してから `claude` を起動し、ブロックの
中身を最初の入力として貼り付ける。fork した側の会話を中継が自動で拾う形は #928 で扱う。

## 区間をまたいで設定を保つ

**2 つ目以降の区間は、最初の `claude` に付けた起動の方針の引数を先頭に付け、ブロックの
中身で起動する。** alias（devbase の `--dangerously-skip-permissions` など）や手で付けた
`--model`・`--permission-mode`・`--settings`・`--add-dir`・`--mcp-config`・`--plugin-dir` は
すべての区間に効く。**会話ごと・区間ごとの引数は引き継がない。** 最初のプロンプト・`--` 以後・
`-c`/`--continue`・`-r`/`--resume`・`--session-id`・`--fork-session`・`--from-pr`・`--teleport`・
`--cloud`・`-n`/`--name`・`--bg`/`--background`・`--tmux`・`-w`/`--worktree` である。次の区間は新しい会話を
始めるので、付けると前の会話へ戻るか、同じ ID を 2 度使うか、区間が端末に出ない。
引数と値の区切りは `claude` と同じ規則で読み、知らない選択肢は値ごと引き継ぐ。

## 記録の読み方

作業ディレクトリの `log.jsonl` に 1 行ずつ残る。

| `event` | いつ | 主なキー |
| --- | --- | --- |
| `start` | 区間を起動した | `section`・`pid`・`command`・`from_session`・`plugin_version`（起動の直前に読んだ版）・`cwd`（印の作業ディレクトリが消えていたら `cwd_fallback` に元の値）・`carried`（2 つ目以降の区間だけ。ブロックの中身の前に付けた引数） |
| `end` | 区間が終わった | `seconds`（起動から印まで。印なしなら終わりまで）・`ended_by`（`mark` / `no-mark` / `sigterm` / `sigkill`） |
| `stop` | 次の区間を起動しないと決めた | `reason`（`stop-file` / `max-starts` / `spin` / `update-failed` / `start-failed` / `error`） |

```bash
cat ~/.local/state/ndf/relay/*/log.jsonl | jq -c 'select(.event == "stop")'
```

入出力の契約と決定の理由は、ai-plugins の確定仕様 `docs/specifications/ndf-relay-segment-restart.md` にある。

## 付則: `/goal` を付けた場合

区間の最初の入力に `/goal ` を付けると（`/goal /ndf:development-workflow #895`）、その入力は Claude Code の
目標になる。中継は目標が未達のときだけ、付けない場合と違う動きをする。

| 項目 | 振る舞い |
| --- | --- |
| 次の区間へ引き継ぐ | `ndf-next` のブロックの中身の先頭に `/goal ` を付ける（[context-window.md](context-window.md) の「新しい会話で戻す」） |
| 目標が未達のとき | 判定が止めを拒んで応答が続く。区間の切れ目では未達が当然なので、中継は切り替える。印の後に目標の判定（`goal_status` の `met: false`）の行があれば、会話の記録の更新を静まりに数えず、利用者の入力の静まりだけを待つ。Esc を 1 回書いて応答を止め、1 秒おいて `/exit` を書く。利用者の入力・質問・背景の処理の起動があれば切り替えない |
| `/ndf:restart` の引数が無いとき | 定型の 1 文ではなく、目標の入力をそのまま再開用のコマンドにする |
