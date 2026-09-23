# 中継で区間の切れ目を自動にする（Claude Code だけ）

**`/goal /ndf:development-workflow` の区間の切れ目で人が行っていた「`/exit`・起動し直し・
次のコマンドの貼り付け」を、中継（`scripts/relay.py`）が行う。** 人が入力するのは関門の答えだけになる。

例: 設計の関門をまたいで実装へ進む。

1. 利用者がいつもどおり `claude` と打つ（alias が中継を挟む）。その中で `/goal /ndf:development-workflow #895` を入力する
2. conductor が設計の関門で `AskUserQuestion` を出し、利用者が「承認」と答える
3. conductor が設計 Pull Request をマージし、最後の応答に次のブロックを出して応答を終える

   ```ndf-next
   /goal /ndf:development-workflow #895
   ```

4. 中継がそのブロックを拾い、claude へ `/exit` を入力して終え、プラグインを更新し、
   区切りの 1 行（`── ndf-relay: 区間 2 ──`）を出して、同じ端末で `claude "<ブロックの中身>"` を起動する

ブロックの形は [context-window.md](context-window.md) の「新しい会話で戻す」が定める。

## 始め方

**利用者の手作業は無い。** ndf の入った Claude Code を起動すると、SessionStart hook が
`relay.py install` を呼び、次の 2 つを行う。

| すること | 中身 |
| --- | --- |
| 中継を置き直す | `${XDG_DATA_HOME:-~/.local/share}/ndf/relay.py` へ写す。中身が同じなら書かない。更新の後の最初の起動で新しい版になる |
| alias を 1 度だけ足す | `$SHELL` が bash なら `~/.bashrc`、zsh なら `${ZDOTDIR:-~}/.zshrc` の末尾へ、印のついた囲み（`# >>> ndf relay >>>` 〜 `# <<< ndf relay <<<`）で `alias claude='python3 "${XDG_DATA_HOME:-$HOME/.local/share}/ndf/relay.py" run'` を足す。書く前に `<設定>.ndf-bak-<UTC の時刻>` へ写しを取る。足したときだけ 1 行で知らせる |

- **次に開いたシェルから効く。** 今のシェルでは `source ~/.bashrc` で効く
- 既に `claude` の alias か関数がある（bash では `~/.bash_aliases` も見る）と足さず、1 度だけ案内する。中継を使うなら、案内の alias の 1 行を自分で置く
- **戻すには囲みを消す。** 消した後は足し直さない（`${XDG_STATE_HOME:-~/.local/state}/ndf/relay/rc-added` の記録で見分ける。足し直したいときはその行を消す）
- bash と zsh 以外のシェルでは足さない
- **`NDF_RELAY_AUTO=0` で `install` 全体が何もしなくなる**

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

`/goal` を使わない普段の利用では、印が書かれないので中継は何もせず、claude を終えると同じ
終了コードでシェルへ戻る。

## 止め方

| 手段 | 効き目 |
| --- | --- |
| `python3 ~/.local/share/ndf/relay.py stop`（別の端末から） | 動いている中継すべてに停止の印を置く。次の印を受けても `/exit` を入力しない。動いている中継が無ければ終了コード 1 |
| `touch <作業ディレクトリ>/stop` | 同じ（1 つの中継だけ） |
| 区間の中で `/exit` か Ctrl-C を 2 回 | 中継も次の区間を起動せずに終わる |

作業ディレクトリは `${XDG_STATE_HOME:-~/.local/state}/ndf/relay/<時刻>-<pid>-<乱数>/` で、
起動ごとに新しく作る。区間の中では環境変数 `NDF_RELAY_DIR` が指す。

## 上限

| 上限 | 既定 | 変え方 | 超えたとき |
| --- | --- | --- | --- |
| 1 日の起動回数（全部の中継の合計） | 20 | `NDF_RELAY_MAX_STARTS` | 次の区間を起動しない |
| 空回り（区間が 3 つ続けて、起動から 120 秒未満で切れ目に達した） | — | — | 3 つ目の切れ目で次の区間を起動しない |
| 静まり（印・会話の記録・利用者の入力が動かない秒数） | 15 | `NDF_RELAY_QUIET` | この秒数がたつまで `/exit` を入力しない。`/goal` の目標がある区間では、印の後の目標の判定の記録も待つ |

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

## 区間をまたいで設定を保つ

**2 つ目以降の区間は、ブロックの中身だけで起動する。** 最初の `claude` に付けた引数
（`--model`・`--resume`・`-c` など）は引き継がない。捨てた会話へ戻らないためである。
モデルなどを保つときは、設定か環境変数（`ANTHROPIC_MODEL` など）で与える。

## 記録の読み方

作業ディレクトリの `log.jsonl` に 1 行ずつ残る。

| `event` | いつ | 主なキー |
| --- | --- | --- |
| `start` | 区間を起動した | `section`・`pid`・`command`・`from_session`・`plugin_version`（起動の直前に読んだ版）・`cwd`（印の作業ディレクトリが消えていたら `cwd_fallback` に元の値） |
| `end` | 区間が終わった | `seconds`（起動から印まで。印なしなら終わりまで）・`ended_by`（`mark` / `no-mark` / `sigterm` / `sigkill`） |
| `stop` | 次の区間を起動しないと決めた | `reason`（`stop-file` / `max-starts` / `spin` / `update-failed` / `start-failed` / `error`） |

```bash
cat ~/.local/state/ndf/relay/*/log.jsonl | jq -c 'select(.event == "stop")'
```

設計と決定の理由は ai-plugins の課題 #895 の設計文書にある。
