# Gemini CLI 固有の手順

共通手順（プロンプトの書き出し、バックグラウンド起動、三段フォールバック回収、待機間隔、
プロンプトテンプレート）は [../SKILL.md](../SKILL.md) を参照。本ファイルは Gemini CLI に固有の差分だけを扱う。

## インストールとログイン

```bash
which gemini && gemini --version

# 初回ログイン（OAuth）: 対話モードで起動して /auth を叩きブラウザ認証する
gemini

# 未インストールの場合
npm install -g @google/gemini-cli
gemini -p "hello" --output-format text
```

## 承認モード（最重要）

Gemini CLI は対話モードでは tool 実行ごとに承認を求める。非対話で確実に走らせるには
`--yolo` か `--approval-mode` を指定する。指定しないと承認待ちでハングする。

| モード | 用途 |
|---|---|
| `default` | 対話で都度承認（非対話では止まる） |
| `auto_edit` | 編集系のみ自動承認。シェル実行は都度承認 |
| `yolo`（`--yolo`） | 全 tool 自動承認 |
| `plan`（`--approval-mode plan`） | 読み取り専用。編集系 tool は走らない |

- **レビュー / 調査タスク**: `--approval-mode plan`（編集事故を防ぐ）
- **コード生成タスク**: `--yolo`（実ファイル編集が必要）
- **`gh api -X POST` などシェル実行を伴うタスク**: `--yolo` 必須（`plan` / `auto_edit` ではブロックされる）

指定した承認モードは trusted directory 判定で覆される。untrusted なパスで起動すると
`--yolo` が `default` へ降格し、非対話では承認待ちのままハングする。headless 実行では
`GEMINI_CLI_TRUST_WORKSPACE=true` と `--skip-trust` を必ず併用すること（「起動コマンド」節参照）。

> ⚠️ **`--yolo` のセキュリティ注意**: 全 tool 自動承認は `rm -rf` / 任意のシェル実行 /
> 任意のファイル編集を**無確認で許可**する。Docker コンテナ / devcontainer / VM / CI ランナー /
> 隔離された worktree のいずれかの**外部隔離環境内でのみ**使用すること。ホスト直接実行や
> 本番リポジトリ作業中の `--yolo` は厳禁で、その場合は `--approval-mode auto_edit` への降格を検討する。
> プロンプトで「リポジトリ編集禁止」を明示することは有効だが、sandbox の代替にはならない。

## 起動コマンド

プロンプトは `-p "$(cat ...)"` で渡すか、stdin へパイプする。

> ⚠️ **非対話実行では `GEMINI_CLI_TRUST_WORKSPACE=true` と `--skip-trust` を必ず両方付ける**。
> Gemini CLI は未登録のディレクトリ（worktree のような新規パスを含む）を untrusted と判定し、
> `--yolo` を `default` へ降格させる。降格すると tool ごとの承認待ちになり、非対話では
> そのままハングする。片方だけでは降格を防げないため、環境変数とフラグの両方が必要。

```bash
GEMINI_CLI_TRUST_WORKSPACE=true gemini --approval-mode plan --skip-trust --output-format text \
  -p "$(cat /tmp/gemini-prompt.md)" \
  > /tmp/gemini-stdout.md \
  2> /tmp/gemini-err.log &
PID=$!

# stdin パイプでも可
GEMINI_CLI_TRUST_WORKSPACE=true gemini --yolo --skip-trust --output-format text -p "" \
  < /tmp/gemini-prompt.md \
  > /tmp/gemini-stdout.md 2> /tmp/gemini-err.log &
```

| オプション | 用途 |
|---|---|
| `--output-format text` | 最終 response の本文をそのまま stdout へ出す |
| `--output-format json` | `{session_id, response, stats}` の JSON 1 オブジェクトを出す |
| `--include-directories <dir>` | ワークスペース外のディレクトリを参照対象へ追加する |
| `--skip-trust` | trusted directory 判定を飛ばす。`--yolo` が無効化されるのを防ぐ |
| `GEMINI_CLI_TRUST_WORKSPACE=true`（環境変数） | 実行ディレクトリを trusted 扱いにする。`--skip-trust` と併用必須 |
| `-m <model>` | モデルを明示指定する（既定モデルは時期により変動する） |

`/ndf:cross-review` の `scripts/launch-gemini.sh` も同じ組み合わせで起動している。

## 出力ストリーム

| ストリーム | `--output-format text` | `--output-format json` |
|---|---|---|
| **stdout** | 最終 assistant response の本文（Markdown / プレーンテキスト） | `{session_id, response, stats}` |
| **stderr** | 警告のみ（例: `Ripgrep is not available. Falling back to GrepTool.`）。通常数行で無害 | 同左 |

```bash
# 成果物だけ取りたい
GEMINI_CLI_TRUST_WORKSPACE=true gemini --yolo --skip-trust --output-format text \
  -p "$(cat prompt.md)" > out.md

# 統計（トークン数・tool 呼び出し履歴）込みで取りたい
GEMINI_CLI_TRUST_WORKSPACE=true gemini --yolo --skip-trust --output-format json \
  -p "$(cat prompt.md)" > out.json
jq -r '.response' out.json > out.md
jq   '.stats'    out.json > stats.json
```

Gemini には Codex のような「最終 message を返さずに終わる」既知挙動は確認されていないため、
**回収は stdout 優先**（共通手順の三段フォールバックで `STDOUT` を `PRIMARY` にする）。
ただし長尺タスクの途中エラーに備え、保険として `write_file` での書き出しをプロンプトに加えておく。

```markdown
## 出力先（推奨）

最終結果を `/tmp/gemini-output-タスク名.md` にも `write_file` で書き出してください。
（stdout には同内容をそのまま出力すれば冪等で問題ありません。）
```

## 完了検知

Gemini は `^tokens used$` のような sentinel を吐かないため、stderr の grep では完了判定できない。
**プロセスの終了**を見るのが正しい。

```bash
until ! kill -0 $PID 2>/dev/null; do
  sleep 30
done
wait $PID
echo "exit=$?"
```

進捗を覗くときは `tail -30 /tmp/gemini-err.log`（実行中の stdout 出力は限定的）。

## 実例: レビュー依頼の完全フロー

```bash
# === 1. プロンプト書き出し ===
FINAL=/tmp/gemini-output-api-v2-review.md

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

## 出力先
- stdout に Markdown で出力
- 保険として \`${FINAL}\` にも \`write_file\` で書き出すこと

## 出力形式
Markdown で 400〜500 行、日本語。
EOF

# === 2. バックグラウンド起動（レビュー用途なので読み取り専用 + text 出力） ===
# trusted directory 判定で承認モードが降格しないよう、環境変数と --skip-trust を必ず併用する
GEMINI_CLI_TRUST_WORKSPACE=true gemini --approval-mode plan --skip-trust --output-format text \
  -p "$(cat /tmp/review-prompt.md)" \
  > /tmp/gemini-stdout.md \
  2> /tmp/gemini-err.log &
PID=$!
echo "gemini PID: $PID"

# === 3. 完了確認（プロセス終了を待つ） ===
until ! kill -0 $PID 2>/dev/null; do
  sleep 30
done
wait $PID
echo "DONE exit=$?"

# === 4. 成果物を回収（stdout 優先 → ファイルフォールバック） ===
if [ -s /tmp/gemini-stdout.md ]; then
    cp /tmp/gemini-stdout.md ./review-result.md
elif [ -s "$FINAL" ]; then
    cp "$FINAL" ./review-result.md
    echo "WARN: ファイルからフォールバック回収" >&2
else
    echo "ERROR: Gemini の最終出力を回収できませんでした。stderr を確認:" >&2
    tail -200 /tmp/gemini-err.log
    exit 1
fi
```

## Gemini 固有のトラブルシューティング

### Q1. 非対話モードなのにプロセスがハングする

**原因**: 承認が必要な tool 呼び出しで止まっている（`default` / `auto_edit` のまま）。
指定したはずの `--yolo` が trusted directory 判定で `default` へ降格しているケースも同じ症状になる。

**対処**: `--yolo` または `--approval-mode plan` を付けたうえで、
`GEMINI_CLI_TRUST_WORKSPACE=true` と `--skip-trust` を併用する。レビュー / 調査なら `plan` が安全。

### Q2. stdout に思考のような余計な出力が混ざる

**原因**: `--output-format text` でも進捗 / モデル切替メッセージが混ざる場合がある。

**対処**: `--output-format json` にして `jq -r '.response'` で本文だけ抽出する。
あわせてプロンプトへ「最終結果のみを出力すること、思考や前置きは不要」と明記する。

### Q3. ワークスペース外のファイルを読めない / 止まる

**対処**: `--include-directories /path/to/extra` で対象ディレクトリを追加し、プロンプトには絶対パスを書く。
それでも止まる場合は `GEMINI_CLI_TRUST_WORKSPACE=true` + `--skip-trust` を併用する。

### Q4. `--yolo` を付けたのに承認待ちになる

**原因**: trusted directory 判定によって YOLO が無効化され、承認モードが `default` へ降格している。
worktree のような新規パスは既定で untrusted 扱いになる。

**対処**: `GEMINI_CLI_TRUST_WORKSPACE=true` と `--skip-trust` を **両方** 付けて起動する。
片方だけでは降格を防げない。

### Q5. `Error in: mcpServers.<name>` の警告が毎回出る

**原因**: `.gemini/settings.json` に `disabled: false` などの非互換キーがある。

**対処**: 該当キーを除去するか、起動前に設定を sanitize する
（`/ndf:cross-review` の `launch-gemini.sh` は v4.7.2 以降でこれを自動化している）。

### Q6. 認証エラー（`Authentication required` / `token expired`）

**原因**: OAuth セッション失効。

**対処**: 対話モードで `gemini` を起動し、`/auth` を叩いてブラウザ認証をやり直す。
