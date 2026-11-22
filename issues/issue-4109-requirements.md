## 何が起きたか

PR #4107（#4100 の設計）の cross-review のラウンド 1 で、相手役が #4084 の詳細設計 `issues/issue-4084-design-detail.md` を読み、古い修正の結果から行を守る条件が `apply_fix` の上書きにしか掛かっていないと指摘した（r1-4・major・`issues/issue-4084-design-detail.md:51`）。#4084 の設計どおりに実装すると、上書きを拒んだ古い `fixed` の id がラウンドの修正の記録（`fix.fixed`）に残り、次の起動でコミットを送り直したときに、再判定済みの未解決の行が「直した」に戻る。#4100 の範囲外なので起票した。

## 種類

不具合（#4084 の設計が約束する「再判定した行は古い結果で閉じない」（#4084 の決定 5）を、詳細設計が `fix.fixed` の経路で破る）

## 今の姿

cross-review の駆動（`plugins/ndf/src/ndf/domains/crossreview/drive.py`）は、修正担当の結果ファイルを `_fix_record` で読み、`journal.FindingJournal.apply_fix` で指摘の行の終わり方（直した・見送った）を上書きし、`apply_fix` が返した件のうち `fixed` の id をラウンドの修正の記録の `fixed` に保存する。コミットを送れなかったときは `reopen` が `fixed` の行を未解決へ戻し、次の起動の `_resend` が送り直せたら `restore_fixed` で `fixed` の行を直したへ戻す。

- 今のコード（main `ec8340675`・`sprint/p12-lcrfast-r1` `bfc157af6`）の `apply_fix` は、結果の件をすべて上書きするので、`fix.fixed` の id は取り込んだ時点で必ず直したの行である。上書きを拒む経路は無く、この不具合は今のコードには無い
- #4084 の設計（[issues/issue-4084-design.md](https://github.com/devbasex/ai-plugins/blob/main/issues/issue-4084-design.md) の決定 5、[issues/issue-4084-design-detail.md](https://github.com/devbasex/ai-plugins/blob/main/issues/issue-4084-design-detail.md) の `apply_fix` の行）は、#4076 との統合の後、行を新しい MVV の識別子で再判定したラウンド（`rejudged_round`）より前の起動の結果では、行の終わり方を上書きしないと決めた。一方で `apply_fix` が返す件と `_fix_record` が作る `fix.fixed` は今のまま（結果の `fixed` の件をすべて）なので、上書きを拒んだ id も `fix.fixed` に載る

## 事象

#4084 の詳細設計の読み筋（コードはまだ無い）:

1. `apply_fix` が `rejudged_round` を持つ行への古い `fixed` を拒み、行は未解決のまま残る。`kept_handed` は履歴として置かれる
2. `apply_fix` が返す件には拒んだ件も入り、`_fix_record` はその id を最後のラウンドの `fix.fixed` に保存する
3. 次の起動の `_start` で、`_resend` がその記録のコミットを送り直し、`restore_fixed(fix.fixed)` が未解決の行を `fixed` に戻す

同じ `fix.fixed` を読むラウンドの表（`report_decide._round_rows` の「直した指摘」の列）とスレッドの返信の修正のコミット（`threads._fix_commit`）も、拒んだ id を直した指摘として扱う。

## 期待する結果

上書きを拒んだ id は `fix.fixed` に載らず、送り直しの後も行は未解決のまま残る。拒んだ行の `kept_handed` の履歴は残る。

## 再現手順

コードが無いので、#4084 の詳細設計を実装したと仮定した読み筋で再現する（#4084 のテスト設計の「#4076 の旧版の結果を後から取り込む」と同じ前の状態）。

1. 旧版の MVV の識別子のラウンド 1 で、例外が要った行 `r1-1` を修正担当へ渡す。結果（`round: 1`・`r1-1` は `fixed`）が駆動の外に残る
2. MVV の版を上げたラウンド 2 で同じ指摘が出し直され、行が再判定される（`rejudged_round: 2`・未解決）
3. `ndf cross-review merge-fix --file <結果>` でラウンド 1 の結果を取り込む。行は未解決のまま、ラウンド 2 の `fix.fixed` に `r1-1` が載る。コミットは送っていない
4. `ndf cross-review run` を打ち直すと、`_resend` が送り直し、`restore_fixed` が `r1-1` を `fixed` にする

## 環境

- NDF 11.0.16（main `ec8340675`）。前提にする設計は #4084（PR #4094 でマージ済み）と #4045（PR #4080 でマージ済み）、実装の起点は `sprint/p12-lcrfast-r1`
- ランタイム: Claude Code。OS: Linux

## なぜ困るか

#4084 の設計のまま実装すると、新しい MVV で再判定して未解決に戻した指摘が、旧版の修正担当の結果だけで「直した」になり、新版の判断を経ずに収束する（#4084 の決定 5 の「採らなかった案: 古い結果でも行の終わり方を上書きする」が防ぎたかったことが、送り直しの経路で起きる）。直していない指摘が PR の記録とスレッドで直したと案内される。

## 目的

- ラウンドの修正の記録の `fixed` が、その取り込みで実際に直したにした行だけを指す
- 送り直し・ラウンドの表・スレッドの案内が、拒んだ古い結果で行を直したと扱わない

## 対象範囲

含む:
- #4084 の詳細設計の `apply_fix` が返す件と、`_fix_record` が作る `fix.fixed`・`fix.reported` の契約
- #4076 の詳細設計の `by: ndf` の行への結果（`apply_fix` が上書きしない）も同じ契約で `fix.fixed` に載せない（同じ原因・同じ形で、分けると片方だけが残るので範囲に入れた）
- 上の契約に合わせた `issues/issue-4084-design-detail.md` と #4084 のテスト設計の直し（#4045 の実装の PR で行う）

含まない:
- `restore_fixed`・`reopen`・`_resend` の振る舞い（今のまま。読む記録が正しくなれば正しく動く）
- `rejudged_round` で上書きを拒む規則そのもの（#4084 の決定 5 のまま）
- `merge_fix` が最後のラウンドの `fix` を書き換える振る舞い（今のまま）

## 前提

- 前提 1: この直しは #4084 と同じく #4045 の実装の PR に入る。#4084 の実装の PR は単独では無い

## 受け入れ条件

- [ ] 1. 古い `fixed` を上書きせずに拒んだ id が `fix.fixed` に載らず、次の起動の `_resend`・`restore_fixed` がその行を `fixed` に戻さない（再送のテスト）
- [ ] 2. 拒んだ行の `kept_handed` の履歴は残る（テスト）
- [ ] 3. `fix.reported` の件数と `fix.fixed` は、取り込みで行の終わり方を上書きした件だけから作られる。ジャーナルにも足すべき内容にも無い id の結果も入らない（テスト）
- [ ] 4. 拒まずに取り込んだ `fixed` の行は今のとおり `fix.fixed` に載り、送れなかった後の送り直しで直したへ戻る（退行しないことのテスト）
- [ ] 5. #4045 の実装の PR で、`issues/issue-4084-design-detail.md` と #4084 のテスト設計が 1〜4 に合わせて直っている

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（`ndf cross-review` の引数と結果 JSON は変えない。状態ファイルの `fix.fixed`・`fix.reported` の意味を、取り込んだ件だけに絞る） |
| 重さ | #4084 の設計のまま実装すると、MVV の版を上げた後に古い結果を取り込んで送り直した PR で、未解決の指摘が直したとして収束する。今のコードでは起きない |
| データ | スキーマの変更は無い |
| 既存の振る舞い | ジャーナルに無い id の結果が `fix.reported` の件数とラウンドの表の「直した指摘」から外れる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/tests/domains/crossreview -q` |
| 静的解析・型検査 | CI の lint と型検査のジョブ |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | `issues/issue-4084-design-detail.md` は #4084 の実装の PR で直す | 起票時の受け入れ条件 3 | #4084 は #4045 の実装の PR に入るので、受け入れ条件 5 で「#4045 の実装の PR」と読み替えた |
| S2 | `fix.reported` とジャーナルに無い id も同じ契約にそろえる | 設計者（#4109 の設計） | 受け入れ条件 3 |

## リクエスト（原文）

<details>
<summary>起票時の本文</summary>

> ## 何が起きたか
>
> #4084 の詳細設計 `issues/issue-4084-design-detail.md` を、PR #4107（#4100 の設計）の cross-review の相手役がラウンド 1 で読んだ。古い結果から行を守る条件は `apply_fix` の上書きにしか掛かっていない。
>
> ## 種類
>
> 不具合（設計文書が、再判定済みの未解決の行を `fixed` へ戻す実装を導く）
>
> ## 事象（51 行）
>
> 今の `apply_fix` が返す `reported` に古い `fixed` が残ると、`_fix_record` がその ID を最後のラウンドの `fix.fixed` に保存する。次の起動でコミットを再送した `_resend` が `restore_fixed` を通し、再判定済みの未解決の行を `fixed` に戻せる。
>
> ## 期待する結果
>
> 履歴の `kept_handed` は保存しつつ、上書きを拒んだ ID を `fix.fixed` に渡さない契約が詳細設計に書かれている。
>
> ## 受け入れ条件
>
> - [ ] 1. 古い `fixed` を上書きせずに拒んだ ID が `fix.fixed` に載らず、次の起動の `_resend`・`restore_fixed` がその行を `fixed` に戻さない（再送のテスト）
> - [ ] 2. `kept_handed` の履歴は残る（テスト）
> - [ ] 3. `issues/issue-4084-design-detail.md` が 1・2 に合わせて直っている（#4084 の実装の PR で）
>
> ## 由来
>
> PR #4107（#4100 の設計）の cross-review のラウンド 1 の r1-4（major・`issues/issue-4084-design-detail.md:51`）。#4100 の範囲外なので起票した。根拠: Value 13「確かめたことだけが済んだとされる」（MVV バージョン 11）

</details>

（書き手: 起票者（AI。PR #4107 の cross-review から）、日付: 2026-11-22。書き手が利用者でない文は、利用者の指示の原文として扱わない）

