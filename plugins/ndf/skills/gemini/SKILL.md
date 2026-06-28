---
name: gemini
description: "Delegate coding, review, or research to Gemini CLI."
when_to_use: "外部 AI （Gemini）へコード生成 / レビュー / 調査を委譲したいとき。Triggers: 'geminiで調査', 'geminiレビュー', '第二意見レビュー (Gemini)', 'gemini exec', 'external AI review (Gemini)'"
---

# Gemini 外部AI委譲スキル

## 概要

`gemini` CLI（Google Gemini、通常は `/usr/bin/gemini` または `npm` 経由でインストール）を直接実行して、コード生成・独立レビュー・コードベース調査を外部AIに委譲するためのスキル。

`codex` skill と同等の用途だが、Gemini CLI は以下の点で扱いやすい:

- **stdout に最終 response が直接出る**（text/json いずれも空にならない既知挙動なし）
- **bwrap サンドボックスに依存しない**（WSL2 でも追加フラグ不要）
- **プロセス exit で完了判定可能**（sentinel grep が不要）

## NDFとの関係

- `/ndf:review <PR番号> gemini` のように、`review` skill の第二引数 `gemini` 指定時の委譲先として利用される
- 専用エージェント（`corder` 相当）は未整備。委譲時はメインエージェントから本 skill を参照して直接 CLI を起動する
- Codex との使い分けは「既知制約とコスト」節を参照

## いつ使うか

### 使うべきケース
- **独立第二意見レビュー**: 設計書・PR・仕様書を Gemini にレビューさせる（Codex とのクロスチェックに有用）
- **コードベース横断調査**: Gemini はワークスペース全体を走査する設計のため、複数ディレクトリ横断の調査に向く
- **長文生成**: ドキュメント生成・要約・翻訳など
- **Codex でうまくいかない / レート制限に当たったときの代替**

### 使わないべきケース
- 短時間（1〜2分以内）で済むタスク → メインエージェントで直接対応
- ユーザとの対話が必要な設計相談 → Plan Mode等で対話しながら進める
- 単純な質問回答 → WebFetch / WebSearch で足りる
- 機密情報を含むコード → 外部API送信の可否を確認してから

## 前提条件

```bash
# インストール確認
which gemini
gemini --version

# 初回ログイン（OAuth）
gemini   # 起動 → /auth でログイン
```

未インストールの場合は以下でセットアップ:

```bash
# npm 経由
npm install -g @google/gemini-cli

# 動作確認
gemini -p "hello" --output-format text
```

## 基本実行パターン

### 1. 承認モード（重要）

Gemini CLI は対話モードでは tool 実行ごとに承認を求める。非対話で確実に走らせるには `--yolo` か `--approval-mode yolo` を付ける。

```bash
# ❌ 非対話モードで tool 承認待ちで止まる
gemini -p "..."

# ✅ ツール自動承認（外部隔離前提）
gemini --yolo -p "..."

# ✅ 読み取り専用モード（plan mode、編集系 tool は走らない）
gemini --approval-mode plan -p "..."
```

| モード | 用途 |
|---|---|
| `default` | 対話で都度承認（非対話では止まる） |
| `auto_edit` | 編集系のみ自動承認 |
| `yolo` (`--yolo`) | 全 tool 自動承認 |
| `plan` | 読み取り専用（調査・レビュー向け） |

**レビュー/調査タスクの推奨**: `--approval-mode plan`（編集事故を防ぐ）
**コード生成タスクの推奨**: `--yolo`（実ファイル編集が必要）

> ⚠️ **`--yolo` のセキュリティ注意**: 全 tool 自動承認は `rm -rf` / 任意のシェル実行 / 任意のファイル編集を **無確認で許可** する。
> 必ず以下のいずれかの **外部隔離環境** 内でのみ使用すること:
> - Docker コンテナ / devcontainer
> - VM / CI ランナー
> - 隔離された worktree（ホスト本体のリポジトリでは使わない）
>
> ホスト直接実行や本番リポジトリ作業中の `--yolo` は厳禁。コード生成タスクでも、ホスト直接実行なら
> `--approval-mode auto_edit`（編集系のみ自動承認、シェル実行は都度承認）への降格を検討する。

### 2. プロンプトは一時ファイル経由で渡す

長いプロンプトをシェル引数に直接渡すとエスケープが破綻するので、ファイル経由で stdin か `$(cat ...)` 経由にする。

```bash
# Step 1: プロンプトを一時ファイルに書く
cat > /tmp/gemini-prompt.md <<'EOF'
## タスク
以下のファイルを読み込み、設計意図とコードの整合性をレビューしてください。

## 対象ファイル（絶対パスで指定）
/absolute/path/to/design.md

## 出力形式
Markdown で標準出力に吐いてください。
EOF

# Step 2a: stdin 経由（推奨）
gemini --yolo --output-format text -p "$(cat /tmp/gemini-prompt.md)" \
  > /tmp/gemini-stdout.md \
  2> /tmp/gemini-err.log

# Step 2b: あるいは stdin パイプ
cat /tmp/gemini-prompt.md | gemini --yolo --output-format text -p "" \
  > /tmp/gemini-stdout.md \
  2> /tmp/gemini-err.log
```

### 3. 出力ストリームの扱い

Gemini CLI の出力構造（codex と異なる点に注意）:

| ストリーム | `--output-format text` の内容 | `--output-format json` の内容 |
|---|---|---|
| **stdout** | 最終 assistant response の本文（Markdown / プレーンテキスト） | JSON 1 オブジェクト: `{session_id, response, stats}` |
| **stderr** | 警告のみ（例: `Ripgrep is not available. Falling back to GrepTool.`）— 通常数行 |

**実務上の扱い**:
- 成果物が欲しい → `--output-format text` の stdout をそのまま採用
- 統計（トークン数・tool 呼び出し履歴）が欲しい → `--output-format json` で stdout を `jq` 解析

```bash
# 成果物だけ取りたい
gemini --yolo --output-format text -p "$(cat prompt.md)" > out.md

# 統計込みで取りたい
gemini --yolo --output-format json -p "$(cat prompt.md)" > out.json
jq -r '.response' out.json > out.md
jq '.stats' out.json > stats.json
```

### 4. 最終出力をファイル経由で保証する（補強策）

Gemini は codex のような「最終 message を返さずに終わる」既知挙動は今のところ確認されていない。
ただし長尺タスクで途中エラーが起きた場合の保険として、**`apply_patch` 相当の write_file tool で書き出させる指示** をプロンプトに加えると安全:

```markdown
## 出力先（推奨）

最終結果を `/tmp/gemini-output-<task-name>.md` にも書き出してください。
（stdout には同内容をそのまま出力すれば冪等で問題ありません。）
```

回収側は「stdout → ファイル → stderr」の順でフォールバック:

```bash
# 命名規約:
#   STDOUT      = gemini の `> リダイレクト` 先（本 skill では /tmp/gemini-stdout.md で統一）
#   OUTPUT_FILE = プロンプト指示で `write_file` させた保険ファイル（task ごとに固有名）
OUTPUT_FILE=/tmp/gemini-output-pr13734-review.md
STDOUT=/tmp/gemini-stdout.md

if [ -s "$STDOUT" ]; then
    cp "$STDOUT" ./result.md
elif [ -s "$OUTPUT_FILE" ]; then
    cp "$OUTPUT_FILE" ./result.md
else
    echo "WARN: Gemini の最終出力を回収できませんでした。stderr を確認:" >&2
    tail -200 /tmp/gemini-err.log
fi
```

### 5. バックグラウンド実行 + 待機パターン

Gemini も大規模調査タスクでは数分かかる。エージェントハーネスのシェルタイムアウト（通常2〜3分）に引っかかる可能性があるため、**バックグラウンド実行 + 待機** が安全。

```bash
# 1. プロンプトファイル書き出し
# -> /tmp/gemini-prompt.md

# 2. gemini をバックグラウンドで起動
gemini --yolo --output-format text -p "$(cat /tmp/gemini-prompt.md)" \
  > /tmp/gemini-stdout.md \
  2> /tmp/gemini-err.log &
PID=$!
echo "PID: $PID"

# 3. 完了検知 — Gemini は exit するので PID watch で OK
#    (codex の zombie 問題はないが、念のため出力ファイルサイズも併用すると堅牢)
until ! kill -0 $PID 2>/dev/null; do
  sleep 30
done
echo "DONE"

# 4. 終了コード確認
wait $PID
EXIT=$?
echo "exit=$EXIT"
```

**注意**:
- Gemini は codex と違って `^tokens used$` のような sentinel を吐かないため、stderr grep では完了判定できない
- 代わりに **プロセスの終了** を見るのが正しい（`kill -0` でプロセス存在確認、`wait $PID` で終了コード回収）

### 6. 待機間隔のチューニング

エージェントの context cache TTL は通常5分。これを超えると prompt cache がミスして再送料金が発生する:

- **短い間隔**: 60〜270秒（TTL=5分内に収まる、軽量）
- **長い間隔**: 1200秒以上（1回のキャッシュミスを長時間で償却）
- **避けるべき**: 300秒前後（キャッシュミス+短時間の最悪）

Gemini の典型実行時間（数十秒〜5分）に対しては **60〜270秒ポーリング** で十分。

### 7. プロセス確認・ログ追跡

```bash
# 完了したか
kill -0 $PID 2>/dev/null && echo "RUNNING" || echo "DONE"

# 進捗を覗く（Gemini は実行中の stdout 出力は限定的なので stderr 側を見る）
tail -30 /tmp/gemini-err.log
```

## プロンプト設計のコツ

### 必須要素
1. **対象ファイルの絶対パス**（Gemini はワークスペース外のファイルも参照可能だが絶対パスが安全）
2. **調査観点を具体化**（箇条書きで3〜5項目に絞る）
3. **出力形式の指定**（Markdownテンプレートを提示）
4. **スコープ外の明示**（脱線防止）
5. **出力サイズ目安**（例: 400〜500行）

### レビュー依頼テンプレート

```markdown
あなたは<役割（例: シニアバックエンドエンジニア / セキュリティレビュアー）>として、
以下をレビューしてください。

## 対象ファイル（必ず最初に読むこと）
`/absolute/path/to/target.md`

## 観点
1. <観点1: 例「仕様とコードの整合性」>
2. <観点2: 例「既存APIとの後方互換性」>

## 調査対象コード（必要に応じて読む）
- `src/...`
- `lib/...`

## 背景コンテキスト
- <プロジェクト概要>
- <関連PR / Issue番号>
- <既存レビューで対応済みの事項（重複指摘を避けるため）>

## 出力形式

以下を Markdown で **stdout に出力** してください。
（保険として `/tmp/gemini-output-<task-name>.md` にも `write_file` で書き出してください。）

# <タイトル>

## 総評
## 1. <観点1> に関する指摘
### 1.1 正確な主張
### 1.2 訂正推奨
## 2. <観点2> に関する指摘
## 3. 追加提案
## 4. 承認可否

**必須**: 行番号・ファイルパスに紐付けて具体的に指摘してください。400〜500行程度、日本語で出力してください。
```

### コード生成依頼テンプレート

```markdown
以下の実装タスクを実行してください。

## タスク
<具体的な実装内容>

## 制約
- <技術制約: 言語バージョン、依存ライブラリ>
- <コーディング規約: ESLint / Prettier / rustfmt等>
- <テスト要件: ユニットテスト必須等>

## 対象ファイル
- <既存ファイルのパス>
- <新規ファイルのパス案>

## 背景
<なぜこの実装が必要か、設計判断の経緯>

## 完了基準
- [ ] テストがパスする
- [ ] 型チェック / lint がパスする
- [ ] <追加の受け入れ条件>

**必須**: ファイル編集は実際に行い、最後に変更ファイル一覧と要点を
stdout に Markdown で出力してください。
保険として `/tmp/gemini-output-<task-name>.md` にも `write_file` で書き出してください。
```

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
2. API後方互換性（v1クライアントが壊れないか）

## 調査対象コード
- src/api/v2/**
- src/api/v1/** （比較用）

## 出力先
- stdout に Markdown で出力
- 保険として \`${FINAL}\` にも \`write_file\` で書き出すこと

## 出力形式
Markdown で 400〜500 行、日本語。
EOF

# === 2. バックグラウンド起動 ===
# レビュー用途なので plan mode（読み取り専用）+ text 出力
gemini --approval-mode plan --output-format text \
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
EXIT=$?
echo "DONE exit=$EXIT"

# === 4. 成果物を回収（stdout 優先 → ファイルフォールバック） ===
if [ -s /tmp/gemini-stdout.md ]; then
    cp /tmp/gemini-stdout.md ./review-result.md
    echo "✅ stdout から回収"
elif [ -s "$FINAL" ]; then
    cp "$FINAL" ./review-result.md
    echo "⚠ ファイルからフォールバック回収"
else
    echo "❌ Gemini の最終出力を回収できませんでした。stderr を確認:" >&2
    tail -200 /tmp/gemini-err.log
    exit 1
fi
```

## トラブルシューティング

### Q1. 非対話モードなのにプロセスがハングする
**原因**: 承認が必要な tool 呼び出しで止まっている（`default` / `auto_edit` モードのまま）。

**対処**: `--yolo` または `--approval-mode plan` を付ける。レビュー/調査なら `plan` が安全。

### Q2. stdout に思考のような余計な出力が混ざる
**原因**: `--output-format text` でも一部の進捗 / モデル切替メッセージが混ざる場合がある。

**対処**:
- `--output-format json` を使い、`jq -r '.response'` で本文だけ抽出する
- プロンプトで「最終結果のみを出力すること、思考や前置きは不要」と明記

### Q3. ファイルを読めない / ワークスペース外アクセスで止まる
**原因**: Gemini のワークスペース範囲外のファイル参照、または承認待ち。

**対処**:
- `--include-directories /path/to/extra` で対象ディレクトリを追加
- プロンプトには**絶対パス**を書く
- それでも止まるなら `--yolo` または `--skip-trust`

### Q4. 出力が途中で切れる / トークン上限
**原因**: モデルの出力トークン上限に達した。

**対処**:
- プロンプトで「400行以内」など出力サイズを指定
- 観点を絞って分割実行
- `-m <model>` でより長い context のモデルを指定（gemini-3-pro 等、利用可能なものに応じて）

### Q5. 認証エラー (`Authentication required` / `token expired`)
**原因**: OAuth セッション失効。

**対処**:
```bash
# 対話モードで再ログイン
gemini
# プロンプト上で /auth を叩いてブラウザ認証
```

### Q6. ハーネスのシェルタイムアウトで kill される
**原因**: フォアグラウンド実行のまま長尺タスクを走らせた。

**対処**: 必ず `&` でバックグラウンド化し、`kill -0 $PID` ポーリングで待機する（5節参照）。

## 既知の制約とコスト

1. **承認モード必須**: 非対話実行では `--yolo` か `--approval-mode plan` を必ず付ける
2. **stderr 警告は無害**: `Ripgrep is not available. Falling back to GrepTool.` 等は通常運用上問題なし
3. **ログイン状態**: 初回は対話モードで `/auth` 経由のログインが必要
4. **APIコスト**: Google AI Studio / Vertex 経由のトークン課金。Codex より安価な傾向だが従量制
5. **機密情報**: 外部APIにコードが送信されるため、社外秘コードの扱いは組織ポリシーに従うこと
6. **モデル選択**: デフォルトモデルは時期により変動。安定性を求めるなら `-m gemini-2.5-pro` 等を明示

## Codex との使い分け

| 観点 | Codex (`/ndf:codex`) | Gemini (本スキル) |
|---|---|---|
| stdout の信頼性 | 最終 message が落ちることがある（要ファイル書き出し） | stdout に response が直接出る |
| サンドボックス | WSL2 で `--dangerously-bypass-approvals-and-sandbox` 必須 | 追加フラグ不要 |
| 完了判定 | `^tokens used$` sentinel | プロセス exit |
| 出力フォーマット | Markdown 本文のみ | text / json 選択可（json は統計付き） |
| 強み | コード逐語照合、長時間の深い調査 | 横断調査、長文生成、軽量タスク |
| 弱み | セットアップ・運用が煩雑 | 高難度コード解析でやや浅くなることがある |

**指針**:
- 第二意見が欲しい場合、両方走らせてクロスチェックすると最も堅い
- 短時間で済む独立レビュー → Gemini を先に
- 行番号・件数の逐語確認 → Codex を併用

## 関連

- **`/ndf:codex` skill**: Codex CLI 経由の同等スキル（本スキルと併用してクロスチェック可能）
- **`/ndf:review` skill**: 第二引数 `gemini` 指定時に本スキルの手順を参照
- **Gemini CLI 公式ドキュメント**: `gemini --help`
- **他のAI委譲方法**: `codex`, `claude`, `ollama` 等のCLI も同様のパターンで利用可
