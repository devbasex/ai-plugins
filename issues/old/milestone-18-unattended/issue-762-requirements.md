# #762 agent-layers.md の「実行計画は parallel-work.md が持つ」を issue-plan-strategy へ向ける

- モード: `light`（文言 1 行の修正。本番の振る舞いも構造も変えない）
- 依頼（原文）: 「本数の測り方と実行計画は `parallel-work.md` が持つ。」を、「本数の測り方は `parallel-work.md`、実行計画は `issue-plan-strategy` の `references/execution-plan.md` が持つ」に改める。1 行の文言のみ。

## 受け入れ条件

- [x] AC1: `plugins/ndf/skills/development-workflow/references/agent-layers.md` の「並行の本数」の節で、実行計画の持ち主が `issue-plan-strategy/references/execution-plan.md` へのリンクで示される（`grep -n "execution-plan.md" agent-layers.md` が当該節の行を返す）
- ~~AC2: 本数の測り方は引き続き `parallel-work.md` へのリンクで示される~~ → 「本数を抑える下限は `parallel-work.md`、本数の測り方と実行計画は `execution-plan.md`」へ変更（2026-09-19、cross-review ラウンド 1 の kiro の指摘。`parallel-work.md:111` の境界の表が「本数の測り方の手順」を `issue-plan-strategy` 側と宣言しているため）
- [x] AC2': `parallel-work.md` へのリンクは残り、持ち主は「本数を抑える下限」と書かれる
- [x] AC3: `python3 scripts/check-markdown-links.py` が終了コード 0（相対パスは `../../issue-plan-strategy/references/execution-plan.md`）
- [x] AC4: 変更は当該ファイルの当該 1 文に限る（`git diff --stat` が 1 ファイル）

## 範囲

- 含む: 上記 1 文の文言と参照先
- 含まない: `parallel-work.md` / `execution-plan.md` の本文、他の参照の整理、版数の更新

## 検証手段

`check-markdown-links.py` / `check-doc-line-limit.py` / `check-cross-skill-refs.py` / `instructions-check.py --root .` / `build-runtime-plugins.sh --check` / `development-workflow/tests` の pytest

## 検証結果（2026-09-19 00:34、head e6f0546f）

| 検査 | 結果 |
| --- | --- |
| `check-markdown-links.py` / `check-doc-line-limit.py` / `check-cross-skill-refs.py` | exit=0 |
| `plugins/ndf/scripts/instructions-check.py --root .` / `build-runtime-plugins.sh --check` | exit=0 |
| `check-skill-frontmatter.py` / `check-doc-staleness.py` | exit=0 |
| `uv run --with pytest pytest plugins/ndf/skills/development-workflow/tests -q` | 543 passed / exit=0 |

cross-review: 2 ラウンドで収束（round 1: agy APPROVE / kiro REQUEST_CHANGES 1 件 → 修正 e6f0546f、round 2: codex / kiro APPROVE）。未解決 0 件。PR: https://github.com/devbasex/ai-plugins/pull/776
