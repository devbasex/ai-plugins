# 並列開発のオーガナイザー（`--parallel <計画名>`）

並列の計画（[issue-plan-strategy の parallel-plan.md](../../issue-plan-strategy/references/parallel-plan.md) で作る）を、
1 行で始める。

```text
/goal /ndf:development-workflow --parallel p1 -- 終わり: [ndf-parallel] finished p1 の行を出したとき
```

このセッションの conductor が**オーガナイザー**になる。オーガナイザーは、設計の承認の答えで設計 PR をマージするほかは作業を持たない。レーンの起動・観測・リリースのまとめは
耐久ワークフロー（`parallel.py run`）が、リリースプランの実行は一時レーンが、答えの後の処理は持ち主のレーンが行う。
オーガナイザーが打つのは、問いと答え・一時レーンの起動・終わったレーンを起こす 1 行だけである。再開のコマンドも同じ 1 行である。

## 手順

1. `python3 "$NDF_SCRIPTS/parallel.py" start p1` を打つ。検査に誤りがあればレーンを 1 本も起動せずに 1 で終わる。
   ラッパーの外・tmux が無い・統括役が居ないときは 3 で終わり、`items` にレーンごとの手で起動する 1 行が並ぶ。
   その 1 行を示して終える
2. `metrics.next` の `parallel.py wait p1` を背景で打ち、完了の通知を待つ（[waiting.md](waiting.md) の形。前景の sleep で待たない）
3. 返った `metrics.action` ごとに次の 1 手を打ち、`wait p1 --after <metrics.seq>` を背景で打ち直す。どの `action` でも、
   `metrics.wake` があれば、まだ打っていない各行（リリースの答えを受けた止まっているレーンを起こす `relay.py restart <レーン>`）を打つ
   （そのレーンが動き出すまで後の知らせにも添えて届く）

| `wait` が返したもの | すること |
| --- | --- |
| `notice` | 承認待ちのレーンの `metrics.attach` の 1 行を出す。`metrics.release` があり、その `seq` の起動をまだ打っていなければ、`metrics.release.spawn` の 1 行も打つ（知らせが重なって `release` / `confirmed` を読み逃しても起動を失わない） |
| `questions` | 届いている依頼（`items`）を下の「問い方」で問い、依頼ごとに答えを送る |
| `release` / `confirmed` | `metrics.spawn` の 1 行（一時レーンの起動）を打つ。一時レーンがリリースプランを流し、結果を返す |
| `held` | `metrics.spawn` の 1 行を打つ。一時レーンが、止まったループのレーンの答えを受けた保留の周回の残りを流す（終える前の後片付け） |
| `finished` / `stopped` | レーンごとの結果（リリースした版・止まった理由）を要約し、`[ndf-parallel] finished p1` の行を出して終える |
| 終了コード 3 | 同じ `wait` を打ち直す |

### 問い方

並列の計画の下で区分つきの問いを出すのはオーガナイザーだけである。レーンと一時レーンの問いは依頼として `items` に届く。

1. 区分ごとに `AskUserQuestion` を呼ぶ（`metadata.source` は呼び出しごとに 1 つ）。順は `ndf-gate:1` → `ndf-ask:redline:*` →
   ほかの `ndf-ask:*` → `ndf-gate:2`。リリース承認ゲートを最後にするのは、答えの時点で `develop` の先頭を比べるためである
2. 依頼 1 件を問い 1 つにし、5 件以上の区分は届いた順に 4 件ずつ続けて呼ぶ。各問いにはレーン名・課題・設計 PR の URL か作業の場所を添える
3. `ndf-ask:human-action` の依頼は `attach`（`relay.py attach <レーン>`）の 1 行を添えて「済んだ」「まだ」で問う。
   `merged` があれば、同じ原因でまとめた各レーンの元の文面を並べる（答えは全レーンへ届く）
4. 耐久ワークフローが自分で立てる問い（`kind` が `stall`・`helper`・`spawn`・`observe`・`build`）は `options` で問う。選んだ答えの `commands` の 1 行は、答えを送った `answer` が `metrics.next` に返す
5. 答えを送り、下の「答えの後の整理」を通す

| 依頼の `kind` | 問いの答え | 送るコマンド |
| --- | --- | --- |
| `gate1`（設計の承認） | 承認 / 直して出し直す（指摘を書く） / やらない | `parallel.py answer p1 <id> --approved` / `--revise --note <指摘ファイル>` / `--rejected`。`reasons` にマージできなかった理由があれば添えて問う（利用者へ戻した依頼） |
| `gate2`（リリース承認ゲート） | 下の「リリース承認ゲート」 | `confirm` / `settle --rejected` |
| `ask`・`stall`・`helper`・`spawn`・`observe`・`build` | 依頼の選択肢 | `parallel.py answer p1 <id> --choice <選んだ答え>` |

`gate1` の `--approved` では、`answer` が答えを送る前に設計 PR を宛先へマージしてみる（[loop.md](loop.md) の「承認した設計のマージ」）。
結果は `metrics.merge` に載り、答えにも載ってレーンへ届く。マージできなくても答えは送られ、終了コードは 0 である。`answer` はほかに、
答えのイベントを立てるだけで、ラベル・記録・直し・マージし直し・後片付けは持ち主のレーンが周回の境で行う。持ち主のレーンが止まっていれば
`metrics.next` に `relay.py restart <レーン>` の 1 行が返るので、それを打つ（耐久ワークフローが自分で立てた問いでは、選んだ答えの
`commands` の 1 行が返る）。

### 答えの後の整理

出した `AskUserQuestion` は取り下げられず、答えを待つ間に届いた依頼は答えの後まで表に出ない。答えを受けたら、溜まった依頼を
整理してまとめて問い直す。

1. 依頼ごとに答えを送る（`parallel.py answer`）。`metrics.merge.result` が `merged`・`already_merged` 以外なら、理由（`reason`）を
   1 行で利用者へ伝える（問いにしない。次の周回の境でレーンが結果に応じた手を打つ）。`production` なら、リリース承認ゲートで人が
   マージするまで実装に選ばれないことを添える
2. 最後の `answer` の `metrics.after`（答えの後に残る依頼）から、`drop` が付いた依頼を除く。`settled` は今送った答えと同じ設計 PR の依頼、
   `resend` は同じレーンの同じ設計 PR に、より新しい先端の依頼がある古い依頼である
3. `mvv.eligible` が `true` の依頼（設計の承認で、レーンの判定が「判定できない」「判定を読めない」で、レッドラインが空の依頼）ごとに、
   `mvv-gate.py check --sprint <依頼の sprint_state> --gate design --pr <依頼の pr>` を 1 回だけ打つ。「従う」（終了コード 0）なら
   `parallel.py answer p1 <id> --approved --by mvv` を送る（判定の記録は `mvv-gate.py` が書く）。ほかの結果なら、打ち直さずに残す。
   「従わない」・レッドライン・リリース承認ゲート・区分つきの問いは自分で答えない
4. 残りがあれば、上の「問い方」の区分の順と 4 件ずつで、まとめて `AskUserQuestion` を出す。残りが無ければ
   `wait p1 --after <metrics.seq>` を背景で打ち直す

リリース承認ゲートは、提示 1 なら「承認」「差し戻す」で問い、`parallel.py confirm p1 <連番>` か
`parallel.py settle p1 <連番> --rejected` を打つ。出し直しの提示（`represent: true`）なら加わった範囲を添えて
「新しい範囲で出す」「最初の範囲だけ出す」「差し戻す」で問い、`confirm --take new` / `--take first` / `settle --rejected` を打つ。

### セッションの切り替え

カットポイントでは、問いを出していなければ引継ぎ文書 `.ndf/handoff/parallel-p1.md` の「次に実行するコマンド」に起動と同じ 1 行を書き、
`ndf-next` を出して切る。新しいセッションの `start` は動いている `run` をそのまま使い、`wait --after 0` が答えていない問いを返す。

## まとめたリリース

リリースの依頼が届いた時点で流れているリリースが無ければ、他のレーンを待たずに直ちにリリースを組む。リリースの中身は、
承認資料を作る時点の `origin/develop` のコミットまでの範囲で決まる。範囲に入ったレーンは、依頼していなくても結果を受ける。
承認の答えの時点で `develop` が進んでいれば、加わった範囲を添えて同じリリース承認ゲートを 1 回だけ出し直す。
流れているリリースは 1 本までで、範囲に入らない依頼は今のリリースが終わった直後の次のリリースになる。

依頼を受けるたびに、耐久ワークフローはリリースの組み方を `.ndf/supervise.json` の `release.form` と `.ndf/project.json` の `delivery` から読み直す。

| リポジトリの宣言 | リリースの組み方 | 依頼への答え |
| --- | --- | --- |
| `release.form` に雛形がある | 組む | リリースの結果（`released`・`rejected`・`failed`） |
| `delivery` の行がすべてマージで届くか、`delivery` が `[]` | 組まない | その場で `released`（`version` は `null`）。提示のプラン・本番のプラン・一時レーンは作らない |
| 手動の経路がある、宣言が無い・不明・読めない、雛形の無い `release.form` | 組めない | その場で `failed` と直す宣言を示す理由 |

組まないときの `released` は、オーガナイザーが組むリリースが要らないことを指す。本番系へ届いたことはリリース後テストが確かめる。

組むはずのリリースを組めないとき（出す版を読めない・連番の上限・組み立ての失敗）も同じく `failed` と理由を答える。
範囲が決まる前なら受けている依頼のすべてへ答え、提示の出し直しを組めないときは範囲の依頼だけへ答えて、残りは次のリリースへ回す。
リリースを組めないことでは計画は止まらない。止めるかは問い `kind: build`（下の「異常のとき」）への答えで決まる。

## 一時レーン（`--parallel-helper`）

持ち主のいない共有の作業は、オーガナイザーが `metrics.spawn` の 1 行で起こす一時レーン（`<計画名>-rel<連番>`、役割 `helper`）が行う。
最初の入力は次のどちらかで、`development-workflow` は判定の手順へ進まずこの節の手順で進める。

```text
/goal /ndf:development-workflow --parallel-helper p1 <連番> <提示> -- 終わり: [ndf-parallel] helper-done p1 の行を出し、python3 <relay.py> stop p1-rel<連番> で自分のラッパーを止めたとき
/goal /ndf:development-workflow --parallel-helper p1 pending <ループ名> -- 終わり: [ndf-parallel] helper-done p1 の行を出し、python3 <relay.py> stop p1-rel<連番> で自分のラッパーを止めたとき
```

**リリース（`<連番> <提示>`）:**

1. `python3 "$NDF_SCRIPTS/parallel.py" helper p1 <連番> <提示>` を打つ。`metrics.phase` が流すものを決める
2. `metrics.pipeline` を流す（待ちは背景で打ち、完了の通知を待つ）
   - `phase: present`: 提示のプラン → 本番のプランの queue と待ち。提示のプランがリリース承認ゲートで止まったら（done の提示のプランの結果が承認ゲートで止まった値）、
     `metrics.gate2` の 1 行でリリース承認ゲートの依頼を送り、手順 4 へ進む。流れ切れば手順 3 へ進む
   - `phase: confirmed`: 答えの記録 → 提示のプランを `approve` から → 本番のプランの queue と待ち。`approve` が承認ゲート（終了コード 10）で
     終われば本番のプランを流さず、問いとして送って手順 4 へ進む
3. `metrics.settle` の 1 行（`parallel.py settle p1 <連番> --done <D>`）を打つ。結果は耐久ワークフローが範囲のレーンへ返す
4. `[ndf-parallel] helper-done p1` の行を出し、最後に最初の入力の「終わり」にある `relay.py stop <自分の名前>` の 1 行を打って
   自分のラッパーを止めて終える。止めないと対話の claude が入力待ちで残り、続きの一時レーンの起動が同じ名前か上限で断られる。
   答えの後は、オーガナイザーが続きの一時レーンを起こす

CI の失敗・衝突・版数の衝突は、リリースプランの直しの手順（judge・fix）で直す。直せない失敗は問いにする（区分つきの `AskUserQuestion` を
出すと、問いの hook がオーガナイザーへ送り、拒否の理由に保留の手順が返る）。保留したら手順 4 で終える。

**保留の周回（`pending <ループ名>`）:** `python3 "$NDF_SCRIPTS/sprint-state.py" loop pending <ループ名>` を打ち、`metrics.pipeline` を流す。
`metrics.status` が `none` になるまで繰り返し、手順 4 で終える。

一時レーンは並列の計画の `lanes` に載らず、リリースの依頼を送らない。同時に 1 本までで、同時に動かせる数を 1 本だけ超えて通る。

## レーンの側

並列の計画の下のレーン（`NDF_RELAY_NAME` が動いている計画のレーンにある）では、スプリントの本番のステージが
リリースの依頼のプラン（ステップ `parallel-request` の 1 つ。フェーズ「リリースの依頼」）になる。依頼を送ったらプランは承認ゲートとして終わる。
自分の Pull Request がすでに `main` にあれば、依頼を送らずにリリース済みとして続きへ進む。レーンで `supervise.py queue` の `--max` を省くと、
並列の計画のそのレーンのプランの本数で流す。

| レーン | 依頼の後 | 答えが届いたら |
| --- | --- | --- |
| 開発ループ | `loop lap` が周回を「リリース待ち」で締め、答えを待たずに次の周回へ進む（[loop.md](loop.md)） | 周回の境の `loop select` が答えを読み、まとめを流して周回を締め直す |
| 1 スプリント | `parallel.py await p1 --from <プランの状態ディレクトリ>/work/request.txt` を背景で打って待つ | `released` ならまとめのステージを流す。`rejected` は今のリリース承認ゲートの差し戻しと同じに扱う |

設計承認ゲート・リリース承認ゲート・承認ゲートの外の問いは、どのレーンでもオーガナイザーへ送られる（[asking.md](asking.md) の「並列の計画のレーン」）。

## 外のオーガナイザーのレーン（`NDF_ORGANIZER_DIR`）

環境変数 `NDF_ORGANIZER_DIR` があるセッションは、外のオーガナイザー（サービスが実行ジョブの間で動かすまとめ役など）のレーンである。
名前は `NDF_ORGANIZER_LANE` で、依頼と答えは置き場のファイルで受け渡す（形は `contracts/forms/parallel-external.json`）。
並列の計画（`.ndf/parallel/`）は使わず、計画名の代わりに `--external` を付ける。

| 点 | 決まり |
| --- | --- |
| 本番のステージ | リリースの依頼のプラン（`parallel.py request --external`）になる。依頼を `requests/<id>.json` へ書いて 10 で終わる。自分の Pull Request がすでに `main` にあれば書かずに 0 で終わる |
| 答え | `parallel.py await --external --from <プランの状態ディレクトリ>/work/request.txt` が `answers/<id>.json` を読む。ループのレーンは周回の境で読む（上の「レーンの側」と同じ） |
| 問い | 設計の承認と承認ゲートの外の問いは依頼にしない。今どおり止まって人へ戻す |
| 1 台の中のオーガナイザー | 起動しない。`NDF_FACTORY_RUN` がある実行ジョブでは `parallel.py init`・`start`・`run` が 1 で断る |

### リリースの実行（`--parallel-helper --external`）

外のオーガナイザーがまとめたリリース 1 つのために起こすセッションは、次の 1 行で始める。`batch.json` の手（`step`）ごとに 1 手を打って終える。

```text
/goal /ndf:development-workflow --parallel-helper --external -- 終わり: 作業の報告を出したとき
```

1. `python3 "$NDF_SCRIPTS/parallel.py" compose --external` を打つ。`metrics.pipeline` が流すものを決める
2. `metrics.pipeline` を流す（待ちは背景で打ち、完了の通知を待つ）

| `step` | 流すもの | 終わり方 |
| --- | --- | --- |
| `present` / `represent` | 提示 1（`represent` は提示 2）の提示のプラン → 本番のプラン | 提示のプランがリリース承認ゲートで止まったら `metrics.record` の 1 行で提示を書き返し、「判断が要る」で終える。流れ切れば手順 3 |
| `confirm:<k>` | 答えの記録 → 提示 k のプランを `approve` から → 提示 k の本番のプラン | 手順 3。確定しない提示の本番のプランは流さない |
| `continue` | 止まった本番のプランを `--resume` で続ける | 手順 3 か、直せない失敗を問いにして止まる |

3. `metrics.settle` の 1 行（`parallel.py settle --external <連番> --done <D>`）を打つ。届いた段と結果を置き場へ書き、作業の報告で終える

CI の失敗・衝突・版数の衝突は一時レーンと同じくリリースプランの直しの手順で直す。直せない失敗は「判断が要る」で止まる。

## 止める

`python3 "$NDF_SCRIPTS/parallel.py" stop p1` で計画の全レーンへ停止を打ち、耐久ワークフローを終える。リリースの途中なら、リリースが終わる
（`settle` が届く）のを待ってから終える。`--force` を付けると待たずに終え、一時レーンも止める（本番のプランの状態ディレクトリは
残り、`supervise.py queue ... --resume` で続けられる）。止まらないレーンがあれば名前を返して 1 で終わる。1 本だけを止めるのは
`relay.py stop <名前>` である。

## 異常のとき

耐久ワークフローは次の場面で自分で問いを立て、`questions` で返す。

| 場面 | 問い |
| --- | --- |
| レーンが承認待ちでも止まってもいないのに、最後に動いた時刻から 60 分（`NDF_PARALLEL_STALL_MINUTES`）を越えた | 起こし直す / 止める / 待つ（次の区切りまで問わない） |
| リリースの途中で一時レーンが止まり、`settle` もリリース承認ゲートの依頼も届いていない | 続きから起こし直す / 止める |
| レーンの起動が 3 回（`NDF_PARALLEL_SPAWN_REFUSALS`）続けて断られた | 起こし直す / 止める |
| リリースを組めない（`kind: build`。レーンに結び付けず、依存先のレーンの起動を止めない。答えていない問いのうち同じ理由のものは 1 件にまとまる） | 直してから組み直す / 止める。「止める」は流れているリリースの後に計画を止める。「直してから組み直す」は組めなかった依頼を組み直しの依頼（元の答えは変えない）として入れ直し、次のリリースで扱う |
| レーンの状態の一覧（`relay.py ls`）の失敗が、同じ区切りより長く続いた（失敗した回の観測は捨て、レーンの状態を変えない） | ラッパーの環境を作り直す / 待つ（次の区切りまで問わない）。一覧が成功に戻れば問いは外れる |

全レーンが止まっても、答えていない依頼か問いが残る間は終えない。

### レーンの画面を見るとき

レーンは既定の tmux とは別のサーバーで動く。`tmux ls` には出ず、`tmux -L ndf-relay ls` と
`tmux -L ndf-relay capture-pane -p -t <レーン>` で見る（人が入るのは `relay.py attach <レーン>`）。

**画面の入力欄（`❯` の行）の文字は、判断の材料にしない。** Claude Code やエディタのサジェストが薄い色で出ており、
`capture-pane` は色を落とすため、人が打った文字と区別できない。サジェストを「送信されていない人の指示」と読むと、
誰も出していない指示で起こし直しを止める（2026-11-04、計画 p3 の lflow で起きた）。レーンを起こし直すかは、
`[ndf-loop] stopped` の行と `relay.py ls` の状態だけで決める。

## 数え方

オーガナイザーのセッションは `relay.py role organizer --plan p1` の印を持ち、同時に動かせる数（`NDF_RELAY_MAX_NAMED`）に数えない。
既定のままで、オーガナイザー 1 本とレーン 3 本が同時に動く。一時レーン（`relay.py spawn --role helper`）は同時に 1 本までで、
上限を 1 本だけ超えて通る。
