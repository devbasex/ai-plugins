# #4105: MVV 判定: リリース承認ゲートでレビューの保留を読む PR の範囲を、承認資料と同じ確定した集合から得る

リリース承認ゲートで、レビューの保留（cross-review の記録に残るレッドラインの指摘と未解消の見送り）を読む PR の範囲を、ブランチ戦略によらず、承認資料と同じ確定した集合から得る。

## 何が起きたか

#3959 の設計 PR https://github.com/devbasex/ai-plugins/pull/4093 で、承認ゲートが対象の PR の cross-review の記録を読み、レッドラインか未解消の見送りが残れば `fast`・`auto` でも省かない仕組み（#4076 から移したもの）を設計した。設計承認ゲートの対象は設計 PR の 1 件で決まるが、リリース承認ゲートの対象（リリースに含まれる単位の PR）は、ブランチ戦略（`trunk` の `release_pr`・`production_pr`、`release_branch`、`merge_base_branch`）ごとに決め方が違う。cross-review の 3 ラウンドで、この範囲の決め方への指摘が毎ラウンド続き（r1-3 → r2-5・r2-6・r2-7 → r3-1・r3-5）、CHANGELOG の集め方（`assemble._facts`）との共有や組み立てのコミットの記録（`Ndf-Included-Pulls`）にまで広がった。2026-11-22 に conductor が範囲を分けると決め、#3959 から外してこの Issue にした。

## 暫定の扱い（#3959）

この Issue が実装されるまで、#3959 はリリース承認ゲートを `fast`・`auto` でも MVV 判定で省かない（判定は打ち、承認資料と PR のコメントに載る）。#4076 の cross-review の指摘 r9-1（レッドラインの指摘が残る変更が自動で省かれる危険）を開けないためで、代わりにリリース承認ゲートの自動化はこの Issue まで止まる。

## 種類

機能追加

## 今の姿

2026-11-22 の main（`428f91f9e`、NDF 11.0.15）のコードを読んで確かめた。#3959 の実装の後もこの並びは変わらない（#3959 はリリース承認ゲートの保留を読まず、`review_prs` を空にする）。

| 部品 | 今の振る舞い | 場所 |
| --- | --- | --- |
| デプロイの工程 | 判定のステップ `release-mvv` → 承認ゲートのステップ `release-gate` → リリースのプラン `release` の 3 つ。組み立て（`assemble`）と承認資料を作るステップは無く、リリースのプランの worker の作業の中で打つ | `plugins/ndf/contracts/workflow/stages/deploy.json` |
| 判定する PR | 文脈の `pr`。文脈の `pr` を書くのは Pull Request の工程の `pr` ステップだけで、スプリントの PR である（どの戦略でも、判定の時点でマージ済み） | `records/mvv_gate.py` の `pr_number`、`contracts/templates/pr.json` |
| 承認ゲートの質問の PR | 文脈の `pr`。回答を読む前の検証（`recheck`）はこの PR の head と回答が見た head を比べる | `progress/gate/asking.py`、`progress/gate/answering.py` |
| 含む PR の集合（承認資料の「含む PR」） | 組み立てがリリースのコミットへ「含む PR」の行（`Ndf-Included-Pulls`）として書き、リリースの PR の本文の承認資料と同じ `facts.pulls` から作る。組み立てない戦略（`trunk` の `production_pr`・版を持たない `merge_base_branch`）では行が無く、リリース記録は first-parent の件名から決める | `domains/release/assemble.py` の `assemble`、`record.py` の `_included` |
| 「含む PR」の行の選び方 | リリースの PR の head から first-parent で辿り、行を持つ最も新しいコミットを採る。件名は見ず、行を持つコミットが 2 つ以上あっても 1 つ目を採る | `release/record_decide.py` の `_approved_message` |

**判定の時点には、承認資料が確定した集合がどの戦略でも無い。** 組み立ては承認ゲートの後（リリースのプランの中）か工程の外でしか走らないので、判定のステップは組み立ての記録を読めない。判定が読むのはマージ済みのスプリントの PR 1 本で、人が承認するリリースの PR でもない。

## 何を足すか・変えるか

| 項目 | 前 | 後 |
| --- | --- | --- |
| 承認資料を確定する時点 | 承認ゲートの後（リリースのプランの中） | 判定の前 |
| 判定する PR | スプリントの PR（文脈の `pr`） | 承認資料を載せたリリースの PR |
| 保留を読む PR | 無し（#3959 の暫定で空） | リリースの PR・文脈の `pr`・承認資料が確定した「含む PR」の集合 |
| 「含む PR」の行を 1 つに決められないとき | 最も新しい行を採る | 省かない（人へ問う） |
| リリース承認ゲートの省略 | `fast`・`auto` でも省かない（#3959 の暫定） | 保留の検査を経た印を持つ判定・回答だけで省ける |

## 今の利用者への影響と互換

- 普及の段階に入るまで後方互換は求めない（利用者の方針、2026-11-16）。旧版の状態から再開するスプリントは、印の無い判定・回答を採らないので人へ問う（下の受け入れ条件 4）
- 承認済みの MVV を持たないリポジトリでは、判定の出力の形が今と変わらない

## なぜ困るか

範囲が決まらないと、リリース承認ゲートを `fast`・`auto` で省く条件にレビューの保留を足せない。保留を読まずに省くと、版上げの PR の差分に現れない単位の PR に残るレッドラインの指摘を、人が見ないまま本番へ届ける（#4076 の指摘 r9-1・#4093 の指摘 r1-3）。#3959 の暫定は、この危険を閉じる代わりにリリース承認ゲートの自動化を止めている。

## 目的

リリース承認ゲートが、今回のリリースに含まれる PR のレビューの保留を落とさずに読み、保留が無いときだけ `fast`・`auto` で省ける。

## 対象範囲

- 含む: 判定の前に承認資料を確定する工程の並び、判定する PR と保留を読む PR の範囲、「含む PR」の行を 1 つに決める規則、範囲を確定できないときに省かない扱い、リリース承認ゲートの省略を有効にする印、#3959 の暫定を外すこと
- 含まない: 省いた回答を消費の時点で判定した PR・宛先・宛先の先端と照合すること（#4106）、即時修正の判定（#4104）、`trunk` の `production_pr` で本番の PR が `pr-merge` で承認の前に入りうる点（#4128）、cross-review の `mvv-review` の行の書き方（#4076・#4090・#4099）

## 前提

- #3959 の実装（レビューの保留 `review_rows`・`review_holds`・検査 7・`MVV_SKIP_GATES`）が main へ入った後に実装する

## 受け入れ条件

- [ ] 1. リリース承認ゲートの保留の対象が、承認資料が確定した今回のリリースに含まれる PR の集合と一致する（ブランチ戦略の `trunk` の `release_pr`・`production_pr`、`release_branch`、`merge_base_branch` のどれでも）。例: `trunk` の `release_pr` で、版上げの PR の差分に現れない単位の PR に `redline` の行があれば、`fast`・`auto` でも人へ戻る
- [ ] 2. 範囲を確定できない（記録が読めない・組み立てのコミットを 1 つに決められない）ときは省かない
- [ ] 3. #3959 の暫定（リリース承認ゲートを `fast`・`auto` でも省かない）を外す
- [ ] 4. 保留の検査を経た印の無いリリースの判定（`skip: true` でも）と、印の無い判定を添えた `service:mvv` の回答では、リリース承認ゲートを省かずに人の承認待ちになる（#3959 より前の版から直接更新した場合と、#3959 の版で人の承認待ちを挟んでから更新した場合の両方）

## 影響

| 誰が | 何が変わるか |
| --- | --- |
| `fast`・`auto` の利用者 | 保留が無く判定が「従う」なら、リリース承認ゲートが再び省かれる |
| `normal` の利用者 | 判定の記録がスプリントの PR でなくリリースの PR に載り、承認資料の「MVV 判定」の欄に保留が載る |
| リリースのプランの worker | 組み立てを承認ゲートの前に打つ |

## 検証手段

- 単体テスト: PR の CI のテストのジョブで走らせる（`.ndf/project.json` の `test.ci.only` が真なので手元では走らせない）
- 受け入れ条件 1・2 は、判定の関数に組み立てのコミットの一覧（`compare` の形）と `mvv-validation.jsonl` の行を渡す単体テストで確かめる
- 受け入れ条件 4 は、旧版の形の判定の出力と回答の事象をステートマシンへ書いた状態から `decide` と回答の読み口を通すテストで確かめる
- リリースの後、最初の `fast`・`auto` のスプリントのデプロイの工程で、承認資料の「MVV 判定」の欄に保留を読んだ PR の範囲が載ることを確かめる

## 起票者の推定

| 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- |
| 範囲は承認資料と同じ確定した集合から得る（計算し直さない） | #4093 の cross-review の指摘 r2-6・r2-7・r3-1 と conductor の決定（2026-11-22） | 受け入れ条件 1 |
| 範囲を確定できなければ省かない | #4093 の指摘 r3-1 | 受け入れ条件 2 |
| 印の無い判定・回答を採らない（旧版から更新した場合を含む） | #4093 の指摘 r8-3（下の「#3959 から引き継ぐ条件」） | 受け入れ条件 4 |
| 判定の前に承認資料を確定する（工程の並びを変える） | 設計の調査（判定の時点に確定した集合がどの戦略でも無い。「今の姿」） | 受け入れ条件 1 の手段として設計が決める |

## リクエスト（原文）

> MVV 判定は常に実施してください
> normal でも即時修正でも必要です

（書き手: 利用者、日付: 2026-11-21。親の #3959 の原文）

> 普及の段階に入るまで後方互換は要らない

（書き手: 利用者、日付: 2026-11-16。AI の起動指示が引いた方針）

> #3959 の決定 3 の暫定（リリース承認ゲートを #4105 まで fast・auto でも省かない）を外せる設計にする

（書き手: AI の起動指示（conductor、計画 p12 のレーン lcrfast）、日付: 2026-11-22）

## 関連

- #3959（親の課題）
- #4076（`mvv-review` の行を書く cross-review）
- #4106（省いた回答の照合）・#4104（即時修正の判定）・#4128（`production_pr` の本番の PR のマージの順序）

## 移した cross-review の指摘

設計 PR https://github.com/devbasex/ai-plugins/pull/4093 のコミット `19adf3511ffa0492ecd019dcc61cd317096baf7b` の cross-review の記録（https://github.com/devbasex/ai-plugins/pull/4093#issuecomment-7077635480 ）から写した。どれも #3959 の設計 PR では直した扱いだが、この範囲を #3959 から外したので、この Issue の設計で同じ論点を満たす。

| 番号 | 担当 | 重要度 | 場所 | 指摘 | 終わり方 | 見送った理由 |
| --- | --- | --- | --- | --- | --- | --- |
| r1-3 | codex | critical | issues/issue-3959-4055-design-detail.md:129 | リリースの保留対象を判定する PR とその head ブランチへマージされた PR に限定すると、`trunk` の `release_pr` では `release/v…` に単位の PR が入らず、すでに `main` に入った変更のレッドラインを拾えません。ブランチ戦略と今回公開するコミット範囲から対象 PR を特定し、版上げ PR 自体の差分に出ないレッドラインが `fast`・`auto` でも人へ戻ることをテストしてください。 | 直した | — |
| r2-5 | codex | major | issues/issue-3959-4055-design-detail.md:139 | `release_branch` では `_span` が `UNITS` を拒否し、`collect` は選択したスプリントを一時 worktree に組み立てます。提案は `units` に文脈の PR の head ブランチを入れますが、この PR が組み立て済みのリリース PR なら、元のスプリントブランチとは異なります。既存のリリース経路をどの確定済み範囲から読むか定め、複数スプリントを含む `release_branch` で承認資料と同じ保留対象が得られることを確認してください。 | 直した | — |
| r2-6 | codex | major | issues/issue-3959-4055-design-detail.md:137 | 現在の `_facts` は版を上げる単位の起点だけから PR を集め、その結果を単位別の CHANGELOG にも渡します。提案の `included_numbers` は版上げ計画を作らず、返り値も全体の集合だけなので、`_facts` が同じ関数から番号を得ると、対象外の単位の PR が承認資料に入るか、単位別番号を得るために履歴を再走査することになります。確定済みの範囲と対象単位を受け取る収集処理を共有し、起点ごとの結果を再利用できる契約にしてください。 | 直した | — |
| r2-7 | codex | major | issues/issue-3959-4055-design-detail.md:139 | リリース判定で既存の `_span` と `_since_by_unit` をそのまま呼ぶと、追加コストは `git log` に収まりません。たとえば `trunk` は `_judged` が取得した本番ベースを `_span` でも fetch し、タグの取得と照会も行います。`production_pr` は取得済みの PR を `_span` で再読します。取得済みの PR・ベース・タグ情報を共有する境界を決め、共有できない呼び出しを費用と検証に含めてください。 | 直した | — |
| r3-1 | codex | critical | issues/issue-3959-4055-design-detail.md:157 | リリースの保留対象に使う `record.included` は、組み立てが書いたコミットを識別せず、first-parent 上で最後に `Ndf-Included-Pulls` が現れたコミットを採用します。組み立て後の修正コミットに `Ndf-Included-Pulls: none` があると、承認資料に含まれる PR のレッドライン保留を落とし、`fast`・`auto` で人の承認を省けます。承認資料を組み立てた時点の PR 集合を信頼できる記録に固定し、判定時に照合できなければ承認を省かないようにしてください。 | 直した | — |
| r3-5 | codex | major | issues/issue-3959-4055-design-detail.md:84 | 差分取得後の検査が head だけを比較するため、head を変えずに PR の宛先が変わると、旧宛先の MVV と新宛先の差分を合わせて判定できます。リリースの保留対象も `compare_commits` に可変のブランチ名を渡すため、ベースが進むと MVV と異なる範囲を読み、ページ間でも範囲がずれ得ます。差分の照合に宛先とベースの変更を含め、コミット一覧にも確定した `base_sha` を渡してください。 | 直した | — |

## 移した設計（設計 PR https://github.com/devbasex/ai-plugins/pull/4093 のコミット `19adf3511ffa0492ecd019dcc61cd317096baf7b` の詳細設計から）

#3959 の詳細設計から外し、ここへ移した。設計の起点として使う。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/release/record.py` | 変更 | リリース記録 | `record_decide`・`included_decide` | モジュール | `assembled_included`（新設。判定が使う。リリース記録の `_included` は変えない） |
| `src/ndf/domains/release/record_decide.py` | 変更 | リリース記録の純粋な判定 | — | モジュール | `assembled_message`（新設） |

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `assembled_message` | `release/record_decide.py` | 新設 | `assembled_message(commits: Sequence[Mapping[str, Any]], head: str) -> Optional[str]` | `head` から first-parent で辿った PR のコミットのうち、「含む PR」の行（`INCLUDED_TRAILER`）を持つものが 1 つだけで、その件名が `included_decide.RELEASE_TITLE` の形（組み立てが書く件名）ならそのメッセージ。行を持つコミットが無ければ None。2 つ以上ある・1 つでも件名が組み立ての形でなければ `ValueError`（組み立てが書いたコミットを決められない） |
| `assembled_included` | `release/record.py` | 新設 | `assembled_included(commits: Sequence[dict], pull: Pull, production: str, formats: NameFormats) -> list[int]` | `assembled_message` が返したメッセージの「含む PR」の番号。None なら（組み立てない設定）first-parent の件名から決める（`_included` と同じ規則）。`ValueError` は `RecordError`（「組み立てのコミットの『含む PR』を 1 つに決められない」）にして投げる。リリース記録の `_included`（最も新しい行を読む）は変えない |

- 接ぎ目（`mvv_gate._judged` の手順 (5)。`review_prs`）: `prs` は `design`・`fix` では判定する PR の 1 件。`release` では文脈の `pr` と、承認資料が確定した今回のリリースに含む PR の和である。含む PR は範囲を計算し直さず、確定済みの記録から読む: (1) `branch_strategy.load_at_ref(root, base_sha)`（MVV と同じベースのコミット）から本番ブランチと `names_decide.formats_of(strategy.section["release"])` を得る。(2) `hub.compare_commits(base_sha, pull.head_sha)` で判定する PR のコミットを、MVV を読んだベースの SHA と (1) の手順で読んだ head の 2 つの固定した SHA の間で読む（可変のブランチ名を渡さない。ベースが進んでも、ページ送りの途中でも範囲が MVV と同じまま）。(3) `record.assembled_included(commits, pull, production, formats)` で番号を得る。組み立てのリリースの PR（`merge_base_branch` の版を持つ設定・`release_branch`・`trunk` の `release_pr`）では、`assemble` が承認資料の `facts.pulls`（`_facts` が単位ごとの起点から集めた番号の和）を、件名が `RELEASE_TITLE` のリリースのコミット 1 つにだけ「含む PR」の行として書くので、承認資料と同じ集合になる。組み立ての後の直しのコミットが同じ行（`Ndf-Included-Pulls: none` など）を持つと、組み立てのコミットを 1 つに決められず `RecordError` になり、下の (5) の行（`verdict: null`・`skip` 偽）で人へ問う（後から足した行で承認資料の PR の保留を落とさない。照合できなければ省かない）。`release_branch` で複数のスプリントを集めた PR も、組み立てた一時の作業場所の範囲の番号がこの行に入るので、元のスプリントブランチを読み直さない。組み立てない設定（`trunk` の `production_pr`・版を持たない `merge_base_branch`）では行が無く、リリース記録と同じく first-parent の件名から決め、文脈の `pr` を必ず足す。`assemble.py` の `_facts`・`_span`・`_since_by_unit` は変えず呼ばない（本番ブランチ・タグの fetch と照会、PR の読み直しを足さない）。追加の費用は `release` で `compare` の一覧の読み取り（`github.list-pages` ページまで）だけで、`design`・`fix` では API を呼ばない。読めない（`GitHubError`・`StrategyError`・`JsonFileError`）・組み立てのコミットを決められない（`RecordError`）ときは上の表の (5) の行（`verdict: null`）。`mvv` は `snap` の（`version` の文字列・`sha256`）。`repo` は `mvv.repo_key(root)`。`review_rows` が `OSError` か `UnicodeDecodeError`（`jsonl.read_rows` が不正なバイトで投げる。`OSError` の子でない）を投げたら、保留を「MVV の記録（mvv-validation.jsonl）を読めない: <原因>」の 1 件にする（#3959 の本文の異常系の `review_rows` の行の検出は、この 2 つの例外と読む）。結果を `MvvFacts.review_holds` に入れ、`gate_outcome` の `holds` にも同じ値を渡す（承認資料と PR のコメントは同じ結果を読む）

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `review_prs`（`release`） | 組み立てのリリースの PR の「含む PR」の行の番号と文脈の `pr` の和。`compare_commits` に渡すのは `base_sha` と head の SHA | `release_branch` で 2 つのスプリントを集めた PR → 承認資料（`collect` の `facts.pulls`）と同じ集合。行を持たない `production_pr` → first-parent の件名の番号と文脈の `pr`。組み立ての後に行の無い直しのコミットを積む → 組み立てのコミットの行を読む | `compare` の `GitHubError` → (5) の行。組み立ての後の直しのコミットに `Ndf-Included-Pulls: none` → `RecordError` で (5) の行、`fast` でも `skip` 偽 |
| `_judged`（`release` の保留） | `trunk` の `release_pr` で、版上げの PR の差分に現れない単位の PR に `redline` の行 → `fast`・`auto` で「従う」でも `skip` は偽、`holds` にその PR | `assemble` の `_span`・`_since_by_unit` を呼ばない（fetch とタグの照会の回数が増えない） | `review_prs` の失敗 → `verdict: null` |


## #3959 から引き継ぐ条件: 省略を有効にするのは新しい検査を経た判定と回答だけ

#3959 は省いてよい区分の表 `MVV_SKIP_GATES`（`progress/model.py`）を `("design",)` にし、表に無い区分では `mvv_skip` が省かず、`mvv_answer_void` が `service:mvv` の回答を採らない（[#4093](https://github.com/devbasex/ai-plugins/pull/4093) の詳細設計 `issues/issue-3959-4055-design-detail.md` の「リリース承認ゲートの省略の止め」）。この Issue で表に `release` を足すだけでは、人の回答を待ったままの実行をこの版で再開したとき、旧版の `release-mvv` の `skip: true` と未消費の `service:mvv` の回答が、この Issue の保留の検査を経ずに再び有効になる（#4093 の cross-review の指摘 r8-3）。

- リリース承認ゲートの省略を有効にする条件に「判定の出力が、この Issue の保留の検査を経た印（保留を読んだ PR の範囲などのキー）を持つ」を加え、`mvv_skip` と `mvv_answer_void` の両方で、印の無い判定・回答（`service:mvv` の回答の payload の `mvv_gate` に印が無い）を採らない
- テスト: #3959 より前の版からこの Issue の版へ直接更新する場合と、#3959 の版で人の承認待ちを挟んでから更新する場合の両方で、リリース承認ゲートが人の承認待ちになる



