---
name: external-ai
description: "Delegate coding, review, or research to the codex, agy, kiro-cli, or claude CLI. Use when a second opinion or an offloaded investigation is wanted（codexで調査・agyレビュー・外部AIに投げて）."
---

# 外部 AI 委譲スキル (Codex / agy / Kiro / Claude)

## 概要

`codex` CLI（OpenAI Codex）、`agy` CLI（Google Antigravity）、`kiro-cli`（Kiro CLI）、
`claude` CLI（Claude Code のヘッドレス実行）をローカルから直接起動し、
コード生成・独立第二意見レビュー・大規模コードベース調査を外部 AI に委譲する。

起動・上限つきの待ち・回収は `scripts/external-ai.py` の 1 本が 4 つの CLI をまとめて行う。
起動フラグ・完了検知・出力回収など **CLI 固有の事情は補助ファイルに分離** している。

| 補助ファイル | 内容 |
|---|---|
| [references/cli-codex.md](references/cli-codex.md) | Codex CLI のインストール、サンドボックス制約、`codex exec` の起動の形、sentinel 完了検知、最終 message 欠落対策 |
| [references/cli-agy.md](references/cli-agy.md) | agy CLI のインストール、作業領域の宣言、`-p=<本文>` の渡し方、実行時間の上限、プロセス終了による完了検知 |
| [references/cli-kiro.md](references/cli-kiro.md) | Kiro CLI の非対話実行、**終了コードが成否を表さない**こと、ANSI エスケープ除去、ツール絞り込みを使わない理由、Skill 本文を読ませる明示指定 |
| [references/cli-claude.md](references/cli-claude.md) | `claude -p` のヘッドレス実行、root 実行での権限モード制約、`--output-format json` による完了検知と実測モデルの取得 |

**同じランタイムをホストにしていても、CLI として起動すればホストの作業文脈からは
切り離される。** Claude Code から `claude -p` を別プロセスで起動する経路が実際に使われて
いる（`/ndf:cross-refactoring` の適用フェーズ）。

## NDF との関係

- Claude Code 版の `corder` エージェントは Codex CLI を呼び出す
- `/ndf:pr-review <PR番号> codex` / `/ndf:pr-review <PR番号> agy` の委譲先として利用される
- `/ndf:cross-review` は codex / agy を**並列に起動**して両者の APPROVE 収束を待つ
- v4.0.0 で Codex MCP サーバは廃止。`mcp__codex__*` ツールは存在しない
- agy 専用エージェントは未整備。委譲時はメインエージェントから本スキルを参照して直接 CLI を起動する

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

## どの CLI を選ぶか

| 観点 | Codex | agy | Kiro | Claude |
|---|---|---|---|---|
| stdout の信頼性 | 最終 message が落ちることがある（ファイル書き出し必須） | stdout に response が直接出る | ANSI エスケープが必ず混ざる | `--output-format json` で構造化される |
| 承認の与え方 | `--dangerously-bypass-approvals-and-sandbox` | `--dangerously-skip-permissions`（**作業領域の外への書き込みは止まらない**） | `--trust-all-tools`（**絞り込みは防御にならない**） | `--permission-mode acceptEdits` + `--allowed-tools` |
| 非対話実行 | `codex exec` で完結。プロンプトは**標準入力必須** | `-p=<本文>` で渡す。**標準入力は受け取らない** | `chat --no-interactive` | `-p` |
| 完了判定 | stderr の `^tokens used$` sentinel | プロセス終了（`kill -0` / `wait`） | **終了コードは使えない。** 結果ファイルと stderr の照合 | JSON の `is_error` / `subtype` |
| 実測モデルの取得 | できない | できない | **できない**（既定 `auto` は特に不可） | `modelUsage` から取れる |
| 典型実行時間 | 5〜10 分 | 数十秒〜5 分 | 数分 | 数分（29 ターンで 218 秒の実測） |
| 強み | コード逐語照合、長時間の深い調査 | 横断調査、長文生成、軽量タスク。作業領域を明示的に区切れる | claude 系 / gpt 系のモデルを同じハーネスで選べる | 手順書（Skill）への追従が最も安定 |
| 弱み | セットアップ・運用が煩雑 | 高難度コード解析でやや浅くなることがある | **Skill を配置しても本文を読まない**（明示パスが必須） | 実行コストが高い（1 件 1.42 ドルの実測） |

**指針**:

- 行番号・件数の逐語確認が要る → Codex
- 短時間で済む独立レビュー、横断調査、長文生成 → agy
- 手順書どおりに直させたい → Claude（追従が最も安定。ただしコストが高い）
- ハーネスを固定してモデルだけ比べたい → Kiro（claude 系と gpt 系の両方を提供する）
- 第二意見を確実に取りたい → 複数を走らせてクロスチェック（`/ndf:cross-review` と
  `/ndf:cross-refactoring` が自動化している）
- Codex がレート制限・サンドボックス制約に当たった → agy へ代替

`corder` エージェントとの使い分けは次のとおり。

| 観点 | `corder` エージェント経由 | 本スキルで直接 CLI 起動 |
|---|---|---|
| 使い勝手 | エージェントに委譲するだけ | プロンプトを書き、`external-ai.py run` を 1 行打つ |
| プロンプト制御 | corder 側で整形 | 自由に設計可 |
| スケジュール連携 | 難しい | `/schedule` / `Monitor` と組み合わせやすい |

迷ったら `corder` 経由。プロンプトの細部を自分で握りたい場合に直接起動する。

## 共通の実行手順

LLM が決めるのは **どの CLI に渡すか**（上の表）と **プロンプトの中身**、**結果の解釈**だけである。
起動・上限つきの待ち・回収は次のコマンドが行う。

1. プロンプトをファイルへ書く（ファイル書き込みツールで。テンプレートは「プロンプト設計」）
2. 次を打つ（`$SKILL_DIR` はこの Skill のディレクトリ）。Claude Code では Bash の
   `run_in_background: true` で起動し、完了通知を 1 回受ける

   ```bash
   python3 "$SKILL_DIR/scripts/external-ai.py" run codex \
     --prompt-file "$TMP/review-prompt.md" --output-file "$TMP/codex-review.md" \
     --phase review --workdir "$PWD" [--model M]
   ```

3. 最後の 1 行の JSON の `status` を見る

| `status` | `metrics.outcome` | 次の手 |
|---|---|---|
| `ok` | `ok` | `metrics.result` のファイルを読む（`metrics.source` は `file` / `stdout`） |
| `stopped` | `no_result` | `next` の stderr の末尾を読み、プロンプトを直すか別の CLI へ渡す |
| `stopped` | `timeout` / `stalled` | 読むファイルを絞るか観点を分けて渡し直す |
| `stopped` | `usage_limit` | 同じ CLI では解けない。別の CLI へ渡す |
| `stopped` | `auth` / `missing_cli` | 補助ファイルのログイン・インストールの手順を行う（終了コード 3） |
| `stopped` | `early_error` / `launch_failed` | `metrics.detail` を読む |

- 使えるかだけを先に知りたいときは `external-ai.py check <runtime>`（CLI の有無と認証）
- 上限は `limits.py` の工程の値（`--phase`）で、`--timeout` / `--stall-timeout` で狭められる。
  上限を超えると CLI を止めて必ず終わる
- 回収は結果ファイル → stdout（claude は JSON の `result`、kiro は ANSI を除く） → stderr の末尾の順で、
  回収した本文は `--output-file` に置く。プロンプトに出力先の指示が無ければ末尾へ足す
- 監視の記録は `metrics.stem` の `-monitor.json` にあり、`metrics.monitor_status` / `metrics.reason` と一致する
- 一時ファイルは `NDF_EXTERNAL_AI_TMP_DIR`（既定は一時ディレクトリの `ndf/external-ai/`）に、
  起動ごとに固有の名前で置く

## プロンプト設計

### 必須要素

1. **対象ファイルの絶対パス**（外部 AI は `nl -ba` / `sed -n` / `rg` 等でファイルを読む）
2. **調査観点を具体化**（箇条書きで 3〜5 項目に絞る）
3. **出力形式の指定**（Markdown テンプレートを提示）
4. **スコープ外の明示**（脱線防止）
5. **出力サイズの目安**（例: 400〜500 行）
6. **最終出力先ファイルの指定**: `--output-file` に渡すパスへ書き出させる。
   Codex は `apply_patch`、agy は `write_to_file` を使う
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
最終結果を `--output-file` のパス に書き出したうえで、stdout にも同内容を出力すること。

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
`--output-file` のパス に書き出したうえで、stdout にも同内容を出力すること。
tool 呼び出しのみで終了せず、最後に必ず assistant message として 1 回出力すること。
```

## 共通のトラブルシューティング

CLI 固有の症状（サンドボックス失敗、承認モードによるハング等）は補助ファイルを参照。

| 症状 | 原因 | 対処 |
|---|---|---|
| `no_result` で終わる | tool 呼び出しだけで終わった | プロンプトに出力先と「最後に必ず 1 回出力する」を書く |
| 出力の末尾が途切れる | モデルの出力トークン上限 | プロンプトで「400 行以内」など出力サイズを指定、または観点を絞って分割実行 |
| `timeout` / `stalled` で終わる | 調査範囲が広すぎる、探索ループに入った | 読むべきファイルを明示リスト化し、スコープ外を明記して渡し直す |
| 「ファイルを読めません」と返る | 相対パス指定で cwd が想定と違う | プロンプトには**絶対パス**を書き、`--workdir` を渡す |
| `auth` で終わる | ログインセッション失効 | 各 CLI のログイン手順をやり直す（補助ファイル参照） |

## 既知の制約とコスト

1. **ログイン状態**: 初回はログインが必要。未ログインだと即座に失敗する
2. **stderr の肥大**: 思考ログや警告が出る。`external-ai.py` が `<stem>-err.log` へ逃がす
3. **API コスト**: トークン従量課金。1 セッションで数千〜数万トークン消費することがあり、短時間で済むタスクには使わない
4. **機密情報**: コードが外部 API へ送信される。社外秘コードの扱いは組織ポリシーに従う
5. **モデル選択**: 既定モデルは時期により変動する。安定性が要るときは明示指定する
6. **サンドボックス無効化フラグ**: Codex の `--dangerously-bypass-approvals-and-sandbox` と agy の
   `--dangerously-skip-permissions` は、任意のシェル実行とファイル編集を無確認で許可する。**Docker / devcontainer / VM /
   CI ランナー / 隔離 worktree などの外部隔離環境内でのみ使用**し、ホスト直接実行や本番リポジトリでは使わない

## 関連

- [references/cli-codex.md](references/cli-codex.md) — Codex CLI 固有の手順
- [references/cli-agy.md](references/cli-agy.md) — agy CLI 固有の手順
- [references/cli-kiro.md](references/cli-kiro.md) — Kiro CLI 固有の手順
- [references/cli-claude.md](references/cli-claude.md) — `claude -p` 固有の手順
- `/ndf:cross-review` — codex / agy 両方を並列起動して APPROVE 収束まで回す
- `/ndf:cross-refactoring` — 4 CLI を役割ごとに分担させ、リファクタリングを収束させる
- `/ndf:pr-review` — 第二引数に `codex` / `agy` を指定すると本スキルの手順へ委譲する
- Claude Code 版 `corder` エージェント — 本スキルの手順で Codex CLI を呼び出す独立レビュー担当
- 他の AI CLI（`claude`, `ollama` 等）も同じパターンで利用できる
