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
└── mvv_replay_cases/ *                          起票者の推定の扱いの有無だけが違う作り物の対
```

依存の向きは次のとおりである。`domains` は `core` と `adapters` を読み、`core` は `adapters` だけを読む。逆の向きの参照は無く、循環は無い。

| モジュール | 読む先 |
| --- | --- |
| `design_check_decide` | `ndf.adapters.markdown` だけ。git・ファイル・GitHub を読まない |
| `design_check` | `design_check_decide`・`ndf.core.start_point`・`ndf.adapters.git`・`ndf.core.issue_docs` |
| `cli` | `design_check` |
| `mvv_gate` | `design_check_decide` と `ndf.core.issue_docs` を新しく読む |
| `docrebuild.drive` | `ndf.core.start_point` |
| `core.start_point` | `ndf.adapters.git` だけ。`domains` を読まない |
| 実験版 `experimental/ndf/mvv_replay.py` | `ndf`。`ndf` から実験版は読まない |

設計文書は型を変えないので「構造」のクラス図を持たず、ここでも描かない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/domains/records/design_check_decide.py` | 新設 | 文書の本文から、番号の無い委ね方の行と「人へ残す判断」の表の行を判定する | `ndf.adapters.markdown` | モジュール `design_check_decide`（`DecisionRow`・`Finding`） | `decision_rows`・`redline_rows`・`findings`・`anchors_lost`・`added_lines`・`heading_touched`・`delegates`・`named`・`numbers_in`・`blank`（新設） |
| `plugins/ndf/src/ndf/domains/records/design_check.py` | 新設 | 検査する文書を引数のファイルか分岐点からの差分で集め、判定を通して結果 JSON を作る | `design_check_decide`・`ndf.core.start_point`・`ndf.adapters.git`・`ndf.core.issue_docs` | モジュール `design_check`（`Target`・`DesignCheckError`） | `from_files`・`from_base`・`check`（新設） |
| `plugins/ndf/src/ndf/domains/records/cli.py` | 変更 | `ndf records design check` の引数を受け、結果を出して `--result` へも書く | `design_check` | モジュール `cli` | `_design_check`・`_add_design`（新設）、`_parser`（変更） |
| `plugins/ndf/src/ndf/domains/records/__init__.py` | 変更 | 責務の表に設計の検査の行を足す | — | パッケージの説明 | — |
| `plugins/ndf/src/ndf/adapters/markdown.py` | 変更 | 表の見出しと行の欄を、markdown-it-py のトークンから読む | markdown-it-py | モジュール `markdown`（`Table`・`TableRow`） | `tables`（新設） |
| `plugins/ndf/src/ndf/core/start_point.py` | 新設 | ベースブランチとリモート名から起点の参照と SHA を決める | `ndf.adapters.git` | モジュール `start_point`（`StartPointError`） | `resolve`（新設） |
| `plugins/ndf/src/ndf/domains/docrebuild/drive.py` | 変更 | 起点の解決を `start_point.resolve` へ任せ、失敗を `Precondition` に包む | `ndf.core.start_point` | モジュール `drive` | `start_and_changes`（変更）、`start_point`・`_resolve`（削除） |
| `plugins/ndf/src/ndf/domains/records/mvv_gate.py` | 変更 | 設計承認ゲートを省ける判定のときだけ、差分の設計成果物の head の表を読む。番号を持つ行があれば、人の承認を求める側（`skip` が偽）へ倒す | `design_check_decide`・`ndf.core.issue_docs` | モジュール `mvv_gate` | `_design_paths`・`_table_numbers`（新設）、`_bound`・`_target`・`_judged`（変更） |
| `plugins/ndf/src/ndf/adapters/github.py` | 変更 | マージ先の設定の読み取りに、ほかの選択を同じ要求で載せる | — | モジュール `github` | `GitHub.branch_file_with`（新設） |
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
| `experimental/ndf/mvv_replay.py` | 新設 | 過去の PR を指定の指示文で判定して数える。導入した版のゲートを、書き込みを捕まえて打つ | `ndf`（`--plugin-root` の下か、このリポジトリの `plugins/ndf/src`） | スクリプト | `main`・`judge_once`・`material_of`・`read_pairs`・`review_paths`・`latest_review`・`prompt_at`・`gate_once`・`GuardedApi`・`guarded_hub`・`guard_check`（新設） |
| `experimental/ndf/mvv_replay_cases/` | 新設 | 起票者の推定の扱いの有無だけが違う作り物の材料の対（要求仕様と設計書を 1 組にした材料を 2 つ） | — | 材料のファイル | — |
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
| `named` | 行の番号のどれかが、`blank` でない「判断」と「なぜ当たるか」を持つ表の行の番号と同じで、その表の行の正規化した「判断」が、正規化した委ねる行に含まれる | 真 |
| `named` | 番号の直後が「に当たらない」「には当たらない」「に該当しない」 | その番号を数えない |

`findings` は表の条件を上から順に評価し、最初に一致した条件の結果を返す。`named` は差分の対象外も含め、文書全体の表を参照する。`lines` の範囲の確認を、表の不備や委ね方を指摘する条件より先に置くのは、後に置くと差分の外にある既存の不備まで指摘し、利用者が今回変更していない判断を直すまで PR を送れなくなるためである。

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
  - 差分の形の固定: 解析する差分の出力は、利用者とプロジェクトの Git の設定に左右されない形に固定する。`git diff` は `-c core.quotePath=false` で起動する。引数には、既存の `mvv.LOCAL_DIFF` と同じく `--no-color`・`--no-ext-diff`・`--no-textconv`・`--src-prefix=a/`・`--dst-prefix=b/` を必ず付ける。外部 diff や textconv が働くと、hunk の行番号が作業ツリーの本文と合わなくなり、変えた行が検査から漏れるためである。行番号を取る差分には `--inter-hunk-context=0` も付ける。利用者が `diff.interHunkContext` を設定していると、`-U0` でも近い hunk が 1 つに結合され、hunk の新しい側の範囲に変えていない行が入る。すると今回触れていない委ね方まで検査に掛かり、利用者は変更の外の書き直しを求められる
- `check`
  - 事後条件: 指摘が無ければ `status: ok`（対象 0 件も含む）。あれば `stopped`（`check_failed`）で、`items` は `{"name": "<パス>:<行>", "rule": "unnamed_redline", "text": "<行の文>"}`。`metrics` に `failed_tests` を置かないので、`ndf route repeat` の同じ原因のキーは指摘の中身によらず `["check_failed", []]` になる
- `_design_check`
  - 副作用: `--result` のファイルを書く（親のディレクトリを作る）
  - 上書きの制限: `--result` のファイルが既にあるときは、検査した文書と同じファイルでなく、前の検査の結果（`tool` がこの CLI の結果 JSON）のときだけ上書きする。検査する設計書を保存先に指定して、未コミットの本文を検査結果で消さないためである。プランは同じ `{state_dir}/design-check.json` を打ち直すたびに上書きするので、前の結果の上書きは許す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_design_check` | 入力の組の誤り | ファイルと `--base` がどちらも無い・両方ある | 引数の確かめ | 止める | `stopped`（`invalid`）・終了コード 2 | 引数を直して打ち直す |
| `_design_check` | `--result` へ書けない | 検査した文書と同じファイル・前の検査の結果でない既存のファイル | 書く前の確かめ | 書かずに止める | `stopped`（`invalid`）・終了コード 2 | 保存先を直して打ち直す |
| `from_files` | ファイルを読めない | 無い・権限・UTF-8 でない | `Path.read_text` の `OSError`・`UnicodeDecodeError` | `DesignCheckError` を送出する | `_design_check` が受けて `stopped`（`precondition`）・終了コード 3 にする。パスを載せる | 書き手が直して打ち直す |
| `from_base` | 起点を決められない | リモート追跡ブランチも手元のブランチも無い・別のリモートしか無い | `start_point.resolve` の `StartPointError` | 受けて `DesignCheckError` に包み直して送出する | 同上（起点の参照の名前を載せる） | 取得してから打ち直す |
| `from_base` | 分岐点を求められない・差分を読めない | 履歴がつながらない・git の失敗 | `GitError` | 受けて `DesignCheckError` に包み直して送出する | 同上 | 再実行できる |
| `from_base` | 作業ツリーのファイルを読めない | 差分に出た後に消えた・権限 | `OSError` | 同上 | 同上 | 再実行できる |
| `from_base` | 通常ファイルでない | 差分に出たパスか、その途中の階層がシンボリックリンク | 読む前に、解決したパスが作業ツリーの下の同じパスかを確かめる | リンクをたどらず `DesignCheckError` を送出する | 同上。作業ツリーの外の文書の行を `items[].text` に出さない | リンクを通常ファイルに直して打ち直す |
| `check` | 指摘あり | 番号の無い委ね方・欄を欠く表の行 | `findings` | 例外にせず、`stopped`（`check_failed`）の結果の辞書を戻り値で返す | `stopped`（`check_failed`）・終了コード 1 | 文書を直して打ち直す |

`DesignCheckError` を受けて結果に変えるのは `_design_check` だけである。`_design_check` は `from_files`・`from_base` の呼び出しを `try` で囲み、受けた例外の文を `result.stopped(TOOL, "precondition", …)` の説明にして返す。`cli.main` の `result.emit` が終了コード 3 を返す。`cli._run` が今受ける `GitError` は、`from_base` の中で `DesignCheckError` に包んでから送出するので、`cli._run` の「リポジトリを読めない」の経路には届かない。

### 処理の手順

`from_base` が起動する git は次の 5 種類で、どれも対象のファイルの数によらず 1 回ずつである。4 は追跡済みの対象が 1 件以上あるときだけ打つ。3 と 4 の `git diff` は、上の「差分の形の固定」の引数をすべて付ける。

| 順 | 起動 | 目的 |
| --- | --- | --- |
| 1 | `start_point.resolve` の `rev-parse`（と、読めなければ `git remote`） | 起点の参照と SHA |
| 2 | `Git.merge_base(<起点>, "HEAD")` | 分岐点 |
| 3 | `git -c core.quotePath=false diff --name-status -z --find-renames --no-color --no-ext-diff --no-textconv --src-prefix=a/ --dst-prefix=b/ <分岐点> --` と `git ls-files --others --exclude-standard -z` | 足した・変えた・名前を変えたパスと未追跡のパス |
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

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Target` | 型（NamedTuple: `path: str`・`text: str`・`lines: Optional[frozenset[int]]`） | `design_check.py` | — |
| `DesignCheckError` | 型（例外。`kind` は `precondition`） | `design_check.py` | — |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_design_check` | 結果 JSON（標準出力と `--result`） | 結果 JSON | 文書の本文の全体（指摘した行の文だけを載せる） |

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

単体テストでは git を起動しない。`from_base` は起点の解決から差分の取得まで git を何度も起動するので、一時リポジトリで打つと、テストのケースごとに子のプロセスが増えるためである。テストは Git のアダプター（`ndf.adapters.git.Git`）を差し替える。実際の `git` の出力の形は、`added_lines` と `heading_touched` に渡す文字列の例で縛る。例は、1 行の追加・置き換えと、件数を省いた見出しである。

実際の `git` との接続は、実装の後に手元の一時リポジトリで打って確かめ、単体テストに置かない（実装計画の Task 8）。確かめるのは、未コミットと未追跡のファイルが対象に入り、マージ先だけで進んだ変更が対象から外れるかである。

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
| ステップ `review-fixed` | 設定値（`run` は `review` のステップと同じ `["{{review}}", "--findings", "{state_dir}/rebuild.json"]`、`next`: `design-check-reviewed`。`on_cause` を持たないので、`incomplete` を含め完了しなければプランが止まる） | `templates/design.json` | — |
| 値 `base` | 設定値（`params` に足す） | `templates/design-detail.json` | `route.derived_locally` の `default_base` |

`review-fixed` にも、`review` と同じ `--findings {state_dir}/rebuild.json` を渡す。解決済みの指摘をファイルから除く処理は書かない。直した・見送った指摘を修正担当へ渡さないのは cross-review の側の働きで、その仕組みは次の段落にある。`--findings` を外さないのは、最初のレビューが `incomplete` で終わっても検査へ進む経路があるためである。そのときは、組み直しの指摘の一部が未解決のまま残る。組み直しの指摘は、組み直し（`rebuild`）のステップが `{state_dir}/rebuild.json` に書いた指摘である。外すと、未解決の指摘は `review-fixed` の完了の条件から消え、別の直しが入った版が、組み直しの指摘を確かめないまま承認へ届く。

同じファイルを渡しても、解決済みの指摘は再送されない。`{{review}}` の既定の起動は、どちらのステップでも同じ状態の置き場 `{state_dir}/cross-review`（`route.REVIEW_STATE`）を使う。cross-review は、引き継いだ指摘のうち、その置き場の記録で直した・見送ったと決まっていない件だけを修正担当へ渡す（`journal.pending`）。終わり方も、決まっていない件が残れば `incomplete` にする（`finish_decide._handed`）。このため `review-fixed` が見るのは、直した版と、最初のレビューで未解決のまま残った組み直しの指摘と、新しく出る指摘である。未解決の組み直しの指摘が残れば `review-fixed` は完了せず、プランが止まる。`document-review` の `review-fixed` は、最初の `review` と同じく `["{{review}}"]` で、`--findings` を持たない（組み直しを経ないため）。

`design.json` では、push へ向かう今の分岐をすべて、手前の検査へつなぎ直す。`rebuild` の次と `fix-push` の `next` は `push` から `design-check` へ変える。`review` の `incomplete` とその次、`ci-fix`・`base-sync`・`fix-base`・`fix-push-reviewed` の `next` は、`push-reviewed` から `design-check-reviewed` へ変える。つなぎ直した後は、プランの `push` と `push-reviewed` は、直前の検査に合格した後で実行する。cross-review が各ラウンドで修正を送る前には検査を実行しない（その版は、レビューの後の `design-check-reviewed` でまとめて確かめる）。同じ中身を次の表に並べる（設計文書の「処理の流れ」の、つなぎ直しの表と同じ）。

| ステップ | 今の行き先 | 新しい行き先 |
| --- | --- | --- |
| `rebuild` の次 | `push` | `design-check` |
| `fix-push` の `next` | `push` | `design-check` |
| `review` の `on_cause` の `incomplete` と次 | `push-reviewed` | `design-check-reviewed` |
| `ci-fix`・`base-sync`・`fix-base`・`fix-push-reviewed` の `next` | `push-reviewed` | `design-check-reviewed` |

`design-detail.json` は、ステップを `work`・`design-check`・`design-check-repeat`・`design-check-fix`・`push`・`fix-push` の順に並べる。行き先は次のとおりである。

| ステップ | 終わり方 | 次に流すステップ |
| --- | --- | --- |
| `design-check` | 合格 | `push` |
| `design-check` | `check_failed` | `design-check-repeat` |
| `design-check-fix` | 完了 | `design-check` |
| `push` | 成功 | `end` |
| `push` | 拒まれた | `fix-push` |
| `fix-push` | 完了 | `design-check` |

`summary` の文も、検査と直しを含む流れに書き直す。

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `design` のプラン | 指摘が無ければ `design` → `rebuild` → `design-check` → `push` → … → `design-check-reviewed` → `push-reviewed` → `conflict-check` → `ci` | 1 回落ちて直すと検査へ戻る。`review` の `incomplete` は `design-check-reviewed` へ進む | 3 回目の `check_failed` で `fix_limit`。`review-fixed` の `incomplete` で止まる。`precondition` は直しへ回らない。`ci-fix`・`base-sync`・`fix-base`・`fix-push`・`fix-push-reviewed` の後に検査を通らずに push すると落ちる。`review-fixed` の `run` が `review` と違う（`--findings` を外す・別の状態の置き場を使う）と落ちる。cross-review の終わり方の既存のテストで、同じ状態の置き場に直したと記録した引き継ぎの指摘は修正担当へ渡らず、決まっていない指摘が残れば `incomplete` になることを確かめる（足りなければ足す） |
| `design-detail` のプラン | `work` → `design-check` → `push` | `fix-push` の後に検査へ戻る | `base` を渡さずに組むと、`.ndf/project.json` の `branch_strategy` に書いた、スプリントの PR のマージ先が入る。その宣言が無ければ `RouteError` |

## 文書レビューのプランのテンプレートと工程の定義

この節は、次の 2 つの変更を扱う。

- プランのテンプレート `document-review` を新設する
- 設計の工程とドキュメントレビューの工程が流すレビューのプランを、`cross-review` から `document-review` へ付け替える

MVV 判定（`mvv_gate.py`）の変更は、後の節「MVV 判定のステップ」にある。

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

ドキュメントレビューの工程の最初の項目 `docs-pr`（テンプレート `pr`）と、その前の工程のドキュメント再構成（テンプレート `document-rebuild`）は、検査を打たずに最初のステップで push する。この 2 つは変えない。その版は次の `docs-review` の `design-check` を通り、合格するまで工程は `docs-merge` へ進まない。直した版は、上の表のとおり `design-check-fix` → `review-fixed` → `design-check` → `push` の順に送る。push が拒まれたら `fix-push` → `design-check` へ戻る（設計文書の「詳細設計と文書レビューのプラン」）。

コードレビューの工程のステップ（`subject: code`）と、テンプレート `cross-review` は変えない。工程から組むときの `review`・`worker` の差し込みは、今の `cross-review` と同じ経路（`progress.invoke.template_values` の `_fill_review`）で埋まる。`base` は `route.derived_locally` が埋める。

### 単体テストの観点

| 対象 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `document-review` のプラン | `review` → `design-check` → `push` | `review` の `incomplete` は検査へ進む。1 回落ちると直し → `review-fixed` → 検査 | `review-fixed` の `incomplete` で止まる。3 回目の `check_failed` で `fix_limit` |
| 工程の定義 | 設計レビューとドキュメントレビューが `document-review` を組む | ドキュメントレビューの工程で、`docs-review` の検査が落ちたまま止まると `docs-merge` へ進まない | コードレビューの工程が検査を打たない |
| テンプレートの一覧 | `route.templates()` が `document-review` を含み、`model.parse` を通る | — | — |

## worker の作業の定義

満たす決定: 決定 2

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 作業 `design-check-fix` | 設定値（`work`: `修正`、`skills`: `["design", "document-style"]`、`excerpt`: null、`inputs`: `["branch", "base", "remote"]`、`cwd`: `worktree`、`outputs`: `[]`） | `contracts/worker/kinds.json` | — |
| 作業 `design-check-fix` の `summary` | 設定値 | 同上 | 「プランの保存先（起動指示の state_dir）の `design-check.json` の `items` を読み、design の決定の書き方に従って直し、作業場所でコミットする。検査は打ち直さない。保存先を受け取っていなければ `ndf records design check --base <base> --remote <remote>` を 1 回打って指摘を読む。判断は設計者が決めて決定の記録へ書き直すか、レッドラインに当たるものだけを番号となぜ当たるかを添えて『人へ残す判断』の表へ移す。指摘の無い行は書き換えない」 |

直しの作業は、直前の検査の結果のファイルを読み、検査を自分で打ち直さない。既定の worker（`ndf answer work --state-dir {state_dir}`）はプランの保存先を受け取り、起動指示（`worker.system_prompt`）にもそのパスが載る。どちらの検査のステップも結果を `{state_dir}/design-check.json` へ書くので、作業は同じパスを読めばよい。直した後に合格したかは、プランの次の検査が確かめる。利用者が `worker` を差し替えて保存先を渡さない場合に限り、作業は結果のファイルを読めない。そのときは `ndf records design check --base <base> --remote <remote>` を 1 回打って指摘を読む。

## 設計の手順書と決定の書き方

満たす決定: 決定 1・2、受け入れ条件 1〜4

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 手順 5 の 2 段落目 | 手順の文 | `plugins/ndf/skills/design/SKILL.md` | 「検査のサブコマンドは無いため…読んで確かめる」の段落を、次の 3 点へ変える。(1) 人へ残す判断は `ndf records design check --base <マージ先のブランチ名>` を打って確かめる。受け入れ条件のファイルや実行の記録は、そのパスを引数に渡す。(2) プランから呼ばれた設計者は打たない。(3) 自前で作る決定に書いた「比べた候補」は、今のとおり読んで確かめる |
| 手順 6 の確かめ | 手順の文 | 同上 | プランを使わずに詳細設計を書いた設計者は、追記の後、push の前に同じコマンドを打つ |
| 「人へ委ねない」の最後の段落 | 書き方の文 | `plugins/ndf/skills/design/references/decisions.md` | 委ねる行は、表の「判断」の欄の文と番号を同じ行に書く。番号の直後に「に当たらない」と書いた番号は数えない。確かめは手順 5 のコマンドが行う |

## MVV 判定のステップ

この節は、`mvv_gate.py` の判定に、省ける判定のときだけ表を読む確かめを足す変更を扱う。

満たす決定: 決定 7、受け入れ条件 5c・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_bound` | `plugins/ndf/src/ndf/domains/records/mvv_gate.py` | 変更 | `(j: _Judging, rows: Sequence[Mapping[str, Any]], outcome: dict[str, Any]) -> dict[str, Any]` | 省く結果のときだけ、`_target` で承認の対象と差分の設計成果物の head の表を読む。読めない、表にレッドラインの番号を持つ行がある、本文を読めないファイルがある、のどれかなら `skip` を偽にし、`target` を持たせず `why` に理由を足す |
| `_target` | `mvv_gate.py` | 変更 | `(hub: GitHub, pull: Pull, paths: Sequence[str]) -> tuple[dict[str, Any], dict[str, Optional[tuple[str, ...]]], list[Unread]]` | マージ先の設定と `paths` の head の本文を `hub.branch_file_with` で読み（1 回目の要求はマージ先の設定 1 件と設計成果物を上限−1 件まで、2 回目以降の要求は設計成果物を上限の件数まで読む）、承認の対象、`_table_numbers` の結果、失敗した後続の要求の一覧（`branch_file_with` の 3 つ目の値）を返す。最初の要求の失敗は `GitHubError` をそのまま送出する。後続の要求の失敗は送出せず、一覧に入れて返す |
| `_table_numbers` | `mvv_gate.py` | 新設 | `(got: Mapping[str, Any], paths: Sequence[str]) -> dict[str, Optional[tuple[str, ...]]]` | 応答 `got` のパスごとの本文から、`design_check_decide.redline_rows` の番号を出てくる順に重複なく返す。本文が無いパス（Blob でない・応答に無い）と、本文が切り詰められたパス（`isTruncated` が真）は None にする |
| `_design_paths` | `mvv_gate.py` | 新設 | `(rows: Sequence[Mapping[str, Any]]) -> list[str]` | `filename` が `issue_docs.DESIGN_FILE` に当たり `status` が `removed` でない行のパスを、出てくる順に返す |
| `_judged` | `mvv_gate.py` | 変更 | 変えない | 最後の `j.posted_if_current(_bound(j, …))` を `j.posted_if_current(_bound(j, rows, …))` にする |
| `GitHub.branch_file_with` | `plugins/ndf/src/ndf/adapters/github.py` | 新設 | `(base: str, path: str, selections: dict[str, str]) -> tuple[FileAt, dict[str, Any], list[Unread]]` | `branch_file` と同じ読み取りに、`repository` の下の選択を同じ要求で載せる。返す辞書は `aliased` と同じ形で、読めなかった名前は None になる。3 つ目の値は、失敗した後続の要求ごとの `Unread`（その要求に載せた名前と例外）の一覧である。件数の上限 `github.graphql-batch` には設定も 1 件として数える。1 回目の要求はマージ先の設定 1 件と設計成果物を上限−1 件まで、2 回目以降の要求は設計成果物を上限の件数まで読む。2 回目以降は `aliased` で読む。最初の要求の失敗だけを `GitHubError` として送出する |

- `_bound`
  - 事前条件: `outcome` は `mm.gate_outcome` の結果。`rows` は `_diff` が返した変えたファイル（GitHub の files の行の形）
  - 事後条件: 入力の `outcome["skip"]` が偽なら、何も読まずに同じ値を返す。真なら、`_design_paths` のパスを `_target` でマージ先の設定とまとめて読む。番号のあるファイルと読めないファイルが 1 つも無ければ `target` を持たせて返し、あれば `skip` が偽で `target` を持たない値を返す。`verdict` と `holds` は変えない
  - 読み方: 最初の要求にマージ先の設定 1 件と、残りの枠に収まる設計成果物を載せる。残った設計成果物は上限の件数ごとに分けて読み、パスごとの結果を見る。普通は最初の要求だけで済む。1 件ずつ読む形にしないのは、1 件目で省かないと決まっても残りを直列に待つことになるためである。設定と別の要求にしないのは、省ける判定のたびに GitHub の要求が 1 回増えるためである。すべてを読むので、省かない理由をパスごとに `why` の 1 項目にして、人が承認の場で全部を見られる
  - 副作用: GitHub の GraphQL を 1 回（今もある設定の読み取りと同じ要求）。設定が 1 回目の要求の 1 件を使うので、設計成果物が `github.graphql-batch` より 1 件少ない件数を超えたときだけ増え、超えた分は上限の件数ごとに 1 回増える。省けない判定では 0 回
  - `why` の形: 次の項目を、差分の順に今の `why` の後ろへ足す。番号を持つファイル 1 件につき `TABLE_KEPT + "<パス>（<番号>・<番号>）"`、読めないファイル 1 件につき `TABLE_UNREADABLE + "<パス>（<理由>）"` である。`<理由>` は、本文が無いときは「本文が無い」、GitHub の失敗のときは定型文 `table_unreadable` と診断の記録の識別子である。GitHub の失敗で理由を付ける範囲は、どの要求が失敗したかで次のように分かれる
    - 最初の要求の失敗（`_target` が `GitHubError` を送出した）: 設定と本文を同じ要求で読むので、「マージ先の設定を読めない」の項目に続けて、すべてのパスを読めないファイルとして並べる。診断の記録は `diagnosis` で 1 件だけ書き、設定の項目とパスごとの項目のすべてが同じ識別子を指す。失敗のたびに記録が増えず、利用者には 1 つの障害に 1 つの識別子が見える
    - 後続の要求の失敗（`_target` の 3 つ目の値に `Unread` がある）: 設定と、ほかの要求で読めた本文は使う。`Unread` 1 件ごとに診断の記録を 1 件書き、その `Unread` の `names` のパスをすべて、その記録の識別子で読めないファイルとして並べる。不達・5xx・上限・認証の失敗では、`branch_file_with` が残りの要求を送らずに残りのパスを同じ `Unread` にまとめる。このため、失敗した要求のパスと、送らなかった要求のパスは、同じ 1 件の記録の識別子を指す
- `_target` と `_table_numbers`
  - 事前条件: 本文の選択の SHA は判定した PR の head の SHA（`j.pull.head_sha`）
  - 事後条件: パスごとの選択は `object(expression: "<SHA>:<パス>") { ... on Blob { oid text isTruncated } }` になる（式の文字列は JSON の文字列として引用する）。ブランチ名でなく SHA を渡すので、判定の間に PR が進んでも判定した版を読む
  - 切り詰めの扱い: GitHub は大きな blob の `text` を途中で切って返し、そのとき `isTruncated` を真にする。切られた本文で表を読むと、後ろにある「人へ残す判断」の表の行を見落とし、承認ゲートを省く側へ倒れる。そこで `isTruncated` が真のパスは、`text` が文字列でも読まずに None（本文が無い）として返す。実験版の「本文の読み取り」と同じ扱いである

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_target` | 最初の要求が失敗した（設定と、その要求に載せた本文を読めない） | GitHub の失敗・上限・権限 | `branch_file_with` の `GitHubError` | 送出する（`_bound` が受ける） | 無し（`_bound` が `why` に載せる） | 再実行できる |
| `GitHub.branch_file_with` | 後続の要求が失敗した | 同上 | 後続の `aliased` の `GitHubError` | 送出せず、その要求に載せた名前と例外を `Unread` にして返す。GitHub が要求を受けてその要求の中身を断った失敗（GraphQL の誤りなど）なら、残りの要求は続ける。不達・5xx・上限・認証の失敗（`passing`・`quota`・状態 401 と 403）なら、残りの要求も同じ理由で読めないので送らず、残りの名前を同じ `Unread` にまとめる。要求の数は分けた後続の要求の数を超えず、失敗した要求は打ち直さない | 無し（`_bound` が `Unread` 1 件ごとに診断の記録を 1 件書き、その `Unread` の `names` のパスをすべて、その記録の識別子で読めないファイルとして `why` に載せる。打ち切ったために送らなかった要求のパスも `names` に入るので、失敗した要求のパスと同じ識別子を指す。`Unread` に入らないパスは、読めた本文で表を確かめる） | 再実行できる |
| `_table_numbers` | 本文が無い | パスが blob でない・応答に無い | 応答の `text` が文字列でない | そのパスの値を None にして返す | 無し（`_bound` が `why` に載せる） | 再実行できる |
| `_table_numbers` | 本文が切り詰められた | blob が GitHub の返す本文の上限を超える | 応答の `isTruncated` が真 | `text` を読まず、そのパスの値を None にして返す（省かない側へ倒す） | 無し（`_bound` が `why` に「本文が無い」として載せる） | 同じ版では打ち直しても変わらない。人が承認ゲートで見る |
| `_bound` | 本文を読めないファイルがある | `_table_numbers` がそのパスに None を返すか、`_target` が `GitHubError` を送出したか、そのパスが後続の要求の `Unread` に入った | 戻り値と `except GitHubError` | 省かない側へ倒して続ける | 出力 `mvv_gate.skip` が偽、`why` に「設計文書の本文を読めないため、『人へ残す判断』の表を確かめられない: <パス>（<理由>）」。`<理由>` は「本文が無い」か `failure_text` の定型文と診断の記録の識別子で、GitHub の例外の文は載せない（全文は手元の診断の記録へ書く）。終了コードは今の `ndf records mvv gate` と同じ | 次の判定で読み直す |
| `_bound` | 番号を持つ行がある | 設計者が判断を人へ残した | `_table_numbers` のそのパスの値が空でない | 省かない | `skip` が偽、`why` に「設計文書の『人へ残す判断』の表に、レッドラインに当たる判断がある: <パス>（<番号>）」。`target` を持たない | 人の承認を待つ |

承認の対象を読めない場合は、同じ要求で読む表も読めないので、`_bound` が `skip` を偽にして返す。今の止まり方は変わらない。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `TABLE_KEPT` | 定数（理由の頭の文） | `mvv_gate.py` | `"設計文書の『人へ残す判断』の表に、レッドラインに当たる判断がある: "` |
| `TABLE_UNREADABLE` | 定数（理由の頭の文） | `mvv_gate.py` | `"設計文書の本文を読めないため、『人へ残す判断』の表を確かめられない: "` |
| 失敗の種類 `table_unreadable` の定型文 | 設定値（`failure_text` が引く `STOP_REASONS` の 1 行） | `plugins/ndf/contracts/data/mvv-stop-reasons.json` の `reasons` | 「設計文書の本文を GitHub から読めない。GitHub への接続と権限を確かめる」 |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_bound` | 省かない理由（パスと番号） | 出力 `mvv_gate.why`（`_recorded` が判定の記録のコメントへ載せる） | 表の「判断」と「なぜ当たるか」の文、GitHub の例外の文（`why` には `failure_text` の定型文と診断の記録の識別子だけを載せ、例外の全文は `failure_text` が手元の診断の記録へ書く） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `GitHub.branch_file_with` | `GitHub.aliased`・`_file_at`・`GitHub.branch_file` | `grep -n "def aliased\|def _file_at\|def branch_file" -A10 plugins/ndf/src/ndf/adapters/github.py` | `branch_file` の要求に選択を足す（設定の読み取りと同じ要求にする）。上限を超えた分は `aliased` を使う。応答の読み方は `_file_at` と同じ形にそろえる |
| `_table_numbers` | `design_check_decide.redline_rows` | 本書の「設計の検査の判定ロジック」 | 使う |
| `_bound` | 今の `_bound` の、読めないときの `skip` の倒し方 | `sed -n '/def _bound/,/^def _target/p' plugins/ndf/src/ndf/domains/records/mvv_gate.py` | 同じ書き方（`target` を持たない辞書に `skip`・`why` を重ねる）にそろえる |
| `_design_paths` | `issue_docs.DESIGN_FILE` | `cat plugins/ndf/src/ndf/core/issue_docs.py` | 使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_bound`（`gate` を通して見る） | 回答が `follow` で boundary が空でも、表に番号を持つ行があれば `skip` が偽、`target` が無く、`why` にパスと番号。番号の無い表・表の無い設計成果物・差分に設計成果物の無い PR は省ける | 表の行が差分の外にある。番号が `` `C2` ``。同じ行がマージ先にもある（前に人が承認した行）でも省かない。削除した設計成果物は読まない | 本文を読めない（`GitHubError`・`text` が None）と省かない。`text` が文字列でも `isTruncated` が真なら省かず、`why` にそのパスが載る（切られた本文の前半に番号を持つ行が無く、後半にある場合を作る）。設計成果物が 2 件でも、マージ先の設定と合わせて GitHub の要求は 1 回。省けない判定では表を読む要求を送らない。要求が失敗すると、設定と設計成果物ごとの理由が `why` に載る。選択の式が head の SHA であること |

## MVV 判定の指示文

満たす決定: 決定 3、受け入れ条件 5a・5b

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `SYSTEM` | 定数（判定者へ渡す指示文） | `plugins/ndf/src/ndf/domains/records/mvv_material_decide.py` | 設計文書の「MVV 判定の指示文」の変更後の箇条。ほかの箇条と結果の形は変えない |

指示文の変更は、実装の完了の前に、設計文書の決定 6 の確かめの順のうち、次の 2 つに合格してから入れる。

- 順 1: 対照 20 件と、この課題の設計 PR #4583 を、変更後の指示文で判定する。対照は設計文書の決定 4 の表の 20 件で、NDF 10 系（旧版）の判定の記録で、当時の判定がレッドラインを挙げた設計 PR である
- 順 2: 起票者の推定の扱いの有無だけが違う材料の対を判定する

合格の条件は設計文書の決定 6 にある。合格しなければ `SYSTEM` を変えずに止まる（実装計画のタスクの順序）。

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
| `main` | `experimental/ndf/mvv_replay.py` | 新設 | `(argv: list[str]) -> int` | サブコマンド `material`（PR の材料を 1 回組んで保存する）、`judge`（保存した材料のファイルを指定の指示文で判定し、PR ごとの判定結果をファイルへ保存する。順 1・2 の `--expect` の照合もここで行う）、`bodies`（保存した判定結果から本文の確認に当てる PR を選び、確認に使う本文を読んで保存する）、`tally`（保存した判定結果・材料・本文の確認の結果だけから順 4 の 3 通りの値を集計する。LLM も GitHub も呼ばない）、`gate`（導入した版のゲートを打つ）、`guard-check`（GitHub へ送らずに差し替えの働きを確かめる）を受ける |
| `read_pairs` | `mvv_replay.py` | 新設 | `(hub: GitHub, pairs: Sequence[tuple[str, str]], store: Path) -> dict[tuple[str, str], Optional[str]]` | （head の SHA, パス）の組の本文を、設計文書の決定 6 の「本文の読み取り」のとおりに読む。組の一覧を `github.graphql-batch` 件ずつに分け、1 つの分につき `aliased` を 1 回呼ぶ。組ごとの結果（「取得済み」と本文・「本文が無い」・「未取得」）と、その組を読みに行った回数を `store` へ書く。`store` にある「取得済み」と「本文が無い」の組、および読みに行った回数が 2 の「未取得」の組は読まない。返す値は「取得済み」なら本文、ほかは None |
| `review_paths` | `mvv_replay.py` | 新設 | `(hub: GitHub, prs: Sequence[ReviewTarget]) -> dict[int, ReviewPaths]` | 本文の確認で読む組を PR ごとに返す。PR が足したか変えたファイルすべてと、同じ課題の番号の要求仕様・設計書・詳細設計書（その PR で変えていないものも含む）である。あわせて、PR ごとに `issues/` の一覧を読めたかを返す |
| `latest_review` | `mvv_replay.py` | 新設 | `(records: Sequence[Mapping[str, Any]], material: Material, number: int) -> Optional[Mapping[str, Any]]` | 本文の確認の結果から、PR 番号・head の SHA・PR 本文の SHA-256 が材料と一致する記録のうち、時刻が最も新しいものを返す。無ければ None。`bodies` と `tally` の両方がこの関数で記録を選ぶ |
| `prompt_at` | `mvv_replay.py` | 新設 | `(repo: Path, commit: Optional[str]) -> str` | 指示文を返す。`commit` があれば `git show <commit>:plugins/ndf/src/ndf/domains/records/mvv_material_decide.py` を読み込んで `SYSTEM` を取り、無ければ読み込んだ `ndf` の `SYSTEM` |
| `material_of` | `mvv_replay.py` | 新設 | `(hub: GitHub, g: Git, number: int) -> Optional[Material]` | 決定 5 の 4 つの順で、PR の材料の全文を組む。読んだベースと head の SHA、全文、全文の SHA-256 を返す。あわせて、SHA を読んだ PR の応答にある PR 本文と、その SHA-256 を返す（要求は増えない）。全文を取れなければ None。サブコマンド `material` が結果をファイルへ保存し、`judge` はこのファイルだけを読む（PR から組み直さない） |
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
  - 通す経路: 次の 8 つ
    - `Publisher.comment`（確認用 PR）
    - 別の番号へのコメント
    - `Publisher` の課題の作成
    - `PATCH` による本文の書き換え
    - ラベルの付け替え（`POST .../labels`）
    - `mutation` を含む GraphQL
    - `artifact_zip` と同じく `api.request` を直接呼ぶ `GET`
    - `branch_file` の GraphQL の `query`
  - 合格: 作り物まで届いたのが `GET` と `query` の 2 件だけで、コメントの投稿は捕まり、ほかの書き込みはすべて `Refused` で拒まれた。合格しなければ終了コード 1 で、`gate` を打たない
- `material_of`
  - 表の確かめ: `material_of` は PR ごとに差分の設計成果物の（head の SHA, パス）の組を集めるだけで、本文を読まない。サブコマンド `material` が、対象のすべての PR の組をまとめて `read_pairs` で読む。組の結果がそろってから、PR ごとに `design_check_decide.redline_rows` を当てる。その結果で、`material_of` が返した `Material` の `tabled` を `_replace` で埋めてから保存する。指示文に依らないので、材料と一緒に 1 回だけ求める
  - `Material.tabled` の値: 「番号あり」は番号の空でないタプル、「番号なし」は空のタプル、「未確認」は None（組が 1 つでも「本文が無い」か「未取得」）。番号なしと未確認を同じ値にしない
  - 材料に載せられない件: 材料を組むときに読んだ変えたファイルの一覧（`compare_files` の行）に、本番のゲートと同じ `mvv_material_decide.incomplete_patches` を当てる。結果は `Material.incomplete` に入れて、材料と一緒に保存する。ファイルごとの理由は、`patch` が無い（`changes` が 0 でない）か、行数が `PATCH_LINES` を超えるかで分ける。材料を組む 1 回の中で求めるので、判定も GitHub への要求も増えない。受け入れ条件 7 の値の横に記録する件数と、経路全体の値の試算に使い、合否には使わない
  - PR 本文: 本文の確認では、設計文書の決定 6 のとおり PR 本文もファイルの本文とあわせて読む。そこで `material` は、ベースと head の SHA を読んだ PR の応答の `body` を `Material.pr_body` に、その SHA-256 を `Material.pr_body_sha256` に入れて保存する。`body` が null（説明の無い PR）なら空の文字列とする。応答に `body` の項目が無ければ、どちらも None にする。材料と同じ時点の本文を残すので、後から PR 本文が書き換えられても確認の対象は変わらない
  - 保存の権限: 実験版が書くファイル（材料・本文の置き場・判定結果・本文の確認に使う本文・集計）は、どれも所有者だけが読み書きできる権限（0600）で作る。既にあるファイルも、書く前に 0600 にする。認証して取得した PR の本文・差分・ファイルの全文が入り、umask に任せると、非公開リポジトリを読む権限の無い同じホストの利用者が読めるためである
- `read_pairs`
  - 事後条件: `store` には、渡した組のすべてについて「取得済み」「本文が無い」「未取得」のどれかと、その組を読みに行った回数（1 か 2）が書かれている。「本文が無い」は応答の `text` が null か `isTruncated` が真の組で、打ち直しても変わらないので読み直さない。「未取得」は、その組を載せた要求が `GitHubError` で失敗した組である
  - 打ち直し: 1 つの組を読みに行くのは、最初の 1 回と打ち直しの 1 回の、計 2 回までである。同じ `store` で呼び直すと、「未取得」で読みに行った回数が 1 の組だけを読み、回数を 2 にする。回数が 2 の「未取得」の組は、最終の結果として「未取得」のままにし、3 回目は読みに行かない。回数を `store` に残すので、別の起動から呼び直しても上限を超えない
  - 分け方: 自分で `github.graphql-batch` 件ずつに分けてから `aliased` を呼ぶ。`aliased` は途中の要求が失敗すると、それまでに取れた本文も返さないためである。`aliased` の中でも同じ件数で分けるので、1 回の呼び出しは 1 回の要求になる
- `review_paths`
  - 処理: 課題の番号は、PR の差分の設計成果物のパスから `issue_docs.design_issues` で取る。PR ごとに head の `issues/` の一覧を `object(expression: "<head の SHA>:issues") { ... on Tree { entries { name } } }` で読む（`aliased` で PR をまとめて読む）。`entries.name` は `issues/` の中のファイル名だけなので、先頭に `issues/` を付けたリポジトリ相対のパスにしてから照合する。`DESIGN_FILE` は `^issues/` から始まるパスにしか一致しないためである。そのパスから、`issues/issue-<番号>-requirements.md` と、`DESIGN_FILE` に当たり `design_issues` の番号が重なるパスを選ぶ。本文を読む組にも、同じリポジトリ相対のパスを使う。PR が足したか変えたファイル（`status` が `removed` でない行）と合わせ、重複を除く
  - 戻り値: PR ごとに `ReviewPaths` を返す。`pairs` は読む（head の SHA, パス）の組で、`listed` は `issues/` の一覧を読めたかである。一覧を読めなかった PR は、`listed` を偽にし、`pairs` には PR が変えたファイルだけを入れる。こうすると `bodies` は、同じ課題の文書がもともと無い PR（`listed` が真で、足した組が無い）と、一覧を読めなかった PR を区別できる
  - 使い方: サブコマンド `bodies` は、引数 `--judged` で両方の側の保存した判定結果を受け、本文の確認に当てる PR を選ぶ。選ぶのは、どちらかの側で `follow` で `boundary` が空になり、表の確かめが「番号なし」の PR である。判定は呼び直さない。次の順で処理する
    1. 判定結果の照合: 判定結果の PR ごとの材料のハッシュ・ベースの SHA・head の SHA を、今回の材料のファイルの `Material.sha256`・`base_sha`・`head_sha` と照らす。1 件でも合わなければ止める（下の異常系の表）。別の材料で判定した結果から PR を選ばないためである
    2. 確認済みの PR を除く: 引数 `--reviewed` に前の回までの本文の確認の結果を渡すと、PR ごとに下の「有効な記録の選び方」の `latest_review` で記録を 1 件選ぶ。選んだ記録が「含まない」か「含む」の PR を、`issues/` の一覧を読む前に外す。設計文書の決定 6 のとおり、本文の確認の結果は再測定のすべての回で使い回すためである。外した PR は一覧も本文も読まず、確認担当へも渡さない。選んだ記録が「判断できない」の PR、印の付いた「含まない」の PR、材料と一致する記録が無い PR は外さない
    3. 残った PR の `ReviewTarget` を保存した材料のファイルから作り、組を `review_paths` で集め、`material` と同じ `store` を渡して `read_pairs` で読む。表の確かめで取得済みの組は読み直さない
  - `bodies` の出力: PR ごとに、PR 番号・head の SHA・保存した PR 本文とその SHA-256・組ごとの本文と状態・`listed` を、確認担当（リリース後テストの担当）が読むファイルへ書く。次のどれかに当たる PR には「読めていない本文がある」の印を付け、その理由も書く。`listed` が偽、組が 1 つでも「本文が無い」か「未取得」、`Material.pr_body` が None。印の付いた PR も、読めた分の本文は書く
  - 本文の確認: 承認が必要な操作を含むかの判断は、確認担当が `bodies` の出力を読んで行い、実験版は判断しない。確認担当は、PR ごとに「含まない」「含む」「判断できない」と根拠を、本文の確認の結果のファイルへ書く。「含む」なら、当たる承認必須の操作の番号（`C7` など）も書く。各 PR には、`bodies` の出力からその PR の head の SHA・PR 本文の SHA-256・印の有無を写す。印の付いた PR の書き方は次のとおりである
    - 読めた本文の中で承認必須の操作を特定できたら、「含む」と書く。読めていない本文があっても、見つけた操作は見落としとして数えるためである
    - 「含まない」とは書かず、「判断できない」と書く。読めていない本文に承認必須の操作があるかもしれないためである
    - `tally` も同じ規則で取り込む。印の付いた PR の「含まない」は採らず、未確認に数える
- `main`
  - 順 4 の流れ: 判定の LLM を呼ぶのは `judge` だけである。測定 1 回につき、指示文ごとに `judge` を 1 回起動する。`judge` は、材料のある PR ごとに 1 回ずつ判定を依頼する（12 件をまとめて 1 回で判定しない）。対象 12 件の材料がそろっていれば、1 回の測定で両方の指示文を合わせて 24 件を判定する。後の工程は、保存した判定結果を引数 `--judged` で受け渡し、判定を呼び直さない
    1. `material` で対象 12 件の材料を組んで保存する（表の確かめと PR 本文を含む）
    2. `judge` を両方の側の指示文で打ち、それぞれの判定結果をファイルへ保存する
    3. `bodies --judged <変更後の判定結果> <c93e690ed の判定結果> [--reviewed <前の回までの本文の確認の結果>]` で本文の確認に使う本文を保存する
    4. 確認担当が本文の確認の結果のファイルを書く。前の回までの結果に、この回の結果を書き足す
    5. `tally --judged <変更後の判定結果> <c93e690ed の判定結果> --reviewed <本文の確認の結果>` で 3 通りの値と合否を出す。合否を出し直すときも `tally` だけを打ち直す
    6. 1 回目の `tally` が理由「再測定が要る」で保留を出したときだけ、2〜5 を 2 回くり返す。くり返す回の `judge` には、前の回と別の新しい `--result` のパスを渡す（欠けた行を補う起動と区別し、新しい実行の識別子で独立に判定させる）。確認後の件数の差が 1〜2 件に見えても、理由が「未確認が残る」の保留なら再測定せず、未確認の PR を解消してから（`material` の打ち直しや本文の確認）1 回目の `tally` を打ち直す。再測定は判定を 1 回につき 24 件増やすので、未確認を解消すれば決まる結論のために打たない。最後の `tally` には、3 回分の判定結果を回の順に `--judged` で 3 回渡す（`--judged <1 回目の変更後> <1 回目の c93e690ed> --judged <2 回目の…> …`）
  - 回をまたぐ合否（`tally`）: `--judged` の 1 組を 1 回の測定とする。回ごとに、下の「未確認が残るときの値」で差の範囲（下限と上限）と見落としの可能性を求め、「承認必須の操作の見落とし」を当てる。そのうえで、全回の結果から次の順で最終の合否を 1 つ出す。保留には理由（「判定結果か材料が欠けた PR がある」「再測定が要る」「未確認が残る」「再測定でも差が残らない」のどれか）を付け、回ごとの差の範囲と結論も結果のファイルに残す
    1. どれかの回で「承認必須の操作の見落とし」に当たれば、不合格
    2. どれかの回で、材料が欠けた PR か、どちらかの側の判定結果が無い・判定に失敗した PR があれば、保留（理由「判定結果か材料が欠けた PR がある」）。欠けた判定を省けない回答と同じに数えると、比べる前の側の判定だけが欠けた測定でも差が出て合格になり得るためである。欠けた PR は結果のファイルの回ごとの `gaps` に載せる。材料が欠けた PR は `material` を打ち直す。判定が欠けた・失敗した PR は、同じ `--result` を渡して `judge` を打ち直す。`judge` は、同じ判定の条件（モデル・Claude Code の版・指示文のコミット・送る指示文の SHA-256）の結果ファイルを同じ測定の続きとして扱い、実行の識別子を引き継ぐ。指示文のコミットだけでは、作業ツリーで書き換えた指示文や Git の外の置き場の指示文を見分けられないため、送る指示文のハッシュも条件に入れる。`judge` は、識別子を判定の前に決めて保存し、判定 1 件ごとに結果と試行の回数を一時ファイルからの置き換えで保存する。判定を呼ぶ前に試行の回数を保存するので、途中で止まった回も上限に数える。今の材料で有効な回答を得た行は打ち直さず、欠けた行と失敗した行だけを打つ。成功した行まで打ち直すと、1 件の失敗を補うたびに残りの全件の判定の費用を払うためである。同じ材料で `JUDGE_TRIES`（2 回）まで打っても回答が無い PR は打ち直さず、保留の理由に残る。そのうえで `tally` を打ち直す
    3. どれかの回で、変更後の側に見落としの可能性が残れば、差の範囲にかかわらず保留（理由「未確認が残る」）
    4. 1 回目の差の範囲を、設計文書の決定 6 の「判定の揺れの扱い」の表に当てる。下限が 3 件以上なら合格、上限が 0 件以下なら不合格で、2 回目以降が渡されても合否に使わない。下限と上限がどちらも 1〜2 件なら、次へ進む。下限と上限が表の別の行に当たれば、保留（理由「未確認が残る」）
    5. 渡された回が 3 回に満たなければ、保留（理由「再測定が要る」）
    6. 2 回目と 3 回目は表に当てず、差の下限が 1 件以上かだけを見る。どちらも 1 件以上なら合格、どちらかが 0 件以下なら保留（理由「再測定でも差が残らない」）。ほかの回で差が 3 件以上でも合格にしない
  - 有効な記録の選び方（`bodies`・`tally` で共通）: 本文の確認の結果は追記だけで書き、1 件ずつに記録した時刻を付ける。`bodies` と `tally` は同じ関数 `latest_review` で、PR ごとに有効な記録を 1 件選ぶ。選ぶのは、PR 番号・ベースの SHA・head の SHA・PR 本文の SHA-256 が今回の材料と一致し、記録に写した確認したパスの集合が材料の変えたファイル（`Material.changed`）をすべて含む記録のうち、時刻が最も新しいものである。ベースだけが動くと、head と本文が同じでも変えたファイルが増えることがあり、増えたファイルを確かめていない前の「含まない」を採らないためである。確認したパスの集合とベースの SHA は、`bodies` が出力の件ごとに `paths`・`base_sha` として書き、確認担当が記録へ写す。先に「判断できない」と書いた PR を読み直して「含む」と追記したら、「含む」が採られる。古い記録は消さず、結果のファイルには採らなかった記録の件数も書く。Value 8 のとおり、履歴は追記で残し、今の値は最新の記録から引くためである
  - 本文の確認の結果の取り込み（`tally`）: `--reviewed` の各 PR は、`latest_review` が選んだ記録だけを使う。記録は、上の「有効な記録の選び方」に当たるとき（ベースと head の SHA、PR 本文の SHA-256 が材料に一致し、確認したパスが材料の変えたファイルをすべて含むとき）だけ採る。当たらない記録は、別の版の材料を確認した結果なので採らず、その PR は本文の確認が済んでいないものとして未確認に数え、採らなかった理由を結果のファイルの件ごとの行に書く。`--reviewed` に無い PR も未確認に数える
  - 判定結果の取り込み（`tally`）: `--judged` で渡したすべての判定結果について、PR ごとの材料のハッシュ・ベースの SHA・head の SHA を、今回の材料のファイルの `Material.sha256`・`base_sha`・`head_sha` と照らす。2 つの判定結果を互いに照らすだけでは足りない。前の測定の判定を、別の材料で確かめた本文の確認と組み合わせて合否を出さないためである。1 件でも合わなければ止める（下の異常系の表）。前の回の判定結果も同じ材料のファイルと照らすので、回の途中で材料が変わった場合も、その PR だけを外さずに止める
  - 測定の回の区別（`tally`）: 材料の照合だけでは、同じ材料で打った別の回を区別できない。そこで `judge` は、測定ごとに新しい実行の識別子（UUID）を作り、判定結果のファイルに書く。識別子を作るのは、`--result` に結果ファイルが無いときである。同じ判定の条件（モデル・Claude Code の版・指示文のコミット・送る指示文の SHA-256）の結果ファイルを渡したときは、その測定の欠けた行を補う起動として識別子を引き継ぐ。条件が違う結果ファイル（送る指示文の SHA-256 を持たない、この変更より前の結果を含む）を渡したときは、ファイルを書き換えずに止め、新しい `--result` を求める。別の測定の保存済みの判定を最初の保存で消さないためである。材料のファイルは最初の保存の前に読むので、材料が読めずに止まるときも結果ファイルは変わらない。同じ `--result` を補う `judge` を並べると、起動時に読んだ状態で互いの行を上書きするので、`judge` は結果ファイルごとに排他を取り、取れなければ判定せずに止める。排他は、起動の最初に、指示文の読み出しや Claude Code の版の取得などの外部コマンドを起動する前に取る。競合した起動に余分なコマンドを打たせないためである。このスクリプトは `fcntl.flock` が使える環境（Linux・macOS など）で動く。Windows は対象にしない。判定を始める前に、結果ファイルそのものに排他ロックを掛ける。ロックは inode に付くので、結果の保存先を共有するプロセスどうしなら、ネットワーク名前空間の違う別のコンテナから同じボリュームへ保存する起動とも排他になる。別の結果ファイルへ保存する `judge` とは競合しないので、同じディレクトリに変更後と `c93e690ed` の側の結果を保存する測定も、並べてそのまま進められる。結果は名前の置き換えで保存するので、保存のたびに inode が変わる。そこで、保存では置き換える前の一時ファイルにロックを掛けてから置き換え、ロックを新しい中身へ引き継ぐ。置き換えの前の中身を開いてロックを取った起動は、取れた中身が今のパスの中身かを確かめ、違えば開き直す。結果ファイルが無ければ、空の一時ファイルを作ってロックを掛けてから、`os.link` で結果のパスへ置く。結果のパスに現れた時点で、作った起動がロックを持っている。そのため、並べた起動がどの順で開いても、作った起動より先にロックを取る起動は無い。親のディレクトリが消えて作れないときは、作り直しを繰り返さずに止める。この作り方はハードリンクを使う。結果ファイルがまだ無く、保存先でハードリンクを作れない場合（exFAT・FAT32 など）は、判定の前に、保存先の条件をエラーに表示して止まる。既にある結果ファイルで測定を続けるときはハードリンクを作らないので、この条件は要らず、事前の検査もしない。judge が自動で消すのは、judge が作ったと確かめられた一時ファイルと専用のディレクトリだけで、由来を確かめられないものは残す。judge の一時ファイルは、結果の隣に置く judge 専用のディレクトリの中にだけ作る。専用のディレクトリの名前の候補は、`.<結果のファイル名>.mvv-replay` と、その末尾に `-1` から `-99` までの番号を付けた名前の、合わせて 100 個である。judge はこの順に候補を見て、judge が作った印のあるディレクトリか、新しく作れたディレクトリを使う。印の無いもの（利用者が作ったディレクトリや、印を置く前に強制終了した起動のディレクトリなど）は、残したまま次の候補へ移る。こうすると、由来を確かめられないディレクトリがあっても、新しい一時ファイルは次の起動が回収できる場所に作られる。100 個の候補がどれも使えなければ、judge は判定せずに止まり、候補の名前をエラーに表示する（終了コード 1）。利用者は、不要なものを消してから同じ `--result` で打ち直す。専用のディレクトリを作った起動は、作った直後に、judge が作ったことを示す印のファイル（`.made-by-mvv-replay`）をその中に置き、そのディレクトリの鍵（32 バイトの乱数を 16 進数で書いたもの）を書き込む。既にあったディレクトリには印を足さない。judge が作ったとみなすのは、シンボリックリンクでないディレクトリで、中に印のファイルが通常のファイルとしてあり、ディレクトリと印のファイルの所有者が judge を動かしている利用者で、印のファイルに鍵が書いてあるものだけである。印と一時ファイルは、名前ではなく、開いた専用のディレクトリの記述子を基準に操作する。印の確かめ・書き込み・削除と、一時ファイルの作成・削除がこれに当たる。結果への確定（置き換えとハードリンクの作成）でも、元の一時ファイルは同じ記述子で指定する。途中で専用のディレクトリが移され、同じ名前が別のディレクトリを指しても、その別のディレクトリに印を付けず、ロックを掛けた一時ファイルそのものを結果にするためである。確定先は、専用のディレクトリからの相対パスではなく、`--result` の絶対パスで指定する。保存では、置き換えた後に、結果のパスがロックを掛けた中身と同じ inode を指すかを確かめ、違えば判定を止める。一時ファイルは処理の終わりに消すが、その間にプロセスが強制終了すると専用のディレクトリに残る。そのため、ロックを取った起動は、判定を始める前に、judge が作った専用のディレクトリに残った一時ファイルを消す。消すのは、judge が作ったことを名前で確かめられ、judge を動かしている利用者が持つ通常のファイルだけである。judge は一時ファイルに、`judge-`、16 桁の 16 進数の乱数、その乱数から印の鍵で作った HMAC-SHA256 の先頭 16 桁、`.tmp` をつないだ名前を付ける。回収では、この HMAC を印の鍵で計算し直し、合うものだけを消す。同じ形の名前でも鍵と合わないもの（利用者が置いたファイルなど）は消さない。印のファイルとほかの名前のファイルも消さず、利用者が結果の隣に置いたファイルにも触れない。消す権限が無いなどで消せないファイルは残したまま、判定を続ける。回収と終わりの片付けでは、親のディレクトリを 1 回だけ読み、今ある候補だけを見る。今ない候補の名前は開かない。消す一時ファイルには 2 種類ある。保存用の一時ファイルは、結果のロックを取ってから作るので、ロックを取った時点で残っていれば、強制終了した前の起動のものである。初回の結果ファイルを置くための一時ファイルは、ロックを取る前に作るので、別の起動が作っている途中のこともある。回収と競合して `os.link` の元が消えた起動は、結果ファイルが置かれたものとして開き直す。起動の終わりには、ロックを外した後に、judge が作った専用のディレクトリのうち空のもの（印のファイルのほかに何も無いもの）を消す。一時ファイルの回収には印の鍵が要るので、片付けでは、一時ファイルのあるディレクトリから印を消さない。まず中身を読み、印のほかに何かあるときと、名前がもう同じディレクトリを指さないときは、印を消さずに残す。中身を読んだ時点で一時ファイルがあったディレクトリでは、片付けが途中で強制終了しても鍵が残る。中身が印だけのときは、印を消す前に、ディレクトリを片付けの途中を示す名前へ移す。この名前は、`.mvv-replay.drop-` に、結果のファイル名の SHA-256 の先頭 16 桁、`-`、16 桁の 16 進数の乱数を続けたもので、長さは結果のファイル名によらず 50 バイトである。結果のファイル名を名前に含めると、名前の上限（多くのファイルシステムで 255 バイト）に近い結果のファイル名では、専用ディレクトリは作れても移す先の名前が上限を超え、片付けられなくなるためである。印を消した後、ディレクトリを消す前に強制終了すると、印の無い空のディレクトリが残る。名前を先に移しておけば、次の起動はこの名前の形で judge が片付けの途中だったと確かめて消せる。候補の名前のままだと、利用者が作ったディレクトリと見分けられず、残すしかないためである。手順は次のとおりである。

    1. 名前を片付けの途中の名前へ移し、移した名前が開いたディレクトリを指すかを確かめる。違えば元の名前へ戻して残す
    2. もう一度中身を読み、印のほかに何かあれば、印を残したまま次の回収に任せる。移す前に鍵を読んだ別の起動が、一時ファイルを作ったことがあるためである
    3. 印を消し、名前がまだ同じディレクトリを指すときだけ、名前でディレクトリを消す
    4. 中身を読んだ後に別の起動が一時ファイルを作って消せなかったときは、開いたディレクトリに同じ鍵の印を戻して残す

    回収と片付けでは、候補の名前に加えて、片付けの途中の名前のディレクトリも見る。印のあるものは候補と同じに扱い、中の一時ファイルを回収し、空なら消す。印の無いものは、judge を動かしている利用者が持つディレクトリなら、空のときだけ消す（`rmdir` は空でないディレクトリを消さないので、中のファイルは残る）。新しい一時ファイルは候補の名前のディレクトリにだけ作り、片付けの途中の名前のディレクトリには作らない。

    名前を移すことで、空の専用ディレクトリ 1 個を片付けるたびに、ファイルシステムの操作が 4 回（`rename`・移した後の確かめの `lstat` と `fstat`・もう一度中身を読む `listdir`）と、乱数の生成が 1 回増える。回収と片付けで親のディレクトリを読むときも、候補の名前に当たらない項目ごとに正規表現の照合が 1 回増える。1 回の `judge` の起動で親のディレクトリを読む回数は、排他を取れたかで次のように決まる。

    | 起動の終わり方 | 回収 | 片付け | 親のディレクトリを読む回数 |
    | --- | --- | --- | --- |
    | 排他を取れて最後まで進んだ | 排他を取った後に 1 回 | 起動の終わりに 1 回 | 2 回 |
    | 排他を取った後に、保存用の一時ファイルを作れずに止まった | 排他を取った後に 1 回 | 起動の終わりに 1 回 | 2 回 |
    | 排他を取る前に、初回の結果ファイルを置くための専用ディレクトリか一時ファイルの作成がエラーになった（`_work_tmp` が片付ける。印を置けなかった場合も含む） | しない | 止まる前に 1 回 | 1 回 |
    | 排他を取る前に、ハードリンクの作成がエラーになった。作る時点で親のディレクトリが消えていた場合も含む（`_create_locked` が片付ける）。別の起動が先に結果を置いた場合と、一時ファイルだけが回収された場合は止まらずに開き直すので、この行に当たらない | しない | 止まる前に 1 回 | 1 回（親が消えていれば、読もうとして失敗する） |
    | 排他を取る前に、100 個の候補がどれも使えなかったか、`_work_tmp` が専用ディレクトリを作る時点で親のディレクトリの消失を見つけた | しない | しない | 0 回 |
    | 別の起動が排他を持っていて止まった | しない | しない | 0 回 |

    片付けを誰が呼ぶかは、排他を取る前か後か、どこで失敗したかで決まる。排他を取った後の片付けは、保存に失敗したときも、`_exclusive` の `finally` の 1 回にまとめる。排他を取る前の失敗は `finally` を通らないので、失敗した場所の関数が片付けてから止まる。ただし、並べた起動との競合で初回の結果ファイルを置けなかった場合は、失敗ではないので止まらず、片付けもせずに結果ファイルを開き直す。

    | 排他を取る前に起きたこと | 扱い | 片付ける関数と手順 |
    | --- | --- | --- |
    | `_work_tmp` の中で、新しく作った専用ディレクトリに印のファイルを置けなかった（容量不足で鍵を書き切れないなど） | 止まる | 片付けは次の 3 つの手順で進む。1. `_mark` が、書きかけた印のファイルを消す。2. `_work_tmp` が、作った空の専用ディレクトリの削除を試みる。名前がまだ同じディレクトリを指すときだけ `rmdir` で消す。この手順は、排他を取った後の呼び出しでも行う。印の無いディレクトリは `_drop_work_dir` が利用者のものとみなして残すので、この起動が作ったと確かめられるこの場所で消すしかないためである。3. 残りの片付けは、排他を取る前なら `_work_tmp` が止まる前に `_drop_work_dir` を呼んで行い（下の行と同じ）、排他を取った後なら `_exclusive` の `finally` が行う。排他を取った後の `_work_tmp` は `_drop_work_dir` を呼ばない |
    | `_work_tmp` の中で、専用ディレクトリか一時ファイルの作成がエラーになった | 止まる | `_work_tmp`（`_create_locked` から `drop_on_error` を真にして呼ばれたときだけ）が、止まる前に `_drop_work_dir` を呼ぶ |
    | `_work_tmp` の中で、印を確かめた後に一時ファイルを作る前に、専用ディレクトリが消えた（ロックを外した前の起動の片付け） | 開き直す | 片付けない。`_work_tmp` は None を返し、`_exclusive` が結果ファイルを開き直す |
    | `os.link` が `FileExistsError` になった（別の起動が先に結果ファイルを置いた） | 開き直す | `_create_locked` は一時ファイルを消して None を返し、`_exclusive` が置かれた結果ファイルを開いて排他を確かめる。片付けは呼ばない |
    | `os.link` が `FileNotFoundError` になり、親のディレクトリが残っていて、元の一時ファイルだけが消えていた（結果ファイルを置いてロックを取った別の起動が、残骸として回収した） | 開き直す | 上の行と同じ |
    | `os.link` が上の 2 つ以外のエラーになった（親のディレクトリが消えていた、保存先がハードリンクを作れないなど） | 止まる | `_create_locked` が、一時ファイルを消してから `_drop_work_dir` を呼ぶ |
    | `_work_tmp` の中で、100 個の候補がどれも使えなかったか、専用ディレクトリを作る時点で親のディレクトリの消失を見つけた | 止まる | 片付けない。前者は judge が作った空の専用ディレクトリが無く、後者は片付ける場所が無いためである |

    片付けを `_work_tmp` にだけ置くと、ハードリンクを作れずに止まった初回の起動で、空の専用ディレクトリが保存先に残る。競合で開き直す場合まで止めると、同じ保存先で 2 つの `judge` を起動したとき、後から結果を置こうとした起動が、既にある結果ファイルの排他を確かめないまま作成のエラーで止まる。

    増える回数は片付ける専用ディレクトリの数と親のディレクトリの項目数で決まり、判定する件数にはよらない。所要時間は測っていない。名前を移さない方式とは次のように比べ、名前を移す方式を選んだ。

    | 方式 | 片付けの途中で強制終了したときの残り | 増える処理 | 採否 |
    | --- | --- | --- | --- |
    | 名前を移してから印を消す（採用） | 片付けの途中の名前の空のディレクトリ。次の起動が名前の形で確かめて消す | 上の操作 4 回と乱数 1 回、走査の照合 | 採る |
    | 移さずに印を消し、印の無い空の候補も judge のものとみなして消す | 残らない | 無い | 採らない。利用者が作った空のディレクトリと見分けられず、由来を確かめられないものは残すという規則に反する |
    | 作った証拠を専用ディレクトリの外（結果の隣のファイルなど）に置いてから印を消す | 証拠のファイルが残る。それを片付ける規則がもう 1 つ要る | 証拠の作成と削除で 2 回以上。走査の照合も同じだけ要る | 採らない。結果の隣に置くものが増え、残りの片付けも二重になる |
    | 印を消さずに専用ディレクトリごと残す | 印のある空のディレクトリ（片付けないのと同じ） | 無い | 採らない。役目を終えた作業ディレクトリを自動で片付けるという Value 6 に反する |

    ただし、回収できない一時ファイルが残る順序が 1 つある。別の起動が印の削除前に鍵を読み（`_marked`）、片付け側が手順 2 で中身を確かめた後、ディレクトリが消える前に一時ファイルを作る（`_create_tmp`）順序である。鍵を先に読んだ起動は、片付け側が印を削除した後でも、`rmdir` より先に一時ファイルを作れる。この順序で、片付け側が印を削除してから復元するまでに強制終了し、作成側もその一時ファイルを消す前に強制終了すると、そのディレクトリの鍵は消え、次回の起動はその一時ファイルを回収できない。片付け側が動き続ければ印を戻し、作成側が動き続ければ一時ファイルを自分で消すので、どちらかが動き続ければ残らない。この 2 つの強制終了が重なる場合には備えない。備えるには、一時ファイルを作るたびに専用のディレクトリのロックを取る必要があり、その手間に見合わないためである。結果ファイルを初めて置くときにハードリンクを作れずに止まった場合と、一時ファイルを作れずに止まった場合も、同じ手順で専用のディレクトリを片付けてから止まる。空のファイルは前の結果が無いものとして読む。保存しないまま止まったときは、その起動が作ったファイル（作ったときの inode のまま、パスがまだその中身を指すもの）だけを消す。利用者が置いた空のファイルは、判定に失敗しても消さない。自動で片付けるのは、このスクリプトが自分で作ったものだけである。ロックのためのファイルを別に作らず、プロセスが終われば自動で外れるので、判定の後に片付けるものは残らない。状態の保存先の環境変数（`NDF_STATE_HOME`・`XDG_STATE_HOME`）が違う起動どうしでも同じ排他を取る。`--result` の最後の要素がシンボリックリンクなら、判定せずに止める。結果は名前の置き換えで保存するので、初回の保存でリンクそのものが通常のファイルに置き換わり、次の起動がリンクの先とは別のファイルを読むためである。独立した測定には新しい実行の識別子が要る。初回と順 4 の各再測定では、まだ存在しない結果ファイルのパスを `--result` に渡す。中断した測定を再開するときは、その回のパスを使い回す。前の回の結果ファイルを再測定に渡すと、同じ識別子のまま前の回答を引き継ぐので、`tally` が同じ測定の重なりとして止める。`tally` は、`--judged` で渡したすべての判定結果の識別子を集め、同じ識別子が 2 回以上あれば止める（下の異常系の表）。同じ判定結果を 3 回渡して、1 回の測定だけで再測定の合格を出さないためである。あわせて、1 組の 2 つ目の判定結果が `c93e690ed` の指示文で判定したもので、1 つ目がそれと違う指示文で判定したものかを、判定結果に書いた指示文のコミットで確かめる。さらに、変更後の側と `c93e690ed` の側のそれぞれで、すべての回の判定の条件（モデル・Claude Code の版・指示文のコミット・送る指示文の SHA-256）が同じかを確かめ、違えば止める。回ごとに指示文や実行環境を変えると、公開する指示文で再現を確かめていない改善を合格にしてしまうためである
  - 集計（`tally`）: 設計文書の決定 6 の「件数と割合は 3 通り記録する」のとおり、判定上の値・確認後の値・経路全体の値を、側ごとに件数と、12 を分母にした割合で出す。12 は、設計文書の決定 4 の対象 12 件である。合否に使うのは確認後の値だけである
  - 判定上の値: その側の判定が `follow` で `boundary` が空の PR をすべて数える
  - 経路全体の値: 両方の側で、`Material.incomplete` が空でない PR は省かない。そのうえで、変更前の指示文（`c93e690ed`）の側は判定が `follow` で `boundary` が空の PR を数え、変更後の側はさらに `Material.tabled` が空のタプルの PR だけを数える。変更後の側で表の確かめが止めた件数（`incomplete` が空で、変更後の判定が `follow` で `boundary` が空で、`tabled` が空でないタプルか None の PR）を併記する。本文の確認の結果は使わない
  - 確認後の値: 側ごとに、その側の判定が `follow` で `boundary` が空の PR を、設計文書の決定 6 の順 4 の「その側での扱い」の表に当てる。`Material.tabled` が空でないタプルなら省けない（表で止まる件）、None なら未確認、空のタプルなら本文の確認の結果で決める。確認後の件数は「省ける」の PR だけを数え、未確認の件数を併記する。片方の側の結果を、もう片方の側の件数から引かない
  - 未確認が残るときの値: 設計文書の決定 6 の「未確認の PR があるときの合否」のとおり、回ごとに次の 2 つを求める。合否は上の「回をまたぐ合否」で決め、ここでは決めない
    - 差の範囲: 未確認の PR が、変更後の側ではすべて省けず変更前の側ではすべて省ける場合の差を下限、その逆の場合の差を上限とする。未確認の PR が無ければ、下限と上限は同じ値になる
    - 見落としの可能性: 変更後の側で判定が `follow` で `boundary` が空になり、確認後の扱いが未確認の PR が 1 件でもあれば、可能性が残るとする。未確認には、表を確かめられなかった PR（`tabled` が None）、本文の確認が「判断できない」の PR、記録が無いか材料と合わない PR を含む。その PR の本文に承認必須の操作があれば、下の「承認必須の操作の見落とし」で件数の差にかかわらず不合格になるので、差の範囲だけでは合格を決められないためである
  - 承認必須の操作の見落とし: 本文の確認が「含む」の PR は、どちらの側でも確認後の件数に数えない。ただし分子から除くだけでは済ませない。変更後の側で判定が `follow` で `boundary` が空になり、本文の確認が「含む」の PR が 1 件でもあれば、受け入れ条件 7 は不合格とする。この不合格は、件数の差・「判定の揺れの扱い」の表・未確認の PR の扱いより優先し、差が 3 件以上でも合格にしない。変更後の指示文が承認必須の操作を見落としたことになり、見落としたまま合格と記録すると Value 13「確かめたことだけが済んだとされる」に反するためである
  - #3017 へ記録する件: `tally` は、本文の確認が「含む」の PR のうち、どちらかの側で判定が `follow` で `boundary` が空になった PR を、結果のファイルの「#3017 へ記録する件」に一覧で書く。行ごとに、PR 番号・承認必須の操作の番号・見落とした側（変更後・`c93e690ed`・両方）を載せる。`c93e690ed` の側だけで見落とした PR は、合否を変えず、変更前の指示文が落としていた件として載せる。確認担当は、この一覧の PR と番号を [#3017](https://github.com/devbasex/ai-plugins/issues/3017) の本文へ書き足す。実験版は GitHub へ書かない
  - 副作用: `--plugin-root` があれば、`<置き場>/src` を `sys.path` の先頭へ足してから `ndf` を読み込む。無ければこのリポジトリの `plugins/ndf/src` を足す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `material_of` | 全文を取れない | API の応答に差分が無く、手元でも読み直せない | 戻り値 | 既定値で続ける（LLM を呼ばず「材料が欠けた」と記録する） | 結果のファイルの件ごとの行 | 再実行できる |
| `judge_once` | 回答が無い・形が違う | 期限切れ・利用上限・2 回とも形が違う | `llm.ask` の `JudgeAnswer` | 既定値で続ける（読めない回答として分母に残す） | 結果のファイルの件ごとの行 | 再実行できる |
| `main`（`tally`） | 回によって判定の条件が違う | 送る指示文の SHA-256 を持つ同じ側の判定結果のどれかで、モデル ID・Claude Code のバージョン・指示文のコミット・送る指示文の SHA-256 のどれかが違う | 結果の集計（ハッシュを持たない判定結果の判定し直しを案内する前） | 止める | 標準エラーと終了コード 1 | 同じ条件で判定し直す |
| `main`（`judge`） | 結果ファイルを続きに使えない | `--result` の結果ファイルの判定の条件が今の条件と違う | 最初の保存の前 | ファイルを書き換えずに止める | 標準エラーと終了コード 1 | 新しい `--result` を渡す |
| `main`（`judge`） | 同じ結果ファイルの `judge` が動いている | 結果ファイルごとの排他を取れない（同じボリュームを共有する別のコンテナの起動も含む） | 外部コマンドを起動する前 | 判定せずに止める | 標準エラーと終了コード 1 | 先の `judge` が終わってから打ち直す（同じ測定の続きなので、同じ `--result` を使う） |
| `main`（`judge`） | 結果の保存先がシンボリックリンク | `--result` の最後の要素がシンボリックリンク | 結果ファイルを読む前 | 判定せずに止める | 標準エラーと終了コード 1 | リンクの先のパスを渡す |
| `main`（`judge`） | 照合の値と違う | `--expect-claude-version`・`--expect-prompt-commit`・`--expect-prompt-sha256` のどれかを渡し、今の Claude Code の版・判定結果に書く指示文のコミット・送る指示文の SHA-256 のうち対応するものがその値と違う | 材料を読む前（LLM を呼ぶ前） | 判定せずに止める | 標準エラーと終了コード 2 | 残す結果と同じ Claude Code の版・指示文で打ち直す |
| `main`（`tally`） | 残す結果のモデルか版が側によって違う | 変更後の側と `c93e690ed` の側のどちらにも送る指示文の SHA-256 を持つ判定結果があり、その 2 つでモデル ID か Claude Code のバージョンが違う。組の 2 つのモデルと版が揃っているかは、この照合と、上の「回によって判定の条件が違う」の照合で確かめる。2 つとも通り、ハッシュを持たない判定結果が無ければ、どの組もモデルと版が揃っているので、組ごとには照合し直さない。材料のハッシュが違う組は、下の「判定結果が今回の材料と合わない」で止める | 結果の集計（ハッシュを持たない判定結果の判定し直しを案内する前） | 止める | 標準エラーと終了コード 1 | どちらかの側をすべての回で、もう一方の側と同じモデルと版で判定し直す |
| `main`（`tally`） | 送った指示文を照合できない | 判定結果のどれかが、送る指示文の SHA-256（16 進 64 桁）を持たない（この変更より前の形式） | 結果の集計 | 止める | 標準エラーと終了コード 1。標準エラーには、ハッシュを欠く判定結果ごとに、回・側・`--judged` のファイル名と、判定し直す案内を 1 行ずつ出す。この案内は、ハッシュを持つ判定結果（残す結果）どうしの条件が両立すると分かった後にだけ出す。残す結果どうしが両立しないと、判定し直しても集計へ進めないためである。両立の確かめは 2 つある。1 つは、側ごとに、残す結果の条件が回をまたいで一致すること（上の「回によって判定の条件が違う」）。もう 1 つは、両側に残す結果があれば、モデル ID と Claude Code の版が側をまたいでも一致すること（上の「残す結果のモデルか版が側によって違う」）である。組の 2 つのモデルと版は、組ごとには照合しない。ハッシュを持たない側の版が違うだけなら、その側を判定し直せば揃うので、案内の前に止めない。案内には照合の引数を含める。モデルと Claude Code の版（`--expect-claude-version`）は、組の 2 つでも側ごとの全回でも揃える必要があるので、案内を出す前に全回に共通の値を 1 つ決め、すべての案内で同じ値を使う。残す結果があればそのモデルと版を使い、どちらの側にも無ければ 1 回目の変更後の判定結果のモデルと版を使う。決めた値は標準エラーにも出す。回ごとに旧結果から取ると、案内どおりに判定し直しても、側ごとの条件を揃える確かめで止まるためである。同じ側に残す結果があれば、その指示文のコミット（`--expect-prompt-commit`）と SHA-256（`--expect-prompt-sha256`）も含める。`c93e690ed` の側には `judge` のコマンド例を出す。実行する人は `--result` に新しい結果ファイルのパスを指定し、その引数をシェル向けに引用する。変更後の側は、`--prompt-commit` を省いて読み込んだ `ndf` の指示文で判定したかを結果ファイルから見分けられないので、コマンドは出さない。元の `judge` の起動方法（`--prompt-commit`・`--plugin-root`）を確かめ、同じ起動に照合の引数を足すよう案内する | 挙がった側と回だけを、新しい `--result` で判定し直す。以前の結果ファイルは残す。次の `tally` では、該当する `--judged` の引数に新しい結果ファイルのパスを渡す。前のファイルを `--result` に渡すと、判定の条件が違うので `judge` が止まる。ほかの側と回は取り直さない |
| `gate_once` | 捕まえたコメントが 0 件 | 差し替えが働かない | 捕まえた件数 | 止める（「未確認」と記録する） | 結果のファイルと終了コード 1 | 直して打ち直す |
| `gate_once` | 拒んだ要求が 1 件以上 | ゲートが想定の外の書き込みを試みた・許可の一覧が足りない | 拒んだ件数 | 止める（「未確認」と記録する）。拒んだ要求は GitHub へ届いていない | 結果のファイルと終了コード 1 | 一覧を見直し、`guard-check` から打ち直す |
| `guard_check` | 書き込みが作り物まで届いた・コメントを捕まえない | 差し替えの抜け | 作り物が記録した要求 | 止める | 結果のファイルと終了コード 1 | 直して打ち直す |
| `read_pairs` | 一部の要求が失敗した | GitHub の失敗・上限・権限 | `aliased` の `GitHubError` | その分の組を「未取得」として `store` に書いて続ける。ほかの分の結果は残す | 結果のファイルの組ごとの行 | 同じ `store` で 1 回だけ打ち直せる。残った組は表の確かめで「未確認」、本文の確認で「判断できない」になる |
| `review_paths` | `issues/` の一覧を読めない | GitHub の失敗・head に `issues/` が無い | `aliased` の `GitHubError`・応答が Tree でない | その PR の同じ課題の文書を集めずに、`pairs` に PR が変えたファイルだけを入れ、`listed` を偽にして返す。`bodies` はその PR に「一覧を読めない」と書き、「判断できない」の印を付ける | `bodies` の出力の件ごとの行。その PR の本文の確認は「判断できない」になる | 再実行できる |
| `main`（`bodies`・`tally`） | 判定結果が今回の材料と合わない。前の回と材料が違う場合も含む | 判定結果の PR ごとの材料のハッシュか、ベースか head の SHA が、今回の材料のファイルと違う（前の測定や、組み直す前の材料で判定した結果を渡した。再測定の途中で材料を組み直した） | 判定結果の取り込み | 止める。合わない PR だけを外して続けることはしない。違う対象で測った回をまとめて合否を出さないためである | 標準エラーに合わない PR と判定結果のファイルを書き、終了コード 1 | 今回の材料で `judge` を打ち直すか、その材料で判定した結果を渡し直す。材料を組み直したときは、1 回目の測定から打ち直す |
| `main`（`tally`） | 同じ測定を 2 回以上渡した・組の指示文が違う | `--judged` の判定結果に同じ実行の識別子が 2 回以上ある。1 組の 2 つ目が `c93e690ed` の指示文でないか、1 つ目と同じ指示文 | 判定結果の取り込み | 止める | 標準エラーに重なった識別子か、合わない組を書き、終了コード 1 | 回ごとに別に打った判定結果を渡し直す |
| `main`（`tally`） | 本文の確認の記録が材料と合わない | 記録の head の SHA か PR 本文の SHA-256 が、集計に使う材料と違う | 記録の取り込み | その記録を採らず、その PR を未確認に数える | 結果のファイルの件ごとの行 | 今の材料で `bodies` から打ち直し、確認し直す |
| `prompt_at` | 指定のコミットを読めない | コミットが無い | `git show` の失敗 | 止める | 標準エラーと終了コード 2 | 取得してから打ち直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 前の指示文のコミット | 引数（`--prompt-commit`） | 実験版へ渡す。値は設計文書の決定 4 | `c93e690ed` |
| 比べる PR の番号と、対照で挙がるべき番号 | 引数（`--prs`・`--expect`） | 実験版へ渡す。値は設計文書の決定 4・6 | — |
| モデル | 引数（`--model`） | 実験版へ渡す | — |
| 作り物の材料の対 | ファイル | `experimental/ndf/mvv_replay_cases/` | 対は 1 つ。「扱いあり」と「扱いなし」の 2 つの材料で、それぞれ要求仕様と設計書の 2 ファイル（設計書は同じ） |
| `Material` | 型（NamedTuple: `base_sha: str`・`head_sha: str`・`text: str`・`sha256: str`・`tabled: Optional[tuple[str, ...]]`・`incomplete: tuple[str, ...]`・`changed: tuple[str, ...]`・`pr_body: Optional[str]`・`pr_body_sha256: Optional[str]`。`tabled` の None は表を確かめられなかった。`incomplete` は `incomplete_patches` の理由で、空なら差分の全体が材料に載る。`changed` は削除を除く変えたファイルのパス。`pr_body` は材料を組んだ時点の PR 本文で、None は取得できなかった） | `mvv_replay.py` | — |
| `ReviewTarget` | 型（NamedTuple: `number: int`・`head_sha: str`・`changed: tuple[str, ...]`。保存した材料の `Material` から作る） | `mvv_replay.py` | — |
| `ReviewPaths` | 型（NamedTuple: `pairs: tuple[tuple[str, str], ...]`・`listed: bool`。`listed` が偽なら `issues/` の一覧を読めず、`pairs` は PR が変えたファイルだけ） | `mvv_replay.py` | — |
| 組ごとの読み取りの結果 | ファイル（組ごとに状態「取得済み」「本文が無い」「未取得」と、読みに行った回数（1 か 2）と、取得済みなら本文） | 引数 `--store` で渡す | — |
| 判定結果 | ファイル（測定 1 回の側ごとに 1 つ。測定ごとの実行の識別子（UUID。欠けた行を補う起動では引き継ぐ）、判定の条件（指示文の SHA-256 を含む）、PR ごとの試行の回数 `tries`、PR ごとの回答と `judge_once` の実行の情報。実行の情報に指示文のコミット、材料のハッシュとベースと head の SHA を含む） | `judge` が引数 `--result` へ書き、引数 `--judged` で `bodies` と `tally` へ渡す。`--judged` は変更後と `c93e690ed` の 1 組を 1 回の測定として、回の数だけくり返す | — |
| 本文の確認の結果 | ファイル（追記だけで書く記録の並び。記録ごとに、記録した時刻、PR 番号、「含まない」「含む」「判断できない」と根拠、「含む」なら承認必須の操作の番号、確認したベースと head の SHA・確認したパスの集合 `paths`・PR 本文の SHA-256・「読めていない本文がある」の印の有無。リリース後テストの担当が書き、回ごとに書き足す） | 引数 `--reviewed` で `bodies`（確認済みの PR を外す）と `tally` へ渡す。無い PR と、ベースか head の SHA、PR 本文の SHA-256 が材料と合わない PR と、確認したパスが材料の変えたファイルを含まない PR は、本文の確認が済んでいないものとして未確認に数える | — |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `material_of` | `mvv.compare_files`・`mvv_material_decide.patch_material`・`mvv_material_decide.incomplete_patches` | `grep -n "def compare_files\|def patch_material\|def incomplete_patches" -r plugins/ndf/src/ndf/domains/records` | 使う。`patch_material` には `limit=None` を渡す。`incomplete_patches` は本番のゲートと同じ判定を引数を変えずに使う |
| `read_pairs`・`review_paths` | `GitHub.aliased` | `grep -n "def aliased" -A6 plugins/ndf/src/ndf/adapters/github.py` | 使う。途中の失敗で一部の結果を返さないので、呼ぶ前に自分で件数ごとに分ける |
| `review_paths` | `issue_docs.design_issues`・`DESIGN_FILE` | `grep -n "^def \|^[A-Z_]* =" plugins/ndf/src/ndf/core/issue_docs.py` | 使う。要求仕様の名前の定数は無いので、`issue-<番号>-requirements.md` の形をこの関数に書く |
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
