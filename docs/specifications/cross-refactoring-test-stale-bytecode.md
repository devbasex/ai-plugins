# cross-refactoring: 同じ秒の書き換えと古いバイトコード（テストの土台と worktree の書き換えの待ち）

## 概要

**git を使うテストの子の Python はバイトコードを書かない。製品が worktree を書き換えるときは、直前の
書き換えの秒が過ぎてから書き換える。** Python の `.pyc` はソースの更新時刻（秒）と大きさだけで新しさを
照らすため、同じ秒に同じ大きさで書き換えたモジュールを前の内容のまま読む。

この文書は、その揺れの原因・常に成り立つ条件・決定と理由・既知の限界・テスト観点を残す。
`_settle_scope` の検証の順序は
[cross-refactoring-final-fix-and-repo-wide-checks.md](cross-refactoring-final-fix-and-repo-wide-checks.md) が、
直さなかった項目の取り消しと確かめ直しの規則は
[cross-refactoring-failed-item-rules.md](cross-refactoring-failed-item-rules.md) が持つ。
原因の項目の判定と打ち切りの後の取り消しの全体は
[cross-refactoring-culprit-and-stop-revert.md](cross-refactoring-culprit-and-stop-revert.md) にある。

## 用語

「古いバイトコード」「git を使うテストの土台」（コンテキスト `repo-dev-checks`）と、「原因の項目」「原因の
手がかり」「確かめ直し」「直さなかった項目」（コンテキスト `ndf-cross-refactoring`）の定義は用語集
（[docs/glossary.md](../glossary.md)）が正である。

## 背景

`plugins/ndf/skills/cross-refactoring/tests/test_culprit_git.py::test_two_items_breaking_two_tests_are_both_culprits`
（AC4。危険フラグの無い 2 項目 `I-002`・`I-003` がそれぞれ別のテストを落とすと、両方が原因の項目になる）が、
2026-10-04〜06 の 3 日で 7 スプリント・12 回の全体テストと範囲テストで揺れ、そのたびにフレーキーとして除かれた。
m1793 では cross-refactoring の着手前のテストで既存失敗として固定され、その実行の間は最終ゲートの検出から
外れた。

揺れの現れ方は「原因が決まらず取り消しで終わる」である。m1793 の着手前のテストのログは
`AssertionError: assert 'done' == 'fix'` を残した。

### 経路

テストの土台（`crossref_helpers.build_git_flow`）は、項目の書き込みから取り消しまでを 1 秒弱で通す。

1. `I-003` が `src/shared.py` を `X = 1` から `X = 2` へ書く（時刻 T0。どちらも 6 バイト）
2. 検証の全体テストが `tests/test_shared.py` を走らせ、`src/__pycache__/shared.*.pyc` を書く。`.pyc` は
   ソースの更新時刻 `int(T0)` と大きさ 6 を持つ
3. 見分けで `test_total` と `test_shared` が変更起因になる。`test_total` は手がかり `path` で `I-002` に決まる
4. 外す走らせ直し（`culprit._isolate`）が `I-003` を `git revert --no-commit` で外す（時刻 T1）。
   `src/shared.py` は `X = 1`・6 バイトに戻る
5. `int(T1) == int(T0)` なら、走らせ直しの pytest は `.pyc` を新しいとみなして `X = 2` を読み、`test_shared` は
   まだ落ちる
6. `I-002`・`I-001` を外しても通らず、手がかりは `undetermined`、取り消しで `VERIFY=done` に終わる

手元の直列の実行では T0 から T1 までが 0.975〜0.986 秒で、同じ秒に入るのは T0 の端数が 0.02 秒未満の回だけで
ある。`-n 4` の並列では負荷で 1 秒を超え、ほとんど起きない。

### 再現の手順と結果

T0 を秒の頭へ寄せて直列で走らせると再現する。探針はコミットしない。
`plugins/ndf/skills/cross-refactoring/tests/test_zz_repro_1806.py` に置いて走らせ、終わったら消す。

```python
"""#1806 の再現。I-003 の書き込みを秒の頭へ寄せ、取り消しを同じ秒に入れる。"""
import os
import time

import pytest

import test_culprit_git as T
from test_culprit_git import flow  # noqa: F401


def _at_second_start():
    time.sleep(1.0 - (time.time() % 1.0) + 0.001)


@pytest.mark.parametrize("n", range(int(os.environ.get("PROBE_N", "30"))))
def test_aligned(n, flow, cmd_setup, cmd_implement, cmd_converge, capsys):  # noqa: F811
    T._existing(flow, {"tests/test_total.py": T.TEST_TOTAL, "src/shared.py": "X = 1\n", "tests/test_shared.py": T.OTHER_TEST})

    def i003(w):
        _at_second_start()
        T._write(w, "src/shared.py", "X = 2\n")

    T._implement(flow, cmd_setup, cmd_implement, {"I-001": T._flagged("other"), "I-002": T._calc(T.CALC_RAISES), "I-003": i003})
    verify, record = T._verify(flow, cmd_converge, capsys)
    assert verify == "fix"
    assert sorted(record["culprit"]["culprits"]) == ["I-002", "I-003"]
    assert record["culprit"]["basis"] == "isolate"
```

```bash
PROBE_N=30 uv run --frozen --project . --all-extras pytest \
  plugins/ndf/skills/cross-refactoring/tests/test_zz_repro_1806.py -q -p no:randomly
```

| 条件 | コミット | 落ちた回数 / 走らせた回数 |
| --- | --- | --- |
| 直列・秒の頭へ寄せる | 修正前 `658c12b67` | 17 / 30（ほかに 8 回の試行で 6 / 8） |
| `-n 4`・秒の頭へ寄せる | 修正前 `658c12b67` | 0 / 30 |
| 直列・秒の頭へ寄せる・起動口に `-B` | 修正前 + 試しの変更 | 0 / 30 |
| 直列・秒の頭へ寄せる・`build_git_flow` で `PYTHONDONTWRITEBYTECODE=1` | 修正前 + 試しの変更 | 0 / 30 |

落ちた回の出力は `verify=done`・手がかり `undetermined`・原因 `['I-002']` で、`.pyc` の 8〜16 バイト目の
更新時刻は `int(T0)`、大きさは 6 だった。最小の例として、`X = 2` を import した後に `X = 1` を同じ大きさ・同じ
秒で書くと、次の `python -m pytest` は `X == 2` のまま読み、`-B` を付けた起動では通る。

### 製品の側の同じ形

外す走らせ直し（`_isolate`）は、外す（T1）→ 子の pytest が外した後の内容で `.pyc`（`int(T1)`）を書く → 戻す
（`revert --quit` と `_discard_worktree_changes`、T2）を通る。小さなテストファイルの走らせ直しは 0.20〜0.25 秒で、
`int(T2) == int(T1)` は利用者の実行でも普通に起きる。同じ大きさの書き換えなら、利用者の worktree に外した後の
内容の `.pyc` が残り、続く走らせ直し・範囲テスト・全体テスト・最終ゲートが項目の変更を読まずに通りうる。
`__pycache__` は無視されたファイルなので、`reset --hard` と `clean -fd` では消えない。

同じ形（書き換え → 短い走らせ直し → 次の書き換え）は取り消しにもある。`revert_in_order` は項目を `drop` で
取り消して走らせ直し、落ちれば次の項目を `drop` する。`converge._revert_shared`（締め切りの取り消し）も取り消す
たびに範囲テストを走らせ直す。打ち切りの後の取り消しで着手前の木へ戻す `stop_revert._plan_b` は、`revert_in_order` の
最後の走らせ直しの直後に `read-tree -u --reset` で書き換える。

## 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | `build_git_flow` の作業ディレクトリで動く子の Python はバイトコードを書かない（`PYTHONDONTWRITEBYTECODE=1`）。同じ秒・同じ大きさで書き換えたモジュールも新しい内容で読む | テストが落とす |
| I2 | 製品による worktree の書き換え（`culprit._isolate` の戻し・`undo.drop`・`stop_revert._plan_b` の `read-tree`）は、同じプロセスが直前に worktree を書き換え終えた秒より後の秒に入る | テストが落とす |
| I3 | 書き換えの印はオーケストレーターのプロセスの中だけに持つ。`build_git_flow` は印を空にし、前のテストの worktree の印で待たない | テストが落とす |
| I4 | 待つのは、直前の書き換えの秒の内に次の書き換えが来た回だけである。1 回の待ちは 1 秒未満で、走らせ直しの回数は変えない。締め切りを過ぎていても書き換えは省かず待つ | テストが落とす |

## 決定と理由

| # | 決定 | 理由 | 採らなかった案 |
| ---: | --- | --- | --- |
| 1 | テストの側は `PYTHONDONTWRITEBYTECODE=1` を `build_git_flow` に置く。`test_fix_rules_git.py` の `flow` フィクスチャには同じ設定を置かない（土台が持つ） | 揺れの原因は書き込みから取り消しまでを 1 秒弱で通す土台にある。土台に置けば、土台を使うすべての git のテストに効き、ファイルごとに同じ大きさの書き換えを調べずに済む。環境変数にするのは、起動口の pytest だけでなく作業ディレクトリで動く子の Python すべてに効くため | 起動口に `-B` を付ける（効くのは起動口を通る pytest だけ）。`X = 2` を大きさの違う値に変える（このテストの症状だけを消す）。`pytest.mark.flaky` などで再試行する |
| 2 | 原因の項目の判定の結果の決め方（`culprit.determine`・`plugins/ndf/scripts/lib/test_triage.py`）と `converge.py` の検証の順序は変えない | 項目の書き込み（実装担当の CLI）から外すまでに、利用者の実行では範囲テストと全体テストが入り、1 秒を大きく超える。cross-refactoring は言語を問わない製品で、Python のバイトコードだけを扱う分岐を既定に入れない | 走らせ直しの前に `__pycache__` を消すか `PYTHONDONTWRITEBYTECODE` を渡す（Python に限った処理を言語を問わない手順へ入れる）。T0 と T1 の間にも待ちを入れる（利用者の実行では起きず、待ちだけが増える） |
| 3 | 製品の側は、worktree を書き換える前に直前の書き換えの秒が過ぎるまで待つ。待ちは共通の層 `refactor_lib/worktree.py` に置き、`wait_past_rewrite()`（印の秒が終わっていなければ次の秒の頭まで待つ）と `mark_rewritten()`（印を今の時刻にする）の 2 つで表す | `_isolate` の外す → 戻すと、`drop` を通る取り消しは利用者の実行でも同じ秒に入る（背景の「製品の側の同じ形」）。更新時刻（秒）が直前より必ず後になれば、更新時刻と大きさで照らすキャッシュは前の内容から作ったものを古いとみなす。言語を問わない更新時刻の規則なので、決定 2 の理由を保つ | 書き換えた後に変えたファイルの更新時刻を 1 秒進める（未来の時刻を置き、ほかの Tool の新しさの判定を狂わせる）。`_isolate` と `revert_in_order` のそれぞれに待ちを書く（`_revert_shared` などほかの取り消しの経路が漏れる）。git の 1 コマンドごとに待つ（`drop` の中の `reset --hard` と積み直しの間にも待ちが増える） |
| 4 | 待ちを呼ぶ場所は 3 つに限る。`_isolate` は `git revert --no-commit` の後に印を付け、`finally` の戻しの前に待ち、戻した後に印を付ける。`undo.drop` は `_rebuild` の前に待ち、終えた後に印を付ける。`_plan_b` は `read-tree` の前に待ち、コミットを積んだ後（失敗して `reset_hard` した後も）に印を付ける | `revert_in_order`・`_revert_shared`・`_drop_unfixed`・`wholetest` の取り消しと `pending_drop` の再開は `drop` を通るため、呼び出し側を変えずに待つ。`_plan_b` だけが `drop` を通らずに worktree を書き換える | — |
| 5 | 同じ秒に入った回を待たずに作るテストを AC4 の形で 1 つ足す（`test_two_culprits_are_found_when_the_isolation_lands_in_the_second_of_the_write`）。外す `git revert --no-commit` の直後に `src/shared.py` の更新時刻を `I-003` の書き込みの時刻へ揃える | 探針は 30 回で約 80 秒かかり、秒の頭まで待つため全体テストに入れられない。更新時刻を揃えれば毎回同じ秒の回になり、決定 1 を外す変更を毎回落とせる。表明は既存の AC4 と同じにする | 探針の繰り返しを全体テストへ入れる（所要が増え、並列では再現しない）。土台だけを見るテスト（原因の項目の判定との結び付きが読めない） |
| 6 | 既存の AC4 のテストの表明は変えない | 揺れの原因はテストの土台にあり、表明（`verify == "fix"`・原因が `I-002` と `I-003`・手がかりが `isolate`・修正へ回る項目が `I-002` と `I-003`・`I-001` が `verified`）は正しい振る舞いを表す | 表明を緩めて通す |

## 仕様

### 構成要素と責務

| 要素 | 責務 |
| --- | --- |
| `plugins/ndf/skills/cross-refactoring/tests/crossref_helpers.py`（`build_git_flow`） | git を使うテストの作業ディレクトリと起動口（`bin/pytest`）を用意し、`PYTHONDONTWRITEBYTECODE=1` を置き、`refactor_lib.worktree._last_rewrite` を空にする（I1・I3） |
| `refactor_lib/worktree.py`（`_last_rewrite`・`wait_past_rewrite`・`mark_rewritten`） | このプロセスが最後に worktree を書き換え終えた時刻（書き換えの印）を持ち、次の書き換えの前に印の秒の終わりまで待つ。時刻と待ち（`_clock`・`_sleep`）はテストが差し替える（I2・I4） |
| `refactor_lib/culprit.py`（`_isolate`） | 外した後と戻した後に印を付け、戻す前に待つ |
| `refactor_lib/undo.py`（`drop`） | 取り消しの書き換え（`_rebuild`）の前に待ち、終えた後に印を付ける |
| `refactor_lib/stop_revert.py`（`_plan_b`） | 着手前の木へ戻す `read-tree` の前に待ち、終えた後に印を付ける |

### 同じ原因を持つほかのテスト

**`test_culprit_git.py` の中で同じ原因を持つのは AC4 だけである。** 古い `.pyc` を読むには、同じファイルを同じ
大きさで書き換える必要がある。ほかのテストの書き換えは大きさが変わるか、ファイルを足すだけである。

| テスト | 項目が書き換えるもの | 大きさ（前 → 後） |
| --- | --- | --- |
| `test_a_path_in_the_failure_names_the_unflagged_item_and_only_it_goes_to_fix` | `src/calc.py` を `CALC_RAISES` へ・`src/other.py` を足す | 139 → 158 |
| `test_after_the_deadline_only_the_culprit_is_reverted` | 同上 | 139 → 158 |
| `test_without_a_path_the_item_whose_removal_makes_the_test_pass_is_the_culprit` | `src/calc.py` を `CALC_OFF_BY_ONE` へ | 139 → 143 |
| `test_two_items_breaking_two_tests_are_both_culprits` | `src/shared.py` を `X = 2` へ | **6 → 6** |
| `test_the_1634_and_1663_shape_reverts_no_flagged_item` | `src/calc.py` に関数を足す・`src/participants.py` を足す | 139 → 167 |
| `test_without_time_and_without_a_path_every_item_is_reverted_newest_first_until_it_passes` | `src/calc.py` を `CALC_OFF_BY_ONE` へ | 139 → 143 |

`build_git_flow` を使うほかのテストファイル（`test_fix_rules_git.py`・`test_stop_revert_git.py` ほか）にも同じ
大きさの書き換えはありうる。決定 1 で土台に置いたため、ファイルごとには調べない。

## 運用

### 既知の限界

| 項目 | 内容 |
| --- | --- |
| 全体テストの中での頻度 | 12 回の揺れがすべてこの原因かは、失敗の本文が残る m1793 の 1 回でしか確かめていない。ほかの 11 回は `test-run` の記録に本文が無い |
| 利用者の実行で T0 と T1 が同じ秒に入らないこと | 決定 2 の「項目の書き込みから外すまでに 1 秒を超える」は推論で、利用者の実行で測っていない |
| 印の届く範囲 | 印はプロセスの中だけに持つため、別のプロセス（実装担当の CLI・子のテスト）が直前に書き換えた秒は見ない。cross-refactoring の工程の間には CLI の起動とテストが入るため同じ秒に入らないとみなしたが、測っていない |
| 更新時刻の粒度 | 秒より粗い更新時刻のファイルシステム（2 秒単位など）と、内容でなく更新時刻だけで照らす Tool が利用者の worktree で同じ形になるかは確かめていない |

## テスト観点

- 外す走らせ直しが `I-003` の書き込みと同じ秒に入っても、原因が `I-002` と `I-003`・手がかり `isolate`・`VERIFY=fix` になること。`build_git_flow` から `PYTHONDONTWRITEBYTECODE` を外すと `verify=done` で落ちること（`tests/test_culprit_git.py::test_two_culprits_are_found_when_the_isolation_lands_in_the_second_of_the_write`）
- 既存の AC4（`test_two_items_breaking_two_tests_are_both_culprits`）の表明が変わらないこと
- 走らせ直しが直前の書き換えの秒の内に終わった回は、`_isolate` が戻しの前に次の秒の頭まで待ち、秒をまたいだ回は待たないこと（`tests/test_culprit_rerun.py::test_isolation_waits_for_the_next_second_before_restoring_the_item`・`test_isolation_does_not_wait_when_the_rerun_crossed_the_second`）
- `revert_in_order` が次の `drop` の前に次の秒の頭まで待つこと（`tests/test_culprit_rerun.py::test_revert_in_order_waits_for_the_next_second_before_the_next_drop`）
- `revert_in_order` の最後の走らせ直しが直前の書き換えの秒の内に終わった回は、`_plan_b` が `read-tree` の前に次の秒まで待ち、終えた後に印を付けること（`tests/test_stop_revert_git.py::test_plan_b_waits_for_the_next_second_before_restoring_the_planned_tree`）
- いずれの待ちのテストも、書き換えの呼び出しと待ちの順序を表明する。待ちを消すか書き換えの後へ動かすと落ちる
- 同じ秒に寄せた回の再現は、上の探針を修正の前後で走らせて比べる（コミットしない。修正後は 30 回で 0 回落ちる）

## 関連リンク

- [cross-refactoring-culprit-and-stop-revert.md](cross-refactoring-culprit-and-stop-revert.md)（原因の項目と打ち切りの後の取り消し）
- [cross-refactoring-failed-item-rules.md](cross-refactoring-failed-item-rules.md)（失敗した改善項目の扱いの規則）
- [cross-refactoring-final-fix-and-repo-wide-checks.md](cross-refactoring-final-fix-and-repo-wide-checks.md)（`_settle_scope` の順序）
- #1806（揺れ）・#1807（`_settle_scope` の記述を揃えた。#1806 へ取り込んだ）・#1793（直さなかった項目の取り消しと確かめ直し）
