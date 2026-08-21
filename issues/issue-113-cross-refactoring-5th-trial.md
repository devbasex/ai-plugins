# cross-refactoring 5 回目の実機試行

Pull Request #130（NDF v8.5.1）で直した 2 件が実機で成立するかを確かめる。

経緯は次の 4 つにある。

- [issue-113-cross-refactoring-trial-report.md](issue-113-cross-refactoring-trial-report.md) — 1 回目
- [issue-113-cross-refactoring-retrial.md](issue-113-cross-refactoring-retrial.md) — 2 回目
- [issue-113-cross-refactoring-re-retrial.md](issue-113-cross-refactoring-re-retrial.md) — 3 回目
- [issue-113-cross-refactoring-4th-trial-report.md](issue-113-cross-refactoring-4th-trial-report.md) — 4 回目

## 目的

4 回目はラウンド 2 の再レビューで進行が終わらなくなり、手で停止した。修正フェーズの
起点の記録と、レビュー結果を残さなかった担当がいるときの扱いを直したため、同じ構成で
もう一度通す。

## 確かめること

| # | 直したこと | 何が観測できれば通ったと言えるか |
| --- | --- | --- |
| 16 | 修正フェーズの起点の記録 | 差し戻しの上限から変更要求へ落ちる経路でも起点が残り、修正の範囲を確定できる。確定できないときも修正ラウンドが増え、上限で見送りへ到達する |
| 17 | レビュー結果の欠落の扱い | 結果を残さなかったレビュー担当がいるラウンドは、実装担当への変更要求にせず中断する |
| — | 収束 | ラウンド上限に達するか、新しい提案が出なくなって終わる |

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
| 使用する版 | プラグインキャッシュの v8.5.1 |
| ラウンド上限 | 3 |
| 着手前のテスト | 466 passed |

## 注意

`plugins/ndf-shared/` は編集元であり、`scripts/build-runtime-plugins.sh` で
`ndf-claude` / `ndf-codex` / `ndf-kiro` へ同期する。同期は進行側の責務のため
`--sync-command` で渡す。
