# #4520: 工程を開き直し、承認の無い工程の先へ進ませない

## 関連リンク

- 要求仕様: [issue-4520-requirements.md](./issue-4520-requirements.md)（正は #4520 の本文）。受け入れ条件は番号で指す
- 設計書: [issue-4520-design.md](./issue-4520-design.md)
- 詳細設計書: [issue-4520-design-detail.md](./issue-4520-design-detail.md)
- モード: `standard`（公開インタフェースのコマンドと事象が変わる）

## 修正対象

ファイルと関数は詳細設計書の「地図」と、構成要素ごとの関数の仕様にある。ここではまとまりだけを引き写す。パスは `plugins/ndf/` から。

| ファイル | 変える中身 |
| --- | --- |
| `contracts/workflow/`（`progress.json`・`ndf.schema.json`・`stages/design.json`） | 事象の追加、書ける状態の拡張、NDF が作る出力の宣言 |
| `src/ndf/core/`（`release_record.py`・`merge_approval.py`） | リリース記録の読み取りの移動、証跡の重複の判定 |
| `src/ndf/adapters/`（`github.py`・`github_pulls.py`） | workflow の実行の一覧の読み取り、コメントの作成時刻 |
| `src/ndf/progress/`（今のモジュール） | 畳み込み、次の 1 手、出力の確かめ、進み具合の表 |
| `src/ndf/progress/`（`approval_decide`・`delivery_decide`・`run_approval`・`reopening`・`redo`・`redo_decide`） | 新しい判定と手 |
| `src/ndf/progress/gate/` | 手の経路の操作、質問と回答、リクエストの記録と取り下げ、対象の読み取り、MVV の証跡 |
| `src/ndf/domains/`（`records`・`release`・`worktree`） | MVV 判定の `--no-skip`、記録の読み取りの参照先、worktree の外し方 |
| `tests/progress/` | 下の各タスクのテスト |
| `skills/`・`docs/glossary*` | 手順書と用語 |

## タスク分解

テストは `uv run --frozen --project . --all-extras pytest plugins/ndf/tests/progress -q -n 4`、静的な確かめは `ndf check` で行う。

### Task 1: 行数の上限に近いモジュールを分ける（プレリファクタリング）

- **対象ファイル:** `src/ndf/progress/command.py`、`src/ndf/progress/run.py`、`src/ndf/progress/gate/finishing.py`（新設）、`src/ndf/progress/run_approval.py`（新設）、`src/ndf/progress/gate/entry.py`
- **変更内容:** `finish`・`check`・`retry` の処理を `gate/finishing.py` へ、承認ゲートの回答を読む処理と `approved_seen` を `run_approval.py` へ移す。振る舞いとコマンドの形は変えない
- **満たす受け入れ条件:** 無し
- **進め方:** 今のテストが通ることを確かめてから移し、移した後も同じテストが通ることを確かめる

### Task 2: 終えた工程を開き直す

- **対象ファイル:** `contracts/workflow/progress.json`、`src/ndf/progress/{model,folding,stages,run_view,records,reopening}.py`、`gate/finishing.py`、`gate/records.py`、`src/ndf/core/merge_approval.py`、`tests/progress/test_reopen_operations.py`（新設）、`tests/progress/test_model.py`
- **変更内容:** 事象 `reopened`、開き直す工程の決定、`retry --later`、世代と出力の扱い、承認を読む側が直近の開き直しより前を読まないこと、取り消されない操作の警告、表の PR の欄、開き直す承認の証跡の取り消し（同じ HEAD とマージ先のまま開き直すと `ndf ci merge` が断るテストを置く）
- **満たす受け入れ条件:** 10・11・12・13・16・17・19・20
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。単体テストの観点は詳細設計の「遷移の表」「畳み込み」「開き直し」「承認を読む側」

### Task 3: 手で終えた承認ゲートの工程で承認を待つ

- **対象ファイル:** `progress.json`、`ndf.schema.json`、`stages/design.json`、`src/ndf/progress/{approval_decide,run_approval,run,folding,definition}.py`、`gate/{finishing,targets,answering,asking,entry}.py`、`tests/progress/test_manual_gate.py`（新設）
- **変更内容:** 事象 `finish_held`、有効な承認の判定、対象の 1 回の読み取り、`next` の回答待ちとマージの確かめ、自動の流れが先の承認を使うこと
- **満たす受け入れ条件:** 1・2・3・4
- **進め方:** 失敗するテスト（手で終えると後ろが始まる、今の不具合）→ 通す最小実装 → 整理

### Task 4: どの状態の工程にも回答を記録する

- **対象ファイル:** `progress.json`、`gate/{answering,asking,finishing,records,entry}.py`、`src/ndf/core/merge_approval.py`、`src/ndf/progress/folding.py`、`tests/progress/test_manual_gate.py`
- **変更内容:** `ndf progress question`、待っていない工程への回答、開き直しを起こす回答の順序、排他と送り直し、否定の回答が残る間の扱い、証跡の再利用
- **満たす受け入れ条件:** 5・6・7・8・9・14・15・18・21・22
- **進め方:** 失敗するテスト（2026-11-25 の実例の順を通しで打つ）→ 通す最小実装 → 整理

### Task 5: 手の経路の MVV 判定と承認の証跡

- **対象ファイル:** `src/ndf/domains/records/{cli,mvv_gate}.py`、`gate/{finishing,mvv_trail}.py`、`src/ndf/progress/run_approval.py`、対応するテスト
- **変更内容:** `finish` が設計の判定を打つ、`--no-skip` と設計の `--expect-pr`、判定のコマンドが証跡を書かない、承認を記録した `next` が証跡を書く
- **満たす受け入れ条件:** 無し（設計の決定 7。テスト設計の「決定 7」「判定の対象」の行）
- **進め方:** 失敗するテスト（判定のコマンドが証跡を書く）→ 通す最小実装 → 整理

### Task 6: デプロイの工程のリリースの確かめと追認

- **対象ファイル:** `src/ndf/core/release_record.py`（新設）、`src/ndf/domains/release/{record,record_decide}.py`、`src/ndf/adapters/{github,github_pulls}.py`、`src/ndf/progress/delivery_decide.py`、`gate/{delivery_check,finishing,asking,release}.py`、`tests/progress/test_release_confirm.py`（新設）
- **変更内容:** 記録の読み取りを `core` へ移し、`--record-pr`・`--no-release`、記録と CD の条件、追認の記録、後ろの工程の無効化と復帰
- **満たす受け入れ条件:** 無し（不変条件 I1 のデプロイ。テスト設計の「デプロイ済み」「CD の結果」「追認」の行）
- **進め方:** 読み取りの移動を先に（今のテストが通る）→ 失敗するテスト → 通す最小実装 → 整理

### Task 7: 出したリクエストの記録と取り下げ

- **対象ファイル:** `progress.json`、`gate/{requests,release,answering,finishing}.py`、`src/ndf/progress/plan/external.py`、`tests/progress/test_requests.py`（新設）
- **変更内容:** 事象 `request_issued`、取り下げ、改修前の依頼の警告、計画の依頼を閉じる、`ndf progress request-status`
- **満たす受け入れ条件:** 無し（決定 9。テスト設計の「出したリクエストの記録」「依頼の有効性」の行）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 8: マージ済みの設計を作り直す

- **対象ファイル:** `progress.json`、`src/ndf/progress/{redo,redo_decide,run,run_approval}.py`、`gate/finishing.py`、`src/ndf/domains/worktree/{cli,merged}.py`、`tests/progress/test_redo.py`（新設）
- **変更内容:** 事象 `redo_recorded`、作業場所の作成、入力の差し替え、取り込みと再開、`ndf worktree remove` と片付け
- **満たす受け入れ条件:** 無し（決定 8。テスト設計の「作り直し」「取り込み」「片付け」の行）
- **進め方:** 失敗するテスト（git は一時リポジトリで実際に動かす）→ 通す最小実装 → 整理

### Task 9: 手順書と用語

- **対象ファイル:** 詳細設計の「手順書」の表のファイル
- **変更内容:** 新しい操作と振る舞いの説明。用語「開き直し」「有効な承認」を用語集へ足し、`ndf records glossary render` で文書を作り直す
- **満たす受け入れ条件:** 23
- **進め方:** テスト駆動は適用しない（文書のため）。`ndf check` と差分のレビューで確かめる

## レビューの単位

同じファイルを続けて触るので、実行順の値の順に 1 つずつ流す。行数は追加と削除の和の見込みで、`git diff --shortstat` で数える。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 | 実行順 |
| --- | --- | --- | --- | --- | --- |
| 分割 | Task 1 | 無し | 約 700 | | 1 |
| 開き直し | Task 2 | 4520:10・4520:11・4520:12・4520:13・4520:16・4520:17・4520:19・4520:20 | 約 700 | | 2 |
| 回答待ち | Task 3 | 4520:1〜4 | 約 900 | | 3 |
| 回答の記録 | Task 4 | 4520:5〜9・4520:14・4520:15・4520:18・4520:21・4520:22 | 約 900 | | 4 |
| 判定と証跡 | Task 5 | 無し | 約 450 | | 5 |
| リリースの確かめ | Task 6 | 無し | 約 900 | | 6 |
| リクエスト | Task 7 | 無し | 約 450 | | 7 |
| 作り直し | Task 8 | 無し | 約 900 | | 8 |
| 手順書 | Task 9 | 4520:23 | 約 250 | | 9 |

400 行の目安を超える単位は、タスクの中で切ると単独でテストが通らないため分けない。1,000 行を超える見込みになった単位は、実装の途中で分け直す。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | `command.py` と `run.py` を分ける（Task 1） | 2026-11-27 の行数は `command.py` 1,000 行、`run.py` 988 行で、`ndf check` の行数の上限 1,000 行に当たる |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | 承認の対象の読み取りと比較（`approval_decide`・`gate/answering.py`・`run_approval.py`）、承認の回答の走査（`StageAnswers`・`approved_head`） | 複数の単位が同じ形の処理に触れる |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 流し直した工程のプランが、マージ済みの PR を前に止まる（設計の「未確認のまま残ること」） | リリース承認の前に、実際の開き直しを 1 回通す（下の「出荷前の確かめ」） |
| `finish` の中の MVV 判定の所要時間が分からない | 実装の後に 1 回測り、PR に書く |
| 進行中のスプリントの記録が新しいコードで読めない | 改修前の事象の並びを読むテストを Task 2・3 に置く |

## 出荷前の確かめ

実装やデプロイまで進んだ利用者が変更を求めた場合に、流し直しが進めなくならないことを、リリース承認を求める前に確かめる。版上げの PR の head から導入した NDF を、試験用のリポジトリで使う。

1. 試験用のリポジトリへ最初の push をする前に、人の承認を得る（条件は「実装中の取り決め」の懸念 3）。承認を得たら、デプロイの工程までを終了し、スプリント自体は進行中のままにする。同じ初期状態のスプリントを 2 つ別々に用意する。一方は、終了した設計の工程への変更要求（`ndf progress question` の後に `ndf answer`）で開き直す。もう一方は、設計の工程への `ndf progress retry ... --later reopen` で開き直す。手順 2 以降は両方で行う
2. `ndf progress next` で流し直し、マージ済みの PR、残っているブランチ、公開済みのバージョンを前に、どのステップが止まるかを記録する
3. 工程が止まったら、停止時の状態を `ndf progress show` と停止の結果で確かめ、下の表の順で再開する。開き直した設計から、実装とデプロイまでを流し直す
4. 再開できない箇所があれば、直すか、手順書へ再開の打ち方を足す。その後、直した版上げの PR の head を導入するか、足した手順を使って、失敗した経路をもう一度流す
5. 両方の開き直しで、デプロイの工程が終了したことを `ndf progress show` で確かめる。各工程で止まった箇所と、再開の打ち方と結果（手順 4 を行った場合は再試行の結果）を PR に書く。書き終えるまで、リリース承認を求めない

手順 3 の再開の順は、停止時の状態で分かれる。

| 停止時の状態 | 再開の順 |
| --- | --- |
| 自動の流れの承認待ち | 回答の承認を `ndf answer` で記録し、`ndf progress next` を打つ |
| 手で終えた設計の工程の承認待ち | 承認を記録する → 承認した HEAD が未マージなら設計 PR をマージする → `next` |
| 手で終えたデプロイの工程の承認待ち（リリース済みの追認） | 追認を記録する → `next` |
| 設計の工程が失敗（手で終える） | 設計 PR を作る（差分が無ければ前の設計 PR を使う） → 出力に `design_pr` を書いて `ndf progress finish`（回答待ちになる） → 「手で終えた設計の工程の承認待ち」の順で進める |
| デプロイの工程が失敗し、リリース対象の差分が無い（手で終える） | デプロイなしの記録を置く → `ndf progress finish --no-release`（回答待ちになる） → リリースせずに工程を終える承認を記録する → `next` |
| デプロイの工程が失敗し、リリースする（手で終える） | `ndf progress question` → 事前の承認を記録する → リリースする → リリース記録を置き、CD の成功を確かめる → 出力を添えて `finish` |
| 承認ゲートを持たない工程が失敗（手で終える） | 作業を終える → 出力を添えて `finish` |
| 実行中のまま停止 | `finish` は受けない。停止の結果が案内する操作を行う |

承認、リリース記録、CD の結果、リリースしないで終える場合の条件は、設計書の「処理の流れ」にある。

## 切り戻し手順

データの移行は無い。ただし、新しい事象を書いた進行中のステートマシンは、前の版で動かさない。前の版は `finish_held` の工程を普通の実行中と読み、工程を初めから流す。手の経路でリリースした後の追認を待っていれば、デプロイをもう一度実行する。開き直しで書いた `担い手: reopen` の証跡も読まないので、取り消した承認が有効に戻る。前の版がほかの新しい事象を読めるかは未確認である。

1. 版を戻す前に、進行中のスプリントのステートマシンを `ndf progress show` で確かめ、手で終えた回答待ち、開き直し（`reopened`）、作り直し用の作業場所があるものを書き出す
2. 該当するスプリントは、新しい版のまま回答して終了させるか、`ndf progress cancel` で中止してから版を戻す。中止する場合は、開き直しで承認を取り消した未マージの PR を閉じる。前の版では取り消しが効かないためである
3. 終える前に戻す必要があるときは、該当するステートマシンに前の版で `ndf progress next` を打たず、その PR をマージしない。直した版へ上げた後に `ndf progress next` で再開する

## 完了の定義

- [ ] 受け入れ条件 1〜23 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `pytest plugins/ndf/tests` と `ndf check` が通る
- [ ] 「出荷前の確かめ」の 2 つの開き直しで、デプロイの工程が終了したことを `ndf progress show` で確かめ、各工程で止まった箇所と再開の打ち方と結果（直した場合は再試行の結果）が PR に書かれている

## 実装中の取り決め（conductor、2026-11-28 JST）

- 利用者は次の 2 つの懸念を残したまま設計を承認した（[課題 #4520 の「設計承認で残った懸念」](https://github.com/devbasex/ai-plugins/issues/4520)の懸念 1・3）。実装の中で次のことを決め、決定と理由を実装の Pull Request に記録する
  - 懸念 1: 自動の流れで人が承認した後に、マージ先や「本番へ反映されるか」が変わった場合に、再承認をどう扱うか。今の設計では承認し直しにならない
  - 懸念 3: 「出荷前の確かめ」は、本体のリリース承認より前に、試験用の別のリポジトリでデプロイの工程まで進む。そこへの公開の有無と、公開を承認する人が決まっていない
    - 決定（2026-11-28 JST、conductor）: 試験用のコードや成果物を別のリポジトリへ出す操作は、実行の前に人の承認を得る。初回の push も含み、そのリポジトリが公開か非公開かは問わない。承認ゲートを通らない場合は、実行せずに人へ戻す
    - 理由: 別のリポジトリへの公開は、NDF の共通原則で必ず人の承認が要る操作（C5）である
    - まだ決めていないこと: 試験用のリポジトリを公開にするか非公開にするか、出すコードと成果物の範囲、承認する人を誰にするか
    - 試験の進め方: 「出荷前の確かめ」は試験用のリポジトリで Pull Request を作ってマージするので、push は必ず要る。最初の push の前に、出すコードと成果物の範囲、出し先のリポジトリ、そのリポジトリが公開か非公開かを示し、その条件で人の承認を得る。承認を得られなければ push せず、人へ戻す
