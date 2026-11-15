# 記録の読み方と閾値の見直し（`pace`）

[pace.md](pace.md) の続きである。自動で通した承認ゲートの記録と、検査の記録（`check-trigger.py record` / `stats`）の読み方を持つ。

**自動で通した承認ゲートは 3 か所に残る。** スプリント状態ファイルの `gates[]` に `by: mvv`（`verdict`・`reasons`・`log`）の行、
`mvv-gate.jsonl` に同じ `gate` の判定の 1 行（`at` で状態の行と対応する）、Pull Request に判定のコメント（設計承認ゲートは設計 PR、
リリース承認ゲートは出す版の PR。`auto` では検査のスプリントの PR）。`handoff` で自動の通過を取り消すと、`gates[]` の行が消えて
`withdrawals[]` に承認ゲートの名前と時刻が残る。どの進め方で通ったかは、状態とマニフェストの `進め方` と、課題の本文の
見出し行の `進め方: <値>`（`normal` 以外で書く）で読む。

以下は検査の記録である。**書き手は `check-trigger.py record` の 1 つで、前回の検査からの差分の検査
（`new check --since-last`。`scope: since`）も、PR を指す検査（`new check --pr N`。`record --target-pr N`・`scope: pr`）も、
マージ・変更なし・落ちたのどの終わり方でも 1 行を書く。** PR を指す検査の行は `pr` と `to` を持たないため、トリガーの範囲の
起点にも、範囲から外す PR にもならない。承認ゲートで止まった間と、計画の外から止められた計画は書かない。
検査の記録は通過記録と同じ置き場（`${CLAUDE_PLUGIN_DATA}` → `~/.claude/ndf/state` →
`${TMPDIR:-/tmp}/ndf-checks`）の `checks/<所有者>__<リポジトリ>.jsonl` に、事象を追記するだけで持つ。

| `kind` | いつ足すか | 主な列 |
| --- | --- | --- |
| `eval` | 評価のたび（立たなかった回も） | `id`・`from`・`to`・`fired`・`metrics`（`prs`・`score`・`lines`・`hours`・`escapes`） |
| `check` | 検査の終わり | `result`（`merged` / `no_change` / `failed`）・`failed_at`・`scope`（`since` / `pr`。無い行は `since`）・`pr`（`since` の検査の PR）・`target_pr`（`pr` の検査した PR）・`prs`（範囲の PR）・`findings` |
| `escape` | 流出不具合を直したマージの後 | `pr`・`of`（0 は不明）・`areas` |

`findings` の件数は計画の `state.json` の `counts`（フェーズレポートと同じ出所）からコピーする。

| キー | 単位 |
| --- | --- |
| `applied`・`reverted` | 構造改善の改善項目 |
| `findings`・`fixed`・`deferred`・`rejected` | 修正担当が扱った指摘（`findings` = 直した + 見送った + 却下した。`findings` ≥ `fixed`） |
| `comments` | レビュー担当が出したコメント（1 つに複数の指摘が入り、2 者が同じ所を指せば 2 件。指摘の数と比べない） |
| `unresolved` | 解決されていないスレッド |
| `rounds` | 実装レビューのラウンド |

`comments` を持たない古い行の `findings` はコメントの数である。`stats` はそれを `comments` として出し、`findings` と
`fixed` を空にする（行は書き換えない）。

**`check-trigger.py stats` が集計する**（評価の回数と立った回数・トリガーごとの回数・検査ごとの件数・検査の後に
流出した不具合の件数・流出不具合と検査の結び付き）。流出不具合は、持ち込んだ PR（`of`）を範囲（`prs`）に含めた最新の
検査へ結び付く（`items[].escapes_linked`）。結び付かないものは `metrics.unlinked` に理由と並ぶ: `of_unknown`（`of: 0`）/
`no_check`（`of` を範囲に含めた検査が無い）/ `no_range`（それより前に範囲を持たない古い行しか無い）。時刻の窓で数える
`escapes_after` は `scope: since` の検査だけが持つ。スプリントの終わりの振り返りはこの出力を材料にし、検査を 5 回回した後に閾値を見直す。
別の端末では記録が無く、最新の正式版のタグから数え直す（範囲が広がる側に倒れ、取りこぼしは起きない）。
