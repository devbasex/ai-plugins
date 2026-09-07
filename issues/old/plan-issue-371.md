# 371: レビュワーの母集合と終了基準を揃える

## 関連リンク

- 設計: [parallel-batch-08/02-issue-371.md](parallel-batch-08/02-issue-371.md)
- 全体の境界と順序: [parallel-batch-08/00-overview.md](parallel-batch-08/00-overview.md)
- 課題: #371

## モード

`architecture`。`cross-review` の公開の引数（`--host`）と状態ファイルの形式を変え、
共通層（`lib/`）へ 2 つの部品を足す。

## 目的と非目的

達成したい状態:

- レビュワーの母集合が「全ランタイム − ホスト」で定義され、ホストを指定できる
- 参加する CLI の認証を起動前に確認する
- 終了基準が「新しい指摘が出ない」で書かれ、振動検知・引き継ぎ判定との順序が 1 箇所にある

やらないこと:

- 母集合の 3 者すべてを毎ラウンド起動する（設計の決定 2）
- ラウンド上限の既定を変える（決定 5）
- 判定の中身（何を採用するか）を置き換える。#156 が扱う

## 受け入れ条件

- [ ] 1. `review_assign` がホストを含まない 2 者を返す（4 つのホストすべてで単体テスト）
- [ ] 2. `--host` を渡した `init` が状態へ `host` と `host_source` を書く
- [ ] 3. ホストを推定できないとき `init` が失敗する
- [ ] 4. 認証を起動前に確認し、未認証なら中断する。`NDF_SKIP_AUTH_CHECK=1` で飛ばせる
- [ ] 5. 前のラウンドと同じ指摘だけが残ったラウンドで `judge` が収束（0）を返す
- [ ] 6. 指摘が 0 件のラウンドは初回でも収束する
- [ ] 7. 重複率 1.0 のラウンドが収束し、振動として中断しない
- [ ] 8. 引き継いだ指摘があるラウンドは、新規 0 件でも修正へ回る
- [ ] 9. `host` を持たない状態ファイルで `reviewers` が `["codex", "agy"]` になる
- [ ] 10. 既存の 39 本（461 件）が通る
- [ ] 11. 状態ファイルの差分が `docs/04-contracts.md` に書かれている

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `--host` | 追加 | 省略時は環境変数から推定。推定できなければ失敗する |
| `--only` | 受け付ける値を 4 つへ広げる | 追加のみ |
| 状態ファイル | `host` / `host_source` / `rounds[].reviewers` を追加 | 無いときは 2 者として読む |
| `judge` の出力 | `CODEX_INTENT` / `AGY_INTENT` を `REVIEWER_INTENTS` へ | **呼び出し側の手順を同時に直す** |
| `launch-codex.sh` / `launch-agy.sh` | 新しい入口への委譲 | 呼び出し側は変わらない |

## 修正対象

- `plugins/ndf/scripts/lib/assignment.py`
- `plugins/ndf/scripts/lib/auth.py`（新規）
- `plugins/ndf/skills/cross-refactoring/scripts/refactor.py`（認証確認の委譲）
- `plugins/ndf/skills/cross-review/scripts/state.py`
- `plugins/ndf/skills/cross-review/scripts/launch-reviewer.sh`（新規）
- `plugins/ndf/skills/cross-review/scripts/launch-codex.sh` / `launch-agy.sh`
- `plugins/ndf/skills/cross-review/SKILL.md`
- `plugins/ndf/skills/cross-review/docs/01-state-and-review.md`
- `plugins/ndf/skills/cross-review/docs/04-contracts.md`

## タスク分解

### Task 1: 母集合から 2 者を選ぶ

- **対象ファイル:** `lib/assignment.py`、`cross-refactoring/tests/test_assignment.py`
- **変更内容:** `review_assign(round_no, host)` を足す。母集合は `review_pool(host)` の 3 者で、
  外す 1 者を `(round_no - 1) % 3` で回す
- **満たす受け入れ条件:** 1
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 2: 認証の確認を共通層へ移す

- **対象ファイル:** `lib/auth.py`（新規）、`refactor.py`
- **変更内容:** `AUTH_PROBES` / `UNAUTHENTICATED_MARKERS` / `check_auth` を移し、
  `cross-refactoring` は委譲にする
- **満たす受け入れ条件:** 4（移設のみ。振る舞いは変えない）
- **進め方:** 既存テスト（`test_init.py`）を現状固定として使い、移設後も通ることを確かめる

### Task 3: ホストの確定と認証の確認を `init` へ

- **対象ファイル:** `state.py`、`cross-review/tests/`（新規テスト）
- **変更内容:** `--host` を受け取り、`detect_host` で確定して状態へ書く。`check_auth` を呼ぶ
- **満たす受け入れ条件:** 2 / 3 / 4
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 4: ラウンドの担当を決め、判定の出力を担当名で返す

- **対象ファイル:** `state.py`、テスト
- **変更内容:** `start-round` が `review_assign` で 2 者を決めて状態へ書く。`AGENTS` の固定を
  ラウンドの `reviewers` から読む形へ置き換え、`judge` は `REVIEWER_INTENTS` を出す
- **満たす受け入れ条件:** 9 / 10
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 5: 終了基準を新規の指摘で決める

- **対象ファイル:** `state.py`、テスト
- **変更内容:** 指摘を一致の判定に使える形へ揃える `_finding_key` を切り出し、振動検知と
  共有する。`judge` が新規の指摘の件数で収束を判定する
- **満たす受け入れ条件:** 5 / 6 / 7 / 8
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 6: レビュワーの起動を 1 本の入口へ

- **対象ファイル:** `launch-reviewer.sh`（新規）、`launch-codex.sh` / `launch-agy.sh`
- **変更内容:** 共通層の `lib/launch-cli.sh` を通す入口を作り、既存の 2 本を薄い委譲にする
- **満たす受け入れ条件:** 10
- **進め方:** 既存テストを現状固定として使う

### Task 7: 手順書を書き換える

- **対象ファイル:** `SKILL.md`、`docs/01-state-and-review.md`、`docs/04-contracts.md`
- **変更内容:** 母集合・担当の決め方・終了基準の 3 層・状態ファイルの差分を書く
- **満たす受け入れ条件:** 11
- **進め方:** 文書のみ

## 影響範囲

- `cross-review` を呼ぶ利用者の手順（`--host` の指定が要る場合がある）
- `cross-refactoring` の認証確認（実装の位置が変わる。振る舞いは同じ）
- 中断した実行の再開（状態ファイルの既定で保つ）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| ホストの推定を誤り、自分自身をレビュワーへ含める | 推定できないときは既定を置かず失敗する |
| 再開時に担当が入れ替わる | `host` を持たない状態ファイルは 2 者として読む |
| 新規性の判定で、修正されていない指摘を無視して収束する | 最終スイープが未解決スレッドを解消する。再提出は Pull Request に残る |

## 切り戻し手順

- 変更は 1 つの Pull Request に載る。取り消しは revert で足りる
- 状態ファイルへ足すキーは追加のみで、古い版が読んでも無視される

## 完了の定義

- [ ] 受け入れ条件 11 件をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest . -q` が通る
- [ ] `bash scripts/validate-runtime-plugins.sh` が通る
