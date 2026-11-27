# #2912 の詳細設計: 設計の判断の検査と MVV 判定の表の確かめ

設計文書 [issue-2912-design.md](./issue-2912-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 2 つ以上あり、既存の関数のシグネチャも変える（`patch_material` に行数の引数を足す）ため、別ファイルにした。決定の中身と受け入れ条件の文は、設計文書と要求仕様（[issue-2912-requirements.md](./issue-2912-requirements.md)）を指す。

この文書で使う語の意味は、設計文書のとおりである。

| 語 | 意味 |
| --- | --- |
| レッドライン | 必ず人の承認が要る操作。番号は `C<数>` / `P<数>` / `R<数>` の形をとる |
| 番号の無い委ね方 | レッドラインの番号を示さずに、判断を人に任せる行 |
| 起票者の推定 | 課題の題と本文のうち、利用者の指示の原文に無く、起票者が推して足した条件 |
| MVV 判定 | 承認ゲートの直前に、変更がプロジェクトの MVV（Mission・Vision・Value。プロジェクトが承認した判断基準）に従うかを LLM の判定者に判定させ、承認ゲートを省けるかを決める工程の項目（`ndf records mvv gate`。本体は `mvv_gate.gate`） |
| 判定の回答（`verdict`・`boundary`） | 判定者が返す欄。`verdict` は判定の結論で、`follow`（従う）・`not_follow`（従わない）・`unknown`（判定できない）のどれか。`boundary` は、変更が当たるレッドラインの番号と中身の一覧。この文書の「`follow` で `boundary` が空」は、判定者の回答だけを見れば承認ゲートを省ける回答を指す |
| 設計承認ゲート | 実装の前に、人が設計 PR の設計を承認する場。MVV 判定が省ける結果を出し、進め方が `pace: fast` / `pace: auto` なら、人の承認を待たずに通る |
| 省ける判定 | MVV 判定が設計承認ゲートを省ける結果（出力 `skip` が真）。省く結果だけが承認の対象 `target` を持つ |
| 導入した版 | 利用者と同じ手順で導入した配布物の NDF。置き場はプラグインのキャッシュ（例: `~/.claude/plugins/cache/ai-plugins/ndf/<版>`）で、実験版へ引数 `--plugin-root` で渡す |

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/
│   ├── adapters/markdown.py                     表の行と欄を読む関数を足す
│   ├── core/start_point.py *                    起点の解決（ドキュメント再構成から移す）
│   └── domains/
│       ├── docrebuild/drive.py                  起点の解決を core から呼ぶ
│       └── records/
│           ├── __init__.py                      責務の表に 1 行足す
│           ├── cli.py                           サブコマンド design check
│           ├── design_check.py *                検査する文書を集める（git とファイルを読む口）
│           ├── design_check_decide.py *         番号の無い委ね方と表の行の判定（副作用なし）
│           ├── mvv_gate.py                      承認ゲートを省ける判定のときだけ表を読む
│           └── mvv_material_decide.py           指示文の箇条・差分を切る行数の引数
├── contracts/
│   ├── data/mvv-stop-reasons.json               定型文 table_unreadable
│   ├── templates/design.json                    検査と直しのステップ
│   ├── templates/design-detail.json             検査と直しのステップ・値 base
│   ├── templates/document-review.json *         cross-review に検査を足したプラン
│   ├── workflow/stages/design.json              設計レビューのプランを document-review へ
│   ├── workflow/stages/document-review.json     ドキュメントレビューのプランを document-review へ
│   └── worker/kinds.json                        作業 design-check-fix
├── skills/design/SKILL.md                       手順 5・6 の確かめをコマンドへ
├── skills/design/references/decisions.md        委ねる行の書き方
└── tests/
    ├── adapters/test_markdown.py
    ├── domains/docrebuild/test_docrebuild_run.py
    ├── domains/records/test_design_check.py *
    ├── domains/records/test_mvv_gate.py
    ├── domains/records/test_mvv_material.py
    ├── route/test_flows.py
    └── route/test_templates.py
experimental/ndf/
├── mvv_replay.py *                              過去の PR を判定し直して数える・導入した版のゲートを打つ
└── mvv_replay_cases/ *                          推定の扱いの有無だけが違う作り物の対
```

依存の向き: `design_check_decide` は `ndf.adapters.markdown` だけを読み、git・ファイル・GitHub を読まない。`design_check` は `design_check_decide`・`ndf.core.start_point`・`ndf.adapters.git`・`ndf.core.issue_docs` を読み、`cli` は `design_check` を読む。`mvv_gate` は `design_check_decide` と `ndf.core.issue_docs` を新しく読む。`docrebuild.drive` は `ndf.core.start_point` を読む。`core.start_point` は `ndf.adapters.git` だけを読み、`domains` を読まない。どの向きも `adapters` → `core` → `domains` の一方向で、循環は無い。実験版 `experimental/ndf/mvv_replay.py` は `ndf` を読むが、`ndf` から実験版は読まない。設計文書は型を変えないので「構造」のクラス図を持たず、ここでも描かない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/domains/records/design_check_decide.py` | 新設 | 文書の本文から、番号の無い委ね方の行と「人へ残す判断」の表の行を判定する | `ndf.adapters.markdown` | モジュール `design_check_decide`（`DecisionRow`・`Finding`） | `decision_rows`・`redline_rows`・`findings`・`anchors_lost`・`added_lines`・`heading_touched`・`delegates`・`named`・`numbers_in`・`blank`（新設） |
| `plugins/ndf/src/ndf/domains/records/design_check.py` | 新設 | 検査する文書を引数のファイルか分岐点からの差分で集め、判定を通して結果 JSON を作る | `design_check_decide`・`ndf.core.start_point`・`ndf.adapters.git`・`ndf.core.issue_docs` | モジュール `design_check`（`Target`・`DesignCheckError`） | `from_files`・`from_base`・`check`（新設） |
| `plugins/ndf/src/ndf/domains/records/cli.py` | 変更 | `ndf records design check` の引数を受け、結果を出して `--result` へも書く | `design_check` | モジュール `cli` | `_design_check`・`_add_design`（新設）、`_parser`（変更） |
| `plugins/ndf/src/ndf/domains/records/__init__.py` | 変更 | 責務の表に設計の検査の行を足す | — | パッケージの説明 | — |
| `plugins/ndf/src/ndf/adapters/markdown.py` | 変更 | 表の見出しと行の欄を、markdown-it-py のトークンから読む | markdown-it-py | モジュール `markdown`（`Table`・`TableRow`） | `tables`（新設） |
| `plugins/ndf/src/ndf/core/start_point.py` | 新設 | ベースブランチとリモート名から起点の参照と SHA を決める | `ndf.adapters.git` | モジュール `start_point`（`StartPointError`） | `resolve`（新設） |
| `plugins/ndf/src/ndf/domains/docrebuild/drive.py` | 変更 | 起点の解決を `start_point.resolve` へ任せ、失敗を `Precondition` に包む | `ndf.core.start_point` | モジュール `drive` | `start_and_changes`（変更）、`start_point`・`_resolve`（削除） |
| `plugins/ndf/src/ndf/domains/records/mvv_gate.py` | 変更 | 設計承認ゲートを省ける判定のときだけ、差分の設計成果物の head の表を読む。番号を持つ行があれば、人の承認を求める側（`skip` が偽）へ倒す | `design_check_decide`・`ndf.core.issue_docs` | モジュール `mvv_gate` | `_tabled`・`_table_numbers`（新設）、`_judged`（変更） |
| `plugins/ndf/src/ndf/domains/records/mvv_material_decide.py` | 変更 | 判定者への指示文に、起票者の推定の扱いの箇条を足す。差分を切る行数を引数にする | — | モジュール `mvv_material_decide` | `patch_material`（変更）、定数 `SYSTEM`（変更） |
| `plugins/ndf/contracts/templates/design.json` | 変更 | 2 つの push の前に検査と直しを置く | `ndf records design check`・`ndf route repeat` | プランのテンプレート | — |
| `plugins/ndf/contracts/templates/design-detail.json` | 変更 | push の前に検査と直しを置き、値 `base` を受ける | 同上 | プランのテンプレート | — |
| `plugins/ndf/contracts/templates/document-review.json` | 新設 | レビューの後、push の前に検査を置き、直した版を再レビューする | 同上 | プランのテンプレート | — |
| `plugins/ndf/contracts/workflow/stages/design.json` | 変更 | 設計レビュー `design-review` のプランを `document-review` にする | — | 工程の定義 | — |
| `plugins/ndf/contracts/workflow/stages/document-review.json` | 変更 | ドキュメントレビュー `docs-review` のプランを `document-review` にする | — | 工程の定義 | — |
| `plugins/ndf/contracts/data/mvv-stop-reasons.json` | 変更 | 表の確かめで本文を読めないときの定型文 `table_unreadable` を足す | — | 止まった理由の定型文の表 | — |
| `plugins/ndf/contracts/worker/kinds.json` | 変更 | 検査の指摘を直す作業を足す | — | worker の作業の定義 | — |
| `plugins/ndf/skills/design/SKILL.md` | 変更 | 手順 5・6 の委ね方の確かめを、読む形からコマンドへ変える | — | Skill の本文 | — |
| `plugins/ndf/skills/design/references/decisions.md` | 変更 | 委ねる行の書き方（表の「判断」の文と番号を同じ行に書く）を定める | — | 参照文書 | — |
| `experimental/ndf/mvv_replay.py` | 新設 | 過去の PR を指定の指示文で判定して数える。導入した版のゲートを、書き込みを捕まえて打つ | `ndf`（`--plugin-root` の下か、このリポジトリの `plugins/ndf/src`） | スクリプト | `main`・`judge_once`・`material_of`・`prompt_at`・`gate_once`・`GuardedApi`・`guarded_hub`・`guard_check`（新設） |
| `experimental/ndf/mvv_replay_cases/` | 新設 | 推定の扱いの有無だけが違う作り物の材料の対（要求仕様と設計書を 1 組にした材料を 2 つ） | — | 材料のファイル | — |
| `plugins/ndf/tests/domains/records/test_design_check.py` | 新設 | 設計の検査の判定と集め方を縛る | `design_check_decide`・`design_check` | テスト | — |
| `plugins/ndf/tests/domains/records/test_mvv_gate.py` | 変更 | 表の確かめの振る舞いを縛る | `mvv_gate` | テスト | — |
| `plugins/ndf/tests/domains/records/test_mvv_material.py` | 変更 | `patch_material` の行数の引数を縛る | `mvv_material_decide` | テスト | — |
| `plugins/ndf/tests/adapters/test_markdown.py` | 変更 | `tables` を縛る | `ndf.adapters.markdown` | テスト | — |
| `plugins/ndf/tests/domains/docrebuild/test_docrebuild_run.py` | 変更 | 移した起点の解決を `start_point.resolve` で縛る | `ndf.core.start_point` | テスト | — |
| `plugins/ndf/tests/route/test_flows.py` | 変更 | 3 つのプランの流れを縛る | プランのテンプレート | テスト | — |
| `plugins/ndf/tests/route/test_templates.py` | 変更 | 新しいテンプレートが組めることを縛る | プランのテンプレート | テスト | — |

## 設計の検査の判定ロジック

満たす決定: 決定 1、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `decision_rows` | `design_check_decide.py` | 新設 | `(text: str) -> list[DecisionRow]` | 「人へ残す判断」で始まるセクションの表の行を、「判断」「当たるレッドライン」「なぜ当たるか」の欄に分けて返す |
| `redline_rows` | `design_check_decide.py` | 新設 | `(text: str) -> list[DecisionRow]` | `decision_rows` のうち、番号の欄にレッドラインの番号を 1 つ以上持つ行だけを返す（MVV 判定の表の確かめが使う） |
| `findings` | `design_check_decide.py` | 新設 | `(text: str, path: str, lines: Optional[frozenset[int]] = None) -> list[Finding]` | 見る範囲のセクションで、番号の無い委ね方の行と、欄を欠く表の行を返す。`lines` があれば、その行番号（1 始まり）の行だけを返す |
| `anchors_lost` | `design_check_decide.py` | 新設 | `(before: str, after: str) -> bool` | 分岐点の本文 `before` の表で対応付けに使えた行が、作業ツリーの本文 `after` の表から失われたか |
| `added_lines` | `design_check_decide.py` | 新設 | `(patch: str) -> Optional[frozenset[int]]` | `git diff -U0` の 1 ファイル分の出力から、足した・変えた行の新しい側の行番号を返す。読めない hunk の見出しが 1 つでもあれば None（全行を見る） |
| `heading_touched` | `design_check_decide.py` | 新設 | `(before: str, after: str, patch: str) -> bool` | 差分が見出しの行を足した・変えた・消したか。新しい側の変えた行に `after` の見出しの行があるか、古い側の消した・変えた行に `before` の見出しの行があれば真 |
| `delegates` | `design_check_decide.py` | 新設 | `(line: str) -> bool` | インラインコードを除いた行が委ねる語（設計文書の正規表現）に当たるか |
| `named` | `design_check_decide.py` | 新設 | `(line: str, rows: Sequence[DecisionRow]) -> bool` | 委ねる行 `line` が、表の行 `rows` のどれか 1 行の「判断」の文と、その表の行と同じ番号（打ち消されていないもの）を両方含むか。相手にする表の行は、「判断」と「なぜ当たるか」が `blank` でない行だけ |
| `numbers_in` | `design_check_decide.py` | 新設 | `(text: str, *, negatable: bool) -> tuple[str, ...]` | 文のレッドラインの番号（`C<数>` / `P<数>` / `R<数>`）。`negatable` が真なら、直後に打ち消しが続く番号を除く。インラインコードの中も数える |
| `blank` | `design_check_decide.py` | 新設 | `(cell: str) -> bool` | 空白と記号 `` ` `` `*` `_` `-` `—` を除くと何も残らないか |

- `findings`
  - 事前条件: `text` は改行を含む文書全体。`lines` は `added_lines` の結果か None
  - 事後条件: 返す `Finding` はどれも見る範囲のセクションの中の行で、コードブロックの中の行を含まない。表の行の指摘と地の文の指摘は、同じ行なら 1 件にまとめる
- `anchors_lost`
  - 事後条件: 「対応付けに使える行」（「判断」と「なぜ当たるか」が `blank` でなく番号を持つ行）の（正規化した「判断」, 番号）の組がすべて `after` に残れば偽。行を足しただけ、「なぜ当たるか」を空でない別の文に変えただけなら偽

### 異常系の処理

純粋な関数で、ファイル・git・GitHub を読まない。入力の文字列がどんな形でも例外を投げず、表や見出しが無ければ空の結果を返す。

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `decision_rows` | 欄の見出しが無い表 | 「人へ残す判断」の表が「当たるレッドライン」などの列を持たない | 見出しの行の照合 | 既定値で続ける（欠けた欄を空の文として扱う） | `findings` が番号と理由を欠く行として指摘する（終了コード 1） | 書き直して打ち直せる |
| `added_lines`・`heading_touched` | 読めない hunk の見出し | `@@` の行が `@@ -a[,b] +c[,d] @@` の形でない | 正規表現 `^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@` の照合 | 指摘なしにしない。`added_lines` は None を返し、`from_base` がそのファイルの全行を見る。`heading_touched` は真を返す | 全行を見た結果（指摘があれば終了コード 1） | 再実行できる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `findings` | `lines` があり、行番号が含まれない | 数えない |
| `findings` | 行が見る範囲の外、またはコードブロックの中 | 数えない |
| `findings` | 「人へ残す判断」の表の行で、「判断」が `blank`・番号が無い・「なぜ当たるか」が `blank` のどれか | 指摘する |
| `findings` | `delegates` が真で `named` が偽 | 指摘する |
| `findings` | `delegates` が真で `named` が真 | 指摘しない |

`findings` の行は、表の上から順に当て、最初に当たった行で結果を決める。`lines` の範囲の確認は、表の不備や委ね方を指摘する条件より先に行う。後に置くと、差分の外にある既存の不備まで指摘し、利用者は今回変更していない判断を直すまで PR を送れなくなるためである。ただし `named` が相手にする表の行は、`lines` に依らず文書全体の表から読む（差分の外の表の行も、委ねる行の対応付けの相手になる）。
| `named` | 行の番号のどれかが、`blank` でない「判断」と「なぜ当たるか」を持つ表の行の番号と同じで、その表の行の正規化した「判断」が、正規化した委ねる行に含まれる | 真 |
| `named` | 番号の直後が「に当たらない」「には当たらない」「に該当しない」 | その番号を数えない |

- hunk の見出しの件数: `git diff -U0` は、範囲が 1 行のとき件数を省く（1 行の置き換えは `@@ -a +c @@`、1 行の追加は `@@ -a,0 +c @@`）。省いた件数は 1 として読む。件数が 0 の側は行を返さない
- 見出しの行: `heading_touched` は `atx_headings` の行番号（0 始まり）に 1 を足して、hunk の行番号（1 始まり）と比べる
- 見る範囲: `atx_headings` の見出しのうち、題が「決定の記録」「未確認のまま残ること」「人へ残す判断」で始まるもの。範囲はその見出しから、同じか浅い深さの次の見出しの手前まで
- 正規化: 空白と記号 `` ` `` `*` `_` を除く。`blank` はさらに `-` `—` も除く
- 計算量の注意: 1 行ごとに表の全行と照らすので、行の数 × 表の行の数になる。表は数行なので打ち切りを置かない

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `DecisionRow` | 型（NamedTuple: `line: int`・`decision: str`・`numbers: tuple[str, ...]`・`reason: str`） | `design_check_decide.py` | — |
| `Finding` | 型（NamedTuple: `path: str`・`line: int`・`text: str`） | `design_check_decide.py` | — |
| `SECTIONS` | 定数 | `design_check_decide.py` | `("決定の記録", "未確認のまま残ること", "人へ残す判断")` |
| `TABLE_SECTION` | 定数 | `design_check_decide.py` | `"人へ残す判断"` |
| `COLUMNS` | 定数（「判断」「当たるレッドライン」「なぜ当たるか」の見出し） | `design_check_decide.py` | `("判断", "当たるレッドライン", "なぜ当たるか")` |
| `DELEGATE` | 定数（正規表現。主語・否定・済み・打ち消しの部品から組む） | `design_check_decide.py` | 設計文書の「入出力のコントラクト」の式 |
| `REDLINE_NO` | 定数（正規表現） | `design_check_decide.py` | `(?<![A-Za-z0-9])[CPR][0-9]+(?![0-9])` |
| `NEGATED` | 定数（正規表現。番号の直後の打ち消し） | `design_check_decide.py` | `\s*(?:に当たらない\|には当たらない\|に該当しない)` |
| `RULE` | 定数 | `design_check_decide.py` | `"unnamed_redline"` |

委ねる語・番号・見る範囲の定数は、この 1 か所にだけ置く。MVV 判定の表の確かめも `redline_rows` を呼び、同じ定数を使う。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `decision_rows` | `ndf.adapters.markdown` の `block_kinds`・`atx_headings` | `grep -n "def " plugins/ndf/src/ndf/adapters/markdown.py` | 見出しには `atx_headings` を使う。表の欄を読む関数は無いので `tables` を足す |
| `findings` | `markdown.code_lines`・`markdown.atx_headings` | 同上 | 使う |
| `delegates` | 10 系の `design-survey.py` の `DELEGATE` | `git show ndf--v10.27.53:plugins/ndf/scripts/design-survey.py` | 元にするが、済んだ記録と否定に当たるので式を直す（設計文書の「入出力のコントラクト」） |
| `numbers_in` | `mvv_material_decide._REDLINE_ROW` | `grep -n "_REDLINE_ROW" plugins/ndf/src/ndf/domains/records/mvv_material_decide.py` | 使えない。MVV の表の行頭の番号だけを読み、文中の番号と打ち消しを扱わない |
| `added_lines` | `mvv_material_decide.diff_rows` | `grep -n "def diff_rows" plugins/ndf/src/ndf/domains/records/mvv_material_decide.py` | 使えない。GitHub の files の行の形を作る関数で、行番号を返さない |
| `anchors_lost`・`redline_rows`・`named`・`blank` | — | `grep -rn "人へ残す判断" plugins/ndf/src` | 該当する既存の関数は無い |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `findings` | 設計文書の「行の例」の 2 つの表の行がすべて期待どおりに判定される | 3 つのセクションのどれか 1 つだけに委ねる行がある。コードブロックとインラインコードの中の語。`lines` が空集合。`lines` の外に欄を欠く表の行と番号の無い委ね方の行があり、`lines` の中に正常な表の行と番号を名指しした行がある文書は、指摘 0 件 | 見る範囲の外の委ねる行を指摘しない |
| `decision_rows` | 3 欄の表を読む | 欄が `**` だけ・`—` だけ・`要確認` だけ。番号が `` `C2` `` | 欄の見出しの無い表は欠けた欄として指摘される |
| `redline_rows` | 番号を持つ行だけを返す | 番号の欄が空 | 表が無ければ空 |
| `anchors_lost` | 行を消す・「判断」か番号を書き換える・「なぜ当たるか」を `—` にすると真 | 行を足すだけ、「なぜ当たるか」を別の文にするだけは偽 | 分岐点に表が無ければ偽 |
| `added_lines` | 追加と変更の行番号を返す | 1 行の追加（`@@ -a,0 +c @@`）と 1 行の置き換え（`@@ -a +c @@`）は行 c を返す。削除だけの hunk（`+c,0`）は行を返さない | 形の違う見出しがあれば None |
| `heading_touched` | 見出しを足す・題を変える・消すと真 | 見出しの無い行だけを変えると偽。1 行の置き換えで見出しを変えても真 | 形の違う見出しがあれば真 |
| `delegates`・`named`・`numbers_in` | 設計文書の「行の例」の表 | 「本人」「個人」の「人」、疑問の「か」 | 打ち消した番号・表に無い番号・別の判断の番号を借りた行 |

## Markdown のアダプター

満たす決定: 決定 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `tables` | `plugins/ndf/src/ndf/adapters/markdown.py` | 新設 | `(text: str) -> list[Table]` | 文書の最上位の GFM の表を、見出しの欄と行ごとの欄の文（インラインのままの Markdown）で返す |

- `tables`
  - 事後条件: `Table.line` と `TableRow.line` は 0 始まりの行番号（`Heading.line` と同じ数え方）。欄の文は markdown-it-py の `inline` トークンの `content` で、インラインコードの `` ` `` を残す。引用と箇条の中の表は返さない（`atx_headings` と同じく最上位だけ）

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Table` | 型（NamedTuple: `line: int`・`header: tuple[str, ...]`・`rows: tuple[TableRow, ...]`） | `markdown.py` | — |
| `TableRow` | 型（NamedTuple: `line: int`・`cells: tuple[str, ...]`） | `markdown.py` | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `tables` | `_tokens`（`table` を有効にした markdown-it-py の解析）と `block_kinds` | `grep -n "enable(\"table\")\|def block_kinds" plugins/ndf/src/ndf/adapters/markdown.py` | `_tokens` を使う。`block_kinds` は表の行かを返すだけで欄を返さない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `tables` | 見出しと 2 行の表の欄と行番号 | 空の欄・欄の中のインラインコードと `\|`・欄の数が見出しより少ない行 | コードブロックと引用の中の表を返さない |

## `records` のサブコマンド

満たす決定: 決定 1・2、受け入れ条件 4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `from_files` | `plugins/ndf/src/ndf/domains/records/design_check.py` | 新設 | `(root: Path, paths: Sequence[str]) -> list[Target]` | 指定したファイルを全行の対象として読む |
| `from_base` | `design_check.py` | 新設 | `(root: Path, base: str, remote: str) -> list[Target]` | 起点との分岐点から作業ツリーまでの設計成果物を集め、ファイルごとに見る行を決める |
| `check` | `design_check.py` | 新設 | `(targets: Sequence[Target]) -> dict[str, Any]` | 対象ごとに `findings` を打ち、統一 JSON の結果（ok か `check_failed`）を作る |
| `_design_check` | `plugins/ndf/src/ndf/domains/records/cli.py` | 新設 | `(args: argparse.Namespace, root: Path) -> dict[str, Any]` | 引数の組を確かめて `from_files` か `from_base` を呼び、`--result` があれば同じ結果をそのファイルへも書く |
| `_add_design` | `cli.py` | 新設 | `(sub: Any) -> None` | `design check` の引数（ファイル・`--base`・`--remote`・`--result`）を登録する |

- `from_base`
  - 事前条件: `root` は git の作業ツリーの中。`remote` は呼び手が決める（引数が無ければ `_design_check` が `Git(root).remote()` で `.ndf/worktree.json` の `remote` を読む）
  - 事後条件: 返す `Target` は、名前が `issue_docs.DESIGN_FILE` に当たり、作業ツリーにある（削除していない）ファイルだけ。次のどれかに当たるファイルは `lines` が None（全行）、ほかは `added_lines` の行である
    - 新規と未追跡のファイル
    - `anchors_lost` が真のファイル
    - `heading_touched` が真のファイル
    - `added_lines` が None を返したファイル（読めない hunk の見出しがある）
  - 副作用: git を起動する（下の表）。取得（fetch）はしない
  - 差分の形の固定: 解析する差分の出力は、利用者とプロジェクトの Git の設定に左右されない形に固定する。`git diff` には、既存の `mvv.LOCAL_DIFF` と同じく `--no-color`・`--no-ext-diff`・`--no-textconv`・`--src-prefix=a/`・`--dst-prefix=b/` を必ず付け、`-c core.quotePath=false` で起動する。外部 diff や textconv が働くと、hunk の行番号が作業ツリーの本文と合わなくなり、変えた行が検査から漏れるためである。行番号を取る差分には `--inter-hunk-context=0` も付ける。利用者が `diff.interHunkContext` を設定していると、`-U0` でも近い hunk が 1 つに結合され、hunk の新しい側の範囲に変えていない行が入る。すると今回触れていない委ね方まで検査に掛かり、利用者は変更の外の書き直しを求められる
- `check`
  - 事後条件: 指摘が無ければ `status: ok`（対象 0 件も含む）。あれば `stopped`（`check_failed`）で、`items` は `{"name": "<パス>:<行>", "rule": "unnamed_redline", "text": "<行の文>"}`。`metrics` に `failed_tests` を置かないので、`ndf route repeat` の同じ原因のキーは指摘の中身によらず `["check_failed", []]` になる
- `_design_check`
  - 副作用: `--result` のファイルを書く（親のディレクトリを作る）

`from_base` が起動する git は次の 5 種類で、どれも対象のファイルの数によらず 1 回ずつである。4 は追跡済みの対象が 1 件以上あるときだけ打つ。

| 順 | 起動 | 目的 |
| --- | --- | --- |
| 1 | `start_point.resolve` の `rev-parse`（と、読めなければ `git remote`） | 起点の参照と SHA |
| 2 | `Git.merge_base(<起点>, "HEAD")` | 分岐点 |
| 3 | `git diff --name-status -z --find-renames --no-ext-diff --no-textconv <分岐点> --` と `git ls-files --others --exclude-standard -z` | 足した・変えた・名前を変えたパスと未追跡のパス |
| 4 | `git -c core.quotePath=false diff -U0 --inter-hunk-context=0 --find-renames --no-color --no-ext-diff --no-textconv --src-prefix=a/ --dst-prefix=b/ <分岐点> -- <パス>...` を 1 回。パスには、追跡済みの対象の新しいパスと、名前を変えた対象の前のパスを全部渡す | 対象ごとの、足した・変えた行と、見出しに触れたか |
| 5 | `Git.blobs` に `<分岐点>:<前のパス>` をまとめて 1 回 | `anchors_lost` と `heading_touched` に渡す分岐点の本文 |

4 で前のパスも渡すのは、Git が改名を見つけるのは比べる両側のパスがパスの指定に入っているときだけだからである。新しいパスだけを渡すと、Git は名前を変えたファイルを新規の追加として出し、全行を足した行として返す。その結果、変えていない既存の行まで検査に掛かり、利用者は今回の変更の外の書き直しを求められる。`--find-renames` を明示するのは、利用者の `diff.renames` の設定によらず改名を見つけるためである。

4 の出力は、`diff --git` の行ごとにファイル単位へ分け、各部分を対象に結び付けてから `added_lines` と `heading_touched` に渡す。結び付けを誤った対象は検査から黙って外れるので、パスの読み方と、結び付けられなかったときの扱いを次のように決める。

- 新しいパスは、`diff --git` の行ではなく、部分の中の `rename to <パス>`（改名）か `+++ b/<パス>`（それ以外）から読む。前のパスは `rename from <パス>` から読む。`diff --git` の行は、空白を含むパスで前後のパスの切れ目が分からないためである
- `+++` の行は、パスが空白を含むと、Git がパスの後ろに区切りのタブを 1 つ付ける（例: `+++ b/a b.md<TAB>`）。行の末尾のタブを 1 つ取り除いてから読む。`rename from`・`rename to` の行にはタブが付かない
- `core.quotePath=false` でも、タブ・改行・`"`・`\` などを含むパスは、Git が C 言語の文字列の形で引用する（例: `+++ "b/t\tx.md"`、`rename to "t\tx.md"`）。`"` で始まるパスは、引用を外して `\t` `\n` `\"` `\\` と 8 進数の `\ooo`（バイト列として集め、UTF-8 で読む）を元に戻してから、`b/` を取り除く
- 3 は `-z` で区切るので、パスを引用せずそのまま返す。4 から読んで元に戻したパスを、3 のパスと文字列で照合する
- 次のどれかに当たる対象は、差分が無いとはみなさず、全行を見る。4 の部分に結び付けられなかった対象（モードだけを変えたファイルのように `+++` も `rename to` も持たない部分を含む）。4 の改名の組が 3 と食い違う対象（3 では追加なのに 4 では改名、3 と 4 で前のパスが違う、3 では改名なのに 4 では追加）。後者は、3 が作業ツリー全体を改名の候補にし、4 は渡したパスだけを候補にするため起こりうる。たとえば、設計書を移動しながら別の設計書を書き直すと、4 だけが別のファイルからの改名と組み、本来は全行を見る新しい設計書の行が漏れる。候補をそろえるには 4 にも作業ツリー全体を渡すことになり、設計書以外の差分まで出力するので、食い違った対象だけを全行にする

名前を変えたファイルの検査の範囲は、次のとおりである。

| 改名の形 | 見る行 |
| --- | --- |
| 本文を変えない改名（`R100`。4 の部分は `rename from`・`rename to` だけで hunk を持たない） | 無し。`added_lines` は空の集合を返し、分岐点の本文と作業ツリーの本文が同じなので `anchors_lost` と `heading_touched` も偽になる |
| 名前を変えて一部を直した改名 | 直した行だけ。`anchors_lost` か `heading_touched` が真なら全行 |
| Git が改名と見なさない大きな書き換え（3 で削除と追加に分かれる） | 新しいパスの全行（新規のファイルとして扱う） |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_design_check` | 入力の組の誤り | ファイルと `--base` がどちらも無い・両方ある | 引数の確かめ | 止める | `stopped`（`invalid`）・終了コード 2 | 引数を直して打ち直す |
| `from_files` | ファイルを読めない | 無い・権限・UTF-8 でない | `Path.read_text` の `OSError`・`UnicodeDecodeError` | `DesignCheckError` を送出する | `_design_check` が受けて `stopped`（`precondition`）・終了コード 3 にする。パスを載せる | 書き手が直して打ち直す |
| `from_base` | 起点を決められない | リモート追跡ブランチも手元のブランチも無い・別のリモートしか無い | `start_point.resolve` の `StartPointError` | 受けて `DesignCheckError` に包み直して送出する | 同上（起点の参照の名前を載せる） | 取得してから打ち直す |
| `from_base` | 分岐点を求められない・差分を読めない | 履歴がつながらない・git の失敗 | `GitError` | 受けて `DesignCheckError` に包み直して送出する | 同上 | 再実行できる |
| `from_base` | 作業ツリーのファイルを読めない | 差分に出た後に消えた・権限 | `OSError` | 同上 | 同上 | 再実行できる |
| `check` | 指摘あり | 番号の無い委ね方・欄を欠く表の行 | `findings` | 例外にせず、`stopped`（`check_failed`）の結果の辞書を戻り値で返す | `stopped`（`check_failed`）・終了コード 1 | 文書を直して打ち直す |

`DesignCheckError` を受けて結果に変えるのは `_design_check` だけである。`_design_check` は `from_files`・`from_base` の呼び出しを `try` で囲み、受けた例外の文を `result.stopped(TOOL, "precondition", …)` の説明にして返す。`cli.main` の `result.emit` が終了コード 3 を返す。`cli._run` が今受ける `GitError` は、`from_base` の中で `DesignCheckError` に包んでから送出するので、`cli._run` の「リポジトリを読めない」の経路には届かない。

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_design_check` | 結果 JSON（標準出力と `--result`） | 結果 JSON | 文書の本文の全体（指摘した行の文だけを載せる） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Target` | 型（NamedTuple: `path: str`・`text: str`・`lines: Optional[frozenset[int]]`） | `design_check.py` | — |
| `DesignCheckError` | 型（例外。`kind` は `precondition`） | `design_check.py` | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `from_base` | `docrebuild.drive.start_and_changes`・`diff_paths` | `grep -n "def start_and_changes\|def diff_paths" plugins/ndf/src/ndf/domains/docrebuild/drive.py` | 起点の解決だけを `core.start_point` へ移して使う。`diff_paths` は `HEAD` までの 3 点比較で、未コミットと未追跡を含まないので使わない |
| `from_base` | `Git.merge_base`・`Git.blobs` | `grep -n "def merge_base\|def blobs" plugins/ndf/src/ndf/adapters/git.py` | 使う |
| `from_files` | `issue_docs.DESIGN_FILE` | `cat plugins/ndf/src/ndf/core/issue_docs.py` | `from_base` が名前で絞るときに使う。`from_files` は名前で絞らない |
| `check` | `ndf.contracts.result.result`・`result.stopped` | `grep -n "^def " plugins/ndf/src/ndf/contracts/result.py` | 使う |
| `_design_check` | `ndf.core.route.local_tests._write_result` | `grep -rn "def _write_result" plugins/ndf/src` | 使えない。`route` の内部の関数で、`records` から読むと依存が逆になる。同じ 3 行を `_design_check` に書く |
| `_add_design` | `_add_mvv` などの登録の形 | `grep -n "def _add_" plugins/ndf/src/ndf/domains/records/cli.py` | 同じ形にそろえる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `from_base` | Git のアダプターを差し替えた偽物（`git diff --name-status`・`ls-files`・`diff -U0`・`blobs` の出力を文字列で返す）で、追加・変更・名前の変更と、未コミット・未追跡のファイルを対象にし、削除のファイルを除く | 対象 0 件。表の行を消したファイル・見出しを足したファイル・読めない hunk の見出しを返したファイルが全行になる。表に行を足しただけのファイルは足した行だけ。名前を変えて一部を直したファイルは直した行だけで、本文を変えない改名は 0 行。行番号を取る `diff` は対象が複数でも 1 回で、名前を変えた対象の前のパスも渡し、`--find-renames`・`--no-ext-diff`・`--no-textconv`・`--no-color`・`--inter-hunk-context=0` を含む。空白を含むパスの部分（実際の形の `+++ b/a b.md<TAB>` と `rename to a b.md`）と、引用されたパスの部分（`+++ "b/t\tx.md"`）を正しい対象に結び付ける。4 の部分に結び付けられない対象と、改名の組が 3 と食い違う対象（3 では追加で 4 では改名）は全行になる | 起点が無いと `precondition`（ローカルブランチが無いだけでは失敗しない）。`GitError` で `precondition` |
| `from_files` | 設計成果物でない名前のファイルも全行を見る | 空のファイル | 無いファイルで `precondition` |
| `check` | 指摘なしは `ok` | 対象 0 件で `ok` | 指摘ありは `check_failed` と `items` の 3 つの欄 |
| `_design_check` | `--result` に標準出力と同じ結果を書く | — | 入力が無い・両方ある組で `invalid` |

単体テストでは git を起動しない。`from_base` は起点の解決から差分の取得まで git を何度も起動するので、一時リポジトリで打つと、テストのケースごとに子のプロセスが増えるためである。テストは Git のアダプター（`ndf.adapters.git.Git`）を差し替え、実際の `git` の出力の形は、`added_lines` と `heading_touched` に渡す文字列の例（1 行の追加・置き換え、件数を省いた見出し）で縛る。実際の `git` との接続（未コミット・未追跡・マージ先だけの変更が期待どおりに集まるか）は、実装の後に手元の一時リポジトリで打って確かめ、単体テストに置かない（実装計画の Task 8）。

## 設計・詳細設計のプランのテンプレート

満たす決定: 決定 2、受け入れ条件 4

### 内部のデータと定数

ステップは JSON の設定で、関数を持たない。足すステップとつなぎ直す分岐は次のとおりである。`{state_dir}` は流すときにプランの保存先へ、`{{…}}` は差し込みの値へ置き換わる。

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| ステップ `design-check` | 設定値（`run`: `["ndf", "records", "design", "check", "--result", "{state_dir}/design-check.json", "--base", "{{base}}", "--remote", "{{remote}}"]`、`on_cause`: `{"check_failed": "design-check-repeat"}`、`next`: `push`） | `templates/design.json`・`templates/design-detail.json` | — |
| ステップ `design-check-repeat` | 設定値（`run`: `["ndf", "route", "repeat", "--result", "{state_dir}/design-check.json", "--log", "{state_dir}/design-check-causes.jsonl"]`、`next`: `design-check-fix`） | 同上 | — |
| ステップ `design-check-fix` | 設定値（`run`: `["{{worker}}", "design-check-fix"]`、`next`: `design-check`） | 同上 | — |
| ステップ `design-check-reviewed` と、その `-repeat`・`-fix` | 設定値（上の 3 つと同じ形。結果は同じ `{state_dir}/design-check.json` に上書きし、直しの作業が直前の検査の結果を同じパスで読めるようにする。繰り返しの記録は分けて `{state_dir}/design-check-reviewed-causes.jsonl`。`design-check-reviewed-fix` の `next` は `review-fixed`、`design-check-reviewed` の `next` は `push-reviewed`） | `templates/design.json` | — |
| ステップ `review-fixed` | 設定値（`run`: `["{{review}}"]`、`next`: `design-check-reviewed`。`on_cause` を持たないので、`incomplete` を含め完了しなければプランが止まる） | `templates/design.json` | — |

`review-fixed` の `run` には、`review` のステップが渡す `--findings {state_dir}/rebuild.json` を付けない。`rebuild.json` は組み直しが最初のレビューへ引き継いだ足すべき内容で、最初のレビューがすでに扱っている。直した版にもう一度渡すと、解決済みの組み直しの指摘が再び修正の対象になり、同じ修正のやり直しや、未完了による停止が起きる。直した版のレビューが見るのは、直した版そのものと、そのレビューが新しく出す指摘だけである。`document-review` の `review-fixed` も同じく `["{{review}}"]` で、`--findings` を持たない（もともと最初の `review` も持たない）。
| 値 `base` | 設定値（`params` に足す） | `templates/design-detail.json` | `route.derived_locally` の `default_base` |

`design.json` でつなぎ直す分岐は次のとおりである（設計文書の「処理の流れ」の表）。

| ステップ | 今の行き先 | 新しい行き先 |
| --- | --- | --- |
| `rebuild` の次 | `push` | `design-check` |
| `fix-push` の `next` | `push` | `design-check` |
| `review` の `on_cause` の `incomplete` と次 | `push-reviewed` | `design-check-reviewed` |
| `ci-fix`・`base-sync`・`fix-base`・`fix-push-reviewed` の `next` | `push-reviewed` | `design-check-reviewed` |

`design-detail.json` は、ステップを `work`・`design-check`・`design-check-repeat`・`design-check-fix`・`push`・`fix-push` の順に並べる。行き先は、`design-check` が合格なら `push`、`check_failed` なら `design-check-repeat`、`design-check-fix` の後は `design-check`、`push` が拒まれたら `fix-push`（成功なら `end`）、`fix-push` の後は `design-check` である。`summary` の文も、検査と直しを含む流れに書き直す。

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `design` のプラン | 指摘が無ければ `design` → `rebuild` → `design-check` → `push` → … → `design-check-reviewed` → `push-reviewed` → `conflict-check` → `ci` | 1 回落ちて直すと検査へ戻る。`review` の `incomplete` は `design-check-reviewed` へ進む | 3 回目の `check_failed` で `fix_limit`。`review-fixed` の `incomplete` で止まる。`precondition` は直しへ回らない。`ci-fix`・`base-sync`・`fix-base`・`fix-push`・`fix-push-reviewed` の後に検査を通らずに push すると落ちる。`review-fixed` の `run` が `--findings` を含むと落ちる |
| `design-detail` のプラン | `work` → `design-check` → `push` | `fix-push` の後に検査へ戻る | `base` を渡さずに組むと、`.ndf/project.json` の `branch_strategy` のスプリントの PR のマージ先が入る。その宣言が無ければ `RouteError` |

## 文書レビューのプランのテンプレートと工程の定義

満たす決定: 決定 2、受け入れ条件 4

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| テンプレート `document-review` | 設定値（`purpose`: `document_review`、`params`: `["repo", "worktree", "branch", "remote", "base", "review", "worker"]`） | `templates/document-review.json` | — |
| ステップの並び | 設定値（`review`・`design-check`・`design-check-repeat`・`design-check-fix`・`review-fixed`・`push`・`fix-push` の順。行き先は下の表） | 同上 | — |
| 工程のステップ `design-review` の `with.plan` | 設定値 | `workflow/stages/design.json` | `cross-review` → `document-review` |
| 工程のステップ `docs-review` の `with.plan` | 設定値 | `workflow/stages/document-review.json` | `cross-review` → `document-review` |

`document-review` のステップの行き先は次のとおりである。

| ステップ | 終わり方 | 次に流すステップ |
| --- | --- | --- |
| `review` | 完了・`incomplete` | `design-check` |
| `design-check` | 合格 | `push` |
| `design-check` | `check_failed` | `design-check-repeat` |
| `design-check-repeat` | 上限の内 | `design-check-fix`（上限を超えたら `fix_limit` でプランが止まる） |
| `design-check-fix` | 完了 | `review-fixed` |
| `review-fixed` | 完了 | `design-check`（`incomplete` を含め完了しなければプランが止まる） |
| `push` | 成功 | 終わり（`end`） |
| `push` | 拒まれた | `fix-push` |
| `fix-push` | 完了 | `design-check` |

ドキュメントレビューの工程の最初の項目 `docs-pr`（テンプレート `pr`）と、その前の工程のドキュメント再構成（テンプレート `document-rebuild`）は、検査を打たずに最初のステップで push する。この 2 つは変えない。その版は次の `docs-review` の `design-check` を通り、合格するまで工程は `docs-merge` へ進まない。直した版は上の表のとおり `design-check-fix` → `review-fixed` → `design-check` → `push`（拒まれたら `fix-push` → `design-check`）の順に送る（設計文書の「詳細設計と文書レビューのプラン」）。

コードレビューの工程のステップ（`subject: code`）と、テンプレート `cross-review` は変えない。工程から組むときの `review`・`worker` の差し込みは、今の `cross-review` と同じ経路（`progress.invoke.template_values` の `_fill_review`）で埋まる。`base` は `route.derived_locally` が埋める。

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `document-review` のプラン | `review` → `design-check` → `push` | `review` の `incomplete` は検査へ進む。1 回落ちると直し → `review-fixed` → 検査 | `review-fixed` の `incomplete` で止まる。3 回目の `check_failed` で `fix_limit` |
| 工程の定義 | 設計レビューとドキュメントレビューが `document-review` を組む | ドキュメントレビューの工程で、`docs-review` の検査が落ちたまま止まると `docs-merge` へ進まない | コードレビューの工程が検査を打たない |
| テンプレートの一覧 | `route.templates()` が `document-review` を含み、`model.parse` を通る | — | — |

## MVV 判定のステップ

満たす決定: 決定 7、受け入れ条件 5c・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_tabled` | `plugins/ndf/src/ndf/domains/records/mvv_gate.py` | 新設 | `(j: _Judging, rows: Sequence[Mapping[str, Any]], outcome: dict[str, Any]) -> dict[str, Any]` | 省く結果のときだけ、差分の設計成果物の head の表にレッドラインの番号を持つ行か、読めないファイルがあれば、`skip` を偽にし `target` を取り除いて `why` に理由を足す |
| `_table_numbers` | `mvv_gate.py` | 新設 | `(hub: GitHub, head: str, paths: Sequence[str]) -> dict[str, Optional[tuple[str, ...]]]` | `paths` の本文を `hub.aliased` の 1 回の要求（件数が `github.graphql-batch` を超えた分だけ次の要求）で読み、パスごとに `design_check_decide.redline_rows` の番号を出てくる順に重複なく返す。本文が無いパス（Blob でない・応答に無い）と、本文が切り詰められたパス（`isTruncated` が真）は None にする。GitHub の失敗は `GitHubError` をそのまま送出する |
| `_judged` | `mvv_gate.py` | 変更 | 変えない | 最後の `j.posted_if_current(_bound(j, …))` を `j.posted_if_current(_tabled(j, rows, _bound(j, …)))` にする |

- `_tabled`
  - 事前条件: `outcome` は `_bound` を通した結果。`rows` は `_diff` が返した変えたファイル（GitHub の files の行の形）
  - 事後条件: 入力の `outcome["skip"]` が偽なら、何も読まずに同じ値を返す。真なら、`filename` が `issue_docs.DESIGN_FILE` に当たり `status` が `removed` でない行のパスを、出てくる順に並べ、`_table_numbers` でまとめて読む。番号のあるファイルと読めないファイルが 1 つも無ければ同じ値を返し、あれば `skip` が偽で `target` を持たない値を返す。`verdict` と `holds` は変えない
  - 読み方: 差分の設計成果物をすべて 1 回の要求で読み、パスごとの結果を見る。1 件ずつ読む形にしないのは、1 件目で省かないと決まっても残りを直列に待つことになるためである。すべてを読むので、省かない理由をパスごとに `why` の 1 項目にして、人が承認の場で全部を見られる
  - 副作用: GitHub の GraphQL を 1 回（`aliased`。設計成果物が `github.graphql-batch` 件を超えれば、超えた分ごとに 1 回増える）。省けない判定と、差分に設計成果物が無い判定では 0 回
  - `why` の形: 番号を持つファイル 1 件につき `TABLE_KEPT + "<パス>（<番号>・<番号>）"`、読めないファイル 1 件につき `TABLE_UNREADABLE + "<パス>（<理由>）"` を、差分の順に今の `why` の後ろへ足す。`<理由>` は、本文が無いときは「本文が無い」、`GitHubError` のときは `j.text("table_unreadable", exc)`（`failure_text` の定型文と診断の記録の識別子）である。`GitHubError` は要求の全体の失敗なので、そのときはすべてのパスを読めないファイルとして並べる
- `_table_numbers`
  - 事前条件: `head` は判定した PR の head の SHA（`j.pull.head_sha`）
  - 事後条件: パスごとの選択は `object(expression: "<SHA>:<パス>") { ... on Blob { oid text isTruncated } }` になる（式の文字列は JSON の文字列として引用する）。ブランチ名でなく SHA を渡すので、判定の間に PR が進んでも判定した版を読む
  - 切り詰めの扱い: GitHub は大きな blob の `text` を途中で切って返し、そのとき `isTruncated` を真にする。切られた本文で表を読むと、後ろにある「人へ残す判断」の表の行を見落とし、承認ゲートを省く側へ倒れる。そこで `isTruncated` が真のパスは、`text` が文字列でも読まずに None（本文が無い）として返す。実験版の「本文の読み取り」と同じ扱いである

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_table_numbers` | 本文を読めない | GitHub の失敗・上限・権限 | `aliased` の `GitHubError` | 送出する（`_tabled` が受ける） | 無し（`_tabled` が `why` に載せる） | 再実行できる |
| `_table_numbers` | 本文が無い | パスが blob でない・応答に無い | 応答の `text` が文字列でない | そのパスの値を None にして返す | 無し（`_tabled` が `why` に載せる） | 再実行できる |
| `_table_numbers` | 本文が切り詰められた | blob が GitHub の返す本文の上限を超える | 応答の `isTruncated` が真 | `text` を読まず、そのパスの値を None にして返す（省かない側へ倒す） | 無し（`_tabled` が `why` に「本文が無い」として載せる） | 同じ版では打ち直しても変わらない。人が承認ゲートで見る |
| `_tabled` | 本文を読めないファイルがある | `_table_numbers` がそのパスに None を返すか、`GitHubError` を送出した | 戻り値と `except GitHubError` | 省かない側へ倒して続ける | 出力 `mvv_gate.skip` が偽、`why` に「設計文書の本文を読めないため、『人へ残す判断』の表を確かめられない: <パス>（<理由>）」。`<理由>` は「本文が無い」か `failure_text` の定型文と診断の記録の識別子で、GitHub の例外の文は載せない（全文は手元の診断の記録へ書く）。終了コードは今の `ndf records mvv gate` と同じ | 次の判定で読み直す |
| `_tabled` | 番号を持つ行がある | 設計者が判断を人へ残した | `_table_numbers` のそのパスの値が空でない | 省かない | `skip` が偽、`why` に「設計文書の『人へ残す判断』の表に、レッドラインに当たる判断がある: <パス>（<番号>）」。`target` を持たない | 人の承認を待つ |

承認の対象を読めない場合は `_bound` が先に `skip` を偽にするので、`_tabled` は表を読まずに返す。今の止まり方は変わらない。

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_tabled` | 省かない理由（パスと番号） | 出力 `mvv_gate.why`（`_recorded` が判定の記録のコメントへ載せる） | 表の「判断」と「なぜ当たるか」の文、GitHub の例外の文（`why` には `failure_text` の定型文と診断の記録の識別子だけを載せ、例外の全文は `failure_text` が手元の診断の記録へ書く） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `TABLE_KEPT` | 定数（理由の頭の文） | `mvv_gate.py` | `"設計文書の『人へ残す判断』の表に、レッドラインに当たる判断がある: "` |
| `TABLE_UNREADABLE` | 定数（理由の頭の文） | `mvv_gate.py` | `"設計文書の本文を読めないため、『人へ残す判断』の表を確かめられない: "` |
| 失敗の種類 `table_unreadable` の定型文 | 設定値（`failure_text` が引く `STOP_REASONS` の 1 行） | `plugins/ndf/contracts/data/mvv-stop-reasons.json` の `reasons` | 「設計文書の本文を GitHub から読めない。GitHub への接続と権限を確かめる」 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_table_numbers` | `GitHub.aliased`・`_file_at`・`GitHub.branch_file` | `grep -n "def aliased\|def _file_at\|def branch_file" -A10 plugins/ndf/src/ndf/adapters/github.py` | `aliased` を使う（件数の上限で分ける処理を持つ）。`branch_file` は 1 件を 1 回で読むので使わない。応答の読み方は `_file_at` と同じ形にそろえる |
| `_table_numbers` | `design_check_decide.redline_rows` | 本書の「設計の検査の判定ロジック」 | 使う |
| `_tabled` | `_bound` の `target` の外し方、`_recorded` の失敗時の `kept` | `sed -n '/def _bound/,/^def _target/p' plugins/ndf/src/ndf/domains/records/mvv_gate.py` | 同じ書き方（`target` を除いた辞書に `skip`・`why` を重ねる）にそろえる |
| `_tabled` | `issue_docs.DESIGN_FILE` | `cat plugins/ndf/src/ndf/core/issue_docs.py` | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_tabled`（`gate` を通して見る） | 回答が `follow` で boundary が空でも、表に番号を持つ行があれば `skip` が偽、`target` が無く、`why` にパスと番号。番号の無い表・表の無い設計成果物・差分に設計成果物の無い PR は省ける | 表の行が差分の外にある。番号が `` `C2` ``。同じ行がマージ先にもある（前に人が承認した行）でも省かない。削除した設計成果物は読まない | 本文を読めない（`GitHubError`・`text` が None）と省かない。`text` が文字列でも `isTruncated` が真なら省かず、`why` にそのパスが載る（切られた本文の前半に番号を持つ行が無く、後半にある場合を作る）。設計成果物が 2 件でも GitHub の要求は 1 回。省けない判定では表を読む要求を送らない。承認の対象を読めないときは表を読まない。選択の式が head の SHA であること |

## 起点の解決

満たす決定: 決定 1

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `resolve` | `plugins/ndf/src/ndf/core/start_point.py` | 新設 | `(g: Git, base: str, remote: str) -> tuple[str, str]` | 起点の参照の名前と SHA を返す。規則は今の `drive.start_point` と同じで、リモート追跡ブランチを読めればそれを使う。読めないときは、求めた名前のリモートが登録済みか、リモートが 1 つも無いときだけ手元のブランチを使う |
| `start_and_changes` | `plugins/ndf/src/ndf/domains/docrebuild/drive.py` | 変更 | 変えない | `start_point.resolve` を呼び、`StartPointError` を `Precondition` に包む |

`drive.start_point` と `drive._resolve` は消す。普及のフェーズまで後方互換を求めないので、元の名前は残さない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `resolve` | 起点の参照を読めない | リモート追跡ブランチと手元のブランチがどちらも無い・別の名前のリモートだけがある・`git remote` の失敗 | `Git.commit_or_none`・`Git.run` | `StartPointError` を送出する（文は今の `Precondition` の文と同じ）。`design_check.from_base` は受けて `DesignCheckError` に、`drive.start_and_changes` は受けて `Precondition` に包み直す | どちらの呼び出し元でも `stopped`（`precondition`）・終了コード 3 | 取得してから打ち直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `StartPointError` | 型（例外。`detail` を持つ） | `core/start_point.py` | — |
| `WORKTREE_REMOTE` | 定数（「`.ndf/worktree.json` の remote」の文） | `core/start_point.py` へ移す | `f"{git.WORKTREE_NDF_SETTINGS} の remote"` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `resolve` | `docrebuild.drive.start_point` | `sed -n '/def start_point/,/^def diff_paths/p' plugins/ndf/src/ndf/domains/docrebuild/drive.py` | 中身をそのまま移す |
| `resolve` | `worktree.follow` の参照の探し方 | `grep -n "refs/remotes" plugins/ndf/src/ndf/domains/worktree/follow.py` | 使えない。手元のブランチを先に読み、登録済みのリモートかを確かめない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `resolve` | 今の `test_start_point` の 6 つの場合（呼び先を `start_point.resolve`、例外を `StartPointError` にする） | リモートが 1 つも無い | どちらの参照も読めない・別のリモートだけ |
| `start_and_changes` | 今のテストのまま | — | `StartPointError` が `Precondition` で届く |

## worker の作業の定義

満たす決定: 決定 2

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 作業 `design-check-fix` | 設定値（`work`: `修正`、`skills`: `["design", "document-style"]`、`excerpt`: null、`inputs`: `["branch", "base", "remote"]`、`cwd`: `worktree`、`outputs`: `[]`） | `contracts/worker/kinds.json` | — |
| 作業 `design-check-fix` の `summary` | 設定値 | 同上 | 「プランの保存先（起動指示の state_dir）の `design-check.json` の `items` を読み、design の決定の書き方に従って直し、作業場所でコミットする。検査は打ち直さない。保存先を受け取っていなければ `ndf records design check --base <base> --remote <remote>` を 1 回打って指摘を読む。判断は設計者が決めて決定の記録へ書き直すか、レッドラインに当たるものだけを番号となぜ当たるかを添えて『人へ残す判断』の表へ移す。指摘の無い行は書き換えない」 |

直しの作業は、直前の検査の結果のファイルを読み、検査を自分で打ち直さない。既定の worker（`ndf answer work --state-dir {state_dir}`）はプランの保存先を受け取り、起動指示（`worker.system_prompt`）にもそのパスが載る。どちらの検査のステップも結果を `{state_dir}/design-check.json` へ書くので、作業は同じパスを読めばよい。直した後に合格したかは、プランの次の検査が確かめる。利用者が `worker` を差し替えて保存先を渡さない場合に限り、作業は結果のファイルを読めないので、`ndf records design check --base <base> --remote <remote>` を 1 回打って指摘を読む。

## MVV 判定の指示文

満たす決定: 決定 3、受け入れ条件 5a・5b

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `SYSTEM` | 定数（判定者へ渡す指示文） | `plugins/ndf/src/ndf/domains/records/mvv_material_decide.py` | 設計文書の「MVV 判定の指示文」の変更後の箇条。ほかの箇条と結果の形は変えない |

指示文の変更は、実装の完了の前に、設計文書の決定 6 の確かめの順のうち、次の 2 つに合格してから入れる。

- 順 1: 対照 20 件と、この課題の設計 PR #4583 を、変更後の指示文で判定する。対照は設計文書の決定 4 の表の 20 件で、NDF 10 系（旧版）の判定の記録で、当時の判定がレッドラインを挙げた設計 PR である
- 順 2: 推定の扱いの有無だけが違う材料の対を判定する

合格の条件は設計文書の決定 6 にある。合格しなければ `SYSTEM` を変えずに止まる（実装計画のタスクの順序）。

## 設計の手順書と決定の書き方

満たす決定: 決定 1・2、受け入れ条件 1〜4

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 手順 5 の 2 段落目 | 手順の文 | `plugins/ndf/skills/design/SKILL.md` | 「検査のサブコマンドは無いため…読んで確かめる」の段落を、次の 3 点へ変える。(1) 人へ残す判断は `ndf records design check --base <マージ先のブランチ名>` を打って確かめる。受け入れ条件のファイルや実行の記録は、そのパスを引数に渡す。(2) プランから呼ばれた設計者は打たない。(3) 自前で作る決定に書いた「比べた候補」は、今のとおり読んで確かめる |
| 手順 6 の確かめ | 手順の文 | 同上 | プランを使わずに詳細設計を書いた設計者は、追記の後、push の前に同じコマンドを打つ |
| 「人へ委ねない」の最後の段落 | 書き方の文 | `plugins/ndf/skills/design/references/decisions.md` | 委ねる行は、表の「判断」の欄の文と番号を同じ行に書く。番号の直後に「に当たらない」と書いた番号は数えない。確かめは手順 5 のコマンドが行う |

## MVV 判定の材料の組み立て

満たす決定: 決定 5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `patch_material` | `plugins/ndf/src/ndf/domains/records/mvv_material_decide.py` | 変更 | `(files: Sequence[Mapping[str, Any]], limit: Optional[int] = PATCH_LINES) -> str` | 差分を材料の本文にする。`limit` が None なら切らない。既定は今の 200 行 |

`_cut` と `incomplete_patches` は `PATCH_LINES` のまま変えない。本番の判定は `limit` を渡さず、実験版だけが None を渡す。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `patch_material` | 引数を省くと今と同じ本文 | 201 行の差分を `limit=None` で切らない・`limit=200` で切る | `patch` の無い行は見出しだけ（今のまま） |

## 判定の比較（実験版）

満たす決定: 決定 4・5・6、受け入れ条件 5〜7

実験版は単体テストを持たず、手元で打って確かめる（決定 5 の代償）。既定で動くコード・Skill の本文からは参照しない。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `main` | `experimental/ndf/mvv_replay.py` | 新設 | `(argv: list[str]) -> int` | サブコマンド `material`（PR の材料を 1 回組んで保存する）、`judge`（保存した材料のファイルを指定の指示文で判定して数える）、`gate`（導入した版のゲートを打つ）、`guard-check`（GitHub へ送らずに差し替えの働きを確かめる）を受ける |
| `prompt_at` | `mvv_replay.py` | 新設 | `(repo: Path, commit: Optional[str]) -> str` | 指示文を返す。`commit` があれば `git show <commit>:plugins/ndf/src/ndf/domains/records/mvv_material_decide.py` を読み込んで `SYSTEM` を取り、無ければ読み込んだ `ndf` の `SYSTEM` |
| `material_of` | `mvv_replay.py` | 新設 | `(hub: GitHub, g: Git, number: int) -> Optional[Material]` | 決定 5 の 4 つの順で、PR の材料の全文を組む。読んだベースと head の SHA、全文、全文の SHA-256 を返す。全文を取れなければ None。サブコマンド `material` が結果をファイルへ保存し、`judge` はこのファイルだけを読む（PR から組み直さない） |
| `judge_once` | `mvv_replay.py` | 新設 | `(question: str, material: Material, model: str) -> dict[str, Any]` | `llm.ask` を、`--model` を足した `call` で 1 回呼び、回答と実行の情報（モデル ID・Claude Code のバージョン・指示文のコミット・材料のベースと head の SHA・材料のハッシュ）を返す |
| `gate_once` | `mvv_replay.py` | 新設 | `(root: Path, answer: Literal["llm", "stand-in"], result: Path) -> int` | 導入した版の `mvv_gate.gate` を、`hub` の送る口を `guarded_hub` で差し替え、`NDF_MVV_STATE_DIR` を一時ディレクトリにして呼ぶ。`answer` は引数 `--answer` の値で、`llm` なら判定の呼び出し `ask` を差し替えず導入した版の `llm.ask` で実物の LLM に判定させ（設計文書の決定 6 の順 3 の 1 回目）、`stand-in` なら `follow` で `boundary` が空の回答を返す代役に差し替える（2 回目） |
| `GuardedApi` | `mvv_replay.py` | 新設 | クラス（`GitHub.api` に差し替える、GitHub へ要求を送る口。`request` と `graphql._request` を持つ） | すべての要求を送る前に許可の一覧に照らす。読み取り（REST の `GET`、`mutation` と `subscription` を含まない GraphQL）だけを本物へ送る。確認用 PR へのコメントの投稿だけを捕まえて作り物の応答を返し、ほかの要求はすべて送らずに記録して `Refused` を送出する。要求を送らない属性（`config` など。`GitHub.__post_init__` が読む）は元の口へ委ねる |
| `guarded_hub` | `mvv_replay.py` | 新設 | `(hub: GitHub, pr: int) -> tuple[GitHub, GuardedApi]` | `dataclasses.replace(hub, api=GuardedApi(hub.api, hub.repo, pr))` で、送る口だけを差し替えた `GitHub` を作る。高水準のメソッド（`branch_file`・`visibilities`・`artifact_zip` など）も、どれもこの口を通る |
| `guard_check` | `mvv_replay.py` | 新設 | `(result: Path) -> int` | 送る口の先を、要求が届いたら記録して失敗する作り物にして `GuardedApi` を作り、書き込みの経路を順に通す。GitHub へは何も送らない |

- `gate_once`
  - 事前条件: 環境変数 `NDF_STAGE_INPUT` に工程の入力（`issues`・`worktree`・`mode`・`pace`・`mvv_ref`）がある。同じ版で `guard-check` が合格している
  - 処理: `mvv_gate.gate` の引数 `hub` に、本物の `GitHub` を開いてから `guarded_hub` で送る口を差し替えたものを返す関数を渡す。確認用 PR の番号は、worktree のブランチの開いた PR を読み取りで引いて決め、`GuardedApi` に渡す
  - 事後条件: 結果のファイルに、ゲートの出力・捕まえたコメントの本文と件数・拒んだ要求（方法・パス、GraphQL は操作の種類）と件数・読み込んだ置き場とバージョンがある
  - 副作用: GitHub を読む。書き込まない。判定の記録は一時ディレクトリにだけ書く
- `GuardedApi` の許可の一覧
  - 送る: REST の `GET`。GraphQL は、文の先頭の操作（コメントと空白を除いた最初の語）が `query` か、操作の語が無い `{` で始まる文で、文のどこにも語 `mutation`・`subscription` が無いもの
  - 捕まえる: REST の `POST` で、パスが `/repos/<hub.repo>/issues/<確認用 PR の番号>/comments` に一致するもの。本文を記録し、`{"id": 0, "html_url": "<PR の URL>#capture"}` の形の作り物の応答を返す
  - 拒む: 上に当たらないものすべて。方法が `GET` でない REST、別の番号やリポジトリへのコメント、GraphQL の `mutation`、読めない文。送らずに記録し、`Refused` を送出する。ゲートが `Refused` を受けて別の経路で続けても、拒んだ件数が結果に残る
- `guard_check`
  - 通す経路: `Publisher.comment`（確認用 PR）、別の番号へのコメント、`Publisher` の課題の作成、`PATCH` による本文の書き換え、ラベルの付け替え（`POST .../labels`）、`mutation` を含む GraphQL、`artifact_zip` と同じく `api.request` を直接呼ぶ `GET`、`branch_file` の GraphQL の `query`
  - 合格: 作り物まで届いたのが `GET` と `query` の 2 件だけで、コメントの投稿は捕まり、ほかの書き込みはすべて `Refused` で拒まれた。合格しなければ終了コード 1 で、`gate` を打たない
- `material_of`
  - 表の確かめ: `material_of` は PR ごとに差分の設計成果物の（head の SHA, パス）の組を集めるだけで、本文を読まない。サブコマンド `material` が、対象のすべての PR の組をまとめて、設計文書の決定 6 の順 4 の「本文の読み取り」のとおりに読む（`github.graphql-batch` 件ずつの `aliased`、組ごとの「取得済み」「本文が無い」「未取得」の保存、未取得の組だけの 1 回の打ち直し）。組の結果がそろってから、PR ごとに `design_check_decide.redline_rows` を当て、`material_of` が返した `Material` の `tabled` を `_replace` で埋めてから保存する。指示文に依らないので、材料と一緒に 1 回だけ求める
  - `Material.tabled` の値: 「番号あり」は番号の空でないタプル、「番号なし」は空のタプル、「未確認」は None（組が 1 つでも「本文が無い」か「未取得」）。番号なしと未確認を同じ値にしない
- `main`
  - 集計: 側ごとに、その側の判定が `follow` で `boundary` が空の PR を、設計文書の決定 6 の順 4 の「その側での扱い」の表に当てる。`Material.tabled` が空でないタプルなら省けない（表で止まる件）、None なら未確認、空のタプルなら本文の確認の結果で決める。確認後の件数は「省ける」の PR だけを数え、未確認の件数を併記する。片方の側の結果を、もう片方の側の件数から引かない。未確認の PR が残るときの合否は、設計文書の決定 6 の「未確認の PR があるときの合否」で決める。未確認の PR が、変更後の側ではすべて省けず変更前の側ではすべて省ける場合と、その逆の場合の 2 つで差を求める。2 つの差が同じ結論になるときだけ、その結論で合否を決め、違えば結論を保留する
  - 副作用: `--plugin-root` があれば、`<置き場>/src` を `sys.path` の先頭へ足してから `ndf` を読み込む。無ければこのリポジトリの `plugins/ndf/src` を足す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `material_of` | 全文を取れない | API の応答に差分が無く、手元でも読み直せない | 戻り値 | 既定値で続ける（LLM を呼ばず「材料が欠けた」と記録する） | 結果のファイルの件ごとの行 | 再実行できる |
| `judge_once` | 回答が無い・形が違う | 期限切れ・利用上限・2 回とも形が違う | `llm.ask` の `JudgeAnswer` | 既定値で続ける（読めない回答として分母に残す） | 結果のファイルの件ごとの行 | 再実行できる |
| `main` | 比較に使えない組 | 比べる 2 つの判定でモデル ID か Claude Code のバージョンか材料のハッシュが違う | 結果の集計 | 止める（その組を比較に使わない） | 標準エラーと終了コード 1 | 揃えて打ち直す |
| `gate_once` | 捕まえたコメントが 0 件 | 差し替えが働かない | 捕まえた件数 | 止める（「未確認」と記録する） | 結果のファイルと終了コード 1 | 直して打ち直す |
| `gate_once` | 拒んだ要求が 1 件以上 | ゲートが想定の外の書き込みを試みた・許可の一覧が足りない | 拒んだ件数 | 止める（「未確認」と記録する）。拒んだ要求は GitHub へ届いていない | 結果のファイルと終了コード 1 | 一覧を見直し、`guard-check` から打ち直す |
| `guard_check` | 書き込みが作り物まで届いた・コメントを捕まえない | 差し替えの抜け | 作り物が記録した要求 | 止める | 結果のファイルと終了コード 1 | 直して打ち直す |
| `main` | 材料が前の回と違う | 保存した材料のハッシュか、ベースと head の SHA が前の回と違う | 結果の集計 | その件を前の回と比べない | 標準エラーと結果のファイルの件ごとの行 | 同じ材料のファイルで打ち直す |
| `prompt_at` | 指定のコミットを読めない | コミットが無い | `git show` の失敗 | 止める | 標準エラーと終了コード 2 | 取得してから打ち直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 前の指示文のコミット | 引数（`--prompt-commit`） | 実験版へ渡す。値は設計文書の決定 4 | `c93e690ed` |
| 比べる PR の番号と、対照で挙がるべき番号 | 引数（`--prs`・`--expect`） | 実験版へ渡す。値は設計文書の決定 4・6 | — |
| モデル | 引数（`--model`） | 実験版へ渡す | — |
| 作り物の材料の対 | ファイル | `experimental/ndf/mvv_replay_cases/` | 対は 1 つ。「扱いあり」と「扱いなし」の 2 つの材料で、それぞれ要求仕様と設計書の 2 ファイル（設計書は同じ） |
| `Material` | 型（NamedTuple: `base_sha: str`・`head_sha: str`・`text: str`・`sha256: str`・`tabled: Optional[tuple[str, ...]]`。None は表を確かめられなかった） | `mvv_replay.py` | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `material_of` | `mvv.compare_files`・`mvv_material_decide.patch_material` | `grep -n "def compare_files\|def patch_material" -r plugins/ndf/src/ndf/domains/records` | 使う。`patch_material` には `limit=None` を渡す |
| `judge_once` | `llm.ask` の差し替え口 `call`（型 `Caller`） | `grep -n "def ask" -A8 plugins/ndf/src/ndf/adapters/llm.py` | 使う。`--model` を足す `call` を渡す |
| `gate_once` | `mvv_gate.gate` の引数 `hub`（`OpenHub`）・`ask` と、環境変数 `NDF_MVV_STATE_DIR`（表 locations の `mvv-state`） | `grep -n "def gate(" -A8 plugins/ndf/src/ndf/domains/records/mvv_gate.py`・`grep -n "mvv-state" plugins/ndf/contracts/domains/locations.json` | 使う。書き込みの捕まえと記録の隔離を、ゲートを変えずに引数と環境変数で行う |
| `prompt_at` | `mvv_material_decide.SYSTEM` | `grep -n "^SYSTEM" plugins/ndf/src/ndf/domains/records/mvv_material_decide.py` | 使う。前の指示文はコミットの本文から同じ名前で取る |
| `main` | 10 系の `mvv-gate-replay.py` | 設計文書の「手元で数えた事実」 | 選び方だけを使う。11 系の部品の名前と違うので、コードは写さない |
| `GuardedApi`・`guarded_hub` | `GitHub.rest`・`GitHub.graphql`・`GitHub.artifact_zip` が送る口（`self.api.request`・`self.api.graphql._request`）、`Publisher.comment` の `hub.rest("POST", …)` | `grep -n "self\.api\." plugins/ndf/src/ndf/adapters/github.py`・`grep -n "hub.rest" plugins/ndf/src/ndf/adapters/publish.py` | GitHub への要求はすべてこの 2 つの口を通るので、メソッドごとではなく口で照らす。`GitHub` は凍結した dataclass なので `dataclasses.replace` で `api` だけを替える |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 導入した版の依存ライブラリ | `--plugin-root` の `src` を読み込むとき、依存ライブラリ（markdown-it-py など）はこのリポジトリの venv のものを使う。導入した版の `uv.lock` と版が違う場合に動くかは、設計文書の決定 6 の順 3（リリースの後に、配った版を導入して判定し直す確かめ）を打つまで分からない |
</content>
