# cross-refactoring 4 回目の実機試行

Pull Request #127（NDF v8.5.0）で直した 5 件が実機で成立するかを確かめる。

経緯は次の 3 つにある。

- [issue-113-cross-refactoring-trial-report.md](issue-113-cross-refactoring-trial-report.md) — 1 回目
- [issue-113-cross-refactoring-retrial.md](issue-113-cross-refactoring-retrial.md) — 2 回目
- [issue-113-cross-refactoring-re-retrial.md](issue-113-cross-refactoring-re-retrial.md) — 3 回目

## 目的

3 回目で見つけた 4 件と、投稿の成否を突き合わせない課題を直した。生成物を持つ
リポジトリで進行が止まる不具合 12 は、`--sync-command` を渡す構成でしか踏まないため、
同じ構成でもう一度通す。

## 確かめること

| # | 直したこと | 何が観測できれば通ったと言えるか |
| --- | --- | --- |
| 12 | 生成物の同期コミット | `Chore: 生成物を同期する（cross-refactoring 進行側）` が積まれ、`git add` が落ちない |
| 13 | 同期の後段の失敗 | 失敗しても作業ツリーが綺麗に戻り、次の実行が清浄性の検査で止まらない |
| 14 | 実装担当のコミット | 手順書の迂回手段でコミットが作れる。作れなくても取り込みが 0 件として続行する |
| 15 | 見送り後の読み取り同期 | 次ラウンドの提案が、取り消しで消えた対象を指さない |
| — | 投稿の成否の突き合わせ | cross-review が申告と GitHub 側の実数を照合する |

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
| 使用する版 | リポジトリ内の `plugins/ndf-claude`（v8.5.0） |
| ラウンド上限 | 3 |
| 着手前のテスト | 463 passed |

## 注意

`plugins/ndf-shared/` は編集元であり、`scripts/build-runtime-plugins.sh` で
`ndf-claude` / `ndf-codex` / `ndf-kiro` へ同期する。同期は進行側の責務のため
`--sync-command` で渡す。
