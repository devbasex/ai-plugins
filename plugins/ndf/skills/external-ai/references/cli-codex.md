# Codex CLI 固有の手順

共通手順（プロンプトの書き出し、バックグラウンド起動、3 段階のフォールバック回収、待機間隔、
プロンプトテンプレート）は [../SKILL.md](../SKILL.md) を参照。本ファイルは Codex CLI に固有の差分だけを扱う。

## インストールとログイン

```bash
which codex && codex --version
codex login          # 初回のみ。未ログインだと即座に失敗する

# 未インストールの場合
npm install -g @openai/codex
codex exec --help
```

## サンドボックス制約（最重要）

Codex の既定サンドボックスは `bubblewrap (bwrap)` に依存する。次の環境では bwrap が動作せず、
`exec` で実行するシェルコマンドがすべて失敗する。

- **WSL2**（カーネルで `unprivileged_userns_clone` が無効）
- **一部の devcontainer / Docker 環境**（user namespace 非対応）

安全装置の引数は自分で書かず、`ndf delegate-run` に入れさせる。`ndf delegate-run` は名前空間を作れるか（`unshare -Ur true` が通るか）を
確かめ、作れれば `-s workspace-write`（`--use review` なら `-s read-only`）で起動する。作れない環境では、起動の設定
（`.ndf/launch.json` の `permissions.unsandboxed`）で codex を承認したときだけ `--dangerously-bypass-approvals-and-sandbox` で起動し、
承認が無ければ起動せずに止まる。

**判断基準**: 承認するのは、Docker / devcontainer / VM / CI ランナー等で外部的に隔離済みの環境に限る。
ホスト直接実行でコードを全書き換えされたくない場合は承認せず、下記の bwrap 代替で名前空間を作れるようにする。

### bwrap 代替の有効化（ホスト直接実行時）

```bash
# Debian/Ubuntu 系でホスト user namespace を有効化
sudo sysctl kernel.unprivileged_userns_clone=1

# 永続化
echo 'kernel.unprivileged_userns_clone=1' | sudo tee /etc/sysctl.d/00-local-userns.conf
```

## 起動コマンド

次の形で起動する（`timeout <秒>` で包む。秒は SKILL.md の「共通の実行手順」）。作業ディレクトリへ移ってから打ち、プロンプトは **stdin へ流す**。
`'{args}'` は `codex exec` のすぐ後に置く。

```bash
cd <workdir>
ndf delegate-run --runtime codex -- codex exec '{args}' --config model_reasoning_effort=medium \
  [--model M] < <prompt> > <stem>-stdout.log 2> <stem>-err.log
```

| オプション | 用途 |
|---|---|
| `'{args}'` | `ndf delegate-run` が、安全装置の引数と作業ディレクトリの指定（`-C <今のディレクトリ>`）を入れる位置。`codex exec` のすぐ後でないと拒まれる。`-s` や `-C` を自分で書いても拒まれる |
| `--config model_reasoning_effort=medium` | `high` だと思考へ偏り最終 message を返さない頻度が上がるため、既定で `medium` を推奨 |
| `-o <file>` | 最終 message を、呼び出した側が決めたファイルへ書かせる。プロンプトに出力先を書けないときに使う。最終 message が無ければ中身は得られない |
| `--json` | JSON Lines でイベントを出力。`event.type=assistant_message` を grep すれば確実に本文を取れる |
| `codex resume` | 長時間ジョブで親エージェントが再起動した場合にセッションを再開する |

## 実際に動いたモデル

`--model` を渡さなくても、実際に動いたモデルを取れる（#759。codex-cli 0.159.3 で実測）。
`codex exec` は標準エラーの見出しに `session id: <ID>` を出し、セッションの記録
`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*-<ID>.jsonl`（`CODEX_HOME` が無ければ `~/.codex`）の
`turn_context` の行に `"model"` を残す。ID で記録を名指しして読み、
ID が無いときは起動の開始〜終了の時刻範囲と作業ディレクトリで 1 件に絞る。
候補が 2 件以上なら取らない（`ambiguous`）。見出しの `model:` は起動の設定の表示で、実測には使わない。

## 出力ストリーム

| ストリーム | 内容 |
|---|---|
| **stdout** | 最終 assistant message のみ（Markdown 本文）。**空になることがある**（下記） |
| **stderr** | プロンプトのエコー + 実行コマンドと結果 + 思考プロセス + `^tokens used$` sentinel。数千行になる |

## 最終出力をファイル経由で保証する（必須）

Codex CLI（特に `gpt-5-codex` / 高 `reasoning_effort`）は、長時間調査の末に
**最終 assistant message を返さずセッションを終えることがある**。このとき stdout は空のまま、
stderr のイベントログにはコードを読んだ痕跡だけが残る（`^tokens used$` は出ているのに stdout が空）。

**根本対策**: プロンプトに「最終結果は指定ファイルへ書き出すこと」を必須化する。
Codex は最終 message を返さなくても `apply_patch` でファイルを作成できるため、ファイル経由なら確実に回収できる。

```markdown
## 出力先（必須）

最終的なレビュー / 調査結果を以下のファイルに **必ず書き出してください**:

`--output-file` に渡したパス

書き出しは `apply_patch` で新規ファイル作成してください。
**stdout への出力だけでは不十分です**（セッション終了で失われる場合があるため）。
書き出し後、念のため stdout にも同じ内容を出力してください（冪等で問題ありません）。
```

補助策として `--config model_reasoning_effort=medium` へ下げる、`--json` でイベントを採取する、
プロンプト末尾に「tool 呼び出しのみで終了しないこと」を明記する、の 3 つを併用する。

回収は **ファイル → stdout → stderr** の順で行う。

読むだけの用途（`--use review`）では、結果ファイルに頼らず、プロンプトで結果を最終応答に返すよう指示し、呼び出し元で stdout を回収する。
サンドボックスを利用できる場合は `ndf delegate-run` が `-s read-only` で起動するので、Codex は結果ファイルも worktree も書けない。
サンドボックスを利用できない環境では、利用者がサンドボックスなしの起動を承認していれば、`--use review` でもサンドボックスなしで起動し、Codex はファイルを変更できる。
そのため、どちらの場合もレビューのプロンプトに「ファイルを変更しないこと」と書く。

終了コード 124 で止まった起動は、本文があっても途中までの結果である。本文の有無を見る前に、SKILL.md の「共通の実行手順」の「上限に当たったとき」に従う。
124 でなく、結果ファイルにも stdout にも本文が無ければ、渡し直すかどうかを、終了コードと stderr から次の表で決める。
渡し直すと NDF と Codex の起動が 1 回ずつ増え、レビュー 1 回分の推論の費用がかかるので、渡し直せば結果が得られると分かるときだけ渡し直す。
表は上の行から順に照らし、最初に当てはまった行の手だけを取る。

| 順 | 終了コードと stderr | 原因 | 渡し直すか | 次の手 |
|---|---|---|---|---|
| 1 | 終了コード 3 で、stderr に `ndf delegate-run` の理由がある | Codex を起動していない | 渡し直さない | 理由に従う（承認が要るなら呼び出し元へ返す） |
| 2 | stderr に認証・利用上限・サンドボックスの失敗がある | Codex では解けない | 渡し直さない | SKILL.md の「共通の実行手順」の表と、下の Q2・Q4 に従う |
| 3 | stderr に `^tokens used$` がある（セッションは最後まで進んだ） | 最終応答を返さずにセッションが終わった（上の現象） | 同じ内容で 1 回だけ | 上の補助策を足して渡し直す。CLI の引数で `--config model_reasoning_effort=medium` を指定し、プロンプトの末尾に「tool 呼び出しのみで終了しないこと」を追記する。書き込みを伴う作業なら出力先の指示もプロンプトに足す。読むだけのレビューは `--use review` のまま渡し直し、書き込みできる用途に変えない。サンドボックスを利用できない環境では書き込みが止まらないので、プロンプトの「ファイルを変更しないこと」も残す |
| 4 | どれにも当たらない | 分からない | 渡し直さない | stderr の末尾を添えて呼び出し元へ返す |

渡し直しても本文が無ければ、もう渡し直さずに呼び出し元へ返す。

## 完了検知

`ps -p` は zombie (defunct) にも 0 を返すため、PID の存在では判定しない。完了は
stderr の `^tokens used$` sentinel と結果ファイルで見て、上限は起動を包んだ `timeout` が持つ（必ず終わる）。
進捗を覗くときは `tail -30 <stem>-err.log`。

## 実例: レビューリクエスト

読むだけのレビューなので `--use review` で起動し、結果は stdout から回収する。

```bash
# プロンプト（レビュー結果を最終応答で返すことと、ファイルを変更しないことを書く）はファイル書き込みツールで $TMP/review-prompt.md に置く
cd /workspace && timeout 1320 ndf delegate-run --runtime codex --use review -- codex exec '{args}' --config model_reasoning_effort=medium \
  < "$TMP/review-prompt.md" > "$TMP/codex-stdout.log" 2> "$TMP/codex-err.log"
# => $TMP/codex-stdout.log を読む。空なら、上の「最終出力をファイル経由で保証する」の末尾の表を上の行から順に照らして次の手を決める
```

## Codex 固有のトラブルシューティング

### Q1. stdout が空で stderr に大量の exec ログだけある

**原因**: まだ最終回答を出す前に停止した、または最終 assistant message を出さずにセッションが終わった。

**対処**: 結果ファイルにも stdout にも本文が無ければ、「最終出力をファイル経由で保証する」の末尾の表を上の行から順に照らし、
渡し直すかどうかと次の手を決める。

### Q2. `bwrap: No permissions to create a new namespace` で exec 失敗

**原因**: `ndf delegate-run` を通さずに codex を起動し、かつ環境が user namespace 非対応。

**対処**: `ndf delegate-run` で打ち直す。止まったら、外部隔離環境であることを確かめて `/ndf:launch-config` で codex を承認するか、
ホストで名前空間を有効化する（「サンドボックス制約」セクション）。

### Q3. 「ファイルを読めません」と返ってくる

**原因**: サンドボックス有効で読み取りに失敗、またはプロンプトの相対パスと cwd の不一致。

**対処**: プロンプトには絶対パスを書き、対象のディレクトリへ移ってから `ndf delegate-run` を打つ（作業ディレクトリは `ndf delegate-run` が今のディレクトリで入れる）。サンドボックスが読み取りを止めたなら Q2 を見る。

### Q4. 認証エラー（`Unauthorized` / `token expired`）

```bash
codex logout
codex login
```
