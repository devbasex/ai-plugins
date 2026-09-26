# ラッパーでカットポイントを自動にする（Claude Code だけ）

**`/ndf:development-workflow` のカットポイントで人が行っていた「`/exit`・起動し直し・
次のコマンドの貼り付け」を、ラッパー（`scripts/relay.py`）が行う。** 人が入力するのは承認ゲートの答えだけになる。

例: ゲート 1 をまたいで実装へ進む。

1. 利用者がいつもどおり `claude` と打つ（`/ndf:install-wrapper` で入れた関数がラッパーを挟む）。その中で `/ndf:development-workflow #895` を入力する
2. conductor がゲート 1 で `AskUserQuestion` を出し、利用者が「承認」と答える
3. conductor が設計 Pull Request をマージし、最後の応答に次のブロックを出して応答を終える

   ```ndf-next
   /ndf:development-workflow #895
   ```

4. ラッパーがそのブロックを拾い、claude へ `/exit` を入力して終え、プラグインを更新し、
   区切りの 1 行（`── ndf-relay: 区間 2 ──`）を出して、同じ端末で `claude "<ブロックの中身>"` を起動する

ブロックの形は [context-window.md](context-window.md) の「新しい会話で戻す」が定める。

## 始め方

**ラッパーを使うかは利用者が決める。** claude の中で `/ndf:install-wrapper` を 1 度打つ。SessionStart hook は
シェルの設定を書かない。

| 置くもの | 中身 |
| --- | --- |
| コピーとコピーの版 | `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/relay.py` と `relay.version` |
| ラッパーの rc | `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/shellrc`。`function claude { ... }` を定義する。関数は呼んだ時点でコピーが在ればラッパーを、無ければ素の `claude` を起こす |
| 読み込みの 1 行 | `[ -f "$HOME/.claude/ndf/shellrc" ] && . "$HOME/.claude/ndf/shellrc"`。`DEVBASE_SHELLRC_DIR` がディレクトリを指せば `$DEVBASE_SHELLRC_DIR/ndf-relay.sh` に置き、無ければ `$SHELL` の設定（bash は `~/.bashrc`（macOS では `~/.bash_profile`）、zsh は `${ZDOTDIR:-~}/.zshrc`）の末尾へ囲み（`# >>> ndf relay >>>` 〜 `# <<< ndf relay <<<`）で足す。書く前に `<設定>.ndf-bak-<UTC の時刻>` へコピーを取る |

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
| `touch <作業ディレクトリ>/stop` | 同じ（1 つのラッパーだけ） |
| セッションの中で `/exit` か Ctrl-C を 2 回 | ラッパーも次のセッションを起動せずに終わる |

作業ディレクトリは `${XDG_STATE_HOME:-~/.local/state}/ndf/relay/<時刻>-<pid>-<乱数>/` で、
起動ごとに新しく作る。セッションの中では環境変数 `NDF_RELAY_DIR` が指す。

## 好きな時点で切り替える（`/ndf:restart`）

**ラッパーの下で `/ndf:restart [再開用のコマンド]` を打つと、カットポイントと同じ経路で切り替わる。**
claude が再開コマンドを `ndf-next` のブロックで出して応答を終え、ラッパーがアイドルを待ってから
`/exit` → プラグインの更新 → 起動を行う。プラグインの更新を反映したいとき・文脈を切りたいときに使う。
引数が無ければ、課題番号・worktree・Pull Request を差し込む定型の 1 文で作る。
ラッパーの外では、手順の 1 行と貼り付ける中身を示すだけで終わる。

## 承認ゲートを越えない守り

**ラッパーは、質問（`AskUserQuestion`）の答えを代わりに送らない。** 質問の表示中にラッパーが書いた `\r` は
選択肢 1 を決めるため、次のあいだは子の端末へ何も書かない。

| 条件 | 見分け方 |
| --- | --- |
| 質問が表示されている | `AskUserQuestion` の `PreToolUse` hook が作業ディレクトリへ `question` を作り、`PostToolUse` と次の Stop（`mark`）が消す |
| シグナルファイルを書いた後に質問が出た | `PreToolUse` hook が質問の時刻を `asked` へ書く。シグナルファイルの `written_at` 以後なら書かず、次のブロックの無い Stop がシグナルファイルを消す |
| シグナルファイルを書いた後に利用者が入力した・背景の処理を起動した | 会話の記録に、シグナルファイルより後の利用者の入力の行か、`run_in_background` が真の Tool の呼び出しの行がある。次の Stop がシグナルファイルを書き直すまで待つ |
| シグナルファイルを書いた後に応答が再開した（目標が未達の判定が無いとき） | 会話の記録に、シグナルファイルより後の `assistant` か `user` の行がある。次の Stop がシグナルファイルを書き直すまで待つ |

**シグナルファイルを書いた後は、切り替えを確定とする。** ブロックの無い Stop は、背景の処理が動いているときと
シグナルファイルを書いた後に質問が出たときだけシグナルファイルを消し、それ以外は前のシグナルファイルを残す。

- `/exit` と改行は 1 回の write で書く。確かめ直しと write は、質問の hook と同じロック（`question.lock`）の中で行い、書いた後も 1 秒持つ。**ロックを 3 秒以内に取れない質問の hook は、その質問を拒否する**（モデルが呼び直す）
- 書いた `/exit` が質問の答えの後に働く形になったら、質問が消えるまで SIGTERM までの秒を数えない。子が終わった後はシグナルファイルを読み直し、無ければ次のセッションを起動しない
- **守りが効くのは、ラッパーを起動し直した後からである**（`/exit` でラッパーを抜けて `claude` と打ち直した後）。動いているラッパーは古い版の `run` のまま動く

## 上限

| 上限 | 既定 | 変え方 | 超えたとき |
| --- | --- | --- | --- |
| 1 日の起動回数（全部のラッパーの合計） | 20 | `NDF_RELAY_MAX_STARTS` | 次のセッションを起動しない |
| 再起動ループ（セッションが 3 つ続けて、起動から 120 秒未満でカットポイントに達した） | — | — | 3 つ目のカットポイントで次のセッションを起動しない |
| アイドル（シグナルファイル・会話の記録・利用者の入力が動かない秒数。目標が未達の判定の後は会話の記録を数えない） | 5 | `NDF_RELAY_QUIET` | この秒数がたつまで `/exit` を入力しない |

**文脈の上限（`NDF_CONTEXT_LIMIT`、既定 200,000）はラッパーの下で強くなる。** ラッパーの直接の子の
conductor では、コンテキスト量の hook が工程へ入る起動を 1 度の通しなしに止め続ける。conductor は動いて
いる supervisor の報告を待ってから、引継ぎ文書を更新し、ブロックを出して終える
（[context-window.md](context-window.md) の「上限を超えたら hook が止める」）。背景の処理
（supervisor を含む）が動いているあいだの応答ではシグナルファイルを書かない。

## カットポイントの引継ぎ文書と ndf-next はスクリプトで作る

**conductor は引継ぎ文書の「今の会話の進み」の表と `ndf-next` の文面を手で書かない。**
`scripts/mission-state.py` が、ミッション状態ファイル `mission.json`（プランの出力先に置く）から作る。LLM は呼ばない。同じパスに別の形の JSON（`supervise.py new mission` の目録など）があると、`init` は上書きせずに終了コード 1 で止まる。

例: セッション 7 の実装 3 本と開発版・本番を流す。

1. プランを作った後に 1 度: `mission-state.py init /tmp/ndf-sv/r7/mission.json --name <ミッション> --milestone 26 --plan 実装=<plan.json> ... --plan 開発版=<plan.json> --plan 本番=<plan.json> --done <queue の done> --dev <開発版> --prod <本番> --goal @<雛形>`（雛形は次のセッションの `/goal` の文面。`{heading}`・`{dev}`・`{prod}`・`{milestone}`・`{name}`・`{issues}` を差し込む）
2. 承認ゲートで承認を得たら: `mission-state.py gate <mission.json> "関門 2" --what "本番 <版>"`
   - `pace: fast` のミッションは、1 に `--pace fast --milestone <M>`（MVV のコピー元。`--mvv <ファイル>` でもよい）を足し、利用者が
     `mvv.md` を承認した後に `mission-state.py gate <mission.json> MVV --what <要約>` を打つ。ゲート 1・2 の記録は、MVV 判定が
     通したときは `mvv-gate.py` が `--by mvv --verdict --reasons --log` 付きで書く（`status` の行は「MVV 判定」）
3. カットポイントでは次の順に呼ぶ:
   - `mission-state.py update <mission.json> [--done <done>] [--next <plan.json>=<行の「次」>]`（done と報告から状態・PR・秒・費用を埋める。何度走らせても同じ）
   - `mission-state.py render <mission.json> <引継ぎ文書> --section 今の会話の進み`（節の本文だけを置き換える。新しいセッションなら `--demote 前の会話の進み --heading "今の会話の進み（<時刻>）"` で今の節を下げて新しい節を足す）
   - `mission-state.py next <mission.json> --doc <引継ぎ文書> --replace`（「次に実行するコマンド」の節を置き換え、同じ `ndf-next` の囲みを最後の応答に出す）

**本番へのリリースの後は、カットポイントの 3 つを本番のキューと同じ背景の Bash で続けて流す。** ゲート 2 の承認から次のセッションの起動までに、conductor が組み立てる文は無くなる。conductor は完了の通知を受けたら `relay.py notice` のアナウンスと、出力の `ndf-next` の囲みをそのまま出す。ラッパーがプラグインを本番の版へ更新し、次のセッションを起動する。

```bash
O=/tmp/ndf-sv/r7; M=plugins/ndf/scripts/mission-state.py; DOC=issues/handoff-<名>.md
python3 plugins/ndf/scripts/supervise.py queue $O/plan-release-prod.json --done $O/done-release-prod.json >/dev/null &&
python3 plugins/ndf/scripts/supervise.py wait $O/done-release-prod.json --timeout 3600 >/dev/null &&
python3 $M update $O/mission.json >/dev/null &&
python3 $M render $O/mission.json $DOC --demote 前の会話の進み --heading "今の会話の進み（$(date -u +%Y-%m-%d\ %H:%M) UTC まで）" >/dev/null &&
python3 $M next $O/mission.json --doc $DOC --replace >/dev/null && sed -n '/^```ndf-next/,/^```$/p' $DOC
```

**次のセッションを止めずに続けるには、`/goal` の雛形を特定の課題に縛らない。** 雛形には「効果の順の残りから次のミッションを選び、同じパイプラインで流し、最後に同じ雛形で `ndf-next` を出す」ことを書く。止まるのは承認ゲート 2 つだけになる。

`mission-state.py status <mission.json>` は端末向けに 1 行ずつ（ミッション・状態・次）を出す。
節が見つからないときは文書を変えずに `"status": "stopped"` と理由を返す。

## 落ちたときの続け方

ラッパーが次のセッションを起動しないと決めたときは、`ndf-relay:` で始まる 1 行が画面に出る。

| 1 行 | 状態 | 続け方 |
| --- | --- | --- |
| `次の区間を起動しない（<理由>）。このまま続けるか、/exit して示されたコマンドを手で入力する` | 上限・再起動ループ・停止シグナルファイル。今のセッションは動いたまま | そのまま続けるか、`/exit` してから conductor のブロックの中身で `claude` を起動する |
| `次の区間を起動できない（<理由>）。次のコマンド:` の後に中身 | 更新か起動に失敗し、ラッパーは終わった（終了コード 2） | 表示された中身で `claude` を起動する |

## fork したセッションで進めたとき

例: ラッパーがセッション 2 を子 pid 1883（セッション ID `ba764798`）で起動し、利用者が別の入口から同じ会話を
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
| ラッパーの直接の子でない（fork したセッション・`bg-pty-host` の下・別の入口） | `ラッパーは元の会話（子 pid <child.pid>）しか見ていないため、この会話で出した ndf-next は自動では拾われない。元の会話へ戻って同じ ndf-next を出すか、元の会話を /exit してから claude を起動し、下の中身を最初の入力として貼り付ける` |

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
| `start` | セッションを起動した | `section`・`pid`・`command`・`from_session`・`plugin_version`（起動の直前に読んだ版）・`cwd`（シグナルファイルの作業ディレクトリが消えていたら `cwd_fallback` に元の値）・`carried`（2 つ目以降のセッションだけ。ブロックの中身の前に付けた引数） |
| `end` | セッションが終わった | `seconds`（起動からシグナルファイルを書くまで。シグナルファイルなしなら終わりまで）・`ended_by`（`mark` / `no-mark` / `sigterm` / `sigkill`） |
| `stop` | 次のセッションを起動しないと決めた | `reason`（`stop-file` / `max-starts` / `spin` / `update-failed` / `start-failed` / `error`） |

```bash
cat ~/.local/state/ndf/relay/*/log.jsonl | jq -c 'select(.event == "stop")'
```

入出力の契約と決定の理由は、ai-plugins の確定仕様 `docs/specifications/ndf-relay-segment-restart.md` にある。

## 付則: `/goal` を付けた場合

セッションの最初の入力に `/goal ` を付けると（`/goal /ndf:development-workflow #895`）、その入力は Claude Code の
目標になる。ラッパーは目標が未達のときだけ、付けない場合と違う動きをする。

| 項目 | 振る舞い |
| --- | --- |
| 次のセッションへ引き継ぐ | `ndf-next` のブロックの中身の先頭に `/goal ` を付ける（[context-window.md](context-window.md) の「新しい会話で戻す」） |
| 目標が未達のとき | 判定が止めを拒んで応答が続く。カットポイントでは未達が当然なので、ラッパーは切り替える。シグナルファイルを書いた後に目標の判定（`goal_status` の `met: false`）の行があれば、会話の記録の更新をアイドルに数えず、利用者の入力のアイドルだけを待つ。Esc を 1 回書いて応答を止め、1 秒おいて `/exit` を書く。利用者の入力・質問・背景の処理の起動があれば切り替えない |
| `/ndf:restart` の引数が無いとき | 目標の入力をそのまま再開コマンドにする |
