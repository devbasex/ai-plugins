# #4446: 承認資料と質問の文を、承認ゲートの前の工程で読み手役に確かめさせる

## 関連リンク

- 要求仕様: [issue-4446-requirements.md](./issue-4446-requirements.md)（正は https://github.com/devbasex/ai-plugins/issues/4446 の本文）。受け入れ条件は「条件 N」で指す
- 設計書: [issue-4446-design.md](./issue-4446-design.md)、詳細設計書: [issue-4446-design-detail.md](./issue-4446-design-detail.md)
- 設計 PR: https://github.com/devbasex/ai-plugins/pull/4477 （マージ済み。設計承認の条件は同じ PR のコメント「設計承認の取り直し」にある）
- モード: `standard`（既定で働く工程・hook・結果 JSON の契約を変える機能追加。要求仕様の「種類」）

実装計画を省かない理由: 要求仕様の「影響」の表で公開インタフェースが「変わる」。触るファイルも 4 を超える（設計書の「設計の結果」で 50 以上）。省ける条件を満たさない。

## 修正対象

詳細設計書の「地図」の表のとおりで、ここには引き写さない。パスは `plugins/ndf/` からの相対で書く。タスクごとの対象ファイルは下の「タスク分解」にある。地図の表に無いファイルを足すタスクは、そのタスクの「変更内容」に理由を書く。

## 計画で決めたこと

設計書の「未確認のまま残ること」が工程「計画」に預けた 8 点の扱いである。7 点は実装の細部としてここで決めた。PR の題と本文の書き戻し（レッドライン C3）は、承認の条件に関わるので利用者に問い、利用者が決めた（下の「決めたこと: PR の題と本文の書き戻し」、設計書の決定 10）。

| 番号 | 預けられた点 | 決めたこと | 理由 |
| --- | --- | --- | --- |
| (1) | 手順 2 で `--material` のファイルを直接直すか、作業用のコピーを直すか | `--state-dir` の下の作業用のコピー（`material.md`）を直す。入力のファイルは変えない。`outputs.material` はコピーのパスを指し、会話のセッションはそれを `present` に渡す | 途中で止まっても入力のファイルが残り、打ち直しが同じ入力から始まる。PR の本文の経路と同じ扱いになる |
| (2) | 残った文のセクションを、手順 5 で決めた文から作るか、保存した行から作るか | 手順 5 で決めた残った文の並びを、手順 9 の 1 で行に書く値と同じ 1 つのリストとして持ち、`render_remained` にそのリストを渡す。セクションと行は同じ値から作る | セクションと記録が別の値から作られると、`check` が基準の確認から作り直したセクションと 1 字ずれる |
| (3) | 前に残った文の扱いの表の「今の中身に無い」の判定・同じ指摘とみなす基準・全体を読ませたときの扱い | 「今の中身に無い」は、引用の連続する空白を 1 つにそろえた文字列が、対象の今の全体（同じ規則でそろえたもの）に部分文字列として無いこと。同じ指摘は、対象が同じで、そろえた引用が同じもの。全体を読ませた対象では、前に残った文はすべて「今回読ませた差分の部分に含まれる」として扱う（今回も挙がったものだけが残る） | 文の言い換えまで同じとみなすと、機械で決められない。空白だけをそろえれば、改行の位置の違いで解消と誤らない |
| (4) | 手順 5 で作り直した質問のキーが合わないときの終え方 | 決定 10 で、題の案から質問を作り直さず差し戻すので、作り直した質問のキーを比べる場面は無くなった。worker の結果に書かれた質問のキーが、gate が作った質問のキーと違うときは、`stopped`（`incomplete`）で終え、`gaps` に `question` を書く。記録は書かない | 読ませていない質問を確認済みにしない。手順 5 のほかの確かめと同じ扱いになる |
| (5) | 手順 8 で書き換えの後の照合が合わないときの終え方と、ほかの人の編集の見分け方 | 決定 10 で、手順 8 は PR を書き換えなくなった。書き換えの後の照合も、ほかの人の編集の見分けも要らない。代わりに手順 8 は、同じ承認資料のコメントが既にあるかを、手順 7 の取得で読んだコメントの本文の SHA-256 と最終版のファイル全体の SHA-256 で見分ける。あれば足さない | 打ち直しで同じ資料のコメントが重ならない。コメントの追記は、ほかの人の編集を消さない |
| (6) | 読み手の立場を固定するか、文書の種類で決めるか | 対象の種類で固定する。質問の文と承認資料は「承認する人」、設計 PR の文書は「エンジニア」とし、`records/reading.py` の定数 1 か所に置く | 設計書の「読ませるものの決め方」の立場と同じで、設計 PR に載る文書の読み手はどれもエンジニアである。文書の種類ごとに変える場面がこの変更の範囲に無い |
| (7) | 古い工程の定義で `reread` が gate を直接打つときに、`--reviewed` と `--no-doc-fix` を渡すか | 渡す。値は工程の記録から `reading_args` と同じ規則で作る（`reading_args` を、ステップの定義が無くても工程の記録とゲートの名前から作れる形にする） | 2 つの経路で往復の上限と解消の扱いが違うと、古い定義のスプリントだけ設計のレビューとの往復が止まらない |

### 決めたこと: PR の題と本文の書き戻し（設計書の手順 8、レッドライン C3）

GitHub の PR の更新には、前の版を条件にした書き換えが無い。そのため、gate が PR の本文を書き換えると、取得し直してから書き換えるまでの短い間にほかの人が入れた編集を消しうる。設計承認はこの点を残したまま受け、「計画で直し方を決め、決まるまでその部分は実装しない」が条件になっていた（PR #4477 のコメント）。

計画では 3 つの案を示して問うた。案 A は、PR を書き換えず、直した承認資料と題の案をコメントで足す。案 B は、今の設計のまま書き換えの後に照合する。案 C は、直した版を残して止め、人が PR へ反映する。

利用者は 2026-11-25 に、区分 `ndf-ask:redline:C3` の質問へ次のとおり答えた。「通過時（指摘がないとき）はコメントとして追加で良い。タイトルや本文を書き換えないと意味が通じない場合は差し戻して修正し、新しい読み手役に再度読ませる」。続けて、「直す側はレーンのコンダクターでも別に構わない」と補った。この回答を設計の変更の承認として扱い、設計承認は取り直さない。設計書には決定 10 として書いた。

| 場面 | 決めたこと |
| --- | --- |
| 読み手役が合格したとき | gate は PR の題と本文を書き換えない。確かめた承認資料を PR のコメントとして 1 件足す（案 A と同じ） |
| 題か本文を直さないと通じないとき | gate は PR を書き換えず、承認を求めずに `stopped`（`precondition`。差し戻し）で終える。直しの案は `--state-dir` に残す。直す担当は 1 つに限らず、レーンの conductor 自身でもよい。直した後に工程を再開すると、読み手役のステップを打ち直し、新しい worker の読み手役が変わった部分を読む |
| 差し戻しを繰り返すとき | 同じ PR とゲートで差し戻すのは 1 回までとする。2 回目は題と本文を直させず、残った指摘を承認資料の「読み手役が残した文」に書いて `ok` で進める |
| `check` の照合 | PR の本文の経路では、同じ取得で読んだ PR のコメントに、記録したファイル全体のハッシュと同じ本文のコメントがあるかと、PR の本文のハッシュが記録と同じかを見る。案 A の弱点だった「コメントの後からの編集に気づけない」は、`check` では残らない。`check` の後から回答までのコメントの編集は、`recheck` が見ないので残る |

詳細設計書の `rewrite_pr` と、その「計画で決める」の欄は、この決定で次のとおり置き換わる。`rewrite_pr` は作らない。代わりに、差し戻しを `verify_worker` の結果から決め、コメントの追記を `post_material` として Task 12 で作る。詳細設計書は、実装の中で、この計画と設計書に合わせて直す。

### 実装の途中で決めたこと（2026-11-25）

単位「記録と質問の形」（Task 1〜3、コミット `c33311ec4`）の後に、次の 2 つを決めた。

| 点 | 決めたこと | 誰が・なぜ |
| --- | --- | --- |
| 子の表 `children.json` の依存 | 設計書の「子の表の変更」のとおりに、次の単位（gate のサブコマンド）の中で `children.json` を直す（M1 の `needs` に `M7`、M1 の `order` を 8、M7 の `needs` に `M2`）。直すまでは `ndf check` の規則 `child-deps` が `docrebuild/drive.py` と `records/reading.py` を落とす | オーガナイザーの判断。承認済みの設計の依存の向きのとおりで、設計の決定を変えないので、承認ゲートに当たらない。リリースの承認資料に 1 行で載せる |
| テストの関数の数の上限（`.ndf/project.json` の `budget.tests` 6000） | 上限は変えない。残りの単位では、足すテストの数だけ、同じ観点の既存のテストをパラメータ化でまとめて枠を空ける。確かめる内容は減らさない | conductor の判断（人に問う区分に当たらない）。上限の値を変えるのは方針の変更で、この課題の範囲を超えるため |

単位「工程の定義と待ちの出し直し」（Task 8・9）の中で、詳細設計書の関数の置き場所と値の持ち方を、次のとおり実装に合わせた。振る舞い（何を見つけ、いつ止め、いつ打ち直すか）は設計書のままである。

| 点 | 実装 | 理由 |
| --- | --- | --- |
| 回答が固定する「質問のキー」 | `Target` は質問のキーの代わりに PR の題の SHA-256（`title`）を持ち、本文の SHA-256（`body`）と同じ 1 回の取得で読む。`ndf answer` は `--body-sha256`・`--title-sha256` を受ける | 質問のキーを作る `ndf.domains.records.reading` は子 M7 のモジュールで、`ndf.progress.gate`（子 P2）は読めない（子の表の向き）。質問の文はゲートの種類・PR の番号・題・リリースの形だけから決まるので、題の変更を見れば質問の変更を見つけられる |
| 本文と題の照合 | 見た側が値を持つときだけ比べる | MVV 判定で省いた回答は head だけを見て書くため |
| `reading_skip` の `in_lane` | `run.Env.in_lane` の口から引き、`gate.lane_env` が `lane.sprint_row` で埋める | `ndf.progress.run`（子 P1b）はレーン（子 P3）を読まない |
| `reread` の 1 ステップの実行 | `answering._step_once` に置き、読み手役のステップだけを同じプロセスで流す。`review_again` で続きがあれば作業を起動して `wait` を返す | `run.py` の安全網（1000 行）。続きの設計のレビューを前景で抱えないため |
| ステップを未了に戻す記録 | 事象 `step_recorded` の `status: reset`。戻したステップは新しい保存先（`<ステップ>-g<世代>-r<回>`）で打つ | 同じ保存先でプランを打つと、終わった回の結果が返る |
| 結果の規約の `outputs` | `questions`・`material`・`check`・`review_again` を閉じた語彙に足した | gate のサブコマンドが返す `outputs` が語彙に無く、CLI が結果を `stopped`（invalid）に差し替えていた（単位「gate のサブコマンド」の取りこぼし） |

## タスク分解

依存の向き（記録 `records` ← サブコマンド `docrebuild` ← 工程を進める仕組み `progress` ← 手順書）の順に並べる。工程の定義に読み手役のステップを足すのは、gate のサブコマンドができた後にする。先に足すと、マージした時点のスプリントブランチで、設計のプランが知らないコマンドで落ちるためである。手順書は、書いたコマンドが動く単位でまとめて直す。

### Task 1: 今の止め方と工程の数え方を縛る退行テスト

- **対象ファイル:** `tests/domains/hooks/test_reading_check.py`、`tests/domains/records/test_reading.py`、`tests/progress/test_definition.py`
- **変更内容:** 今の振る舞いのうち、この変更で壊れうるものにテストを足す。hook が記録の無い質問と head の動いた質問を止めること（足りない分だけ）。今の `check` が `no_record`・`no_pr`・`head_moved` を返すこと。設計とデプロイの工程で `StageDef.gates` が 1 つで、`before` が承認ゲートの直前のステップを返すこと
- **満たす受け入れ条件:** 7（今の止める働きが失われない）
- **進め方:** 今のコードで通るテストを先に足し、以後のタスクで変えずに通ることで見る

### Task 2: 読ませたものの記録と承認の質問の形を作る

- **対象ファイル:** `contracts/schemas/internal/reading-docs.schema.json`（新設）、`contracts/domains/ndf-files.json`、`contracts/domains/release-forms.json`、`src/ndf/domains/release/forms_decide.py`、`src/ndf/domains/records/reading.py`、`tests/domains/records/test_reading.py`
- **変更内容:** 行 `reading-docs`・`reading-materials` を表 `ndf-files` に足す。`docs_path`・`materials_path`・`append_doc`・`completed`・`latest_read`・`remained_of`・`pending_review`・`select_reads`・`row_fingerprint`・`material_hashes` と、印の 2 行・見出し・立場の定数を `reading.py` に置く。`gate_questions` と、リリースの形の表の列 `approve`（`Form`・`form_of`）を足す。前に残った文の判定は「計画で決めたこと」の (3) のとおりにする
- **満たす受け入れ条件:** 2・4（質問の決まった形）、6（`select_reads` の選び方）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。観点は詳細設計書の「承認の質問の組み立て」「リリースの形の表」「読ませたものの記録」の単体テストの表

### Task 3: 組み直しで読ませた文書を記録し、`not_run` で止める

- **対象ファイル:** `src/ndf/domains/docrebuild/drive.py`、`tests/domains/docrebuild/test_docrebuild_run.py`
- **変更内容:** `record_reads` を足し、`_checked` が `ok` を返す前に打つ。blob は `git hash-object` の値にする。`_reader_gap` が `not_run` を欠けとして返し、`stopped`（`precondition`）にする
- **満たす受け入れ条件:** 1（組み直しの結果が記録に残る）、5（`run` に「読ませていない」で終わる経路が無い）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 4: gate のサブコマンドで、読ませて記録する（文書を直さない経路）

- **対象ファイル:** `src/ndf/domains/docrebuild/gate.py`（新設）、`src/ndf/domains/docrebuild/cli.py`、`src/ndf/domains/docrebuild/drive.py`（`run_worker` を保存先と kind を引数に取る形へ広げる）、`contracts/worker/document-rebuild-gate.md`（新設）、`contracts/schemas/io/document-rebuild.args.schema.json`（生成）、`contracts/schemas/io/document-rebuild.result.schema.json`、`docs/contracts/commands.md`（生成）、`tests/domains/docrebuild/test_docrebuild_gate.py`（新設）
- **変更内容:** `drive_gate`・`fix_head`・`working_material`・`strip_marks`・`plan_reads`・`write_facts`・`gate_prompt`・`verify_worker`・`render_remained`・`refetch_check`・`record` を作る。設計の経路（PR の本文）・デプロイの経路・`--material` の経路を通す。PR の題と本文は書き換えない（設計書の決定 10）。worker が題の案を書いたとき、または最終版で PR の本文から写した行を消すか変えたときは、差し戻しの行を `reading-docs.jsonl` に書き、ほかの記録を書かずに `stopped`（`precondition`。差し戻し）で終え、直しの案を `--state-dir` に残す。同じ PR とゲートに差し戻しの行が既にあれば、題の案のファイルを決めず、worker に本文の行を直させない。`--material` の経路は PR の本文を扱わないので、差し戻しに当たらない。合格した資料をコメントで足す処理は Task 12 で作る。読ませるものが無いときは worker を起動せずに記録だけを作り直す。「計画で決めたこと」の (1)・(2)・(4)・(6) をここで実装する。生成の 2 ファイルは規約の生成で作り、手で書かない
- **満たす受け入れ条件:** 2・4（資料と質問を読ませて記録する）、5（`not_run` で止める）、6（変わった部分だけを読ませる）、9（資料を書く・読ませる・直す・記録する、の流れ）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。worker・GitHub・git は偽物に替える。観点は詳細設計書の「`ndf document-rebuild gate`」の単体テストの表のうち、`open_review_again`・`push_fixed`・`rewrite_pr` を除く行と、設計書の「テスト設計」の差し戻しの 2 行

### Task 5: 直した文書を push して設計のレビューへ戻す経路

- **対象ファイル:** `src/ndf/domains/docrebuild/gate.py`、`tests/domains/docrebuild/test_docrebuild_gate.py`
- **変更内容:** `open_review_again`・`push_fixed` を作る。再レビュー待ちの行を push の前に書き、CI を待ち、`outputs.review_again` を返す。`--reviewed` と `--no-doc-fix` を受ける。処理の手順の表の 5 行をそのまま通す
- **満たす受け入れ条件:** 6（直した文書を設計のレビューへ戻す）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。`ci_watch` と `git push` は偽物に替える

### Task 6: Skill「承認の前の確認」と、worker が読み手役を立てる例外

- **対象ファイル:** `skills/document-rebuild/SKILL.md`、`contracts/worker/document-rebuild.md`、`skills/development-workflow/references/agent-layers.md`
- **変更内容:** `document-rebuild` の SKILL.md にセクション「承認の前の確認」を足し、description のトリガーに「承認資料」を足す。「承認の前の確認」には、PR の題の案と PR の本文から写した行の直しは PR へ反映されず差し戻しになること、書き足した判断の材料は自由に直してよいことを書く（設計書の決定 10）。`agent-layers.md` の例外の段落に、`ndf document-rebuild gate` が起動した worker を足す。`document-rebuild.md` から「読ませていない」で終える指示を外す
- **満たす受け入れ条件:** 5、9
- **進め方:** 手順書の変更なのでテスト駆動を適用しない。`python3 scripts/check-skill-frontmatter.py` と `ndf check` で見る

### Task 7: `ndf records reading check` で本文と題を照合し、質問を作り直す

- **対象ファイル:** `src/ndf/domains/records/reading.py`（`check`）、`src/ndf/domains/records/cli.py`、`src/ndf/domains/hooks/guards/reading_check.py`（`denial` の案内の文）、`docs/contracts/commands.md`（生成）、`tests/domains/records/test_reading.py`、`tests/domains/hooks/test_reading_check.py`
- **変更内容:** `check` が PR を 1 回取得し、題から質問を作り直し、行の指紋で完了の行と結び付けて承認資料のハッシュを照合する。PR の本文の経路では、同じ取得でコメントも読み、記録したファイル全体のハッシュと同じ本文のコメントがあるかと、PR の本文のハッシュが記録と同じかを見る（設計書の「確認の id と完了の行」）。`--material` の経路では、渡されたファイルの 2 つのハッシュを見る。`--gate`・`--pr`・`--material`・`--questions-out` を足し、`--file` を受けない。hook の判定は変えず、止める理由の案内だけを変える
- **満たす受け入れ条件:** 7（本文と題の変更を `check` が見つける。hook の止め方は変わらない）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。Task 1 の hook のテストを変えずに通す

### Task 8: 工程の定義に読み手役のステップを足す

- **対象ファイル:** `contracts/workflow/ndf.schema.json`、`contracts/workflow/stages/design.json`、`contracts/workflow/stages/deploy.json`、`src/ndf/progress/stages.py`、`src/ndf/progress/model.py`、`src/ndf/progress/run.py`、`tests/progress/test_definition.py`、`tests/progress/test_model.py`、`tests/progress/test_run.py`
- **変更内容:** ステップの種類 `reading` を足し、`design-reading` を `design-mvv` と `design-gate` の間に、`release-reading` を `release-mvv` と `release-gate` の間に置く。`StageDef.gates` は種類 `gate` だけを数え、`mvv_before`・`reading_for` を足す。`reading_skip`・`reading_args`・`review_again_drafts` を足し、`_running_move`・`_consume`・`_run_step`・`_step_draft` を変える。`reading_args` は「計画で決めたこと」の (7) のため、工程の記録とゲートの名前から作れる形にする
- **満たす受け入れ条件:** 1・3（設計のプランが組み直しと読み手役のステップを流し、承認ゲートの直前に読み手役を起動しない）、4（デプロイの工程）、9（ゲートの手前のステップが Skill の流れを呼ぶ）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。Task 1 の `gates` と `before` のテストを変えずに通す

### Task 9: 回答の固定と待ちの出し直し

- **対象ファイル:** `src/ndf/progress/gate/asking.py`、`src/ndf/progress/gate/answering.py`、`src/ndf/progress/gate/entry.py`、`tests/progress/gate/test_gate_answer.py`
- **変更内容:** `Target` に本文のハッシュと質問のキーを足す。`github_observe` が同じ 1 回の取得で本文と題を読む。`recheck` が待ちへ戻すときに `_reading_reset` を同じトランザクションで足す。`reread` と `ndf progress reread` を足す。古い定義のスプリントでは gate を直接打ち、`reading_args` の値を渡す
- **満たす受け入れ条件:** 3（問う側が読み手役を起動せずに確認をやり直せる）、6（変わったときに読み手役のステップを未了に戻す）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 10: 会話のセッションのリリースで、提示と回答を資料のハッシュで結び付ける

- **対象ファイル:** `src/ndf/domains/release/approval_decide.py`、`src/ndf/domains/release/trail.py`、`src/ndf/domains/release/cli.py`、`docs/contracts/commands.md`（生成）、`tests/domains/release/test_release_approval.py`
- **変更内容:** `render_request` に行 `提示した資料:` を、利用者の回答の `render_trail` に行 `回答した資料:` を足す。`parse_request` を足し、`judge` を担い手で分ける。`present` は資料が違えば提示し直す。`answer` に `--material-sha256` を足す
- **満たす受け入れ条件:** 4（読ませた資料と承認した資料を結び付ける）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。ほかのタスクに依存しないので、どの順でも流せる

### Task 11: 版上げのスプリントと計画の行の入力 `kind: release`

- **対象ファイル:** `contracts/workflow/modes/release-bump.json`（新設）、`contracts/workflow/stages/release-bump.json`（新設）、`contracts/templates/release-bump.json`（新設）、`contracts/workflow/parallel-plan.schema.json`、`src/ndf/progress/lane/types.py`、`src/ndf/progress/lane/launch.py`、`src/ndf/progress/command.py`、`src/ndf/progress/gate/release.py`、`tests/progress/test_lane_launch.py`、`tests/progress/gate/test_gate_release.py`、`tests/progress/test_definition.py`
- **変更内容:** モード `release-bump` と工程「版上げ」（`bump-assemble`・`release-mvv`・`release-reading`・`bump-present`・`release-gate`・`release`・`bump-record`）を足す。`LaneRow.requests`・`release_commands` を足し、`ndf progress new` に `--release-requests` を足す。`_plan_request` が版上げのスプリントでは `bump-release` を書く
- **満たす受け入れ条件:** 4（版上げの PR の資料と質問を工程の中で作る）、10（オーガナイザーが資料を書かない）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 12: 合格した承認資料を PR のコメントで足す

- **対象ファイル:** `src/ndf/domains/docrebuild/gate.py`（`post_material`）、`tests/domains/docrebuild/test_docrebuild_gate.py`
- **変更内容:** 設計書の「処理の流れ」の手順 8 のうち、コメントの追記を作る。PR の本文の経路で、差し戻さず、文書を push していないときに、手順 7 で読んだコメントに最終版と同じ SHA-256 の本文が無ければ、`gh pr comment` で 1 件足す。PR の題と本文は更新しない。詳細設計書の `rewrite_pr` は作らない（「決めたこと: PR の題と本文の書き戻し」）。Task 12 は、決定の前の「PR の題と本文の反映」から中身を変えて残す。コメントの追記が残るので、不要にはならない
- **満たす受け入れ条件:** 2（直した承認資料を承認する人に示す）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。GitHub は偽物に替える。書く前に、実際の PR へ 1 件コメントを足して取得し直し、GitHub が返すコメントの本文の SHA-256 が送ったファイルと同じかを確かめる（末尾の改行などが変わるなら、比べる前のそろえ方を決める）。Task 7 の `check` がこのコメントで通ることを、同じテストで確かめる

### Task 13: 承認を問う側とオーガナイザーの手順書を直す

- **対象ファイル:** `skills/development-workflow/references/approval-request.md`、`skills/development-workflow/references/asking.md`、`skills/development-workflow/references/parallel.md`、`skills/release/references/release-steps.md`、`skills/release/SKILL.md`、`skills/issue-plan-strategy/references/parallel-plan.md`
- **変更内容:** 設計書の「承認を問う側の手順」と、オーガナイザーの手順書の表のとおりに直す。問う側が読み手役を起動する手順、オーガナイザーが資料と質問の文を書く手順を外し、承認資料は PR のコメントにあると書き、`check --gate`・`ndf progress reread`・一時レーンの版上げのスプリントを書く。書くコマンドは、Task 7・9・11 の後の作業場所で打って確かめる
- **満たす受け入れ条件:** 3・8・10
- **進め方:** 手順書の変更なのでテスト駆動を適用しない。`ndf check` で参照切れを見る

## レビューの単位

見込みの差分は全体で約 4,400 行になり 1,000 行を超えるので、タスクの依存の向きで分ける。どの単位も単独でテストが通り、マージした後のスプリントブランチで知らないコマンドを指さない。行数は生成の 2 ファイルを数えない（`git diff --shortstat <ベース>...HEAD -- . ':(exclude)plugins/ndf/contracts/schemas/io/document-rebuild.args.schema.json' ':(exclude)plugins/ndf/docs/contracts/commands.md' ':(exclude)uv.lock'`）。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| 記録と質問の形 | Task 1・2・3 | 4446:1・4446:5・4446:7 | 約 700 | （実装の後に書き足す） |
| gate のサブコマンド | Task 4・6 | 4446:2・4446:4・4446:5・4446:6・4446:9 | 約 950 | （実装の後に書き足す） |
| 設計のレビューへ戻す経路 | Task 5 | 4446:6 | 約 400 | （実装の後に書き足す） |
| `check` の照合 | Task 7 | 4446:7 | 約 350 | （実装の後に書き足す） |
| 承認資料のコメント | Task 12 | 4446:2 | 約 150 | （実装の後に書き足す） |
| 工程の定義と待ちの出し直し | Task 8・9 | 4446:1・4446:3・4446:4・4446:6・4446:9 | 約 900 | （実装の後に書き足す） |
| リリースの証跡 | Task 10 | 4446:4 | 約 400 | （実装の後に書き足す） |
| 版上げのスプリント | Task 11 | 4446:4・4446:10 | 約 650 | （実装の後に書き足す） |
| 手順書 | Task 13 | 4446:3・4446:8・4446:10 | 約 300 | （実装の後に書き足す） |

単位の順は表の上からである。「リリースの証跡」はほかの単位に依存しないので、どこで流してもよい。「承認資料のコメント」は、工程の定義に読み手役のステップを足す前に流す。先に足すと、合格した資料が PR に示されないまま承認ゲートへ進むためである。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触る既存のファイルで最も大きいのは `src/ndf/progress/command.py`（981 行）と `src/ndf/progress/run.py`（934 行、テスト 60 件）である。どちらも触るタスクは 13 のうち 1〜2 つにとどまる。新しい処理の大半は新設の `docrebuild/gate.py` と `records/reading.py`（153 行）に入る。`docrebuild/drive.py`（478 行、テスト 42 件）は Task 3・4 が触るが、変えるのは 3 つの関数と `run_worker` の引数だけである |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `docrebuild/drive.py` と `docrebuild/gate.py` の worker の起動と結果の読み取り。`records/reading.py` の `material_hashes` と `gate.strip_marks` の除き方。`gate/answering.py` の `recheck` と `reread` のトランザクションの形 | 別の単位が同じ形の処理をそれぞれ書くため、単位をまたいで重複が出やすい |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 差し戻しの後に直す担当が動かず、工程が止まったままになる | 差し戻しは `stopped` で返るので、工程を進める仕組みがレーンの conductor へ理由と直しの案のパスを返す（設計書の決定 5 と同じ止まり方）。直す担当は conductor 自身でもよい |
| 新しい版を入れる前に工程の定義を読み込んだスプリントに、読み手役のステップが無い | Task 9 の `reread` が gate を直接打つ（「計画で決めたこと」の (7)）。今のスプリントの承認ゲートは、問う側が `check` で確かめる今の手順のまま動く |
| この変更の前の形式の、利用者の回答の証跡（資料のハッシュの行が無い）を数えない | 変更を入れた時点で回答待ちのリリースは、提示し直して回答を取り直す。リリースの承認資料の「互換」に書く |
| 2 つの記録が追記だけで増え続ける | 読み取りを 1 回の実行で 1 回にまとめる（詳細設計書の「計算量の注意」）。消す仕組みはこの変更に入れない |

## 切り戻し手順

- 単位ごとに revert できる。データの移行は無い
- 新しい記録のファイル `reading-docs.jsonl` とコピーのファイルは、revert の後は読まれないだけで、残しても害は無い
- 「リリースの証跡」を revert すると、資料のハッシュ付きの証跡は今の `judge` が head だけで数える。承認済みの判定が緩む方向なので、revert の後は回答待ちのリリースを提示し直す

## 完了の定義

- [ ] 受け入れ条件 1〜10 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run --frozen --project . --all-extras pytest plugins/ndf/tests/domains/docrebuild plugins/ndf/tests/domains/records plugins/ndf/tests/domains/hooks plugins/ndf/tests/domains/release plugins/ndf/tests/progress -q -n 4` が通る
- [ ] `ndf check` と `python3 scripts/check-skill-frontmatter.py` と `claude plugin validate .` が通る
- [ ] 「決めたこと: PR の題と本文の書き戻し」のとおり、gate が PR の題と本文を書き換えず、差し戻しとコメントの追記が Task 4・12 のとおりに作られている
- [ ] リリースの後に、設計のプランを 1 回流し、設計承認ゲートで問う直前に読み手役が起動されないことを実行の記録で確かめる（要求仕様の「リリース後の確認の計画」。条件 4 は次のリリースの後）
