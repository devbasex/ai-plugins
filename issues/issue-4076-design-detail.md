# #4076 の詳細設計: 工程と MVV（写し・指摘の基準 5・根拠の行の確かめ・記録）

設計文書 [issue-4076-design.md](./issue-4076-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。前提はスプリントブランチ `sprint/p12-lcrfast-r1` が main へ入った後の形である。このブランチは #4014 の `impact`・`effective_severity`、#4030 の `fixer_ci_only`、#4013 の修正担当の会話を持つ。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/data/review-criteria.json         … 基準 5 の文と mvv の指示
├── src/ndf/domains/records/
│   ├── mvv.py                                   … current（写し）・review の記録・共通原則の本文
│   ├── mvv_gate.py                              … principles を mvv.py から読む
│   ├── mvv_decide.py                            … 項目の一覧・根拠の行の確かめ・兆候の種類
│   └── cli.py                                   … ndf records mvv current
├── src/ndf/domains/crossreview/
│   ├── criteria_decide.py                       … 基準 5 を組む
│   ├── drive.py                                 … 写しを得る・根拠の行を確かめる・指示に足す
│   ├── journal.py                               … mvv の行・NDF の指摘・レッドライン
│   ├── finish_decide.py                         … 欠け mvv_unresolved
│   └── report_decide.py                         … 結果の outputs.mvv
└── tests/domains/
    ├── records/test_mvv_current.py *
    ├── records/test_mvv_citations.py *
    └── crossreview/test_crossreview_{criteria,journal,run,finish}.py
```

依存の向き: `crossreview` が `records.mvv`（写しと記録）と `records.mvv_decide`（純粋な確かめ）を読む。`records` は `crossreview` を読まない。`mvv_decide` は git もファイルも読まない純粋な関数だけを持ち、`mvv.py` が git とファイルを読んで `mvv_decide` へ渡す。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/records/mvv_decide.py` | 変更 | MVV の純粋な判定 | `mvv_material_decide.redlines` | モジュール | `items_of`（新設）・`citation_errors`（新設）・`count_signals`（変更） |
| `src/ndf/domains/records/mvv.py` | 変更 | MVV の設定・本文・記録の読み書き | `git`・`mvv_decide`・`contracts` | `Snapshot`（新設）、モジュール | `current`・`record_review`・`principles`（新設。`mvv_gate` から移す） |
| `src/ndf/domains/records/mvv_gate.py` | 変更 | 承認ゲートの直前の判定 | `mvv` | モジュール | `principles` を消し `mvv.principles` を呼ぶ |
| `src/ndf/domains/records/cli.py` | 変更 | `ndf records` の入口 | `mvv` | モジュール | `_mvv_current`（新設） |
| `contracts/data/review-criteria.json` | 変更 | 指摘の基準の文 | — | — | キー `mvv_criterion`・`mvv`（新設） |
| `src/ndf/domains/crossreview/criteria_decide.py` | 変更 | 基準と指示の組み立て | `records.mvv.Snapshot` | モジュール | `criteria_lines`・`reviewer_block`・`fixer_block`（変更）・`mvv_item`（新設） |
| `src/ndf/domains/crossreview/drive.py` | 変更 | 収束ループの駆動 | `records.mvv`・`criteria_decide`・`journal` | `RunConfig`・`_Run`（変更） | `_start`・`_round`・`_review_prompt`・`_fix_prompt`・`_summary`（変更）・`_citations`（新設） |
| `src/ndf/domains/crossreview/journal.py` | 変更 | 指摘のジャーナル | `criteria_decide` | `FindingJournal` | `ingest`（変更）・`ingest_citations`（新設）・`handable`（新設） |
| `src/ndf/domains/crossreview/finish_decide.py` | 変更 | 終わり方の判定 | `criteria_decide` | `Finding`（変更） | `_finding_gaps`・`finish`（変更） |
| `src/ndf/domains/crossreview/report_decide.py` | 変更 | 結果 JSON | — | モジュール | `mvv_outputs`（新設） |

## MVV の写し（`current`）

満たす決定: 決定 1・2、受け入れ条件 1・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Snapshot` | `records/mvv.py` | 新設 | `NamedTuple(state: str, version: Optional[int], sha256: Optional[str], path: Optional[Path], items: tuple[str, ...])` | 承認済みの MVV の写しの結果 |
| `current` | `records/mvv.py` | 新設 | `current(root: Path, ref: str) -> Snapshot` | 参照の MVV の設定と本文を読み、承認済みなら写しを書いて返す |
| `items_of` | `records/mvv_decide.py` | 新設 | `items_of(body: str, principles: str) -> tuple[str, ...]` | 本文と共通原則から実在する項目の一覧を作る |
| `_mvv_current` | `records/cli.py` | 新設 | `_mvv_current(args: argparse.Namespace, root: Path) -> dict[str, Any]` | `ndf records mvv current --base` の結果 JSON |

- `current`
  - 事前条件: `ref` は `<リモート>/<ブランチ>` の形で取得済み（`signals_ref` で作る）
  - 事後条件: 状態が `approved` なら `path` のファイルの中身は `ref` の本文と同じである。その sha256 は `ref` の `.ndf/mvv.json` の最後のバージョンの `sha256` と一致する。`approved` でなければ `path`・`items` は空で、ファイルを書かない
  - 副作用: `git show` を 2 回（設定と本文）。CLI の `_mvv_current` だけは、読む前に `git fetch <リモート> <ブランチ>` を 1 回打つ（cross-review は同期で取得済みの参照を渡すので打たない）。写しの置き場に 1 ファイルを書く（同じ名前があれば書かない）
- `items_of`
  - 事後条件: `## Value` の下の行頭の `<番号>.` から `Value <番号>` を作る。`## 必ず人の承認が要る操作` の表と共通原則の表から `P<番号>`・`C<番号>` を作る（`mvv_material_decide.redlines` を使う）。`## Mission`・`## Vision` があれば `Mission`・`Vision` を作る。重複を除き、出た順

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `current` | 設定が無い | `ref` に `.ndf/mvv.json` が無い | `decl_at` の `MvvError`（`git show` の失敗） | 状態 `absent` を返す | `ok`・`outputs.mvv.state: absent` | 無し |
| `current` | 本文の sha256 が合わない | 承認の後に本文が変わった | sha256 の比較 | 状態 `mismatch` を返し写しを書かない | `ok`・`state: mismatch` | 無し |
| `current` | 設定が壊れている・旧い形 | JSON でない・`_refuse_legacy` | `decl_at` | `MvvError` を呼び出し元へ返す | `refused`（`precondition`）、`summary` に理由 | 設定を直して打ち直す |
| `_mvv_current` | 取得に失敗した | ネットワーク・リモートの名前 | `git fetch` の終了コード | 古い参照を読まずに止める | `refused`（`precondition`）、`summary` に取得の誤り | 打ち直せる |
| `current` | 写しを書けない | 保存先の権限・容量 | `OSError` | 呼び出し元へ返す | `refused`（`precondition`） | 打ち直せる（同じ名前は書き直さない） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `SNAPSHOT_DIR` | 定数 | `records/mvv.py` | `snapshots`（`_state_dir()` の下） |
| 写しの名前 | 規則 | `records/mvv.py` | `v<版>-<sha256 の先頭 12 文字>.md` |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 複数のレーンの `current` | 写しのファイル | 一時ファイルへ書いて `os.replace`（同じ中身なので後勝ちでよい） | 名前が中身で決まるので、何度打っても同じファイル |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `current` | `mvv.decl_at`・`mvv_decide.latest`・`mvv_decide.status`・`signals_ref` | `grep -n "def " plugins/ndf/src/ndf/domains/records/mvv*.py` | 使う |
| `items_of` | `mvv_material_decide.redlines`・`mvv_gate.principles` | 同上 | `redlines` を使う（P・C の番号）。`principles` は `mvv_gate` が `mvv` を読むため `mvv.py` へ移す（循環を作らない）。Value の番号を拾う関数は無いので足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `current` | ベースの承認済みの本文から写しを書き、版 11・項目を返す。作業場所の本文が違っても写しはベースの中身 | 同じ中身で 2 回打つとファイルは 1 つ | 設定が無い → `absent`。sha256 が違う → `mismatch` で書かない。壊れた設定 → `MvvError` |
| `items_of` | Value 1〜17・P1〜P4・C1〜C8・Mission・Vision | `## Value` の下の入れ子の箇条（`    - `）を番号にしない | `## Value` が無い本文 → Value の項目が 0 件 |

## 指摘の基準 5

満たす決定: 決定 3、受け入れ条件 2・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `criteria_lines` | `criteria_decide.py` | 変更 | `criteria_lines(focus: Sequence[str], mvv: Optional[Snapshot] = None) -> list[str]` | `mvv` が承認済みのときだけ基準 5 を足す |
| `reviewer_block` | `criteria_decide.py` | 変更 | `reviewer_block(focus: Sequence[str], mvv: Optional[Snapshot] = None) -> list[str]` | 基準 5 があるとき `mvv` のキーの書き方の文を足す |
| `fixer_block` | `criteria_decide.py` | 変更 | `fixer_block(focus: Sequence[str], mvv: Optional[Snapshot] = None) -> list[str]` | 基準 5 と、写しを読んで反するかを確かめ直す文を足す |
| `mvv_item` | `criteria_decide.py` | 新設 | `mvv_item(value: object, items: Sequence[str]) -> Optional[str]` | 相手役の `mvv` が項目の一覧にあればその値、無ければ None |

- `criteria_lines`: `mvv` が None か `state != "approved"` なら今と同じ行。基準 5 の文は `review-criteria.json` の `mvv_criterion`（`{version}`・`{path}` を埋める）
- `reviewer_block`: 基準 5 があるときだけ、`mvv.reviewer`（「基準 5 の指摘は mvv に反する項目を 1 つ書き、impact に反する文と利用者に起きることを書く。必ず人の承認が要る操作に当たるものは P・C の番号を mvv に書く」）を足す
- 指示の JSON の形（`drive._review_prompt` の最後の行）には、基準 5 があるときだけ `"mvv": "<基準 5 のとき反する項目 か 空>"` を足す

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `mvv_criterion` | 文 | `review-criteria.json` | 「プロジェクト MVV（版 {version}。本文は {path}）の Mission・Vision・Value の文に反する」 |
| `mvv_number` | 定数 | `review-criteria.json` | `"5"` |
| `mvv.reviewer`・`mvv.fixer`・`mvv.citation` | 文 | `review-criteria.json` | 相手役・修正担当への文と、根拠の行の誤りの指摘の文（`{path}`・`{line}`・`{cited}`・`{version}`） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `criteria_lines` | 承認済みで基準 5 が番号の順に出る（3 の重点と並ぶ） | `mvv` が None・`absent` で今と同じ行 | — |
| `mvv_item` | `Value 5` → `Value 5` | 前後の空白を除く | 一覧に無い `Value 99`・文字列でない値 → None |

## 根拠の行の確かめ

満たす決定: 決定 4、受け入れ条件 3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `citation_errors` | `records/mvv_decide.py` | 新設 | `citation_errors(added: Sequence[tuple[str, int, str]], versions: Sequence[int], items: Sequence[str]) -> list[dict[str, Any]]` | 足した行（パス・行の番号・本文）の根拠の行から、版の違いと実在しない項目を返す |
| `_citations` | `crossreview/drive.py` | 新設 | `_citations(run: _Run, prep: _Prepared) -> list[dict[str, Any]]` | 差分の足した行を集め、`citation_errors` の結果を NDF の指摘の形にする |
| `ingest_citations` | `crossreview/journal.py` | 新設 | `ingest_citations(self, errors: Sequence[dict[str, Any]], round_no: int) -> frozenset[str]` | NDF の指摘をジャーナルへ入れ、触ったキーを返す |

- `citation_errors`
  - 根拠の行は、本文を左から空白と `- ` を除いた先頭が `根拠:` で、`（MVV バージョン <数>` を含む行。コードのブロック（` ``` ` の間）の行は呼び手が除く
  - 項目は `根拠:` の後ろから括弧の前までを ` / ` で分けたもの。`スプリント` で始まる項目と `根拠なし` は照らさない
  - 返す 1 件: `{"path", "line", "cited": "<行の項目と版>", "version": <引いた版>, "unknown": ["<実在しない項目>"]}`。版が `versions` のどれかで `unknown` が空なら返さない。`versions` はベースブランチの版と、PR が MVV を変えるときの head の版である。head の版は、head の最後のバージョンが head の本文の sha256 と一致するときだけ入る。`items` は 2 つの版の項目の和
- `_citations`: `prep.diff` の unified diff の `+` の行（`+++` を除く）から、パスと新しい側の行の番号を作る。指摘の `text` は `review-criteria.json` の `mvv.citation` から作り、行の番号を含めない。行がずれても同じキーで出し直されるように、パスと引いた本文だけで作る

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_citations` | 差分を読めない | 差分のファイルが無い | `OSError` | 確かめを飛ばし、注意に載せて続ける | 結果の `warnings` | 次のラウンドで読み直す |
| `citation_errors` | 版が数でない | `（MVV バージョン x` | 正規表現の不一致 | 根拠の行として拾わない | 無し | — |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `citation_errors` | 版が違う・項目は全部ある | 1 件（`unknown` は空） |
| `citation_errors` | 版が同じ・実在しない項目がある | 1 件（`unknown` に項目） |
| `citation_errors` | 行頭が `根拠:` でない（文中の「根拠: Value N（MVV バージョン 1）」の引用） | 返さない |
| `_round` | MVV が承認済みでない | 確かめない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `citation_errors` | 版 1 を引いた行 → 1 件。`Value 18` → `unknown` | `スプリント Value 4`・`根拠なし` は照らさない。全角の括弧だけを読む | 文中の引用・数でない版を拾わない |
| `_citations` | `+` の行だけを読み、新しい側の行の番号を付ける | `+++` の行と削除の行を読まない | 差分が無い → 注意 |
| `ingest_citations` | 同じ誤りが次のラウンドも出れば同じ行を出し直す。直れば出ない | — | — |

## ジャーナルとレッドライン

満たす決定: 決定 3・5、受け入れ条件 2・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `FindingJournal.ingest` | `journal.py` | 変更 | `ingest(self, raised, round_no, items: Sequence[str] = ()) -> frozenset[str]` | 相手役の `mvv` を `mvv_item` で確かめて行の `mvv` に書く。一覧に無ければ外して `self.warnings` に足す |
| `FindingJournal.handable` | `journal.py` | 新設 | `handable(self) -> list[dict[str, Any]]` | 修正担当へ渡す未解決の行（収束を止める重さで、`mvv` が `P`・`C` でないもの） |

- 行の `mvv` が `P<番号>`・`C<番号>` のときは行の `redline: true` を書き、`blocking` の判定から外す（収束を止めない）。修正担当の指示へ並べない
- 行の `by` が `ndf` の根拠の行の誤りは、`severity: major` とし、`impact` に `mvv.citation` の起きることの文を持たせる。この誤りは #4014 の `effective_severity` で下がらない

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `ingest` | `mvv: Value 5` の行が `mvv` を持ち収束を止める | 出し直しで `mvv` が変わったら最初の値を残す | 一覧に無い `mvv` → 外して注意 |
| `handable` | `P1` の行を渡さない・`Value 5` を渡す | 直した行・見送った行を渡さない | — |

## 終わり方と結果

満たす決定: 決定 5・6、受け入れ条件 4・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_finding_gaps` | `finish_decide.py` | 変更 | （同じ） | `mvv` を持つ行か `by: ndf` の根拠の行の誤りの未解決を、`unresolved_finding` でなく `mvv_unresolved` の欠けにする |
| `finish` | `finish_decide.py` | 変更 | （同じ） | 欠けに `mvv_unresolved` があれば、状態を止める側（`STOPPED`・`cause.kind: mvv_unresolved`）にする |
| `mvv_outputs` | `report_decide.py` | 新設 | `mvv_outputs(rows: Sequence[Mapping[str, Any]], version: int) -> dict[str, Any]` | 結果 JSON の `outputs.mvv`（`findings`・`citations`・`redlines`・`disputed`） |
| `record_review` | `records/mvv.py` | 新設 | `record_review(root: Path, pr: int, version: int, rows: Sequence[Mapping[str, Any]]) -> int` | MVV の行ごとに `mvv-review` を、見送りごとに `review_disputed` を書き、書いた件数を返す |
| `count_signals` | `records/mvv_decide.py` | 変更 | （同じ） | `review_disputed` を（PR・指摘の id）で 1 件にまとめ、`unknown` に足す |

- `record_review`: `drive._summary` が終わりに 1 回呼ぶ。同じ（PR・指摘の id・終わり方）の行が既にあれば書かない（起動のやり直しで重ねない）
- `Signals` の形は変えない（`unknown` の数に含める）。`count_signals` の `pairs` に `review_disputed` を足す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `record_review` | 記録を書けない | 保存先の権限 | `OSError` | 結果は変えず注意に載せる | 結果の `warnings` | 次の起動で書く（重ねない） |
| `finish` | `mvv_unresolved` で止まる | 上限のラウンドで MVV の指摘が残った | 欠けの種類 | 止まる | 終了コード `STOPPED`、`summary` に残った指摘と項目 | 直して `ndf cross-review run` を打ち直す |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `record_review` | 種類・PR・指摘の id・項目・終わり方・版・repo・時刻 | `mvv-validation.jsonl`・`mvv-signals.jsonl` | 指摘の本文（PR に残るので写さない） |
| `mvv_outputs` | 指摘の id・項目・相手役・終わり方 | 結果 JSON の `outputs.mvv` | — |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `finish` | MVV の未解決 → `mvv_unresolved` で止まる。普通の未解決だけ → 今どおり `incomplete` | MVV の指摘が全部直った → 今どおり | — |
| `record_review` | 見送り 1 件 → `review_disputed` 1 行と `mvv-review` 1 行 | 同じ起動のやり直しで重ねない | 書けない → 注意 |
| `count_signals` | `review_disputed` 2 件（別の指摘）→ `unknown` が 2 増える | 同じ組の 2 行は 1 件 | 前の版の行は数えない |

## 手順書

満たす決定: 決定 2・7、受け入れ条件 1・6

| ファイル | 変える中身 |
| --- | --- |
| `skills/development-workflow/references/project-mvv.md` | 節「工程での読み方」を足す（工程ごとに読む所・`current` の結果の欄・人へ戻す形・「MVV なし」）。根拠の行の形は `decisions.md` へリンク |
| `skills/development-workflow/references/approval-request.md` | 「MVV 判定」の行の出所に、cross-review の MVV の記録のうち終わり方が `redline`・`deferred` の行を足す。設計承認ゲートは設計 PR の行、リリース承認ゲートはスプリントの単位の PR の行を、項目・理由とともに載せる |
| `skills/development-workflow/references/agent-layers.md` | conductor → supervisor と supervisor → worker の起動指示の項目に「MVV」を足し、守る規則に「MVV の欄の写しを作業の前に読む」を足す |
| `skills/design/SKILL.md`・`skills/design/references/decisions.md`・`skills/tdd-cycle/SKILL.md`・`skills/requirements-design/SKILL.md` | 参照を `project-mvv.md#工程での読み方` へ直す。`decisions.md` の例の版を「`current` の版を書く」形にする |
| `skills/cross-refactoring/SKILL.md` | `$RF_MVV` を `current` の結果の 1 行と写しを読む指示で埋める手順 |
| `skills/cross-review/SKILL.md`・`skills/fix/SKILL.md` | 基準 5・`mvv_unresolved`・`outputs.mvv` の説明。`fix` の「指摘の基準」の表に基準 5 の写し |
| `docs/contracts/commands.md` | `ndf records mvv current` の行 |
| `docs/glossary/glossary.json` | 語「MVV の写し」「根拠の行の確かめ」「MVV に反する指摘」を追加し、`ndf records glossary render` で `docs/glossary.md` を作り直す |
