# #4014 の詳細設計: 起きることを書けない重い指摘を軽い指摘として残す

設計文書 [issue-4014-design.md](./issue-4014-design.md) の決定 1〜7 を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。新設のファイルは無い）:

```text
plugins/ndf/
├── src/ndf/domains/crossreview/
│   ├── criteria_decide.py     effective_severity・downgraded を新設、reviewer_block の重要度の行を替える
│   ├── journal.py             _raised を新設、enter・_raised_again が下げた後の値と impact・raised_severity を書く
│   └── drive.py               _review_prompt の JSON の形に impact、_fix_prompt の各指摘に起きることの 1 文
├── contracts/data/review-criteria.json   impact のキー（相手役への指示の文・修正担当への添え書きの名前）と summary
├── skills/cross-review/docs/
│   ├── 01-state-and-review.md   1 ラウンドの JSON の形・指摘の基準・判定の表・状態ファイルの findings[]
│   └── 02-fix-and-rotation.md   修正担当へ渡す指示の中身に起きることの 1 文
└── tests/domains/crossreview/
    ├── test_crossreview_criteria.py
    ├── test_crossreview_journal.py
    ├── test_crossreview_run.py    偽の相手役（Fake）が文字列の指摘に 1 文を添える
    └── test_crossreview_report.py
```

依存の向き: `journal` と `drive` が `criteria_decide` を読む。`criteria_decide` は `ndf.contracts`（`review-criteria.json`・`severities.json`）だけを読む。今の向きのままで、新しい依存は足さない。`report_decide`・`threads`・`finish_decide`・`prior_decide` は変えない（行の `severity` を今のまま読む）。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/crossreview/criteria_decide.py` | 変更 | 重要度と 1 文から取り込む重要度を決め、下げた行を見分け、相手役への指示を組む | `ndf.contracts`・`ndf.contracts.facts` | モジュール `criteria_decide` | `effective_severity`（新設）・`downgraded`（新設）・`reviewer_block`（変更） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 相手役の指摘を下げた後の重要度でジャーナルへ取り込む | `criteria_decide`・`prior_decide`・`finish_decide` | クラス `FindingJournal`・モジュール `journal` | `_raised`（新設）・`FindingJournal.enter`（変更）・`FindingJournal._raised_again`（変更） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 相手役と修正担当への指示に 1 文の項目を載せる | `criteria_decide` | モジュール `drive` | `_review_prompt`（変更）・`_fix_prompt`（変更） |
| `contracts/data/review-criteria.json` | 変更 | 1 文を求める指示の文と修正担当への添え書きの名前の唯一の保存先 | — | — | — |
| `skills/cross-review/docs/01-state-and-review.md` | 変更 | 出力の形・重要度の扱い・ジャーナルの行の項目を人へ説明する | — | — | — |
| `skills/cross-review/docs/02-fix-and-rotation.md` | 変更 | 修正担当へ渡す指示の中身を人へ説明する | — | — | — |
| `tests/domains/crossreview/test_crossreview_criteria.py` | 変更 | 取り込む重要度の規則と指示の行を縛る | — | — | — |
| `tests/domains/crossreview/test_crossreview_journal.py` | 変更 | 取り込み・出し直し・引き継ぎの重要度を縛る | — | — | — |
| `tests/domains/crossreview/test_crossreview_run.py` | 変更 | 実行の通しで、下げた指摘が修正担当へ渡らず 1 文が修正担当へ載ることを縛る | — | — | — |
| `tests/domains/crossreview/test_crossreview_report.py` | 変更 | 下げた行が記録のコメントの表に「軽い」で出ることを縛る | — | — | — |

## 取り込む重要度の規則（criteria_decide）

満たす決定: 決定 2、決定 3、決定 5、受け入れ条件 1・3・4・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `effective_severity` | `src/ndf/domains/crossreview/criteria_decide.py` | 新設 | `effective_severity(severity: Optional[str], impact: object) -> str` | 相手役が付けた重要度（`severity_of` を通した値）と 1 文から、ジャーナルの行に書く重要度を返す |
| `downgraded` | `src/ndf/domains/crossreview/criteria_decide.py` | 新設 | `downgraded(row: Mapping[str, Any]) -> bool` | ジャーナルの行が、取り込みで `minor` へ下げた行かを返す |
| `reviewer_block` | `src/ndf/domains/crossreview/criteria_decide.py` | 変更 | 変えない（`reviewer_block(focus: Sequence[str]) -> list[str]`） | 重要度の行を、`review-criteria.json` の `impact` の指示の文から組む行に替える |

- `effective_severity`
  - 事前条件: `severity` は `severity_of` を通した値（尺度の値か None）。`impact` は相手役の出力の値をそのまま渡す（型を確かめない）
  - 事後条件: 返りは必ず尺度の値（`critical`・`major`・`minor`・`nit` のどれか）。1 文を持つかは「`impact` が文字列で、空白を除いて空でない」で決める（前提 1）
  - 返りの型を `str` にするのは、どの入力でも尺度の値を返し、None を返す枝が無いためである
- `downgraded`
  - 事後条件: `raised_severity` の項目が行に無ければ偽（前の版の行・引き継いだ行・組み直しの行）。項目があり、その値が収束の妨げになる重さか null で、`severity` が `minor` なら真
  - 呼び手: テストと、受け入れ条件 6 の集計（状態ファイルの `findings` を読んで数える）。止める判定・表・スレッドは呼ばない（行の `severity` を読めば足りる）
- `reviewer_block`
  - 変える前: 最後の行が「重要度 severity は {尺度}。基準に当たる指摘は {止める重さ}。{軽い重さ} は収束を止めず、修正担当へ渡らない。」
  - 変えた後: 最後の行が「重要度 severity は {尺度} から選ぶ。」に続けて `review-criteria.json` の `impact.reviewer` の文（止める重さ・軽い重さの名前は尺度から埋める）。「基準に当たる指摘は」の文を持たない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `effective_severity` | 1 文が文字列でない（数値・配列・null） | 相手役の出力の形の誤り | 関数の中の型の確かめ | 既定値で続ける（1 文を持たない指摘として読む） | 無し | 無し |
| `downgraded` | `raised_severity` が尺度に無い文字列 | 状態ファイルを手で書き換えた | `severity_of` | 既定値で続ける（null と同じく収束の妨げになる重さとして読む） | 無し | 無し |
| `reviewer_block` | `review-criteria.json` に `impact` のキーが無い | 契約のデータの誤り | `_table()` の読み出し | 止める（`KeyError`。契約のデータの誤りは契約のテストで先に落ちる） | 実行の例外 | データを直して打ち直す |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `effective_severity` | `severity` が `minor` か `nit` | `severity` のまま（1 文の有無によらない） |
| `effective_severity` | `severity` が `critical` か `major`、1 文あり | `severity` のまま |
| `effective_severity` | `severity` が `critical` か `major`、1 文なし | `minor` |
| `effective_severity` | `severity` が None、1 文あり | `major` |
| `effective_severity` | `severity` が None、1 文なし | `minor` |
| `downgraded` | 行に `raised_severity` が無い | 偽 |
| `downgraded` | `raised_severity` が `critical`・`major`・null（尺度に無い値を含む）で、`severity` が `minor` | 真 |
| `downgraded` | ほか | 偽 |

- 下げる先の `minor` と上げる先の `major` は文字列で直書きせず、尺度の表（`severities.json`）から引く（今の `_rank` が上げる先と同じ引き方で読む）
  - 下げる先: 収束の妨げにならない重さのうち最も重いもの
  - 上げる先: 収束の妨げになる重さのうち最も軽いもの

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `impact.reviewer` | 設定値（相手役への指示の文） | `contracts/data/review-criteria.json` | 「{止める重さ} には impact に、利用者の経路で何が起きるかを 1 文で書く（基準 4 の指摘では、実装する人が何を作り違えるか）。書けない指摘は {軽い重さの先頭} にする（impact の無い {止める重さ} は {軽い重さの先頭} として扱われる）。{軽い重さ} は収束を止めず、修正担当へ渡らない。」の意味の文 |
| `impact.fixer_label` | 設定値（修正担当への添え書きの名前） | `contracts/data/review-criteria.json` | 「起きること」 |
| `summary` | 設定値（データの説明） | `contracts/data/review-criteria.json` | 今の文に、`impact` が critical・major に求める 1 文の指示であることと、1 文の無い重い指摘・重要度の無い指摘を取り込みで下げることを足す |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `effective_severity` | `criteria_decide.severity_of`・`blocking`・`_rank`、`journal._text` | `criteria_decide.py`・`journal.py` を読んだ | `blocking` で止める重さかを読む。1 文の確かめは `journal._text` と同じ規則だが、`criteria_decide` は `journal` を読まない向きなので同じ 1 行の式を持つ（関数を `criteria_decide` へ移すと `journal` の位置の読み取りまで動くので移さない） |
| `downgraded` | `criteria_decide.blocking`・`severity_of` | 同上 | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `effective_severity` | 4 つの重要度と None × 1 文の有り無しの 10 通りが処理の手順の表のとおり | 1 文が空文字・空白だけ・前後に空白のある 1 文 | 1 文が数値・配列・null |
| `downgraded` | 1 文の無い `major` を取り込んだ行が真。1 文つきの `major`・元から `minor` の行が偽 | `raised_severity` が null で `severity` が `minor` の行が真 | `raised_severity` の無い行（引き継ぎ・前の版）が偽。尺度に無い `raised_severity` は null と同じ |
| `reviewer_block` | 出力に 1 文を求める行があり、「基準に当たる指摘は」で始まる文が無い | 重点がある・無いで重要度の行が変わらない | `review-criteria.json` の `impact.reviewer` を差し替えると出力の行が変わる |

## ジャーナルへの取り込み（journal）

満たす決定: 決定 2、決定 3、決定 4、決定 7、受け入れ条件 1・2・3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_raised` | `src/ndf/domains/crossreview/journal.py` | 新設 | `_raised(item: dict[str, Any]) -> dict[str, Any]` | 相手役の 1 件から、行に足す位置・下げた後の重要度・1 文・元の重要度を返す |
| `FindingJournal.enter` | `src/ndf/domains/crossreview/journal.py` | 変更 | 変えない | 新しい指摘の行に `_located` の代わりに `_raised` の返りを足す |
| `FindingJournal._raised_again` | `src/ndf/domains/crossreview/journal.py` | 変更 | 変えない | 出し直しの重要度を下げた後の値で重ね、1 文と元の重要度を足す |

- `_raised`
  - 事後条件: 返りは `_located(item)` の位置（`path`・`line`）に、次の 3 つを足した辞書
    - `severity`: `effective_severity` の返り
    - `raised_severity`: `severity_of(item.get("severity"))`。None なら null。相手役が `severity` のキーを省いた指摘（テストの `Fake` の文字列の指摘を含む）も `KeyError` にせず、重要度の無い指摘として読む
    - `impact`: 1 文があるときだけ、前後の空白を除いた値
- `FindingJournal.enter`
  - 変える前: 新しい行に `_located(item)`（形の合う重要度だけ）を足す
  - 変えた後: 新しい行に `_raised(item)` を足す。ラウンドの収束の妨げになるキーは、今のまま重ねた後の行の `severity` で決める（下げた行は数えない）
- `FindingJournal._raised_again`
  - 変える前: `heavier(既にある行の重要度, 相手役が付けた重要度)` を残す
  - 変えた後: `new = _raised(item)` として、`severity` は `heavier(既にある行の重要度, new["severity"])`。既にある行に `impact` が無く `new` にあれば足す。既にある行が `raised_severity` の項目を持つときだけ、`heavier(既にある元の値, new["raised_severity"])` へ更新する（引き継いだ行・前の版の行には足さない）。直した指摘が戻ったときの位置とセクションの置き換えは今のまま（`severity` 以外の位置だけを取る）
- `FindingJournal.carry` は変えない。今のまま `_located` で記録された重要度を読み、`impact`・`raised_severity` を足さない

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_raised` | 重要度・1 文の形が違う | 相手役の出力の形の誤り | `severity_of`・`effective_severity` | 既定値で続ける（重要度の無い指摘・1 文の無い指摘として読み、`minor` にする） | 無し（記録のコメントの表に `minor` と「軽い」で出る） | 無し |
| `FindingJournal._raised_again` | 既にある行の `severity` が尺度に無い | 状態ファイルを手で書き換えた・前の版の行 | `severity_of` | 既定値で続ける（今と同じく None を収束の妨げになる重さとして重ねる） | 無し | 無し |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_raised` | 1 文つきの `major` は `severity` が `major`・`impact` が 1 文・`raised_severity` が `major` | 1 文の前後の空白を除く。`severity` のキーを持たない指摘は例外にならず、1 文つきなら `severity` が `major`、1 文が無ければ `minor`（どちらも `raised_severity` が null） | 重要度が `"重い"`・1 文が数値のとき `severity` が `minor`・`raised_severity` が null・`impact` が無い |
| `FindingJournal.enter` | 1 文の無い `major` と `critical` の行が `minor` で、返すキーに入らない | 同じラウンドで同じ指摘が 1 文の無い `major` → 1 文つきの `major` の順でも逆の順でも `major` で 1 文を持つ | 本文の無い指摘は今のまま入れない |
| `FindingJournal._raised_again` | 下げた行が 1 文つきで出し直されると `major` に戻り、`downgraded` が偽になる | 引き継いだ行が相手役から出し直されても `raised_severity` を持たない | 直した指摘が 1 文の無い `major` で戻ると未解決・`minor` になり、修正担当へ渡らない |
| `FindingJournal.carry` | 引き継いだ重要度の無い行は `severity` を持たず、今のまま収束の妨げになる | — | — |

## 相手役と修正担当への指示（drive）

満たす決定: 決定 1、決定 5、決定 6、受け入れ条件 2・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_review_prompt` | `src/ndf/domains/crossreview/drive.py` | 変更 | 変えない | 最後の行の JSON の形に `impact` を足す |
| `_fix_prompt` | `src/ndf/domains/crossreview/drive.py` | 変更 | 変えない | 各指摘の行の末尾に、行が `impact` を持てば「— {`impact.fixer_label`}: {1 文}」を足す |

- `_review_prompt`
  - 変える前: `{"text": "<指摘>", "severity": "<重要度>", "path": ..., "line": ..., "section": ...}`
  - 変えた後: `severity` の後ろに `"impact": "<critical・major のとき、利用者の経路で何が起きるか 1 文 か 空>"` を足す
- `_fix_prompt`
  - 変える前: `- <id>: <本文>（<重要度>・<場所>）`
  - 変えた後: 行が `impact` を持てば、その後ろに `— 起きること: <1 文>` を足す。持たない行（引き継いだ行・前の版の行）は今のまま。`_where` は変えない

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_review_prompt` | 相手役への指示に `"impact"` の形と `reviewer_block` の行がすべて載る | — | — |
| `_fix_prompt` | 1 文つきの `major` の行に「起きること」と 1 文が載る | 引き継いだ重い行（1 文なし）は今の形のまま | — |

## 手順書と既存のテスト

満たす決定: 決定 5、決定 6、決定 7、受け入れ条件 1〜5

| ファイル | 変える中身 |
| --- | --- |
| `skills/cross-review/docs/01-state-and-review.md` | 「1 ラウンド」の手順 2 の JSON の形に `impact` を足す。「指摘の基準」の「重要度の無い・知らない値の指摘は、収束の妨げになる側（`major` と同じ扱い）に倒す」を、1 文の無い `critical`・`major` と重要度の無い指摘は取り込みで `minor` に、1 文のある重要度の無い指摘は `major` になる規則に替え、引き継いだ指摘は記録された重要度のまま扱うことを書く。「判定」の表の「重要度の無い指摘」を外す。「状態ファイル」の `findings[]` の行に `impact`・`raised_severity` を足し、`severity` が下げた後の値であることを書く |
| `skills/cross-review/docs/02-fix-and-rotation.md` | 修正担当へ渡す指示の中身（`id`・本文・重要度・場所）に、起きることの 1 文を足す |
| `tests/domains/crossreview/test_crossreview_run.py` | 偽の相手役（`Fake`）が文字列の指摘を `{"text": t, "section": "", "impact": "<1 文>"}` にして返す（今の多くのテストが重要度の無い指摘で修正担当の起動を見ているため）。下げた指摘で修正担当を起動しない通しのテストと、1 文が修正担当への指示に載る通しのテストを足す |
| `tests/domains/crossreview/test_crossreview_criteria.py` | `critical`・`major` を返して修正担当や収束の妨げを確かめる 2 件に 1 文を足す。`effective_severity`・`downgraded`・`reviewer_block` のテストを足す |
| `tests/domains/crossreview/test_crossreview_journal.py` | `test_enter_keeps_the_heaviest_severity_within_one_round` の `major` に 1 文を足す。取り込み・出し直し・引き継ぎのテストを足す |
| `tests/domains/crossreview/test_crossreview_report.py` | 下げた行が記録のコメントの表に `minor` と「軽い（直さずに残した）」で出て、スレッドを作らないテストを足す。状態を直に組むテストの `major` は取り込みを通らないので変えない |
