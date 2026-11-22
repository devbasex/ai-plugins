# #4084 の詳細設計: 単一値の criteria を空の並びと読み、例外で実際に渡した回だけを数える

設計文書 [issue-4084-design.md](./issue-4084-design.md) の解き方を、ファイルと関数の粒度へ下ろした文書である。#4045 の詳細設計 [issue-4045-design-detail.md](./issue-4045-design-detail.md) に対する変更として書く。新設する関数が 1 つ（`criteria_decide.kept_needed`）、シグネチャを変える関数が 3 つ（`journal.FindingJournal.apply_fix`・`drive._fix_record`・`drive._take_fix`）あるので別ファイルにした。前提のコードは `sprint/p12-lcrfast-r1`（head `68d887913`）の形で、パスは `plugins/ndf/` からの相対で書く。

## 地図

```text
plugins/ndf/
├── src/ndf/domains/crossreview/criteria_decide.py   criteria_of を配列だけにし、kept_needed を足す
├── src/ndf/domains/crossreview/journal.py           _raised・_raised_again が unkept_severity を置き、apply_fix が sent で kept_handed を置く
├── src/ndf/domains/crossreview/drive.py             _round が sent を書き、_fix・_take_fix・_fix_record・merge_fix が渡す
└── tests/domains/crossreview/
    ├── test_crossreview_criteria.py                 criteria_of・kept_needed
    ├── test_crossreview_journal.py                  unkept_severity・apply_fix の kept_handed
    ├── test_crossreview_run.py                      ラウンド全体（受け入れ条件 3・7）
    └── test_crossreview_store.py                    merge_fix が最後のラウンドの sent を読む
```

依存の向き: `drive` → `journal` → `criteria_decide` の一方向で、今と変わらない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/crossreview/criteria_decide.py` | 変更 | 指摘の基準と重さの純粋な判定 | — | モジュール | `criteria_of`（#4045 の新設の中身を変える）・`kept_needed`（新設） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 指摘のジャーナルの取り込みと修正の結果のマージ | `criteria_decide` | `FindingJournal` | `_raised`・`_raised_again`（#4045 の変更の中身）・`apply_fix`（引数を足す） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | cross-review のラウンドの駆動 | `journal`・`store` | モジュール | `_round`（責務）・`_fix`（呼び方）・`_take_fix`・`_fix_record`（引数を足す）・`merge_fix`（呼び方） |

## 基準の読み方と例外の要否（`criteria_decide.py`）

満たす決定: 決定 1・決定 2・決定 4、#4045 の受け入れ条件 3・7

| 関数 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- |
| `criteria_of` | 変更（#4045 の新設の中身） | 変えない | 配列だけを読む。配列でない値（整数・文字列・`null`・真偽値・浮動小数・オブジェクト）と無い値は空の並び。配列の要素の読み方（真偽値を除く整数と、前後の空白を除いた文字列）は変えない |
| `kept_needed` | 新設 | `kept_needed(row: Mapping[str, Any]) -> bool` | 行の `mvv_pinned` が真なら偽（#4076 の MVV による固定は、例外が無くても妨げの重さにする）。そうでなければ、行が `unkept_severity` を持ち、それが `severity_of` で尺度の値に読め、`blocking` が偽のときだけ真。`unkept_severity` の無い行（例外を当てていない行・この項目を足す前の版で書いた状態ファイルの行）・尺度に無い値・型の違う値は偽 |

- `kept_needed`
  - 入力: ジャーナルの行 1 つ
  - 返り: 真偽値（その行の妨げの重さが基準 2 の例外だけから来ているか）
  - 異常系: 例外を投げない。読めない値はすべて偽
  - 副作用: 無い（純粋な関数）
  - `mvv_pinned` は #4076 の詳細設計の行の項目で、#4045 の実装が先に入る間は行に無い。無い・真でない値は固定が無いとして読む

## ジャーナル（`journal.py`）

満たす決定: 決定 2・決定 3・決定 4、#4045 の受け入れ条件 7

| 関数 | 種類 | シグネチャ | 変える前（#4045 の詳細設計） | 変えた後 |
| --- | --- | --- | --- | --- |
| `_raised` | 変更（#4045 の変更の中身） | 変えない | `kept_applies` が真なら `kept_round` を置く | 同じ条件で `unkept_severity` に `effective_severity(raised, impact)`（基準の並び無し）も置く |
| `FindingJournal._raised_again` | 変更（#4045 の変更の中身） | 変えない | 今回の入力が `kept_round` を持ち行が持たなければ置く | 行が `unkept_severity` を持てば、今回の入力の例外を当てない重さと重い方へ更新する。今回 `kept_round` を初めて置くなら、更新前の行の `severity` と今回の例外を当てない重さの重い方を `unkept_severity` に置く。見送り済みの行でも置き・更新する（数えないのは `kept_handed` の側で決まる） |
| `FindingJournal.apply_fix` | 変更 | `apply_fix(items, handed, round_no, sent: Collection[str] = ())`（今は `apply_fix(items, handed, round_no)`） | 結果の終わり方と理由を行に上書きする | 上書きの前に、行の id が `sent` にあり、`kept_needed(row)` が真で、`kept_handed` が無ければ、`kept_handed` に `{"round": round_no, "outcome": item["outcome"], "reason": 理由}` を置く。足すべき内容（`handed`。結果から `by: rebuild` の新しい行を作る）には置かない。`sent` を省いた呼び手（既定の空）では置かない |

### 異常系の処理

| 関数 | 失敗 | 原因 | 扱い | 利用者に見える出力 |
| --- | --- | --- | --- | --- |
| `kept_needed`・`apply_fix` | `unkept_severity`・`kept_handed` の型が違う | 状態ファイルが壊れている・手で書き換えた | どちらも無い行として読む（`kept_needed` は偽、`kept_handed` は置き直さず数えない） | 無し（#4045 の異常系の表と同じく注意に載せない） |
| `apply_fix` | 結果の id が `sent` に無い | 修正担当が渡していない行（レッドライン・見送り済み・妨げでない行）の結果を返した | 終わり方と理由は今のとおり上書きし、`kept_handed` は置かない | 無し |

### 処理の手順（`apply_fix` の `kept_handed`）

| 行の id が `sent` にある | `kept_needed(row)` | 行が `kept_handed` を持つ | 結果 |
| --- | --- | --- | --- |
| 偽 | — | — | 置かない |
| 真 | 偽 | — | 置かない |
| 真 | 真 | 真 | 置き直さない |
| 真 | 真 | 偽 | 置く |

## 駆動（`drive.py`）

満たす決定: 決定 3

| 関数 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- |
| `_round` | 変更（責務） | 変えない | 修正必須の判定の後、`_save(run)` の前に、`record["sent"]` に `open_`（`pending` が返した行）の `id` の文字列を並びのまま書く。修正担当を起動しない判定（収束・止まり）では書かない |
| `_fix` | 変更（呼び方） | 変えない | `_take_fix(run, result_path, round_no, [str(f["id"]) for f in open_])` を打つ。`_fix_prompt` へ渡す `open_` と同じ並びから作る |
| `_take_fix` | 変更 | `_take_fix(run, path, round_no, sent: Collection[str])`（今は `_take_fix(run, path, round_no)`） | `_fix_record(run.journal, run.ref, path, run.cfg.handed, round_no, sent)` を返す |
| `_fix_record` | 変更 | `_fix_record(journal, ref, path, handed, round_no, sent: Collection[str])`（今は `sent` が無い） | `journal.apply_fix(…, handed, round_no, sent)` を打つ。ほかは変えない |
| `merge_fix` | 変更（呼び方） | 変えない | 最後のラウンドの記録の `sent` が文字列の配列ならそれを、無い・形が違えば空を `_fix_record` へ渡す |

- `sent` を書くのは状態ファイルへ書く前なので、修正担当の起動の後に駆動が落ちても、`merge-fix` は最後のラウンドの `sent` を読める
- 修正担当が結果を書かずに終わったラウンド（`_fix` が None）は `kept_handed` を置かない。次のラウンドの `sent` に同じ行が入れば、そのラウンドの結果で置く

## 単体テストの観点

#4045 の詳細設計の「単体テストの観点」に次の行を足す（`criteria_of` の単一値の行は設計文書の「#4045 の文書のどこを置き換えるか」のとおり置き換える）。

| 観点 | 置き場 |
| --- | --- |
| `kept_needed`: `unkept_severity` が `minor` の行で真、`major` の行・`unkept_severity` の無い行・尺度に無い値・型の違う値で偽、`mvv_pinned` が真の行で `unkept_severity` が `minor` でも偽 | `tests/domains/crossreview/test_crossreview_criteria.py` |
| `apply_fix`: `sent` にある例外が要った行に `kept_handed` を置く。`sent` に無い id（例外を当てた未解決の `major` を含む）・`sent` を省いた呼び出し・既に `kept_handed` を持つ行には置かない。足すべき内容の新しい行には置かない | `tests/domains/crossreview/test_crossreview_journal.py` |
| `_round` が修正担当を起動するラウンドの記録に、指示に載せた行の id と同じ `sent` を書く。修正担当が結果を返さないラウンドでも `sent` は残り、`kept_handed` は置かれない | `tests/domains/crossreview/test_crossreview_run.py` |
| `merge_fix` が最後のラウンドの `sent` を読んで `kept_handed` を置き、`sent` の無いラウンドの記録では置かない | `tests/domains/crossreview/test_crossreview_store.py` |
| #4076 との統合（後に入る PR で足す）: 基準 2 と有効な `Value` を併記した `impact` の無い `major` が渡っても `kept_handed` が無い。基準 2 と有効な `P` を併記した指摘は `sent` に入らず、その id の結果を返されても `kept_handed` が無い | `tests/domains/crossreview/test_crossreview_run.py` |
