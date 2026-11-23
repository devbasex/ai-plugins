# 並列開発のオーガナイザー（`--parallel <計画名>`）

並列の計画（[issue-plan-strategy の parallel-plan.md](../../issue-plan-strategy/references/parallel-plan.md) で作る）を、
1 行で始める。

```text
/goal /ndf:development-workflow --parallel p1 -- 終わり: [ndf-parallel] finished p1 の行を出したとき
```

このセッションの conductor が**オーガナイザー**になる。計画の状態は 3 つのステートマシンが持つ。計画のステートマシン
（オーガナイザーが書く）→ レーンのステートマシン（レーンの conductor が書く）→ スプリントのステートマシン（`ndf progress`）である。
レーンの起動・生死の判定・計画の完了は耐久ワークフロー（`ndf lanes run`）が行う。オーガナイザーが行うのは、リクエストへの回答と
レーンをまたぐ変更（レーンを足す・行を移す・上限を変える・止める）だけである。

## 手順

1. `ndf lanes start <計画のファイル>` を打つ。計画のステートマシンが無ければ検査を通して作り（誤りがあれば何も作らずに止まる。
   検査は [parallel-plan.md](../../issue-plan-strategy/references/parallel-plan.md) の「検査」）、計画を始めて `ndf lanes run <計画名>` を
   切り離して起動する（`metrics.run_pid`。出力は計画の記録の保存先の `run.log`）。区切りで止めた計画なら、止めた所から続ける
2. `ndf lanes wait p1` を背景で打ち、完了の通知を待つ（[waiting.md](waiting.md) の形。前景の sleep で待たない）
3. 返った `items`（知らせ）ごとに下の表の手を打ち、結果の `next`（`ndf lanes wait p1 --after <番号> [--known-stalls ...]`）を背景で打ち直す

`wait` は、`--after` の番号より後の計画の事象のうち、リクエストが開いた（`request_opened`）・計画が完了した（`plan_completed`）ものを
すべて返す。閉じたリクエストは返さない。知らせが無くても、計画が終わった（完了か中止）か、まだ知らせていない止まり（`metrics.new_stalls`）が
出たら返る。上限（`contracts/limits.json` の `parallel-wait.poll`）を過ぎたら知らせの無いまま同じ番号で返るので、`next` を打ち直す。

| 知らせ（`items` の `kind`・`payload.body.kind`） | すること |
| --- | --- |
| `request_opened`・`lane_question` | レーンの質問である。下の「質問に回答する」 |
| `request_opened`・`lane-release` | レーンのリリース承認ゲートのリクエストである。下の「まとめたリリース」 |
| `request_opened`・`launch_failed` | 耐久ワークフローがレーンを起動できなかった（打ち直しの回数が尽きた・開発ループ以外の起動できない行・計画と別のリポジトリのレーン）。`detail` を読んで原因を直し、`ndf lanes request close p1 <リクエスト> --reason <理由>` で閉じる。閉じると、打ち直せば起動できる失敗のレーンを耐久ワークフローが起動し直す |
| `request_opened`・`lane_stalled` | 実行中の行のレーンが止まった（生死の判定が `stalled`・`ended`）。中身に最後に動いた時刻と起動の試みの回数が載る。`ndf lanes status p1` とレーンの画面（下の「レーンの画面を見るとき」）で確かめて利用者へ伝える。レーンが動き出せば耐久ワークフローが閉じる |
| `request_opened`・`halt_failed` | 最後の行が終わったレーンのラッパーを止められなかった。`ndf relay stop <ラッパー>` で止めてから、リクエストを閉じる。閉じるまで計画は完了しない |
| `plan_completed`、または `metrics.state` が終わり | レーンごとの結果を要約し、`[ndf-parallel] finished p1` の行を出して終える |
| 知らせが無く、`metrics.new_stalls` がある | 回答が載った後に、レーンの conductor がスプリントを続けないまま猶予を過ぎた行である。`ndf lanes status p1` の行の `awaiting_next` を読み、利用者へ伝える |

レーンの様子は `ndf lanes status p1` で読む。レーンごとに状態・`after`・行（キー・状態・入力・スプリントのステートマシンの ID）を返し、
実行中の行を持つレーンには生死の判定（`liveness`）、マシン全体の稼働中の数の上限で起動を待つレーンには `machine_wait`（理由と数）が載り、集計の `metrics.machine` にマシンの稼働中の数と上限が載る
（[parallel-checks.md](parallel-checks.md) の「マシン全体のプランの本数」）。`metrics` に開いているリクエスト・閉じたリクエストと回答・
まとめたリリースの数が、`warnings` に止まりが載る。レーンの画面を見に行く前にこれを読む。

## 質問に回答する

並列の計画の下で利用者へ問うのはオーガナイザーだけである。レーンの conductor が出した `AskUserQuestion` は、hook の guard
`ask-forward` が中継のシグナルファイルへ写し、中継のラッパーが `ndf lanes question` で計画のリクエスト（`lane_question`）にする
（[asking.md](asking.md) の「並列の計画のレーン」）。同じ質問は 1 つのリクエストにまとまる。

1. リクエストの中身（レーン・`questions`）を読み、区分ごとに `AskUserQuestion` で利用者へ問う（`metadata.source` は中身の質問の区分）。
   各問いにはレーン名・課題・設計 PR の URL か作業の場所を添える
2. 回答をレーンのスプリントへ届ける。スプリントのステートマシンの ID は `ndf lanes status p1` の行の `sprint_id` にある
   - 承認ゲートの質問なら、質問に添えられた承認ゲートの結果（質問の id `metrics.gate.id` と回答ごとのルート）から、
     ルートの `ndf answer <スプリントの ID> --gate <質問の id> --result <回答の語> --reason <理由> --by organizer --key <冪等のキー>` を打つ
   - どの質問でも、続けて `ndf lanes answered <スプリントの ID> --answer <回答>` で回答をレーンの行へ載せる。耐久ワークフローが
     そのレーンの conductor を、`ndf lanes row resumed` を打ってスプリントを続ける（開発ループの行は `ndf loop get <レーン>` を読む）入力で起動する
3. `ndf lanes request close p1 <リクエスト> --reason <理由>` でリクエストを閉じる。開いたリクエストが残る間は計画を完了しない

## まとめたリリース

計画のレーンのスプリントがリリース承認ゲートに着くと、`ndf progress` がリリースのリクエスト（`lane-release`。中身にレーン・PR・課題・
スプリントのステートマシンの ID `sprint_state`）を計画のステートマシンへ書く。同じ質問には 1 回だけ書く。

1. 届いているリリースのリクエストを 1 本のリリースにまとめ、利用者のリリース承認ゲートを通して出す（手順は `release` の Skill）
2. 結果を `ndf lanes release p1 <リクエスト>... --result released --version <バージョン> --release <リリース記録の PR> --reason <理由>` で記録する。
   承認されなければ `--result rejected`。まとめたリクエストは同じトランザクションで閉じる
3. リクエストごとに `ndf lanes answered <sprint_state> --answer <released か rejected>` を打つ。起きたレーンの conductor がスプリントを続けると、`ndf progress` が
   記録した回答を承認ゲートの回答（`by: organizer`）として書き、出したバージョンとリリース記録の PR をスプリントの文脈へ写す

リクエストを出した後に PR の head が変わっていれば、回答は承認ゲートに断られ、待ちへ戻る。

## セッションの切り替え

**オーガナイザーの会話も、文脈の量の 2 つの値で切る**（[context-window.md](context-window.md) の「運用上限で hook が止める」）。

| 文脈の量 | 切る時点 |
| --- | --- |
| 判断開始の値（既定 250,000）以上 | hook の知らせを受けた後の、知らせを処理し終えて次の `wait` を張る前か、利用者への報告を終えた時点 |
| 運用上限（既定 500,000）以上 | hook に止められた時点。止められた後も下の 1〜3 に要る呼び出し（TaskStop・引継ぎ文書の Read・Write・Edit・`ndf records handoff check`）は通る |

切るときは、質問を出していなければ次の順で切る。引継ぎ文書は `.ndf/handoff/parallel-p1.md` で、「現在地」には新しい会話が
最初に打つ `wait` の 1 行（`--after <番号>` と `--known-stalls`。どの行かは下の 1 で決まる）を、「次に実行するコマンド」には
計画名と文書のパスの両方を含む 1 行を書く。

```text
/goal /ndf:development-workflow --parallel p1 .ndf/handoff/parallel-p1.md の続きから -- 終わり: [ndf-parallel] finished p1 の行を出したとき
```

起動と同じ 1 行（パスが無い）では、新しい会話が文書を名指しできず、保存した `--after` を戻せない。
`--parallel p1` を落とすと、新しい会話はオーガナイザーの手順に入らない。

1. 「現在地」に書く `wait` の行を決める。保存する行は、まだ処理していない知らせを次に返す行でなければならない。
   処理し終えた範囲の行を保存すると、閉じていないリクエスト（`lane_stalled` など）が再び返り、利用者への報告が重なる。
   - **背景の `wait` が無いとき**（判断開始の値で、通知を処理し終えて次の `wait` を張る前に切る通常の経路）: 止めるものは無い。
     直前に処理した結果の `next` を打たずに、その行をそのまま保存する。直前に打った `wait` の引数は保存しない
   - **背景の `wait` が無く、`wait` の結果を処理している途中で運用上限に止められたとき**: 結果の `next` は保存しない（同じ結果に
     含まれる未処理の質問やリリースのリクエストが再開後に返らなくなる）。その結果を返した `wait` を打った元の行を保存し、
     「現在地」に項目ごとの進み具合を書く
     - **処理済み**: 最後の操作まで済んだ項目だけを列挙する。質問は `ndf lanes request close` でリクエストを閉じた時点、
       リリースのリクエストはまとめた全リクエストに `ndf lanes answered` を打ち終えた時点で処理済みとする
     - **途中の項目**: 済んでいない項目は、確定した回答と済んだ操作・残りの操作を書く。質問なら利用者の回答・承認ゲートの結果と、
       打ち終えた `ndf answer`・`ndf lanes answered` のどれか。リリースなら `ndf lanes release` で記録した結果（`--result`・
       バージョン・リリース記録の PR）と、まだ `ndf lanes answered` を打っていない `sprint_state`。`ndf lanes release` が閉じた
       リクエストは元の `wait` を打ち直しても返らないので、ここに書かないと回答がレーンへ届かない

     再開後は `wait` より先に途中の項目の残りの操作を打つ（利用者へ問い直さず、承認ゲートと済んだ操作を打ち直さない）。
     その後で `wait` を打ち、返る項目のうち処理済みと途中の項目に挙げたものは飛ばして、残りだけを処理する
   - **背景の `wait` があるとき**: `TaskStop <task id>` で止め、完了通知で確かめる。`killed` で届けば、その `wait` を打った元の行を
     保存する（同じ `--after` で打ち直すと、まだ処理していない同じ知らせが返る）。`killed` 以外で届いたら止める前に `wait` が
     戻っていた。判断開始の値で切るときは、その結果を上の表のとおりに扱ってから 1 からやり直す（上の「背景の `wait` が無いとき」に
     当たり、結果の `next` を保存する）。運用上限で止められたときは結果を処理せず、その `wait` を打った元の行を保存する
2. 引継ぎ文書を書く。**文書が無ければ（その計画で最初の切り替え）、[handoff.md](handoff.md) の「セクションの形」の必須のセクションを
   すべて備えた本体を作る。** 「進め方（利用者の指示）」に起動のときの利用者の指示（並列の上限など）、「課題の順」に計画のレーンと
   行の状態、「現在地」に 1 で決めた `wait` の行と開いているリクエスト、「次にやること」の 1 つ目に「現在地」の `wait` を背景で
   打つこと（途中の項目があれば、その前に残りの操作を打つこと）、「次に実行するコマンド」に上の 1 行の `ndf-next` のブロックを書く。「課題の順」は、判断開始の値で切るときは
   `ndf lanes status p1` から書く。**運用上限で止められた後は `ndf lanes status p1` も hook に断られる**ので、会話の中にある
   計画のファイルの中身と最後に読んだ `ndf lanes status p1` の結果から書き、どの時点の状態かを添える。会話の中で
   `ndf lanes status p1` を一度も読んでいない（`start` から `wait` へ進んだ）ときは、計画のファイルからレーンと行を書き、
   状態は「未確認」と記す。このときは
   「次にやること」の 2 つ目に「再開したら `ndf lanes status p1` を読み、「課題の順」を最新の状態に直す」を書く。
   **文書があれば**「現在地」「次にやること」「次に実行するコマンド」を書き直し、「課題の順」は変わった行だけを直す
   （運用上限の後で最新の状態を読めないときは、同じく再開後に直すことを「次にやること」へ書く）。書いたら
   `ndf records handoff check` で確かめる
3. 確かめが通れば `ndf-next` を出して切る。通らなければブロックを出さずに理由を伝える

**1 を飛ばすと切り替わらない。** `wait` のシェルが残るとラッパーはシグナルファイルを書かず、claude の終了を止める
（[waiting.md](waiting.md) の「バックグラウンドの作業を止める」）。`wait` を止めても耐久ワークフローとレーンは止まらない。

**新しいセッションは、名指しされた文書を読んでから「手順」を通す。** 1 の `start` は動いている計画をそのまま読む。
2 の最初の `wait` は `ndf lanes wait p1` でなく、文書の「現在地」の `wait` の行（保存した `--after` と `--known-stalls`）を
そのまま背景で打つ。その間に開いたリクエストはこの `wait` が返す。「現在地」に途中の項目があれば、`wait` より先に
その残りの操作を打つ（上の 1 の「途中の項目」）。処理済みと途中の項目に挙げたものは、返った項目の中でも処理せずに飛ばす。文書が無い・「現在地」に `wait` の行が無いときだけ
`ndf lanes wait p1` から始める（開いているリクエストがすべて返る）。

## 耐久ワークフローがすること

`ndf lanes run` は計画が終わるまで、1 周ごと（`parallel-run.poll`。`NDF_PARALLEL_POLL` で秒を変えられる）に計画とレーンの
ステートマシンを読み、次のどれかを 1 つ打つ。同じ計画の `run` は 1 本に限り、落ちた後の回復で済んだ起動を打ち直さない。

| 場面 | 打つもの |
| --- | --- |
| 次の行を始められるレーンがある（`after` を満たし、マシン全体のプランの本数に収まる） | 同じ名前の元のラッパーを止めてから、`ndf relay spawn` でレーンの conductor を起動する。最初の入力は `ndf progress new <スプリント名> --mode <モード> --issues <課題>... --parent <レーンのステートマシン>` → `ndf lanes row start`（開発ループの行は `ndf progress new --loop <レーン>` → `ndf lanes row start` → `ndf loop start <レーン> --row <ID>`） |
| 回答が載った実行中の行（`ndf lanes answered`） | そのレーンの conductor を、回答の後にスプリントを続ける入力で起動する。同じ回答では 1 回だけ |
| 認証の失敗で `/goal` が外れたレーン | 子を起動するアカウントが使えれば、`/goal` を入れ直して続ける入力で再起動する（上限の回数まで） |
| 起動が打ち直しても通らない・起動の後に行が実行中にならない | `launch_failed` のリクエストを書く |
| 実行中の行のレーンが止まった | `lane_stalled` のリクエストを書く。承認ゲートや質問の回答を待つ行は正常な待ちで知らせない |
| 最後の行が終わったレーンのラッパーが動いている | 止める。止められなければ `halt_failed` のリクエストを書く |
| 全レーンの行が終わり、開いたリクエストが無い | 計画を完了にする |

レーンは計画のリポジトリ（`start` を打った場所）で起動する。計画の設定のレーンに計画と違う `repo` を書いたレーンは、
保存先を解決できないため起動できない失敗（`launch_failed`）になる。

## レーンをまたぐ変更

上限を変えるのは人の指示を受けたときだけである。オーガナイザーは値を人へ問うてから打つ。

| すること | 打つもの |
| --- | --- |
| レーンを足す | 足すレーン 1 本の設定を JSON のファイルに書き（形は [parallel-plan.md](../../issue-plan-strategy/references/parallel-plan.md) の「レーンごとに決めること」）、`ndf lanes lane add p1 <JSON のファイル>` |
| レーンの `touches`・`after` を変える | `ndf lanes set p1 <レーン> [--touches <場所>...] [--after <先>...] --reason <理由>`。`name` と `repo` は変えられない |
| マシン全体の稼働中の数の上限を変える | `ndf relay cap --named <数>`（消すのは `--clear`）。計画ごとの上限は無い（[parallel-checks.md](parallel-checks.md) の「マシン全体の稼働中の数」） |
| 課題の行を別のレーンへ移す | `ndf lanes move p1 <元のレーン> <行> <先のレーン> --reason <理由>` |

どの変更も、計画の決まり（触る場所の重なり `I13`・課題の重なり `I19`・`after` の輪 `I21`。[parallel-checks.md](parallel-checks.md)）に
新しく当たれば何も書かずに断る。リクエストに応えた変更なら `--request <リクエスト>` を付けると同じトランザクションで閉じる（`lane add`・`set`）。
レーンの中の行（足す・並べ替える・中止にする・外した課題を次の行へ入れる）は、そのレーンの conductor が `ndf lanes row` で書く。

## 外のオーガナイザーのレーン（`NDF_ORGANIZER_DIR`）

環境変数 `NDF_ORGANIZER_DIR` があるセッションは、外のオーガナイザー（サービスのランナーなど）の下のレーンである。
名前は `NDF_ORGANIZER_LANE` で、計画のステートマシンの代わりに保存先のファイルでやり取りする（パスと項目は
`contracts/domains/ndf-files.json` の `organizer-request`・`organizer-answer`）。

| 点 | 決まり |
| --- | --- |
| リリース | リリース承認ゲートに着くと、`ndf progress` が `requests/<id>.json` へリクエストを書く（同じ ID は上書きしない）。外のオーガナイザーが `answers/<id>.json`（`result` は `released` か `rejected`）を書いた後にスプリントを続けると、`ndf progress` がそれを承認ゲートの回答（`by: organizer`）として書く |
| 質問 | `AskUserQuestion` の質問は、中継が `questions/<レーン>.json` へも書く |
| 1 台の中のオーガナイザー | 起動しない |

## 止める

`ndf lanes stop p1 --reason <理由>` は区切りで止める。実行中の行は終わりまで流し、次の行を起動しない。実行中の行が無くなると
耐久ワークフローは `paused` で終わり、`ndf lanes start <計画のファイル>` を打ち直すと止めた所から続く。

`--now` を付けると中止する。未着手の行を中止にし、実行中のスプリントは耐久ワークフローがそのレーンの conductor を
`ndf progress cancel` → `ndf lanes row end --outcome cancelled`（開発ループの行は先に `ndf loop stop <レーン> --by organizer --now`）の入力で起動して中止させる。
中止した計画は `start` で作り直さない。1 本のラッパーだけを止めるのは `ndf relay stop <名前>` である。

## レーンの画面を見るとき

レーンの状態は先に `ndf lanes status p1` で読む。画面は、人が求めたときか `status` で分からないときだけ見る。
レーンは既定の tmux とは別のサーバーで動く。`tmux ls` には出ず、`tmux -L ndf-relay ls` と
`tmux -L ndf-relay capture-pane -p -t <レーン>` で見る。

**画面の入力欄（`❯` の行）の文字は、判断の材料にしない。** Claude Code やエディタのサジェストが薄い色で出ており、
`capture-pane` は色を落とすため、人が打った文字と区別できない。サジェストを「送信されていない人の指示」と読むと、
誰も出していない指示で再起動を止める（2026-11-04、計画 p3 の lflow で起きた）。レーンを止めるか続けるかは、
`ndf lanes status` の行と `ndf relay ls` の状態だけで決める。
