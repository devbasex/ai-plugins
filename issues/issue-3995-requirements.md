# #3995: 文脈の切り替えの閾値を 250k（タイミングを見て再起動）と 500k（速やかに再起動）の 2 段に揃え、置き場を 1 つにする

正は課題の本文（#3995）で、この文書はその写しである。本文から作り直す。手で直さない。

2026-11-21 に利用者が値を決めた: **250,000 token で「タイミングを見て再起動」、500,000 token で「速やかに再起動」に揃える。** 対象はオーガナイザーとレーンの conductor の両方である。

| 段 | 値 | 振る舞い |
| --- | --- | --- |
| タイミングを見て再起動 | 250,000 | 次のカットポイントで引継ぎ文書を書いて切る。工程の途中の Tool は止めない |
| 速やかに再起動 | 500,000 | カットポイントを待たずに、次の Tool の呼び出しを文脈の量の guard（`context-limit`）が止め、引継ぎ文書を書いて切らせる。中継のラッパーもこの値で子に引継ぎを求める |

## 何が起きたか

2026-11-21、オーガナイザーのセッションで「目的の曖昧な設定値・上限値」を洗い出した（利用者の指示の調査）。文脈の量で会話を切り替える閾値が 3 か所にあり、手順書が約束する値（200,000）と、実際に Tool を止める値（800,000）が食い違っていた。手順書が書く止める口（`NDF_CONTEXT_GUARD=0`）には読み手が無かった。同じ日に利用者は、閾値を 250,000 と 500,000 の 2 段にし、オーガナイザーとレーンの conductor の両方に効かせると決めた。

## 種類

機能追加（2 段の閾値・250,000 を超えたことの知らせ・役ごとの切りどころは、今の SSOT（`context-window.md`・`limits.json`）に無い約束である。手順書とコードの食い違いの解消は、新しい約束へそろえる中で行う）

## 今の姿

文脈の量で会話を切り替える仕組みは、長い文脈のまま後の工程の判断が悪くなるのを避けるためにある（`plugins/ndf/skills/development-workflow/references/context-window.md` の「会話をまたいで続ける」）。閾値は 3 か所にあり、読み手がそれぞれ違う（main の `759d2a385` で確かめた）。

| 置き場 | 値 | 読み手 |
| --- | --- | --- |
| 上限の設定 `hook.context-limit`（`plugins/ndf/contracts/limits.json:61`） | 800,000 | 文脈の量の guard（`plugins/ndf/src/ndf/domains/hooks/guards/context_limit.py:28`）。**実際に Tool を止めるのはこれだけ**。値以上なら、引継ぎ文書の Write/Edit を除くすべての Tool の呼び出しを止める |
| 上限の設定 `relay.context`（`plugins/ndf/contracts/limits.json:148`） | 800,000 | 中継のラッパー（`plugins/ndf/src/ndf/domains/relay/cli.py:287` → `switch_decide.py:115`）。値以上で子が静かになったら、1 セグメントに 1 度、子に引継ぎを求める |
| 再起動の方針（`plugins/ndf/src/ndf/domains/relay/context_policy.py:17-19`・`:125`。環境変数 `NDF_CONTEXT_LIMIT`・`NDF_CONTEXT_POLICY`、設定 `.ndf/context.json`） | 現行方式 200,000、試行方式は判断開始の値 200,000・運用上限 400,000 | `ndf relay context resolve` の表示（`cli_context.py:29`）と判定の記録（`cli_context.py:45`）だけ。`context_policy` を import するのは relay の 3 ファイル（`cli_context.py`・`context_records.py`・`context_report.py`）で、guard もラッパーも読まない |

手順書と契約の記述はコードと次の点で食い違う。

- `context-window.md:128-129` は「上限の既定は 200,000。`NDF_CONTEXT_LIMIT` で変えられ、`NDF_CONTEXT_GUARD=0` で止められる」と書く。`:160` も運用上限を 200,000 と書く。`relay.md:360`・`glossary.md:72` も既定を 200,000 と書く。`statusline` の `SKILL.md:30` と `context-window.md:238` は hook の止める上限を 200K と書く
- `NDF_CONTEXT_GUARD` の読み手は無い（`git grep -n NDF_CONTEXT_GUARD -- plugins/ndf/src plugins/ndf/contracts` は 0 件）
- 設定の形 `plugins/ndf/contracts/schemas/ndf-settings/context.schema.json` は読み手を「ndf hooks」と書く（`x-ndf-read-by`）が、hook は `.ndf/context.json` を読まない
- `context-window.md` の「上限を超えたら hook が止める」は、止めるのは工程へ入る起動（工程 Skill・フェーズの Agent）だけで、ラッパーの外では同じ起動の 2 度目を 1 度だけ通すと書く。guard は Tool の種類を問わず止め（`plugins/ndf/contracts/hooks/guards.json` の `context-limit` の `tools` は空）、引き合いの一覧 `scripts/lib/token-guard-stages.txt` はリポジトリに無い

役ごとの今の切り方は次の 2 つの手順書にある。

- レーンの conductor: `context-window.md` の 4 つのカットポイント（ドキュメントレビューのマージの後・リファクタリングとコードレビューの前後・スプリントの Pull Request を出した後・マージのフェーズを終えた後）で、引継ぎ文書を書いて `ndf-next` を出す
- オーガナイザー: `development-workflow` の `references/parallel.md` の「セッションの切り替え」（引継ぎ文書 `.ndf/handoff/parallel-<計画>.md` の「次に実行するコマンド」と「現在地」を書く → 背景の `ndf lanes wait` を止める → `ndf-next`）。背景の `wait` が残るとラッパーが切り替えられない（同節の「2 を飛ばすと切り替わらない」）

## 何を足すか・変えるか

| | 今 | 足した後 |
| --- | --- | --- |
| 閾値の置き場 | 3 か所（`hook.context-limit`・`relay.context`・再起動の方針） | 1 か所。guard・中継のラッパー・`ndf relay context resolve` が同じ 2 つの値を読む |
| 値 | 800,000（guard とラッパー）と 200,000 / 400,000（方針） | 250,000（タイミングを見て）と 500,000（速やかに） |
| 250,000 を超えたとき | 何も起きない | conductor に超えたことが知らされ、次のカットポイントで切る。工程の途中の Tool は止めない |
| 500,000 を超えたとき | 何も起きない（800,000 まで止めない） | guard が次の Tool の呼び出しを止め、ラッパーが子に引継ぎを求める |
| オーガナイザー | 切りどころの値の決まりが無い | レーンの conductor と同じ 2 つの値で切る。止める理由の文に背景の `wait` を止める手順を含む |
| 変える口 | `NDF_CONTEXT_LIMIT`・`.ndf/context.json` は表示と記録にしか効かない。`NDF_CONTEXT_GUARD` は読み手が無い | 残す口は guard とラッパーの両方に効く。効かない口は消す |

## 事象

```console
$ git grep -n "context-limit\|relay.context\|NDF_CONTEXT_LIMIT\|NDF_CONTEXT_GUARD\|decide_from" -- plugins/ndf/src plugins/ndf/contracts
plugins/ndf/contracts/limits.json:61:    "hook.context-limit": {... "count": 800000},
plugins/ndf/contracts/limits.json:148:    "relay.context": {... "count": 800000},
plugins/ndf/src/ndf/domains/hooks/guards/context_limit.py:28:    limit = int(tables.policy_value("hook.context-limit", "count"))
plugins/ndf/src/ndf/domains/relay/cli.py:287:        context_limit=int(limits.value("relay.context", "count")),
plugins/ndf/src/ndf/domains/relay/context_policy.py:125:    raw = environ.get("NDF_CONTEXT_LIMIT")
...（NDF_CONTEXT_GUARD の行は 0 件）
```

guard は 800,000 だけを読み、手順書の 200,000 と `NDF_CONTEXT_GUARD` に当たる読み手は無い。

## 期待する結果

閾値は 250,000 と 500,000 の 2 つで 1 か所にだけ置かれ、guard・中継のラッパー・`ndf relay context resolve` が同じ値を読む。手順書の値と口がコードと一致する。

## 再現手順

1. `git grep -n "context-limit\|relay.context\|NDF_CONTEXT_LIMIT\|NDF_CONTEXT_GUARD" -- plugins/ndf/src plugins/ndf/contracts`
2. guard が `hook.context-limit`（800,000）だけを読み、手順書の 200,000 と `NDF_CONTEXT_GUARD` に当たる読み手が無いことを見る

## 環境

NDF 11.0.13（main の `759d2a385`）・Linux

## なぜ困るか

conductor は手順書から 20 万 token で切ると読むが、hook は 80 万 token まで止めない。長い文脈のまま後の工程の判断が悪くなる。閾値を変えようとした利用者の設定（`NDF_CONTEXT_LIMIT`・`.ndf/context.json`）が guard にもラッパーにも効かない。止める口として書かれた `NDF_CONTEXT_GUARD` を打っても何も変わらない。

## 目的

- 文脈の切り替えの閾値を 250,000 と 500,000 の 2 段にし、オーガナイザーとレーンの conductor の両方で、手順書どおりの値で切り替わる状態にする
- 閾値を 1 か所に置き、値を変えると guard・ラッパー・表示のすべてに効く状態にする
- 手順書の値と口がコードと一致し、読み手の無い口が残らない状態にする

## 対象範囲

含む:
- 閾値の置き場を 1 つにすること（上限の設定 `hook.context-limit`・`relay.context`、再起動の方針の値と設定の形 `context.schema.json`、環境変数の契約 `env.json`）
- 文脈の量の guard・中継のラッパー・`ndf relay context resolve` が同じ 2 つの値を読むこと
- 250,000 を超えたことを conductor に知らせる仕組み（届け方は設計で決める）
- guard の止める理由の文に、オーガナイザーが背景の `ndf lanes wait` を止める手順を含めること
- 現行方式と試行方式の 2 つの方針を残すかの決定と、残すときの既定の値（未決 1）
- `NDF_CONTEXT_GUARD` を作るか手順書から消すかの決定と、その反映（未決 2）
- 手順書の直し: `context-window.md`（役ごとの切りどころ・2 つの値・口・「遅くとも 20 万」の目安・hook の止める範囲の記述）、`parallel.md` の「セッションの切り替え」、`relay.md`・`glossary.md`・`development-workflow` の `SKILL.md`・`statusline` の `SKILL.md` の閾値の記述、`plugins/ndf/docs/contracts/ndf-settings.md` の例
- 1〜9 を確かめるテスト（文脈の量と回数で確かめる。時間で区切らない）

含まない:
- 切る閾値を実測で見直すこと（#2908。今後の見直しの材料にする）
- statusline の色の閾値（500k で赤・窓 200K 以下は 150k）。窓に対する表示の警告で、切り替えの閾値とは別の意味である。説明の文の「hook の止める上限」の値だけを直す
- 試行方式の費用の見込みで決める判断帯と、窓の容量で止める帯を guard に実装すること（今の guard は持たない。方針を残すかとあわせて未決 1 で扱う）
- supervisor のスイッチポイント（`NDF_SUPERVISOR_CUT_RATIO`・`NDF_SUPERVISOR_CUT_GUARD`）。`context-window.md` は guard `context-limit` が決めると書くが、`plugins/ndf/src`・`plugins/ndf/contracts` に読み手が無い（`git grep -n "NDF_SUPERVISOR_CUT\|supervisor-cut" -- plugins/ndf/src plugins/ndf/contracts` は 0 件）。別の課題にする
- statusline が読む適用値のファイル（`sessions/<セッション>.applied.json`）の書き手が `plugins/ndf/src` に無いこと。別の課題にする
- Codex の hook（`plugins/ndf/contracts/hooks/runtimes.json:84` のとおり Codex では guard `context-limit` を置かない）。Codex は手順書の規約だけで守る
- 確定仕様（`docs/specifications/ndf-context-restart-policy.md`・`ndf-token-waits-and-context-cut.md`）の書き換え。確定仕様化の工程で直す
- 11 系に無い設定への参照の全体の洗い出し（#3857）

## 前提

- 前提 1: 閾値の値ちょうどは「超えた」側に数える。今の guard（`tokens < limit` を通す）とラッパー（`>=`）の比べ方と同じ
- 前提 2: 文脈の量は、今の guard と同じく会話の記録の最後の応答の `usage` から読み、読めなければ止めない・知らせない
- 前提 3: 速やかに再起動の値を口で 250,000 より下げたときは、タイミングを見て再起動の値も同じ値へ下げる（今の `context_policy._thresholds` の「判断開始の値が運用上限を上回れば同じ値へ下げる」と同じ規則）
- 前提 4: サブエージェントの中の呼び出しは止めない・知らせない（今の手順書の「見るのは conductor だけである」）
- 前提 5: NDF は普及のフェーズの前で、上限の設定のキー・環境変数・設定の形を変えても互換の経路を持たない（MVV Value 4）

## 受け入れ条件

- [ ] 1. 文脈の切り替えの 2 つの値（250,000 と 500,000）が `plugins/ndf/src`・`plugins/ndf/contracts` の 1 か所にだけ定義されている。その 1 か所の値を変えると、guard・中継のラッパー・`ndf relay context resolve` の 3 つが変えた値を使う（テストで確かめる）
- [ ] 2. `limits.json` から 800,000 の `hook.context-limit`・`relay.context` が無くなり、`context_policy.py` の 200,000・400,000 の既定と `context.schema.json` の 200,000・400,000 の既定と例が無くなる（`git grep -n -E "800000|800_000|200_000|400_000" -- plugins/ndf/src plugins/ndf/contracts` に文脈の切り替えの閾値の行が無い）
- [ ] 3. 設定も環境変数も無いとき、`ndf relay context resolve` が判断開始の値 250,000・運用上限 500,000 を返す。2 つの方針を残すなら、`--policy current` と `--policy trial` のどちらでもそうなる
- [ ] 4. 文脈の量が 250,000 以上 500,000 未満のとき、guard は conductor の Tool の呼び出しを止めない（deny しない）
- [ ] 5. 文脈の量が 250,000 以上になったとき、conductor にタイミングを見て再起動の値を超えたことを知らせる文が届く。249,999 では届かない（届け方と回数は設計で決める）
- [ ] 6. 文脈の量が 500,000 以上のとき、guard は引継ぎ文書の Write/Edit を除く次の Tool の呼び出しを止める。499,999 では止めない。止める理由の文は、中継の下では次のセグメントのブロックを書くこと、外では引継ぎ文書を書くことを示す（今と同じ）
- [ ] 7. 中継のラッパーは、子の文脈の量が 500,000 以上になって子が静かになったとき、子に引継ぎを求める。499,999 では求めない
- [ ] 8. オーガナイザーの会話で guard が止めたとき、止める理由の文が、背景の `ndf lanes wait` を止めてから引継ぎ文書（`.ndf/handoff/parallel-<計画>.md`）を書いて切る手順を含む
- [ ] 9. 速やかに再起動の値を口（`NDF_CONTEXT_LIMIT` と、残すなら `.ndf/context.json`）で変えると、guard・ラッパー・`ndf relay context resolve` の 3 つが同じ値を使う（例: `NDF_CONTEXT_LIMIT=300000` で guard は 300,000 以上で止め、ラッパーは 300,000 以上で引継ぎを求め、`resolve` は運用上限 300,000 を返す）
- [ ] 10. 手順書・契約（`context-window.md`・`relay.md`・`glossary.md`・`env.json`・`context.schema.json`・`ndf-settings.md`）が挙げる閾値の口は、どれも 9 のとおり 3 つに効くか、手順書・契約から消えている。`context.schema.json` の読み手（`x-ndf-read-by`）が実際の読み手と一致する
- [ ] 11. `NDF_CONTEXT_GUARD` は、作るなら `NDF_CONTEXT_GUARD=0` で guard が判定も知らせもしない（テストで確かめる）。作らないなら `git grep -n NDF_CONTEXT_GUARD -- plugins/ndf` が 0 件になる
- [ ] 12. `context-window.md` が、2 つの値（250,000・500,000）と振る舞い、レーンの conductor の切りどころ（4 つのカットポイント）、オーガナイザーの切りどころ（知らせを処理し終えて次の `wait` を張る前と、利用者への報告を終えた時点）、guard が止める範囲（引継ぎ文書の Write/Edit を除くすべての Tool）を書き、コードと一致する。存在しない `scripts/lib/token-guard-stages.txt` を引かない（設計レビュー・ドキュメントレビューで確かめる）
- [ ] 13. `parallel.md` の「セッションの切り替え」が、オーガナイザーの 2 つの値と切りどころを書き、背景の `wait` を止めてから切る手順と、切った後の新しい会話が引継ぎ文書から見張りを続けることを書く（ドキュメントレビューで確かめる）
- [ ] 14. `relay.md`・`glossary.md`・`development-workflow` の `SKILL.md`・`statusline` の `SKILL.md`・`context-window.md:238` の閾値の値が 250,000 / 500,000 にそろう（`git grep -n -E "200,000|200K|20 万" -- plugins/ndf/skills` に文脈の切り替えの閾値を指す行が無い）
- [ ] 15. 退行しない: サブエージェントの中の Tool の呼び出しは、文脈の量が 500,000 以上でも guard が止めない。文脈の量が読めないときも止めない
- [ ] 16. 4〜9・11・15 を、文脈の量の境界の値（249,999 / 250,000 / 499,999 / 500,000）と回数で確かめるテスト（時間で区切らない）がある

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。上限の設定のキー（`hook.context-limit`・`relay.context`）の置き場と値、`NDF_CONTEXT_LIMIT` の効く先、`.ndf/context.json` の形（方針を残すかによる）、`ndf relay context resolve` の既定の値、`NDF_CONTEXT_GUARD` の有無。互換なし（前提 5） |
| データ | 判定の記録（`ndf relay context record` の行）の形は変えない。手元の `.ndf/context.json` に旧い値があれば、方針の決定（未決 1）に従って読む |
| 既存の振る舞い | guard が止める値が 800,000 から 500,000 に下がり、今より早く止まる。ラッパーが引継ぎを求める値も 500,000 に下がる。250,000 を超えると知らせが届くようになる。今 200,000 と読んで切っていた conductor は 250,000 で切る |

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 文脈の量が 250,000 を超えた | conductor の応答（会話の記録の `usage`） | 文脈の量が読めなければ知らせない（前提 2） | — |
| E2 | conductor に超えたことを知らせた | E1 の後の Tool の呼び出し（届け方は設計） | 知らせが届かなければ E5 の guard が止める | E1 |
| E3 | conductor が切りどころに着いた | レーン: 4 つのカットポイント。オーガナイザー: 知らせを処理し終えて次の `wait` を張る前か、利用者への報告を終えた時点 | 切りどころに着く前に E5 に当たる | E2 |
| E4 | 引継ぎ文書を書き、オーガナイザーは背景の `wait` を止めた | E3 か E6 | `wait` が止まらなければラッパーが切り替えられない（`parallel.md`） | E3 か E6 |
| E5 | 文脈の量が 500,000 を超えた | conductor の応答 | 文脈の量が読めなければ止めない | — |
| E6 | guard が次の Tool の呼び出しを止めた | E5 の後の Tool の呼び出し | 引継ぎ文書の Write/Edit は通す | E5 |
| E7 | 中継のラッパーが子に引継ぎを求めた | E5 の後に子が静かになった | 1 セグメントに 1 度だけ求める（今と同じ） | E5 |
| E8 | `ndf-next` を出して会話を切り、新しい会話が引継ぎ文書から続けた | E4 の後の応答の終わり | 新しい会話は引継ぎ文書から状態を確かめ直す | E4 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/tests/domains/hooks plugins/ndf/tests/domains/relay -q -n 4`（CI テストの設定があれば PR の CI の結果で決める） |
| 静的解析・型検査 | CI の既存のジョブ（PR の CI の結果で決める） |
| 手動確認 | リリースの後、オーガナイザーとレーンの conductor のセッションで、250,000 を超えたときに知らせが届き、500,000 を超えたときに guard が止めることを、`ndf relay context report` と会話の記録で利用者が見る |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 現行方式と試行方式の 2 つの方針を残すか。残すなら、どちらの既定も 250,000 / 500,000 にし、`.ndf/context.json` と `NDF_CONTEXT_POLICY` を guard とラッパーに効かせる。残さないなら、設定の形と口を消す | 設計（設計承認ゲートで利用者が確かめる） | 設計承認ゲート |
| 2. `NDF_CONTEXT_GUARD` を作るか、手順書から消すか | 設計（同上） | 設計承認ゲート |
| 3. 250,000 を超えたことを conductor に知らせる届け方と回数（guard が止めずに文を足す、statusline の色、ほか） | 設計 | 設計承認ゲート |
| 4. guard がオーガナイザーの会話を見分ける方法（受け入れ条件 8） | 設計 | 設計承認ゲート |
| 5. 1 か所の置き場をどこにするか（上限の設定 `limits.json`・再起動の方針のどちらか、ほか） | 設計 | 設計承認ゲート |

## 起票者の推定

題・本文のうち、利用者の決定（250,000 と 500,000 の 2 段の値と振る舞い、対象がオーガナイザーとレーンの conductor の両方）に無く、起票者（オーガナイザー）が書き足した条件。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 閾値を 1 か所にだけ置き、guard・ラッパー・`resolve` が同じ値を読む。800,000 と 200,000・400,000 は無くなる | 題の「置き場を 1 つにする」と期待する結果 1（設定値の洗い出しの調査） | 受け入れ条件 1・2・3。置き場は未決 5 |
| S2 | 250,000 を超えたことを知らせる仕組みを置く | 期待する結果 2 | 受け入れ条件 5。届け方は未決 3 |
| S3 | オーガナイザーの切りどころは、知らせを処理し終えて次の `wait` を張る前と、利用者への報告を終えた時点 | 本文の対象の表 | 受け入れ条件 12・13 |
| S4 | guard の止める理由の文に、背景の `wait` を止める手順を含める | 本文の対象の表（`parallel.md` の「2 を飛ばすと切り替わらない」） | 受け入れ条件 8。見分け方は未決 4 |
| S5 | `NDF_CONTEXT_LIMIT`（と方針の設定）を変えると guard とラッパーの両方に効く。効かない口は消す | 期待する結果 7 | 受け入れ条件 9・10 |
| S6 | 2 つの方針を残すかは設計で決める。残すなら既定を 2 つの値にする | 期待する結果 6 | 受け入れ条件 3・未決 1 |
| S7 | `NDF_CONTEXT_GUARD` を作るか手順書から消すかを決める | 期待する結果 8 | 受け入れ条件 11・未決 2 |
| S8 | 手順書の値と口がコードと一致する | 期待する結果 5・9 | 受け入れ条件 12・13・14。hook の止める範囲の記述の食い違い（工程の起動だけと書くが、すべての Tool を止める）もここで直す |

## リクエスト（原文）

> 2026-11-21 に利用者が値を決めた: **250,000 token で「タイミングを見て再起動」、500,000 token で「速やかに再起動」に揃える。**

（書き手: 起票者（オーガナイザー）が利用者の決定を写した文、日付: 2026-11-21。課題の本文の冒頭）

> **対象はオーガナイザーとレーンの conductor の両方である**（2026-11-21 の利用者の指示）。

（書き手: 起票者（オーガナイザー）が利用者の指示を写した文、日付: 2026-11-21。課題の本文の冒頭）

## 由来

2026-11-21、オーガナイザーのセッションで「目的の曖昧な設定値・上限値」を洗い出した調査（利用者の指示）で見つけた。関連: #2908（切る閾値を実測で決める調査）、#3857（11 系の手順書と契約に 11 系に無い設定への参照が残る）。
