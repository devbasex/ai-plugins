# cross-refactoring 6 回目の実機試行

Pull Request #133 / #134 / #135（NDF v8.5.2〜v8.5.4）で直した 4 件が実機で成立するかを
確かめる。あわせて、5 回の試行で一度も到達していない経路まで通す。

経緯は次の 5 つにある。

- [issue-113-cross-refactoring-trial-report.md](issue-113-cross-refactoring-trial-report.md) — 1 回目
- [issue-113-cross-refactoring-retrial.md](issue-113-cross-refactoring-retrial.md) — 2 回目
- [issue-113-cross-refactoring-re-retrial.md](issue-113-cross-refactoring-re-retrial.md) — 3 回目
- [issue-113-cross-refactoring-4th-trial-report.md](issue-113-cross-refactoring-4th-trial-report.md) — 4 回目
- [issue-113-cross-refactoring-5th-trial-report.md](issue-113-cross-refactoring-5th-trial-report.md) — 5 回目

## 目的

5 回目はレビューの投稿が成立せず、レビュー結果の欠落を理由に 2 ラウンド目で中断した。
投稿にまつわる 3 件と差分予算の 1 件を直したので、同じ構成でもう一度通す。

## 確かめること

| # | 直したこと | 何が観測できれば通ったと言えるか |
| --- | --- | --- |
| 18 | 自分の Pull Request では投稿の event を `COMMENT` へ倒す | レビュー担当が `HTTP 422` を受けずに投稿でき、結果ファイルの判定は `APPROVE` / `REQUEST_CHANGES` のまま残る |
| 19 | 投稿に失敗しても結果ファイルを書く | 投稿が失敗したラウンドで `post_error` 付きの結果ファイルが残り、担当の停止と投稿の失敗を区別できる |
| 20 | 投稿されたことを GitHub 側で確かめる | 結果ファイルのレビュー URL が必須になり、GitHub 側に無い判定は差し戻される |
| 21 | 抽出系の手法の差分予算 | `long_method` の抽出が見積の 2 倍をわずかに超えても採用され、範囲外を触った変更は落ちる |

## 到達したい範囲

5 回の試行で一度も到達していない。

| 対象 | 内容 |
| --- | --- |
| 提案の収束 | 新しい提案が出なくなって `advance` が繰り返しを終えるところ |
| 修正ラウンドの上限 | `should-abandon` から `abandon-items` へ進み、指摘が残る項目だけを取り消すところ |
| Step 7 の関門 | 収束後に `/ndf:cross-review` を Pull Request 全体へ実行するところ |
| Draft の解除 | Step 8 の報告と Draft 解除 |

## 対象範囲

| パス | 内容 |
| --- | --- |
| `plugins/ndf-shared/skills/cross-refactoring/scripts/` | `refactor.py` / `launch-cli.sh` / `prepare-worktrees.sh` |
| `plugins/ndf-shared/skills/cross-refactoring/tests/` | 現状固定テスト |
| `plugins/ndf-shared/skills/cross-review/scripts/lib/` | 収束ループの共通層 |
| `plugins/ndf-shared/skills/cross-review/tests/` | 現状固定テスト |

## 実行条件

| 項目 | 値 |
| --- | --- |
| ホスト | Claude Code（提案・レビューには不参加） |
| 提案・レビュー | codex / gemini / kiro |
| 適用の母集合 | claude / codex / kiro |
| 使用する版 | プラグインキャッシュの v8.5.4 |
| kiro のモデル | `claude-sonnet-5`（既定の `auto` は集計から分離されるため指定する） |
| ラウンド上限 | 3 |
| 着手前のテスト | 500 passed |

```bash
/ndf:cross-refactoring <PR番号> \
  --scope plugins/ndf-shared/skills/cross-refactoring/scripts \
          plugins/ndf-shared/skills/cross-refactoring/tests \
          plugins/ndf-shared/skills/cross-review/scripts/lib \
          plugins/ndf-shared/skills/cross-review/tests \
  --model kiro=claude-sonnet-5 \
  --sync-command "bash scripts/build-runtime-plugins.sh" \
  --baseline-test "uv run --with pytest python -m pytest \
      plugins/ndf-shared/skills/cross-refactoring/tests \
      plugins/ndf-shared/skills/cross-review/tests -q" \
  --max-outer-rounds 3
```

## 注意

`plugins/ndf-shared/` は編集元であり、`scripts/build-runtime-plugins.sh` で
`ndf-claude` / `ndf-codex` / `ndf-kiro` へ同期する。同期は進行側の責務のため
`--sync-command` で渡す。
