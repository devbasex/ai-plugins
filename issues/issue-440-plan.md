# #440: refactor_lib のテストをモジュール境界へ寄せる（段階 2）

## 関連リンク

| 種類 | 場所 |
| --- | --- |
| 課題 | #440 |
| 要求と受け入れ条件 | [issue-440-441-refactor-lib-stages.md](issue-440-441-refactor-lib-stages.md) |
| 設計 | [design-refactor-lib-structure.md](design-refactor-lib-structure.md)（PR #460 でマージ済み） |

## モード

`standard`。テストの書き換えで、何を担保しなくなるかの判断が要る。

## 目的と非目的

達成したい状態:

- **テストがモジュール境界を通り、どのモジュールが何を公開しているかがテストから読める**

やらないこと:

- 実装（`refactor_lib/` の中身）の変更
- **`assert` が確かめる期待値の変更**
- 段階 3（#441）の再エクスポートの除去

## 実測（2026-09-07）

| 対象 | 実測 |
| --- | --- |
| `tests/` の収集 | 576 件 / 20 ファイル |
| 入口経由で差し替えている名前 | 11 個（`cmd_init` / `collect_commit_facts` / `commits_in_range` / `_discard_impl_leftovers` / `_drop_items` / `_git_out` / `_push_head` / `resolved_threads_on_github` / `_run_with_timeout` / `_sh` / `_unassigned_fix_commits`） |

**テストファイルの多くは 1〜2 のモジュールに集中している。**

| 主に触るモジュール | テストファイル |
| --- | --- |
| `gitfacts` | `test_git_facts.py` / `test_merge_apply.py` / `test_drop_items_git.py` / `test_sync_generated.py` |
| `plan` | `test_plan_comment.py` / `test_plan_file.py` |
| `commands.converge` | `test_verify_round.py` / `test_abandon_items.py` / `test_commit_granularity.py` |
| `commands.apply` | `test_apply_rounds.py` / `test_merge_proposals.py` |
| `commands.gate` | `test_final_fix.py` / `test_final_gate.py` |
| `commands.setup` | `test_init.py` / `test_models_and_metrics.py` |
| `commands.report` | `test_rounds.py` |
| `scope` | `test_scope_gate.py` |
| `outbound` | `test_outbound_text.py` |
| `vocabulary` | `test_propose_prompt.py` / `test_test_round_prompt.py` / `test_skill_terms.py` |
| `rounds` / `proposals` | `test_test_rounds.py` |

## 受け入れ条件

- [ ] 1. `conftest.py` がモジュールごとのフィクスチャを持つ
- [ ] 2. 各テストファイルが、主に触るモジュールのフィクスチャを通して呼ぶ
- [ ] 3. **`assert` が確かめる期待値が変わっていない**（下の「差分の見方」で確かめる）
- [ ] 4. テストの件数が減っていない（576 件）
- ~~5. 入口経由の差し替え 11 個が、定義元のモジュールへの差し替えになっている~~
      → **寄せられるものを寄せ、寄せられないものは理由を残す**へ変更（2026-09-07、実測による）
- [ ] 6. 実装（`refactor_lib/` と `refactor.py`）が変わっていない（差分 0 行）
- [ ] 7. 検査 8 本が終了コード 0
- [ ] 8. `uv run --with pytest pytest scripts/tests plugins/ndf -q` が終了コード 0

## 差分の見方（条件 3）

**参照の接頭辞だけが変わることを、機械で確かめる。**

**前後の `assert` 行を集合として突き合わせる。**

```bash
norm() {
  grep -hE '^\s*assert |^\s+assert ' "$@" \
    | sed -E 's/^[[:space:]]+//; s/\b(refactor|gitfacts|plan|scope|outbound|vocabulary|rounds|proposals|verify|paths|cmd_[a-z_]+)\./MOD./g' \
    | sort
}
git archive origin/<起点> <テストのパス> | tar -x -C <退避先>
diff <(norm <退避先>/<テストのパス>/*.py) <(norm <テストのパス>/*.py)
```

**接頭辞を伏せた `assert` 行の集合が一致すること。** 差が出た行は期待値が変わった行である。

**差分から拾う形は採らない。** 同じ内容の行が複数の箇所にあると、追加と削除の対を数える
形では対応が取れず、変わっていない行が残って見える（実際に 2 行が残った）。

## 条件 5 を書き換えた理由

**実装が非公開名を自分の名前空間へ取り込んでいるため、定義元を差し替えても届かない。**

```python
# scripts/refactor_lib/commands/apply.py
from ..gitfacts import (
    _git_out,          # ← 取り込んだ時点で値の写しになる
)
```

`gitfacts._git_out` を差し替えても、`commands/apply.py` は取り込んだ元の値を呼び続ける。
**入口経由の差し替えが届くのは、`_Entry.__setattr__` が同じ名前を持つモジュールすべてへ
書き戻しているためである。**

| 実測（2026-09-07） | 件数 |
| --- | ---: |
| 差し替えの総数 | 109 |
| 定義元（`gitfacts`）へ寄せたもの | 27 |
| 入口経由のまま残したもの | 82 |

**残りは段階 3（#441）で寄せる。** あちらの受け入れ条件に「モジュールをまたいで渡している
非公開名が 0 個」があり、それを満たした時点で定義元への差し替えが届くようになる。

**順序は変えない。** 段階 2 でテストがモジュールを読む形にしておかないと、段階 3 で
再エクスポートを外したときにテストが一斉に落ち、原因を分けられない。

## タスク分解

**1 モジュール = 1 タスクにする。** タスクごとにテストが通る状態を保つ。

### Task 1: `conftest.py` にモジュールごとのフィクスチャを足す

- **対象ファイル:** `tests/conftest.py`
- **変更内容:** `refactor.py` を読んだ後、`sys.modules` からモジュールを引くフィクスチャを
  13 個足す。`refactor` フィクスチャは残す（入口そのもののテストが使う）
- **満たす受け入れ条件:** 1
- **進め方:** フィクスチャを足す → 既存のテストが通ることを確かめる（この時点では
  どのテストもまだ使わない）

### Task 2〜N: モジュールごとにテストを寄せる

**モジュールの単位で 1 タスクとする。** 上の表の並びで進める。

- **対象ファイル:** そのモジュールを主に触るテストファイル
- **変更内容:** `refactor.<名前>` を `<モジュール>.<名前>` へ替える。差し替え
  （`monkeypatch.setattr`）も定義元のモジュールへ向ける
- **満たす受け入れ条件:** 2 / 3 / 5
- **進め方:** 1 ファイルずつ書き換え → そのファイルのテストを実行 → 全体を実行

### Task 最終: 差分と件数を確かめる

- **満たす受け入れ条件:** 3 / 4 / 6 / 7 / 8

## 影響範囲

| 対象 | 影響 |
| --- | --- |
| 実装 | 変わらない |
| 配布物 | `cross-refactoring` の `tests/` と `conftest.py` |
| 段階 3（#441） | **この段階が終わるまで着手できない**（差し替えが入口経由のままでは再エクスポートを外せない） |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 差し替えを寄せると、入口を見ている別の経路が古い値を見る | 段階 3 まで `__setattr__` の伝播が残るため、両方に効く。タスクごとに全体テストを実行して確かめる |
| 期待値をうっかり変える | 「差分の見方」を Task 最終で実行し、対にならない行が 0 であることを示す |
| 1 ファイルが複数モジュールにまたがる | 主に触るモジュールへ寄せ、残りは元の `refactor` 経由のままにする。**全部を寄せ切ることは条件にしない** |

## 切り戻し手順

コミットを戻すだけで元へ戻る。実装は変えない。

## 完了の定義

- [ ] 受け入れ条件 8 件をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `standard` の検証の段階（限定的な検証 → 全体テスト → 静的解析）を通している
