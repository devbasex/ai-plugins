# Codex CLI 固有の手順

共通手順（プロンプトの書き出し、バックグラウンド起動、三段フォールバック回収、待機間隔、
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

該当環境では `--dangerously-bypass-approvals-and-sandbox` を付けて起動する。

```bash
# ❌ サンドボックス有効（bwrap 失敗で exec コマンドが全滅）
codex exec -s read-only -C "$PWD"

# ✅ サンドボックスバイパス（外側が既にコンテナ等で隔離されている前提）
codex exec --dangerously-bypass-approvals-and-sandbox -C "$PWD"
```

**判断基準**: Docker / devcontainer / VM / CI ランナー等で外部的に隔離済みなら実用上安全。
ホスト直接実行でコードを全書き換えされたくない場合はフラグを付けず、下記の bwrap 代替で対処する。
`-s read-only` / `-s workspace-write` も bwrap を使うため、フラグなしでは同じ失敗になる点に注意。

### bwrap 代替の有効化（ホスト直接実行時）

```bash
# Debian/Ubuntu 系でホスト user namespace を有効化
sudo sysctl kernel.unprivileged_userns_clone=1

# 永続化
echo 'kernel.unprivileged_userns_clone=1' | sudo tee /etc/sysctl.d/00-local-userns.conf
```

## 起動コマンド

`external-ai.py run codex` が次の形で起動する（共通層の `launch-cli.sh`）。プロンプトは **stdin へ流し**、
`-C` で作業ディレクトリを渡す。

```bash
codex exec --dangerously-bypass-approvals-and-sandbox --config reasoning.effort=medium \
  -C <workdir> [--model M] < <prompt> > <stem>-stdout.log 2> <stem>-err.log
```

| オプション | 用途 |
|---|---|
| `-C <dir>` | 作業ディレクトリ。指定しないと cwd が想定と異なりファイルを読めなくなる |
| `--dangerously-bypass-approvals-and-sandbox` | bwrap 非対応環境で必須。外部隔離環境内でのみ使用する |
| `--config reasoning.effort=medium` | `high` だと思考へ偏り最終 message を返さない頻度が上がるため、既定で `medium` を推奨 |
| `--json` | JSON Lines でイベントを出力。`event.type=assistant_message` を grep すれば確実に本文を取れる |
| `codex resume` | 長時間ジョブで親エージェントが再起動した場合にセッションを再開する |

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

補助策として `--config reasoning.effort=medium` へ下げる、`--json` でイベントを採取する、
プロンプト末尾に「tool 呼び出しのみで終了しないこと」を明記する、の 3 つを併用する。

回収は **ファイル → stdout → stderr** の順で、`external-ai.py run` が行う。

## 完了検知

`ps -p` は zombie (defunct) にも 0 を返すため、PID の存在では判定しない。監視（`monitor.py`）は
stderr の `^tokens used$` sentinel と結果ファイルを脱出条件にし、上限（`--phase` の工程の値）で必ず終わる。
進捗を覗くときは `tail -30 <metrics.stem>-err.log`。

## 実例: レビュー依頼

```bash
FINAL=$TMP/codex-api-v2-review.md
# プロンプト（出力先に $FINAL を書く）はファイル書き込みツールで $TMP/review-prompt.md に置く
python3 "$SKILL_DIR/scripts/external-ai.py" run codex --phase review \
  --prompt-file "$TMP/review-prompt.md" --output-file "$FINAL" --workdir /workspace
# => {"status": "ok", ..., "metrics": {"outcome": "ok", "result": "$FINAL", "source": "file", ...}}
```

## Codex 固有のトラブルシューティング

### Q1. stdout が空で stderr に大量の exec ログだけある

**原因**: まだ最終回答を出す前に停止した、または最終 assistant message を出さずにセッションが終わった。

**対処**: `metrics.outcome` が `no_result` なら「最終出力をファイル経由で保証する」の
パターンで渡し直す（`apply_patch` 指示の追加 + `reasoning.effort=medium`）。

### Q2. `bwrap: No permissions to create a new namespace` で exec 失敗

**原因**: `--dangerously-bypass-approvals-and-sandbox` を付け忘れ、かつ環境が user namespace 非対応。

**対処**: フラグを追加して再実行する。ホストで有効化する方法は「サンドボックス制約」節を参照。

### Q3. 「ファイルを読めません」と返ってくる

**原因**: サンドボックス有効で読み取りに失敗、またはプロンプトの相対パスと cwd の不一致。

**対処**: `--dangerously-bypass-approvals-and-sandbox` を追加し、プロンプトには絶対パス、`-C` で cwd を明示する。

### Q4. 認証エラー（`Unauthorized` / `token expired`）

```bash
codex logout
codex login
```
