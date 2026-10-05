# #1645: merge-when-green / release: 同じチェックの古い失敗と、Runner が付かずに取り消されたジョブを CI の失敗と数えて止まる

正は課題の本文（#1645）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

**この課題は #1768 を取り込んだ。** どちらも `merge-when-green` と `release` が CI のチェックの結論をどう数えるか（`plugins/ndf/scripts/merged_lib/checks.py`）の問題なので、1 本の要求と 1 本の設計で扱う。#1768 の受け入れ条件もこの本文が持つ。

**この節は「何を満たすか」だけを扱う。** どう作るか（チェックの束ね方・再実行の回数と間隔・結果の形）は設計が決める。

## 例: 2 つの課題で止まった実物

**#1645（同じチェックの古い失敗）**: 設計 PR 1753（https://github.com/devbasex/ai-plugins/pull/1753 ）の先頭のコミットには、workflow `PR body decisions` のジョブ `check` が 2 件載っていた。

| run | 引き金 | 開始（UTC） | 結論 |
| --- | --- | --- | --- |
| 37264758029 | push（本文の決定を揃える前） | 04:4x | FAILURE |
| 37264973318 | 本文の編集（`edited`） | 04:46:35 | SUCCESS |

今（ndf 10.17.62）: `merged-steps.py merge-when-green 1753` は FAILURE の 1 件を数えて `#1753 の CI が失敗: check` で止まる（2 回続けて止まった）。案内は「`gh pr checks 1753` で失敗を読み、直して push してから打ち直す」だが、直すものは無い。conductor が `gh run rerun 37264758029` を手で打ち、3 回目でマージした。

**#1768（Runner が付かなかった取り消し）**: 本番 10.17.62 の配布で、リリースの PR #1765（https://github.com/devbasex/ai-plugins/pull/1765 ）の CI が GitHub Actions の障害（2026-10-05 19:50Z〜21:58Z）に当たった。

| ジョブ | 状態 | 結論 | ステップの数 | Runner の名前 |
| --- | --- | --- | --- | --- |
| Lint の `lint`（run 37364001907 の試行 1） | completed | cancelled | 0 | 空 |
| Runtime plugin smoke の `ci-scope`（job 111944834887） | completed | cancelled | 0 | 空 |

今: `release-steps.py release` は `#1765 の CI が失敗: lint` などで止まり、judge が `fix` を 3 回選んで修正の worker が空振りした（release → judge → fix の往復 19 ステップ、$0.857）。再開は conductor の手作業（githubstatus の確認・`gh run rerun --failed`・プランを `-r2`・`-r3` へ写して `run --from release`）だった。

変えた後:

1. PR 1753 の形では、新しい実行（37264973318）の SUCCESS をそのチェックの結論とし、古い FAILURE は数えずにマージへ進む
2. PR #1765 の形では、取り消しを「CI の基盤待ち」として失敗と分け、実行が終わるのを待って取り消されたジョブを再実行し、上限まで待ち直す。上限を使い切ったら「基盤待ちで止まった」と返し、`release` のプランは `fix` へ回さない

## 依頼（原文）

課題 #1645 の元の本文:

> ## 何を見つけたか
>
> `merged-steps.py merge-when-green 1641` が `#1641 の CI が失敗: check, check` で止まった。ところが同じ名前の新しい実行は成功していた。
>
> - `statusCheckRollup` に `check`（workflow `PR body decisions`）が 3 件あった。run 37047684024 と 37047686558 は FAILURE、run 37049425669 は SUCCESS
> - 失敗の 2 件は、本文の決めたことを同期する前に走った実行である。成功の 1 件は同期の後の実行
> - GitHub の判定は、保護の規則の必須チェックとしては BLOCKED で、マージを塞いでいたのは古い失敗の 2 件だった
> - 失敗の 2 件を `gh run rerun` で打ち直すと、merge-when-green は通ってマージした
>
> merge-when-green は、同じ名前のチェックが複数あるとき、新しい実行で置き換わった失敗も失敗として数える。
>
> ## どこで見つけたか
>
> PR #1641（https://github.com/devbasex/ai-plugins/pull/1641 ）の承認ゲート 1 の後のマージ。2026-10-03 JST。
>
> ## なぜこの変更の範囲外なのか
>
> スプリント m725 の受け入れ条件に、マージの待ちは入っていない。GitHub の側でも古い実行が必須チェックを塞いでいる。打ち直しを merge-when-green が自分で行うか、案内だけにするかは方針の判断になり、即時修正の 4 条件の 3 つ目を満たさない。根拠: Value 1・Value 4（MVV 版 2）。
>
> ## 直さないと何が起きるか
>
> 本文を `pull_request` の `edited` で検査するワークフロー（PR body decisions など）が 1 度でも落ちた PR では、直した後も merge-when-green が止まる。conductor が失敗の実行を探して手で打ち直すことになる。案内の `gh pr checks 1641 で失敗を読み、直して push してから打ち直す` では、直すものが既に無いため読み手が迷う。
>
> ## 由来
>
> issue #1289（PR #1641）
>
> ## 再発（2026-10-05、スプリント m815 の設計 PR 1753）
>
> 設計 PR 1753 の `merge-when-green` が 2 回続けて `#1753 の CI が失敗: check` で止まった。`PR body decisions` の push 時の実行（run 37264758029）は、本文の決定を揃える前で FAILURE のまま残っていた。本文の編集で走った新しい実行（run 37264973318）は SUCCESS だった。`gh run rerun 37264758029` で古い実行を通し直してから 3 回目でマージできた。

取り込んだ課題 #1768 の元の本文（要点の節）:

> ## 何を見つけたか
>
> 本番 10.17.62 の配布（`8-release-prod.json`）の `release` のステップが、リリースの PR #1765 の CI を「失敗」と読んで止まり、judge が `fix` を 3 回選んで修正の worker が空振りした。
>
> - 実際は GitHub Actions の障害（githubstatus 「Incident with Actions」19:50Z〜21:58Z）で Runner が割り当たらず、ジョブがステップを 1 つも動かさずに `cancelled` で終わっていた（Lint の run 37364001907 は 16 分待って取り消し、Runtime plugin smoke の ci-scope の job 111944834887 は 19 分待って取り消し。Runner の名前は空・ログは 404）
> - `release` の出力は「#1765 の CI が失敗: lint」「… 失敗: check, ci-scope, runtime-plugin-build-check, …」「… 失敗: ci-scope」で、取り消しと失敗を区別しない
> - judge は「変更物の形の誤り」「PR の中身が検査に落ちた」と読んで `fix` を選んだ（12-judge だけは「CI の揺れ」と読んで `release` を選んだ）。修正の worker 3 回は直すものが無く、最後は exit 1 でプランが止まった（release → judge → fix の往復 19 ステップ、$0.857）
> - 再開は conductor の手作業だった: githubstatus を見て障害と判断し、復旧を背景で待ち（5 分ごと・上限 3 時間）、`gh run rerun --failed` を打ち、プランを `-r2`・`-r3` へコピーして `run --from release` を 2 回打った。r2 は障害の最中に流して同じ理由で止まった
>
> ## どこで見つけたか
>
> - 記録: `~/.local/state/ndf/sv/sprint-m815/8-release-prod-state/`（`07-release.out`〜`19-fix.out`・`report.md`）
> - `plugins/ndf/scripts/merged_lib/checks.py` 23 行の `FAIL_CONCLUSIONS` が `CANCELLED` を失敗に含め、266 行で取り消しのジョブを `failed` へ入れる
> - 由来の PR: #1765（本番のリリース）、#1763（スプリント m815 の検査）
>
> ## 直さないと何が起きるか
>
> - GitHub Actions の障害や Runner 不足のたびに、配布とマージのプランが「CI の失敗」として止まり、LLM の判断と修正の worker に費用を使ったうえで、conductor が障害を調べて再実行と再開を手で行う
> - 取り消しを失敗と書くため、報告を読んだ人が中身の不具合と誤る
>
> 期待する振る舞いの案:
> - ステップが 0 件のまま `cancelled` になったジョブ（Runner が付かなかった取り消し）を失敗と分け、「CI の基盤待ち」として返す
> - その場合は fix へ回さず、run の完了を待って `gh run rerun --failed` を打ち、上限まで待ち直す。GitHub の状態（`https://www.githubstatus.com/api/v2/components.json` の Actions）が劣化中なら、復旧まで待つ

取り込みの指示（2026-10-05、conductor の起動指示）:

> #1645。この要求へ次を取り込む: #1768（Runner が付かずに取り消された CI のジョブを merge-when-green / release が失敗と数え、GitHub Actions の障害の間も fix へ回して空振りする）。どちらも merge-when-green と release が CI のチェックの結論をどう数えるか（merged_lib/checks.py）の問題として 1 本の設計で扱う。

## 今の状態（2026-10-05 に develop の eb65817b で調べた）

- `merged_lib/checks.py:23` の `FAIL_CONCLUSIONS` は `FAILURE`・`CANCELLED`・`TIMED_OUT`・`ACTION_REQUIRED`・`STARTUP_FAILURE`・`STALE` を失敗とする。`check_states`（27 行）は `statusCheckRollup` の項目を 1 件ずつ見て、同じチェックが複数あっても全部を数える
- `GreenWatch._settle_pending`（258 行）は、表示が pending のまま実行が終わったチェックをジョブの結論で扱い、`cancelled` も `FAIL_CONCLUSIONS` で失敗へ入れる
- `GreenWatch.poll`（289 行）は失敗が 1 件でもあれば、その場で `#<PR> の CI が失敗: <名前>` と `next`「`gh pr checks <PR>` で失敗を読み、直して push してから打ち直す」を出して止まる。名前は workflow を含まないので、PR 1753 では `Glossary`・`PR body decisions`・`Script structure` の 3 つの workflow がどれも `check` と出る
- `merged-steps.py probe`（`_classify_checks`）も同じ `check_states` と `FAIL_CONCLUSIONS` を使い、失敗を `failed` → 手 `fix` に分ける。`release` の雛形の `probe`（`supervise_lib/release_templates.py:191`）がこれを呼ぶ
- `release-steps.py release` は `wait_and_merge`（682 行）で `merge-when-green --no-cleanup` を呼び、止まると `PR #<n> をマージできない: <merge-when-green の summary>` の `StepError` を出す。雛形の `release` のステップは `on_fail: judge` で、judge が summary を読んで次を選ぶ
- `merge-when-green` は `gh pr merge --admin` でマージする（`merged_lib/merge.py`）。管理者の迂回で、GitHub が必須チェックで BLOCKED と判定していてもマージは通る
- `statusCheckRollup` の CheckRun は `name`・`workflowName`・`status`・`conclusion`・`startedAt`・`completedAt`・`detailsUrl`（`/actions/runs/<run>/job/<job>`）を持つ。再実行した実行は同じ run の番号で `startedAt` が新しくなる（PR 1753 の 37264758029 は再実行の後 08:53:21）
- Runner が付かなかった取り消しのジョブは、REST の `GET /repos/{owner}/{repo}/actions/jobs/{job_id}` で `conclusion: cancelled`・`steps` が 0 件・`runner_name` が空になる（run 37364001907 の試行 1 の `lint`、job 111944834887 の `ci-scope` で確かめた）。`statusCheckRollup` の項目だけでは区別できない
- `lib/gh_checks.py` の `fold_check_runs` は、REST の check run を名前ごとの最新の 1 件へ畳む（#632、`test-run.py` が使う）。新しさは `completed_at` と `started_at` の新しい方 → run の番号で決める。名前だけで束ね、workflow の名前を持たない

## 目的

- **同じチェックが新しい実行で通ったら、古い失敗で止まらない。** 直した後に conductor が古い実行を探して打ち直す手作業を無くす
- **GitHub Actions の基盤の都合（Runner が付かない取り消し）を、PR の中身の失敗と分ける。** 基盤待ちの間は待ちと再実行をスクリプトが行い、LLM の判断と修正の worker に費用を使わない
- **止まったときの報告から、何が起きたかを読み違えない。** 中身の失敗・基盤待ち・古い失敗を、出力の上で別のものとして書く

## 解釈

| 依頼文の語 | 具体化 |
| --- | --- |
| 同じ名前のチェック | 同じ PR の先頭のコミットに載る CheckRun のうち、`workflowName` と `name` の組が同じもの。StatusContext は `context` が同じもの。`name` だけでは束ねない（PR 1753 では 3 つの workflow の `check` が別のチェック） |
| 新しい実行 | 同じチェックの項目のうち `startedAt` が最も新しいもの。`startedAt` が無い項目（まだ始まっていない）は最も新しいとみなす |
| 置き換わった失敗 | 同じチェックに、より新しい項目があるときの古い項目の失敗。チェックの結論には数えない |
| Runner が付かなかった取り消し | 結論が `cancelled` で、ジョブのステップが 0 件、Runner の名前が空のジョブ。障害のときに起きる |
| 基盤待ち | Runner が付かなかった取り消しがあり、ほかに中身の失敗が無い状態 |
| fix へ回さない | `release` のプランが基盤待ちで止まったとき、judge に `fix` を選ばせない。待ちと再実行はスクリプトの中で行い、使い切ったら止まった理由を「基盤待ち」と書く |

## 前提

- 前提 1: チェックの結論は、同じチェックの最新の項目だけで決める。先頭のコミットが同じなので、古い項目と新しい項目は同じ中身を検査している（引き金が push か本文の編集かは問わない）。最新の項目が失敗なら失敗、pending なら pending、成功なら成功である
- 前提 2: 置き換わった失敗は再実行しない。マージは今のとおり `gh pr merge --admin` で行い、GitHub の必須チェックの判定（BLOCKED）は管理者の迂回で通す。マージが拒まれたときは今の経路（`gh pr merge --admin が失敗`）で止まり、置き換わった失敗の run の番号と再実行のコマンドを `next` に添える
- 前提 3: Runner が付かなかった取り消しの判定には、取り消されたジョブ 1 件ごとに REST のジョブの照会を 1 回使う。取り消しの無い PR では照会を足さない
- 前提 4: 取り消しのうち、ステップが 1 件以上あるもの（実行の途中で人や concurrency が取り消したもの）は今のとおり失敗として数える。同じチェックの新しい項目があれば前提 1 で置き換わる
- 前提 5: 基盤待ちの再実行は、取り消されたジョブを持つ実行が終わってから、その実行の失敗したジョブを打ち直す（`gh run rerun <run> --failed`）。同じ実行の再実行には回数の上限を置き、待ちは `merge-when-green` の待ちの上限（`--timeout`）の中で行う。回数と間隔の既定値は設計が決め、引数で変えられる
- 前提 6: githubstatus（`https://www.githubstatus.com/`）は github.com だけが持つ。読むなら報告の材料に限り、読めなくても待ちと再実行の判断は変えない（GitHub Enterprise Server でも同じ手順で動く。Value 5）
- 前提 7: `release-steps.py release` は `merge-when-green` の結果を読み、基盤待ちで止まったことを出力で区別して返す。`release` の雛形は、その出力で judge を通さずに「待ち直し」か「止まる」へ進む（`fix` へ行かない）。形（終了コードか結果の項目か）は設計が決める
- 前提 8: `merged-steps.py probe` も同じ数え方を使う。数え方の正本は `merged_lib/checks.py` の 1 か所に置き、`merge-when-green`・`release`・`probe` の 3 つで分けない（Value 6）
- 前提 9: 検証は GitHub の実物を壊さずに行う。数え方は記録した `statusCheckRollup` とジョブの形（この本文の「例」の 2 件）を入力にした単体テストで確かめ、障害は再現しない

## 対象範囲

含む:

- 同じチェックの束ね方と、最新の項目だけで結論を決めること（`check_states` と `_settle_pending` の数え方）
- Runner が付かなかった取り消しの判定と、基盤待ちとしての再実行・待ち直し（`merge-when-green` の中）
- `merge-when-green` の止まったときの出力（summary・items・`next`）で、中身の失敗・基盤待ち・置き換わった失敗を分けること。チェックの名前に workflow の名前を添えること
- `release-steps.py release` が基盤待ちを区別して返すことと、`release` の雛形が基盤待ちを `fix` へ回さないこと
- `merged-steps.py probe` の分類に基盤待ちを足し、手を `fix` にしないこと
- `lib/gh_checks.py` の `fold_check_runs` と新しさの規則を共有するかの判断（共有するなら同じ規則へ寄せる）

含まない:

- `gh run rerun` で置き換わった失敗を打ち直すこと（前提 2）
- `TIMED_OUT`・`STARTUP_FAILURE`・`ACTION_REQUIRED`・`STALE` の扱いの変更（今のとおり失敗）
- githubstatus を待ちの判断に使うこと（前提 6。報告の材料に載せるかは設計が決める）
- `test-run.py` の `checks_outcome` が名前だけで束ねていることの直し（宣言のチェック名を引くので衝突は今見つかっていない。共有の結果として変わる場合を除く）
- `cross-review` の CI の見方、`sprint-close.py` のマージの待ち
- プランを `-r2` へ写して再開する手順そのものの変更（基盤待ちで止まらなければ要らなくなる）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | PR のチェックの一覧を読んだ | `merge-when-green` の 1 回の読み直し | `gh pr view` が失敗 → 今のとおり止まる | — |
| E2 | 同じチェックを束ね、最新の項目を選んだ | E1 | `startedAt` が読めない → 前提の規則（無い項目は最新）で選ぶ | E1 |
| E3 | 置き換わった失敗を記録した | E2 で古い失敗があった | — | E2 |
| E4 | 取り消しのジョブを照会し、Runner が付かなかったと判定した | E2 の最新の項目が `cancelled` | 照会が失敗 → 判定できないので失敗として数え、照会できなかったことを出力に書く（AC9） | E2 |
| E5 | 中身の失敗で止まった | E2 の最新の項目に E4 に当たらない失敗がある | — | E2・E4 |
| E6 | 取り消しを含む実行が終わるのを待った | E4 | 待ちの上限 → E9 | E4 |
| E7 | 取り消されたジョブを再実行した | E6 の後、再実行の回数が上限の内 | `gh run rerun` が失敗 → 基盤待ちとして止まる（E9） | E6 |
| E8 | 全部のチェックが通り、マージした | E2 の最新の項目が全部通った | `gh pr merge --admin` が拒まれた → 止まり、置き換わった失敗の再実行を案内する（AC4） | E2（E7 の後もありうる） |
| E9 | 基盤待ちで止まった | 再実行の回数か待ちの上限を使い切った | — | E6 か E7 |
| E10 | `release` が基盤待ちを区別して返した | E9 が `release` の中で起きた | — | E9 |
| E11 | `release` のプランが fix へ回らずに次へ進んだ | E10 | — | E10 |

E4 の照会の失敗は受け入れ条件 AC9 へ、E8 のマージの拒否は AC4 へ移した。E7 の回数と E6・E9 の上限の値は前提 5 で設計へ渡した。

## 用語

| 用語 | 意味 |
| --- | --- |
| 置き換わった失敗 | 同じチェック（`workflowName` と `name` の組）に、より新しい項目があるときの古い項目の失敗。結論に数えない |
| 基盤待ち | Runner が付かずに取り消されたジョブ（結論 `cancelled`・ステップ 0 件・Runner の名前が空）があり、ほかに中身の失敗が無い状態 |

## 受け入れ条件

- [ ] AC1: PR 1753 の形（同じ `PR body decisions` / `check` に、古い FAILURE と新しい SUCCESS がある。ほかのチェックは全部 SUCCESS）の `statusCheckRollup` を与えると、`merge-when-green` の 1 回の読み直しは失敗で止まらず、通った扱いになる。結果の items に置き換わった失敗の run の番号（37264758029）が「置き換わった」として 1 件載る
- [ ] AC2: 同じチェックの新しい項目が FAILURE で古い項目が SUCCESS のとき、`merge-when-green` は失敗で止まる（新しい失敗を古い成功で隠さない）
- [ ] AC3: `workflowName` が違い `name` が同じ（`Glossary` / `check` と `PR body decisions` / `check`）チェックは別のチェックとして数える。片方が FAILURE なら、もう片方の SUCCESS があっても失敗で止まる
- [ ] AC4: `merge-when-green` がマージを試みて `gh pr merge --admin` が拒まれ、置き換わった失敗があったとき、止まった結果の `next` に置き換わった失敗の run ごとの `gh run rerun <run>` が載る
- [ ] AC5: 中身の失敗で止まったときの summary と items は、チェックを `<workflowName> / <name>` の形で示し、失敗した最新の項目の run の番号を持つ
- [ ] AC6: PR #1765 の形（`Lint` / `lint` の最新の項目が `cancelled` で、そのジョブのステップが 0 件・Runner の名前が空。ほかに失敗が無い）を与えると、`merge-when-green` は「CI が失敗」で止まらず、その実行が終わった後に `gh run rerun <run> --failed` を 1 回打って待ち直す
- [ ] AC7: AC6 の再実行を回数の上限まで行っても取り消しが続くとき（または待ちの上限に達したとき）、`merge-when-green` は「CI の基盤待ち」と分かる summary と、基盤待ちを示す結果の項目で止まる。summary に「CI が失敗」の語を使わない。`next` は中身を直す案内でなく、復旧の後に打ち直すコマンドを示す
- [ ] AC8: 最新の項目が `cancelled` でもステップが 1 件以上あるジョブは、今のとおり失敗として数えて止まる（AC6 の再実行をしない）
- [ ] AC9: 取り消されたジョブの照会が失敗したときは、失敗として数えて止まり、照会できなかったことを items に書く（基盤待ちへ倒さない）
- [ ] AC10: 基盤待ちと中身の失敗が同じ読み直しで両方あるときは、中身の失敗で止まる（再実行で待たない）
- [ ] AC11: `release-steps.py release` が、`merge-when-green` の基盤待ちの停止を受けたとき、中身の失敗とは別の形（終了コードか結果の項目）で返す。中身の失敗のときの形は今と変わらない
- [ ] AC12: `release` の雛形（`supervise.py new release` が作るプラン）で、`release` のステップが基盤待ちで止まると、judge を通らず `fix` のステップへ進まない。待ち直しか止まるかの行き先がプランに書かれている
- [ ] AC13: `merged-steps.py probe` は、AC1 の形を `failed` に分けず、AC6 の形を基盤待ちの分類に分けて手を `fix` にしない
- [ ] AC14: 取り消しの無い PR では、1 回の読み直しで使う `gh` の呼び出しの数が今と変わらない（ジョブの照会を足さない）
- [ ] AC15: 同じチェックの束ね方と新しさの規則は 1 か所で定義され、`merge-when-green`・`release`・`probe` が同じものを使う（`check_states` 相当の関数を別に持たない）
- [ ] AC16: 既存のテスト（`plugins/ndf/scripts/tests/test_merged_probe.py`・`test_sprint_close_merge_green.py` ほか `merged-steps.py` と `release-steps.py` のテスト）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 取り消しの無い PR の読み直しで `gh` の呼び出しを足さない（AC14）。基盤待ちの再実行は回数の上限を持つ |
| 運用・保守性 | 止まった結果を読んだ人が、中身の失敗・基盤待ち・置き換わった失敗のどれかを summary だけで言える（AC5・AC7） |
| システム環境 | githubstatus を読めない環境（GitHub Enterprise Server・ネットワークの制限）でも同じ判断で動く（前提 6） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる（互換あり）。`merge-when-green` の結果に基盤待ちと置き換わった失敗の項目が増え、summary のチェック名に workflow の名前が付く。`probe` の分類に基盤待ちが増える。`release` の基盤待ちの返し方が増える。既存の分類・終了コードの意味は変えない |
| データ | 無し |
| 既存の振る舞い | 同じチェックに新しい成功がある古い失敗で止まらなくなる。Runner が付かなかった取り消しで止まらず再実行する |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4` |
| 静的解析・型検査 | 継続的統合の `Lint`・`Script structure` |
| 手動確認 | 次に起きた PR body decisions の古い失敗、または GitHub Actions の障害のときの `merge-when-green` と `release` の出力を、リリース後テストで読む（障害は再現しない。前提 9） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 数え方は `plugins/ndf/scripts/merged_lib/checks.py` に置く。`release-steps.py` と `merged-steps.py` は呼ぶだけにする（前提 8）。実験版の置き場は使わない（既定で動く安定版の変更） |
| コーディング規約 | `AGENTS.md` のポリシーと `script-structure` の検査 |
| テスト戦略 | 数え方・判定・再実行の決定は、記録した rollup とジョブの JSON を入力にした単体テストで担保する。`gh` は差し替える。`.md` の文言は照合しない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、`merge-when-green`・`release`・`probe` の 3 つを同じ数え方へ揃えること |
| 確認してから行う | `release` の終了コードの意味を変えること（既存の値の意味は変えない） |
| 行わない | 置き換わった失敗の自動の再実行、githubstatus を待ちの判断に使うこと、範囲外の結論（`TIMED_OUT` など）の扱いの変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 基盤待ちの再実行の回数・間隔の既定値と、待ちの上限（今の `--timeout` 3600 秒で足りるか。#1768 の障害は約 2 時間） | 設計 | 設計 PR |
| `release` が基盤待ちを返す形（専用の終了コードか、結果の項目か）と、雛形の行き先（待ち直しのステップか、止まって conductor へ戻すか） | 設計 | 設計 PR |
| githubstatus の Actions の状態を報告の材料に載せるか | 設計 | 設計 PR |
| `lib/gh_checks.py` の `fold_check_runs` と新しさの規則を共有するか（束ねる鍵が名前だけか workflow を含むかが違う） | 設計 | 設計 PR |
