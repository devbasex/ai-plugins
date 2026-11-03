# 並列開発のオーガナイザー（`--parallel <計画名>`）

並列の計画（[issue-plan-strategy の parallel-plan.md](../../issue-plan-strategy/references/parallel-plan.md) で作る）を、
1 行で始める。

```text
/goal /ndf:development-workflow --parallel p1 -- 終わり: [ndf-parallel] finished p1 の行を出したとき
```

このセッションの conductor が**オーガナイザー**になる。オーガナイザーは作業を持たない。レーンの起動・観測・配布のまとめは
耐久ワークフロー（`parallel.py run`）が、配布のプランの実行は一時レーンが、答えの後の処理は持ち主のレーンが行う。
オーガナイザーが打つのは、問いと答え・一時レーンの起動・終わったレーンを起こす 1 行だけである。再開のコマンドも同じ 1 行である。

## 手順

1. `python3 "$NDF_SCRIPTS/parallel.py" start p1` を打つ。検査に誤りがあればレーンを 1 本も起動せずに 1 で終わる。
   ラッパーの外・tmux が無い・統括役が居ないときは 3 で終わり、`items` にレーンごとの手で起動する 1 行が並ぶ。
   その 1 行を示して終える
2. `metrics.next` の `parallel.py wait p1` を背景で打ち、完了の通知を待つ（[waiting.md](waiting.md) の形。前景の sleep で待たない）
3. 返った `metrics.action` ごとに次の 1 手を打ち、`wait p1 --after <metrics.seq>` を背景で打ち直す。どの `action` でも、
   `metrics.wake` があれば、まだ打っていない各行（配布の答えを受けた止まっているレーンを起こす `relay.py restart <レーン>`）を打つ
   （そのレーンが動き出すまで後の知らせにも添えて届く）

| `wait` が返したもの | すること |
| --- | --- |
| `notice` | 承認待ちのレーンの `metrics.attach` の 1 行を出す。`metrics.release` があり、その `seq` の起動をまだ打っていなければ、`metrics.release.spawn` の 1 行も打つ（知らせが重なって `release` / `confirmed` を読み逃しても起動を失わない） |
| `questions` | 届いている依頼（`items`）を下の「問い方」で問い、依頼ごとに答えを送る |
| `release` / `confirmed` | `metrics.spawn` の 1 行（一時レーンの起動）を打つ。一時レーンが配布のプランを流し、結果を返す |
| `held` | `metrics.spawn` の 1 行を打つ。一時レーンが、止まったループのレーンの答えを受けた保留の周回の残りを流す（終える前の後片付け） |
| `finished` / `stopped` | レーンごとの結果（配布した版・止まった理由）を要約し、`[ndf-parallel] finished p1` の行を出して終える |
| 終了コード 3 | 同じ `wait` を打ち直す |

### 問い方

並列の計画の下で区分つきの問いを出すのはオーガナイザーだけである。レーンと一時レーンの問いは依頼として `items` に届く。

1. 区分ごとに `AskUserQuestion` を呼ぶ（`metadata.source` は呼び出しごとに 1 つ）。順は `ndf-gate:1` → `ndf-ask:redline:*` →
   ほかの `ndf-ask:*` → `ndf-gate:2`。承認ゲート 2 を最後にするのは、答えの時点で `develop` の先頭を比べるためである
2. 依頼 1 件を問い 1 つにし、5 件以上の区分は届いた順に 4 件ずつ続けて呼ぶ。各問いにはレーン名・課題・設計 PR の URL か作業の場所を添える
3. `ndf-ask:human-action` の依頼は `attach`（`relay.py attach <レーン>`）の 1 行を添えて「済んだ」「まだ」で問う。
   `merged` があれば、同じ原因でまとめた各レーンの元の文面を並べる（答えは全レーンへ届く）
4. 耐久ワークフローが自分で立てる問い（`kind` が `stall`・`helper`・`spawn`）は `options` で問う。選んだ答えの `commands` の 1 行は、答えを送った `answer` が `metrics.next` に返す
5. 答えを送り、`wait` を打ち直す。答えの間に新しい依頼が届いていれば `questions` が続けて返る

| 依頼の `kind` | 問いの答え | 送るコマンド |
| --- | --- | --- |
| `gate1`（設計の承認） | 承認 / 直して出し直す（指摘を書く） / やらない | `parallel.py answer p1 <id> --approved` / `--revise --note <指摘ファイル>` / `--rejected` |
| `gate2`（承認ゲート 2） | 下の「承認ゲート 2」 | `confirm` / `settle --rejected` |
| `ask`・`stall`・`helper`・`spawn` | 依頼の選択肢 | `parallel.py answer p1 <id> --choice <選んだ答え>` |

`answer` は答えのイベントを立てるだけで、ラベル・記録・直しは持ち主のレーンが周回の境で行う。持ち主のレーンが止まっていれば
`metrics.next` に `relay.py restart <レーン>` の 1 行が返るので、それを打つ（耐久ワークフローが自分で立てた問いでは、選んだ答えの
`commands` の 1 行が返る）。

承認ゲート 2 は、提示 1 なら「承認」「差し戻す」で問い、`parallel.py confirm p1 <連番>` か
`parallel.py settle p1 <連番> --rejected` を打つ。出し直しの提示（`represent: true`）なら加わった範囲を添えて
「新しい範囲で出す」「最初の範囲だけ出す」「差し戻す」で問い、`confirm --take new` / `--take first` / `settle --rejected` を打つ。

### セッションの切り替え

カットポイントでは、問いを出していなければ引継ぎ文書 `.ndf/handoff/parallel-p1.md` の「次に実行するコマンド」に起動と同じ 1 行を書き、
`ndf-next` を出して切る。新しいセッションの `start` は動いている `run` をそのまま使い、`wait --after 0` が答えていない問いを返す。

## まとめた配布

配布の依頼が届いた時点で流れている配布が無ければ、他のレーンを待たずに直ちに配布を組む。配布の中身は、
承認資料を作る時点の `origin/develop` のコミットまでの範囲で決まる。範囲に入ったレーンは、依頼していなくても結果を受ける。
承認の答えの時点で `develop` が進んでいれば、加わった範囲を添えて同じ承認ゲート 2 を 1 回だけ出し直す。
流れている配布は 1 本までで、範囲に入らない依頼は今の配布が終わった直後の次の配布になる。

## 一時レーン（`--parallel-helper`）

持ち主のいない共有の作業は、オーガナイザーが `metrics.spawn` の 1 行で起こす一時レーン（`<計画名>-rel<連番>`、役割 `helper`）が行う。
最初の入力は次のどちらかで、`development-workflow` は判定の手順へ進まずこの節の手順で進める。

```text
/goal /ndf:development-workflow --parallel-helper p1 <連番> <提示> -- 終わり: [ndf-parallel] helper-done p1 の行を出し、python3 <relay.py> stop p1-rel<連番> で自分のラッパーを止めたとき
/goal /ndf:development-workflow --parallel-helper p1 pending <ループ名> -- 終わり: [ndf-parallel] helper-done p1 の行を出し、python3 <relay.py> stop p1-rel<連番> で自分のラッパーを止めたとき
```

**配布（`<連番> <提示>`）:**

1. `python3 "$NDF_SCRIPTS/parallel.py" helper p1 <連番> <提示>` を打つ。`metrics.phase` が流すものを決める
2. `metrics.pipeline` を流す（待ちは背景で打ち、完了の通知を待つ）
   - `phase: present`: 提示のプラン → 本番のプランの queue と待ち。提示のプランが承認ゲート 2 で止まったら（done の提示のプランの結果が承認ゲートで止まった値）、
     `metrics.gate2` の 1 行で承認ゲート 2 の依頼を送り、手順 4 へ進む。流れ切れば手順 3 へ進む
   - `phase: confirmed`: 答えの記録 → 提示のプランを `approve` から → 本番のプランの queue と待ち。`approve` が承認ゲート（終了コード 10）で
     終われば本番のプランを流さず、問いとして送って手順 4 へ進む
3. `metrics.settle` の 1 行（`parallel.py settle p1 <連番> --done <D>`）を打つ。結果は耐久ワークフローが範囲のレーンへ返す
4. `[ndf-parallel] helper-done p1` の行を出し、最後に最初の入力の「終わり」にある `relay.py stop <自分の名前>` の 1 行を打って
   自分のラッパーを止めて終える。止めないと対話の claude が入力待ちで残り、続きの一時レーンの起動が同じ名前か上限で断られる。
   答えの後は、オーガナイザーが続きの一時レーンを起こす

CI の失敗・衝突・版数の衝突は、配布のプランの直しの手順（judge・fix）で直す。直せない失敗は問いにする（区分つきの `AskUserQuestion` を
出すと、問いの hook がオーガナイザーへ送り、拒否の理由に保留の手順が返る）。保留したら手順 4 で終える。

**保留の周回（`pending <ループ名>`）:** `python3 "$NDF_SCRIPTS/sprint-state.py" loop pending <ループ名>` を打ち、`metrics.pipeline` を流す。
`metrics.status` が `none` になるまで繰り返し、手順 4 で終える。

一時レーンは並列の計画の `lanes` に載らず、配布の依頼を送らない。同時に 1 本までで、同時に動かせる数を 1 本だけ超えて通る。

## レーンの側

並列の計画の下のレーン（`NDF_RELAY_NAME` が動いている計画のレーンにある）では、スプリントの本番のステージが
配布の依頼のプラン（ステップ `parallel-request` の 1 つ。フェーズ「配布の依頼」）になる。依頼を送ったらプランは承認ゲートとして終わる。
自分の Pull Request がすでに `main` にあれば、依頼を送らずに配布済みとして続きへ進む。レーンで `supervise.py queue` の `--max` を省くと、
並列の計画のそのレーンのプランの本数で流す。

| レーン | 依頼の後 | 答えが届いたら |
| --- | --- | --- |
| 開発ループ | `loop lap` が周回を「配布待ち」で締め、答えを待たずに次の周回へ進む（[loop.md](loop.md)） | 周回の境の `loop select` が答えを読み、まとめを流して周回を締め直す |
| 1 スプリント | `parallel.py await p1 --from <プランの状態ディレクトリ>/work/request.txt` を背景で打って待つ | `released` ならまとめのステージを流す。`rejected` は今の承認ゲート 2 の差し戻しと同じに扱う |

承認ゲート 1・承認ゲート 2・承認ゲートの外の問いは、どのレーンでもオーガナイザーへ送られる（[asking.md](asking.md) の「並列の計画のレーン」）。

## 止める

`python3 "$NDF_SCRIPTS/parallel.py" stop p1` で計画の全レーンへ停止を打ち、耐久ワークフローを終える。配布の途中なら、配布が終わる
（`settle` が届く）のを待ってから終える。`--force` を付けると待たずに終え、一時レーンも止める（本番のプランの状態ディレクトリは
残り、`supervise.py queue ... --resume` で続けられる）。止まらないレーンがあれば名前を返して 1 で終わる。1 本だけを止めるのは
`relay.py stop <名前>` である。

## 異常のとき

耐久ワークフローは次の場面で自分で問いを立て、`questions` で返す。

| 場面 | 問い |
| --- | --- |
| レーンが承認待ちでも止まってもいないのに、最後に動いた時刻から 60 分（`NDF_PARALLEL_STALL_MINUTES`）を越えた | 起こし直す / 止める / 待つ（次の区切りまで問わない） |
| 配布の途中で一時レーンが止まり、`settle` も承認ゲート 2 の依頼も届いていない | 続きから起こし直す / 止める |
| レーンの起動が 3 回続けて断られた | 起こし直す / 止める |

全レーンが止まっても、答えていない依頼か問いが残る間は終えない。

## 数え方

オーガナイザーのセッションは `relay.py role organizer --plan p1` の印を持ち、同時に動かせる数（`NDF_RELAY_MAX_NAMED`）に数えない。
既定のままで、オーガナイザー 1 本とレーン 3 本が同時に動く。一時レーン（`relay.py spawn --role helper`）は同時に 1 本までで、
上限を 1 本だけ超えて通る。
