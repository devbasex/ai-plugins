# #4045 の詳細設計: 1 文の欠けた基準 2 の重い指摘を下げずに渡す

設計文書 [issue-4045-design.md](./issue-4045-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。前提はスプリントブランチ `sprint/p12-lcrfast-r1`（head `68d887913`）が main へ入った後の形で、#4014 の `impact`・`effective_severity`・`fixer_impact` を持つ。

## 地図

パッケージ・モジュール構成（触るファイルだけ）:

```text
plugins/ndf/
├── contracts/data/review-criteria.json            … criteria_field・impact.kept_criteria・impact.fixer_missing・impact.reviewer・summary
├── src/ndf/domains/crossreview/
│   ├── criteria_decide.py                         … 基準の並びを読み、重要度と添え書きを決める
│   ├── journal.py                                 … 行に criteria と例外の記録を残し、出し直しで合わせる
│   └── drive.py                                   … 出力の形に criteria を足し、例外で渡す行を起動の前に記録する
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
| `contracts/data/review-criteria.json` | 変更 | 指摘の基準と指示の文 | — | — | キー `criteria_field`・`impact.kept_criteria`・`impact.fixer_missing`（新設）・`impact.reviewer`・`summary`（変更） |
| `src/ndf/domains/crossreview/criteria_decide.py` | 変更 | 基準と重要度の純粋な関数 | `review-criteria.json`・`severities.json` | モジュール | `criteria_of`・`kept_applies`・`kept_without_impact`（新設）・`effective_severity`・`reviewer_block`・`fixer_impact`（変更） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 指摘のジャーナル | `criteria_decide` | `FindingJournal` | `FindingJournal.kept_offer`（新設）・`_raised`・`FindingJournal.enter`・`FindingJournal._raised_again`・`FindingJournal.apply_fix`（変更） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `criteria_decide`・`journal` | モジュール | `_fixer_offered`（新設）・`_review_prompt`・`_round`・`_fix_record`・`_take_fix`・`merge_fix`（変更） |
| `skills/cross-review/docs/01-state-and-review.md` | 変更 | 手順書 | — | — | 「状態とレビュー」の手順 2・「指摘の基準」・状態ファイルの `findings[]` の行 |
| `skills/cross-review/docs/02-fix-and-rotation.md` | 変更 | 手順書 | — | — | 「修正」の渡す指示 |

## 基準の並びと重要度（criteria_decide）

満たす決定: 決定 1・2・3・5・7・8、受け入れ条件 1〜5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `criteria_of` | `criteria_decide.py` | 新設 | `criteria_of(value: object) -> tuple[str, ...]` | 相手役の `criteria` の値を、表にある基準の番号の重ねない並び（番号の順）にする |
| `kept_applies` | `criteria_decide.py` | 新設 | `kept_applies(severity: Optional[str], impact: object, criteria: Sequence[str]) -> bool` | 相手役の 1 件に例外（1 文が無くても下げない）を当てるか |
| `effective_severity` | `criteria_decide.py` | 変更 | `effective_severity(severity: Optional[str], impact: object, criteria: Sequence[str] = ()) -> str` | ジャーナルの行に書く重要度。`kept_applies` が真なら下げない |
| `kept_without_impact` | `criteria_decide.py` | 新設 | `kept_without_impact(row: Mapping[str, Any]) -> bool` | 1 文の無いまま基準 2 の例外で修正担当へ渡す行か |
| `reviewer_block` | `criteria_decide.py` | 変更 | 変えない（`reviewer_block(focus: Sequence[str]) -> list[str]`） | 重要度の行の後に `criteria_field` の文を 1 行足す。重要度の行は置き換えた `impact.reviewer` の文から組む（関数の中の組み方は今のまま） |
| `fixer_impact` | `criteria_decide.py` | 変更 | 変えない（`fixer_impact(row: Mapping[str, Any]) -> str`） | 1 文があれば今の文、無くて `kept_without_impact` が真なら `impact.fixer_missing` の文を「— 起きること: 」の後に返す |

- `criteria_of`
  - 事前条件: 無い（どの値でもよい）
  - 事後条件: 返す並びの要素は、`review-criteria.json` の `criteria` のキーと `focus_criterion` の値のどれか。重ならず、整数として小さい順
  - 副作用: 無い
- `effective_severity`
  - 事前条件: `severity` は `severity_of` を通した値。`criteria` は `criteria_of` の返り
  - 事後条件: 返りは必ず尺度の値。`criteria` を渡さない呼び出しは今と同じ値を返す
  - 副作用: 無い
- `kept_applies`
  - 事前条件: `severity` は `severity_of` を通した値。`criteria` は `criteria_of` の返り
  - 事後条件: `severity` が None か収束の妨げになる重さで、1 文が無く（`_has_impact` が偽）、`criteria` が `impact.kept_criteria` と重なるときだけ真
  - 副作用: 無い
- `kept_without_impact`
  - 事後条件: 行が整数の `kept_round` を持ち、`severity` が収束の妨げになる重さで、`impact` を持たないときだけ真。`raised_severity` の有無は見ない（引き継いだ行への出し直しで例外を当てた行も真）。`kept_round` の無い行（例外を当てていない行・前の版の行）は偽

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `criteria_of` | 値が配列 | 要素ごとに、真偽値を除く整数は 10 進の文字列へ、文字列は前後の空白を除き、表の番号にあるものだけを採る。重点（基準 3）の番号は、重点の無い実行でも採る（基準 2 の規則に関わらず、実行の重点の有無を `journal` が持たないため） |
| `criteria_of` | 値が真偽値でない整数か文字列の 1 つ | 要素 1 つの配列と同じ |
| `criteria_of` | 値が無い・`null`・真偽値・浮動小数・オブジェクト | 空の並び |
| `effective_severity` | `severity` が妨げにならない重さ（`minor`・`nit`） | `severity` |
| `effective_severity` | 1 文がある | `severity`。None なら妨げになる重さのうち最も軽いもの（`major`） |
| `effective_severity` | 1 文が無く、`kept_applies` が真（`criteria` が `kept_criteria` と重なる） | 同上（`severity`、None なら `major`） |
| `effective_severity` | 1 文が無く、重ならない | 妨げにならない重さのうち最も重いもの（`minor`） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `impact.kept_criteria` | 設定値 | `review-criteria.json` | `["2"]` |
| `impact.fixer_missing` | 設定値（文） | `review-criteria.json` | 「書かれていない（基準 {criteria} の指摘として下げずに渡した。当たるかを確かめ直し、結果のこの指摘に criteria_met（当たれば true、当たらなければ false）を足す。当たらなければ理由を添えて見送る）」の意味の文。`{criteria}` は `kept_criteria` を `・` でつないで埋める |
| `criteria_field` | 設定値（文） | `review-criteria.json` | 「criteria には、指摘が当たる基準の番号（上の番号）をすべて書く。」の意味の文 |
| `impact.reviewer` | 設定値（文。変更） | `review-criteria.json` | 今の文から「書けない指摘は {light_first} にする（impact の無い {blocking} は {light_first} として扱われる）。」を外し、「重要度は指摘そのものの重さで選び、impact を書けないことを理由に下げない（impact の無い {blocking} は、当たる基準によって {light_first} として扱われる）。」に置き換える。1 文を書く要求と基準ごとの書き方の例、「{light} は収束を止めず、修正担当へ渡らない。」は残す（設計文書の決定 7） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `criteria_of` | `severity_of`（尺度の値だけを通す）・`journal._text`（空白を除いた文字列） | `sprint/p12-lcrfast-r1` の `criteria_decide.py`・`journal.py` を読んだ | 使えない。どちらも 1 つの値を読み、配列と番号の表を扱わない。番号の表は `criteria_lines` と同じ `_table()` から作る |
| `kept_applies` | `effective_severity` の分岐（`blocking`・`_has_impact`） | 同上 | `_has_impact`・`blocking` を使う。重なりの判定は `kept_criteria` の集合との積だけで、既存の部品が無い |
| `kept_without_impact` | `downgraded`（下げた行か）・`_has_impact` | 同上 | `_has_impact` と `blocking`・`severity_of` を使う。`downgraded` は逆の条件で、そのまま使えない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `criteria_of` | `["2", "1"]` → `("1", "2")`、`[2]` → `("2",)`、`"2"` → `("2",)` | 重点の番号 `"3"`、重なり `["2", 2, " 2 "]` → `("2",)`、空の配列 → 空 | 表に無い番号 `["9"]`・真偽値 `True`・`[True]`・浮動小数・オブジェクト・`None` → 空 |
| `kept_applies` | 基準 2 を含む 1 文の無い `major`・`critical`・None → 真 | 空白だけの 1 文 → 真、1 文あり → 偽 | `minor`・`nit` → 偽、基準 1 だけ → 偽 |
| `effective_severity` | 基準 2 を含む 1 文の無い `major`・`critical` → そのまま、重要度の無い → `major` | 2 つの引数の呼び出しが今の表のまま、空白だけの 1 文は無い扱い | 基準 2 を含む `minor`・`nit` → そのまま、基準 1 だけの 1 文の無い `major` → `minor` |
| `kept_without_impact` | `kept_round` を持ち 1 文の無い `major` の行 → 真。`kept_round` を持ち `raised_severity` の無い行（引き継いだ行への出し直し）→ 真 | 1 文のある行・`minor` の行 → 偽 | `kept_round` の無い行（引き継ぎ・前の版）・`kept_round` が整数でない行 → 偽 |
| `reviewer_block` | 出力に `criteria_field` の文がある。1 文を書く要求の文（`impact` に、利用者の経路で何が起きるかを 1 文で書く）がある | 重点の有無で同じ文がある | 出力に「書けない指摘は minor にする」と「impact の無い critical・major は minor として扱われる」（無条件に下がる説明）が無い |
| `fixer_impact` | 1 文のある行 → 今の文、`kept_without_impact` の行 → `fixer_missing` の文 | 1 文があり基準 2 も含む行 → 今の文だけ | どちらでもない行 → 空 |

## ジャーナルの行の基準の並び（journal）

満たす決定: 決定 3・4・8、受け入れ条件 1〜4・6・7（数える元の記録）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_raised` | `journal.py` | 変更 | `_raised(item: dict[str, Any], round_no: int) -> dict[str, Any]` | `criteria_of(item.get("criteria"))` を先に読み、`effective_severity` の 3 つ目の引数に渡す。並びが空でなければ行に `criteria`（配列）を足す。`kept_applies` が真なら `kept_round: round_no` を足す |
| `FindingJournal.enter` | `journal.py` | 変更 | 変えない | 新しい行を作るときに `_raised(item, round_no)` を呼ぶ（呼び方だけが変わる） |
| `FindingJournal._raised_again` | `journal.py` | 変更 | 変えない | 今の重ね方（重い方の重要度・1 文・元の重要度）に加え、既にある `criteria` と今回の並びの和集合を番号の順で行に置き直す。どちらも空なら置かない。今回の `_raised(item, round_no)` が `kept_round` を持ち、行が持たなければ行へ置く |
| `FindingJournal.kept_offer` | `journal.py` | 新設 | `kept_offer(self, open_: Sequence[dict[str, Any]]) -> list[str]` | 修正担当へ渡す行のうち `kept_without_impact` が真の行の id を、渡す順で返す。行を変えない |
| `FindingJournal.apply_fix` | `journal.py` | 変更 | `apply_fix(self, items, handed, round_no, offered: Sequence[str] = ()) -> list[dict[str, Any]]` | 今の終わり方のマージの前に、`offered` にあり行のある id のうち `kept_handed_round` を持たない行へ `kept_handed_round: round_no` を置く（結果ファイルがあること = 指示を受け取った記録）。マージでは、今の `reported`（`outcome` が `fin.DECIDED` の 1 件）のうち `offered` にある行の 1 件ごとに `kept_result` を `{"round": round_no, "outcome": 結果の outcome}` で組ごと置き直し、その 1 件の `criteria_met` が真偽値なら組に足す。`outcome` が `unresolved`・欠けた・尺度に無い 1 件では `kept_result` を変えない（既にある組を残す） |

- `kept_offer` が返す id は、呼び手（`drive._round`）がラウンドの記録の `kept_offered` に置く（空なら置かない）。`apply_fix` の `offered` は、取り込む結果がそのラウンドの修正担当に由来するときだけ、そのラウンドの記録の `kept_offered` を呼び手（`drive._fixer_offered` を通した `drive._fix_record`）が渡す。ホストが自分で直して作った結果には空の並びを渡す

- `_raised_again`: 直した指摘（`fixed`）の再発で位置とセクションを置き換えるときも、`criteria` は合わせる（捨てない。#4014 の決定 4 と同じ）。引き継いだ行（`raised_severity` が無い）への出し直しでも和集合と `kept_round` を置き、`raised_severity` は今のとおり足さない（引き継いだ時点の扱いを変えない）。重要度は今と同じく `heavier(既にある値, 今回の effective_severity)` で、今回の値は今回の並びだけで決まる
- `kept_round`・`kept_handed_round` は一度置いたら消さず置き直さない。`kept_result` は `offered` にある行の後の結果のうち `fin.DECIDED` の 1 件でだけ組ごと置き直し、`offered` に無い（添え書きを付けずに渡した・ホストが直した）結果と、決めていない（`unresolved`・`outcome` の欠けた）1 件では変えない。後の出し直しで 1 文が補われると `kept_without_impact` は偽になり添え書きは付かなくなるが、受け入れ条件 7 の数える元は残る

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_raised` | `criteria` の形が違う | 相手役の書き損じ・古い出力 | `criteria_of` | 既定値で続ける（空の並び = 基準 2 に当たらない） | 無し（注意にも載せない。#4014 の `impact` の欠けと同じ扱い） | 要らない。打ち直しても同じ結果 |
| 状態ファイルの読み込み | 前の版の行に `criteria` が無い | 版を上げる前に始めた実行 | 行を読む関数 | 既定値で続ける（空の並び） | 無し | 要らない |
| `apply_fix` | 結果の `criteria_met` が無い・真偽値でない・`offered` に無い行への値 | 修正担当の書き損じ・添え書きの無い指摘への記入 | `apply_fix` | 捨てて続ける（`offered` にある行は `criteria_met` の無い組を置き、無い行の `kept_result` は変えない） | 無し（受け入れ条件 7 では判断の分からない行として数える） | 要らない |
| `apply_fix` | 結果の 1 件の `outcome` が `unresolved`・無い・尺度に無い | 修正担当が決めきれなかった・書き損じ | `apply_fix`（今の `reported` の絞り込み） | その 1 件で `kept_result` を変えない（既にある組を残す）。`kept_handed_round` は結果ファイルがあれば置く | 無し | 要らない |
| `apply_fix` | ラウンドの記録に `kept_offered` が無い・配列でない | この版より前に始めたラウンド・書き損じ | `drive._fixer_offered`（`store.list_of` で読む） | 空の並びとして続ける（`kept_handed_round`・`kept_result` を置かない） | 無し | 要らない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_raised` | 基準 2 を含む 1 文の無い `major` → 行の `severity` が `major`・`criteria` が `["2"]`・`raised_severity` が `major`・`kept_round` がそのラウンド | 並びが空 → 行に `criteria` と `kept_round` が無く、今の行と同じ。1 文のある基準 2 の `major` → `kept_round` が無い | 形の違う `criteria` → 空と同じ |
| `_raised_again` | `["1"]` の後に `["2"]` → `["1", "2"]`、重要度は `minor` から `major` へ | 同じ並びの出し直し → 変わらない。直した指摘の再発でも合わさる。`kept_round` を持つ行への出し直し → 最初のラウンドのまま | 引き継いだ `minor` の行が同じキーの `criteria: ["2"]`・`major`・1 文なしで出し直される → `severity` が `major`、`criteria` と `kept_round` が足され、`raised_severity` は足されず、`kept_without_impact` が真 |
| `kept_offer` | `kept_without_impact` の行の id を返す。行は変わらない | 渡す行に無い → 空 | `kept_without_impact` が偽の行 → 返さない |
| `apply_fix` | `offered` の行に `kept_handed_round` が置かれる。`criteria_met: false` の見送り → `kept_result` が `deferred`・偽。`true` の見送り（範囲外）→ `deferred`・真。`criteria_met: false` で直した → `fixed`・偽 | 既に `kept_handed_round` を持つ行 → 置き直さない。後のラウンドの `offered` にある結果 → `kept_result` が組ごと置き直される（前の `criteria_met` が残らない）。1 文が補われた後 → `kept_handed_round` が残る | `offered` が空 → `kept_handed_round`・`kept_result` を置かない。真偽値でない `criteria_met` → 組に入れない。`offered` に無い行への値 → 置かない。`kept_result` を持つ行へ後のラウンドで `outcome: unresolved`・`outcome` の無い 1 件 → `kept_result` が前の組のまま（`kept_handed_round` は置かれる） |

## 相手役への出力の形（drive）

満たす決定: 決定 2・8、受け入れ条件 4・5・7（数える元の記録）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_review_prompt` | `drive.py` | 変更 | 変えない | 最後の行の JSON の形の `impact` の後に `"criteria": ["<当たる基準の番号>", ...]` を足す |
| `_round` | `drive.py` | 変更 | 変えない | 判定が修正の要る（`CHANGES_REQUIRED`）とき、修正担当を起動する前の今の `_save(run)` より前に、`run.journal.kept_offer(open_)` が空でなければラウンドの記録の `kept_offered` に置く。今の `_save` が起動の前に状態ファイルへ書く（保存の時点は増やさない） |
| `_fixer_offered` | `drive.py` | 新設 | `_fixer_offered(record: Mapping[str, Any]) -> list[str]` | 取り込む結果がラウンド `record` の修正担当に由来するとき（`record` の判定が `CHANGES_REQUIRED` で `fix` を持たない）だけ、`store.list_of(record, "kept_offered")` を返す。それ以外（`errored`・`fix` を持つ・ほかの判定）は空。判定は `fixgate_decide.require_fix_for_changes` の `OK` でない条件（`verdict == CHANGES_REQUIRED and not fix`）と同じで、別の規則を作らない |
| `_fix_record` | `drive.py` | 変更 | `_fix_record(journal, ref, path, handed, round_no, offered: Sequence[str] = ())` | `offered` を `journal.apply_fix` へ渡す |
| `_take_fix` | `drive.py` | 変更 | 変えない | `_fixer_offered(store.last_round(run.data))` を `_fix_record` へ渡す。駆動の中の取り込み（`_fix`。判定を `CHANGES_REQUIRED` に置いた後で `fix` を置く前）と再開の取り込み（`_ingest_pending`。`INGEST` のときだけ）の両方がここを通り、どちらも修正担当の結果なので並びがそのまま渡る |
| `merge_fix` | `drive.py` | 変更 | 変えない | `rounds[-1]["fix"]` を置く前に `_fixer_offered(rounds[-1])` を読み、`_fix_record` へ渡す。`NO_SLOT` の後（`errored`）や、修正担当の結果を取り込んだ後（`fix` あり）にホストが自分で直して通した結果では空の並びになり、`kept_handed_round` を置かず `kept_result` を上書きしない |

`_fix` は変えない。起動枠が空かない（`NO_SLOT`）・結果を書かないまま終わったときは今のとおり結果ファイルが無く `_take_fix` を通らないので、`kept_handed_round` は置かれず、ラウンドは `errored` で残る。その後にホストが自分で直して `merge_fix` を通しても、`_fixer_offered` が空を返すので置かれない。次の実行は新しいラウンドで同じ行を `kept_offered` に置き直して起動する。

`_fix_prompt` は変えない。修正担当への添え書きと `criteria_met` の記入の求めは、`_fix_prompt` が今も呼ぶ `criteria_decide.fixer_impact` の返す文だけで変わる（#4013 が `_fix_prompt` を触るので、ここを変えない）。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_review_prompt` | 出力の形に `criteria` がある | — | — |
| ラウンド全体（`test_crossreview_run.py`） | 偽の相手役が基準 2 を含む 1 文の無い `major` を返すと修正担当が起動され、指示の行に `fixer_missing` の文が載り、状態ファイルのラウンドに `kept_offered`、行に `kept_handed_round` が残る。偽の修正担当が `criteria_met: false` で見送ると行の `kept_result` が `deferred`・偽になり、`criteria_met: false` で直すと `fixed`・偽になる。起動枠が空かない（偽の起動が `NO_SLOT` を返し結果を書かない）ラウンドの後に次の実行で起動できると、`kept_handed_round` は起動できたラウンドの番号になる（空かなかったラウンドには `kept_offered` だけが残る）。偽の修正担当が結果ファイルを書いた後に駆動が中断した（状態ファイルは起動前の保存のまま・結果ファイルが残る）状態から打ち直すと、再開の取り込みで `kept_handed_round` が中断したラウンドの番号になり、`kept_result` が置かれる。前の実行から引き継いだ軽い指摘を、偽の相手役が同じ本文・`criteria: ["2"]`・`major`・1 文なしで返すと、修正担当の指示の行に `fixer_missing` の文が載る | 偽の相手役が `criteria` を持たない今の形 → 今の結果のまま。`NO_SLOT` で `errored` になったラウンドへ、ホストの結果ファイル（`criteria_met` つき）を `merge_fix` で通す → 行に `kept_handed_round`・`kept_result` が無い。修正担当の結果を取り込んだラウンドへホストの結果を `merge_fix` で通す → `kept_result` が修正担当の組のまま。修正担当が結果を書いた後に中断した（`CHANGES_REQUIRED`・`fix` なし）ラウンドへ `merge_fix` → `kept_handed_round`・`kept_result` が置かれる。次のラウンドで同じ指摘が 1 文つきで出し直される → `kept_handed_round` が残る | 偽の相手役が基準 1 だけの 1 文の無い `major` を返す → 修正担当は起動されず `converged` |

## 手順書と契約の説明

満たす決定: 決定 1〜5・7・8

| ファイル | 変える中身 |
| --- | --- |
| `review-criteria.json` の `summary` | 基準の並び（`criteria`）を全指摘に書かせること、`impact.kept_criteria` の基準は 1 文が無くても下げないこと、1 文の無い重い指摘を下げるのは取り込みだけで相手役には下げさせないこと、`impact.fixer_missing` の添え書きと `criteria_met`（#4045）を足す |
| `01-state-and-review.md` | 手順 2 の出力の形に `criteria` を足す。「指摘の基準」の重要度の規則に「1 文の無い `critical`・`major` と重要度の無い指摘は、基準の並びに 2 を含めば下げない（重要度の無い指摘は `major`）」を足し、相手役が 1 文を書けない指摘を自分で `minor` にするという記述があれば外す。`findings[]` の行の説明に `criteria`（和集合）・`kept_round`・`kept_handed_round`・`kept_result` を、`rounds[]` の説明に `kept_offered` を足す |
| `02-fix-and-rotation.md` | 修正担当へ渡す指示に、基準 2 で下げずに渡した指摘の添え書きと、その指摘の結果に `criteria_met` を書くことを足す |
