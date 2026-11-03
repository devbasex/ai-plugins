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
3. 返った `metrics.action` ごとに次の 1 手を打ち、`wait p1 --after <metrics.seq>` を背景で打ち直す

| `wait` が返したもの | すること |
| --- | --- |
| `notice` | 承認待ちのレーンの `metrics.attach` の 1 行を出す |
| `questions` | 届いている依頼（設計の承認・承認ゲート 2・承認ゲートの外の問い）を区分ごとにまとめて 1 回で問う |
| `release` / `confirmed` | `metrics.spawn` の 1 行（一時レーンの起動）を打つ。一時レーンが `metrics.pipeline` を流し、結果を返す |
| `finished` / `stopped` | レーンごとの結果（配布した版・止まった理由）を要約し、`[ndf-parallel] finished p1` の行を出して終える |
| 終了コード 3 | 同じ `wait` を打ち直す |

承認ゲート 2 は、提示 1 なら「承認」「差し戻す」で問い、`parallel.py confirm p1 <連番>` か
`parallel.py settle p1 <連番> --rejected` を打つ。出し直しの提示（`represent: true`）なら加わった範囲を添えて
「新しい範囲で出す」「最初の範囲だけ出す」「差し戻す」で問い、`confirm --take new` / `--take first` / `settle --rejected` を打つ。

## まとめた配布

配布の依頼が届いた時点で流れている配布が無ければ、他のレーンを待たずに直ちに配布を組む。中身は依頼の集合ではなく、
承認資料を作る時点の `origin/develop` のコミットまでの範囲で決まる。範囲に入ったレーンは、依頼していなくても結果を受ける。
承認の答えの時点で `develop` が進んでいれば、加わった範囲を添えて同じ承認ゲート 2 を 1 回だけ出し直す。
流れている配布は 1 本までで、範囲に入らない依頼は今の配布が終わった直後の次の配布になる。

## レーンの側

並列の計画の下のレーン（`NDF_RELAY_NAME` が動いている計画のレーンにある）では、スプリントの本番のステージが
配布の依頼のプラン（ステップ `parallel-request` の 1 つ）になる。依頼を送ったら承認ゲートとして終わり、レーンは答えを待たずに
次の周回へ進む。答えは `parallel.py await p1 --from <プランの状態ディレクトリ>/work/request.txt --timeout 0` で待たずに読む。自分の Pull Request が
すでに `main` にあれば、依頼を送らずに配布済みとして続きへ進む。レーンで `supervise.py queue` の `--max` を省くと、
並列の計画のそのレーンのプランの本数で流す。

## 止める

`python3 "$NDF_SCRIPTS/parallel.py" stop p1` で計画の全レーンへ停止を打ち、耐久ワークフローを終える。止まらないレーンがあれば
名前を返して 1 で終わる。1 本だけを止めるのは `relay.py stop <名前>` である。

## 数え方

オーガナイザーのセッションは `relay.py role organizer --plan p1` の印を持ち、同時に動かせる数（`NDF_RELAY_MAX_NAMED`）に数えない。
既定のままで、オーガナイザー 1 本とレーン 3 本が同時に動く。一時レーン（`relay.py spawn --role helper`）は同時に 1 本までで、
上限を 1 本だけ超えて通る。
