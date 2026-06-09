# PLAN25: cross-review fix結果 `resolved_threads` が int だと merge-fix が TypeError で落ちる問題の堅牢化

- 起票日: 2026-06-09
- 対象 plugin: `ndf` v4.12.1
- 対象 skill: `ndf:cross-review`
- 関連 issue: [#25](https://github.com/devbasex/ai-plugins/issues/25)
- 関連 plan (先行): [issues/PLAN21_cross-review-gemini-stall-and-fix-merge.md](./PLAN21_cross-review-gemini-stall-and-fix-merge.md)（fix 戻り値マージ堅牢化の系譜）
- 報告者: takemi-ohama

## 背景・課題

`/ndf:cross-review` の修正フェーズで、fix サブエージェントが書き出す戻り値ファイル
`$TMP_DIR/fix-pr<PR>-result.json` の `resolved_threads` を **int（件数）** で書くと、
`scripts/state.py` の `cmd_merge_fix` が `len()` を int に適用して `TypeError` でクラッシュし、
cross-review ループが停止する。**頻繁に再発**している。

### 再現エラー

```
File ".../skills/cross-review/scripts/state.py", line 704, in cmd_merge_fix
    "resolved_threads": len(fix.get("resolved_threads", []) or []),
TypeError: object of type 'int' has no len()
```

`fix-pr<PR>-result.json` が `"resolved_threads": 3` のように int のとき発生。

### 根本原因（ドキュメントの自己矛盾）

`resolved_threads` の表現がドキュメント間で食い違っており、fix エージェントが int を書きやすい:

| 箇所 | 表現 | 実態 |
|---|---|---|
| `docs/02-fix-and-rotation.md` 戻り値スキーマ | **list**（`[{"thread_id":...}]`） | コードが期待する形 |
| `docs/01-state-and-review.md` state.json 例 (L55) | `"resolved_threads": 4`（**int**） | state.json 側の保存形（`len()` 後） |
| `docs/01-state-and-review.md` 説明 (L75) | 「resolveReviewThread で resolve した**件数**」 | int を連想させる |

`state.py` は **fix結果=list を受け取り `len()` して state.json には int(件数) を保存**する設計
だが、ドキュメントの「件数」表現により fix エージェントが fix結果側にも int を書いてしまう。

## 改修方針（両側で堅牢化）

### 1. state.py 側を int / list / None いずれも受理できるよう正規化（後方互換）

`cmd_merge_fix`（state.py L704 付近）の `len()` 適用箇所を正規化ヘルパ経由に変更する。

```python
def _count(v):
    """int(件数) でも list でも None でも件数(int)に正規化する。"""
    if isinstance(v, int):
        return v
    if isinstance(v, (list, tuple)):
        return len(v)
    return 0
```

適用先（現状 `len(... or [])` の 3 箇所）:

```python
    "deferred":         _count(fix.get("deferred")),
    "rejected":         _count(fix.get("rejected")),
    "resolved_threads": _count(fix.get("resolved_threads")),
```

さらに、`deferred` を list として走査する箇所:

```python
    for d in (fix.get("deferred") or []):
        st["deferred_nits"].append({**d, "pr": pr, "round": round_no})
```

は `deferred` が int だと `for d in 2` で `TypeError: 'int' object is not iterable` になるため、
`isinstance(..., list)` ガードを追加する:

```python
    _deferred = fix.get("deferred")
    if isinstance(_deferred, list):
        for d in _deferred:
            st["deferred_nits"].append({**d, "pr": pr, "round": round_no})
```

### 2. ドキュメントの一貫性を取り、そもそも int を書きにくくする

- `docs/02-fix-and-rotation.md` の戻り値スキーマで `resolved_threads` / `deferred` / `rejected` が
  **必ず list** であることを明記し、「件数(int)ではない」注意書きを追加。
- `docs/01-state-and-review.md` L75 の「件数」説明を、fix結果側は **list**・state.json 側は
  その **`len()`（int）** である、と区別して記述。

## 変更対象ファイル

| ファイル | 変更内容 |
|---|---|
| `plugins/ndf/skills/cross-review/scripts/state.py` | `_count()` 追加 + `cmd_merge_fix` の 3 件数フィールド正規化 + deferred ループの isinstance ガード |
| `plugins/ndf/skills/cross-review/docs/01-state-and-review.md` | L75 の「件数」説明を list / int(len後) で区別 |
| `plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md` | 戻り値スキーマに list 必須・int 不可の注意書き追加 |

## PR 分割判定: 単一 PR

- 変更は 3 ファイル・低結合・依存タスクなし（差分 ~50 行以内）
- 1 PR で安全に review 可能
- → **release ブランチを作らず**、通常の `/ndf:implementation-plan` + `/ndf:pr` フローで進める

```
base branch: main
branch:      fix/PLAN25-merge-fix-count-normalize
```

## テスト計画

- [ ] `resolved_threads` が int の `fix-pr<PR>-result.json` を用意し、`state.py merge-fix` が
      `TypeError` を出さず int 件数を state.json に保存することを確認
- [ ] `resolved_threads` が list の場合も従来どおり `len()` 件数が保存されることを確認（回帰なし）
- [ ] `deferred` が int の場合に deferred ループでクラッシュしないことを確認
- [ ] `deferred` が list の場合に従来どおり `deferred_nits` へ展開されることを確認
- [ ] `claude plugin validate` が通ること
- [ ] ドキュメント記述（docs/01・docs/02）が list / int(len後) を矛盾なく説明していること

## 期待される効果

- fix エージェントが int / list どちらを書いても cross-review ループが止まらない。
- ドキュメントの一貫性が取れ、そもそも int を書きにくくなる。

## 補足（既存の手動回避策）

発生時は `fix-pr<PR>-result.json` の `resolved_threads` を `["t1","t2","t3"]` のような list に
手で書き換えて `merge-fix` を再実行すると復旧できる。本 PLAN でこの手作業を不要にする。
