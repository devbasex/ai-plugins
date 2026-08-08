---
name: external-ai
description: "Delegate coding, review, or research to an external AI CLI (Codex / Gemini). Use for 'codexで調査', 'geminiレビュー', '第二意見レビュー', 'external AI review', 'codex exec', 'gemini exec'."
when_to_use: "外部 AI へコード生成 / レビュー / 調査を委譲したいとき。追加トリガ: '外部AIに投げて', 'クロスチェックして', 'もう一つのAIに見てもらう', 'CLI で codex を回す'"
---

# 外部 AI 委譲スキル (Codex / Gemini)

## 概要

`codex` CLI（OpenAI Codex）と `gemini` CLI（Google Gemini）をローカルから直接起動し、
コード生成・独立第二意見レビュー・大規模コードベース調査を外部 AI に委譲する。

**手順の大半は 2 つの CLI で共通**であり、本ファイルはその共通手順を規定する。
起動フラグ・完了検知・出力回収など **CLI 固有の差分は補助ファイルに分離** している。

| 補助ファイル | 内容 |
|---|---|
| [references/cli-codex.md](references/cli-codex.md) | Codex CLI のインストール、サンドボックス制約、`codex exec` の起動、sentinel 完了検知、最終 message 欠落対策 |
| [references/cli-gemini.md](references/cli-gemini.md) | Gemini CLI のインストール、承認モード、`--output-format` の使い分け、プロセス終了による完了検知 |

## NDF との関係

- Claude Code 版の `corder` エージェントは本スキルの手順で Codex CLI を呼び出す
- `/ndf:review <PR番号> codex` / `/ndf:review <PR番号> gemini` の委譲先として利用される
- `/ndf:cross-review` は codex / gemini を**並列に起動**して両者の APPROVE 収束を待つ
- v4.0.0 で Codex MCP サーバは廃止。`mcp__codex__*` ツールは存在しない
- Gemini 専用エージェントは未整備。委譲時はメインエージェントから本スキルを参照して直接 CLI を起動する

## いつ使うか

### 使うべきケース

- **独立第二意見レビュー**: 設計書・PR・仕様書を外部 AI にレビューさせ、メインエージェントの思考バイアスを避ける
- **コードベース逐語照合**: 行番号・関数名・重複箇所の件数を正確に突き合わせる
- **長時間の調査タスク**: 複数ファイル横断で 5〜10 分以上かかる調査
- **実装タスクの並列化**: メインエージェントで他作業を進めつつ、別タスクを外部 AI に走らせる
- **長文生成**: ドキュメント生成・要約・翻訳

### 使わないケース

- 短時間（1〜2 分以内）で済むタスク → メインエージェントで直接対応
- ユーザとの対話が必要な設計相談 → Plan Mode 等で対話しながら進める
- 単純な質問回答 → WebFetch / WebSearch で足りる
- 機密情報を含むコード → 外部 API へ送信されるため、可否を組織ポリシーで確認してから

## どちらの CLI を選ぶか

| 観点 | Codex | Gemini |
|---|---|---|
| stdout の信頼性 | 最終 message が落ちることがある（ファイル書き出し必須） | stdout に response が直接出る |
| サンドボックス | WSL2 / 一部コンテナで `--dangerously-bypass-approvals-and-sandbox` が必須 | bwrap 非依存。ただし trusted directory 判定があり `GEMINI_CLI_TRUST_WORKSPACE=true` + `--skip-trust` が必須 |
| 非対話実行 | `codex exec` で完結 | `--yolo` か `--approval-mode plan` に加えて trust 解除が必須 |
| 完了判定 | stderr の `^tokens used$` sentinel | プロセス終了（`kill -0` / `wait`） |
| 出力フォーマット | Markdown 本文のみ | `text` / `json`（json は統計付き） |
| 典型実行時間 | 5〜10 分 | 数十秒〜5 分 |
| 強み | コード逐語照合、長時間の深い調査 | 横断調査、長文生成、軽量タスク |
| 弱み | セットアップ・運用が煩雑 | 高難度コード解析でやや浅くなることがある |

**指針**:

- 行番号・件数の逐語確認が要る → Codex
- 短時間で済む独立レビュー、横断調査、長文生成 → Gemini
- 第二意見を確実に取りたい → 両方を走らせてクロスチェック（`/ndf:cross-review` が自動化している）
- Codex がレート制限・サンドボックス制約に当たった → Gemini へ代替

`corder` エージェントとの使い分けは次のとおり。

| 観点 | `corder` エージェント経由 | 本スキルで直接 CLI 起動 |
|---|---|---|
| 使い勝手 | エージェントに委譲するだけ | プロンプト書き出し・起動・PID 管理を自分で制御 |
| プロンプト制御 | corder 側で整形 | 自由に設計可 |
| スケジュール連携 | 難しい | `/schedule` / `Monitor` と組み合わせやすい |

迷ったら `corder` 経由。プロンプト細部や非同期タイミングを自分で握りたい場合のみ直接起動する。

## 共通の実行手順

CLI 固有のコマンドラインは補助ファイルを参照し、流れは以下で統一する。

### 1. 前提確認

インストールとログイン状態を確認する。未インストール時のセットアップ手順は補助ファイルに記載。

```bash
which codex  && codex --version
which gemini && gemini --version
```

### 2. プロンプトを一時ファイルへ書き出す

長いプロンプトをシェル引数へ直接渡すとエスケープが破綻する。**必ず一時ファイル経由**にする。

```bash
cat > /tmp/external-ai-prompt.md <<'EOF'
## タスク
以下のファイルを読み込み、設計意図とコードの整合性をレビューしてください。

## 対象ファイル（絶対パスで指定）
/absolute/path/to/design.md
EOF
```

エージェントから実行する場合は、ファイル書き込みツールでプロンプトを作ってから、
シェル実行ツールのバックグラウンド実行オプションで CLI を起動する。

### 3. バックグラウンドで起動する

多くのエージェントハーネスはシェル実行に 2〜3 分のタイムアウトを課す。
外部 AI は数分〜10 分かかるため、**フォアグラウンド実行は禁止**。`&` で必ず非同期化し、
stdout と stderr を別ファイルへリダイレクトする。

```bash
<CLI 固有の起動コマンド> \
  > /tmp/external-ai-stdout.md \
  2> /tmp/external-ai-err.log &
PID=$!
```

stderr には思考ログや警告が出る。Codex では数千行になるため、必ずファイルへ逃がす。

### 4. 完了を検知する

検知方法は CLI で異なる。**PID の存在だけで判定しない**（Codex は zombie 化して `ps -p` が
0 を返し続けることがある）。

| CLI | 脱出条件 |
|---|---|
| Codex | stderr に `^tokens used$` が現れる（[references/cli-codex.md](references/cli-codex.md)） |
| Gemini | プロセスが終了する（[references/cli-gemini.md](references/cli-gemini.md)） |

### 5. 成果物を三段フォールバックで回収する

外部 AI の最終出力は、CLI とモデルの都合で欠落しうる。**stdout だけに依存しない**。
プロンプト側で「最終結果を指定ファイルへ書き出すこと」を必ず指示し（手順 6 のテンプレート参照）、
回収側は次の順で拾う。

```bash
# STDOUT      = CLI の `>` リダイレクト先
# OUTPUT_FILE = プロンプト指示でツールに書き出させた保険ファイル（task ごとに固有名）
STDOUT=/tmp/external-ai-stdout.md
OUTPUT_FILE=/tmp/external-ai-output-pr13734-review.md

# PRIMARY / SECONDARY は CLI ごとに下表の順で割り当てる
PRIMARY="$OUTPUT_FILE"; SECONDARY="$STDOUT"   # Codex の場合
# PRIMARY="$STDOUT"; SECONDARY="$OUTPUT_FILE" # Gemini の場合

if [ -s "$PRIMARY" ]; then
    cp "$PRIMARY" ./result.md
elif [ -s "$SECONDARY" ]; then
    cp "$SECONDARY" ./result.md
else
    echo "WARN: 外部 AI の最終出力を回収できませんでした。stderr 末尾を確認:" >&2
    tail -200 /tmp/external-ai-err.log
fi
```

| CLI | 優先 (`PRIMARY`) | 次点 (`SECONDARY`) | 最後の手段 |
|---|---|---|---|
| Codex | `OUTPUT_FILE` | `STDOUT` | stderr 末尾 |
| Gemini | `STDOUT` | `OUTPUT_FILE` | stderr |

Codex は最終 assistant message を返さずにセッションを終える既知挙動があるため、
ファイルを優先する。Gemini は stdout が信頼できるため stdout を優先する。

### 6. 待機間隔のチューニング

エージェントの context cache TTL は通常 5 分。これを超えると prompt cache がミスして
再送料金が発生する。

- **短い間隔**: 60〜270 秒（TTL 内に収まる、軽量）
- **長い間隔**: 1200 秒以上（1 回のキャッシュミスを長時間で償却）
- **避ける**: 300 秒前後（キャッシュミス + 短時間待機の最悪の組み合わせ）

Codex（5〜10 分）は 270 秒ポーリングか 1200 秒一括待ち、Gemini（数十秒〜5 分）は
60〜270 秒ポーリングでよい。

## プロンプト設計

### 必須要素

1. **対象ファイルの絶対パス**（外部 AI は `nl -ba` / `sed -n` / `rg` 等でファイルを読む）
2. **調査観点を具体化**（箇条書きで 3〜5 項目に絞る）
3. **出力形式の指定**（Markdown テンプレートを提示）
4. **スコープ外の明示**（脱線防止）
5. **出力サイズの目安**（例: 400〜500 行）
6. **最終出力先ファイルの指定**: `/tmp/<cli>-output-タスク名.md` のような明示パスへ書き出させる。
   Codex は `apply_patch`、Gemini は `write_file` を使う。**stdout のみへの出力は不可**
7. **assistant message の強制**: 「tool 呼び出しのみで終了せず、最後に必ず 1 回出力すること」

### レビュー依頼テンプレート

```markdown
あなたは（役割: 例 シニアバックエンドエンジニア / セキュリティレビュアー）として、
以下をレビューしてください。

## 対象ファイル（必ず最初に読むこと）
`/absolute/path/to/target.md`

## 観点
1. （観点1: 例「仕様とコードの整合性」）
2. （観点2: 例「既存 API との後方互換性」）

## 調査対象コード（必要に応じて読む）
- `src/...`

## 背景コンテキスト
- プロジェクト概要 / 関連 PR・Issue 番号
- 既存レビューで対応済みの事項（重複指摘を避けるため）

## 出力先（必須）
最終結果を `/tmp/<cli>-output-タスク名.md` に書き出したうえで、stdout にも同内容を出力すること。

## 出力形式
# タイトル
## 総評
## 1. 観点1 に関する指摘
## 2. 観点2 に関する指摘
## 3. 追加提案
## 4. 承認可否

**必須**: 行番号・ファイルパスに紐付けて具体的に指摘すること。400〜500 行、日本語。
**必須**: tool 呼び出しのみで終了せず、最後に必ず assistant message として 1 回出力すること。
```

### コード生成依頼テンプレート

```markdown
以下の実装タスクを実行してください。

## タスク
（具体的な実装内容）

## 制約
- 技術制約（言語バージョン、依存ライブラリ）
- コーディング規約（ESLint / Prettier / rustfmt 等）
- テスト要件（ユニットテスト必須 等）

## 対象ファイル
- 既存ファイルのパス / 新規ファイルのパス案

## 背景
（なぜこの実装が必要か、設計判断の経緯）

## 完了基準
- [ ] テストがパスする
- [ ] 型チェック / lint がパスする

**必須**: ファイル編集は実際に行い、最後に変更ファイル一覧と要点を
`/tmp/<cli>-output-タスク名.md` に書き出したうえで、stdout にも同内容を出力すること。
tool 呼び出しのみで終了せず、最後に必ず assistant message として 1 回出力すること。
```

## 共通のトラブルシューティング

CLI 固有の症状（サンドボックス失敗、承認モードによるハング等）は補助ファイルを参照。

| 症状 | 原因 | 対処 |
|---|---|---|
| 完了通知が来たのに出力が空 | `&` で起動したラッパーシェルだけが終了し、本体はまだ実行中 | 手順 4 の脱出条件で待ち直す。PID の存在で判定しない |
| 出力の末尾が途切れる | モデルの出力トークン上限 | プロンプトで「400 行以内」など出力サイズを指定、または観点を絞って分割実行 |
| 実行が 15 分以上終わらない | 調査範囲が広すぎる、探索ループに入った | 読むべきファイルを明示リスト化し、スコープ外を明記。必要なら `kill` して再実行 |
| 「ファイルを読めません」と返る | 相対パス指定で cwd が想定と違う | プロンプトには**絶対パス**を書き、CLI 側でも作業ディレクトリを明示する |
| 認証エラー | ログインセッション失効 | 各 CLI のログイン手順をやり直す（補助ファイル参照） |
| ハーネスのシェルタイムアウトで kill される | フォアグラウンド実行のまま長尺タスクを走らせた | 手順 3 のとおり必ずバックグラウンド化する |

## 既知の制約とコスト

1. **ログイン状態**: 初回はログインが必要。未ログインだと即座に失敗する
2. **stderr の肥大**: 思考ログや警告が出るため必ず `2> /tmp/...` へリダイレクトする
3. **API コスト**: トークン従量課金。1 セッションで数千〜数万トークン消費することがあり、短時間で済むタスクには使わない
4. **機密情報**: コードが外部 API へ送信される。社外秘コードの扱いは組織ポリシーに従う
5. **モデル選択**: 既定モデルは時期により変動する。安定性が要るときは明示指定する
6. **サンドボックス無効化フラグ**: Codex の `--dangerously-bypass-approvals-and-sandbox` と Gemini の
   `--yolo` は、任意のシェル実行とファイル編集を無確認で許可する。**Docker / devcontainer / VM /
   CI ランナー / 隔離 worktree などの外部隔離環境内でのみ使用**し、ホスト直接実行や本番リポジトリでは使わない

## 関連

- [references/cli-codex.md](references/cli-codex.md) — Codex CLI 固有の手順
- [references/cli-gemini.md](references/cli-gemini.md) — Gemini CLI 固有の手順
- `/ndf:cross-review` — codex / gemini 両方を並列起動して APPROVE 収束まで回す
- `/ndf:review` — 第二引数に `codex` / `gemini` を指定すると本スキルの手順へ委譲する
- Claude Code 版 `corder` エージェント — 本スキルの手順で Codex CLI を呼び出す独立レビュー担当
- 他の AI CLI（`claude`, `ollama` 等）も同じパターンで利用できる
