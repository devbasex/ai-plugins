# #3412: #1142 の作り直しの仕様を確定仕様 6 本へ移し、入れ替えの前後を同じ物差しで測る（実装計画）

## 関連リンク

- 要求仕様: [issue-3412-requirements.md](./issue-3412-requirements.md)（正は #3412 の本文）。受け入れ条件は `3412:<番号>` で指す
- 設計書: [issue-3412-design.md](./issue-3412-design.md)、詳細設計書: [issue-3412-design-detail.md](./issue-3412-design-detail.md)
- モード: `standard`（設計書の「影響と危険」の判定。配らない文書とスクリプトだけを変えるが、触るファイルが 20 を超え、新設する関数が 8 ある）
- 範囲の外: `plugins/ndf/` のコメント・README・JSON の説明の書き換え（詳細設計書の手順 5 のうち `plugins/ndf` の分と、手順 6 のうち `plugins/ndf` の分）は #3960 が持つ。計画 p9 のオーガナイザーが ls3-spec の触る場所から外した（2026-11-21）。この計画のタスクは `plugins/ndf/` を触らない

## 修正対象

詳細設計書の「地図」から引き写す。`plugins/ndf/…` の行は #3960 へ移したので除く。

| ファイル | 変える中身 |
| --- | --- |
| `docs/specifications/ndf-rebuild.md`（新設） | 目的・構造・入れ替え・子の記号の表・番号の対応表（詳細設計書の手順 2〜4） |
| `docs/specifications/ndf-rebuild-libraries.md`（新設） | 設計書の「移す先の対応表」で `libraries` へ移す文書の決定 |
| `docs/specifications/ndf-rebuild-data-and-smells.md`（新設） | 同じく `data-and-smells` へ移す文書の決定 |
| `docs/specifications/ndf-rebuild-scripted-flow.md`（新設） | 同じく `scripted-flow` へ移す文書の決定。決定 65 を 130 へ振り直す |
| `docs/specifications/ndf-rebuild-measure.md`（新設） | 物差し・前後の値・打ったコマンド（測り直しの結果は Task 11） |
| `docs/specifications/ndf-rebuild-review-scope.md`（新設） | 同じく `review-scope` へ移す文書の決定 |
| `docs/specifications/ndf-knowledge-and-kiro.md` | 「仕様化の扱い」を `issues/README.md` へ向ける |
| `docs/specifications/README.md` | 索引に 6 本を足す |
| `issues/README.md` | 完了後の扱い（確定仕様へ移した後に `ndf records spec finalize` で消す。`old/` に新しく足さない） |
| `docs/glossary/glossary.json`・`docs/glossary/ndf-rebuild.md` | `ndf records spec finalize` と `ndf records glossary render` が書く。語の定義の本文の旧いファイル名は正本の JSON で直す |
| `scripts/measure/ai-branching.py` | `load_rules`・`load_measures`・`count_choices`・`summarize`（新設）、`count_progress`・`count_ledgers`・`scan_transcript`・`count_transcripts`・`main`（変更。`--until` と時差つきの両端） |
| `scripts/measure/ai-branching-rules.json` | 規則に `rule`（`R` と数字）と `form`（`before` / `after`）を持たせ、後の形の規則を足す |
| `scripts/measure/ai-branching-measures.json`（新設） | 規則の番号 → G1〜G3 と形ごとの出所 |
| `scripts/measure/claude-p-usage.py` | `count_launches`・`plan_cost`（新設）、`main`（副命令 `launches`・`plan-cost`） |
| `scripts/token-usage.py` | `main`（`--since`、`--until` を `t >= until` で切る）・`unique_prs`（新設）・`aggregate`（変更）・`--sessions-out` |
| `scripts/check-markdown-links.py` | `removing_failures`（新設）・`main`（`--removing`） |
| `scripts/tests/test_ai_branching.py`（新設）・`test_claude_p_usage.py`・`test_token_usage.py`・`test_check_markdown_links.py` | 詳細設計書の「単体テストの観点」 |
| `scripts/` の作り直しの決定を指すコメント | 手順 5 のうち `scripts` の分（`scripts/ndf-next-parity/` を除く 76 行から、作り直しの決定を指す行） |
| `issues/issue-1142-*`・`issue-3347-*`・`issue-3395-*`・`issue-3388-*`（36 本） | `ndf records spec finalize` で消す |

## タスク分解

機能追加なので、各スクリプトの最初のタスクは今の振る舞いを縛る退行テストから始める（Task 1・3・5 の頭）。確定仕様の側（Task 7〜10）は文書の移しで、テスト駆動を当てない。代わりに要求仕様の検証手段のコマンド（番号の一意の `grep`・参照の検査の 1 と 2）を終わりの条件にする。テストは CI で走らせる（`.ndf/project.json` の `test.ci.only`。合否は単位の PR の `gh pr checks` で読む）。

### Task 1: `check-markdown-links.py` の `--removing`

- **対象ファイル:** `scripts/check-markdown-links.py`、`scripts/tests/test_check_markdown_links.py`
- **変更内容:** `--removing <パス>`（繰り返し）を受け、`git ls-files '*.md'` から `--removing` を除いた全件を読み、（a）消すファイルへ解決する相対リンクと、（b）消すファイルを 40 桁の SHA でない ref で指す GitHub のリンクを `文書:行: (a|b) 宛先` で挙げる。引用の中は読み、コードの中は読まない。当たりがあれば終了コード 1。`--removing` を付けない今の検査は変えない
- **満たす受け入れ条件:** 3412:5・3412:11（参照の検査の 2 の道具）
- **進め方:** 今の 52 件のテストが通ることを確かめる → 失敗するテスト（`../issues/…` と `blob/main/issues/…` を挙げる、40 桁の SHA とコードの書式を挙げない、引用の中を挙げる）→ 最小実装 → 整理

### Task 2: 規則の番号と形を持つ規則の表

- **対象ファイル:** `scripts/measure/ai-branching-rules.json`、`scripts/measure/ai-branching-measures.json`（新設）、`scripts/measure/ai-branching.py`（`load_rules`・`load_measures`）、`scripts/tests/test_ai_branching.py`（新設）
- **変更内容:** 規則の各行に `rule`・`form`・`kind` を持たせ、後の形（11 系のコマンド）の規則を足す。`ai-branching-measures.json` に規則の番号ごとの `measure` と出所 `before`・`after` を書く（詳細設計書の「規則ごとの出所」）。モジュールの頭の読み込みを `load_rules` へ移して確かめを足す
- **満たす受け入れ条件:** 3412:7（規則の表）
- **進め方:** 退行テスト（今の規則の表で今の `main` の出力の形が変わらない）→ 失敗するテスト（`rule` の無い行で終了コード 2、規則 0 行の形）→ 実装 → 整理

### Task 3: 前後の形を同じ規則の番号で数える

- **対象ファイル:** `scripts/measure/ai-branching.py`（`count_choices`・`summarize` 新設、`count_progress`・`count_ledgers`・`scan_transcript`・`count_transcripts`・`main` 変更）、`scripts/tests/test_ai_branching.py`
- **変更内容:** `route_chosen` の行を `plugins/ndf/contracts/schemas/route-chosen.schema.json`（ファイルとして読む）と `jsonschema` で確かめて `chooser` ごとに数える。半開区間 `[since, until)` を時差つきの時刻で比べる。役割（`conductor` / `other` / `unattributed`）と `follows_next`、後の形の R03・R10・R13 の寄せ先を返す。`summarize` が出所 1 つだけから G1〜G3 を足し、出所 `none` を `unmeasured` と `partial: true` にする。`plugins/ndf/src` を import しない
- **満たす受け入れ条件:** 3412:7・3412:8
- **進め方:** 失敗するテスト（設計書のテスト設計の 7・8 の行を 1 行ずつ。`chooser` を読み捨てると落ちる、役割を読み捨てると 3 になる、`until` ちょうどを数えない、スキーマに合わない行が `rejected`、`ndf` を import しない環境で打てる）→ 最小実装 → 整理。時刻の形は実装の前に実データ（`progress.jsonl`・使用量ログ・会話の JSONL）で確かめて見本に写す

### Task 4: `token-usage.py` の期間と一意な PR

- **対象ファイル:** `scripts/token-usage.py`、`scripts/tests/test_token_usage.py`
- **変更内容:** `--since` を足し、`--until` を `t >= until` で切る。時差の無い値は終了コード 2。`unique_prs` で PR を番号で一意にし、`pr_cost_total`・`pr_minutes_total` を出す。`--sessions-out` で U1 の分子に費用を入れた会話の `session_id` の一覧を書く
- **満たす受け入れ条件:** 3412:9・3412:10（U1・U3 の分母）
- **進め方:** 今の 22 件のテストが通ることを確かめる → 失敗するテスト（`--since` の境界、同じ PR を 2 セッションが持つと 1 本、対象の外の PR を数えない、PR 0 本で「計測不能」）→ 実装 → 整理

### Task 5: `claude-p-usage.py` の `launches`・`plan-cost`

- **対象ファイル:** `scripts/measure/claude-p-usage.py`、`scripts/tests/test_claude_p_usage.py`
- **変更内容:** 副命令 `launches`（U2 を（`run_id`・ステップ・試行）の鍵で突き合わせる `count_launches`）と `plan-cost`（`caller: claude-p` の行のうち対象リポジトリの `repo` を持ち `--counted` に無い `session_id` だけを足す `plan_cost`）を足す。副命令を付けない今の集計は変えない。旧い帳簿・`gh`・`ndf records usage report` を読まない
- **満たす受け入れ条件:** 3412:9・3412:10（U1 の計画起動分・U2）
- **進め方:** 今の 11 件のテストが通ることを確かめる → 失敗するテスト（詳細設計書の「単体テストの観点」の `plan_cost`・`count_launches` の行）→ 実装 → 整理

### Task 6: 決定を分ける（作業ファイル）

- **対象ファイル:** 無し（作業ファイルはコミットしない。結果は Task 7・8 の番号の対応表に入る）
- **変更内容:** 36 本すべての `### 決定 N` の見出しと注記から、決定ごとの「生きている / 置き換えた（置き換え先）/ 戻した」の表を作る。移さない 16 本の生きている決定（`fixes` の決定 69〜75・`issue-3388-design.md` の決定 1〜10 を含む）を 1 件ずつ「移す（移し先と番号。重なれば 131 以降）」か「移さない（理由）」に決める
- **満たす受け入れ条件:** 3412:1・3412:4 の前提
- **進め方:** テスト駆動を当てない（文書の選別）。終わりの条件は詳細設計書の手順 1 のとおり、36 本の見出しの全件が表にあること

### Task 7: 確定仕様の前半 3 本（索引・ライブラリ・データと兆候）

- **対象ファイル:** `docs/specifications/ndf-rebuild.md`・`ndf-rebuild-libraries.md`・`ndf-rebuild-data-and-smells.md`（新設）、`docs/specifications/README.md`
- **変更内容:** 詳細設計書の手順 2〜4。`ndf-rebuild.md` に子の記号の表と番号の対応表（表の頭に消す前のコミットの `issues/` の固定リンク）を置く。各本 500 行以下。`plan-to-spec` の削除・変換ルールを当てる
- **満たす受け入れ条件:** 3412:1・3412:2・3412:3・3412:4
- **進め方:** テスト駆動を当てない。番号の一意の `grep`（設計書のテスト設計の 2）が 0 行、記号が表と一致することを確かめる

### Task 8: 確定仕様の後半 3 本（スクリプトの流れ・計測・レビューの範囲）

- **対象ファイル:** `docs/specifications/ndf-rebuild-scripted-flow.md`・`ndf-rebuild-measure.md`・`ndf-rebuild-review-scope.md`（新設）、`docs/specifications/README.md`
- **変更内容:** Task 7 と同じ。`scripted-flow` の決定 65 を 130 へ、不変条件の重なりを最大の次へ振り直し、`ndf-rebuild.md` の番号の対応表に足す。`ndf-rebuild-measure.md` は物差しと打つコマンドまでを書き、前後の値の表は Task 11 で埋める
- **満たす受け入れ条件:** 3412:1・3412:2・3412:3・3412:4
- **進め方:** Task 7 と同じ

### Task 9: `issues/` の扱いを 1 か所にし、参照を書き換える

- **対象ファイル:** `issues/README.md`、`docs/specifications/ndf-knowledge-and-kiro.md`、`docs/glossary/glossary.json`（語の定義の本文）・`docs/glossary/*.md`（`ndf records glossary render` で生成）、`scripts/` の作り直しの決定を指すコメント、残る文書の 36 本へのリンク（`issues/issue-3412-requirements.md`・`issues/issue-3341-requirements.md` など）
- **変更内容:** 詳細設計書の手順 5（`scripts` の分）・6・9。リンクは手順 7 の SHA（このタスクの頭で 1 回だけ取る `origin/main`）に固定する。系統を読み取れない行は前後 10 行から決め、決められなければ書き換えず PR 本文に挙げる
- **満たす受け入れ条件:** 3412:5（`plugins/ndf` を除く範囲）・3412:6
- **進め方:** テスト駆動を当てない。参照の検査の 1 が着手の前に 1 行以上を返すことを確かめ、終わりに 1 が `pending_source` の行だけ、2 が `plugins/ndf/src/ndf/checks/README.md:94` の 2 本（#3960 が直す）だけを返すことを確かめる

### Task 10: 開いている Issue のリンクを固定し、36 本を消す

- **対象ファイル:** 36 本（削除）、`docs/glossary/glossary.json`・`docs/glossary/ndf-rebuild.md`
- **変更内容:** 詳細設計書の手順 7・8。置換案（作業ファイル）を作り、PR 本文に Issue の番号と替えるリンクの数の表と適用のコマンドを載せる。適用は編集者が打つ。消す直前に 36 本の未コミットの変更が 0 行であることを確かめ、「消すコマンド」を打つ
- **満たす受け入れ条件:** 3412:1・3412:5・3412:6・3412:11
- **進め方:** テスト駆動を当てない。終わりの条件は `ndf records glossary render --check` が `ok`、参照の検査の 1 と 2 が 0 行、`scripts/check-markdown-links.py` と文書の古さの検査が通ること。**着手の条件: #3960 の `plugins/ndf/src/ndf/checks/README.md` の書き換えがスプリントブランチへ入っている**（下の「リスクと対処」）

### Task 11: 測り直して結果を残す

- **対象ファイル:** `docs/specifications/ndf-rebuild-measure.md`（前と後の値の表）、#1142 のコメント
- **変更内容:** 詳細設計書の「測り直し」の期間と打つコマンドで、G1〜G3 の前の値の打ち直しと 1 回目の後の値、U1〜U3 を出す。測れなかった値（後の形の選択の記録が #3908 で 0 件・U2 と U1 の計画起動分が `record_cli` を呼ぶ箇所が無く欠ける）は理由とともに書く。U1 の行に目標 1.14M との差か「判定不能」と観測下限を書く
- **満たす受け入れ条件:** 3412:9・3412:10
- **進め方:** テスト駆動を当てない（測定）。測る日の `T00:00:00+09:00` を打つ前に決めて表に書く

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| u1-links | Task 1 | 3412:5・3412:11 | 約 200 | （実装の後に書き足す） |
| u2-branching | Task 2・3 | 3412:7・3412:8 | 約 900 | |
| u3-usage | Task 4・5 | 3412:9・3412:10（道具の分） | 約 500 | |
| u4-spec-a | Task 6・7 | 3412:1・3412:2・3412:3・3412:4 | 約 900 | |
| u5-spec-b | Task 8 | 3412:1・3412:2・3412:3・3412:4 | 約 900 | |
| u6-refs | Task 9 | 3412:5・3412:6 | 約 300 | |
| u7-finalize | Task 10 | 3412:1・3412:5・3412:6・3412:11 | 約 100（36 本の削除の約 14,500 行は数えない） | |
| u8-measure | Task 11 | 3412:9・3412:10 | 約 100 | |

- 依存の向き: u1〜u3 と u4〜u5 は互いに依存しない（設計書の処理の流れの 1〜6 と 7〜8）。u6 は u4・u5 の後、u7 は u1・u6 の後と #3960 の後、u8 は u2・u3・u5 の後
- 数えないパス: u7 の `issues/issue-1142-*`・`issue-3347-*`・`issue-3395-*`・`issue-3388-*`（移した文書の削除で、読む中身が確定仕様の側にある）、生成した `docs/glossary/*.md`。数えるコマンドは `git diff --shortstat <ベース>...HEAD -- . ':(exclude)issues/issue-1142-*' ':(exclude)issues/issue-3347-*' ':(exclude)issues/issue-3395-*' ':(exclude)issues/issue-3388-*' ':(exclude)docs/glossary/*.md'`
- u4・u5 は確定仕様 3 本ずつで、各本 500 行以下の上限から 1 単位 1,000 行を超えない。超えそうなら本ごとに単位を分け直す

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触るスクリプトはどれも 1 つのタスクの単位だけが触る（`ai-branching.py` 286 行は u2 だけ、`token-usage.py` 1,085 行と `claude-p-usage.py` 744 行は u3 だけ、`check-markdown-links.py` 271 行は u1 だけ）。同じファイルを触るタスクの割合は 11 のうち 2（Task 2・3 の `ai-branching.py`、Task 4 と 5 は別のファイル）。既存のテストは 85 件（`test_check_markdown_links.py` 52・`test_token_usage.py` 22・`test_claude_p_usage.py` 11）で、`ai-branching.py` だけテストが無いが、Task 2 の頭の退行テストで縛る |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | 時刻の半開区間の解釈（`ai-branching.py`・`token-usage.py`・`claude-p-usage.py` の 3 本が同じ `[since, until)` を時差つきで比べる） | 3 つの単位（u2・u3）が同じ形の処理を持つ。共通の部品へ寄せるかは `cross-refactoring` が予算の中で決める |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 36 本を消すと、`plugins/ndf/src/ndf/checks/README.md:94` の 2 本の相対リンク（`issue-3347-design-detail.md`・`issue-3395-design.md`）が壊れ、Markdown のリンクの検査が落ちる。この行は ls3-spec の触る場所の外（#3960） | u7 を最後の単位の 1 つ前に置き、#3960 の書き換えがスプリントブランチへ入ってから着手する（base-sync）。u7 の着手の時点で入っていなければ、オーガナイザーに順番を問う |
| `plugins/ndf` のほかの 5 ファイル（`pyproject.toml`・`children.json`・`invariants.py`・`durable.py`・`transition_decide.py`）のコメントが消したファイル名を指したまま残る。リンクの検査は落ちないが、3412:5 の 0 件に届かない | 3412:5 の `plugins/ndf` の分は #3960 の受け入れ条件が持つ。スプリントの完了判定で、#3960 が閉じていなければ 3412:5 を「#3960 待ち」として挙げる |
| 開いている Issue の本文の書き換えは AI が排他にできない（詳細設計書の「本文の書き戻しと同時の編集」） | 置換案と適用のコマンドを PR 本文に載せ、編集者が打つ。適用は u7 を含むスプリントの PR のマージより前に終える |
| `ndf records spec finalize` は未コミットの編集ごと消す | 打つ直前の `git diff`・`git diff --cached` が 0 行でなければ止める（詳細設計書の「消すコマンド」） |
| 後の形の選択の記録が #3908 のため 0 件で、G1〜G3 の後の値が会話の記録の分だけになる | Task 11 の結果に「プランの選択は測れなかった（#3908）」と残す（設計書の「未確認のまま残ること」） |

## 切り戻し手順

- u1〜u3・u8 はスクリプトと文書の追加で、PR の revert で戻せる
- u7 の削除は revert で 36 本が戻る。ただし開いている Issue の本文のリンクの固定は戻さない（40 桁の SHA を指すので、戻しても壊れない）

## 完了の定義

- [ ] 受け入れ条件 3412:1〜11 をすべて満たし、条件ごとに検証手段（要求仕様の「検証手段」と設計書のテスト設計）と結果が対応している。3412:5 の `plugins/ndf` の分は #3960 の完了で確かめる
- [ ] `standard` の検証の段階（`quality-gates`）: 単位ごとの PR の CI の必須のジョブが通り、cross-review が収束している
