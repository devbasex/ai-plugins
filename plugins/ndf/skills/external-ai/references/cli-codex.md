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

次の形で起動する（`timeout <秒>` で包む。秒は SKILL.md の「共通の実行手順」）。プロンプトは **stdin へ流し**、
作業ディレクトリへ移ってから `-C "$PWD"` で渡す。

```bash
cd <workdir>
ndf delegate-run --runtime codex -- codex exec '{args}' --config model_reasoning_effort=medium \
  -C "$PWD" [--model M] < <prompt> > <stem>-stdout.log 2> <stem>-err.log
```

| オプション | 用途 |
|---|---|
| `'{args}'` | 安全装置の引数を `ndf delegate-run` が入れる位置。`-s` などを自分で書くと拒まれる |
| `-C "$PWD"` | 作業ディレクトリ。指定しないと cwd が想定と異なりファイルを読めなくなる。今のディレクトリと違う場所は拒まれる |
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

## 完了検知

`ps -p` は zombie (defunct) にも 0 を返すため、PID の存在では判定しない。完了は
stderr の `^tokens used$` sentinel と結果ファイルで見て、上限は起動を包んだ `timeout` が持つ（必ず終わる）。
進捗を覗くときは `tail -30 <stem>-err.log`。

## 実例: レビューリクエスト

```bash
FINAL=$TMP/codex-api-v2-review.md
# プロンプト（出力先に $FINAL を書く）はファイル書き込みツールで $TMP/review-prompt.md に置く
cd /workspace
timeout 1320 ndf delegate-run --runtime codex --use review -- codex exec '{args}' --config model_reasoning_effort=medium \
  -C "$PWD" < "$TMP/review-prompt.md" > "$TMP/codex-stdout.log" 2> "$TMP/codex-err.log"
# => $FINAL があればそれを、無ければ $TMP/codex-stdout.log を読む
```

## Codex 固有のトラブルシューティング

### Q1. stdout が空で stderr に大量の exec ログだけある

**原因**: まだ最終回答を出す前に停止した、または最終 assistant message を出さずにセッションが終わった。

**対処**: 結果ファイルにも stdout にも本文が無ければ「最終出力をファイル経由で保証する」の
パターンで渡し直す（`apply_patch` 指示の追加 + `model_reasoning_effort=medium`）。

### Q2. `bwrap: No permissions to create a new namespace` で exec 失敗

**原因**: `ndf delegate-run` を通さずに codex を起動し、かつ環境が user namespace 非対応。

**対処**: `ndf delegate-run` で打ち直す。止まったら、外部隔離環境であることを確かめて `/ndf:launch-config` で codex を承認するか、
ホストで名前空間を有効化する（「サンドボックス制約」セクション）。

### Q3. 「ファイルを読めません」と返ってくる

**原因**: サンドボックス有効で読み取りに失敗、またはプロンプトの相対パスと cwd の不一致。

**対処**: プロンプトには絶対パスを書き、対象のディレクトリへ移ってから `-C "$PWD"` を渡す。サンドボックスが読み取りを止めたなら Q2 を見る。

### Q4. 認証エラー（`Unauthorized` / `token expired`）

```bash
codex logout
codex login
```
