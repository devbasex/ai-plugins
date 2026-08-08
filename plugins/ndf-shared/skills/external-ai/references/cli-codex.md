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

プロンプトは **stdin へ流す**。`-C` で作業ディレクトリを明示する。

```bash
codex exec --dangerously-bypass-approvals-and-sandbox \
  --config reasoning.effort=medium \
  -C "$PWD" \
  < /tmp/codex-prompt.md \
  > /tmp/codex-stdout.md \
  2> /tmp/codex-err.log &
PID=$!
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

`/tmp/codex-output-タスク名.md`

書き出しは `apply_patch` で新規ファイル作成してください。
**stdout への出力だけでは不十分です**（セッション終了で失われる場合があるため）。
書き出し後、念のため stdout にも同じ内容を出力してください（冪等で問題ありません）。
```

補助策として `--config reasoning.effort=medium` へ下げる、`--json` でイベントを採取する、
プロンプト末尾に「tool 呼び出しのみで終了しないこと」を明記する、の 3 つを併用する。

回収は **ファイル → stdout → stderr** の順（共通手順の三段フォールバック、Codex は `OUTPUT_FILE` 優先）。

## 完了検知

`ps -p $PID` は zombie (defunct) にも 0 を返すため、**PID watch は永久ループになりうる**。
stderr 末尾の sentinel を脱出条件にする。

```bash
# ❌ 永久ループ化しうる
until ! ps -p $PID; do sleep 30; done

# ✅ zombie 安全
until grep -q '^tokens used$' /tmp/codex-err.log 2>/dev/null; do
  sleep 30
done
```

進捗を覗くときは `tail -30 /tmp/codex-err.log`。

## 実例: レビュー依頼の完全フロー

```bash
# === 1. プロンプト書き出し（最終出力先を明示し apply_patch で書かせる） ===
FINAL=/tmp/codex-output-api-v2-review.md

cat > /tmp/review-prompt.md <<EOF
あなたはシニアバックエンドエンジニアとして、以下をレビューしてください。

## 対象ファイル（必ず最初に読むこと）
/workspace/docs/design/api-v2.md

## 観点
1. コードとの一致（行番号・件数・関数シグネチャ）
2. API 後方互換性（v1 クライアントが壊れないか）

## 調査対象コード
- src/api/v2/**
- src/api/v1/**（比較用）

## 出力先（必須）
最終的なレビュー結果を **必ず** \`${FINAL}\` に \`apply_patch\` で新規作成してください。
**stdout への出力だけでは不十分です**。書き出し後、stdout にも同じ内容を出力してください。

## 出力形式
Markdown で 400〜500 行、日本語。tool 呼び出しのみで終了せず、最後に必ず assistant message として 1 回出力してください。
EOF

# === 2. バックグラウンド起動 ===
codex exec --dangerously-bypass-approvals-and-sandbox \
  --config reasoning.effort=medium \
  -C /workspace \
  < /tmp/review-prompt.md \
  > /tmp/codex-stdout.md \
  2> /tmp/codex-err.log &
PID=$!
echo "codex PID: $PID"

# === 3. 完了確認（^tokens used$ sentinel を待つ） ===
until grep -q '^tokens used$' /tmp/codex-err.log 2>/dev/null; do
  sleep 30
done

# === 4. 成果物を回収（ファイル優先 → stdout フォールバック） ===
if [ -s "$FINAL" ]; then
    cp "$FINAL" ./review-result.md
elif [ -s /tmp/codex-stdout.md ]; then
    cp /tmp/codex-stdout.md ./review-result.md
    echo "WARN: stdout からフォールバック回収（ファイル書き出しなし）" >&2
else
    echo "ERROR: Codex の最終出力を回収できませんでした。stderr 末尾を確認:" >&2
    tail -200 /tmp/codex-err.log
    exit 1
fi
```

## Codex 固有のトラブルシューティング

### Q1. stdout が空で stderr に大量の exec ログだけある

**原因**: まだ最終回答を出す前に停止した、または最終 assistant message を出さずにセッションが終わった。

**対処**: `grep -q '^tokens used$' /tmp/codex-err.log` で sentinel を確認する。
未出力なら実行中なので追加待機。出ているのに stdout が空なら「最終出力をファイル経由で保証する」の
パターンでリトライする（`apply_patch` 指示の追加 + `reasoning.effort=medium`）。

### Q2. `bwrap: No permissions to create a new namespace` で exec 失敗

**原因**: `--dangerously-bypass-approvals-and-sandbox` を付け忘れ、かつ環境が user namespace 非対応。

**対処**: フラグを追加して再実行する。ホストで有効化する方法は「サンドボックス制約」節を参照。

### Q3. 「ファイルを読めません」と返ってくる

**原因**: サンドボックス有効で読み取りに失敗、またはプロンプトの相対パスと cwd の不一致。

**対処**: `--dangerously-bypass-approvals-and-sandbox` を追加し、プロンプトには絶対パス、`-C` で cwd を明示する。

### Q4. タスク完了通知が来たのに出力が空 / 待機ループが抜けない

**原因**: `&` で起動したラッパーシェルだけが終了した、または zombie を `ps -p` が生存と誤判定している。

**対処**: 検知を PID ではなく `^tokens used$` sentinel で行う（「完了検知」節）。

### Q5. 認証エラー（`Unauthorized` / `token expired`）

```bash
codex logout
codex login
```
