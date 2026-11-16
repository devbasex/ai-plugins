# #3329 の詳細設計: 差し戻しの文と案内に、契約から作った形の説明を載せる

設計文書 [issue-3329-design.md](issue-3329-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。語（形の説明・差し戻しの文・案内・値ごとに要る項目）は設計文書の「用語」の意味で使う。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。新設のファイルは無い）:

```text
plugins/ndf/
├── scripts/
│   ├── lib/
│   │   └── unattended_report.py        区間の報告の読み手（形の説明と値ごとの例を足す）
│   ├── hook_lib/
│   │   └── unattended.py               人のいない実行の hook（差し戻しの文・案内・上限の後の記録）
│   └── tests/
│       ├── test_unattended_report.py
│       └── test_hook_unattended.py
└── skills/development-workflow/references/
    └── asking.md                       問い合わせの決まりの「人のいない実行」
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/lib/unattended_report.py` | 変更 | 形の宣言から形の説明の文と値ごとの例を作る純粋な関数を足し、`ReportForm` に値の意味を持たせる | `jsonio`（今どおり） |
| `plugins/ndf/scripts/hook_lib/unattended.py` | 変更 | 差し戻しの文と案内に形の説明を足し、上限の後の形に合わない Stop の誤りの一覧を標準エラーへ残す | `unattended_report`・`limits`・`jsonio`・`state_home`（今どおり） |
| `plugins/ndf/skills/development-workflow/references/asking.md` | 変更 | 差し戻しの文と案内が形の説明を載せること、上限の後は通して標準エラーへ残すことを書く | 無し（契約のファイルを指すだけ） |
| `plugins/ndf/scripts/tests/test_unattended_report.py` | 変更 | 形の説明が契約から作られることと、値ごとの例が判定に通ることを縛る | `unattended_report` |
| `plugins/ndf/scripts/tests/test_hook_unattended.py` | 変更 | 差し戻しの文・案内の中身、上限の後の扱い、契約の読み込みの回数、文の長さを縛る | `hook_lib.unattended`・`unattended_report`・`hook.py` |

依存の向きは今と同じで、`hook.py` → `hook_lib/unattended.py` → `lib/unattended_report.py` → `jsonio` の一方向である。区間の報告の読み手は hook を知らず、形の説明を作る関数もファイルと環境変数を読まないため、循環は生じない。`hook.py` は変えない（`mark_session` は今どおり文字列か None を、`review_stop` は今どおり辞書か None を返す）。

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/unattended_report.py` | `ReportForm`（データクラス） | 欄 `meanings` を足す（変更） |
| `plugins/ndf/scripts/lib/unattended_report.py` | モジュール | `form_from_dict`（変更: `meanings` を埋める）、`describe_form`（新設）、`form_examples`（新設）、`_requirement_lines`（新設）、`_condition_text`（新設）、`_example`（新設） |
| `plugins/ndf/scripts/hook_lib/unattended.py` | モジュール | `pushback_reason`（変更）、`start_guide`（新設）、`mark_session`（変更）、`review_stop`（変更）、`_read_report_form`（変更） |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `form_from_dict` | `plugins/ndf/scripts/lib/unattended_report.py` | 変更 | `form_from_dict(data: dict) -> ReportForm` | 契約の中身から `shape` を取り出し、値の意味を含めて読み手が使う形へ直す | 無し（形が違えば `FormError`） | 戻り値の `meanings` は `values` と同じキーの集合を同じ順で持ち、値は `shape.values` の説明の文字列である。説明が空でない文字列でなければ `FormError` | 無し |
| `describe_form` | `plugins/ndf/scripts/lib/unattended_report.py` | 新設 | `describe_form(form: ReportForm) -> str` | 形の宣言から、置き方・値ごとの意味と要る項目・材料の書き方・値ごとの例の順に並べた形の説明の文を作る | `form` は `form_from_dict` が作ったもの | 戻り値は `form.values` のすべて・`form.meanings` のすべて・各値の `always` の項目・各条件の項目と条件の値と `need`・`form.material` の 4 つの文字列・`form_examples(form)` のすべての値を部分文字列として含む。同じ `form` には同じ文字列を返す | 無し |
| `form_examples` | `plugins/ndf/scripts/lib/unattended_report.py` | 新設 | `form_examples(form: ReportForm) -> dict[str, str]` | 結果の値ごとに、判定に通る区間の報告の例を 1 つ作る | `form` は `form_from_dict` が作ったもの | キーは `form.values` と同じ順。どの値の例も `report_errors(例, form) == []` かつ `result_value(例) == 値` | 無し |
| `_requirement_lines` | `plugins/ndf/scripts/lib/unattended_report.py` | 新設 | `_requirement_lines(form: ReportForm) -> list[str]` | 結果の値ごとに、値・意味・要る項目・条件つきの項目の行を作る | 同上 | 値 1 つにつき 1 行で、`form.values` の順に並ぶ | 無し |
| `_condition_text` | `plugins/ndf/scripts/lib/unattended_report.py` | 新設 | `_condition_text(cond: Condition) -> str` | 条件つきの項目 1 つを「項目が〜で始まる（または〜と同じ）とき、〜も要る」の文にする | `cond` は `prefix` か `equals` の一方を持つ | `prefix` を持てば `TEXT_PREFIX`、`equals` を持てば `TEXT_EQUALS` の文になる | 無し |
| `_example` | `plugins/ndf/scripts/lib/unattended_report.py` | 新設 | `_example(value: str, form: ReportForm) -> str` | 結果の値 1 つの例の文を作る | `value` は `form.values` のどれか | 結果の行が最初の項目の行で、残りの項目は `form.items` の順に 1 行ずつ並ぶ。`always` の項目は空でも `無し` でもない仮の値を持ち、そのうち材料の項目は `material.body` を持つ。材料の項目が `material.body` のときだけ、結果の行より前に `material.heading` の行がある。`always` に無い項目は `無し` | 無し |
| `pushback_reason` | `plugins/ndf/scripts/hook_lib/unattended.py` | 変更 | `pushback_reason(errors: list[str], remaining: int, form: ur.ReportForm) -> str` | 誤りの一覧・残りの回数・決まりの置き場の後ろに、形の説明を足した差し戻しの文を作る | `errors` は空でない | 戻り値は各誤り・`remaining`・`ur.describe_form(form)` を含む。誤りの種類に関わらず形の説明を含む（I1） | 無し |
| `start_guide` | `plugins/ndf/scripts/hook_lib/unattended.py` | 新設 | `start_guide(form: ur.ReportForm \| None) -> str` | 区間の始めの案内の文を作る | 無し | `form` が None なら `GUIDE` と同じ文字列、そうでなければ `GUIDE` の後ろに空行と `ur.describe_form(form)` を続けた文字列（I4） | 無し |
| `mark_session` | `plugins/ndf/scripts/hook_lib/unattended.py` | 変更 | `mark_session(hook_input: dict, now: float) -> str \| None`（変えない） | 過ぎた印を消し、対象のセッションなら印を作って、契約を読んで作った案内の文を返す | 無し | 対象のセッションなら印（回数 0）があり、戻り値は `start_guide(読めた ReportForm か None)`。対象でなければ None で、契約を読まない | 印のファイルを書く・古い印を消す・契約のファイルを 1 回読む・読めなければ標準エラーへ 1 行 |
| `review_stop` | `plugins/ndf/scripts/hook_lib/unattended.py` | 変更 | `review_stop(hook_input: dict) -> dict \| None`（変えない） | 差し戻すかを決めて回数を更新し、差し戻すなら形の説明つきの文を返し、上限の後に通すなら誤りの一覧を標準エラーへ残す | 無し | 差し戻すときの `reason` は `pushback_reason(errors, limit - count, form)` で、`form` は判定に使ったのと同じもの（I8）。誤りがあり、背景の作業も予定も無く、差し戻さないとき（上限に達したとき）は回数が変わらず、標準エラーに 1 行出る（I5）。それ以外の振る舞いは今どおり（I6） | 印のファイルを書く・契約のファイルを 1 回読む・標準エラーへ 1 行 |
| `_read_report_form` | `plugins/ndf/scripts/hook_lib/unattended.py` | 変更 | `_read_report_form(command: str) -> ur.ReportForm \| None` | 区間の報告の契約を読み、読めなければ呼んだ副命令の名前で標準エラーへ 1 行残して None を返す | `command` は `unattended-start` か `unattended-stop` | 読めれば `ReportForm`。読めなければ None で、標準エラーに `ndf: <command>: 区間の報告の契約を読めない: <理由>` の 1 行 | 契約のファイルを 1 回読む・読めなければ標準エラーへ 1 行 |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `form_from_dict` | 値の説明が空でない文字列でない | 契約のファイルの `shape.values` の値が空か文字列でない | `form_from_dict` の値の確かめ | 呼び出し元へ返す（`FormError`） | hook では `_read_report_form` が標準エラーへ 1 行、終了コード 0 | 後始末は無い。契約を直せば次の起動で読める |
| `_read_report_form` | 契約のファイルを読めない・形が違う | ファイルが無い・JSON が壊れている・`shape` の形が違う | `ur.load_form` の `FormError` | 既定値で続ける（None を返す） | 標準エラーに `ndf: <command>: 区間の報告の契約を読めない: <理由>`、終了コード 0 | 後始末は無い。次の Stop か UserPromptSubmit で読み直す |
| `mark_session` | 印を書けない | 状態の置き場に書けない | `write_mark` の `OSError` | 呼び出し元へ返す（今どおり。`hook.py` の `_unattended` が受ける） | 標準エラーに `ndf: unattended-start: <例外の型>`、標準出力は空、終了コード 0 | 印は作られず、契約も読まない。次のプロンプトで作り直せる |
| `mark_session` | 契約を読めない | `_read_report_form` の行と同じ | `_read_report_form` | 既定値で続ける（`start_guide(None)` で今の案内だけを返す） | 標準出力に今の案内の文だけの `additionalContext`、標準エラーに 1 行、終了コード 0 | 印は作られたまま。Stop の側も契約を読めなければ差し戻さずに通す（I6） |
| `review_stop` | 契約を読めない | `_read_report_form` の行と同じ | `_read_report_form` | 既定値で続ける（今どおり、差し戻さずに通す） | 標準出力は空、標準エラーに 1 行、終了コード 0 | 回数を変えない。次の Stop で読み直す |
| `review_stop` | 上限の宣言を読めない | `limits.json` の形が違う | `limits.value` の `LimitsError` | 既定値で続ける（今どおり、差し戻さずに通す） | 標準出力は空、標準エラーに 1 行、終了コード 0 | 回数を変えない |
| `review_stop` | 回数を書けない | 状態の置き場に書けない | `_save_count` の `OSError` | 既定値で続ける（今どおり、警告して判定は返す） | 差し戻すなら今どおり `decision: block`、標準エラーに 1 行、終了コード 0 | 印の回数は古いまま。次の Stop で数え直す |
| `review_stop` | 上限に達した後も形に合わない | conductor が差し戻しの上限の回数を使い切っても形を直さなかった | `next_count` が差し戻さないと返し、誤りが空でなく背景の作業も予定も無い | 止める（Stop を差し戻さずに通し、記録だけ残す） | 標準出力は空、標準エラーに `ndf: unattended-stop: 差し戻しの上限（<回数> 回）に達したため通す: <誤り> / <誤り>`、終了コード 0 | 回数を変えない。区間はそのまま終わり、結果は Factory のランナーが決める |

次の 7 つは純粋な関数で、`form_from_dict` を通った `ReportForm` に対しては失敗しない。

- `describe_form`・`form_examples`・`_requirement_lines`・`_condition_text`・`_example`・`pushback_reason`・`start_guide`

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `review_stop` | 印が無い | None（今どおり） |
| `review_stop` | 印があり、契約を読めない | None、標準エラーに 1 行（今どおり） |
| `review_stop` | 印があり、上限の宣言を読めない | None、標準エラーに 1 行（今どおり） |
| `review_stop` | 背景の作業か予定が残る | None、回数を変えない（今どおり） |
| `review_stop` | 誤りが無い | None、回数を 0 に戻す（今どおり） |
| `review_stop` | 誤りがあり、回数が上限より少ない | 回数を 1 足し、`{"decision": "block", "reason": pushback_reason(誤り, 残り, form)}` |
| `review_stop` | 誤りがあり、回数が上限に達している | None、回数を変えない、標準エラーに誤りの一覧を 1 行 |
| `mark_session` | 対象でない・識別子が合わない | None、契約を読まない（今どおり） |
| `mark_session` | 対象で、契約を読める | 印を作り、`start_guide(form)` |
| `mark_session` | 対象で、契約を読めない | 印を作り、`start_guide(None)`、標準エラーに 1 行 |
| `_example` | 項目が結果の項目（`RESULT_ITEM`） | 先頭の結果の行として書き、項目の並びでは飛ばす |
| `_example` | 項目が `always` にあり、材料の項目である | 値は `material.body`。結果の行より前に `material.heading` の行と、仮の材料の行を置く |
| `_example` | 項目が `always` にあり、材料の項目でない | 値は仮の値 `<項目>` |
| `_example` | 項目が `always` に無い | 値は `無し`（`NONE_VALUE`） |

`_example` が条件つきの項目を `無し` にしてよいのは、仮の値 `<項目>` がどの条件の `prefix` にも `equals` にも当たらないためである。条件の値は空でない文字列で、`<` で始まる値は今の契約に無い。仮の値が条件に当たる契約に変わったときは、`form_examples` の事後条件を縛るテスト（I3）が落ちる。

形の説明の文の並び（設計文書の「入出力の契約」の順）:

1. 置き方の 1 行: `TEXT_PLACEMENT`（最後の応答の末尾に置き、`- 結果: <値>` の行の後ろに `- <項目>: <値>` の行を並べる。行頭は `- `。項目は `form.items` の順を `・` で繋いで示す。要らない項目は `無し`）
2. `_requirement_lines` の値ごとの行: `- <値>: <意味>。要る項目: <always を・で繋ぐか 無し>。<条件ごとの _condition_text>`
3. 材料の書き方の 1 行: `TEXT_MATERIAL`（`material.item` は `material.body` か `material.url_prefix` で始まる URL。`material.body` なら結果の行より前に `material.heading` の行を置き、その節に材料を書く）
4. 値ごとの例: 値 1 つに 1 つ、`例（<値>）:` の行と、` ```markdown ` のコードブロックに入れた `form_examples` の値

- 繰り返しの終わる条件: どの繰り返しも `form.values`・`form.items`・各値の `when` の有限の並びを 1 回なぞるだけである
- 計算量の注意: 無し（今の契約で値 3・項目 9・条件 3）

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `ReportForm.meanings` | 型（`dict[str, str]`。値 → 意味） | `plugins/ndf/scripts/lib/unattended_report.py` | 無し（`shape.values` から作る。正本は契約のファイル） |
| `RESULT_ITEM` | 定数 | `plugins/ndf/scripts/lib/unattended_report.py` | `"結果"`。`RESULT_RE` もこの定数から組み、結果の項目の名前を 1 か所に置く |
| `TEXT_HEAD` | 定数（形の説明の見出しの文） | `plugins/ndf/scripts/lib/unattended_report.py` | `"区間の報告の形（契約のファイルから作った説明）:"` |
| `TEXT_PLACEMENT` | 定数（置き方の文のテンプレート） | `plugins/ndf/scripts/lib/unattended_report.py` | `{result}`・`{items}`・`{none}` を埋める書式の文字列 |
| `TEXT_PREFIX`・`TEXT_EQUALS` | 定数（条件の文のテンプレート） | `plugins/ndf/scripts/lib/unattended_report.py` | `「{item}」が {cond} で始まるとき「{need}」も要る` と `「{item}」が {cond} のとき「{need}」も要る` |
| `TEXT_MATERIAL` | 定数（材料の書き方の文のテンプレート） | `plugins/ndf/scripts/lib/unattended_report.py` | `Material` の 4 欄を埋める書式の文字列 |
| `EXAMPLE_MATERIAL` | 定数（例の材料の節の仮の本文） | `plugins/ndf/scripts/lib/unattended_report.py` | `"<人が判断するための材料>"` |
| `GUIDE` | 定数（今の案内の文） | `plugins/ndf/scripts/hook_lib/unattended.py` | 今の文のまま |
| `MAX_TEXT_CHARS` | テストの定数 | `plugins/ndf/scripts/tests/test_hook_unattended.py` | `5000`（Claude Code の上限 10,000 文字の半分。設計文書の非機能） |

テンプレートの定数が持つのは文の言い回しだけで、結果の値・項目の名前・条件の値・材料の 4 欄は持たない（I2）。値と項目の正本は契約のファイルの `shape` の 1 つのままである。

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じセッションの UserPromptSubmit と Stop | 印のファイル | 取らない（#3242 の前提のまま。この変更は印の読み書きの順序を変えず、契約を読む処理を足すだけである） | 印は `write_atomic` で書くため、途中で止まっても壊れた印は残らない。壊れた印は `read_mark` が None とみなす |
| 別のセッションの hook | 契約のファイル（読むだけ） | 取らない（読むだけで書かない） | 形の説明は読むたびに作り直し、保存しない。何度呼んでも同じ契約から同じ文になる |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `review_stop` | 上限に達して通すときの回数と誤りの一覧（`ndf: unattended-stop: 差し戻しの上限（<回数> 回）に達したため通す: <誤り> / <誤り>`） | 標準エラー（1 行。Claude Code のデバッグの記録に残る） | 応答の文の全体（誤りの文に載る値は結果の行と材料の行の値だけ） |
| `_read_report_form` | 契約を読めない理由（`ndf: <command>: 区間の報告の契約を読めない: <理由>`） | 標準エラー（1 行） | 秘密は扱わない |
| `review_stop` | 差し戻しの文（誤りの一覧・残りの回数・形の説明） | 標準出力の `decision: block` の `reason`（Claude へ渡る） | 契約の `cases`・`rule` の全文 |
| `mark_session` | 案内の文（今の案内と形の説明） | 標準出力の `additionalContext`（`hook.py` の `_prompt_context` が包む） | 契約の `cases`・`rule` の全文 |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `describe_form` | `lib/issue_items.py` の `describe_gaps`、`lib/tool_paths.py` の `describe` | `grep -rnE "def (describe\|render\|explain)\w*\(" plugins/ndf/scripts --include=*.py` | 使えない。どちらも別の型（課題の欠け・パスの並び）を文にする関数で、区間の報告の形を扱わない |
| `form_examples` | 契約のファイルの `cases` | `python3 -c` で `unattended-report.json` のキーを並べた（`name`・`summary`・`rule`・`shape`・`writers`・`readers`・`cases`） | 使えない。`cases` は判定のテストの例の組で誤りの例を含み、値ごとに 1 つの正しい例という形でない。`shape` を変えたテストで例が追随しない |
| `_requirement_lines` | `unattended_report.py` の `_missing_items`（値ごとに要る項目を集める） | `unattended_report.py` を読んだ | 使えない。応答の項目の値で条件を評価して欠けを返す関数で、条件そのものを文にしない |
| `_condition_text` | `Condition.matches` | 同上 | 使えない。条件に当たるかの判定で、文を作らない |
| `_example` | `asking.md` の「人のいない実行」の例 | `grep -n "人のいない実行" -A40 plugins/ndf/skills/development-workflow/references/asking.md` | 使えない。文書の中の固定の例で、コードから読むと値と項目の正本が 2 つになる（I2） |
| `start_guide` | `hook.py` の `_prompt_context` | `sed -n 100,160p plugins/ndf/scripts/hook.py` | `_prompt_context` は今どおり使う（案内の文を `additionalContext` に包む）。案内の文の組み立ては持たないため、文は `start_guide` が作る |

文字列の組み立ては標準ライブラリの f 文字列・`str.format`・`str.join` で行う（設計文書の外部の調査 LIB2・決定 1）。

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `form_from_dict` | 今の契約で `meanings` が `shape.values` と同じ値と意味を同じ順で持つ | 値が 1 つだけの `shape` | 値の説明が空・文字列でない `shape` で `FormError` |
| `describe_form` | 今の契約で、3 つの値・その意味・`always` の項目・3 つの条件の項目と条件の値と `need`・材料の 4 欄が現れる | 値か要る項目か条件を変えた別の `shape` で、変えた後の語が現れ、変える前の値が現れない（I2） | 該当しない（純粋な関数で、`form_from_dict` を通った形にだけ呼ばれる） |
| `form_examples` | 今の契約で、どの値の例も `report_errors` が空で `result_value` がその値になり、例が `describe_form` の戻り値に含まれる（I3） | 材料が要らない値（`完了`）の例に `material.heading` の行が無い。値・要る項目を変えた別の `shape` でも誤り 0 件 | 該当しない（同上） |
| `_requirement_lines` | 値ごとに 1 行で、契約の値の順に並ぶ | `always` が空の値（`完了`）で要る項目が `無し` と書かれる | 該当しない（同上） |
| `_condition_text` | `prefix` と `equals` の条件がそれぞれのテンプレートの文になる | `need` が 2 つ以上の条件で、すべての項目が現れる | 該当しない（同上） |
| `_example` | `判断が要る` の例で、結果の行より前に `material.heading` の行があり、材料の項目が `material.body` | `always` に無い項目がすべて `無し` | 該当しない（同上） |
| `pushback_reason` | 誤りの一覧・残りの回数・形の説明を含む | 誤りの種類（結果の行が無い・値が契約の外・要る項目の欠け・材料の誤り・`次のコマンド:` の残り）のそれぞれで、同じ形の説明を含む（I1）。5 種類すべての誤りが出た文が `MAX_TEXT_CHARS` 以下 | 該当しない（同上） |
| `start_guide` | `form` を渡すと `GUIDE` と形の説明を含み、`MAX_TEXT_CHARS` 以下 | `form` が None で `GUIDE` と同じ文字列 | 該当しない（同上） |
| `mark_session` | 対象のセッションで印ができ、戻り値が `start_guide(ur.load_form())` と同じ。`hook.py unattended-start` を `python3 -S` で打った `additionalContext` も同じ（既存の 3 つのテストの期待値を `un.GUIDE` からこの値へ変える） | 対象でないプロンプトで契約を読まない（`ur.load_form` を数える置き換えで 0 回） | 契約を読めないとき、印ができ、戻り値が `GUIDE`、標準エラーに `ndf: unattended-start: 区間の報告の契約を読めない` の 1 行。印を書けないときは今どおり標準エラーに 1 行で標準出力が空 |
| `review_stop` | 差し戻す Stop の `reason` が `pushback_reason(誤り, 残り, ur.load_form())` と同じ。差し戻す Stop 1 回で `ur.load_form` が 1 回だけ呼ばれる（I8） | 上限まで差し戻した後の、値が契約の外の Stop が None を返し、回数が上限のまま、標準エラーに誤りの一覧の 1 行（I5）。背景の作業が残る Stop と誤りの無い Stop では標準エラーに上限の行が出ない | 契約・上限の宣言を読めない Stop と、回数を書けない Stop は既存のテストの期待値のまま通る（I6） |
| `_read_report_form` | 読めれば `ReportForm` | 該当しない | 読めなければ None と、渡した副命令の名前の標準エラーの 1 行（既存の `unattended-stop` のテストは期待値のまま） |
