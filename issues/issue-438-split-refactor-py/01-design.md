# #438: `refactor.py` を責務ごとに分割する

**振る舞いを変えない構造変更である。** 対象にテストが 458 件あるため `standard` で進める。

## 結論

**責務ごとのモジュールへ分け、`refactor.py` は入口として全部を再エクスポートする。**

```text
scripts/
├── refactor.py          # 入口。argparse と main。全モジュールを再エクスポートする
└── refactor_lib/
    ├── vocabulary.py    # 語彙
    ├── paths.py         # パス解決
    ├── proposals.py     # 提案のマージ
    ├── verify.py        # 適用結果の検証
    ├── review.py        # レビュー判定
    ├── gitfacts.py      # git から事実を取る
    └── commands/        # サブコマンド（12 個）
```

**再エクスポートする理由**: テストは `refactor` フィクスチャ 1 つから全関数を参照している
（`conftest.py` が `importlib` の source loader で `refactor.py` を 1 モジュールとして読む）。
**再エクスポートすれば、テスト 458 件を 1 件も書き換えずに済む。**

## 確かめたこと

**`uv run --script` は同じディレクトリのモジュールを読める。** PEP 723 の自己完結スクリプトが
外部のモジュールを import できるかは自明でないため、実機で確かめた。

```console
$ uv run --script main.py
from _helper
```

`sys.path` へ自身のディレクトリを入れる 1 行が要る。`refactor.py` は共通層
（`plugins/ndf/scripts/lib`）を読むために既に同じことをしている。

## 分割の単位

**責務のコメントをそのまま使う。** 既に区切られており、新しい判断を要しない。

| モジュール | 元の行範囲 | 行数 |
| --- | --- | ---: |
| `vocabulary.py` | 58–214 | 157 |
| `paths.py` | 215–305 | 91 |
| `proposals.py` | 306–452 | 147 |
| `verify.py` | 453–663 | 211 |
| `review.py` | 664–859 | 196 |
| `commands/` | 860–2661 | 1802 |
| `gitfacts.py` | 2664–3610 | 947 |
| `refactor.py`（残り） | 1–57 / 3611–3706 | 約 150 |

**サブコマンドは 12 個ある。** 1802 行を 1 ファイルへ移しても分割の目的を果たさないため、
**工程の段階でまとめる**。

| ファイル | 持つサブコマンド |
| --- | --- |
| `commands/setup.py` | `init` / `start-round` |
| `commands/apply.py` | `merge-proposals` / `merge-apply` |
| `commands/review.py` | `review-targets` / `judge-review` / `should-abandon` / `abandon-items` / `merge-fix` |
| `commands/report.py` | `advance` / `status` / `report` |

## 決定の記録

### 1. `refactor.py` を入口として残す

**結論**: 呼び出し側（`SKILL.md` の実行のコマンド列 / `launch-cli.sh` /
`prepare-worktrees.sh`）が指すパスを変えない。

**理由**: **呼び出し側を変えると、この変更が振る舞いを変えたことになる。** 分割は内部の
構造の変更であり、外から見た入口は同じでなければならない。

### 2. 全部を再エクスポートする

**結論**: `refactor.py` が各モジュールの公開名をすべて自分の名前空間へ取り込む。

**理由**: **テストを 1 件も書き換えないため。** テストは `refactor.<関数>` で参照しており、
再エクスポートすればその形が保たれる。**テストが変わったら、それは振る舞いを変えている。**

**採らなかった案**: テストの側を各モジュールへ向ける（458 件の書き換えが要る。受け入れ条件と
衝突する）。

### 3. 置き場所は `scripts/refactor_lib/`

**結論**: `scripts/` の直下へ並べず、`refactor_lib/` の下へ置く。

**理由**: `scripts/` には `launch-cli.sh` と `prepare-worktrees.sh` が並んでおり、**実行する
ものと読み込まれるものが混ざる**。共通層（`plugins/ndf/scripts/lib/`）と同じ考え方で分ける。

**採らなかった案**: `scripts/lib/`（プラグイン直下の共通層と紛らわしい）。

### 4. 共通層へは移さない

**結論**: `cross-review` と共有するものがあっても、この変更では移さない。

**理由**: **共有の判断は別の変更である。** 分割と移設を混ぜると、どちらが原因で壊れたかが
分からなくなる。移す候補が見つかったら `out-of-scope` で起票する。

## テスト設計

| 受け入れ条件 | 何で確かめるか |
| --- | --- |
| 1 ファイルあたりの行数が減る | 分割後の行数 |
| **テスト 458 件が変更なしで通る** | `git diff` にテストのファイルが 1 件も出ないこと |
| 呼び出し側が変わらない | `SKILL.md` / `launch-cli.sh` / `prepare-worktrees.sh` の差分が無いこと |
| 分割後の説明がある | 各モジュールの docstring |
| 検査 9 本が 0 | 実行 |

**`uv run --script` で動くことを実機で確かめる。** テストは `importlib` で読むため、
スクリプトとしての起動は別に確かめる必要がある。

## 実装で分かったこと

### 1. 責務のコメントの並びは、依存の向きを持っていなかった

**コメントのとおりに分けると循環が 1 つできる。** `gitfacts`（元 2664–3610）の
`_write_plan_file` と `_sync_generated` が、改修計画の本文を組み立てる
`format_plan` / `normalize_plan_file`（元 2494–2583）を呼ぶ。この 2 つは
`commands/report.py`（`advance` / `status` / `report`）へ入る範囲にあり、
`commands/*` は `gitfacts` を読む。**`gitfacts` → `commands/report` →
`gitfacts` になる。**

**改修計画の 5 つの関数を `plan.py` へ分けた。** 元の位置は `cmd_status` と
`cmd_report` の間だが、**この 5 つはどちらのサブコマンドからも呼ばれていない**
（呼ぶのは `gitfacts` の 2 つと `cmd_init` である）。並びの都合でそこにあった
だけで、`report` の一部ではない。分けた結果、依存は一方向になった。

| 分けたもの | 元の行 | 呼び出し元 |
| --- | --- | ---: |
| `default_plan_file` / `normalize_plan_file` / `format_plan` / `_plan_round_section` / `_plan_item_section` | 2494–2583 | `gitfacts` / `commands/setup` |

### 2. 実測した依存

**import から数えた。** モジュールをまたぐ参照はすべて `from` の形で書いており、
モジュール越しの属性参照（`gitfacts._git_out(...)` のような形）と関数内の相対
import は 1 件も無い（`paths` の `import tempfile` だけが関数内で、これは標準
ライブラリの読み込みである）。数字は受け取っている名前の数を表す。

| 読む側 ＼ 読まれる側 | `__init__` | `vocabulary` | `paths` | `plan` | `gitfacts` | `proposals` | `verify` | `review` | `commands/report` | `commands/apply` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `vocabulary` | — | — | — | — | — | — | — | — | — | — |
| `paths` | 1 | — | — | — | — | — | — | — | — | — |
| `plan` | 1 | 2 | — | — | — | — | — | — | — | — |
| `gitfacts` | 2 | 4 | 2 | 2 | — | — | — | — | — | — |
| `proposals` | 1 | 4 | — | — | 1 | — | — | — | — | — |
| `verify` | — | 6 | — | — | 1 | — | — | — | — | — |
| `review` | 1 | — | — | — | — | — | — | — | — | — |
| `commands/report` | 1 | 1 | 1 | — | — | 1 | — | — | — | — |
| `commands/setup` | 3 | 3 | 6 | 2 | 1 | — | — | — | 1 | — |
| `commands/apply` | 2 | 1 | 3 | — | 15 | 1 | 1 | — | — | — |
| `commands/review` | 3 | 2 | 3 | — | 16 | — | 2 | 4 | — | 2 |

**循環は無い。** 表は下三角に収まっており、上の行から下の行への向きしか無い。
機械でも確かめた（有向グラフの深さ優先探索で閉路 0 件）。

### 3. 分割後の行数

| ファイル | 行数 |
| --- | ---: |
| `refactor.py`（入口） | 206 |
| `refactor_lib/__init__.py` | 32 |
| `refactor_lib/vocabulary.py` | 164 |
| `refactor_lib/paths.py` | 104 |
| `refactor_lib/plan.py` | 102 |
| `refactor_lib/proposals.py` | 161 |
| `refactor_lib/verify.py` | 227 |
| `refactor_lib/review.py` | 205 |
| `refactor_lib/gitfacts.py` | 971 |
| `refactor_lib/commands/__init__.py` | 5 |
| `refactor_lib/commands/setup.py` | 444 |
| `refactor_lib/commands/apply.py` | 657 |
| `refactor_lib/commands/review.py` | 592 |
| `refactor_lib/commands/report.py` | 152 |

**`gitfacts.py` は 971 行で、分割前の 26% を 1 ファイルが持つ。** 元の責務の
コメントが 1 つだったため、この段階では分けていない。

### 4. 差し替えは定義元へ伝える

**再エクスポートは値の写しである。** 入口の `refactor._sh` を差し替えても、
`from .paths import _sh` で受け取った側は元の値を見続ける。入口のモジュールへ
`__setattr__` を持たせ、同じ名前を持つモジュールすべてへ書き戻す。テストが
差し替える名前は 9 つある（`_sh` / `_git_out` / `_posted_review_state` /
`_push_head` / `_run_with_timeout` / `commits_in_range` / `collect_commit_facts` /
`resolved_threads_on_github` / `cmd_init`）。

### 5. この課題は段階 1 に限る

テストをモジュールの境界へ寄せること（段階 2）と、再エクスポートを外して公開
API を確定すること（段階 3）は行っていない。**段階 3 で問題になるのは、
モジュールをまたいで渡している非公開の名前（`_` で始まる名前）が 27 個ある
ことである。** 内訳は `gitfacts` が 15、`paths` が 7、`review` が 2、
`commands/apply` が 2、`commands/report` が 1。
