# #3703 の決定の記録

設計文書は [issue-3703-design.md](issue-3703-design.md) にある。決定 1〜7 は、課題の「決めること」の 7 つの問いに、次の順で答える。

1. CI の待ちの置き場
2. 先端が動いたときの入れ直し
3. `queued` のときの公開の時期
4. 版上げの PR を開いて止める手段
5. 開発版のプランが無いときの本番の版
6. CD の無い trunk の承認資料を作る主体
7. 起票者の案 S2 の採否

## 決定の記録

### 決定 1: `release-steps.py land` の約束を変えないため、CI の待ちを本番の取り込み（`land_approved` の手前）に置く

本番の取り込み（新設の `release_lib/prod_land.py` の `land`）が、`land_approved` を呼ぶ前に承認した head の検査を待つ。待ちは今の `reviewed.wait_ci_except`（PR のチェックのバケットを読み、中身の失敗で 1・上限で 75）をそのまま使う。承認の証跡のチェック（`ndf/release-approval`）が必須なら今の `wait_trail_status` も同じ順で待つ。`land_approved` は今のまま待たず、入れる直前に `checks_failing` で検査がそろっていることだけを確かめる。

`land_approved` の中へ待ちを入れる形は採らない。`release-steps.py land` の単独の呼び出しも待つようになり、受け入れ条件 14（引数・結果・終了コードを変えない）と、その呼び出しを打つ人が待ちを持たない今の使い方が変わる。`checks_failing` は実行中と失敗を同じ「そろっていない」で返すため、1 と 75 を分けられないことも理由である。

根拠: Value 3 / Value 11（MVV 版 9）

### 決定 2: 承認の外のコミットを本番ブランチへ入れないため、trunk は承認した head との完全な一致だけで入れ、分かれた経路は今の比べ方で決めた先端だけを入れる

trunk（版上げの PR が本番の PR を兼ねる）では、承認の記録・証跡の SHA と版上げの PR の今の head が一致するときだけ本番の取り込みへ進む。違えば承認ゲート（10）で止め、承認の取り直しを求める。許容リスト（#2221。`docs/`・`issues/` だけを触るコミット）で入れ直さない。trunk の承認は版上げの PR の head そのものを対象に取るため、承認の後の差は必ず承認の外である。今の CD の無い trunk の経路が許容に版上げの PR 自身のコミットを渡している（`release_pr_allowed` に同じ PR を渡す）形も、この決定で無くなる。

ベースブランチと本番ブランチが分かれた CD の無い経路は、`_merge_prod_pr` と `_merge_approved` である。この経路は、承認したコミット（開発版の承認資料）から比べて許容に収まった先端（`verdict.tip`）を、「承認した head」として本番の取り込みへ渡す。比べ方は変えない。本番の取り込みは入れる直前に PR の今の head を読み、比べた先端と違えば今の `HeadMoved` を投げ、呼び手が今の回数（`HEAD_MOVED_RETRIES`）まで比べ直す。CD のある経路は今のとおり完全な一致だけで通す（#2773 の決定 7）。

分かれた経路まで常に承認を取り直す形は採らない。分かれた経路の承認は開発版の時点のコミットを対象にし、本番のプランの bump・changelog・消費の記録が承認の後に必ず積まれるため、どのリリースも承認ゲートへ戻る。

分かれた経路の扱いは、次の 2 つを「許容の比べ方を承認の延長とみなす」と読んだ上に立つ。要求の前提 2（承認した head と PR の今の head の一致）と、受け入れ条件 6（承認していないコミットを本番ブランチへ入れない）である。この読み替えは設計者が決め、人へ残さない。今の `_merge_prod_pr`・`_merge_approved` は、既に許容で決めた先端を入れている。この設計は入れる手段を `land_approved` へ替えるだけで、本番ブランチへ入るコミットの範囲を広げない。そのため、必ず人の承認が要る操作の C1〜C8・P1・P2 のどれにも当たらない。`land_approved` へ渡す承認の SHA は、分かれた経路では「許容で決めた先端」を指す。不変条件は trunk（I2）と分かれた経路（I9）に分けて持つ。

根拠: Value 1 / C4（MVV 版 9）

### 決定 3: merge queue の検査の結果を公開の前提にするため、`queued` では公開せず、打ち直しで公開する

`land_approved` が `queued` を返したら、`release` はタグと GitHub Release を作らず、公開を欠けとして `stopped`・終了コード 6（欠けが残る）で返す。`next` は同じコマンドの打ち直しである。打ち直したとき、PR がマージ済みなら今の再開の経路（`_resume_merged_prod`・`_open_prod_pr` のマージ済みの分岐）で無いものだけを公開する。PR がまだ merge queue にあれば、`--ci-wait` を上限に merge queue のマージを待ち、入れば公開し、上限なら 75 で返す。merge queue から外れて開いていれば、`land_approved` を打ち直して宣言どおりに入れ直す。返す値はその実行の結果（merge queue へ入れたなら 6、入れたなら 0、照合や検査で止まれば 10・1・75）である。merge queue にあるかは、GitHub の GraphQL API の PR の項目 `isInMergeQueue` を、本番の取り込みの中（`prod_land.queue_state`）で読む。実測は設計文書の「merge queue にあるかの読み方」にある。

本番のプランの release のステップは、6 を受けたら `release-landed`（同じコマンドを打つ別のステップ）へ進み、そこでの 6 は止める。1 回目は待たずに返し、2 回目で待つため、受け入れ条件 1（merge queue へ入れた直後は公開しない）と、プランが人を介さず本番まで流れることが両立する。

1 回目の実行の中で merge queue のマージを待ってから公開する形は採らない。受け入れ条件 1 は、投入の直後の結果に公開が無いことを求める。merge queue の検査（PRD1）は合成のブランチで新しく走るため、待つ時間を 1 回目の上限に重ねると、今の CI の待ちと合わせて上限が 2 倍になる。

根拠: Value 3 / Value 13（MVV 版 9）

### 決定 4: trunk の本番のプランが承認の前に版上げの PR を開いて止まれるよう、`release` を「PR を開く → 承認を照らす → 取り込む」の順にする

trunk の `release --channel prod` は、承認を読む前に版上げの PR を開く（開いた PR があれば使い回す）。承認が無いか head と違えば、検査を待ってから承認ゲート（10）で止まる。CD のある場合は、今の `reviewed.release_prod` を使う。この関数は、trunk では開発版の PR をマージせずに版上げの PR を開く分岐を既に持つ。CD の無い場合は、新設の `release_lib/trunk_release.py` が同じ順で動く。CD の無い場合に承認資料・承認の記録がまだ無いことは、前提の不足（3）ではなく「承認がまだ無い」（10）として扱う。

本番のプランは CD の有無によらず、release（1 回目）の 10 の行き先を承認前の試用（`trial`）にし、その後に承認資料を作るステップを置く。CD のある場合は今の `prod-cd` の種類（`cd_prod_steps`）の行き先だけを変え、CD の無い trunk には新しい種類 `prod-trunk` を足す。

trunk の本番のプランだけ別の起動（開く用のコマンドと入れる用のコマンド）に分ける形は採らない。同じ `release` が承認の有無で止まる形を CD のある場合が既に持ち、2 つの形を持つと打ち直しの説明が経路ごとに分かれる。

根拠: Value 3 / Value 11（MVV 版 9）

### 決定 5: 開発版のプランが無くても版が決まるよう、trunk の本番のプランは系列を持たず、キューが origin から版を決めて記録する

trunk の本番のプランの「版」の欄は、「系列」を空にする。「明示」が無ければ、キューが流す時点に `_fill_prod` が次の正式版を決め、版の記録（決め方 `decided`）を書く。次の正式版は、origin の最新のタグ・本番ブランチの版・ベースブランチの版の基底のうち最も大きいものを、上げ幅で上げた版である。決めた後の埋めは記録の版を使い、決め直さない（不変条件 I7）。決める関数は今の `decide_dev` と同じ観測（`release_version.observe`）を使う `decide_release` を `lib/release_version.py` に足す。`version_field` は、系列の無い本番を、trunk の計画のときだけ組めるようにする。

本番のプランに開発版のプランと同じ決め方（接尾辞つきの版）を持たせる形は採らない。trunk には開発版のチャネルが無く、接尾辞つきの版は出す先が無い。

- 作り方: 自前で作る
- 比べた候補: PRD1（GitHub の merge queue は版を決めない。版の決め方は NDF の版の記録の仕組みの延長で持つ）

根拠: Value 3 / Value 4（MVV 版 9）

### 決定 6: CD の無い trunk でも承認ゲートの前に承認資料がそろうよう、承認資料は本番のプラン自身が作り、承認は手元の承認資料の記録で取る

CD の無い trunk の本番のプランは、release（1 回目）の 10 の後に承認資料を書く。組み方は今の開発版のプランと同じ `facts → explain → others` で、`--head release/v<版>` を渡す。承認の場所は、今の CD の無い場合（`release_case` の `no-cd`）のまま、手元の承認資料の記録である。`approve` のステップは、自分のプランの facts の出力（自分の状態ディレクトリ `{state_dir}`）から承認した SHA を読み、版上げの PR の head と同じ SHA を記録する。normal は others の後で終えて、リリース承認ゲートへ進む（ゲートのステージを計画に置かない）。fast・auto は others の後に MVV 判定を置く（今の `_add_prod_mvv_gate` の判定を先頭から others の後へ移す）。

ゲートで止まる仕組みは今の CD のある本番のプランと同じものを使う。normal の release（1 回目）は 10 を `gate_next: trial` で受け、実行の基盤がこれを承認ゲートとして記録する。そのため others の後で完了しても結果は「関門」になり、キューの結果は `gate` になる。キューは前のステージがすべて完了したときだけ次のステージを流すため、承認の前に後続のステージは動かない。プランの中の approve・release（2 回目）・cd-result は、承認の後に `run <プラン> --from approve` を打つまで動かない。fast・auto は 10 を `on_exit` で trial へ送り、承認ゲートかは MVV 判定が決める。新しい終了コード・状態は足さない。

CD の無い trunk でも承認を PR の上の証跡へ移す形は採らない。CD の有無から決まるリリースの場合（`release_case`）は、全体テストを流すかなどほかの判断にも使われており、承認の場所だけを取り込み方で変えると、同じ場合の名前が 2 つの意味を持つ。

根拠: Value 1 / Value 11（MVV 版 9）

### 決定 7: 承認前の試用を CD の有無によらず通すため、起票者の案 S2 を採り、CD の無い trunk にも同じ位置で当てる

trunk の計画から開発版のステージを外す。本番のプランの release（1 回目）の後・facts の前に、`trial`（版上げの PR の head からの `verify-install`）を置く。normal は `verify-install --ref release/v<版> --expect <版> --runtimes <宣言の並び>` を打つ。fast・auto は `.ndf/pace.json` の `verify` の `{ref}` を `release/v<版>` に置き換えて打つ。置き換えは計画を組む時点に行い、`{release_version}` の差し込みはキューが流す時点に埋める。試用で導入に失敗したら judge へ進み、承認資料を作らない。

S2 は CD のある trunk だけを対象にしていたが、CD の無い trunk も開発版のチャネルを持たず、試用の場所が版上げの PR の head しか無いため、同じ位置へ置く。

根拠: Value 13 / Value 5（MVV 版 9）

### 決定 8: 本番ブランチへ入れる経路を 1 か所にするため、4 つの呼び出しを本番の取り込みへ集め、`ProdOps.wait_and_merge` を `ProdOps.land` に替える

本番ブランチ宛てのマージを持つ 4 か所は、すべて `prod_land.land(root, a, pr, head, trail_url)` を呼ぶ。4 か所は `_merge_prod_pr`・`reviewed.merge_on_pr_approval`・`strategy_route._merge_approved`・CD の無い trunk で、一覧は設計文書の「本番ブランチへ入れる経路」にある。`reviewed.merge_on_pr_approval` は、`reviewed.release_prod` と `strategy_route._merge_exact` の 2 つから呼ばれる。`_merge_exact` は自分ではマージしないため、1 か所を替えれば両方の呼び出し元が本番の取り込みを通る。`land` は宣言（メインディレクトリの `branch_strategy`。`land_cli` と同じ読み方）を読み、GitHub 版の Port（`GhPort`）で `land_approved` を呼ぶ。ベースブランチへ入れるマージ（開発版の PR・`-sync` の PR・戻すマージ）は `wait_and_merge` のまま残す（不変条件 I8）。

`merge-when-green` に宣言の主体と方式の分岐を足す形は採らない。主体・方式・履歴の作り方・ruleset との照合・迂回の記録・早送りは `land_approved` が既に持ち（#3672 の I5・I7・I10）、2 か所に持つと宣言の読み方が分かれる。`gh pr merge --auto`（PRD1 のキューへの投入）を直接打つ形も、ruleset との照合と承認した head の固定を持たないため採らない。

- 作り方: 自前で作る
- 比べた候補: PRD1

根拠: Value 11 / Value 9（MVV 版 9）

### 決定 9: 宣言の主体が人の場合も打ち直しで進めるよう、`queued` と `human` を同じ「公開の欠け」（6）で返し、新しい終了コードを足さない

`human` も `queued` と同じく `stopped`・6 で返し、`gaps` の理由と `next` だけを「人のマージを待つ」「人がマージした後に打ち直す」にする。プランの `release-landed` は 6 で止まるため、人のマージの後に `run <プラン> --from release` で続ける。

承認ゲートの範囲（10〜19）の新しい値で返す形は採らない。CD のある本番のプランは release の 10 を「承認がまだ無い」として承認資料の組み立てへ回すため、範囲の扱いを経路ごとに分ける必要が出る。

根拠: Value 3（MVV 版 9）

### 決定 10: 迂回の記録を 1 か所で書くため、本番の取り込みでは `land_approved` が書き、CD の経路の `admin_record_item` を外す

`land_approved` は管理者の迂回でマージしたら、迂回の意図の印と記録を自分で書く（#3672）。CD の経路（`merge_on_pr_approval`）が取り込みの後に書いていた記録は、同じ記録を 2 回書くことになるため外す。再開（マージ済み）の経路の `admin_record_on_resume` は、`land_approved` を通らない打ち直しのために残す。迂回するかは `land_approved` の判定（`merge.method: direct` と `merge.admin: true`）だけで決まり、#3672 の手順 1 で直した判定を変えない。

根拠: Value 11（MVV 版 9）

### 決定 11: 試用の結果をどの経路でも同じ欄に載せるため、承認資料の欄の名前を「承認前の試用で確かめたこと」にする

承認資料の欄「開発版のデプロイで確かめたこと」を「承認前の試用で確かめたこと」へ改める。10 系の書き手（`materials.write_approval`）・説明（`release-steps.md`・`approval-request.md`）・テストの入力をそろえる。分かれた経路の開発版のデプロイも承認前の試用の一つであり、欄の中身（経路・ref・版・一致したか）は変わらない。trunk では ref が `release/v<版>` になる。古い欄の名前は読まない（普及のフェーズまで後方互換を求めない）。

根拠: Value 4 / Value 14（MVV 版 9）

### 決定 12: スプリントの終わりの計画も同じ規則で本番まで流れるよう、`new close` にも開発版を置かない規則を当てる

`close_waves`（`new close`）は最終の検査の後に開発版と本番を置いており、trunk でも開発版のプランを作る。同じ判定（`trunk_release(a)`）で開発版のステージを外し、本番のプランだけを置く。課題の対象範囲は `new sprint` を挙げているが、同じ計画の組み立てを使う `new close` に残すと、trunk のスプリントの終わりが同じ 3 で止まる。

要求の対象範囲を広げるが、同じ組み立ての不具合を同じ判定で直すもので、必ず人の承認が要る操作に当たらないため、設計者が決める。

根拠: Value 7（MVV 版 9）
