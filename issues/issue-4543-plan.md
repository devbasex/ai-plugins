# #4543: 設計書と Pull Request の概要で、現状と改善の案を書き分ける

これまで、AI が書く設計書と Pull Request（PR）の概要は、今の状態も変える内容も現在形で書かれることがあった。この実装では、書き分けの表を文書の書き方の基準に小見出しとして足し、手順書からそこを指す。書き分けが要らないと分かる指示からは、プログラムで小見出しを外して渡すようにする。

## 関連リンク

- 要求仕様: [issue-4543-requirements.md](./issue-4543-requirements.md)（正は https://github.com/devbasex/ai-plugins/issues/4543 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4543-design.md](./issue-4543-design.md)。詳細設計は [issue-4543-design-detail.md](./issue-4543-design-detail.md) にある。下の「決定」はこの設計書の「決定の記録」の番号である
- モード: `standard`（機能追加で、AI への指示を組むプログラムと、配る手順書を変えるため）

## 設計承認の結果と、決定 7 についての判断

[設計 PR 4703](https://github.com/devbasex/ai-plugins/pull/4703) は、コミット `09167d277` を利用者が承認して 2026-11-28 にマージした。承認資料は「このまま承認すると、2 件は直さないまま実装へ進みます」と示していた。そのため、次の 2 件は要求と設計の今の記述のまま実装する。

- 受け入れ条件 3 で確かめる対象の PR は絞らない
- PR の本文から承認資料へ写した行の扱いは変えない（工程を止めて、PR の本文の直しを求める）

承認の後に足した詳細設計と決定 7 の直しは、[PR 4730](https://github.com/devbasex/ai-plugins/pull/4730)（2026-11-28 時点で未マージ）にある。その AI によるレビューは上限の回数で止まり、決定 7（要求仕様のコピーへの指摘の扱い）に 6 件が残った（[レビューの記録](https://github.com/devbasex/ai-plugins/pull/4730#issuecomment-7232350264)）。

利用者は 2026-11-28 に、次のとおり判断した。この判断は決定 7、詳細設計の「cross-review の指示」、この計画に反映してあり、Task 6 はほかのタスクと同じように着手できる。

- 修正担当は、要求仕様のコピーのうち課題の本文から写した範囲への指摘では、課題の本文、コピー、PR の本文を書き替えない。直す案を添えて、利用者の判断が要る指摘（`needs_user`）で返す
- 実装計画など、コピーにだけあるセクションへの指摘は、そのセクションを直接直す
- 本文とコピーを自動でそろえるコマンドは作らない。課題も起票しない

残っていた 6 件は、この扱いではどれも起きない。

| 指摘 | 中身 | この扱いで起きない理由 |
| --- | --- | --- |
| r1-1・r2-1・r3-1 | 修正担当が課題の本文と PR の本文を、人の承認なしに書き替える | 修正担当は本文を書き替えない |
| r2-2 | 同時の書き込みで、ほかの書き込みが消える | 書き込みが無い |
| r2-5 | 本文の切り出し、組み立て、比較が AI 任せになる | 本文は範囲の見分けに読むだけで、組み立てない |
| r2-6 | 中断した後に再開できない | 途中の状態が残る書き込みが無い |

## 修正対象

詳細設計書の「地図」から引き写した。パスは `plugins/ndf/` からの相対パスである。「足す」と書いた行は、地図の表に無いファイルである。

| ファイル | 変える中身 | タスク |
| --- | --- | --- |
| `contracts/data/writing-criteria.md` | 3 章の末尾に小見出し「現状と改善の案の書き分け」を足し、目印のコメントで囲む | 2・3 |
| `src/ndf/contracts/writing.py` | 目印の定数と、目印で囲まれた範囲を外す関数を足す | 3 |
| `src/ndf/domains/records/reading.py` | 指示の JSON の任意のキー `distinction` を受け取る | 3 |
| `skills/document-rebuild/SKILL.md` | `distinction` を `false` にする文書を足す | 3 |
| `src/ndf/core/worker.py`、`contracts/worker/kinds.json` | 4 つの作業にだけ小見出しを渡す | 4 |
| `src/ndf/contracts/worker_kinds.py`（足す） | 作業の定義のキーの説明に `distinction` を足す。詳細設計の「作業の AI への指示」が定めているためである | 4 |
| `src/ndf/core/issue_docs.py`、`src/ndf/domains/docrebuild/drive.py` | 要求仕様のコピーの名前の形を `issue_docs.py` の 1 か所にする | 5 |
| `src/ndf/domains/crossreview/drive.py`、`src/ndf/domains/crossreview/fixer.py` | 小見出しの渡し分けと、要求仕様のコピーの扱いの指示 | 5・6 |
| `contracts/data/review-criteria.json` | 要求仕様のコピーへの指摘の扱いの文 | 6 |
| `src/ndf/domains/crossreview/criteria_decide.py` | 上の文を読み出す関数。指摘の基準の表を読むのがこのモジュールで、`fixer.py` から表を直接読まないため（今の `fixer_ci_only` と同じ形） | 6 |
| `skills/design/references/design-template.md`、`skills/pr/SKILL.md`、`skills/requirements-design/SKILL.md`、`skills/development-workflow/references/approval-request.md`、`skills/decision-request/SKILL.md`、`skills/document-style/SKILL.md` | 小見出しを指す指示を足す | 7 |
| `tests/contracts/test_writing.py`、`tests/domains/records/test_reading.py`、`tests/worker/test_rules.py`、`tests/domains/crossreview/test_crossreview_documents.py`、`tests/core/test_issue_docs.py`（足す） | 理由は、設計書の「テスト設計」と詳細設計の「単体テストの観点」をテストで確かめるためである | 1・3〜6 |

## タスク分解

着手の前に `main` を取り込む。この計画を書いた後の `main` では、`crossreview/drive.py`、`crossreview/fixer.py`、`core/worker.py` が変わっている。

テストはこのリポジトリの設定（`.ndf/project.json` の `test.ci.only`）に従い、手元では打たず、PR の CI で合否を読む。静的解析は手元で打ってよい。文書の文言を照合するテストは書かない。

### Task 1: 今の渡し方を固定する退行テストを置く

- **対象ファイル:**
  - `tests/contracts/test_writing.py`
  - `tests/domains/records/test_reading.py`
  - `tests/worker/test_rules.py`
  - `tests/domains/crossreview/test_crossreview_documents.py`
  - `tests/core/test_issue_docs.py`
- **変更内容:** 読み手役への指示に型の章が入ること、文書を書く作業と cross-review の指示に基準の全文が入ることを、3 つの経路の出力で確かめる。今のテストが確かめている振る舞いは足さない
- **満たす受け入れ条件:** 5
- **進め方:** 今の振る舞いを縛るテストを足し、製品のコードを変えずに通ることを CI で見る

### Task 2: 書き分けの小見出しを基準に足し、読み手役の指示に入れる

- **対象ファイル:** `contracts/data/writing-criteria.md`
- **変更内容:** 設計書の「どこを変えるか」の「書き分けの小見出しに書くこと」の表のとおりに、`### 現状と改善の案の書き分け` を 3 章の末尾に足す（決定 1〜4）。見出しは `### ` にする。小見出しは 1,700 字以内に収め、例は短い 1 組にする。足した字数を実装の PR の本文に書く
- **満たす受け入れ条件:** 1・2
- **進め方:** 文書だけの変更なのでテストを先に書かない。型の章の切り出しに小見出しが入ることは、今のテストと Task 3 のテストで確かめる

### Task 3: 読み手役の指示で、小見出しを渡し分ける

- **対象ファイル:**
  - `contracts/data/writing-criteria.md`
  - `src/ndf/contracts/writing.py`
  - `src/ndf/domains/records/reading.py`
  - `skills/document-rebuild/SKILL.md`
  - `tests/contracts/test_writing.py`
  - `tests/domains/records/test_reading.py`
- **変更内容:**
  - 小見出しの前後に `<!-- ndf-distinction-begin -->` と `<!-- ndf-distinction-end -->` を置く
  - `writing.py` に、目印の定数と、本文から目印で囲まれた範囲を除いて返す関数を足す。関数は見出しの文を持たない（決定 6）
  - `reading.py` の `prompt` は、任意のキー `distinction`（真偽。省けば真）を受け取り、偽なら小見出しを外す
  - `document-rebuild` の SKILL.md の `prompt` の JSON の説明に、変更履歴と、現状か案の片方しか書かない文書では `false` にすると足す
- **満たす受け入れ条件:** 2・5
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。テストは、`distinction` が無ければ小見出しがあること、`false` なら無いこと、小見出しの見出しの文を変えた本文でも外れることを見る。配っている基準から目印を消すと落ちる形にする。目印が対にならない本文で `CriteriaBroken` になることと、真偽でない `distinction` を拒むことも見る

### Task 4: 作業の AI への指示で、小見出しを渡し分ける

- **対象ファイル:**
  - `contracts/worker/kinds.json`
  - `src/ndf/core/worker.py`
  - `src/ndf/contracts/worker_kinds.py`
  - `tests/worker/test_rules.py`
- **変更内容:** `design`・`design-detail`・`design-check-fix`・`requirements` の定義にだけ、小見出しを渡す値を足す。`kinds.json` の `summary` と `worker_kinds.work_kinds` の docstring にそのキーの説明を足す。ほかの作業へ渡す基準の全文からは小見出しを外す（決定 6）
- **満たす受け入れ条件:** 1・5
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 5: cross-review の指示で、小見出しを渡し分ける

- **対象ファイル:**
  - `src/ndf/core/issue_docs.py`
  - `src/ndf/domains/docrebuild/drive.py`
  - `src/ndf/domains/crossreview/drive.py`
  - `src/ndf/domains/crossreview/fixer.py`
  - `tests/domains/crossreview/test_crossreview_documents.py`
  - `tests/core/test_issue_docs.py`
- **変更内容:**
  - 要求仕様のコピーの名前の形を `issue_docs.py` に置き、`docrebuild/drive.py` の `REQUIREMENTS_FILE` の定義をやめてそこを参照する
  - PR の変更に、課題の設計書も要求仕様のコピーも無ければ、レビュー担当と修正担当の指示の基準の全文から小見出しを外す。呼ぶ側で外すので、`document_block` のシグネチャは変えない
- **満たす受け入れ条件:** 5
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。設計書がある、要求仕様のコピーだけがある、どちらも無い、の 3 つの変更で指示を組んで確かめる。子のプロセスは起動しない。要求仕様のコピーを名前で見分ける関数は `test_issue_docs.py` で確かめる。`drive.py` への追加は 6 行以内にする（下の「リスクと対処」）

### Task 6: 修正担当の指示に、要求仕様のコピーへの指摘の扱いを足す

- **対象ファイル:**
  - `contracts/data/review-criteria.json`
  - `src/ndf/domains/crossreview/criteria_decide.py`
  - `src/ndf/domains/crossreview/fixer.py`
  - `src/ndf/domains/crossreview/drive.py`
  - `tests/domains/crossreview/test_crossreview_documents.py`
- **変更内容:** 決定 7 の扱い（詳細設計の「cross-review の指示」の、指摘された場所ごとの扱いと直す案の書き方）を `review-criteria.json` に置き、PR の変更に要求仕様のコピーがあるときだけ修正担当の指示に足す。JSON の `summary` にも、足したキーの説明を書く。指示の 1 行目は変えない
- **満たす受け入れ条件:** 5（決定 7。受け入れ条件には対応する番号が無い）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。コピーがあるときだけ扱いの文が指示に入ることを見る

### Task 7: 手順書から小見出しを指す

- **対象ファイル:**
  - `skills/design/references/design-template.md`
  - `skills/pr/SKILL.md`
  - `skills/requirements-design/SKILL.md`
  - `skills/development-workflow/references/approval-request.md`
  - `skills/decision-request/SKILL.md`
  - `skills/document-style/SKILL.md`
- **変更内容:** 設計書の「どこを変えるか」の表の各行のとおりに指示を足す（決定 1・3・5）。表は写さず、小見出しを指す。仕組みが読み取る見出しは変えない。承認資料の手順書では、PR の本文から写した行の扱いを変えない
- **満たす受け入れ条件:** 1・4・5
- **進め方:** 文書だけの変更なのでテストを書かない。次を確かめる
  - 参照切れの検査を打つ（`python3 scripts/check-markdown-links.py --root .`）
  - 行数の検査を打つ（`python3 scripts/check-doc-line-limit.py --root .`）
  - 差分の `#` の行の変更が小見出しの追加だけであることを見る

### Task 8: 読み手役に例を読ませて確かめる

- **対象ファイル:** 変えない（結果が期待と違えば `contracts/data/writing-criteria.md` の小見出しを直す）
- **変更内容:** 設計書の「テスト設計」の「読み手役に読ませる例」と、要求仕様の書き分ける前と後の引用を、1 つずつ別のファイルにして別々の読み手役に読ませ、「期待する結果」の表と突き合わせる。要求仕様の全体も読ませ、引用が挙がらないことを見る。指示は作業場所の `plugins/ndf/bin/ndf records reading prompt` で組み、直した基準が入っていることを先に確かめる。結果は実装の PR に書く
- **満たす受け入れ条件:** 2
- **進め方:** 手で確かめる。CI と自動テストでは読み手役を起動しない。実装を担当する worker は別の AI を起動できないので、読み手役の起動は supervisor か conductor が行う

## レビューの単位

課題は 1 つで、見込みの差分は 1,000 行を超えないので、1 つの単位にする。行数は追加と削除の和で、`issues/` を数えない。受け入れ条件 3 はリリースの後に人が確かめるので、単位に含めない。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 現状と案の書き分け | Task 1〜8 | 4543:1・2・4・5 | 約 400 | 285（`main` との差分） |

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 2026-11-28 の `main` で測った行数は、`crossreview/drive.py` 994、`core/worker.py` 980、`docrebuild/drive.py` 537、`records/reading.py` 446、`worker_kinds.py` 286、`crossreview/fixer.py` 270、`writing.py` 35、`issue_docs.py` 30。1,000 行に近い 2 つを変えるタスクは、`drive.py` が 8 件中 2 件、`worker.py` が 1 件。テストの数は `test_crossreview_fixer_sessions.py` 20、`test_rules.py` 15、`test_reading.py` 14、`test_crossreview_documents.py` 12、`test_writing.py` 5、`test_issue_docs.py` 4。足すのは値と条件の分岐が数行ずつで、構造は変えない |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | 3 つの経路の「基準の本文を受け取り、条件で小見出しを外す」処理 | `reading.py`・`worker.py`・`crossreview` の 3 か所が同じ形の処理を持つため、まとめられるかを見る |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `crossreview/drive.py`（`main` で 994 行）と `core/worker.py`（980 行）は、ファイルの行数の安全網（1,000 行を超えると検査が落ちる）に近い | `drive.py` への追加は、値の 2 つと分岐を合わせて 6 行以内にする。Task 5 まで進めた実装の作業場所では 998 行で、Task 6 で足せるのは 2 行である。収まらないときは、上限に合わせて行を詰めたり切ったりせず、実装を止めて、責務で分けるリファクタリングを先に通すかの判断を仰ぐ |
| `approval-request.md` は 487 行で、Markdown の分割の基準（500 行まで）に近い | 足すのは 2〜3 行にし、直し方は `document-rebuild` の SKILL.md を指す |
| [テンプレートの形をそろえる PR 4548](https://github.com/devbasex/ai-plugins/pull/4548)（未マージ）が、手順書 6 ファイルのうち設計書のテンプレート・PR・要求・承認資料の手順書と、`kinds.json`・`document-rebuild` の SKILL.md を変える | 着手の時に状態を読む。先にマージされていれば main を取り込み、「承認する人が読む欄」が `design-writing.md` へ移っていれば、そちらへ指示を足す |
| 要求仕様の写した範囲への指摘は、文の直し 1 件でも利用者の判断を待ち、工程が止まる（決定 7 の代償） | 直す案（課題の番号、見出し、直す前と後の文）を見送りの理由に書かせ、利用者が理由だけで決められるようにする |
| 読み手役の指摘が期待どおりに出ない | 小見出しの言い回しを直して読ませ直す。中身（設計書の表）を変える必要があれば、設計へ戻す |

## 切り戻し手順

- 実装の PR のマージを revert すれば、これまでの指示に戻る。データの移行は無い。この変更の後に書かれた設計書と課題の本文は、書き分けたまま残る

## 完了の定義

- [ ] 受け入れ条件 1・2・4・5 を満たし、条件ごとに検証手段と結果が対応している（1・4 は手順書の差分、2 は Task 8 の結果、5 は見出しの差分と CI の全体テスト）
- [ ] CI のテストと静的解析が通っている
- [ ] 受け入れ条件 3 は、この変更を含むバージョンを出した後に確かめる（要求仕様の「リリース後の確認の計画」。対象の PR は絞らない）
