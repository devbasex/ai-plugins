# #4363 の詳細設計: 実際に起きると確かめられた指摘だけを重い扱いのまま残す

[設計文書](./issue-4363-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。決定の理由と不変条件（I1〜I4）は設計文書を、受け入れ条件は [要求仕様](./issue-4363-requirements.md) を指す。この課題の次に #3780、その次に #2468 を実装し、2 つはここで作る関数とデータをそのまま使う。

この文書で使う語は次のとおりである。

| 語 | 意味 |
| --- | --- |
| 相手役 | cross-review で差分を読んで指摘を書く AI |
| 判定役 | 相手役が書いた重い指摘（critical・major）が実際に起きるかを確かめる読むだけの claude |
| 行 | 状態ファイルの指摘 1 件分の記録（`findings` の要素） |
| 保護の種類 | `secret`・`authz`・`user_data`・`irreversible`・`safety` の 5 つ（秘密・認証認可・利用者のデータ・戻せない操作・人の安全） |

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/domains/crossreview/
│   ├── carry.py *            引き継ぎ（drive.py から移す）
│   ├── reviewing.py *        相手役への指示と起動（drive.py から移す）
│   ├── verify_decide.py *    判定役の判断
│   ├── verify.py *           判定役の起動
│   ├── criteria_decide.py
│   ├── journal.py
│   ├── drive.py
│   └── report_decide.py
├── contracts/data/review-criteria.json
├── contracts/limits/cross-review.remark-chars.json
├── contracts/limits/cross-review.remarks-total.json
├── skills/cross-review/docs/01-state-and-review.md
└── tests/domains/crossreview/
```

依存の向き: `drive` が `carry`・`reviewing`・`verify`・`journal` を読み、`verify` が `verify_decide` を読み、`journal` と `verify_decide` が `criteria_decide` を読む。`carry`・`reviewing`・`verify` は `drive` を読まず、使う値を Protocol（呼ばれる側が要る属性だけを書いた型）で受けるので、輪は無い。`verify_decide` は名前が `_decide` で終わる判断のモジュールで、外部のコマンドもファイルも触らない（`ndf check` の構造の規則）。

`drive.py` は 986 行で、構造の規則の上限は 1,000 行である。この課題と後の 2 課題がすべて `drive.py` を変えるので、先に引き継ぎと相手役の起動の 2 つのまとまりを別のモジュールへ移す（実装計画のプレリファクタリング）。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/domains/crossreview/carry.py` | 新設 | 起動の始めに PR を読み、前の実行の指摘を引き継ぐ | `journal`・`prior_decide`・`threads`・`store` | モジュール、`Prior`、`CarryRun`（Protocol） | 変更（移動）: `reader`・`read_prior`・`read_reviews`・`carry`・`restate`・`detach_revived`・`_settle_resolved`・`_merge_tracked` |
| `plugins/ndf/src/ndf/domains/crossreview/reviewing.py` | 新設 | 相手役へ渡す資料と指示を組み、席を起動する | `seats`・`criteria_decide`・`prior_decide`・`changes`・`scope_decide` | モジュール、`Prepared`、`Reviewed`、`ReviewingRun`（Protocol） | 変更（移動）: `write_diff`・`remarks`・`remarks_file`・`review_prompt`・`review_round`・`_fill_seats`・`_prompt_diffs`・`_full_diff` |
| `plugins/ndf/src/ndf/domains/crossreview/verify_decide.py` | 新設 | 確かめる指摘を選び、指示を組み、回答を読み、下げるかを決める | `criteria_decide` | モジュール、`Verdict`、`Answers` | 新設: `select`・`instruction`・`read`・`lowers` |
| `plugins/ndf/src/ndf/domains/crossreview/verify.py` | 新設 | 読むだけの claude を 1 回起動して回答を返す | `verify_decide`・`participants` | モジュール、`VerifyRun`（Protocol） | 新設: `ask` |
| `plugins/ndf/src/ndf/domains/crossreview/criteria_decide.py` | 変更 | 指摘の種類の一覧と、相手役への `protect` の指示を持つ | `contracts` | モジュール | 新設: `protect_kinds`・`track_kinds`・`protect_of`・`document_line` / 変更: `reviewer_block`・`downgraded` |
| `plugins/ndf/src/ndf/domains/crossreview/journal.py` | 変更 | 行に `protect` と `check` を書き、下げる・戻すを決める | `criteria_decide`・`verify_decide` | `FindingJournal`、`Checked` | 新設: `apply_checks` / 変更: `enter`・`_raised`・`_raised_again`・`_revive` |
| `plugins/ndf/src/ndf/domains/crossreview/drive.py` | 変更 | 指摘を入れた直後に判定役を呼ぶ | `carry`・`reviewing`・`verify` | `_Run` | 新設: `_confirm` / 変更: `_round` |
| `plugins/ndf/src/ndf/domains/crossreview/report_decide.py` | 変更 | 下げた行の元の重要度と理由を表に出す | `criteria_decide` | モジュール | 変更: `_finding_rows` |
| `plugins/ndf/contracts/data/review-criteria.json` | 変更 | 判定役への指示の文と、指摘の種類の一覧を持つ | 無し | データ | — |
| `plugins/ndf/contracts/limits/cross-review.remark-chars.json` | 変更 | 読み手のモジュールを `reviewing` にする | 無し | データ | — |
| `plugins/ndf/contracts/limits/cross-review.remarks-total.json` | 変更 | 同上 | 無し | データ | — |
| `plugins/ndf/skills/cross-review/docs/01-state-and-review.md` | 変更 | 「指摘の基準」に確認の決まりを足す | 無し | 文書 | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_verify.py` | 新設 | 判定役の判断・起動・適用を確かめる | 上の各モジュール | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_journal.py` | 変更 | `protect`・下げた行の出し直しを確かめる | `journal` | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_report.py` | 変更 | 表の元の重要度と理由を確かめる | `report_decide` | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_criteria.py` | 変更 | 移した関数の参照名と、`protect` の指示 | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_citations.py` | 変更 | 移した関数の参照名 | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_documents.py` | 変更 | 移した定数の参照名 | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_prior.py` | 変更 | 移した定数の参照名 | — | テスト | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_finish.py` | 変更 | 差し替える関数の参照名 | — | テスト | — |

## 引き継ぎと相手役の起動の分離

満たす決定: 決定 1（判定役を足す場所を `drive.py` に空けるための移動）、受け入れ条件 1

振る舞いを変えない移動である。関数の中身は変えず、名前の先頭の `_` を外し、状態ファイルの保存だけを呼び手（`drive`）へ戻す。テストの変更は参照名の置き換えだけにする。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `carry` | `carry.py` | 変更 | `carry(run: CarryRun, prior: Prior) -> bool` | 前の実行が残した指摘を引き継ぎ、状態が変わったかを返す（保存は呼び手がする） |
| `restate` | `carry.py` | 変更 | `restate(run: CarryRun) -> bool` | 前の MVV で決めた判定を外し、外したかを返す |
| `detach_revived` | `carry.py` | 変更 | `detach_revived(run: CarryRun) -> None` | 出し直された指摘から古いスレッドの記録を外す |
| `review_round` | `reviewing.py` | 変更 | `review_round(run: ReviewingRun, round_no: int, prep: Prepared, remarks: Optional[Path], launch: Launcher) -> Reviewed` | 席を並べて起動し、起動し直しと席の埋めまで行う |
| `write_diff` | `reviewing.py` | 変更 | `write_diff(run: ReviewingRun, round_no: int, suffix: str, base: str, head: str) -> Path` | `base...head` の差分をラウンドのファイルへ書く |

`reader`・`read_prior`・`read_reviews`・`remarks`・`remarks_file`・`review_prompt` はシグネチャも責務も変えないので、地図に名前だけを置く。相手役の出力の形の文（`FINDINGS_SHAPE`）と定数 `REMARK_CHARS`・`REMARKS_TOTAL`・`RETRY_SUFFIX`・`REMARKS_SUFFIX` は `reviewing.py` へ、`RESOLVED_ELSEWHERE` は `carry.py` へ移す。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| 移した関数の全部 | 今の cross-review のテスト（417 関数）が、参照名の置き換えだけで通る | 無し | 無し |

## 指摘の種類と相手役の指示（criteria_decide）

満たす決定: 決定 2、受け入れ条件 1・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `protect_kinds` | `criteria_decide.py` | 新設 | `protect_kinds() -> tuple[str, ...]` | 保護の種類 5 つを表から返す |
| `track_kinds` | `criteria_decide.py` | 新設 | `track_kinds() -> tuple[str, ...]` | 指摘の種類の全部（保護の種類と `delivered`・`approval`）を返す |
| `protect_of` | `criteria_decide.py` | 新設 | `protect_of(value: object) -> tuple[str, ...]` | 値が文字列の並びなら、保護の種類だけを表の順で重ねずに返し、ほかは空を返す |
| `document_line` | `criteria_decide.py` | 新設 | `document_line(documents: bool) -> list[str]` | 文書を含む PR なら基準 6 の 1 行を返す（判定役の指示用） |
| `reviewer_block` | `criteria_decide.py` | 変更 | `reviewer_block(focus: Sequence[str], mvv: Optional[Snapshot] = None) -> list[str]` | 相手役へ `protect` の書き方の行を足す |
| `downgraded` | `criteria_decide.py` | 変更 | `downgraded(row: Mapping[str, Any]) -> bool` | 判定役の確認で下げた行（`check.lowered`）を「入れるときに下げた行」に数えない |

相手役の出力の形（`reviewing.FINDINGS_SHAPE`）に `"protect": [<当たる保護の種類>]` を足す。

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `kinds.protect` | 定数 | `contracts/data/review-criteria.json` | `secret`・`authz`・`user_data`・`irreversible`・`safety` |
| `kinds.other` | 定数 | 同上 | `delivered`・`approval` |
| `protect.reviewer` | 指示の文 | 同上 | 設計文書の「相手役の指示と出力」の文 |
| `verify` | 指示の文の並び | 同上 | 設計文書の「判定役の指示」と「回答の形」の要点 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `protect_kinds` | `criteria_decide._table` | `grep -rn "def .*protect\|\"protect\"" --include=*.py src/ndf` | 保護の種類を返す関数は無い。表の読み口 `_table` を使う |
| `track_kinds` | 同上 | 同上 | `_table` を使って作る |
| `protect_of` | `journal._located`（位置の形の検査） | 同上 | 形の検査の書き方をそろえる。対象の値が違うので別の関数にする |
| `document_line` | `criteria_decide.document_block` | `criteria_decide.py` を読んだ | `document_block` は文書の基準の全文を足すので、判定役には 1 行だけを返す関数を置く。文は同じ表の `document` から取る |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `protect_of` | `["secret", "user_data"]` がそのまま返る | 空の並び、同じ値の重なり、5 つ以外の値が混じる | 文字列・`null`・数は空を返す |
| `reviewer_block` | `protect` の行を含む | 無し | 無し（文言は照合しない。保護の種類 5 つが指示に現れることだけを見る） |
| `downgraded` | 入れるときに下げた行は真 | `check.lowered` が偽の `check` を持つ行は今のとおり | `check.lowered` が真の行は偽 |

## 判定役の判断（verify_decide）

満たす決定: 決定 1・2・3、受け入れ条件 1・3・4・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `select` | `verify_decide.py` | 新設 | `select(rows: Sequence[Mapping[str, Any]], use_tracks: bool = False) -> list[Mapping[str, Any]]` | このラウンドで新しく出たか戻った重い指摘のうち、判定役へ渡すものを返す |
| `instruction` | `verify_decide.py` | 新設 | `instruction(rows: Sequence[Mapping[str, Any]], diff: Path, worktree: Path, criteria: Sequence[str]) -> str` | 判定役への指示を組む。誰が書いた指摘か（`by`）は載せない |
| `read` | `verify_decide.py` | 新設 | `read(found: Optional[Sequence[Mapping[str, Any]]], ids: Sequence[str]) -> Answers` | 回答の並びを指摘ごとの `Verdict` にし、読めない分を注意にする |
| `lowers` | `verify_decide.py` | 新設 | `lowers(row: Mapping[str, Any], verdict: Verdict) -> bool` | 成り立たないと答えた指摘を minor へ下げてよいかを返す |

- `read`
  - 事前条件: `found` は出力の最後の `{"verdicts": [...]}` の要素（見つからなければ None）
  - 事後条件: `Answers.verdicts` は有効な回答と `holds` が null の回答だけを持つ。不正な回答・知らない番号・回答の無い番号は `Answers.warnings` に番号を載せる

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `select` | 行が `redline` を持つか `by` が NDF | 渡さない |
| `select` | 残りのうち `mvv` か `protect` を持つ行 | `use_tracks` が真か、下の行の指摘が 1 件でもあれば渡す。どちらも無ければ渡さない |
| `select` | ほかの行 | 渡す |
| `read` | `holds` が真で `condition`・`path`・`result` がすべて空でない | 有効 |
| `read` | `holds` が偽で `reason` が空でない | 有効 |
| `read` | `holds` が null | `Verdict(holds=None)`。`tracks` を残し、理由を注意にする |
| `read` | 上のどれでもない | 不正。`verdicts` に入れず注意にする |
| `lowers` | 行が `redline`・`mvv`・`by` = NDF・`protect` のどれかを持つ | 下げない |
| `lowers` | `tracks` に保護の種類があるか、種類の表に無い文字列がある | 下げない |
| `lowers` | ほかで `holds` が偽 | 下げる |

- 繰り返しの終わる条件: 回答の並びを 1 回なめるだけである

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Verdict(id, holds, condition, path, result, reason, tracks, unknown)` | 型 | `verify_decide.py` | `unknown` は `tracks` に種類の表に無い文字列があったか。`tracks` は表にある値だけ |
| `Answers(verdicts, warnings)` | 型 | `verify_decide.py` | `verdicts` は番号から `Verdict` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `select` | `journal.FindingJournal.handable` | `grep -rn "verdicts\|def confirm\b" --include=*.py src/ndf` | 判定役への振り分けは無い。`handable` は修正担当向けで条件が違う |
| `instruction` | `criteria_decide.criteria_lines`・`reviewer_block` | `criteria_decide.py` を読んだ | 基準の行は `criteria_lines` を呼び手が渡して使う。指示の骨組みは新しく書く |
| `read` | `participants.listed_of(text, "verdicts")` | `grep -rn "def listed_of" src/ndf` | 出力の最後の JSON を取り出すのは `listed_of` を `verify.ask` で使う。回答の意味の検査だけを新しく書く |
| `lowers` | `criteria_decide.effective_severity` | `criteria_decide.py` を読んだ | 登録のときの下げと、確認の後の下げは条件が違うので別の関数にする（設計文書の決定 2 の表の 2 段階） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `select` | 新しい major を渡す | MVV の指摘と `protect` の指摘だけなら `use_tracks` が偽で空、真で渡る。ほかの指摘が 1 件あれば一緒に渡る | `redline` と NDF の行は常に渡らない |
| `instruction` | 番号・本文・重要度・1 文・場所・差分のパスを含む | 1 文や場所の無い行 | 指摘の `by` の値が指示に現れない |
| `read` | 有効な 2 件を読む | `holds` が null で `tracks` を持つ | `found` が None・知らない番号・回答の欠け・3 つの文の欠け・理由の無い偽・`holds` が文字列 |
| `lowers` | 成り立たない普通の指摘は真 | `tracks` が `delivered`・`approval` だけなら真 | 保護の種類・知らない値・行の `protect`・`mvv`・`redline`・NDF は偽 |

## 判定役の起動（verify）

満たす決定: 決定 1・3、受け入れ条件 3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `ask` | `verify.py` | 新設 | `ask(run: VerifyRun, round_no: int, rows: Sequence[Mapping[str, Any]], diff: Path, launch: Launcher) -> verify_decide.Answers` | 読むだけの claude を 1 回起動し、回答を `Answers` にして返す。失敗は空の回答と注意にする |

- `ask`
  - 事前条件: `rows` は空でない。`run.input_dir`（この実行の資料のディレクトリ）に `diff` がある
  - 事後条件: 例外で駆動を止めない。起動し直さない
  - 副作用: claude を 1 回起動する（`participants.read_only_launch`。Tool は `participants.READ_TOOLS`、読める場所は worktree と資料のディレクトリ）。出力は状態の保存先の `round<n>-verify.*` に書く。過程の行と注意を `run.note` で足す

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `ask` | 起動が終わらない | 利用上限・無進捗・監視の上限・起動の拒否 | `launch` の返す状態が `done` でない | 既定値で続ける（どの指摘も下げない） | `warnings` に状態を 1 行。終了コードは変えない | 次のラウンドで新しく出た指摘だけがまた確かめられる |
| `ask` | 差分を読めない | claude の `permission_denials` に差分のファイルがある | `participants.denied_reads` | 既定値で続ける | `warnings` に差分のファイル名（絶対パスは過程にだけ） | 同上 |
| `ask` | 回答が読めない | 出力が無い・JSON でない・`verdicts` の配列が無い | `participants.claude_answer` と `listed_of` | 既定値で続ける | `warnings` に 1 行 | 同上 |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `ask` | 判定役へ渡した件数と回答の有効な件数 | 過程（結果 JSON の `process`） | 指摘の本文・回答の本文 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `ask` | `participants.read_only_launch`・`claude_answer`・`denied_reads`・`listed_of`、`seats._seat_launch` | `grep -rn "def listed_of\|def read_only_launch\|def denied_reads\|def claude_answer" src/ndf` | 起動・出力の読み取り・拒否の検出は 4 つをそのまま使う。`seats._seat_launch` は席の名前から相手役を選ぶので呼ばず、同じ `read_only_launch` を直に使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `ask` | 偽の起動が回答を返すと `Answers` になる。起動の引数が読むだけの Tool と読める場所の限定を持つ | `verdicts` が空の配列 | 起動の失敗・差分の読み取り拒否・JSON の無い出力で、`verdicts` が空で注意が 1 件。起動は 1 回だけ |

## ジャーナル（journal）

満たす決定: 決定 2・3、受け入れ条件 3・4・5（不変条件 I1〜I4）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `enter` | `journal.py` | 変更 | `enter(self, round_no: int, raised: Sequence[dict[str, Any]], items: Sequence[str] = (), mvv: Optional[str] = None) -> frozenset[str]` | 今の登録に加え、このラウンドで新しく出たか戻った行の番号を `self.fresh` に残す |
| `apply_checks` | `journal.py` | 新設 | `apply_checks(self, asked: Sequence[str], verdicts: Mapping[str, verify_decide.Verdict]) -> Checked` | 確認の結果を行に書き、下げる行を下げる |
| `_raised_again` | `journal.py` | 変更 | シグネチャは変えない | 出し直しの `protect` を足し、下げた行の重要度の扱いを分ける |
| `_revive` | `journal.py` | 変更 | シグネチャは変えない | 戻った行から `check` を外す（`protect` は残す） |

`_raised` は相手役の `protect` を `criteria_decide.protect_of` で通して行に足す（1 件も無ければキーを持たない）。

- `apply_checks`
  - 事前条件: `asked` は `verify_decide.select` が返した行の番号
  - 事後条件: 有効な回答（`holds` が真か偽）を持つ行は `check` を持つ。`holds` が null か回答の無い行は `check` を持たず、重要度が変わらない。どの場合も、回答の `tracks` のうち保護の種類は行の `protect` に足される。下げた行は `severity` が minor、`check.lowered` が真で、`raised_severity` を持つ

### 処理の手順

出し直し（`_raised_again`）は、行の `check.lowered` が真かで分かれる。

| 下げた行への出し直し | 重要度 | `check.lowered` |
| --- | --- | --- |
| 今回の `protect` が空でない | `at_least_blocking(raised_severity)` へ戻し、今回の値と重い方を残す | 偽 |
| 今回 `mvv` の項目か必ず人の承認が要る操作を挙げた | 今の `_classify` の決まりで固定する | 偽 |
| ほか | 変えない（I1） | 真のまま |

2 行目は設計文書の I1 に無い場合である。MVV に反する指摘を下げない受け入れ条件 5 を優先する（設計の承認で確かめる）。

下げる行に `raised_severity` が無い（前の実行から引き継いだ行が戻った）場合は、下げる前の重要度を `raised_severity` に書いてから下げる（I3）。

`apply_checks` が返す `Checked` の使い道は次のとおりである。

| 欄 | 中身 | 使い道 |
| --- | --- | --- |
| `lowered` | 下げた行の指摘のキー | 駆動が振動の判定のキーの集合から除く |
| `unconfirmed` | `asked` のうち有効な回答の無い行の番号 | この課題では使わない。#3780 が「確認できなかった指摘は進める」の判断に使う |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `fresh` | `list[str]` の属性 | `journal.FindingJournal` | 最後の `enter` の分だけ |
| `Checked(lowered: frozenset[str], unconfirmed: frozenset[str])` | 型 | `journal.py` | 空 |
| `PROTECT = "protect"`・`CHECK = "check"` | 行のキー | `journal.py` | 無し |

行の `protect` と `check` の形は設計文書の「データ構造」にある。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `apply_checks` | `_raised_again`・`merge_marks`・`criteria_decide.at_least_blocking` | `journal.py` を読んだ | 確認の結果を書く処理は無い。重要度の尺度の関数は `criteria_decide` を使う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `enter` | `protect` が行に残り、`fresh` に新しい行と戻った行だけが入る | 同じラウンドの 2 件の `protect` を合わせる | `protect` が文字列なら持たない |
| `apply_checks` | 成り立たないと答えた major は minor になり、`pending` に出ず、元の重要度が残る | 示せた回答は重要度が変わらない。`authz` と `user_data` の両方を持つ。`delivered` だけは下がる。元の重要度が critical・不明 | `holds` が null・回答の欠けは `check` が無く重要度が変わらず `unconfirmed` に入る。MVV・`redline`・`protect` の行は下がらない |
| `_raised_again` | 下げた行が minor のまま出し直されても minor（I1） | `protect` 付きの出し直しで元の重要度へ戻り `check.lowered` が偽 | 下げた行に MVV の項目を挙げた出し直しは固定した重さになる |
| `_revive` | 直した後の再発で `check` が消え、`protect` が残る。次の確認の `tracks` が空でも下がらない | 無し | 無し |

## 駆動への組み込み（drive）

満たす決定: 決定 1・3、受け入れ条件 3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_confirm` | `drive.py` | 新設 | `_confirm(run: _Run, round_no: int, diff: Path, launch: Launcher, use_tracks: bool = False) -> Checked` | このラウンドの新しい重い指摘を選び、判定役を呼び、ジャーナルへ適用する |
| `_round` | `drive.py` | 変更 | シグネチャは変えない | 登録の直後、読めない相手役の確認と収束の判定の前に `_confirm` を呼ぶ |

- `_confirm`
  - 事前条件: 直前に `run.journal.enter` を呼んでいる
  - 事後条件: 渡す指摘が無ければ判定役を起動せず、空の `Checked` を返す
  - 副作用: `verify.ask` が claude を起動する。注意を `run.warnings` に足す

`_round` は `enter` が返したキーの集合から `Checked.lowered` を除いたものを振動の判定の履歴（`run.history`）に足す。一部の相手役が読めなかったラウンドでも、読めた分は確かめる。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_confirm` | 回答が得られない | `verify.ask` の異常系 | `Answers.verdicts` が空 | 既定値で続ける | `verify.ask` の注意 | 今と同じ数の指摘が修正担当へ渡る |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_confirm` | `verify.ask`・`verify_decide.select`・`journal.apply_checks` | `grep -rn "def confirm\b" --include=*.py src/ndf`（駆動の側に同名は無い） | 3 つを順に呼ぶだけの関数にする |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_confirm`（`ndf cross-review run` を偽の起動で通す） | 成り立たない major だけのラウンドは、修正担当を起動せずに収束する。示せた major は修正担当へ渡る | `redline` の指摘だけ・MVV の指摘だけ・`protect` の指摘だけのラウンドは判定役を起動しない | 判定役が失敗したラウンドは今のとおり修正へ回り、警告が載る |

偽の起動（テストの `Fake`）は、指示に判定役の system の文を持つ claude の起動を判定役として扱う。

## 記録のコメント（report_decide）

満たす決定: 決定 1、受け入れ条件 3（不変条件 I3）

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_finding_rows` | `report_decide.py` | 変更 | シグネチャは変えない | `check.lowered` の行は、重要度の欄に「minor（確認の前は <元の重要度>）」、理由の欄に判定役の理由を出す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_finding_rows` | 下げた行の重要度の欄に元の重要度が、理由の欄に `check.reason` が現れる | 理由に表の区切り `\|` と改行がある | `check` の無い行・`lowered` が偽の行は今のとおり |

## 手順書

満たす決定: 決定 1・2・3、受け入れ条件 1

`skills/cross-review/docs/01-state-and-review.md` の「1 ラウンド」に判定役の確認のステップを、「指摘の基準」に重要度が決まる 2 段階（設計文書の決定 2 の表）と下げない指摘を足す。文の中身を照合するテストは書かない。
