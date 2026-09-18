# #621: 本数の測定（`parallel-measure.py`）

## 関連リンク

- 要求: [issue-540-541-621-requirements.md](issue-540-541-621-requirements.md)
- 設計: [issue-540-541-621-design.md](issue-540-541-621-design.md)
- 契約: [issue-540-541-621-design-contracts.md](issue-540-541-621-design-contracts.md)（`parallel-measure.py` の節と `parallel-work.md` の文言）

**設計の決定 1 の 3 本のうちの 1 本目である。** 実行計画（#540）と組（#541）は別の Pull Request が持つ。

## モード

`standard`。新設のスクリプトと文書の変更を含み、受け入れ条件が 7 件ある（設計の決定 1 の表）。

## 目的と非目的

達成したい状態:

- 担当を起動する前に、ホストのメモリと OOM Killer の発生から**起動してよい本数**が出る
- Pull Request の一覧から**並行度と最大同時本数**が出る（実行計画を閉じるときの測定）
- `parallel-work.md` の下限 6 が、CLI の共有と並べて**ホストのメモリ**を理由に持つ

やらないこと:

- 起動の機械的な拒否（決定 8。測るだけで、担当の起動は止めない）
- 下限 4・5 の書き換え、重なりの目安の節、`issue-plan-strategy`、`issues/README.md`（#540 の Pull Request が持つ）
- `milestones.md` の組（#541 の Pull Request が持つ）

## 前提

- 前提 1: `plugins/ndf/scripts/` は配布物へ symlink 1 本で入る（`build-runtime-plugins.sh` の `expected.update({... "scripts": "../scripts"})`）。**新しいファイルを一覧へ足す必要は無い**（設計の U3 はこれで解ける）
- 前提 2: 既存の `test_approval_gates.py` の下限の組は `F10` を `収束レビューが回る範囲` で見ている。新しい下限 6 の文言はこの語を残すため、この Pull Request では同ファイルを触らない
- 前提 3: スクリプトは標準ライブラリだけを使う（契約の文書）。テストは `uv run --with pytest pytest` で走る

## 受け入れ条件

要求の AC30〜AC36・AC40・AC42 を満たす。

- [ ] AC30: 下限 6 の理由に「メモリ」と実測（21GiB・5〜6 本で 2 回・3 本では落ちなかった）がある — `test_parallel_work_bounds.py`
- [ ] AC31: `capacity` が空き・cgroup の残り・スワップの空き・`oom_kill`・本数を出し、終了コード 0 — `test_parallel_measure.py`
- [ ] AC32: `--meminfo` を読めないとき `allowed=` を出さず終了コード 3 — 同上
- [ ] AC33: `allowed = min(上限, running + ⌊(空き − 予備) ÷ 1 本⌋)`。予備・1 本・上限は引数で上書きでき、既定値はスクリプトの定数だけが持つ — 同上 ＋ 文書に数値が無いことを `test_parallel_work_bounds.py` で見る
- [ ] AC34: スワップの空きが閾値を下回ると 1 下がる（`limited_by` に `swap_low`） — 同上
- [ ] AC35: `--oom-baseline` から増えていれば `oom_kill_increased=yes` と `allowed ≤ running − 1`（0 を下回らない） — 同上
- [ ] AC36: コンテナを起動する検査を持つ担当は同時に 1 本、と下限 6 に書かれている — `test_parallel_work_bounds.py`
- [ ] AC40: `concurrency` が並行度・最大同時本数・期間を出す — `test_parallel_measure.py`
- [ ] AC42: `gh` へ渡すのは `pr view ... --json ...` だけ（偽の `gh` が受けた引数を記録して見る） — 同上
- [ ] 退行しないこと: 既存の `test_approval_gates.py`（関門・下限 F5〜F10・振り分け）がそのまま通る。`development-workflow/SKILL.md` の差分が空

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | `plugins/ndf/scripts/parallel-measure.py` に 2 つの副コマンドを置く | 採用 | 設計の構成要素。初期値を持つ唯一の場所を 1 ファイルにする |
| B | `capacity` と `concurrency` を別のスクリプトにする | 不採用 | 測定という 1 つの責務で、実行計画の手順が両方を呼ぶ |
| C | 共通層（`scripts/lib/`）へ置く | 不採用 | 手順が直接呼ぶ入口であり、他のスクリプトから読まれる部品ではない |

## ドメイン用語

| 用語 | 意味 |
| --- | --- |
| 担当（1 本） | 作業ツリー 1 つで 1 本の Pull Request を進める実行主体（G3 の supervisor 1 つ）。その中の worker は数えない |
| 予備 | 空きメモリから引く余裕。進行側の本体と他プロセスの揺れを受ける |
| 並行度 | 対象の Pull Request のうち 2 本以上が同時に開いていた時間の割合 |

## 不変条件

- `capacity` の `allowed` は、`oom_kill_increased=yes` の見直しを除いて 1 を下回らない
- `limited_by` には `memory` と `max` の少なくとも一方が必ず付く
- 2 つの副コマンドはファイルへ書き込まない。`concurrency` は `gh pr view` しか呼ばない

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 公開インタフェース | `parallel-measure.py` が増える | 追加のみ。既存のスクリプトの引数は変えない |
| データスキーマ | なし | — |

## 修正対象

- `plugins/ndf/scripts/parallel-measure.py`（新設）
- `plugins/ndf/scripts/tests/test_parallel_measure.py`（新設）
- `plugins/ndf/skills/development-workflow/references/parallel-work.md`（下限 6 と振り分けの行 6）
- `plugins/ndf/skills/development-workflow/tests/test_parallel_work_bounds.py`（新設）
- `issues/issue-621-parallel-measure-plan.md`（この文書）

## タスク分解

### Task 1: `capacity` が空きメモリから本数を出す

- **対象ファイル:** `parallel-measure.py`・`tests/test_parallel_measure.py`
- **変更内容:** `--meminfo` を読み、`mem_available_mib` / `swap_*` / `running` / `by_memory` / `allowed` / `limited_by` を出す。読めなければ終了コード 3
- **満たす受け入れ条件:** AC31・AC32・AC33 の一部
- **進め方:** 偽の `meminfo` を書いたテスト → 実装

### Task 2: cgroup の残りと `oom_kill` を足す

- **対象ファイル:** 同上
- **変更内容:** `--cgroup-dir` から `memory.max` / `memory.current` / `memory.events` を読み、`cgroup_available_mib` と `oom_kill` を出す。読めない項目は `max` / `unknown` で続ける
- **満たす受け入れ条件:** AC31・AC33（cgroup の残り）
- **進め方:** 3 通り（数値・`max`・不在）の入力のテスト → 実装

### Task 3: スワップと OOM Killer で下げる

- **対象ファイル:** 同上
- **変更内容:** スワップの空きが閾値未満なら 1 減らし、`--oom-baseline` から増えていれば `max(0, min(allowed, running − 1))` を当てる。それ以外は `max(allowed, 1)`
- **満たす受け入れ条件:** AC34・AC35
- **進め方:** 境界（`--running 0` / `1`）を含むテスト → 実装

### Task 4: `concurrency` が並行度を出す

- **対象ファイル:** 同上
- **変更内容:** `--input` の JSON か `gh pr view` から区間を作り、`overlap_minutes` / `concurrency_pct` / `max_open` を出す
- **満たす受け入れ条件:** AC40・AC42
- **進め方:** 3 本の区間（重なり・端が接する・開いたまま）のテスト → 実装。`gh` は `PATH` の先頭に置いた偽物で引数を記録する

### Task 5: `parallel-work.md` の下限 6 と振り分けの行 6

- **対象ファイル:** `parallel-work.md`・`tests/test_parallel_work_bounds.py`
- **変更内容:** 契約の文書の文言へ書き換える。初期値の数値（2048・25）を文書へ書かない
- **満たす受け入れ条件:** AC30・AC36・AC33（1 か所）
- **進め方:** 文言のテスト → 書き換え

## 影響範囲

- `development-workflow` の `parallel-work.md` を読む手順（`issue-plan-strategy`）。下限 6 の読み方が変わるが、参照の形は変わらない
- `test_approval_gates.py` の `F10` は語を残すため通り続ける

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 触る対象が新設のファイルで、既存の構造を巻き込まない | 実装の後の構造改善で足りる |
| 下限の表は #540 の Pull Request が同じ節の行 4・5 を書き換える（重なりの区分は「書き換え」） | 設計の決定 1 が実装の順序を決めている。この Pull Request が先にマージされる |
| テストファイルの基底名が既存と衝突すると収集で落ちる（#748） | `test_parallel_measure.py` / `test_parallel_work_bounds.py` はリポジトリ全体で未使用であることを確かめてから作る |

## 切り戻し手順

追加だけのため、Pull Request の revert で戻る。データの移行を持たない。

## 完了の定義

- [ ] 受け入れ条件をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] `python3 scripts/check-skill-frontmatter.py` / `check-doc-line-limit.py` / `check-markdown-links.py` が通る
- [ ] `bash scripts/build-runtime-plugins.sh` の後に `bash scripts/validate-runtime-plugins.sh` が通る
- [ ] `claude plugin validate .` の終了コードが 0
- [ ] このホストで `capacity` を実行し、`free -h` と `/sys/fs/cgroup/memory.events` の値と一致する
