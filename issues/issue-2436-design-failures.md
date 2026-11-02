# 並列開発の設計: 異常系（#2436 の受け入れ条件 6g・6h）

設計文書 [issue-2436-design.md](issue-2436-design.md) から、異常系の扱いを分けたファイル。問いの集め方は [issue-2436-design-approvals.md](issue-2436-design-approvals.md)、レーンのループの側は [issue-2436-design-lane-loop.md](issue-2436-design-lane-loop.md) にある。決定の理由は [issue-2436-design-decisions.md](issue-2436-design-decisions.md) の決定 20 にある。

## 一時レーンの失敗（受け入れ条件 6g）

一時レーンも問いの hook の経路に乗る（役割のファイルが `helper`）。直せない CI の失敗・衝突は、一時レーンが問いとしてオーガナイザーへ送る。一時レーン自体が止まったときは、耐久ワークフローの観測が見つけてオーガナイザーへ問いを立てる。

| 止まり方 | 検知 | オーガナイザーの問い |
| --- | --- | --- |
| 一時レーンのラッパーが止まり、`settle` も `gate2` の依頼も届いていない | 観測の耐久ステップが `relay.py ls --json` で一時レーンの `state: stopped` を見る | 「続きから起こし直す」「止める」。起こし直すと、新しい一時レーンが配布のプランを `supervise.py queue ... --resume` で続ける |
| 動いているが答えが来ない | 一時レーンの `at`（最後に動いた時刻）が下の「動かないレーン」の時間より前 | 「起こし直す」「止める」「待つ」 |
| `main` へのマージの後に止まった（タグ・GitHub Release・導入の確認・リリース記録・後片付けの前） | 本番の Pull Request（head が `release/v<版>`、base が `main`）がマージ済みか（`gh pr list --head release/v<版> --base main --state merged --json number,mergedAt` が 1 件）。`release` のステップの終了コードでは決めない（`release` のステップは `main` のマージの後にタグと GitHub Release も作るため、その途中で止まると 0 の記録が残らない） | 差し戻しの選択肢を出さず「続きから流す」だけを出す。済んだステップの一覧を添える |

`main` へのマージの後は、差し戻しても `main` から取り消せないため、選択肢から外す。マージ済みの判定は耐久ステップ「観測」が GitHub から読み、マージ済みなら `release` のステップの記録によらず「続きから流す」だけを出す。続きの `release` のステップはタグと GitHub Release がすでにあるかを見て、無いものだけを作る。

## 異常系の一覧（受け入れ条件 6h）

| # | 起きること | 検知 | 扱い | テストの観点 |
| --- | --- | --- | --- | --- |
| 1 | オーガナイザーの claude が切り替わる（カットポイント・利用上限・落ちた） | 新しいセッションが `--parallel <計画名>` の同じ 1 行で始まる | `parallel.py run` は conductor の Bash の背景の子にしない。`parallel.py start <計画名>` が `run` をセッションから切り離して起動する（`subprocess.Popen(..., start_new_session=True)`。`relay_lib/login.py` に前例がある。出力は `.ndf/parallel/<計画名>/run.log`）。`run` は実行の鍵の排他（`durable.launch`）を待たずに取り（`lock_timeout=0`）、取れなければ今のプロセス（`run.pid`）を指して 0 で終わる。オーガナイザーの引継ぎ文書は `.ndf/handoff/parallel-<計画名>.md`（`handoff.md` の形。「次に実行するコマンド」は起動と同じ 1 行）。カットポイントでは、問いを出していなければ `ndf-next` を出して切る。問いの途中で切り替わったら、新しいセッションの `wait --after 0` が答えていない問い（イベント `questions`）を返し、出し直す | `run` を 2 回打つと 2 本目が 0 で今の pid を返す。`start` の後に conductor のプロセスを止めても `run` が残る。答えていない問いが新しいセッションの `wait` に返る |
| 2 | 同じ計画の `--parallel` を 2 つのセッションで始める | `start` が、耐久ワークフローに記録したオーガナイザーの作業ディレクトリ（`NDF_RELAY_DIR`）と自分を比べ、記録の側のラッパーが動いている | 断る（終了コード 1）。理由と今の持ち主（作業ディレクトリと名前）を返す。記録の側が止まっていれば、`kind: organizer` のメッセージで持ち主を移して続ける | 持ち主が動いていれば断る・止まっていれば移る |
| 3 | 動かないレーン | 観測の耐久ステップで、レーンの `relay.py ls` の `at` が `stall_minutes`（`plan.json`。既定 60 分）より前で、承認待ち（`awaiting_approval`）でも保留でもない | 問い（`kind: stall`、区分 `ndf-ask:human-action`）を立てる: 「起こし直す（`relay.py restart`）」「止める（`relay.py stop`）」「待つ（次の `stall_minutes` まで問わない）」 | 60 分を越えたら 1 回だけ問いが立つ。「待つ」の後は次の区切りまで立たない |
| 4 | 配布を差し戻した変更が `develop` に残る | `settle --rejected --to <レーン>` が差し戻した Pull Request を記録する | 宛先のレーンが直したと報告するまで、耐久ワークフローは配布を自動で組まない。報告は、直しの周回の配布の依頼に `fixes: <差し戻した配布の連番>` と直す・revert の Pull Request を付けたメッセージで行う。報告より前に別の依頼で配布を組む必要が出たら（宛先のレーンが止まった等）、オーガナイザーへ問いを立て、承認資料に「差し戻した変更が範囲に残っている」と Pull Request を示す | 報告の前に配布が組まれない。報告の後に組まれる。残っている間の資料に印が付く |
| 5 | 同じ原因の問いが複数のレーンから届く（`gh` の認証切れ・ディスク不足・ネットワーク・API の上限） | 区分と、問いの文面を正規化した値（空白をまとめ、レーン名・パス・番号を伏せる）が同じ | 1 つの問いにまとめて出し、答えを依頼してきた全レーンへ配る（`answer-<依頼>` をそれぞれに立てる） | 2 レーンから同じ文面の `ndf-ask:human-action` が届くと問いは 1 つで、答えは 2 つ立つ |
| 6 | 手元の作業が待ちだけになったレーン | ループの周回の境で、選べる課題が無く（`no_issue`）、保留の周回か答えの無い人の判断待ちがある。または保留の周回が `release_pending_max` に達した | ループは新しい止まる理由 `waiting_only`（待ちだけ）で止まる。`no_issue` で止まらない（見直しも挟まない）。セッションを終え、統括役の上限の枠を空ける。オーガナイザーは答えを送るとき、宛先のレーンが止まっていれば `relay.py restart <レーン>` の 1 行を出す | 待ちだけのとき `waiting_only` で止まり `no_issue` で止まらない。答えの後に起こされた境で続きを流す |
| 7 | レーンの間で課題・マイルストーンが重なる | 検査（I19） | 誤り（終了コード 1）。スプリントのレーンの `input.issues` どうし、ループのレーンの `--milestones` どうし、スプリントの課題がループのマイルストーンに入るものを重なりとする。`--milestones` の無いループのレーン（すべてのマイルストーン）は、ほかのレーンと必ず重なる | 重なる組・`--milestones` の無いループが 2 本で誤り。重ならなければ通る |
| 8 | メインディレクトリの git の操作が複数のレーンで同時に走り、`.git/index.lock` が衝突する | — | メインディレクトリで index か HEAD を動かす操作を、`<メインディレクトリ>/.git/ndf-main.lock` の排他（`lib/locks.py`）で 1 本ずつにする。当たる操作は下の表 | 2 プロセスが同時に後片付けを打つと、片方が待ってから成功する |
| 9 | 設計の「直して出し直す」が同じ課題で続く | 人の判断待ちの行の `revise_count` | 3 回続いた後の 4 回目の問いは、選択肢に「やらない」「方針を変える（指摘に新しい方針を書く）」を含める | 4 回目の問いの選択肢に 2 つが入る |
| 10 | 問いの後に状況が変わり、古い答えを使う | 依頼に問いの時点の印（設計の承認なら設計 PR の先頭の SHA、承認ゲート 2 なら承認資料の承認したコミット、承認ゲートの外の問いなら作業の場所の `HEAD`）を付け、答えにも同じ印を付ける | レーンは答えを使う前に今の印と比べ、違えば答えを使わずに問い直す（新しい依頼を送る） | 設計 PR へ push した後に届いた承認は使われず、問い直しが送られる |
| 11 | 止まったレーンの配布待ちのまとめが残る | 計画を終える条件（全レーンが止まり、答えていない依頼が無い）を満たした時点で、`released` を受けたのにまとめを流していない保留の周回がある | `finished` の前に、一時レーンを起こして `sprint-state.py loop pending <ループ>` が返すまとめの `pipeline` を流させる。流し終えてから `finished` にする | 止まったレーンの保留の周回が残ると `finished` の前に一時レーンが起こされる |
| 12 | 配布の途中の `parallel.py stop` | 一時レーンが動いていて、`settle` がまだ | レーンへ `relay.py stop` を打ち、配布が終わる（`settle` が届く）のを待ってから耐久ワークフローを `stopped` で終える。`--force` なら一時レーンも止める。本番のプランの状態ディレクトリは消さず、済んだステップを残す（新しい計画から `--resume` で続けられる） | 配布の途中の `stop` は `settle` の後に終わる。`--force` は待たずに終わり、状態ディレクトリが残る |
| 13 | 利用上限で全レーンが止まる | 観測で、すべてのレーンが動いていない（`at` が進まない）か止まっている | アカウントの切り替えと再開はラッパーに任せる。オーガナイザーは、全レーンが上限で止まったと分かった時点で 1 回だけまとめて知らせる（`notice`）。レーンごとには知らせない | 全レーンの停止で `notice` が 1 回だけ立つ |
| 14 | GitHub API の同時本数の枠（GraphQL 2 本）がプロセスごとで、レーンをまたいで数えない | — | この設計では扱わない。リリース後テストで、レーン 3 本と一時レーンの同時の呼び出しで上限（403・二次の上限）に当たるかを測る。当たれば別の課題にする | （リリース後テスト） |
| 15 | 計画の途中でプロジェクト MVV が変わる | レーンが `mvv_changed` で止まる | オーガナイザーは止まったレーンをまとめて 1 回知らせる。計画は新しい計画名で始め直す（`manual` / `stopped` / `finished` から `running` に戻らない） | 2 レーンが `mvv_changed` で止まると `notice` が 1 回 |
| 16 | `main` へのホットフィックス | — | 範囲外。並列の計画は扱わない（「未確認のまま残ること」） | — |
| 17 | 一時レーンを起こせない（統括役が断る・tmux が無い） | `relay.py spawn` の終了コード | 次の観測の周で起こし直す。3 回続けて断られたら、理由を添えた問いを立てる | 3 回目の断りで問いが立つ |
| 18 | 耐久の記録のファイルが読めない・消えた（掃除・壊れた） | `run` が `durable.launch` で `DurableError` | `run` が終了コード 2 で止まり、オーガナイザーは理由を出して終える。計画は新しい計画名で始め直す。レーンは計画に属さない（I12）ため、今の本番のプランを流す | 記録の無い計画でレーンが依頼を送らない |
| 19 | 出す版のタグがもう在る（計画の外で同じ版が出た） | queue の版の差し込みが止まる（`fill_release_version`） | 一時レーンが問いとして送る（区分 `ndf-ask:irreversible`）。「次の版で出す」「止める」 | 版の重なりで問いが立つ |

### メインディレクトリの git の操作（異常系 8）

| 操作 | 置き場 | 排他を取る箇所 |
| --- | --- | --- |
| マージの後の後片付け（worktree を外す・ブランチを消す・メインディレクトリの pull） | `plugins/ndf/scripts/merged-steps.py` の `_cleanup_pr`（`_remove_pr_worktrees`・`_delete_branch`・メインディレクトリの pull） | `_cleanup_pr` の全体 |
| worktree を作る | `plugins/ndf/scripts/supervise_lib/paths.py` の `_add_worktree_with_retry` | `git worktree add` の 1 回ごと（今の `.git/config` の lock のやり直しは残す） |
| メインディレクトリの追従（`follow_branch: true`） | `plugins/ndf/scripts/worktree-session.sh` の `git -C "$MAIN_DIR" checkout -q --detach` と `fetch` | `flock <メインディレクトリ>/.git/ndf-main.lock` で囲む |

引継ぎ文書の更新（`sprint_loop_record.py` の `update_handoff`）はレーンごとに別のファイルを書き、git を動かさないため当たらない。

## 触るファイル

`plugins/ndf/scripts/lib/parallel_run.py`（`start`・観測の動かないレーンと一時レーン・問い `stall`・重複の正規化・終わりの前のまとめ・`stop --force`）、`plugins/ndf/scripts/lib/parallel_plan.py`（I19・`stall_minutes`）、`plugins/ndf/scripts/lib/parallel_lane.py`（答えの印の比べ）、`plugins/ndf/scripts/lib/sprint_loop_record.py`・`sprint_loop_select.py`（止まる理由 `waiting_only`・`revise_count`）、`plugins/ndf/scripts/lib/sprint_loop.py`（`loop pending`）、`plugins/ndf/scripts/merged-steps.py`、`plugins/ndf/scripts/release-steps.py`（本番の Pull Request がマージ済みなら、マージを飛ばし、タグと GitHub Release のうち無いものだけを作って続ける）、`plugins/ndf/scripts/supervise_lib/paths.py`、`plugins/ndf/scripts/worktree-session.sh`、`plugins/ndf/skills/development-workflow/references/parallel.md`（オーガナイザーの引継ぎ文書とカットポイント）

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 切り離した `run` の寿命 | `start_new_session=True` で起動した `run` が、ラッパーの終わり・コンテナの中のセッションの切り替えで残るかは、実装で確かめる。残らなければ統括役の tmux のセッション（`relay_lib/tmux.py` の `new-session -d`）で起動する |
| `flock` と `filelock` の相互の排他 | シェルの `flock(1)` と `lib/locks.py`（`filelock` の `fcntl.flock`）が同じファイルで互いを待つかは、実装の前に実行して確かめる |
| 利用上限の検知 | `relay.py ls` の行には利用上限で待っていることを示す欄が無い。異常系 13 は `at` が進まないことで推し量る。欄を足すかは #2346 の側の課題にする |
| GitHub API の枠 | 異常系 14 のとおり、リリース後テストで測る |
| ホットフィックス | `main` へ直接直す配布は並列の計画の範囲外。必要になれば別の課題にする |
