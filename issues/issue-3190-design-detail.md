# #3190 の詳細設計: 用語集の意味の説明に残る廃止した語と避ける語を落とす

設計文書 [issues/issue-3190-design.md](issue-3190-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。語の意味（意味の説明・避ける語・単独で照合する廃止した語）は設計文書の「用語」の表のとおりに使う。

この文書の関数名・欄名と、設計文書の語の対応:

| 設計文書の語 | この文書で指す識別子 |
| --- | --- |
| 単独で照合する廃止した語の欄 | `terms[].deprecated_standalone` |
| 廃止した語の読み出し（2 つの欄を合わせる） | `all_deprecated_of` |
| 語の正規表現 | `lib/glossary_words.py` の `word_pattern` と `standalone_part` |
| 構造の検査 | `structure_findings` |
| 意味の説明の廃止した語の規則 | 規則 `deprecated_meaning`（`_meaning_findings` が当てる） |
| 正規表現の組 | `WordPair`（型は変えない） |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。新設のファイルは無い）:

```text
ai-plugins/
├── .github/workflows/glossary.yml
├── docs/
│   ├── glossary.md
│   └── glossary/glossary.json
└── plugins/ndf/
    ├── scripts/
    │   ├── glossary.py
    │   ├── lib/glossary_words.py
    │   └── tests/test_glossary.py
    └── skills/requirements-design/references/glossary-format.md
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/lib/glossary_words.py` | 変更 | 文書の中の語を探す正規表現を作り、漢字の文字の範囲を 1 か所で持つ | 無し（標準ライブラリの `re` だけ） |
| `plugins/ndf/scripts/glossary.py` | 変更 | 用語集の読み出し・構造の検査・意味の説明の照合・文書の照合・文書の生成・差分を持つ | `lib/glossary_words.py` ほか既存の `lib/` の部品 |
| `plugins/ndf/scripts/tests/test_glossary.py` | 変更 | 構造の検査と照合の振る舞いを、一時の用語集で縛る | `glossary.py` |
| `docs/glossary/glossary.json` | 変更 | 避ける語 4 つの登録と、意味の説明 21 か所の言い換え（設計文書の決定 5） | 無し |
| `docs/glossary.md` | 変更 | `glossary.py render` で作り直す | `docs/glossary/glossary.json` |
| `plugins/ndf/skills/requirements-design/references/glossary-format.md` | 変更 | 欄の表・規則の表・`schema` の条件・規則の数に新しい欄と規則を足す | 無し |
| `.github/workflows/glossary.yml` | 変更 | 先頭のコメントに、構造の検査が意味の説明の廃止した語も見ることを足す | 無し |

依存の向きは `glossary.py` → `lib/glossary_words.py` の一方向で、`lib/glossary_words.py` は `glossary.py` を読まない。漢字の判定は `lib/glossary_words.py` だけが持ち、`glossary.py` の形の検査も照合もそこを呼ぶ。循環は無い。

`plugins/ndf/scripts/plan-to-spec-steps.py` は `structure_findings(g, decl)` を呼んで `schema` の当たりだけを拾う。引数を省ける形に保つため、このファイルは触らない（「関数の仕様」の `structure_findings`）。

## クラスと関数の構造

型の関係は設計文書の「構造」の節のとおりで、型は増やさない。ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/glossary_words.py` | モジュール `glossary_words` | `KANJI`（新設の定数）・`is_kanji_edged`（新設）・`standalone_part`（新設）・`word_pattern`（変更）・`_word_part`（変えない） |
| `plugins/ndf/scripts/glossary.py` | モジュール `glossary` | `deprecated_standalone_of`（新設）・`all_deprecated_of`（新設）・`_standalone_findings`（新設）・`_meaning_findings`（新設）・`structure_findings`（変更）・`_build_word_patterns`（変更）・`cmd_check`（変更）・`render_text`（変更）・`replacement`（変更）・`diff_items`（変更）・`STRUCTURE_RULES`（変更）・`deprecated_of`（変えない。形の検査だけが使う）・`_deprecated_findings`（変えない）・`_uncovered`（変えない）・`mask_inline_code`（変えない） |
| `plugins/ndf/scripts/tests/test_glossary.py` | テストのモジュール | 「単体テストの観点」の行ごとにテストの関数を足す |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `is_kanji_edged` | `plugins/ndf/scripts/lib/glossary_words.py` | 新設 | `is_kanji_edged(w: str) -> bool` | 語の先頭か末尾の 1 文字が漢字かを返す | `w` は文字列 | 空の文字列は偽。先頭か末尾が `KANJI` の範囲にあるときだけ真 | 無し |
| `standalone_part` | `plugins/ndf/scripts/lib/glossary_words.py` | 新設 | `standalone_part(w: str) -> str` | 単独で照合する語 1 つの正規表現の断片を作る | `w` は空でない文字列 | 先頭が漢字なら直前が漢字でない条件、末尾が漢字なら直後が漢字でない条件を付けた `re.escape(w)` を返す。漢字でない側には条件を付けない | 無し |
| `word_pattern` | `plugins/ndf/scripts/lib/glossary_words.py` | 変更 | `word_pattern(words, katakana: bool = False, standalone=()) -> re.Pattern \| None` | 廃止した語と単独で照合する語を合わせた 1 つの正規表現を、長い語から当てる順で作る | `words` と `standalone` は文字列の並び | 2 つの並びの空でない語が 1 つも無ければ `None`。`standalone` の語は `standalone_part`、`words` の語は今の `_word_part` で組む。同じ語が両方にあれば `words` の側で組む（当たる範囲が広い側） | 無し |
| `deprecated_standalone_of` | `plugins/ndf/scripts/glossary.py` | 新設 | `deprecated_standalone_of(t: dict) -> list[str]` | 語の項目の単独で照合する廃止した語の欄を読む | `t` は語の項目の辞書 | 欄が配列なら、空でない文字列の要素だけを元の順で返す。欄が無い・配列でないなら空の並び | 無し |
| `all_deprecated_of` | `plugins/ndf/scripts/glossary.py` | 新設 | `all_deprecated_of(t: dict) -> list[str]` | 語の項目の廃止した語を、2 つの欄を合わせて返す | `t` は語の項目の辞書 | `deprecated_of(t)` の後に `deprecated_standalone_of(t)` を並べ、重なりを除いた並び（先に出た方を残す） | 無し |
| `_standalone_findings` | `plugins/ndf/scripts/glossary.py` | 新設 | `_standalone_findings(i: int, t: dict, live_by_ctx: dict[str, set[str]], hit) -> None` | 単独で照合する廃止した語の欄の形を確かめる（不変条件 I3） | `t` は `_term_shape_problem` を通った項目。`hit` は `_hit_into` が返す関数 | 欄があって形が崩れていれば、崩れごとに `schema` の当たりが `hit` で足されている。欄が無ければ何も足さない | 無し |
| `_meaning_findings` | `plugins/ndf/scripts/glossary.py` | 新設 | `_meaning_findings(g: dict, words: WordPair, hit) -> None` | 語の項目とコンテキストの意味の説明を照らし、廃止した語の出現を 1 件ずつ当てる（不変条件 I1） | `words` は `_build_word_patterns(g)` の 1 つ目 | `words.dep` が `None` なら何も足さない。それ以外は、意味の説明が文字列の項目ごとに、`_uncovered(mask_inline_code(意味), words)` の一致 1 つにつき `deprecated_meaning` の当たりが 1 件足されている | 無し |
| `structure_findings` | `plugins/ndf/scripts/glossary.py` | 変更 | `structure_findings(g: dict, decl: Declaration, words: WordPair \| None = None) -> list[dict]` | 用語集の形・重なり・禁止語と、意味の説明の廃止した語を見る | `g` は `parse_glossary` を通った用語集 | 今の当たりに、`_standalone_findings` と `_meaning_findings` の当たりを足した並びを返す。`words` が `None` なら `_build_word_patterns(g)` で作ってから照らす | 無し |
| `_build_word_patterns` | `plugins/ndf/scripts/glossary.py` | 変更 | `_build_word_patterns(g) -> tuple[WordPair, WordPair]` | 本文の照合と識別子の照合に使う正規表現の組を作る | `g` は用語集 | 本文の組の廃止した形は、`deprecated` の 2 文字以上の語と、単独で照合する語（1 文字を含む）から、どこかのコンテキストの生きた語を除いて作る。識別子の組は変えない | 無し |
| `cmd_check` | `plugins/ndf/scripts/glossary.py` | 変更 | `cmd_check(a) -> None`（`emit` で終わる） | 用語集の構造と、指定の文書の語を確かめて結果を出す | `a` は `check` の引数 | 正規表現の組を `structure_findings` の前に 1 回だけ作り、構造の検査と文書の照合の両方へ渡す。出力と終了コードの形は今と同じ | 用語集・宣言・対象の文書を読む。標準出力へ結果 JSON、標準エラーへ当たりの行を書く（今と同じ） |
| `render_text` | `plugins/ndf/scripts/glossary.py` | 変更 | `render_text(g: dict, source: str) -> str` | 人が読む用語集の文書を作る | 今と同じ | 「廃止した語」の列が、`deprecated` の語の後に単独で照合する語を `<語>（単独）` の形で `、` 区切りで並べる。列の数は 6 のまま | 無し |
| `replacement` | `plugins/ndf/scripts/glossary.py` | 変更 | `replacement(g: dict, word: str) -> str` | 見つけた廃止した語の置き換え先の語を返す | 今と同じ | `word` が `all_deprecated_of` に入る項目の語を ` / ` で並べる。1 つも無ければ `用語集の語` | 無し |
| `diff_items` | `plugins/ndf/scripts/glossary.py` | 変更 | `diff_items(before: dict, after: dict) -> list[dict]` | 2 つの用語集の変化を返す | 今と同じ | 廃止した語の追加の判定に `all_deprecated_of` を使う。単独で照合する語の追加も `change` が `deprecated` の行になる | 無し |

`STRUCTURE_RULES` は関数ではないため「内部のデータと定数」に置く。

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `deprecated_standalone_of` | 欄が配列でない・要素が文字列でないか空 | 用語集の書き誤り | 関数の中の型の判定 | 既定値で続ける（形の合う要素だけを返す。照合はその語だけで続く） | この関数は何も出さない。形の誤りは `_standalone_findings` が `schema` で出す | 後始末は無い。用語集を直して打ち直せる |
| `_standalone_findings` | 欄が配列でない・空の文字列か文字列でない要素を含む | 用語集の書き誤り | 欄の型と、`deprecated_standalone_of` の長さと欄の長さの比較 | 呼び出し元へ返す（`schema` の当たりを足す） | `ERROR: <用語集>:0: schema: <語>` と、`detail` の `terms[<i>] の deprecated_standalone は空でない文字列の配列で書く`。終了コード 1 | 後始末は無い。直して打ち直せる |
| `_standalone_findings` | 先頭も末尾も漢字でない語がある | 単独で照合する条件が働かない語を登録した | `is_kanji_edged` | 呼び出し元へ返す（`schema`） | `detail` は `terms[<i>] の単独で照合する廃止した語「<語>」は先頭か末尾を漢字にする（漢字で始まりも終わりもしない語は deprecated へ置く）`。終了コード 1 | 同上 |
| `_standalone_findings` | 同じコンテキストの生きた語と重なる | 生きた語を廃止した語にも登録した | `live_by_ctx` との突き合わせ | 呼び出し元へ返す（`schema`） | `detail` は `terms[<i>] の単独で照合する廃止した語が同じコンテキストの生きた語と重なる`。終了コード 1 | 同上 |
| `_meaning_findings` | 意味の説明が文字列でない | 用語集の書き誤り | 型の判定 | 既定値で続ける（その項目を照らさない） | 語の項目なら `_term_shape_problem` が `schema` で出す。コンテキストなら出さない（今のコンテキストの形の検査が意味の欄を求めないため） | 後始末は無い |
| `_meaning_findings` | 意味の説明に廃止した語がある | 改名の後に意味の説明を言い換えていない | `_uncovered` の一致 | 呼び出し元へ返す（`deprecated_meaning`） | `ERROR: <用語集>:0: deprecated_meaning: <項目の語>` と、`detail` の `意味の欄に廃止した語「<見つけた語>」がある。<置き換え先> と書く`。終了コード 1 | 後始末は無い。意味の説明を言い換えて `render` し、打ち直せる |
| `structure_findings` | `words` を省いて呼ばれた | `plan-to-spec-steps.py` など、正規表現の組を持たない呼び手 | 引数が `None` | 既定値で続ける（自分で組を作る） | 出力は変わらない | 後始末は無い |
| `cmd_check` | 宣言・用語集・対象の文書が読めない | ファイルが無い・JSON が壊れている | 既存の `load_declaration`・`load_glossary`・`_collect_targets` | 止める（既存と同じ） | `unreadable` の結果 JSON。終了コード 2 | 後始末は無い。直して打ち直せる |
| `cmd_check` | 当たりがある | 構造・意味の説明・文書の語のどれか | 当たりの並びが空でない | 止める（既存と同じ） | 当たりごとに標準エラーへ 1 行、標準出力へ `stopped` の結果 JSON。終了コード 1 | 後始末は無い。直して打ち直せる |

`is_kanji_edged`・`standalone_part`・`word_pattern` は、入力の語を `re.escape` してから組むため、語に正規表現の記号があってもコンパイルに失敗しない。失敗の経路を持たないので表に行を置かない。

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `standalone_part` | 先頭が漢字・末尾が漢字（例: `段`・`字で持`） | `(?<![<KANJI>])<語>(?![<KANJI>])` |
| `standalone_part` | 先頭が漢字・末尾が漢字でない | `(?<![<KANJI>])<語>` |
| `standalone_part` | 先頭が漢字でない・末尾が漢字 | `<語>(?![<KANJI>])` |
| `standalone_part` | 先頭も末尾も漢字でない | `<語>`（形の検査が `schema` で当てるため、照合の側では条件を付けずに組む） |
| `_build_word_patterns` | 語が `deprecated` にだけある・2 文字以上・生きた語でない | `words` の側で組む（今と同じ） |
| `_build_word_patterns` | 語が `deprecated` にだけある・1 文字 | 照合に入れない（今と同じ。形の検査が `schema` で当てる） |
| `_build_word_patterns` | 語が単独で照合する欄にだけある・生きた語でない | `standalone` の側で組む（1 文字を含む） |
| `_build_word_patterns` | 語が `deprecated`（2 文字以上）と単独で照合する欄の両方にある | `words` の側で組む（`word_pattern` の規則） |
| `_build_word_patterns` | 語がどこかのコンテキストの生きた語と同じ | どちらの欄でも照合に入れない |
| `_meaning_findings` | 意味の説明の一致が、生きた語の出現に完全に含まれる | 当てない（`_uncovered`） |
| `_meaning_findings` | 一致がバッククォートの中にある | 当てない（`mask_inline_code` が空白で埋める） |
| `_meaning_findings` | 同じ意味の説明に同じ廃止した語が 2 回ある | 2 件当てる（設計文書の F1 の「1 件ずつ」） |
| `_meaning_findings` | 項目自身の廃止した語が、その項目の意味の説明にある | 当てる（不変条件 I1 は項目を区別しない） |
| `_meaning_findings` | コンテキストの意味の説明に当たる | 当たりの `term` はコンテキストの `id` |

- 繰り返しの終わる条件: `_meaning_findings` は用語集の語の項目とコンテキストを 1 回ずつ通るだけで、項目ごとに正規表現の `finditer` を 1 回回す。用語集の大きさで終わる
- 計算量の注意: 正規表現の組は `cmd_check` で 1 回だけ作り、構造の検査と文書の照合で共有する。ai-plugins の用語集（1,063 項目・20 コンテキスト）で、組の作成と意味の説明の照合を合わせて約 0.28 秒（設計文書の「実測の手順」）。`plan-to-spec-steps.py` から `words` を省いて呼ぶと、そちらでも 1 回作る

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `KANJI` | 定数（正規表現の文字クラスの中身の文字列） | `plugins/ndf/scripts/lib/glossary_words.py` | `"㐀-䶿一-鿿豈-﫿々"`（CJK 統合漢字拡張 A・CJK 統合漢字・CJK 互換漢字・繰り返しの記号）。漢字の範囲の唯一の置き場で、`glossary.py` は持たない |
| `STRUCTURE_RULES` | 定数 | `plugins/ndf/scripts/glossary.py` | `("schema", "duplicate", "stale_document", "deprecated_meaning")`。構造の検査が出す規則の一覧で、規則を足したので並びにも足す |
| `terms[].deprecated_standalone` | 用語集の欄（文字列の配列。省略できる） | `docs/glossary/glossary.json` ほか各プロジェクトの用語集 | 無し（欄が無い項目は空の並びとして読む） |
| 当たりの辞書 | 型（既存の形） | `_hit_into` が作る | `{"rule", "path", "line": 0, "term", "detail"}`。`deprecated_meaning` も同じ形で、新しい項目を足さない |

`STRUCTURE_RULES` は今どこからも読まれていない（`grep -rn STRUCTURE_RULES plugins/ scripts/` の結果は定義の 1 行だけ。2026-11-13）。規則の一覧として正しく保つためだけに足す。

## 並行と排他

省く: `glossary.py check` は用語集と文書を読むだけで書かず、新しく足す関数はどれも引数だけから結果を返す。共有する資源と中断の途中の状態が無い。

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `cmd_check` | `deprecated_meaning` の当たりを、既存の当たりと同じ 1 行（`ERROR: <パス>:0: deprecated_meaning: <項目の語>`）で出す | 標準エラー | 意味の説明の全文（`detail` は見つけた語と置き換え先だけを持つ） |
| `cmd_check` | 当たりの並びと件数（`metrics.hits`）を、規則の名前の一覧つきの要約で出す | 標準出力の結果 JSON（`step_result`） | 秘密は扱わない（用語集はリポジトリの中の公開の文書である） |
| `_meaning_findings`・`_standalone_findings` | 何も出さない。当たりを `hit` で並びに足すだけ | 無し | — |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `is_kanji_edged` | 漢字の範囲や判定を持つ既存のコード | `grep -rn "u4e00\|一-龥\|一-鿿\|\\p{Han}\|CJK" plugins/ndf/scripts --include=*.py` と `grep -rn "漢字\|\[一-" plugins/ndf/scripts --include=*.py`（tests を除く。2026-11-13） | 使えない（どちらも 0 件）。`lib/glossary_words.py` に新設する。PyPI の regex の `\p{Han}` は設計文書の決定 2 で採らない |
| `standalone_part` | `lib/glossary_words.py` の `_word_part` | ファイルを読んだ | 一部を使う。`re.escape` と語ごとの断片を返す形に倣う。`_word_part` は境界を両側に同じ文字の種類で掛けるため、片側だけに条件を付ける形には使えない |
| `deprecated_standalone_of` | `deprecated_of`・`deprecated_code_of` | `grep -n "def deprecated" plugins/ndf/scripts/glossary.py` | 形を倣う。`deprecated_of` は空の文字列を通し、`deprecated_code_of` は識別子の形で絞るため、どちらも「空でない文字列」の条件を持たない |
| `all_deprecated_of` | `deprecated_of` | 同上と、呼び出し元の一覧 `grep -rn "deprecated_of" plugins/ndf/scripts --include=*.py`（`glossary.py` の 5 か所だけ） | 中で使う。呼び出し元のうち `render_text`・`replacement`・`diff_items`・`_build_word_patterns` を `all_deprecated_of` へ移し、`_deprecated_findings` は欄ごとの形の検査なので `deprecated_of` のまま |
| `_standalone_findings` | `_deprecated_findings` | ファイルを読んだ | 生きた語との重なりの判定（`live_by_ctx`）と文面の形を倣う。1 文字を `schema` で当てる今の判定は単独で照合する欄には当てないため、関数を分ける |
| `_meaning_findings` | `_uncovered`・`mask_inline_code`・`_build_word_patterns`・`_hit_into`・`replacement` | ファイルを読み、設計文書の「実測の手順」でこの組み合わせを用語集に当てて 7 件を得た | 使う。照合・除き方・当たりの形・置き換え先はすべて既存の部品で組み、新しく書くのは項目とコンテキストを回すことだけ |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `is_kanji_edged` | `段`・`字で持` は真 | 空の文字列は偽。`々` で終わる語は真。`ステップ`・`で持つ` は偽 | — |
| `standalone_part` | `段` の断片が `第 1 段`・`3 段` に当たり、`段階`・`手段` に当たらない | `字で持` の断片が `字で持ち` に当たり、`文字で持つ`・`数字で持つ` に当たらない。行の先頭と末尾の `段` に当たる | 語に正規表現の記号（`.`・`(`）があってもコンパイルでき、字面だけに当たる |
| `word_pattern` | `words` と `standalone` を合わせた正規表現が、両方の語に当たる | 両方が空なら `None`。同じ語が両方にあれば `words` の側で組まれ、前後が漢字でも当たる | — |
| `deprecated_standalone_of` | 文字列の配列をそのまま返す | 欄が無い項目は空の並び | 配列でない欄は空の並び。空の文字列と文字列でない要素は除く |
| `all_deprecated_of` | `deprecated` の後に単独で照合する語が並ぶ | 両方に同じ語があれば 1 つだけ | — |
| `_standalone_findings` | 正しい欄では当たりが無い | 欄が無い項目では当たりが無い | 配列でない・空の文字列を含む・先頭も末尾も漢字でない語を含む・同じコンテキストの生きた語と重なる、がそれぞれ `schema` の 1 件になる（異常系の処理の 3 行） |
| `_meaning_findings` | 語の項目の意味の説明に別の項目の廃止した語を書くと、項目の語・見つけた語・置き換え先を持つ `deprecated_meaning` が 1 件出る。コンテキストの意味の説明でも出て、`term` は `id` | 単独で照合する語は前後が漢字の出現に当たらない。同じ語の 2 回の出現は 2 件 | どこかのコンテキストの生きた語と同じ廃止した語・生きた語の出現の内側・インラインコードの中は当てない。意味の説明が文字列でないコンテキストは飛ばす（異常系の処理の行） |
| `structure_findings` | `words` を渡すと、渡した組で照らす | `words` を省くと自分で組を作り、同じ当たりを返す | 語の項目だけを回すように壊すとコンテキストのテストが、`deprecated` の欄だけで組を作るように壊すと単独で照合する語のテストが落ちる |
| `_build_word_patterns` | 単独で照合する 1 文字の語が本文の組に入る | 生きた語と同じ単独で照合する語は入らない | — |
| `cmd_check` | `--rules structure` で意味の説明の当たりが出て終了コード 1。言い換えた後は 0 | `--rules all` の `--file` で、単独で照合する語が追加した行の `deprecated` で当たる | 読めない用語集は 2（既存のテストが縛る） |
| `render_text` | 単独で照合する語が「廃止した語」の列に `（単独）` を付けて並ぶ | `deprecated` が無く単独で照合する語だけの項目でも列に出る。列の数は 6 | — |
| `replacement` | 単独で照合する語にも置き換え先の語を返す | `段` を 2 つの項目に登録すると `ステップ / ステージ` | 当たる項目が無ければ `用語集の語` |
| `diff_items` | 単独で照合する語の追加が `change` が `deprecated` の行になる | 既にある語は行にならない | — |

受け入れ条件の粒度の対応は、設計文書の「テスト設計」の表にある。
