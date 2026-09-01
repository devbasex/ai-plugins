# 並行開発 バッチ 03 — 全体指示

4 件の課題を同時に進めるための指示書である。各担当は自分の担当ファイルだけを読めば着手できる。
この文書は、担当どうしが同じ行を書き換えないための境界と、着手の順序を定める。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 担当 | 1 件の課題を最後まで進める実行主体。1 担当 = 1 作業ツリー = 1 Pull Request |
| 検査 | `scripts/` 配下のスクリプトが行う機械的な突き合わせ。値が食い違えば 0 以外の終了コードで終わる |
| 工程 | `development-workflow` が振り分ける開発の段階 |
| 進行 | `cross-review` が回すラウンドの進み方。収束するまで繰り返す |

## 担当と課題

| 担当 | 課題 | 指示書 | ブランチ | モード |
| --- | --- | --- | --- | --- |
| A | pytest のテストを CI で実行する（#182） | [01-issue-182.md](01-issue-182.md) | `feature/issue-182-pytest-in-ci` | `light` |
| B | 結果を残さないレビューを収束と判定しない（#196） | [02-issue-196.md](02-issue-196.md) | `fix/issue-196-no-result-not-approve` | `standard` |
| C | ラウンド開始時に作業ツリーを head へ同期する（#217） | [03-issue-217.md](03-issue-217.md) | `fix/issue-217-sync-on-round-start` | `standard` |
| D | 説明文書の本文の版数を検査の対象へ入れる（#209） | [04-issue-209.md](04-issue-209.md) | `feature/issue-209-version-staleness` | `standard` |

```text
担当 A（light）
  worktree → 限定的な検証 → quality-gates → pr → merged → release

担当 B / C / D（standard）
  worktree → requirements-design → design → 設計レビュー → 実装用の worktree
    → implementation-plan → tdd-cycle → refactoring → cross-review
    → quality-gates → pr → plan-to-spec（仕様が変わった場合） → merged → release
    → release-verification（マージ前に実施できなかった受け入れ条件がある場合）
    → retrospective
```

## 設計レビューを 1 本にまとめる

`standard` の 3 件は設計 Pull Request が必須である。**この 3 件は 1 本の設計 Pull Request に
まとめる。** 設計文書はいずれも `issues/parallel-batch-03/` の別々のファイルにあり、同じ行を
書き換えない。レビューの観点も「3 件の設計が妥当か」で共通する。

分けた場合、レビューの回数は設計 3 回と実装 3 回の計 6 回になる。まとめると 4 回に減る。
設計の内容は担当ごとに独立した節として読めるため、まとめても指摘の宛先は曖昧にならない。

設計 Pull Request の本文には課題を自動で閉じる語（`Closes` / `Fixes` / `Resolves`）を書かない。
実装が終わっていない段階でマージするためである。

## 担当どうしの境界

| 担当 | 書き換えてよいパス | 他の担当が触るため書き換えないパス |
| --- | --- | --- |
| A | `.github/workflows/` | `scripts/` / `plugins/` / `README.md` / `AGENTS.md` |
| B | `plugins/ndf/skills/cross-review/`（`state.py` の判定に関わる部分） | `.github/` / `scripts/` / `README.md` / `AGENTS.md` |
| C | `plugins/ndf/skills/cross-review/`（`state.py` の作業ツリーの同期に関わる部分） | `.github/` / `scripts/` / `README.md` / `AGENTS.md` |
| D | `scripts/check-doc-staleness.py` / `scripts/tests/` / `README.md` / `AGENTS.md` / `plugins/ndf/README.md` / `docs/plugin-development-guide.md` | `.github/` / `plugins/ndf/skills/` |

### 担当 B と C は同じファイルを触る

どちらも `plugins/ndf/skills/cross-review/scripts/state.py` を書き換える。触る位置は離れている。

| 担当 | 触る関数 | 現在の行 |
| --- | --- | --- |
| B | `_is_pass` / `_round_passes` / `_guard_previous_round` の再計算部 / `cmd_read_result` / `cmd_judge` | 1005 / 1018 / 1053 / 1326 / 1396 |
| C | `_sync_worktree` / `_create_worktree` / `cmd_start_round` / `_resolve_head_branch`（新設） | 285 / 229 / 1085 / — |

### 終了コードの割り当てを分ける

**両者とも `state.py` の終了コードを 1 つ新設する。** 1〜6 は使用済みであるため、設計の段階で
どちらも 7 を選んでいた。マージの順序に合わせて割り当てを分ける。

| 値 | 意味 | 担当 |
| --- | --- | --- |
| 7 | 結果を残さなかった担当を、同じラウンドで 1 度だけ起動し直す | B（#196） |
| 8 | ラウンド開始時の同期に失敗した | C（#217） |

割り当てを分けないと、後からマージする側が先の値を上書きし、ループの骨組みが 2 つの事象を
区別できなくなる。

**マージの順序は B → C とする。** 担当 C は担当 B のマージ後に `main` を取り込んでから
Pull Request を更新する。同じ関数を触らないため、取り込みで競合が出る可能性は低い。
競合が出た場合は担当 C が解消する。

`SKILL.md` は両者が触り得る。担当 B は「全体フロー」の図と収束の説明を、担当 C は作業ツリーの
同期の説明を書き換える。**節が違うため、同じ行にはならない。**

### 4 担当とも変えないもの

- 配布 Skill の数（`plugins/ndf/manifests/*-skills.txt` の行数と `plugins/ndf/skills/` の実体数）
- `plugins/ndf/.claude-plugin/plugin.json` の版数。版を上げるのはバッチ全体のマージ後で、
  担い手は最後のマージを行った側である（`release`）

生成物の同期（`bash scripts/build-runtime-plugins.sh`）は各担当が自分の Pull Request の中で
実行する。担当 B と C は Skill の中身を変えるため、同期の結果が配布物へ乗る。

## 完了条件（バッチ全体）

- [ ] 設計 Pull Request（担当 B / C / D 分）がマージされている
- [ ] 4 件の実装 Pull Request がいずれもマージされている
- [ ] `bash scripts/validate-runtime-plugins.sh` が終了コード 0 で終わる
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] `claude plugin validate` が通る
- [ ] 4 件それぞれの指示書に書かれた完了条件がすべて満たされている
- [ ] 範囲外と判断した課題が `out-of-scope` で issue になっている
- [ ] 版を上げて配布し（`release`）、振り返りを `docs/development-history/` へ残している

## 共通の進め方

### 作業ツリーを用意する

開発の変更は clone したディレクトリではなく作業ツリーの中で行う。担当ごとに 1 本作る。

```bash
main_dir=/work/ai-plugins
git -C "$main_dir" fetch origin
git -C "$main_dir" worktree add -b "<ブランチ名>" \
  "$main_dir/.worktrees/<ブランチ名>" origin/main
cd "$main_dir/.worktrees/<ブランチ名>"
```

`issues/` と `docs/` は clone したディレクトリで編集してよい。この指示書もそこにある。

### テストを動かす

このリポジトリの Python テストは `uv` の仮想環境で動かす。システムの `python3` には
`pytest` が入っていない。

```bash
uv run --with pytest pytest <テストのパス> -q
```

### Pull Request の宛先

`develop` はまだ無いため、宛先は `main` である（`AGENTS.md` の「Git運用ルール」）。
`--base` は指定しない。

### 範囲外の課題を見つけたとき

`out-of-scope` を使い、**見つけたその場で** issue にする。判断は 3 択（起票する /
範囲内へ入れる / 起票しない）に限り、3 つ目も理由を 1 行残す。

## 参照

- [issues/old/parallel-batch-01/00-overview.md](../old/parallel-batch-01/00-overview.md) — 前のバッチの指示書
- `plugins/ndf/skills/development-workflow/SKILL.md` — 工程の振り分けの基準
