MVV 判定で承認ゲートを省いた回答（回答した者 `service:mvv`）を、読む（消費する）時点で、判定した PR の番号・宛先・宛先の先端（判定が規則を読んだベースのコミット）・head と照合し、どれかが違えば人の承認待ちへ戻す。

## 何が起きたか

今の `fast`・`auto` では、MVV 判定が承認ゲートを省けると出すと、承認ゲートのステップが `service:mvv` の承認を書き、後の駆動がそれを読んで進む。省いた回答が判定の対象と結び付けるのは head だけである（`progress/model.py` の `mvv_answer` の `seen`）。判定の後に、同じ head のまま PR の宛先を変える・宛先のブランチへ MVV の承認の取り消しやレッドラインを足す変更が入る・並行する駆動が照合していない回答を読む、のどれでも、古い規則で出した省略がそのまま通る。

#3959 の設計 PR https://github.com/devbasex/ai-plugins/pull/4093 の cross-review の 4〜6 ラウンドで、この照合の仕組みへの指摘が毎ラウンド続いた（r4-2 → r5-2・r5-3 → r6-1〜r6-6）。直すたびに `ndf progress` の入口の `recheck`・`_consume` の消費の排他・`ndf answer head` の `approved_head`・`show`/`wait` の取得経路へ広がった。この隙は今の `fast`・`auto` にもともとあるもので、#3959 の目的（`normal` でも判定する・レビューの保留・`cite`・`--machine`）とは別の仕組みなので、2026-11-22 に conductor が #3959 から分けると決めた。#3959 の照合は今のまま head だけである。

## 種類

不具合（省いた回答は判定した対象のためのもので、別の対象への承認に使われてはならない。この約束は MVV の「必ず人の承認が要る操作」と Value 1「任せても人の手に残る」が持ち、今の照合がそれを守り切れていない）

## 今の姿

承認ゲートを MVV 判定で省く仕組みは、進め方 `fast`・`auto` で、承認ゲートの直前の判定のステップ（`ndf records mvv gate`）が `mvv_gate.skip: true` を出したときに、承認ゲートのステップが人へ問わずに `service:mvv` の承認を書くものである（`development-workflow` の `references/pace.md`）。省いた回答が判定の対象と結び付けるのは head だけで、ほかの対象は見ない。

| 何を見るか | 今の扱い | 置き場 |
| --- | --- | --- |
| 省いた回答が持つ対象 | `seen` に head だけ（PR の番号・宛先・判定が規則を読んだベースのコミットを持たない） | `progress/model.py` の `mvv_answer` |
| 回答を読む前の照合 | `ndf progress` の入口で、回答が見た head と今の PR の head を比べる。駆動の排他（`store.run_lock`）を取る前に打つ | `progress/gate/answering.py` の `recheck`・`gate/entry.py` の `_progress` |
| 回答の消費 | 排他の中で、読む回答をそのまま消費する（照合し直さない） | `progress/run.py` の `_consume` |
| マージの側が読む承認済みの head | 区分の最後の承認の回答の head。消費されたかを見ない | `progress/gate/records.py` の `approved_head` |

期待する結果の根拠は、MVV（`.ndf/mvv.md`、版 11）の「必ず人の承認が要る操作」の前文（承認は設計の承認か本番への配布の承認の場で取る）と Value 1（承認をどこまで AI に任せるかは利用者が決められ、任せても「必ず人の承認が要る操作」は人の手に残る）である。省いた回答は、判定が読んだ規則（ベースの MVV とレッドライン）と判定した差分に対する承認で、別の宛先・別のベース・別の PR への承認ではない。

## 事象

コードの読みで確かめた（実行での再現は、実装の PR のテストが行う）。`main` のコミット `3d5279f9f` で:

- `mvv_answer` が書く `seen` は `{"head": <判定の head>, "material_sha256": null}` で、PR の番号・宛先・ベースのコミットを持たない
- `recheck` は `Target.differs` で head と承認資料の sha256 だけを比べる。宛先とベースの先端を読まない
- `recheck` は `run.advance` が排他を取る前に打たれ、`_consume` は排他の中で照合し直さずに読む
- `approved_head` は区分の最後の承認の回答の head を返し、その回答がステップの `finished` で消費されたかを見ない

## 期待する結果

省いた回答は、消費する直前に、判定したときと同じ PR の番号・宛先・宛先の先端・head であると確かめられたときだけ読まれ、1 つでも違うか確かめられなければ人の承認待ちへ戻る。マージの側は、消費された省いた回答の head だけを承認済みとして読む。

## 再現手順

1. 進め方 `fast` のスプリントで、設計承認ゲートの判定（`design-mvv`）が `skip: true` を出し、承認ゲートのステップが `service:mvv` の承認を書く
2. 次の `ndf progress next` の前に、設計 PR の宛先を同じ head のまま別のブランチへ変える（または宛先のブランチへ `.ndf/mvv.json` の承認を取り消すコミットを入れる）
3. `ndf progress next` を打つと、`recheck` は head だけを比べて違いを見つけず、承認ゲートは `service:mvv` の承認で通る

## 環境

NDF 11.0.15（`main` のコミット `3d5279f9f`）。Claude Code・Codex のどちらの駆動でも同じ（`ndf progress` の振る舞い）。

## なぜ困るか

判定が読んでいない規則（取り消された MVV・新しいレッドライン）や判定していない宛先に対して、人の承認が省かれたまま工程が進む。利用者が「任せた範囲」は判定した対象だけで、それを越えた省略は、人の手に残るはずの承認を AI が代わりに通したことになる。#4105 がリリース承認ゲートの省略を有効にすると、同じ隙が本番への配布の承認にも開く。

## 目的

省いた回答が、判定したときと同じ PR・宛先・宛先の先端・head に対してだけ読まれる。

## 対象範囲

含む:

- 判定の出力（`mvv_gate`）と省いた回答に、判定した PR の番号・宛先・ベースのコミットを持たせること
- 省いた回答を消費する直前の照合（PR の番号・宛先・head・宛先の先端）と、照合が通らないときに人の承認待ちへ戻すこと
- マージの側（`ndf answer head`）が、消費されていない省いた回答の head を返さないこと
- 判定の対象の欄を欠く判定・省いた回答を、省かずに人へ問うこと
- 設計承認ゲートと、#4105 が省けるようにした後のリリース承認ゲートの両方（照合は区分によらず同じ）

含まない:

- 旧版の NDF が書いた状態から再開するための移行の手順・互換の読み方（#3959 の決定 3。欄を欠く状態は、受け入れ条件 5 のとおり人へ問う側へ倒れるだけで、そのための専用の処理は持たない）
- リリース承認ゲートで保留を読む PR の範囲と、その印 `scope`（#4105）
- 即時修正のマージの前の判定（#4104）
- 人の回答（`ndf answer`）の照合の仕方（今どおり head と承認資料の sha256。文書だけの差で出し直さない #3020 の経路も今どおり）
- 判定そのものの材料と手順（#3959・#4076・#4090・#4099）
- PR を読めないときに待ちの表示（`present`）が質問を出せない今の振る舞い（どの承認ゲートでも同じで、この照合が生んだものでない）

## 前提

- 前提 1: #3959 の実装（判定がベースの MVV から事実を作る・`Snapshot.commit`・`MVV_SKIP_GATES`）と #4105 の実装（`mvv_answer_void`・`scope`）が main へ入った後に実装する。#4105 より先に入るときは、#4105 が作る予定の `mvv_answer_void` をこの Issue の実装が作り、#4105 が印の検査を足す
- 前提 2: 判定が規則を読んだベースのコミットは、判定が `git fetch` で読んだ宛先の先端（`Snapshot.commit`）である。消費の時点の先端も同じリモートから `git ls-remote` で読む（GitHub の PR の `base.sha` は宛先の更新に追いつく時点が決まっていない）

## 受け入れ条件

- [ ] 1. 設計承認ゲートの `service:mvv` の承認を書いた後に、同じ head のまま PR の宛先を変えると、回答を読む前に人の承認待ちへ戻る
- [ ] 2. 判定の後に宛先のブランチが進む（MVV の承認の取り消し・レッドラインの追加を含む）と、省いた回答を消費する直前の照合が人の承認待ちへ戻す
- ~~[ ] 3. 入口で照合していない省いた回答（並行する駆動が書いたもの）を、駆動が消費しない~~ → 次の 3 へ言い換えた（2026-11-22、設計者。照合を入口でなく消費の直前に置くため、「入口で照合していない」が無くなる。設計の決定 1）
- [ ] 3. 駆動の排他の中で消費の直前に照合を通していない省いた回答を、どの駆動（`next`・`work`・入口を通らない駆動）も消費しない。並行する駆動が書いた省いた回答も、消費する駆動が自分の排他の中で照合する
- [ ] 4. マージの側（`ndf answer head`）が、消費を通っていない省いた回答を承認済みの head として返さない
- ~~[ ] 5. 旧版が書いた未消費の `service:mvv` の回答と、判定の対象の欄を持たない旧版の判定から再開しても、省かずに人へ問う~~ → 次の 5 へ言い換えた（2026-11-22、設計者。旧版の状態からの再開は扱わない（#3959 の決定 3・起動指示）ので、旧版を名指さず、欄の欠けで決める形にした）
- [ ] 5. 判定の対象の欄（PR の番号・宛先・ベースのコミット・head）を 1 つでも欠く判定では承認ゲートを省かず、欄を 1 つでも欠く省いた回答は読まずに人へ問う
- [ ] 6. 照合を通った省いた回答は今どおり 1 回の消費で承認ゲートを通し、人の回答の照合（head・承認資料の sha256・文書だけの差の経路）は変わらない

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる（`互換なし`）。`context.schema.json` の `$defs.mvv_gate` に任意のキー `pr`・`base_ref`・`base_sha` を足す（`service:mvv` の回答は判定の出力の写しを payload に持つので、同じ 3 つを持つ）。欄の無い判定と回答は読まない（後方互換を持たない。MVV Value 4） |
| 重さ | `fast`・`auto` の利用者で、判定の後に宛先が変わる・宛先が進む・並行して駆動するときに、人の承認が省かれる。回避は進め方 `normal` |
| データ | ステートマシンの事象の表のスキーマは変えない（payload のキーが増える）。移行は無い |
| 既存の振る舞い | 省いた回答の消費の前に、REST の PR の読み取り 1 回と `git ls-remote` 1 回が増える（省いた回答を消費する駆動だけ）。宛先が判定の後に進むと、省けた承認ゲートが人の承認待ちになる |

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 判定のステップが PR・宛先・ベースのコミット・head を読んで `skip: true` を出した | 承認ゲートの直前のステップ | PR を読めない・材料が欠ける → `skip` 偽（#3959） | — |
| E2 | 承認ゲートのステップが `service:mvv` の承認を書いた | E1 の出力が欄をそろえている | 欄が欠ける → 書かずに人へ問う（受け入れ条件 5） | E1 の後 |
| E3 | 駆動が排他を取り、消費の直前に PR と宛先の先端を照合した | `next`・`work` が E2 の回答を読む | 違う・読めない → 人の承認待ちへ戻した（受け入れ条件 1・2・3） | E2 の後。E2 と E3 の間に宛先の変更・別の駆動が入りうる |
| E4 | 照合を通った省いた回答を消費し、ステップを終えた | E3 が通った | — | E3 と同じ排他の中で続けて |
| E5 | マージの側が承認済みの head を読んだ | `ndf answer head` | E4 が無い → 承認済みの head が無い（受け入れ条件 4） | E4 の後 |

## 関連

- #3959（親の課題）・#4104（即時修正の判定とマージの束ね。同じ「判定した対象と消費する対象を同じにする」形）・#4105（リリース承認ゲートの保留の範囲。`mvv_answer_void` を作り、この Issue は同じ関数に欄の検査を足す）

## 移した cross-review の指摘

設計 PR https://github.com/devbasex/ai-plugins/pull/4093 のコミット `dc137953b16fca9c5e42bd115075960fd7d01748` までの cross-review の記録（https://github.com/devbasex/ai-plugins/pull/4093#issuecomment-7077635480 ）から写した。どれも #3959 の設計 PR では直した扱いだが、この範囲を #3959 から外したので、この Issue の設計で同じ論点を満たす。

| 番号 | 担当 | 重要度 | 場所 | 指摘 | 終わり方 | 見送った理由 |
| --- | --- | --- | --- | --- | --- | --- |
| r4-2 | codex | critical | issues/issue-3959-4055-design-detail.md:73 | PR の宛先を確認するのは差分取得直後だけです。その後に同じ head のまま宛先を変更すると、設計承認の自動回答は旧宛先の MVV・レッドラインで作られた結果を使えます。現行の `model.mvv_answer` は `seen` に head しか束ねず、`gate.recheck` でも宛先変更を検知できません。設計の自動回答では判定した PR 番号・宛先・head を回答の消費時に照合し、同じ head の宛先変更で人の承認待ちに戻ることをテストしてください。 | 直した | — |
| r5-2 | codex | major | issues/issue-3959-4055-design-detail.md:148 | 判定が使ったベースのコミットは `snap.commit` にありますが、省いた回答は宛先のブランチ名しか保持しません。判定後に `main` へ MVV の承認を取り消す変更やレッドラインを追加する変更が入っても、PR 番号・宛先名・head が同じなら古い規則での回答が通ります。判定に使ったベースの SHA を回答に結び付け、既存の PR 取得で得られる現在のベース SHA と照合し、一致を確認できなければ人の承認待ちへ戻す仕様を追加してください。 | 直した | — |
| r5-3 | codex | major | issues/issue-3959-4055-design-detail.md:155 | 入口の `recheck` は `run.advance` が駆動の排他を取る前に実行されます。並行する `next` の一方が回答のない状態で `recheck` を抜け、その後でもう一方が `service:mvv` の回答を書いて排他を解放すると、先に検査を抜けた側はその回答を未照合のまま消費できます。検証した回答の事象番号を消費する回答と一致させ、未検証の回答に切り替わった場合は照合してから進むよう設計を修正してください。 | 直した | — |
| r6-1 | codex | major | issues/issue-3959-4055-design-detail.md:158 | 宛先の先端を `observe(q)` より先に読むため、その REST 呼び出し中に宛先へ MVV の承認取り消しやレッドラインの追加がマージされると、PR 番号・宛先名・head が同じまま照合が通ります。事象番号の一致では、この間に変わった規則を検出できません。PR の照合を先に済ませ、駆動の排他内で対象の回答を確定した後、消費の直前に宛先の先端を照合する手順にしてください。 | 直した | — |
| r6-2 | codex | major | issues/issue-3959-4055-design-detail.md:161 | `ndf progress work` は `next` が起動する通常の子プロセスでも `progress_main` から `_progress` を通ります。仕様表どおり `checked` を渡すと、起動時点で回答があった `work` は照合して消費でき、本文の「work の駆動も読まない」と異なります。CLI の `work` と入口を経由せず直接呼ぶ `run.work` を区別し、それぞれの消費条件を明記してください。 | 直した | — |
| r6-3 | codex | major | issues/issue-3959-4055-design-detail.md:166 | `_progress` は `show`・`wait`・`resync` にも共通です。照合が通ってもこれらの操作は回答を消費しないため、表示や待機で新しい `git ls-remote` が走り、その後の `next` でも同じ回答に対して再実行されます。宛先の先端を読む処理を、実際にその回答を消費する経路に限定してください。 | 直した | — |
| r6-4 | codex | major | issues/issue-3959-4055-design-detail.md:158 | ベースの照合後に `hub.pull` が失敗すると、現行の `github_observe` は `model.Refused` を投げます。表の手順のままでは待ちへ戻す事象が書かれず、利用者は人の回答を入れられません。PR を読めなければ人の承認待ちへ戻すという概要に合わせ、例外を待ちへの遷移に変換し、待ちの表示でも同じ取得失敗が起きた場合の返し方を定めてください。 | 直した | — |
| r6-5 | codex | major | issues/issue-3959-4055-design-detail.md:162 | `_consume` に照合を追加しても、マージ用の `ndf answer head` は `approved_head` から最後の承認事象を直接読みます。旧版が書いた未消費のリリース用 `service:mvv` や、`recheck` が待ちへ戻した回答でも、後続の回答が無ければ承認済みの SHA として返ります。この取得経路でも、未照合または無効になった `service:mvv` を承認済みとして返さない条件を定めてください。 | 直した | — |
| r6-6 | kiro | major | issues/issue-3959-4055-design-detail.md | `Rechecked.checked` の仕様の文が、条件の向きを読み違えられる書き方です。「回答が無い・`gate_id` が無い・待ちへ戻した・文書だけの差で出し直さずに進めた以外の回答なら None」は、字義どおりだと「照合を通した回答のほうが None」になります。実装者が逆に作ると、`_consume` が `seq != checked` で省いた回答を永久に読まず、設計承認ゲートが止まります。「文書だけの差で出し直さずに進めた」場合の `checked` の値も、`service:mvv` の回答について決まっていません。`_docs_only_after` は `base`・`pr` の一致を比べて真を返せるので、`base_tip` が一致したまま head だけが違う省いた回答が、この経路で `checked=None` になりえます。そうなると `_consume` は読まず、次の `next` も同じ結果になって進みません。次のように書き直してください。(a) 「`checked` は、照合を通して読んでよいと決めた回答の `seq`。それ以外は None」と肯定形で書く。(b) 省いた回答（`by: service:mvv`）が文書だけの差の経路を通る場合に、`seq` を返すか待ちへ戻すかを決めて書く。(c) 決めた内容を単体テストの観点に足す。 | 直した | — |

## 移した設計（設計 PR https://github.com/devbasex/ai-plugins/pull/4093 のコミット `dc137953b16fca9c5e42bd115075960fd7d01748` から）

> この節は #3959 から分けたときの記録で、採った設計ではない。採った設計は [issue-4106-design.md](./issue-4106-design.md) にある（照合の場所は決定 1、宛先のキーの名前は `base` でなく `base_ref`（決定 3））。

#3959 の設計文書と詳細設計から外し、ここへ移した。設計の起点として使う。`MVV_SKIP_GATES` と `mvv_skip` の区分の条件（リリース承認ゲートを省かない）は #3959 に残る。

### 設計文書の構成要素とテスト設計

| 要素 | 責務 |
| --- | --- |
| 省いた回答の照合（`progress/model.py`・`progress/gate/asking.py`・`answering.py`・`entry.py`・`run.py`） | `service:mvv` の承認に判定した PR・宛先・ベースのコミット・head を持たせ、回答を読む前の検証（`recheck`）で今の PR と宛先の先端に照合し、照合した回答だけを読む。リリース承認ゲートの省略を #4105 まで止める |

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| 1・2（省いた回答） | 設計承認ゲートの `service:mvv` の承認を書いた後、同じ head のまま宛先を変えると、回答を読む前の検証が人の承認待ちへ戻す。質問の PR が判定した PR と違っても戻す | 同じ head なら宛先の変更を見ずに省いた回答を読む |

### 詳細設計の地図（該当する行）

```text
├── src/ndf/progress/model.py                   … MVV_SKIP_GATES・mvv_skip・mvv_answer（省いた回答に PR・宛先・ベースの SHA・head）
├── src/ndf/progress/gate/asking.py             … Target に base・pr・base_sha
├── src/ndf/progress/gate/answering.py          … github_observe が宛先も返す・recheck が省いた回答の PR を照合し、照合した回答の事象番号を返す
├── src/ndf/progress/gate/entry.py              … 照合した回答の事象番号とベースの先端を読む関数を Env へ渡す・待ちの表示で PR を読めなくても質問を返す
├── src/ndf/progress/gate/records.py            … approved_head が消費していない省いた回答を承認として返さない
├── src/ndf/progress/run.py                     … Env.checked・Env.base_tip・_consume が排他の中でベースの先端を照合してから省いた回答を読む・rewait
    ├── progress/gate/test_gate_mvv_answer.py *
```

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/progress/model.py` | 変更 | ステートマシンの型と規則 | — | モジュール | 定数 `MVV_SKIP_GATES`（新設）・`mvv_skip`・`mvv_answer`（変更） |
| `src/ndf/progress/gate/asking.py` | 変更 | 承認ゲートの質問 | `model` | `Target`（変更） | `differs`・`as_dict`・`from_dict`（変更） |
| `src/ndf/progress/gate/answering.py` | 変更 | 承認ゲートの回答の検証 | `asking`・`model`・`run` | `Rechecked`（新設）、モジュール | `github_observe`・`recheck`・`_docs_only_after`（変更）・`mvv_unbound`・`git_base_tip`（新設） |
| `src/ndf/progress/gate/entry.py` | 変更 | `ndf progress` の入口 | `answering`・`run` | モジュール | `_progress`・`present`（変更） |
| `src/ndf/progress/gate/records.py` | 変更 | 回答の読み出し | `run`・`model` | モジュール | `approved_head`（変更） |
| `src/ndf/progress/run.py` | 変更 | ステートマシンの駆動 | `model`・`store` | `Env`（欄 `checked`・`base_tip` を足す） | `_consume`（変更）・`rewait`（新設） |

### 判定の出力に足す欄（詳細設計の `gate_outcome`）

- `gate_outcome`（`mvv_material_decide.py`）: 引数 `holds: Sequence[str] = ()`・`pr: Optional[int] = None`・`base: Optional[str] = None`・`base_sha: Optional[str] = None` を足し、`holds` が空でなければ `holds` のキーを、`pr`・`base`・`base_sha` があればそのキーを出力に置く。`_judged` は手順 (4) 以降のすべての出力に (1) で読んだ PR の番号と `base_ref`、(2) の `snap.commit`（判定が規則を読んだベースのコミット）を渡す（(3) は承認済みの MVV が無いリポジトリの出力を今と同じにするため渡さない。`skip` が真になりうるのは (8) だけなので照合には足りる）（`head` と同じく、省いた回答を消費の時点で照合するため。下の「省いた回答の照合」）。`context.schema.json` の `$defs.mvv_gate` に任意のキー `holds`（文字列の配列）・`noted`（真偽値）・`pr`（整数）・`base`（文字列）・`base_sha`（文字列）を足す

### 詳細設計の「省いた回答の照合（設計承認ゲート）とリリース承認ゲートの省略の止め」

満たす決定: 決定 1・3

判定の出力（`mvv_gate`）は、判定した時点の PR の番号・宛先・head に属する。`service:mvv` の承認は判定のステップの後に書かれ、回答を読む（消費する）のはさらに後なので、その間に同じ head のまま宛先を変える・別の PR を見る質問になる・宛先のブランチが進む（MVV の承認を取り消す変更やレッドラインを足す変更が入り、判定が読んだ規則が古くなる）・旧版の判定が残る・PR を読めない、のどれでも、省いた回答を読む前に照合し直して人の承認待ちへ戻す。照合は 2 か所に分ける。PR（番号・宛先・head）は入口の `recheck` が照合し、照合した回答の事象番号を駆動へ渡す。宛先の先端は、回答を実際に消費する駆動（`next`・`work`）が排他（`store.run_lock`）の中で、読む回答がその事象番号と一致すると確かめた後、消費の事象を書く直前に 1 回だけ照合する（PR の照合の REST 呼び出しの間に宛先へマージされた変更も、消費の直前の先端に入るので見落とさない）。入口の照合の後に別の駆動が書いた省いた回答は、その駆動では読まない。マージの側が読む承認済みの head（`ndf answer head`）も、消費を通っていない省いた回答を承認として返さない。リリース承認ゲートは #4105 まで、判定のステップの出力（新しい判定は `skip: false`）に頼らずゲートの側でも省かない（旧版で済ませた `release-mvv` の `skip: true` と、旧版が書いた未消費の `service:mvv` の回答から再開されうるため）。

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `MVV_SKIP_GATES` | `progress/model.py` | 新設 | `tuple[str, ...] = ("design",)` | MVV 判定で省いてよい承認ゲートの区分。#4105 が `release` を足す |
| `mvv_skip` | `progress/model.py` | 変更 | シグネチャは同じ | 今の条件に、`gate in MVV_SKIP_GATES` と、`held` が `pr`（整数）・`base`（文字列）・`base_sha`（文字列）・`head`（文字列）をすべて持つことを足す。どれかが外れれば None（承認ゲートで人を待つ）。旧版の判定（`pr`・`base`・`base_sha` を持たない）と旧版で済ませた `release-mvv` の `skip: true` はここで外れる |
| `mvv_answer` | `progress/model.py` | 変更 | シグネチャは同じ | `seen` を `{"head": held.head, "base": held.base, "pr": held.pr, "base_sha": held.base_sha, "material_sha256": None}` にする（判定した PR・宛先・規則を読んだベースのコミット・head） |
| `Target` | `progress/gate/asking.py` | 変更 | 欄 `base: Optional[str] = None`・`pr: Optional[int] = None`・`base_sha: Optional[str] = None` を足す | `as_dict`・`from_dict` が `base`・`pr`・`base_sha` を読み書きする。`differs` は `head`・`material` を今どおり比べ、`base`・`pr` は見た側（`seen`）が値を持つときだけ比べる（今の人の回答の `seen` は持たないので、人の回答の照合は変わらない）。`base_sha` は `differs` で比べない（`github_observe` は読まない。照合は `_consume` が排他の中で `base_tip` で行う） |
| `github_observe` | `progress/gate/answering.py` | 変更 | シグネチャは同じ | 今の `hub.pull(q.pr)` の 1 回の結果から `head_sha` と `base_ref` を読み、`Target(head, material, base=base_ref, pr=q.pr)` を返す。REST の呼び出しは増えない |
| `git_base_tip` | `progress/gate/answering.py` | 新設 | `git_base_tip(cwd: Path) -> run.BaseTip`（`BaseTip = Callable[[str], Optional[str]]` は `run.py` に置く） | 宛先のブランチ名を受け、判定と同じリモート（`_judged` の (2) で `git fetch` した先）の今の先端の SHA を `git ls-remote <リモート> refs/heads/<ブランチ>` の 1 回で返す。読めない・無いときは None。PR の取得の `base.sha` は使わない（GitHub が宛先の更新に追いつく時点が決まっておらず、判定が `git fetch` で読んだ先端と同じ値を返す保証が無いため） |
| `mvv_unbound` | `progress/gate/answering.py` | 新設 | `mvv_unbound(p: Mapping[str, Any], q: Question) -> Optional[str]` | 回答の payload が `by: service:mvv` のときだけ、照合できない理由を返す。`q.gate not in MVV_SKIP_GATES` →「リリース承認ゲートは、保留を読む PR の範囲が決まるまで（#4105）MVV 判定で省かない」。`seen` が `pr`・`base`・`base_sha`・`head` のどれかを持たない →「省いた回答が判定した PR・宛先・ベースのコミット・head を持たない（旧版の判定）」。`q.pr` が None →「照合する PR が決まらない」。ほかは None |
| `Rechecked` | `progress/gate/answering.py` | 新設 | `NamedTuple(why: Optional[str], checked: Optional[int])` | `recheck` の結果。`why` は今の返り値（警告に載せる理由）。`checked` は、照合を通して読んでよいと決めた回答の事象番号（`seq`）で、読んでよいと決めるのは「今の対象と違いが無い」か「人の回答で、違いが文書だけの差で出し直さずに進めた」の 2 つだけ。それ以外（回答が無い・`gate_id` が無い・待ちへ戻した）は None。省いた回答（`by: service:mvv`）は文書だけの差の経路を通らない（下の `recheck`）ので、省いた回答の `checked` が `seq` になるのは違いが無いときだけ |
| `recheck` | `progress/gate/answering.py` | 変更 | `recheck(st, machine_id, observe, docs_only=never_docs_only) -> Rechecked` | 入口で PR を照合する（宛先の先端は読まない。下の `_consume`）。省いた回答（`by: service:mvv`）なら、(1) `mvv_unbound(p, q)` に理由があれば `observe` を呼ばずに待ちへ戻す（`why` は「回答した後に対象が変わった: 」でなく「MVV 判定で省いた回答を読まない: <理由>」）。(2) `observe(q)` を呼び、`model.Refused`（`github_observe` が `hub.pull` の `GitHubError`・`GitError` を包んだもの）を受けたら例外を外へ出さず、同じく待ちへ戻す（`why` は「MVV 判定で省いた回答を読まない: PR #<番号> を読めない: <原因>」。PR を読めなければ人の承認待ちへ戻す）。(3) `observe(q).differs(seen)` に違いがあれば待ちへ戻す（`seen` が `pr`・`base` を持つので、同じ head の宛先の変更と別の PR を検知する）。省いた回答は `_docs_only_after` を呼ばず、文書だけの差でも待ちへ戻す（文書だけの差の例外は人がリリース承認ゲートで見た範囲のためのもの。#4105 で `release` を省けるようにしても、省いた回答へ広がらない）。(4) 違いが無ければ `Rechecked(None, m.event.seq)`。待ちへ戻したときは `Rechecked(why, None)`。人の回答は今どおり（`observe` の `model.Refused` も今どおり外へ出す）で、違いが無ければ `Rechecked(None, seq)`、文書だけの差で進めたら `Rechecked(why, seq)`、待ちへ戻したら `Rechecked(why, None)`。待ちへ戻す事象は `run.rewait` で作る |
| `_docs_only_after` | `progress/gate/answering.py` | 変更 | シグネチャは同じ | 今の条件に、`base`・`pr` の一致を足す。比べるのは `Target.differs` と同じく、回答に記録済みの値（`seen` が値を持つ側）だけ。`seen.base` が値を持ち `now.base` と違う、か `seen.pr` が値を持ち `now.pr` と違えば偽（文書だけの差でも出し直す）。人の回答の `seen` は `--head` と `--material-sha256` からだけ作るので `base`・`pr` が None で、`base`・`pr` を返す新しい `github_observe` の下でも今どおり文書だけの差なら出し直さずに進む（#3020） |
| `_progress` | `progress/gate/entry.py` | 変更 | シグネチャは同じ | `recheck(st, id, seen, git_docs_only(cwd))` の `checked` と `git_base_tip(cwd)` を `lane_env(run.Env(facts=…, checked=checked, base_tip=git_base_tip(cwd)))` で駆動へ渡す。`git_base_tip` は関数を作るだけで git を打たない（打つのは `_consume` の呼び出しだけなので、`show`・`wait`・`resync`・`answer` と、省いた回答の無い `next` では `git ls-remote` が走らない）。`why` は今どおり警告に載せる |
| `present` | `progress/gate/entry.py` | 変更 | シグネチャは同じ | 待ちの表示で `observe(asked)` が `model.Refused`（PR を読めない）を投げたら、例外を外へ出さず `target` を `Target()`（head 無し）にして質問を返し、警告「PR #<番号> の head を読めないので、質問に今の head を載せていない: <原因>。PR を読めるようになってから ndf progress next で質問を出し直すか、PR の head を見て ndf answer で回答する（回答も head を照らす）」を足す。結果は今どおり承認ゲートの待ち（`gate`）で、ステートマシンの待ちは変えない（省いた回答を待ちへ戻した直後に同じ取得失敗が続いても、利用者は質問を受け取れる） |
| `Env` | `progress/run.py` | 変更 | 欄 `checked: Optional[int] = None`・`base_tip: Optional[BaseTip] = None` を足す | `checked` は入口の `recheck` が照合を通した回答の事象番号、`base_tip` は宛先の先端を読む関数。どちらも `ndf progress` の入口（`entry._progress`）だけが置く。`next` が起動する `ndf progress work` の子プロセスも `progress_main` → `_progress` を通るので、自分の入口の `recheck` で `checked` と `base_tip` を持つ。入口を通らずに `run.advance`・`run.work` を直接呼ぶ駆動（`run.main` とテスト）はどちらも None |
| `rewait` | `progress/run.py` | 新設 | `rewait(v: View, m: Move, why: str) -> model.EventDraft` | 承認ゲートのステップを待ちへ戻す事象（`status: waiting`・`reason: why`・今の `generation`）。`recheck` と `_consume` が共有する（今の `recheck` の中の組み立てを移す） |
| `_consume` | `progress/run.py` | 変更 | シグネチャは同じ | 駆動の排他の中で、回答（`m.event`）の payload が `by: service:mvv` なら次の順に確かめる。(1) `m.event.seq != d.env.checked` か `d.env.base_tip` が None なら、読まずに `next` を返す（summary「省いた回答は検証を通してから読むので next を打つ」）。(2) `tip = d.env.base_tip(seen.base)` を 1 回呼び、None か `seen.base_sha` と違えば `d.write(rewait(v, m, why))` で待ちへ戻し（`why` は「MVV 判定で省いた回答を読まない: 判定が規則を読んだベース <base_sha> と今の <宛先> の先端 <SHA か 読めない> が違う」）、警告に `why` を載せて駆動を続ける（次の手は `GATE` で人の承認待ち）。(3) 一致すれば今の承認の読み方で消費する（ステップの `finished` を書く）。(2) と (3) の間に別の読み取りを挟まない。人の回答は今どおり読み、`base_tip` を呼ばない |

- 省いた回答を消費する手順は、(1) 入口の `recheck` が PR（番号・宛先・head）を照合して事象番号を `checked` に決める、(2) `next`・`work` の駆動が排他を取り、`decide` が読む回答の事象番号が `checked` と一致すると確かめる、(3) 宛先の先端を 1 回読んで `seen.base_sha` と比べる、(4) 一致すれば消費の事象を書く、の順。(3) は (1) の REST 呼び出しの後に打つので、PR を読む間に宛先へマージされた MVV の承認の取り消しやレッドラインの追加も (3) の先端に入る。(3) と (4) は同じ排他の中で続けて打ち、(4) の後のマージは、消費した後の変更として次の判定（人の回答の後の次の判定から）に任せる
- `recheck` の今の契約（どのサブコマンドでも回答を読む前に打つ・`_consume` は省いた回答を書いた駆動では読まずに `next` を返す）は保つ。`recheck` は `run.advance` が排他を取る前に打つので、並行する `next` の一方が回答の無い状態で入口を抜け、もう一方が `service:mvv` の回答を書いて排他を放すと、先に入口を抜けた側の駆動は未照合の回答を見る。このとき `checked` が None（か別の事象番号）なので `_consume` は読まずに `next` を返し、次の `next` の入口の `recheck` が照合する
- 消費できる駆動: `ndf progress next`（`run.advance`）と、`next` が起動する `ndf progress work` の子プロセス（`run.work`）。どちらも `progress_main` → `_progress` を通り、自分の入口で照合した `checked` と `base_tip` を持つので、子プロセスの起動の時点で回答があれば、子が自分の入口で照合し、排他の中で先端を照合してから消費できる。入口を通らずに `run.advance`・`run.work` を直接呼ぶ駆動は `checked`・`base_tip` が None で、省いた回答を読まずに `next` を返す。`show`・`wait`・`resync`・`answer` は回答を消費しないので、入口の `recheck` は打っても宛先の先端を読まない
- 人の回答は `_consume` の突き合わせの外に置く（入口の照合と駆動の間に `ndf answer` が書く回答は、回答した者が直前に見た対象を持ち、この設計の前から同じ扱い。省いた回答だけが判定と消費の間に時間が空く）
- 宛先のブランチの先端の照合は省いた回答を消費する駆動だけで、`git ls-remote` は省いた回答を消費する直前の 1 回だけ増える（人の回答・回答の無い `next`・`show`・`wait`・`resync` では打たない。同じ回答を照合してから消費するまでに打つのは 1 回）。宛先へ別の変更がマージされて先端が進むと、省いた回答は待ちへ戻り人の承認を待つ（判定が読んだ MVV とレッドラインが今の宛先と同じことを、コミットの一致でしか確かめない。進んだ先端で判定し直すのは人の回答の後の次の判定から）
- マージの側（`ndf answer head`）: `approved_head` は、区分 `kind` の最後の回答が `by: service:mvv` のとき、`kind in MVV_SKIP_GATES` で、かつその回答の事象番号を `report.metrics.seq` に持つステップの `finished` の事象（`_consume` が照合を通して消費した印。`gate_report` が今も書く）があるときだけ head を返す。どちらかが無ければ None（承認済みの head が無いとして `refused` で止まる）。旧版が書いた未消費のリリース用 `service:mvv`、`recheck`・`_consume` が待ちへ戻した回答、まだ消費していない回答は、後続の回答が無くても承認済みの SHA として返らない。人の回答の扱いは今のまま
- 待ちへ戻した後は `prog.waiting` があるので `_running_move` は省かずに `GATE` を返し、人の回答を待つ（判定のステップを打ち直さない。打ち直しても `release` は `skip: false`）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力 | 再実行 |
| --- | --- | --- | --- | --- | --- | --- |
| `recheck` | 省いた回答の宛先が今の宛先と違う | 判定の後に同じ head のまま宛先を変えた | `Target.differs` の `base` | 待ちへ戻す | 承認ゲートの待ち。理由に見た宛先と今の宛先 | 人が回答する |
| `recheck` | 省いた回答の PR が質問の PR と違う | 判定した PR（`worktree` のブランチ）と文脈の PR が分かれた | `Target.differs` の `pr` | 同上 | 同上 | 同上 |
| `recheck` | 省いた回答の PR を読めない | GitHub が回答しない | `observe` の `model.Refused` | 待ちへ戻す（例外を外へ出さない） | 承認ゲートの待ち。理由に PR を読めない原因。待ちの表示（`present`）でも PR を読めなければ head 無しの質問と警告 | 人が回答する（PR を読めるようになってから） |
| `_consume` | 宛先の先端が判定のベースと違う | 判定の後（PR の照合の間を含む）に宛先へ MVV の承認の取り消し・レッドラインの追加などがマージされた | 排他の中の `base_tip` と `seen.base_sha` | `rewait` で待ちへ戻す | 承認ゲートの待ち。理由に判定のベースと今の先端 | 人が回答する |
| `_consume` | 宛先の先端を読めない | `git ls-remote` の失敗・ブランチが無い | `base_tip` が None を返す | 同上 | 理由に「読めない」 | 同上 |
| `approved_head` | 消費していない省いた回答が最後の回答 | 旧版が書いた未消費の `service:mvv`・待ちへ戻した省いた回答 | `MVV_SKIP_GATES` と `finished` の `report.metrics.seq` | 承認済みとして返さない | `ndf answer head` が `refused`（承認した回答が無い） | 人が回答して消費されてから |
| `recheck` | リリース承認ゲートの省いた回答 | 旧版が書いた未消費の `service:mvv` の回答 | `mvv_unbound` | 同上（`observe` を呼ばない） | 理由に #4105 | 同上 |
| `recheck` | 旧版の判定で省いた回答 | `seen` に `pr`・`base`・`base_sha` が無い | `mvv_unbound` | 同上 | 理由に旧版の判定 | 同上 |
| `_consume` | 照合していない省いた回答 | 入口の照合の後に、並行する駆動が `service:mvv` の回答を書いた・入口を通らない駆動 | `m.event.seq != env.checked`・`env.base_tip` が None | 読まない | `next` を返す | 次の `next` の入口で照合する |
| `mvv_skip` | 旧版で済ませた判定の `skip: true` | 更新の前に `release-mvv`・`design-mvv` を済ませた | `MVV_SKIP_GATES`・`pr`・`base`・`base_sha` の欠け | 省かない | 承認ゲートの待ち | 人が回答する |

### 単体テストの観点

`tests/progress/gate/test_gate_mvv_answer.py`（新設）で、実際のステートマシン（`store` と `run`）に事象を書いて縛る。

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `recheck`（設計） | 判定と同じ PR・宛先・head → `Rechecked(None, その回答の seq)`。`recheck` は `base_tip` を受けない | 同じ head のまま宛先だけを `main` から `develop` へ変えた `observe` → 待ちへ戻り `checked` は None、次の `decide` が `GATE`（人の承認待ち）で、`service:mvv` の承認で工程が進まない。head だけが違い `docs_only` のスタブが真を返す省いた回答 → `docs_only` を呼ばずに待ちへ戻り `checked` は None | 質問の PR が判定した PR と違う → 待ちへ戻る。`observe` が `model.Refused` を投げる → 例外を出さず待ちへ戻り、理由に PR を読めない原因。`seen` に `pr`・`base`・`base_sha` のどれかが無い省いた回答（旧版） → `observe` を呼ばずに待ちへ戻る |
| `recheck`（リリース） | — | 旧版が書いた未消費の `service:mvv` の回答（head は今と同じ）から再開 → `observe` を呼ばずに待ちへ戻り、理由に #4105、次の `decide` が `GATE` | — |
| `recheck`（人の回答） | 違いが無い → `checked` がその回答の seq | 文書だけの差で進めた → `why` があり `checked` がその回答の seq | — |
| `_consume`（省いた回答） | `checked` が回答の seq で `base_tip` が `base_sha` を返す → 回答を消費し、工程が進む。`base_tip` は 1 回だけ呼ばれる | PR 番号・宛先・head は同じで、`observe` の呼び出しの中（スタブ）で宛先へ MVV の承認を取り消すコミットを進めると、排他の中の `base_tip` が別の SHA → 待ちへ戻り `GATE`。`ndf progress work` の子プロセスの入口（`progress_main` の `work`）で照合した `checked` → 子の駆動が消費できる | `base_tip` が None を返す → 待ちへ戻る。`run.work` を直接呼ぶ（`checked`・`base_tip` が None）→ 読まずに `next` |
| `_consume`（並行） | — | 回答の無い状態で `recheck` を通した（`checked` が None）後に、別の駆動が `service:mvv` の回答を書いた状態で `advance` → 回答を読まずに `next` を返し、`base_tip` を呼ばず、工程は承認ゲートのまま | `checked` が別の事象番号 → 同じく読まない |
| `_progress` | `show`・`wait`・`resync` で省いた回答を照合しても `git ls-remote`（`base_tip`）を打たない（呼ぶと落ちるスタブ） | 人の回答の `next` でも打たない | — |
| `present` | — | — | 待ちの表示で `observe` が `model.Refused` → 承認ゲートの待ちの結果（head 無し）と警告を返し、例外を出さない |
| `approved_head` | 照合を通して消費した設計の省いた回答 → その head | 人の回答の後の消費していない省いた回答・待ちへ戻した省いた回答 → None | 旧版が書いたリリース用の未消費の `service:mvv` が最後の回答 → None（`ndf answer head --gate release` が `refused`） |
| `_docs_only_after` | 人の回答の `seen`（`base`・`pr` が無い）と、`base`・`pr` を返す `observe`（新しい `github_observe` の形）で、head だけが違い `docs_only` が真 → リリース承認ゲートを出し直さずに進む（#3020 の経路が保たれる） | `seen` が `base` を持ち `now.base` と違う、か `seen.pr` が `now.pr` と違う → `docs_only` が真でも偽 | — |
| `mvv_skip`・`decide`（リリース） | — | 旧版で済ませた `release-mvv` の出力 `skip: true`（`pr`・`base` 無し・有りの両方）から再開 → `service:mvv` の回答を書かず `GATE` | — |
| `mvv_skip`（設計） | `pr`・`base`・`base_sha`・`head` を持つ `skip: true` → 省く | `pr`・`base`・`base_sha` のどれかが欠ける → None | — |
| `Target.differs` | 人の回答の `seen`（`base`・`pr` 無し）は今の宛先を比べない | `seen` が `base` を持ち今と違う → 違いに載る | — |



## 起票者の推定

| 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- |
| 宛先の名前に加えて、判定が規則を読んだベースのコミットを照合する | #4093 の cross-review の指摘 r5-2 | 受け入れ条件 2 |
| 照合と消費の間に入る並行の駆動の回答を消費しない | 指摘 r5-3・r6-1 | 受け入れ条件 3（照合を消費の直前へ移して言い換えた） |
| マージの側の承認済みの head も消費されていない省いた回答を返さない | 指摘 r6-5 | 受け入れ条件 4 |
| 旧版が書いた回答と判定から再開しても人へ問う | 指摘 r6-5・#4093 の r8-3 | 受け入れ条件 5（欄の欠けで決める形へ言い換え、旧版の移行は含まない） |
| #3959 の実装の後に実装する | conductor の分割の判断（2026-11-22） | 前提 1 |

## リクエスト（原文）

> MVV 判定で承認ゲートを省いた回答（回答した者 `service:mvv`）を、読む（消費する）時点で、判定した PR の番号・宛先・宛先の先端（判定が規則を読んだベースのコミット）・head と照合し、どれかが違えば人の承認待ちへ戻す。

書き手: 起票者（#3959 から分けた conductor）・2026-11-22

> #4106 は #3959 の設計（PR https://github.com/devbasex/ai-plugins/pull/4093 、`issues/issue-3959-*`、マージ済み、決定 9）から分けた課題。
> 根拠は MVV 版 11（`/work/ai-plugins/.ndf/mvv.md`）で引く。普及の段階までは後方互換は要らない（利用者の方針 2026-11-16）。旧版の状態からの再開は扱わない（#3959 の決定）。

書き手: AI の起動指示（計画 p12 のレーン lcrfast の conductor）・2026-11-22。「普及の段階までは後方互換は要らない」は利用者の方針（2026-11-16）の引用



