# cross-refactoring 実機試行（対象: refactor.py と収束ループ共通層）

`issues/issue-113-cross-refactoring` でリリースした `/ndf:cross-refactoring` を、
自分自身のスクリプト群に対して実行して検証する。

## 対象範囲

| パス | 行数 | 内容 |
| --- | --- | --- |
| `plugins/ndf-shared/skills/cross-refactoring/scripts/` | 2,511 | `refactor.py` / `launch-cli.sh` / `prepare-worktrees.sh` |
| `plugins/ndf-shared/skills/cross-review/scripts/lib/` | 1,497 | `monitor.py` / `assignment.py` / `models.py` / `metrics.py` / `statefile.py` |

## 実行条件

| 項目 | 値 |
| --- | --- |
| 提案ラウンド上限 | 3（既定） |
| モデル | 各 CLI の既定（kiro は `auto` のため計測集計から分離される） |
| ベースラインテスト | `uv run --with pytest python -m pytest plugins/ndf-shared/skills/cross-refactoring/tests plugins/ndf-shared/skills/cross-review/tests -q` |
| 着手前の状態 | 387 passed |

## 注意

`plugins/ndf-shared/` は編集元であり、`scripts/build-runtime-plugins.sh` で
`ndf-claude` / `ndf-codex` / `ndf-kiro` へ同期する。CI が `--check` で一致を検査するため、
ラウンド収束後にホストが同期コミットを 1 件加える。
