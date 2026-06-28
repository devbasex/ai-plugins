---
name: codex
description: "Delegate coding, review, or research to Codex CLI."
when_to_use: "外部 AI へコード生成 / レビュー / 調査を委譲したいとき。Triggers: 'codexで調査', 'codexレビュー', '第二意見レビュー', 'codex exec', 'external AI review'"
---

# Codex 外部AI委譲スキル

## 概要

`codex` CLI（OpenAI Codex、通常は `/usr/bin/codex` または `npm` 経由でインストール）を直接実行して、コード生成・独立レビュー・コードベース調査を外部AIに委譲するためのスキル。

ローカルファイルの逐語照合レビューや大規模コードベース調査に向いている。

## NDFとの関係

- NDFプラグインの `corder` エージェントはこの skill の手順に従って Codex CLI を呼び出す
- v4.0.0 で Codex MCP サーバは廃止。`mcp__codex__*` ツールは存在しない
- 使い分け: 軽量な独立レビュー → `corder` エージェントに委譲。手順の詳細を自分で制御したい or 複雑なプロンプトを出したい → 本 skill を参照して直接 `codex exec` 起動

## いつ使うか

### 使うべきケース
- **独立第二意見レビュー**: 設計書・PR・仕様書を外部AIにレビューさせる（メインエージェントの思考バイアスを避ける）
- **コードベース逐語照合**: 「行番号・関数名・重複箇所の件数」を正確に突き合わせる必要がある場合
- **長時間の調査タスク**: 複数ファイル横断で5〜10分以上かかる調査
- **実装タスクの並列化**: メインエージェントで他作業を進めつつ、別タスクを codex に走らせたい場合

### 使わないべきケース
- 短時間（1〜2分以内）で済むタスク → メインエージェントで直接対応
- ユーザとの対話が必要な設計相談 → Plan Mode等で対話しながら進める
- 単純な質問回答 → WebFetch / WebSearch で足りる
- 機密情報を含むコード → 外部API送信の可否を確認してから

## 前提条件

```bash
# インストール確認
which codex
codex --version

# ログイン状態確認（初回のみ必要）
codex login
```

未インストールの場合は以下でセットアップ:

```bash
# npm 経由
npm install -g @openai/codex

# 動作確認
codex exec --help
```

## 基本実行パターン

### 1. サンドボックス制約（重要）

codex のデフォルトサンドボックスは `bubblewrap (bwrap)` に依存する。以下の環境では bwrap が動作せず、`exec` で実行するシェルコマンドがすべて失敗する:

- **WSL2**（カーネルで `unprivileged_userns_clone` が無効）
- **一部の devcontainer / Docker 環境**（user namespace 非対応）

該当環境では **`--dangerously-bypass-approvals-and-sandbox` を付けて起動**する必要がある。

```bash
# ❌ サンドボックス有効（bwrap 失敗で exec コマンドが全滅）
codex exec -s read-only -C "$PWD"

# ✅ サンドボックスバイパス（外側が既にコンテナ等で隔離されている前提）
codex exec --dangerously-bypass-approvals-and-sandbox -C "$PWD"
```

**判断基準**: 既にDocker / devcontainer / VM / CIランナー等で外部的にサンドボックスされているなら `--dangerously-bypass-approvals-and-sandbox` は実用上安全。ホスト直接実行でコード全書き換えされたくない場合はフラグを付けずに対処（後述「bwrap代替」）。

#### bwrap 代替の有効化（ホスト直接実行時）

```bash
# Debian/Ubuntu 系でホスト user namespace を有効化
sudo sysctl kernel.unprivileged_userns_clone=1

# 永続化
echo 'kernel.unprivileged_userns_clone=1' | sudo tee /etc/sysctl.d/00-local-userns.conf
```

### 2. プロンプトは一時ファイル経由で渡す

長いプロンプトをシェル引数に渡すとエスケープ地獄になるので、**一時ファイル経由でstdinに流す**のが基本。

```bash
# Step 1: プロンプトを一時ファイルに書く
cat > /tmp/codex-prompt.md <<'EOF'
## タスク
以下のファイルを読み込み、設計意図とコードの整合性をレビューしてください。

## 対象ファイル（絶対パスで指定）
/absolute/path/to/design.md

## 出力形式
Markdown で標準出力に吐いてください。
EOF

# Step 2: codex exec に stdin で流す（バックグラウンド実行）
codex exec --dangerously-bypass-approvals-and-sandbox -C "$PWD" \
  < /tmp/codex-prompt.md \
  > /tmp/codex-output.md \
  2> /tmp/codex-err.log &
```

**エージェントからの書き方**: ファイル書き込みツールで `/tmp/codex-prompt.md` を作ってから、シェル実行ツールの「バックグラウンド実行」オプションで codex を起動する。

### 3. 出力ストリームの扱い

codex CLI の出力構造:

| ストリーム | 内容 |
|---|---|
| **stdout** | **最終 assistant message のみ**（Markdown本文）。出ないことがある（後述） |
| **stderr** | プロンプトのエコー + 実行したコマンドと結果 + codexの思考プロセス + `^tokens used$` sentinel |

**実務上の扱い**:
- 最終成果物が欲しい → `stdout` をそのまま採用…**ただし stdout が空になるケースがあるので必ずファイル出力も併用**（下記 3.5 参照）
- codexが何を調べたか追跡したい → `stderr` をデバッグ用に保存

```bash
codex exec ... > /tmp/codex-output.md 2> /tmp/codex-err.log
# 成果物 = /tmp/codex-output.md（stdout が空でないことを必ず確認）
# デバッグ = /tmp/codex-err.log（大きめ、数千行になる）
```

### 3.5 最終出力をファイル経由で保証する（重要）

**Codex CLI（特に `gpt-5-codex` / 高 reasoning_effort）は、長時間調査の末に**
**最終 assistant message を返さずにセッションを終えることがある**。
このとき stdout は空のままになり、stderr のイベントログ（数十万バイト）には
コードを実際に読んだ痕跡だけが残る。`^tokens used$` は出ているのに stdout が空、という状態。

**根本対策: プロンプトに「最終結果は指定ファイルへ書き出すこと」を必須化する。**
Codex は最終 message を返さなくても `apply_patch` ツールでファイルを作成できるため、
ファイル経由なら確実に結果を回収できる。

#### プロンプトに必ず含める指示（テンプレート）

```markdown
## 出力先（必須）

最終的なレビュー / 調査結果を以下のファイルに **必ず書き出してください**:

`/tmp/codex-output-<task-name>.md`

書き出しは `apply_patch` で新規ファイル作成してください。
**stdout への出力だけでは不十分です**（セッション終了で失われる場合があるため）。
書き出し後、念のため stdout にも同じ内容を出力してください（冪等で問題ありません）。
```

#### 回収側の安全パターン

```bash
# 1. ファイルが存在するかを最優先で確認（stdout が空でもこちらに本文が残る）
OUTPUT_FILE=/tmp/codex-output-pr13734-review.md
if [ -s "$OUTPUT_FILE" ]; then
    cat "$OUTPUT_FILE"
elif [ -s /tmp/codex-stdout.md ]; then
    # 2. ファイルがなければ stdout フォールバック
    cat /tmp/codex-stdout.md
else
    # 3. どちらも空なら stderr の末尾から拾う最後の手段
    echo "WARN: Codex の最終出力を回収できませんでした。stderr 末尾を確認してください:" >&2
    tail -200 /tmp/codex-err.log
fi
```

#### 補助対策

- **`reasoning_effort` を `medium` に下げる** (`--config reasoning.effort=medium`)
  `high` だと思考に偏って最終 message を返さなくなる頻度が上がる
- **`--json` モードでイベント採取** (`codex exec --json`)
  JSON Lines で `event.type=assistant_message` を grep すれば確実に取れる
- **強制 summary 指示**: プロンプト末尾に「最後に必ず assistant message として 1 回出力すること、tool 呼び出しのみで終了しないこと」を明記

### 4. バックグラウンド実行 + 待機パターン

codex は **5〜10分かかることが普通**。多くのエージェントハーネスはシェル実行に2〜3分のタイムアウトを課すので、**必ずバックグラウンド実行**する。

```bash
# 1. プロンプトファイル書き出し（ファイル書き込みツール）
# -> /tmp/codex-prompt.md

# 2. codex をバックグラウンドで起動（`&` でシェル自体は即時終了）
codex exec --dangerously-bypass-approvals-and-sandbox -C "$PWD" \
  < /tmp/codex-prompt.md \
  > /tmp/codex-output.md \
  2> /tmp/codex-err.log &

# 3. PID を控える
echo "PID: $!"

# 4. 待機（他の作業を進める or スケジューラで再開）

# 5. 完了検知 — ps -p は zombie に騙される。stderr の "tokens used" sentinel を見る
until grep -q '^tokens used$' /tmp/codex-err.log 2>/dev/null; do
  sleep 30
done
```

**⚠️ 罠**: `&` でバックグラウンド実行するとラッパーシェルは即終了し「タスク完了通知」が発火するが、codex 本体はまだ動いている。**`ps -p $PID` は zombie (defunct) も 0 を返す** ため `until ! ps -p $PID` は永久ループになりうる。`grep -q '^tokens used$' /tmp/codex-err.log` を脱出条件にする (codex が最終回答を吐き終わると stderr 末尾に必ず出る sentinel)。

### 5. 待機間隔のチューニング

エージェントの context cache TTL は通常5分。これを超えると prompt cache がミスして再送料金が発生する:

- **短い間隔**: 60〜270秒（TTL=5分内に収まる、軽量）
- **長い間隔**: 1200秒以上（1回のキャッシュミスを長時間で償却）
- **避けるべき**: 300秒前後（キャッシュミス+短時間の最悪）

codex の典型実行時間（5〜10分）に対しては **270秒ポーリング** か **1200秒一括待ち** の二択。

### 6. プロセス確認・ログ追跡

```bash
# 完了したか (stderr 末尾の "tokens used" が最も信頼できる)
grep -q '^tokens used$' /tmp/codex-err.log && echo DONE

# 最新の作業内容を覗く
tail -30 /tmp/codex-err.log
```

## プロンプト設計のコツ

### 必須要素
1. **対象ファイルの絶対パス**（codexは `nl -ba`, `sed -n`, `rg` 等でファイルを読むため）
2. **調査観点を具体化**（箇条書きで3〜5項目に絞る）
3. **出力形式の指定**（Markdownテンプレートを提示）
4. **スコープ外の明示**（codexが脱線しないため）
5. **最終出力先ファイルの指定（必須）**: `/tmp/codex-output-<task>.md` のような明示パスへ
   **`apply_patch` で必ず書き出させる**。stdout だけに頼ると最終 message が落ちて空になる事故が起きる（3.5 節参照）
6. **stdout にも同内容を吐く指示**: ファイル書き出し後、念のため stdout にもエコーさせる（冪等）

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

以下を Markdown で**`/tmp/codex-output-<task-name>.md` に必ず書き出してください**
（`apply_patch` で新規ファイル作成）。書き出し後、stdout にも同内容を出力してください。
**stdout のみへの出力は不可**（セッション終了時に失われる場合があるため）:

# <タイトル>

## 総評
## 1. <観点1> に関する指摘
### 1.1 正確な主張
### 1.2 訂正推奨
## 2. <観点2> に関する指摘
## 3. 追加提案
## 4. 承認可否

**必須**: 行番号・ファイルパスに紐付けて具体的に指摘してください。400〜500行程度、日本語で出力してください。
**必須**: tool 呼び出しのみで終了せず、最後に必ず assistant message として 1 回出力してください。
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
`/tmp/codex-output-<task-name>.md` に書き出してください（`apply_patch` で新規作成）。
書き出し後、stdout にも同内容を出力してください。
**stdout のみへの出力は不可**（セッション終了時に失われる場合があるため）。
tool 呼び出しのみで終了せず、最後に必ず assistant message として 1 回出力してください。
```

## 実例: レビュー依頼の完全フロー

```bash
# === 1. プロンプト書き出し ===
# ポイント: 最終出力先ファイルをプロンプト内で明示し、apply_patch で書かせる
FINAL=/tmp/codex-output-api-v2-review.md

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

## 出力先（必須）

最終的なレビュー結果を **必ず** 以下のファイルに書き出してください:

\`${FINAL}\`

\`apply_patch\` で新規ファイル作成してください。
**stdout への出力だけでは不十分です**（セッション終了時に失われる場合があるため）。
書き出し後、念のため stdout にも同じ内容を出力してください（冪等で問題ありません）。

## 出力形式
Markdown で 400〜500 行、日本語。tool 呼び出しのみで終了せず、最後に必ず assistant message として 1 回出力してください。
EOF

# === 2. バックグラウンド起動（reasoning_effort=medium 推奨） ===
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
echo DONE

# === 4. 成果物を安全に回収（ファイル優先 → stdout fallback） ===
if [ -s "$FINAL" ]; then
    cp "$FINAL" ./review-result.md
    echo "✅ Codex 書き出しファイルから回収"
elif [ -s /tmp/codex-stdout.md ]; then
    cp /tmp/codex-stdout.md ./review-result.md
    echo "⚠ stdout からフォールバック回収（ファイル書き出しなし）"
else
    echo "❌ Codex の最終出力を回収できませんでした。stderr 末尾を確認してください:" >&2
    tail -200 /tmp/codex-err.log
    exit 1
fi
```

## トラブルシューティング

### Q1. stdoutが空でstderrに大量のexecログだけある
**原因**: codex がまだ最終回答を出す前に停止した、または **最終 assistant message を出さずにセッションが終わった**（gpt-5-codex の高 reasoning_effort で発生しやすい既知挙動）。

**対処**:
- `grep -q '^tokens used$' /tmp/codex-err.log` で終了 sentinel が出ているか確認（まだなら動作中なので追加待機）
- 出ているのに stdout が空 → セッション終了で最終 message が失われたケース。**3.5 節「最終出力をファイル経由で保証する」のパターンでリトライ必須**:
  - プロンプトに `apply_patch` で `/tmp/codex-output-<task>.md` へ必ず書き出させる指示を追加
  - 回収側は「ファイル → stdout → stderr」の三段フォールバックで取りこぼしを防ぐ
  - 補助で `--config reasoning.effort=medium` も付けると最終 message を返す傾向が上がる

### Q2. `bwrap: No permissions to create a new namespace` で exec 失敗
**原因**: `--dangerously-bypass-approvals-and-sandbox` を付け忘れ、かつ環境が user namespace 非対応。

**対処**:
- フラグを追加して再実行
- `-s read-only` / `-s workspace-write` も bwrap を使うので同じ結果になる点に注意
- ホストで user namespace を有効化する方法は「サンドボックス制約」節を参照

### Q3. codexが「ファイルを読めません」と返してくる
**原因**:
- サンドボックス有効でファイル読み取りに失敗
- プロンプトで相対パスを指定し、codexの cwd が想定と違った

**対処**:
- `--dangerously-bypass-approvals-and-sandbox` を追加
- プロンプトには**絶対パス**を書く
- `-C <workdir>` で cwd を明示

### Q4. タスク完了通知が来たのに出力が空 / wait loop が抜けない
**原因**: `&` で起動したラッパーシェルが先に終了して通知が出ているだけで、codex 本体は動作中。または既に終わっているが zombie (defunct) として残っており `ps -p $PID` が 0 を返し続けている。

**対処**: 検知を「PID の存在」ではなく **stderr の `^tokens used$` sentinel** で行う。codex は最終回答を吐き終えると必ずこの行を stderr に書く。

```bash
# ❌ 永久ループ化しうる
until ! ps -p $PID; do sleep 30; done

# ✅ zombie 安全
until grep -q '^tokens used$' /tmp/codex-err.log 2>/dev/null; do
  sleep 30
done
```

### Q5. codex実行が15分以上かかる
**原因**: プロンプトで広すぎる調査範囲を指定した、または codex が探索ループに入った。

**対処**:
- プロンプトで「読むべきファイル」を明示リスト化
- スコープ外を明記（「〇〇には踏み込まない」）
- 必要なら `kill <PID>` で打ち切り、プロンプトを絞り込んで再実行

### Q6. stdoutの末尾が途切れている
**原因**: codex がトークン上限に達した可能性。

**対処**: プロンプトで「400行以内」など出力サイズを指定。または観点を絞って再実行。

### Q7. 認証エラー (`Unauthorized` / `token expired`)
**原因**: ログインセッション失効。

**対処**:
```bash
codex logout
codex login
```

## corder エージェント経由との使い分け

本スキルは CLI を直接呼び出す詳細手順を記述している。簡易に独立レビューを取りたいだけなら `corder` エージェントに委譲した方が手間が少ない:

| 観点 | corder エージェント | 本スキルで直接 CLI 起動 |
|---|---|---|
| 使い勝手 | `Agent(subagent_type: "corder", ...)` で委譲するだけ | プロンプト書き出し・バックグラウンド起動・PID 管理を自分で制御 |
| プロンプト制御 | corder 側で整形 | 自由に設計可 |
| バックグラウンド実行 | agent 側が制御 | `&` で非同期化、他作業と並列 |
| スケジュール連携 | 難しい | `/schedule` / `Monitor` と組み合わせやすい |

**指針**: 迷ったら corder 経由。プロンプト細部や非同期タイミングを自分で握りたい場合のみ本スキルの手順で直接起動。

## 既知の制約とコスト

1. **サンドボックス非対応環境**: `--dangerously-bypass-approvals-and-sandbox` で回避必須
2. **stderrに全思考が書かれる**: 数千行になりうるので必ず `2> /tmp/...` にリダイレクト
3. **ログイン状態**: 初回は `codex login` が必要。未ログインだと即座に失敗する
4. **セッション復旧**: 長時間ジョブで親エージェントが再起動した場合、`codex resume` でセッション再開可能
5. **APIコスト**: トークン従量課金のため、短時間で済むタスクには使わない。1セッションで数千〜数万トークン消費することがある
6. **機密情報**: 外部APIにコードが送信されるため、社外秘コードの扱いは組織ポリシーに従うこと

## 関連

- **NDF `corder` エージェント**: 本スキルの手順で Codex CLI を呼び出す独立レビュー担当 (v4.0.0 以降は MCP ではなく CLI 経由)
- **OpenAI Codex CLI公式ドキュメント**: `codex --help` / `codex exec --help`
- **他のAI委譲方法**: `gemini`, `claude`, `ollama` 等のCLI も同様のパターンで利用可
