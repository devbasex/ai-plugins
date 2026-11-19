# #3868: 開発の文書の書き方を、読み手と判断から決めたセクションの順へ直す（実装計画）

## 関連リンク

- 要求仕様: [issue-3868-requirements.md](./issue-3868-requirements.md)（正は #3868 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-3868-design.md](./issue-3868-design.md)。詳細設計は決定 6 により設計書の「どこを変えるか」に畳んである
- 書き直しの例: [不具合](./issue-3868-example-bug.md)・[機能追加](./issue-3868-example-feature.md)・[新規作成](./issue-3868-example-new.md)
- 設計 PR: https://github.com/devbasex/ai-plugins/pull/3874 （2026-11-20 マージ済み）
- モード: `standard`（conductor の判定。この計画はその実装のフェーズの「計画」の工程の出力）

この計画は、設計が決めた新しい実装計画の形（関連リンク → 修正対象 → タスク分解 → レビューの単位 → リファクタリングの時機 → リスクと対処 → 切り戻し手順 → 完了の定義）で書いた最初の実物である。目的・受け入れ条件・代替案は要求仕様と設計書が持つので、ここには書かない。

## 修正対象

設計書の「どこを変えるか」の表から引き写す。新設・変更する関数は無く、変えるのは Markdown の手順書と、移し替えの記録の JSON の文字列だけである。

| ファイル | 決定 |
| --- | --- |
| `plugins/ndf/skills/requirements-design/references/document-roles.md`（新設） | 1・2・6・8・10・16 |
| `plugins/ndf/skills/requirements-design/references/spec-template.md` | 2・3・5・10 |
| `plugins/ndf/skills/requirements-design/SKILL.md` | 2・3・10 |
| `docs/specifications/ndf-issue-items.md` | 10 |
| `scripts/ndf-next-parity/parity/features.json` | 10 |
| `plugins/ndf/skills/design/references/design-template.md` | 3・4・6・11 |
| `plugins/ndf/skills/design/references/deliverables.md` | 3 |
| `plugins/ndf/skills/design/references/decisions.md` | 4 |
| `plugins/ndf/skills/design/references/detailed-design.md`・`detail-template.md` | 3・6・12 |
| `plugins/ndf/skills/design/references/domain-model.md`・`external-survey.md` | 3・4 |
| `plugins/ndf/skills/design/SKILL.md` | 4・6・7 |
| `plugins/ndf/skills/implementation-plan/SKILL.md` | 1・5・6・14・15・16 |
| `plugins/ndf/skills/implementation-plan/references/pre-refactoring.md` | 15 |
| `plugins/ndf/skills/issue-plan-strategy/references/execution-plan.md`・`parallel-plan.md` | 1・13 |
| `plugins/ndf/skills/development-workflow/SKILL.md`・`references/stage-notes.md` | 6 |
| `plugins/ndf/skills/quality-gates/references/design-match.md` | 6・7・16 |
| `plugins/ndf/skills/development-workflow/references/approval-request.md` | 4・7 |
| `plugins/ndf/skills/pr/SKILL.md` | 4・7 |
| `plugins/ndf/skills/document-rebuild/SKILL.md`・`references/related-skills.md`・`references/common.md` | 4・8 |

`plugins/ndf/skills/requirements-design/references/glossary-format.md` は表に無いが足した。設計書の用語の表をドメインモデルの小見出しから単独の「用語」のセクションへ移したので（Task 2）、用語チェックが当たるセクションの説明を同じ名前に付け替えるためである。

設計書の「設計の結果」に挙がる用語集の 3 ファイル（`docs/glossary/glossary.json`・`docs/glossary.md`・`docs/glossary/ndf-design.md`）は、設計 PR #3874 で 5 語を足し終えているので、この実装では触らない。

## タスク分解

依存の向きは Task 1 → 2・3 → 4 → 5 である。Task 1 が新設する `document-roles.md` を後のタスクが指すため、Task 1 を先に終える。どのタスクも Markdown の手順書だけを変えるので、テスト駆動を適用しない（`.md` の文言を照合するテストは書かない。確かめは完了の定義の grep と CI の検査が持つ）。

### Task 1: 文書の役割の表を新設し、要求仕様のテンプレートと記載項目を新しい並びにする

- **対象ファイル:** `document-roles.md`（新設）・`spec-template.md`・`requirements-design/SKILL.md`・`ndf-issue-items.md`・`features.json`
- **変更内容:** 設計書の「文書の役割の表の中身」の 3 つの表（文書 × 読み手・判断・読み方・順序、変更の種類ごとの中心、種類ごとの見出しの順）とラベルの必須の項目の規則、「省いてよい文書と条件」の 2 つの表と箇条、言い直しの問いと照合先（決定 8 の表）、どの文書にも当たる書き方を `document-roles.md` に置く。仕様のテンプレートを種類ごとの見出しの順にし、セクションに「必ず書く／当たるときだけ」を付け、頭に変更の種類を決めるステップとラベルの必須の項目の規則を書き、「リクエスト（原文）」を「依頼（原文）」へ改名する。記載項目の定義に決定 10 の表の「足す（該当時）」の項目を足し、`features.json` の 2 行の `feature` に戻すときの責任を書き足す
- **満たす受け入れ条件:** 2・3・4・8

### Task 2: 設計書・詳細設計のテンプレートと `design` の手順を新しい並びにする

- **対象ファイル:** `design-template.md`・`deliverables.md`・`decisions.md`・`detailed-design.md`・`detail-template.md`・`domain-model.md`・`external-survey.md`・`design/SKILL.md`
- **変更内容:** 設計書のテンプレートを 要約の段落 → 今の姿 → 文脈と制約 → 解き方の全体像 → ［当たるとき］→ 決定の記録 → 影響と危険 → … の順にする（決定 4・11）。「承認する人が読む欄」は名前を残して中身を新しい欄にし、今の欄は「`legacy-refactor` の書き方」へ移す。要否表の `standard` の必ず書くセクションを 8 つにする（決定 3）。決定の形を 問い・選んだ案・採らなかった案・代償・（作り方・比べた候補）・根拠 にする（決定 4）。詳細設計を別ファイルにする条件（決定 6）と、地図 → 構成要素ごとの順・満たす決定の行・当たる関数だけの事前条件（決定 12）にする。`design` の SKILL.md に書く順・詳細設計の条件・まとめる条件（決定 7）を書く
- **満たす受け入れ条件:** 3・4・5・8（コードが読む 7 つの見出しを残す）

### Task 3: 実装計画と開発計画の形を直す

- **対象ファイル:** `implementation-plan/SKILL.md`・`pre-refactoring.md`・`execution-plan.md`・`parallel-plan.md`
- **変更内容:** 実装計画のフォーマットから要求と設計に既にあるセクションを消し、前の文書へのリンクと受け入れ条件の番号で指す形にする（決定 5）。種類ごとの最初のタスク、詳細設計書が無いときの「修正対象」の作り方（決定 6）、「レビューの単位」（決定 14）と「リファクタリングの時機」（決定 15）の欄、「必要なケース」「不要なケース」を `document-roles.md` を指す 1 行に、設計書を省いたときの置き場と頭の行（決定 16）を書く。`pre-refactoring.md` に準備の行へ書くことを足す。実行計画に `目標:` の行（決定 13）と、2 つの計画の冒頭に読み手と判断の行を置く
- **満たす受け入れ条件:** 2・4

### Task 4: 文書を読む側の手順（工程・完了判定・承認資料・PR）を新しいセクションに付け替える

- **対象ファイル:** `development-workflow/SKILL.md`・`stage-notes.md`・`design-match.md`・`approval-request.md`・`pr/SKILL.md`
- **変更内容:** 工程「詳細設計」を別ファイルか「どこを変えるか」へ畳むかを決定 6 の条件で選ぶ形にし、畳んだときの完了とレビュー対象を書く。完了判定に「どこを変えるか」の照合・文書を省いた変更の照合と差し戻し・名前に含む番号のどれかで設計文書を探す読み方を書く（決定 6・7・16）。承認資料の「判断に使うもの」の出所を決定 4 の表に付け替え、`legacy-refactor` の分岐と設計文書が 2 本以上の組み方（決定 7）を書く。`pr` の設計 PR の本文が指すセクション名と Summary の並べる順を直す
- **満たす受け入れ条件:** 8

### Task 5: 読み手役の言い直しと固定の章の一覧を直す

- **対象ファイル:** `document-rebuild/SKILL.md`・`related-skills.md`・`common.md`
- **変更内容:** 読み手役の確認に、要求仕様と設計書のときの言い直しの問い・照合先・合否・読ませるもの（設計書には組になる要求仕様のコピー）・最大 2 回の読み直しと不合格の報告（決定 8）を書く。問いと照合先の表は `document-roles.md` を指し、写さない。固定の章の一覧（K3）を新しい「承認する人が読む欄」に書き換える（決定 4）
- **満たす受け入れ条件:** 6

## レビューの単位

課題は 1 つだが、見込みの差分が 1,000 行を超えるので、依存の向きで 2 つに分ける（決定 14 の手順 2）。数えないパスは無い（再生成のファイルとロックファイルを触らない）。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 書く側（文書の役割の表と各テンプレート） | Task 1・2・3 | 2・3・4・5 | 約 800 | 1,272（分け直した。下の表） |
| 読む側（工程・完了判定・承認資料・PR・読み手役） | Task 4・5 | 6・8 | 約 400 | 184 |

書く側の実際の行数が 1,000 行を超えたので、実装の後に依存の向きのまま分け直した（`git show --shortstat` でコミットごとに数えた和）。

| 単位 | 含むタスク（コミット） | 満たす受け入れ条件 | 実際の行数 |
| --- | --- | --- | --- |
| 1 文書の役割の表と要求仕様 | Task 1（b6cffc5b4）と用語チェックの記述の付け替え（53211f7fd） | 2・3・4・8 | 388 |
| 2 設計書と詳細設計 | Task 2（915bbc1c6） | 3・4・5・8 | 668 |
| 3 計画と、文書を読む側 | Task 3・4・5（1df198b80・cc9f560bb・5c4c7b0ff） | 2・4・6・8 | 400 |

見込みが外れたのは、設計書のテンプレート（`design-template.md`）が並びの入れ替えで 295 行動いたためである。PR は 1 本で出す（単位ごとに PR を分けるかは #3879 が決める）。コードレビューでは単位ごとにコミットの範囲を分けて読める。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触るのは Markdown の手順書だけで、`pre-refactoring.md` の測れる基準（循環・責務の混在）が当たるコードを触らない。行数の上限（500 行）に最も近い `development-workflow/SKILL.md` は 450 行で、Task 4 の追記で超えるなら追記を `stage-notes.md` へ置く |
| 実装の中 | 各タスクで行う | 同じ規則を 2 か所に書いていないかを、タスクの終わりに `document-roles.md` と照らす |
| スプリントの終わり（cross-refactoring） | 規則の重複（`document-roles.md` と各テンプレート・`approval-request.md` と `design-template.md` の欄の一覧） | 複数のタスクが同じ規則（必ず書く／当たるときだけ・省いてよい条件）を指すので、写しが残りやすい |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| コードが読む見出し（種類・影響・公開インタフェース・設計の結果・決定の記録・テスト設計・構成要素・採らなかった案）を書き換えで落とす | タスクごとにテストを通す。Task 1・2 の終わりと完了判定で、テンプレートの中を `grep` して残っていることを確かめる |
| 同じファイルを別のレーン（lclose の #3869・lrelay の #3867）が触る | `main` へ追従してから PR を出す。#3869 が先に入っていれば、`spec-template.md` の「リリース後の確認の計画」を「受け入れ条件」の後ろへ置き直す |
| `development-workflow/SKILL.md` が 500 行を超える | 追記を `stage-notes.md` へ置き、SKILL.md には 1 行の参照だけを残す |

## 切り戻し手順

- 変更は Markdown の手順書と JSON の文字列だけで、データの移行は無い。PR のマージコミットを revert すれば元の形へ戻る

## 完了の定義

- [ ] 受け入れ条件 2〜6・8 を、条件ごとに確かめた手段と結果を PR の本文に対応づけて書いた（1 と 7 は設計 PR で満たした）
- [ ] コードが読む 7 つの見出しと「採らなかった案」の行の書き出しが、新しいテンプレートの中に `grep` で見つかる
- [ ] 仕様のテンプレートの見出しが、すべて `ndf-issue-items.md` の項目の一覧にある
- [ ] CI の必須のチェックが通る（`check-skill-frontmatter`・`check-markdown-links`・`check-doc-line-limit` を含む）
- [ ] 実装の差分のファイルが「修正対象」と一致する（`git diff --name-only origin/main...HEAD`）
