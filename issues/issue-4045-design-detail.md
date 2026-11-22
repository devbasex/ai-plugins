# #4045 の詳細設計: 1 文の欠けた基準 2 の重い指摘を下げずに渡す

設計文書 [issue-4045-design.md](./issue-4045-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。前提はスプリントブランチ `sprint/p12-lcrfast-r1`（head `68d887913`）が main へ入った後の形で、#4014 の `impact`・`effective_severity`・`fixer_impact` を持つ。

## 地図

パッケージ・モジュール構成（触るファイルだけ）:

```text
plugins/ndf/
├── contracts/data/review-criteria.json            … criteria_field・impact.kept_criteria・impact.fixer_missing・summary
├── src/ndf/domains/crossreview/
│   ├── criteria_decide.py                         … 基準の並びを読み、重要度と添え書きを決める
│   ├── journal.py                                 … 行に criteria を残し、出し直しで合わせる
│   └── drive.py                                   … 出力の形に criteria を足す
├── skills/cross-review/docs/
│   ├── 01-state-and-review.md                     … 出力の形・指摘の基準・ジャーナルの行
│   └── 02-fix-and-rotation.md                     … 修正担当へ渡す添え書き
└── tests/domains/crossreview/
    ├── test_crossreview_criteria.py
    ├── test_crossreview_journal.py
    └── test_crossreview_run.py
```

依存の向き: `drive` → `journal` → `criteria_decide` → `contracts`（`review-criteria.json`・`severities.json`）の一方向で、今と変わらない。`criteria_decide` は純粋な関数だけを持ち、ファイルとネットワークに触れない（契約の JSON の読み込みは今の `_table` のキャッシュを使う）。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/data/review-criteria.json` | 変更 | 指摘の基準と指示の文 | — | — | キー `criteria_field`・`impact.kept_criteria`・`impact.fixer_missing`（新設）・`summary`（変更） |
| `src/ndf/domains/crossreview/criteria_decide.py` | 変更 | 基準と重要度の純粋な関数 | `review-criteria.json`・`severities.json` | モジュール | `criteria_of`・`kept_without_impact`（新設）・`effective_severity`・`reviewer_block`・`fixer_impact`（変更） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 指摘のジャーナル | `criteria_decide` | `FindingJournal` | `_raised`・`FindingJournal._raised_again`（変更） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `criteria_decide`・`journal` | モジュール | `_review_prompt`（変更） |
| `skills/cross-review/docs/01-state-and-review.md` | 変更 | 手順書 | — | — | 「状態とレビュー」の手順 2・「指摘の基準」・状態ファイルの `findings[]` の行 |
| `skills/cross-review/docs/02-fix-and-rotation.md` | 変更 | 手順書 | — | — | 「修正」の渡す指示 |

## 基準の並びと重要度（criteria_decide）

満たす決定: 決定 1・2・3・5、受け入れ条件 1〜5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `criteria_of` | `criteria_decide.py` | 新設 | `criteria_of(value: object) -> tuple[str, ...]` | 相手役の `criteria` の値を、表にある基準の番号の重ねない並び（番号の順）にする |
| `effective_severity` | `criteria_decide.py` | 変更 | `effective_severity(severity: Optional[str], impact: object, criteria: Sequence[str] = ()) -> str` | ジャーナルの行に書く重要度。1 文が無くても、基準の並びが `impact.kept_criteria` と重なれば下げない |
| `kept_without_impact` | `criteria_decide.py` | 新設 | `kept_without_impact(row: Mapping[str, Any]) -> bool` | 基準 2 で下げずに渡した行か |
| `reviewer_block` | `criteria_decide.py` | 変更 | 変えない（`reviewer_block(focus: Sequence[str]) -> list[str]`） | 重要度の行の後に `criteria_field` の文を 1 行足す |
| `fixer_impact` | `criteria_decide.py` | 変更 | 変えない（`fixer_impact(row: Mapping[str, Any]) -> str`） | 1 文があれば今の文、無くて `kept_without_impact` が真なら `impact.fixer_missing` の文を「— 起きること: 」の後に返す |

- `criteria_of`
  - 事前条件: 無い（どの値でもよい）
  - 事後条件: 返す並びの要素は、`review-criteria.json` の `criteria` のキーと `focus_criterion` の値のどれか。重ならず、整数として小さい順
  - 副作用: 無い
- `effective_severity`
  - 事前条件: `severity` は `severity_of` を通した値。`criteria` は `criteria_of` の返り
  - 事後条件: 返りは必ず尺度の値。`criteria` を渡さない呼び出しは今と同じ値を返す
  - 副作用: 無い
- `kept_without_impact`
  - 事後条件: 行が `raised_severity` のキーを持ち、`severity` が収束の妨げになる重さで、`impact` を持たず、`criteria_of(row.get("criteria"))` が `impact.kept_criteria` と重なるときだけ真。引き継いだ行・前の版の行（`raised_severity` が無い）は偽

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `criteria_of` | 値が配列 | 要素ごとに、真偽値を除く整数は 10 進の文字列へ、文字列は前後の空白を除き、表の番号にあるものだけを採る。重点（基準 3）の番号は、重点の無い実行でも採る（基準 2 の規則に関わらず、実行の重点の有無を `journal` が持たないため） |
| `criteria_of` | 値が真偽値でない整数か文字列の 1 つ | 要素 1 つの配列と同じ |
| `criteria_of` | 値が無い・`null`・真偽値・浮動小数・オブジェクト | 空の並び |
| `effective_severity` | `severity` が妨げにならない重さ（`minor`・`nit`） | `severity` |
| `effective_severity` | 1 文がある | `severity`。None なら妨げになる重さのうち最も軽いもの（`major`） |
| `effective_severity` | 1 文が無く、`criteria` が `kept_criteria` と重なる | 同上（`severity`、None なら `major`） |
| `effective_severity` | 1 文が無く、重ならない | 妨げにならない重さのうち最も重いもの（`minor`） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `impact.kept_criteria` | 設定値 | `review-criteria.json` | `["2"]` |
| `impact.fixer_missing` | 設定値（文） | `review-criteria.json` | 「書かれていない（基準 {criteria} の指摘として下げずに渡した。当たるかを確かめ直し、当たらなければ理由を添えて見送る）」の意味の文。`{criteria}` は `kept_criteria` を `・` でつないで埋める |
| `criteria_field` | 設定値（文） | `review-criteria.json` | 「criteria には、指摘が当たる基準の番号（上の番号）をすべて書く。」の意味の文 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `criteria_of` | `severity_of`（尺度の値だけを通す）・`journal._text`（空白を除いた文字列） | `sprint/p12-lcrfast-r1` の `criteria_decide.py`・`journal.py` を読んだ | 使えない。どちらも 1 つの値を読み、配列と番号の表を扱わない。番号の表は `criteria_lines` と同じ `_table()` から作る |
| `kept_without_impact` | `downgraded`（下げた行か）・`_has_impact` | 同上 | `_has_impact` と `blocking`・`severity_of` を使う。`downgraded` は逆の条件で、そのまま使えない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `criteria_of` | `["2", "1"]` → `("1", "2")`、`[2]` → `("2",)`、`"2"` → `("2",)` | 重点の番号 `"3"`、重なり `["2", 2, " 2 "]` → `("2",)`、空の配列 → 空 | 表に無い番号 `["9"]`・真偽値 `True`・`[True]`・浮動小数・オブジェクト・`None` → 空 |
| `effective_severity` | 基準 2 を含む 1 文の無い `major`・`critical` → そのまま、重要度の無い → `major` | 2 つの引数の呼び出しが今の表のまま、空白だけの 1 文は無い扱い | 基準 2 を含む `minor`・`nit` → そのまま、基準 1 だけの 1 文の無い `major` → `minor` |
| `kept_without_impact` | 基準 2 を含み 1 文の無い `major` の取り込みの行 → 真 | 1 文のある行・`minor` の行 → 偽 | `raised_severity` の無い行（引き継ぎ・前の版）→ 偽 |
| `reviewer_block` | 出力に `criteria_field` の文がある | 重点の有無で同じ文がある | — |
| `fixer_impact` | 1 文のある行 → 今の文、`kept_without_impact` の行 → `fixer_missing` の文 | 1 文があり基準 2 も含む行 → 今の文だけ | どちらでもない行 → 空 |

## ジャーナルの行の基準の並び（journal）

満たす決定: 決定 3・4、受け入れ条件 1〜3・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_raised` | `journal.py` | 変更 | 変えない（`_raised(item: dict[str, Any]) -> dict[str, Any]`） | `criteria_of(item.get("criteria"))` を先に読み、`effective_severity` の 3 つ目の引数に渡す。並びが空でなければ行に `criteria`（配列）を足す |
| `FindingJournal._raised_again` | `journal.py` | 変更 | 変えない | 今の重ね方（重い方の重要度・1 文・元の重要度）に加え、既にある `criteria` と今回の並びの和集合を番号の順で行に置き直す。どちらも空なら置かない |

- `_raised_again`: 直した指摘（`fixed`）の再発で位置とセクションを置き換えるときも、`criteria` は合わせる（捨てない。#4014 の決定 4 と同じ）。引き継いだ行（`raised_severity` が無い）への出し直しでも和集合を置く。重要度は今と同じく `heavier(既にある値, 今回の effective_severity)` で、今回の値は今回の並びだけで決まる

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_raised` | `criteria` の形が違う | 相手役の書き損じ・古い出力 | `criteria_of` | 既定値で続ける（空の並び = 基準 2 に当たらない） | 無し（注意にも載せない。#4014 の `impact` の欠けと同じ扱い） | 要らない。打ち直しても同じ結果 |
| 状態ファイルの読み込み | 前の版の行に `criteria` が無い | 版を上げる前に始めた実行 | 行を読む関数 | 既定値で続ける（空の並び） | 無し | 要らない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_raised` | 基準 2 を含む 1 文の無い `major` → 行の `severity` が `major`・`criteria` が `["2"]`・`raised_severity` が `major` | 並びが空 → 行に `criteria` が無く、今の行と同じ | 形の違う `criteria` → 空と同じ |
| `_raised_again` | `["1"]` の後に `["2"]` → `["1", "2"]`、重要度は `minor` から `major` へ | 同じ並びの出し直し → 変わらない。直した指摘の再発でも合わさる | 引き継いだ行への出し直し → `criteria` が足され、`raised_severity` は足されない |

## 相手役への出力の形（drive）

満たす決定: 決定 2、受け入れ条件 5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_review_prompt` | `drive.py` | 変更 | 変えない | 最後の行の JSON の形の `impact` の後に `"criteria": ["<当たる基準の番号>", ...]` を足す |

`_fix_prompt` は変えない。修正担当への添え書きは、`_fix_prompt` が今も呼ぶ `criteria_decide.fixer_impact` の返す文だけで変わる（#4013 が `_fix_prompt` を触るので、ここを変えない）。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_review_prompt` | 出力の形に `criteria` がある | — | — |
| ラウンド全体（`test_crossreview_run.py`） | 偽の相手役が基準 2 を含む 1 文の無い `major` を返すと修正担当が起動され、指示の行に `fixer_missing` の文が載る | 偽の相手役が `criteria` を持たない今の形 → 今の結果のまま | 偽の相手役が基準 1 だけの 1 文の無い `major` を返す → 修正担当は起動されず `converged` |

## 手順書と契約の説明

満たす決定: 決定 1〜5

| ファイル | 変える中身 |
| --- | --- |
| `review-criteria.json` の `summary` | 基準の並び（`criteria`）を全指摘に書かせること、`impact.kept_criteria` の基準は 1 文が無くても下げないこと、`impact.fixer_missing` の添え書き（#4045）を足す |
| `01-state-and-review.md` | 手順 2 の出力の形に `criteria` を足す。「指摘の基準」の重要度の規則に「1 文の無い `critical`・`major` と重要度の無い指摘は、基準の並びに 2 を含めば下げない（重要度の無い指摘は `major`）」を足す。`findings[]` の行の説明に `criteria`（和集合）を足す |
| `02-fix-and-rotation.md` | 修正担当へ渡す指示に、基準 2 で下げずに渡した指摘の添え書きを足す |
