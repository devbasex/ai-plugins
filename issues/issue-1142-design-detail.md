# #1142 の詳細設計: 主題をまたぐ部品・実装計画・開いている bug の割り当て

設計文書 [issue-1142-design.md](issue-1142-design.md) の 3 つの主題と前の企画の残りを、子 Issue と流す順序へ下ろした文書である。書き方は `plugins/ndf/skills/design/references/detailed-design.md` にある。要求は [issue-1142-requirements.md](issue-1142-requirements.md) にある。

**この文書は主題をまたぐものだけを書く。** 主題の中のファイルと関数は、主題の詳細設計が持つ。

| 主題 | 詳細設計 | 子 Issue |
| --- | --- | --- |
| smells | [issue-1142-design-smells-design-detail.md](issue-1142-design-smells-design-detail.md) | A0・A1a・A1b・A2・A3a・A3b・A3c-1・A3c-2・A3d・A3e-1・A3e-2・A3f-1・A3f-2・A3g |
| data | [issue-1142-design-data-design-detail.md](issue-1142-design-data-design-detail.md) | DC1〜DC8（設計文書の移行ステップ DT1〜DT8 との対応は data の詳細設計の冒頭の表） |
| scripted-flow | [issue-1142-design-scripted-flow-design-detail.md](issue-1142-design-scripted-flow-design-detail.md) | SF1a・SF1b・SF2〜SF13（SF3 は既存の #3153） |
| 前の企画の残り | [issue-1142-design-base-design-detail.md](issue-1142-design-base-design-detail.md) | K1〜K3・W1〜W8（旧 K4 は A1a にまとめた） |

子 Issue は 47 本で、14 の波で流す。開いている bug 110 件のうち、子 Issue の変更で直る見込みがあるのは 4 件である。

**この文書で決めたこと（conductor の決定を写したもの）。**

- 旧 K4（撤去）は A1a にまとめる。どちらも `legacy_names.py`・`phase-steps.py`・`bundle-close.py` を消すため。K4 の残り（`post_queue.py` の取り込み・契約の 3 項目・構造チェックの除外）も A1a が持つ
- W1 の `lib/report_fields.py` と data の R3 は食い違わない。R3 は事実の表（`contracts/facts/`）へ移さないという意味で、W1 は 1 つのコンテキストだけが読む語を持ち主のモジュールに置く
- #3191（#3092 の実装）が工程の失敗を終了コード 6 で返す点は、#1142 の IS11（終了コードは止まった理由の種類から引く）を正とし、SF8 で合わせる
- リファクタリング計画の 8 件の扱いは利用者の判断待ちである（設計文書の「人へ残す判断」の (d)）。下の表と波は、8 件を先に要るものから外した形で組んだ。推奨（8 件を子 Issue に取り込み、別の設計 PR で設計する）を採ると、下の「リファクタリング計画の 8 件」の節のとおり、8 件に依る子 Issue が後ろへ移る
- #3191 が要る DC7・SF8 は、先に要るものに #3191 を書き、後ろの波へ置く

## ファイル配置

主題をまたいで触るファイル（2 つ以上の主題の子 Issue が触るもの）のうち、順序の決め手になるものだけを挙げる。すべての組は「実装計画」の流す順序の確かめが設計の結果から引く。

```text
plugins/ndf/
├── contracts/
│   ├── index.md                 （生成物。DC2〜DC8・SF1a）
│   ├── limits.json              （SF1a・DC5）
│   └── workflow/stages/         （DC7。#3191 が新設）
└── scripts/
    ├── lib/{README.md, step_result.py, workflow_def.py, legacy_names.py, durable.py, post_queue.py, transcript_agents.py, report_fields.py, deps.py}
    ├── supervise_lib/{state.py, queue.py, verify_steps.py, delivery_templates.py}
    ├── hook_lib/token_guard.py
    └── {supervise.py, sprint-state.py, mvv-gate.py, check-trigger.py}
scripts/{check-ndf-contracts.py, contracts_lib/ratchet.py}
.github/workflows/script-structure.yml
pyproject.toml・plugins/ndf/pyproject.toml
```

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/contracts/index.md` | 変更 | 契約の逆引きの生成物。DC2 が節「事実の表」、SF1a が「止まった理由の表」を足し、DC3〜DC8 が書き直す | `scripts/check-ndf-contracts.py` |
| `plugins/ndf/contracts/limits.json` | 変更 | SF1a が `cause.*` の再試行の方針を、DC5 が `output.tail-lines` ほかの閾値を足す。キーは重ならない | 無し |
| `plugins/ndf/scripts/lib/README.md` | 変更 | 結果 JSON の契約。SF1a・SF1b・SF13 が `cause`・`routes`・止まった理由の表の節を、A1a・A1b が旧名と古い値の節の削除を書く | 無し |
| `plugins/ndf/scripts/lib/step_result.py` | 変更 | SF1b が `cause` を必須に、SF13 が `routes` を必須にし、A3c-2 が握りつぶしを直し、W1 が廃止語を替える | `plugins/ndf/scripts/lib/causes.py` |
| `plugins/ndf/scripts/lib/workflow_def.py` | 変更 | #3191 が新設する。DC7 が語彙 `VOCABULARY` に `phase`、SF8 が `on_cause` を足す | 無し |
| `plugins/ndf/scripts/lib/legacy_names.py` | 削除 | A1a が消す。data の詳細設計の DC7 の行（`BRANCH_PREFIXES`）は A1a の後には当たらない | — |
| `plugins/ndf/scripts/lib/durable.py` | 変更 | SF2 が `sleep` を、DC8 が `RUNNING`・`FINISHED` の公開を足し、W1 が廃止語を替える | `dbos` |
| `plugins/ndf/scripts/lib/post_queue.py` | 変更 | A1a（旧 K4）が移行の前のファイルの取り込みを、DC5 が `BODY_MATCH_CHARS` を、W2 が廃止語を直す | `plugins/ndf/scripts/lib/durable.py` |
| `plugins/ndf/scripts/lib/transcript_agents.py` | 変更 | A1b・SF12・DC7・A3c-2・W3 が順に触る（古い値・中断の点検・工程のフェーズ・握りつぶし・語） | `plugins/ndf/scripts/lib/workflow_def.py` |
| `plugins/ndf/scripts/lib/report_fields.py` | 変更 | W1 がフェーズの結果の語の持ち主にする（1 つのコンテキストだけが読む語。data の R3 の「表へ移さない」と同じ向き） | 無し |
| `plugins/ndf/scripts/supervise_lib/state.py` | 変更 | A1a・SF1b・SF2・SF7・W1 が順に触る | `plugins/ndf/scripts/lib/causes.py` |
| `plugins/ndf/scripts/supervise_lib/queue.py` | 変更 | A1b が `RELEASE_PHASES` を `delivery_templates` から引き、SF1b・SF7・W1 が続く | `plugins/ndf/scripts/supervise_lib/delivery_templates.py` |
| `plugins/ndf/scripts/supervise_lib/verify_steps.py` | 変更 | SF3・DC5・SF2・SF4・W1 が順に触る（#3153・閾値・`on_cause`・マージの種類・語） | `plugins/ndf/scripts/lib/causes.py` |
| `plugins/ndf/scripts/supervise_lib/delivery_templates.py` | 変更 | A1b が `RELEASE_PHASES` の持ち主にし、SF2 が `on_cause` で書き直し、W1 が語を替える | 無し |
| `plugins/ndf/scripts/hook_lib/token_guard.py` | 変更 | DC5・SF12・A3d・DC7・W5 が順に触る | `plugins/ndf/scripts/lib/limits.py` |
| `plugins/ndf/scripts/supervise.py` | 変更 | DC1・A1a・SF1b・SF5・SF13・K2・W1 が順に触る（DC1 は `deps.require` に `jsonschema` を足す） | `plugins/ndf/scripts/supervise_lib/commands.py` |
| `plugins/ndf/scripts/sprint-state.py` | 変更 | A1a・A1b・SF1b・SF13・A3e-2・DC7・K2 が順に触る | `plugins/ndf/scripts/lib/facts.py` |
| `plugins/ndf/scripts/mvv-gate.py` | 変更 | A1a・SF13・A3e-1・DC7・K2・W1 が順に触る | `plugins/ndf/scripts/lib/facts.py` |
| `plugins/ndf/scripts/check-trigger.py` | 変更 | A2・SF1b・A3e-1・DC7・K2・W1 が順に触る | `plugins/ndf/scripts/lib/facts.py` |
| `scripts/check-ndf-contracts.py` | 変更 | DC2 が `const-fact`・`fact-table` を、SF1a が `causes_decl` を呼ぶ | `scripts/contracts_lib/causes_decl.py` |
| `scripts/contracts_lib/ratchet.py` | 変更 | A0 が `initial` に検査の名前を、DC2 が `rows_added` を足す | 無し |
| `.github/workflows/script-structure.yml` | 変更 | A0 が `check-ai-smells.py` の手順を、DC2 が `ndf-contracts-check` に uv を足す | `scripts/check-ai-smells.py` |
| `plugins/ndf/scripts/lib/deps.py` | 変更 | DC1 が `GROUPS` に `jsonschema` を足し、SF1b が `_stop` を種類 `deps_missing` で返す形へ直す | `plugins/ndf/scripts/lib/run_core.py` |
| `pyproject.toml` | 変更 | DC1 が依存 `jsonschema` のグループを足す。SF6 の判断の呼び出しも同じグループを使う | 無し |
| `plugins/ndf/pyproject.toml` | 変更 | 同上（デプロイ先で答えを確かめ直す経路が使う） | 無し |

## クラスと関数の構造

型の関係は各主題の設計文書の「構成要素」と「構造」を指し、ここでは描き直さない。主題をまたいで同じ関数を 2 つの子 Issue が変えるものだけを挙げる。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/step_result.py` | モジュール | `emit`（変更。SF1b が `cause` の確かめ、SF13 が `routes` の確かめを足す。両方の確かめを持つ形は SF13 の PR が作る） |
| `plugins/ndf/scripts/lib/workflow_def.py` | モジュール（#3191 が新設） | `VOCABULARY`・`check_vocabulary`（変更。DC7 が `phase`、SF8 が `on_cause` を語彙へ足す） |
| `scripts/contracts_lib/ratchet.py` | モジュール | `initial`（A0 が変更）・`rows_added`（DC2 が新設） |
| `plugins/ndf/scripts/supervise_lib/state.py` | クラス `RunState` | `retry_counts`（SF2）・`attention`（SF7）・`write_report`（W1） |

## 関数の仕様

省く: 主題をまたいで新設する関数は無い。2 つの子 Issue が変える関数（上の表）の仕様は、それぞれの主題の詳細設計の「関数の仕様」が持つ。

## 異常系の処理

省く: 主題をまたぐ関数を新設しないため、異常系の処理は主題の詳細設計が持つ。流す順序を外れたときの壊れ方は「並行と排他」にある。

## 処理の手順

流す順序（「実装計画」の波）を決める規則である。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| 波の決め方 | 子 Issue に先に要るもの（主題の詳細設計の列と、#3191）がある | 先に要るものの波の後の波へ置く |
| 波の決め方 | 2 つの子 Issue が同じファイルを触る（ディレクトリは包含で見る。生成物 `contracts/index.md` は除く） | 次の優先の順で前の子 Issue を先の波へ置き、2 つを同じ波に置かない |
| 優先の順 | 主題をまたいで重なる | SF3 → A0 → SF1a → DC1 → A1a → A1b → A2 → SF1b → DC2〜DC6 → K3 → K1 → SF2・SF4・SF5・SF9〜SF12・SF7・SF6・SF13 → A3a〜A3g → DC8 → DC7 → K2 → SF8 → W1〜W7 → W8 |
| 優先の順の理由 | 古い経路を消す A1a・A1b | 子 Issue が移すコードから先に経路を除く（smells の決定 43） |
| 優先の順の理由 | 種類の契約 SF1b | 種類を返す 127 本を先にそろえ、A3 の直し方（握りつぶさずに返す）が種類を使える |
| 優先の順の理由 | A3a〜A3g | ファイルごとに中身を直すため、事実を移す DC と行き先を変える SF の後に置き、同じファイルを 2 度直さない |
| 優先の順の理由 | W1〜W7 | 語だけを替えるため最後に置き、先の子 Issue が足した文も同じ PR で替える |
| 優先の順の理由 | DC7・SF8 | #3191 のマージを待つため後ろへ置く |
| E9（測り直し） | すべての子 Issue のマージの後 | `scripts/measure/ai-smells.py`・`const-facts.py --json`・`ai-branching.py` を打ち、前後の数を #1142 のコメントへ残す |

- 繰り返しの終わる条件: 子 Issue は 47 本で有限。波は先に要るものと重なりの有向グラフの最長の道で決まり、輪は無い（優先の順が全順序のため）
- 計算量の注意: 無し

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `RELEASE_PHASES` | 定数 | `plugins/ndf/scripts/supervise_lib/delivery_templates.py` | A1b が `queue.py` から移して置く。DC7 が工程の文書のフェーズから導く形へ替える |
| `report_fields.DONE`・`GATE`・`STOPPED`・`NO_REPORT`・`HEADING` | 定数 | `plugins/ndf/scripts/lib/report_fields.py` | W1 が置く。1 つのコンテキストだけが読む語の持ち主のモジュールで、data の R3 の「事実の表へ移さない」と同じ向き |
| 再試行の方針 `cause.ci-pending`・`cause.infra-wait`・`cause.usage-limit` | 設定値 | `plugins/ndf/contracts/limits.json` | SF1a が足す。DC5 が足す閾値の方針と同じファイルの別のキー |
| `metadata.ndf` の語彙 | 定数 | `plugins/ndf/scripts/lib/workflow_def.py` の `VOCABULARY`（#3191） | #3191 の語彙（`stage`・`when`・`kind`・`range`・`subject`・`per_issue`）に、DC7 が `phase`、SF8 が `on_cause` を足す |
| 工程の失敗の終了コード | 設定値 | `plugins/ndf/contracts/causes.json` の `incomplete` ほか | #3191 は工程の失敗を 6 で返す。IS11 を正とし、SF8 が止まった理由の種類から引く終了コードへ合わせる |
| 依存 `jsonschema` | 設定値 | `pyproject.toml`・`plugins/ndf/pyproject.toml` のグループ | DC1 が足す。DC2 と SF6 が使う |
| `GATE_NAMES`（`関門 1`・`関門 2`） | 定数 | `plugins/ndf/scripts/lib/legacy_names.py` | A1a が消す（設計文書の「人へ残す判断」の (a)） |
| 重なりから除くファイル | 定数 | 下の確かめのスクリプトの `SHARED` | `plugins/ndf/contracts/index.md`（生成物） |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ波の子 Issue の PR | 触るファイル | 取らない。同じ波には触るファイルが重ならない組だけを置く（「流す順序」の確かめ） | 波の途中で止まった子 Issue は、同じ波のほかの子 Issue を止めない |
| 波をまたぐ子 Issue の PR | 重なるファイル | 前の波の PR のマージを待ってから、後の波の子 Issue を起動する（lrebuild が見張る） | 後の波の PR は base-sync で前の波の変更を取り込んでから直す |
| DC2〜DC8・SF1a の PR | 生成物 `plugins/ndf/contracts/index.md` | 取らない。後からマージする PR が base-sync の後に `python3 scripts/check-ndf-contracts.py --render` で書き直す | 書き直しは何度打っても同じ中身 |
| A0 の後のすべての PR | 兆候の基準ファイル `scripts/ai-smells-baseline/` | 取らない。後からマージする PR が base-sync の後に `check-ai-smells.py --write-baseline` を打ち直す（smells の詳細設計の「並行と排他」） | 今の数で上書きするため冪等 |
| DC2 の後の DC の PR | 重複の基準 `scripts/const-fact-baseline.json` | 取らない。直した組の行を自分の PR で消す（data の詳細設計） | 行を消すだけ |
| W1 のマージと、流れているプラン・キュー | 結果の値（`関門`） | 取らない。#1142 の実装の間はほかの開発を止める（決定 65） | W1 の前に止まったプランは、W1 の後に頭から流し直す |
| #3191 のマージと DC7・SF8 | `plugins/ndf/scripts/lib/workflow_def.py`・`progress_*.py` | DC7・SF8 は #3191 のマージの後に起動する | #3191 の実装の名前と突き合わせてから起動する |

**順序を破ったときの壊れ方。** 重なる 2 本を同じ波に置くと、後からマージする PR が衝突し、`base_conflict` で止まる。先に要るものより先に流すと、検査が使う依存（`jsonschema`）や行き先の経路（`causes.json`）がまだ無く、検査か工程が止まる。

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| 各子 Issue の PR | 「この子で解消する Issue」の `Closes` の行と、その Issue の再現の手順を 1 回通した結果 | PR 本文 | 再現できなかった Issue の `Closes`（理由を書いて開いたまま残す） |
| A3a〜A3g・W1〜W7 の PR | 着手の時点で数え直したファイルの一覧（A3 は `check-ai-smells.py`、W は用語チェックと同じ正規表現） | PR 本文 | 一覧の外のファイルの変更 |
| lrebuild の見張り | 波ごとの開始・マージの時刻と、次の波の子 Issue の起動 | 並列の計画の台帳（`parallel.py` の記録） | 子 Issue の中身の判断 |
| E9 | 前後の数（兆候・定数の重複・行き先の判断の回数） | #1142 のコメント | 無し |

## 既存の部品の再利用

省く: この文書は関数を新設しない。流す順序の確かめは、設計の結果の表を読む短いスクリプト（下の「流す順序」）で行い、プラグインのコードには足さない。

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `step_result.emit`（SF1b と SF13 の後） | `stopped` に `cause`、10〜19 に `routes` を持つ結果を出す | `ok` に `cause`・`routes` が無くても出す | `cause` の無い `stopped`・`routes` の無い 10〜19 をどちらも出さない |
| `limits.json` の読み手（SF1a と DC5 の後） | `cause.*` と DC5 の方針の両方を `limits.policy` で引ける | 片方の子 Issue だけがマージされた状態でも、もう片方のキーを読まない読み手は動く | 無いキーで `limits` の誤りとして止まる |
| `workflow_def.check_vocabulary`（DC7 と SF8 の後） | `metadata.ndf.phase` と `metadata.ndf.on_cause` を持つ工程の文書が通る | どちらも持たない文書も通る | 語彙に無いキーを持つ文書を断る |

## 実装計画

子 Issue は 47 本である。触るファイルの全体は設計文書の「設計の結果」の表にあり、`design-survey.py check` が主題の詳細設計のファイル配置の表と照合する。PR は子 Issue ごとに 1 本で、その revert 1 つで 1 つ前に戻る（I10）。A3a〜A3g と W1〜W7 の触るファイルは、2026-11-13（7da134096）の一覧で、着手の時点で数え直す。

| 子 Issue | 主題 | 中身 1 文 | 触るファイル（数と、主題の詳細設計の節への参照） | 先に要るもの | 並列の組 | PR の単位 | 見直す前の企画の設計文書の節 | 消す古いテスト | この子で解消する Issue |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `A0` | smells | 検査・規則・基準を入れ、兆候を増やす変更を CI で落とす | 9 本。smells の詳細設計の「ファイル配置」 | 無し | 組 S | 1 本 | 無し | 無し | — |
| `A1a` | smells | 旧名を読み替える経路と、旧 K4 の撤去（呼び出し元の無いエントリポイント・投稿キューの移行の前のファイルの取り込み）を消す | 45 本。smells の設計文書の「直す順序と触るファイル」の A1a の行と前の企画の詳細設計の「（A1a。旧 K4）」の行 | `A0` | 組 S | 1 本 | 移行の文書の「撤去（3 件）」・決定 65 | `test_legacy_names.py`・`test_gate_names.py`、`test_step_scripts.py` の phase-steps の節、`test_post_queue.py` の取り込みのテスト | — |
| `A1b` | smells | 古い値を読み替える経路を消す | 20 本。smells の設計文書の「直す順序と触るファイル」の A1b の行 | `A1a` | 組 S | 1 本 | 決定 65 | `test_legacy_values.py` | — |
| `A2` | smells | 範囲の外のループの中の外部呼び出しを 1 回のクエリへまとめるか上限の印を付ける | 23 本。smells の設計文書の「直す順序と触るファイル」の「A2 の 22 本」 | `A0` | 組 S | 1 本 | 無し | `test_merged_probe.py` の head ごとに `gh pr list --head` を呼ぶ場合 | — |
| `A3a` | smells | `scripts/lib/` の a〜fの握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 42 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `A3b` | smells | `scripts/lib/` の g〜oの握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 43 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `A3c-1` | smells | `scripts/lib/` の p〜z の前半の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 26 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `A3c-2` | smells | `scripts/lib/` の p〜z の後半の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 26 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `A3d` | smells | `scripts/hook_lib/`・`scripts/relay_lib/`の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 34 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `A3e-1` | smells | `scripts/` の直下と下位の 5 つのうち名前の前半の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 29 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `A3e-2` | smells | 同じく後半の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 28 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `A3f-1` | smells | `skills/cross-refactoring/scripts/` の前半の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 35 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `A3f-2` | smells | 同じく後半の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 34 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `A3g` | smells | cross-refactoring を除く Skill の `scripts/`の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 50 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `DC1` | data | 事実の表の読み手（`lib/facts.py`・`lib/facts.sh`）と表の共通の形、依存 `jsonschema` を足す | 13 本。data の詳細設計の「ファイル配置」 | 無し | 組 D | 1 本 | 無し | 無し | — |
| `DC2` | data | 重複の検査 `const-fact`・`fact-table` と基準を入れる（DT1 の後半・DT7） | 15 本。data の詳細設計の「ファイル配置」 | `DC1` | 組 D | 1 本 | 無し | 無し（計測のスクリプトにテストは無い） | — |
| `DC3` | data | ランタイムの表を置き、読み手を表から導く | 21 本。data の詳細設計の「ファイル配置」 | `DC1` | 組 D | 1 本 | 無し | `test_state_review_pool.py` の消す分岐のテスト 2 件と、ランタイムの文字列の集まりを確かめる assert | — |
| `DC4` | data | 収束ループのフェーズ・指摘の重さ・テストの印の表を置く | 20 本。data の詳細設計の「ファイル配置」 | `DC1` | 組 D | 1 本 | 無し | `test_measure.py:687` の一致の確認 | #1774 |
| `DC5` | data | 閾値を上限の宣言へ移す | 16 本。data の詳細設計の「ファイル配置」 | `DC3` | 組 D | 1 本 | 無し | `test_relay.py:2885`・`test_queue_idempotency.py:52`・`test_testenv_reclaim.py:862` の一致の確認 | — |
| `DC6` | data | 宣言の置き場・環境変数名・PR 本文の見出し・指示書の表を置く | 19 本。data の詳細設計の「ファイル配置」 | `DC1` | 組 D | 1 本 | 無し | 置き場・指示書・見出しの文字列の集まりだけを確かめる assert | — |
| `DC7` | data | 工程とフェーズ（`metadata.ndf.phase`）・モード・進め方・ブランチの接頭辞・承認ゲートを表と工程の文書から引く | 28 本。data の詳細設計の「ファイル配置」 | #3191、`DC5`・`DC6`・`A1a`・`A1b` | 組 D | 1 本 | 移行の文書の「値として読まれる古い語」の R2 | `test_transcript_agents.py` の 2 件と development-workflow の定義の文字列を読むテスト | — |
| `DC8` | data | 子 Issue の後に残る組（`sprint_loop_record`・`release_lib`・`parallel_*`・`durable`・`decl`）を持ち主から引く | 11 本。data の詳細設計の「ファイル配置」 | `DC1` | 組 D | 1 本 | 無し | 持ち主へ寄せた定数の一致を確かめる assert | — |
| `SF3` | scripted-flow | CI の監視が分割ジョブの失敗とまとめのチェックの未完了を読み違える不具合を直す（#3153） | 5 本。scripted-flow の設計文書の「子 Issue の案」の SF3 の行 | 無し | 組 F | 1 本 | 無し | `test_ci_watch.py` の `decide_exit` の `after_merge` の関数 | #3153 |
| `SF1a` | scripted-flow | 止まった理由の表 `causes.json` と読み手 `lib/causes.py` を契約に置く | 9 本。scripted-flow の設計文書の「子 Issue の案」の SF1a の行 | 無し | 組 F | 1 本 | 無し | 無し | — |
| `SF1b` | scripted-flow | 結果 JSON の `cause` を必須にし、`stopped` を出すすべての手順のスクリプトが種類を返す | 135 本。scripted-flow の設計文書の「子 Issue の案」の SF1b の行と、詳細設計の「SF1b の移す先」の 127 本 | `SF1a` | 組 F | 1 本 | 無し | `test_step_result.py`・`test_step_result_readers.py`（ファイルごと）と `test_run_core.py` の関数 | — |
| `SF2` | scripted-flow | プランの行き先を種類で選び、待てば解ける止まりを打ち直す。テンプレートを `on_cause` で書き直す | 46 本。scripted-flow の設計文書の「子 Issue の案」の SF2 の行（テンプレートの 11 本は詳細設計の「表の外にまとめたファイル」） | `SF1b` | 組 F | 1 本 | dbos の文書の「落ちた後の打ち直し（実測）」・決定 27 | `test_supervise_step_rerun.py`・`test_push_fix.py`・`test_supervise_limits.py`（ファイルごと）と 19 本の関数 | #2273 |
| `SF4` | scripted-flow | マージの落ちたチェックをテストとテスト以外に分け、supervisor が CI を手で読む手順を外す | 9 本。scripted-flow の設計文書の「子 Issue の案」の SF4 の行 | `SF2`・`SF3` | 組 F | 1 本 | 無し | `test_ci_watch.py` の終了コード 2・3 の関数・`test_merged_probe.py` の CI の失敗の関数 | #2719 |
| `SF5` | scripted-flow | 設計のプランの最後の judge を外し、レビューの結果で設計承認ゲートへ渡す | 5 本。scripted-flow の設計文書の「子 Issue の案」の SF5 の行 | `SF2` | 組 F | 1 本 | 無し | 無し | — |
| `SF6` | scripted-flow | 判断の呼び出しの答えを選択肢か JSON のスキーマで縛る | 10 本。scripted-flow の設計文書の「子 Issue の案」の SF6 の行 | `SF2`・`SF4`・`DC1` | 組 F | 1 本 | 無し | `test_slow_step.py`・`test_supervise_slow.py` の答えを読めないときに `wait` へ倒す関数 | — |
| `SF7` | scripted-flow | プランの wait が attention を種類つきで返し、次のコマンドを `next` に載せる | 4 本。scripted-flow の設計文書の「子 Issue の案」の SF7 の行 | `SF1b`・`SF2` | 組 F | 1 本 | dbos の文書の「`queue`: ステージ・重なり・資源の枠」 | `test_queue_waits.py`（作り直す） | — |
| `SF8` | scripted-flow | 進行表が工程の失敗の種類から行き先を選び、conductor へ `routes` だけを返す。工程の失敗の終了コード 6 を IS11 に合わせる | 5 本。scripted-flow の設計文書の「子 Issue の案」の SF8 の行と `lib/workflow_def.py`（語彙に `on_cause` を足す。設計文書の「進行表（#3092）との受け渡し」） | #3191、`SF2`・`SF13` | 組 F | 1 本 | —（#3092 の設計） | 無し | — |
| `SF9` | scripted-flow | 並列の計画の待ちの結果の行き先をスクリプトが選ぶ | 6 本。scripted-flow の設計文書の「子 Issue の案」の SF9 の行 | `SF1b` | 組 F | 1 本 | 無し | 無し | — |
| `SF10` | scripted-flow | 手順 0 の 3 つの確かめを 1 回の結果 JSON にまとめる | 4 本。scripted-flow の設計文書の「子 Issue の案」の SF10 の行 | `SF1b` | 組 F | 1 本 | 無し | 無し | — |
| `SF11` | scripted-flow | 開発ループのサブコマンドの断りと止まりが種類と `next` を返す | 4 本。scripted-flow の設計文書の「子 Issue の案」の SF11 の行 | `SF1b` | 組 F | 1 本 | 無し | `test_sprint_loop.py` の終了コードと `metrics.blocked` を見る関数 | — |
| `SF12` | scripted-flow | 会話の切れ目と中断の点検の行き先を結果で返す | 5 本。scripted-flow の設計文書の「子 Issue の案」の SF12 の行 | `SF1b`・`DC5` | 組 F | 1 本 | 無し | `test_token_guard.py` の案内の文を見る関数 | — |
| `SF13` | scripted-flow | 承認ゲートの結果に答えごとの `routes` を必須で足し、問いの区分と選択肢を結果に載せる | 19 本。scripted-flow の設計文書の「子 Issue の案」の SF13 の行 | `SF1b`・`SF5`・`SF9` | 組 F | 1 本 | 無し | 無し | — |
| `K1` | 前の企画 | Markdown のコードブロックの判定をアダプター `md` へ移す | 16 本。前の企画の詳細設計の「ファイル配置」の K1 の行 | `SF3`・`DC5` | 組 B | 1 本 | libraries の文書の「実装の状態と残り」・決定 68 | `test_gh_parts.py` のコードブロックの現状固定のテスト・`test_wait_notify.py` の地の文の行のテスト | — |
| `K2` | 前の企画 | パスの照合とコメントの除去をアダプターへ移し、`comment_mask.py` を消す | 15 本。前の企画の詳細設計の「ファイル配置」の K2 の行 | `K1`・`DC7` | 組 B | 1 本 | libraries の文書の「実装の状態と残り」・決定 68 | `test_pace.py` の照合のテスト | — |
| `K3` | 前の企画 | HTTP を `notify` の httpx へ移す | 11 本。前の企画の詳細設計の「ファイル配置」の K3 の行 | 無し | 組 B | 1 本 | libraries の文書の「実装の状態と残り」・決定 68 | `test_refresh.py`・`test_instructions_check.py` の opener のテスト・`test_jev.py` の `_post` のテスト | — |
| `W1` | 前の企画 | プランの実行の語（フェーズの結果の語を `report_fields` に集める）: 用語集の廃止語を生きた語へ替える | 66 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W1 の行 | 触るファイルの重なる子のすべて（流す順序の波で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W2` | 前の企画 | cross-review の語: 用語集の廃止語を生きた語へ替える | 28 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W2 の行 | 触るファイルの重なる子のすべて（流す順序の波で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W3` | 前の企画 | 外部 CLI と記録の語: 用語集の廃止語を生きた語へ替える | 16 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W3 の行 | 触るファイルの重なる子のすべて（流す順序の波で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W4` | 前の企画 | cross-refactoring の語: 用語集の廃止語を生きた語へ替える | 42 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W4 の行 | 触るファイルの重なる子のすべて（流す順序の波で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W5` | 前の企画 | worktree と hook の語: 用語集の廃止語を生きた語へ替える | 27 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W5 の行 | 触るファイルの重なる子のすべて（流す順序の波で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W6` | 前の企画 | ラッパーの語: 用語集の廃止語を生きた語へ替える | 11 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W6 の行 | 触るファイルの重なる子のすべて（流す順序の波で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W7` | 前の企画 | リリースと文書の語: 用語集の廃止語を生きた語へ替える | 42 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W7 の行 | 触るファイルの重なる子のすべて（流す順序の波で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W8` | 前の企画 | W1〜W7 で外した廃止語を禁止語にする | 1 本。`.ndf/glossary.json` の 1 本 | `W1`〜`W7` | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」 | 無し | — |

**smells の範囲の外の残り。** smells の決定 43 は、リファクタリング計画の 8 件の範囲（`supervise.py`・`supervise_lib/`・`parallel.py`・`lib/parallel_*`・`lib/sprint_loop*`・`release-steps.py`・`release_lib/`・`lib/durable.py`・`lib/post_queue.py`・2 本の `drive.py`。兆候のあるファイル 101 本）を A3 から除いた。8 件を外した形ではこの範囲を直す子 Issue が無く、兆候は基準のラチェットで増えないが減らない。推奨の案（8 件を取り込む）では、8 件の子 Issue がこの範囲を直す。E9 で前後の数を残す。

### リファクタリング計画の 8 件

上の表は、8 件を先に要るものから外した形で組んだ。各主題の「先に要るもの」に書かれた 8 件は表から外し、この形で同じ目的の一部を受け持つ子 Issue を次に示す。「受け持たない部分」が、8 件を取り込む推奨の案で 8 件の子 Issue が受け持つ範囲である。

| Issue | 目的 | 受け持つ子 Issue | 受け持たない部分 |
| --- | --- | --- | --- |
| #2963 | supervise の遷移の判断を耐久の起動から切り離し、遷移のテストで DBOS を起こさない | `SF2`（行き先を純粋な関数 `causes.pick`・`routes` にし、`test_routes.py`・`test_plan_routes.py` は DBOS を起こさない） | `queue.blocked()` の単体のテスト |
| #2990 | 判断を純粋な関数に、耐久の層を薄い殻に分ける共通の形 | `SF2`・`SF8`（行き先の関数と耐久の待ち `durable.sleep`） | 並列の計画・投稿キューで使う共通の形の部品 |
| #2991 | ファイルを分ける基準を行数から責務へ | `A0`（関数の長さ・引数・分岐の多さを規則で数える） | 構造チェックの 500 行の上限の置き直し |
| #2992 | 並列の計画の判断を純粋な関数へまとめ、輪をほどく | `SF9`（wait の結果の行き先） | `parallel_*` の輪をほどく組み直し |
| #2993 | supervise の engine・flow・queue の輪をほどく | `SF2`・`SF6`・`SF7` | 輪をほどく組み直し |
| #2994 | 開発ループの判定・選定・記録を責務で分ける | `SF11`（断りと止まりの種類と `next`） | 記録と実際のリリースの一致 |
| #2995 | `release-steps.py` を段階ごとに分ける | 無し | 全部 |
| #2996 | 構造と契約の検査の構文木の解析を 1 回に | 無し（DC2 の `const_facts` は自分で解析する） | 全部 |

推奨の案（8 件を子 Issue に取り込み、別の設計 PR で設計する）を採ったときに変わる点:

- 8 件を子 Issue として設計し、SF1b・SF2・SF6・SF7・SF9・SF11・SF13・DC2・DC8・K1〜K3 の先に要るものへ戻すため、波が増え、#2963・#2993 が SF1b の前、#2996 が DC2 の前に入る
- smells の決定 43 のとおり、上の「smells の範囲の外の残り」の 101 本の兆候は、その範囲の子 Issue が移すときに直す
- 開いている bug は、別の設計 PR で 8 件の子 Issue へ割り当て直す（下の「開いている bug の割り当て」で範囲外とした行のうち、8 件の範囲のもの）
- 8 件に依らない子 Issue（波 1 の SF3・A0・SF1a・DC1、波 2 の A1a・A2、波 3 の A1b など）は、8 件の設計を待たずに着手する

### 流す順序

同じ波の子 Issue は、触るファイルが重ならず、先に要るものが前の波にある。波の中の並びは優先の順（「処理の手順」）である。

| 波 | 同時に流せる子 Issue | 待つ理由（その波を決めた前の子 Issue と、重なるファイルか先に要るもの） |
| --- | --- | --- |
| 1 | `SF3`・`A0`・`SF1a`・`DC1` | 無し（最初の波） |
| 2 | `A1a`・`A2`・`DC2` | `A1a` ← `SF1a`（`scripts/lib/README.md`）、`A2` ← `A0`（先に要るもの）、`DC2` ← `DC1`（先に要るもの） |
| 3 | `A1b` | `A1b` ← `A1a`（`scripts/lib/README.md` ほか 4 本） |
| 4 | `SF1b` | `SF1b` ← `A1b`（`scripts/context-policy.py` ほか 8 本） |
| 5 | `DC3`・`DC4`・`DC6`・`K3`・`SF9`・`SF10`・`SF11` | `DC3` ← `SF1b`（`scripts/training-optout.py` ほか 2 本）、`DC4` ← `SF1b`（`scripts/mode-materials.py` ほか 1 本）、`DC6` ← `SF1b`（`scripts/glossary.py` ほか 3 本）、`K3` ← `SF1b`（`scripts/release-steps.py`）、`SF9` ← `SF1b`（`scripts/lib/parallel_cli.py` ほか 1 本）、`SF10` ← `SF1b`（先に要るもの）、`SF11` ← `SF1b`（`scripts/lib/sprint_loop_cli.py`） |
| 6 | `DC5`・`A3f-1`・`A3f-2` | `DC5` ← `DC3`（`scripts/hook_lib/wait_notify.py`）、`A3f-1` ← `K3`（`skills/cross-refactoring/scripts/refactor.py`）、`A3f-2` ← `DC4`（`skills/cross-refactoring/scripts/refactor_lib/pathkinds.py` ほか 1 本） |
| 7 | `K1`・`SF2`・`SF12`・`A3c-1`・`W4` | `K1` ← `DC5`（`scripts/issue-file.py`）、`SF2` ← `DC5`（`scripts/supervise_lib/verify_steps.py`）、`SF12` ← `DC5`（`scripts/hook_lib/token_guard.py`）、`A3c-1` ← `DC5`（`scripts/lib/run_owner.py`）、`W4` ← `A3f-2`（`skills/cross-refactoring/scripts/refactor_lib/github.py` ほか 19 本） |
| 8 | `SF4`・`SF5`・`SF7`・`A3b`・`A3d`・`A3g`・`DC8` | `SF4` ← `SF2`（`scripts/supervise_lib/verify_steps.py`）、`SF5` ← `SF2`（`scripts/supervise_lib/design_steps.py`）、`SF7` ← `SF2`（`scripts/supervise_lib/state.py`）、`A3b` ← `K1`（`scripts/lib/gh_parts.py` ほか 2 本）、`A3d` ← `SF12`（`scripts/hook_lib/token_guard.py`）、`A3g` ← `K1`（`skills/backlog-refinement/scripts/upkeep.py` ほか 1 本）、`DC8` ← `SF2`（`scripts/lib/durable.py`） |
| 9 | `SF6`・`SF13` | `SF6` ← `SF4`（先に要るもの）、`SF13` ← `SF7`（`skills/development-workflow/references/waiting.md`） |
| 10 | `A3a`・`A3c-2`・`A3e-1`・`A3e-2` | `A3a` ← `SF13`（`scripts/lib/ask_policy.py`）、`A3c-2` ← `SF13`（`scripts/lib/step_result.py`）、`A3e-1` ← `SF13`（`scripts/merged-steps.py` ほか 2 本）、`A3e-2` ← `SF13`（`scripts/sprint-close.py` ほか 1 本） |
| 11 | `DC7`・`W6` | `DC7` ← `A3e-2`（`scripts/sprint-state.py`）、`W6` ← `A3a`（`scripts/lib/claude_api_keys.py`）、`DC7` は #3191 のマージも待つ |
| 12 | `K2`・`SF8`・`W3`・`W5` | `K2` ← `DC7`（`scripts/check-trigger.py` ほか 4 本）、`SF8` ← `DC7`（`scripts/lib/workflow_def.py` ほか 1 本）、`W3` ← `DC7`（`scripts/lib/transcript_agents.py`）、`W5` ← `DC7`（`scripts/hook_lib/token_guard.py` ほか 1 本）、`SF8` は #3191 のマージも待つ |
| 13 | `W1`・`W2`・`W7` | `W1` ← `SF8`（`skills/development-workflow/references/agent-layers.md`）、`W2` ← `K2`（`scripts/lib/review_criteria.py` ほか 1 本）、`W7` ← `K2`（`skills/fix/scripts/fix-steps.py`） |
| 14 | `W8` | `W8` ← `W7`（先に要るもの） |

確かめ方（リポジトリの根で打つ。2026-11-13 に打ち、「子 Issue 47・波 14: 同じ波の重なりと先に要るものの逆転は 0 件」を返した）:

```bash
python3 - <<'EOF'
import itertools, re, sys
D = open("issues/issue-1142-design.md", encoding="utf-8").read()
H = open("issues/issue-1142-design-detail.md", encoding="utf-8").read()
SHARED = {"plugins/ndf/contracts/index.md"}  # 生成物。後からマージする PR が --render で書き直す
files = {}
for line in D.split("\n## 設計の結果\n", 1)[1].split("\n## ", 1)[0].splitlines():
    m = re.match(r"\| #\d+ \| 実装する \| — \| （([^。）]+)。[^）]*）(.*) \|$", line)
    if m:
        files[m.group(1)] = set(re.findall(r"`([^`]+)`", m.group(2))) - SHARED
def hit(a, b):
    return sorted({x for x in files[a] for y in files[b] if x == y or (x.endswith("/") and y.startswith(x)) or (y.endswith("/") and x.startswith(y))})
waves, deps = {}, {}
for line in H.split("\n### 流す順序\n", 1)[1].split("\n### ", 1)[0].splitlines():
    m = re.match(r"\| (\d+) \| ([^|]+) \|", line)
    if m:
        waves.update({c: int(m.group(1)) for c in re.findall(r"`([^`]+)`", m.group(2))})
for line in H.split("\n## 実装計画\n", 1)[1].split("\n### ", 1)[0].splitlines():
    cells = line.split(" | ")
    if line.startswith("| `") and len(cells) > 4:
        deps[cells[0].strip("| `")] = re.findall(r"`([^`]+)`", cells[4])
bad = [f"波 {waves[a]}: {a} と {b} が重なる: {hit(a, b)[:3]}" for a, b in itertools.combinations(sorted(waves), 2) if waves[a] == waves[b] and hit(a, b)]
bad += [f"{c} が先に要る {d} より前の波にある" for c, ds in deps.items() for d in ds if d in waves and waves[d] >= waves[c]]
bad += [f"設計の結果・流す順序・実装計画の子 Issue が食い違う: {sorted(set(files) ^ set(waves) | set(files) ^ set(deps))}"] if set(files) != set(waves) or set(files) != set(deps) else []
print("\n".join(bad) or f"子 Issue {len(files)}・波 {max(waves.values())}: 同じ波の重なりと先に要るものの逆転は 0 件")
sys.exit(1 if bad else 0)
EOF
```

スクリプトは、設計文書の「設計の結果」の行から子 Issue ごとの触るファイルを読み（括弧の中の名前が子 Issue）、同じ波の組の重なりと、上の表の「先に要るもの」が後ろの波にある逆転を数える。設計の結果の触るファイルが主題の詳細設計のファイル配置の表を含むことは、`design-survey.py check --require-detail` の `detail_file` が確かめる。

### レーンへの割り当ての案

並列の計画 p6 は「同時に動くレーンは 3 本まで」の規則を持つ。lflow は #3092（PR #3191）を続けるため、残りの 2 本を使い、lflow が終わったら lrel を足す。オーガナイザーは `parallel.py lane p6 set --lane <名前> --from <JSON>` で割り当て直す。

| レーン | 受け持つ組 | プランの本数 | いつ | 役割 |
| --- | --- | ---: | --- | --- |
| lrebuild | 組 S（smells）と、lflow が終わるまでに波の来た組 D・組 B の子 Issue | 2 | 今から | 組 S を流し、**波の順序を見張る**（前の波の PR のマージを確かめてから、次の波の子 Issue を各レーンへ知らせる） |
| lbranch（割り当て直し） | 組 F（scripted-flow） | 2 | 今から | SF3 から流す。SF8 は #3191 のマージの後 |
| lflow | #3092（PR #3191）の続き | 1 | 今のまま | #3191 のマージが DC7・SF8 の先に要るもの |
| lrel（割り当て直し、`after: ["lflow"]`） | lflow の後の組 D・組 B の残り（DC7・DC8・K2・W1〜W8 ほか） | 2 | lflow の後 | 組 D と組 B を lrebuild から引き継ぐ |
| lclean | 割り当てない | — | — | 同時に動くレーンを 4 本に上げると利用者が決めたら、組 B を lrel から移す |

`lane set` に渡す JSON の形（子 Issue を起票した後に番号を入れる）:

```json
{"name": "lbranch", "input": {"kind": "sprint", "issues": [<SF3 の #3153 と SF の子 Issue の番号>]}, "plans": 2,
 "touches": ["plugins/ndf/scripts/supervise_lib/", "plugins/ndf/scripts/lib/", "plugins/ndf/contracts/"], "after": []}
```

## 開いている bug の割り当て

2026-11-13 の時点で開いている bug 110 件の本文を読み、本文が名指すファイル・振る舞いと子 Issue の変更内容を突き合わせた。**ファイルが重なるだけでは割り当てない。** 子 Issue の変更で直る見込みがあるものだけを割り当てた。

| 割り当て | 件数 |
| --- | ---: |
| SF3 | 1 |
| SF2 | 1 |
| SF4 | 1 |
| DC4 | 1 |
| 範囲外 | 106 |

範囲外の大半は、手順の誤り・別の原因・作り直しが変えない振る舞い（再開の決まり・並列の計画の上限・リリースの資料・cross-refactoring の手順）である。割り当てた Issue は、子 Issue の PR で再現の手順を 1 回通し、直った PR を添えて閉じる。範囲外の Issue は #1142 の作り直しの後も開いたまま残し、作り直しの後に閉じるか理由を書く。

| Issue | 題 | 割り当て（子 Issue か「範囲外」） | 理由 1 文 |
| --- | --- | --- | --- |
| #3187 | 振る舞いの確認: claude -p の起動で標準入力が閉じず、ほかの対象の依頼文が入力に付く | 範囲外 | `behavior-check.py` の worker の手順の起動の形（標準入力を閉じない）の誤りで、作り直しの子はこの手順を変えない |
| #3186 | supervise.py: merge で終わるプランの報告で、消えた作業場所のまま gh を打ち PR 本文の通した工程を書き直せない | 範囲外 | `Engine.report()` が消えた作業場所を cwd に `gh` を打つ別の原因で、SF2 は `engine.py` の失敗の分岐だけを替える |
| #3181 | cross-review: 設計 PR の 2 ラウンド目以降が前の head からの差分だけを見て、詳細レビューを通らない節を残したまま収束する | 範囲外 | cross-review の `classifications.diff_scope` の範囲の決め方の誤りで、作り直しの範囲の外 |
| #3176 | supervise run: 差し戻しの後に同じ --from で打ち直すと、直した head を流さず前回の報告を返す | 範囲外 | 耐久の実行の鍵が head を持たず前回の報告を返す別の原因で、SF2 の行き先の表は実行の鍵を変えない |
| #3175 | 並列の計画: リリース承認ゲートの承認を送った後、約 6 時間リリースが進まなかった | 範囲外 | 原因を確かめていない（一時レーンの起動の待ちか承認のイベントの受け取り）。SF9 の待ちの行き先では直らない |
| #3174 | parallel.py answer: --choice と --note を一緒に渡すと指摘が答えに載らず、レーンへ届かない | 範囲外 | `parallel_answer.answer_arg_error` が `--choice` と `--note` の組を扱わない引数の誤りで、SF9 は差し戻しの候補を足すだけ |
| #3173 | 並列の計画: 終わった 1 スプリントのレーンのラッパーが止まらず残り、動いていないとの問いが人へ届く | 範囲外 | 終わったレーンのラッパーを止める手順の欠けで、作り直しの範囲の外（#3058 の続き） |
| #3172 | 並列の計画: 古い記録から再開した耐久ワークフローが閉じた PR の依頼を外さず、差し戻すたびに中身の無いリリースを組み直す | 範囲外 | 10.27.60 より前の耐久の記録から再開した別の原因で、作り直した後は空のリリースが組まれていない（本文の表） |
| #3169 | リリース承認ゲートの承認資料: 実行されなかった CI のチェック（skipping）を未通過と数え、MVV 判定が判定できないを返す | 範囲外 | `release-steps.py` の承認資料が skipping を分母に数える集計の誤りで、作り直しの子はこの集計を変えない |
| #3168 | selected のリリース: 承認の後の直接のコミットと、動いたスプリントの先頭を確かめずに本番へ入れる | 範囲外 | selected の取り込み方の承認と本番の比べ方の欠けで、新しい確かめの機能が要り作り直しの範囲の外 |
| #3167 | project-decl.py measure: 丸ごと出すリポジトリで、ベースブランチから切ったスプリントブランチの起点を production と数える | 範囲外 | `project-decl.py measure` のブランチの起点の判定の誤りで、作り直しの範囲の外 |
| #3153 | ci-watch.py: マージがシャードのテストの失敗で落ちた直後に、まとめのチェックが pending だと CI 以外の理由と読む | SF3 | SF3 がこの Issue そのもの（分割ジョブの照合・マージの結果の引き継ぎ・未完了の読み直し） |
| #3145 | parallel.py check: プランの本数の上限（I2）が after で直列のレーンも足し合わせ、順番待ちのレーンを足せない | 範囲外 | `parallel_plan._check_limits` が `after` を見ない上限の数え方の誤りで、作り直しの範囲の外 |
| #3140 | テスト: test_peak_measures_each_count_of_runs が同時に流す 2 本の起動の順で落ち、文書だけの PR の CI を止める | 範囲外 | テストが起動の順に依存する書き方の誤りで、スクリプトの作り直しでは直らない |
| #3136 | 並列の計画: まとめたリリースの差し戻しが gate-rejections の記録に残らず、振り返りの差し戻しが 0 件になる | 範囲外 | `parallel_run` の settle が `gate-rejections` の記録を打たない欠けで、作り直しの子はこの記録を足さない |
| #3129 | コミットの件名「Fix: #N」: GitHub が`閉じる語`と読み、main への取り込みで課題が工程の途中に閉じる（#1271 の再発） | 範囲外 | コミットの件名を決めるプロンプトの書き方（`Fix: #N`）の誤りで、作り直しの範囲の外 |
| #3128 | 並列の計画: リリースの答えにリリース記録の PR が無く、レーンが main 宛ての PR を new close --record-pr に渡して close が記録を読めない | 範囲外 | リリースの答えにリリース記録の PR を載せない受け渡しの欠けで、SF9 の待ちの行き先では直らない |
| #3118 | 並列の計画: 差し戻しを受けた後に同じ状態ディレクトリでリリースの依頼を打ち直すと、同じ ID のため台帳へ載らないのに「送った」と返る | 範囲外 | 同じ ID の依頼を台帳が重複として捨てる別の原因で、SF9 の待ちの行き先では直らない |
| #3115 | リリース後テストの記録: 対象の版を「ndf v<版>」と書くと照合できず、close が他スプリントのブロックで判定する | 範囲外 | `dist_record._strip_note` がプラグイン名の前置きを落とさない読み取りの誤りで、A1a・A1b は古い見出しを外すだけ |
| #3114 | supervise.py queue の再開: --from が base-sync より後を指すと、間に入った PR を取り込まず衝突する | 範囲外 | `--from` が base-sync を飛ばす再開の決まりの欠けで、作り直しの子はこの再開点を変えない |
| #3113 | whole-test-timing.py peak: 承認待ちで止まった他レーンの queue を作業中と数えて計測を断る | 範囲外 | `whole-test-timing.py peak` の作業中の判定の誤りで、作り直しの範囲の外 |
| #3112 | 実装のプラン: impl の担当が全体テストを打ち、過負荷で無進捗の打ち切りになる | 範囲外 | impl の担当への指示文が全体テストを禁じない手順の欠けで、作り直しの範囲の外 |
| #3110 | リリースの承認資料: 「指示書の変更」が .ndf/ の設定の変更を載せず、C7 の変更を人が見られない | 範囲外 | 承認資料の「指示書の変更」が `.ndf/` を見ない集め方の欠けで、DC6 の指示書の表はこの集め方を変えない |
| #3109 | クロスレビュー: レビュー中に PR の worktree の管理情報が消え、result_posts.py fix が「リポジトリを決められない」で止まる | 範囲外 | worktree の管理情報が消える別の原因（片付けの側）で、作り直しの範囲の外 |
| #3108 | 開発版のデプロイ: リリース後テストの前の取り込みが、メインディレクトリに残った版のファイルの変更で落ちる | 範囲外 | メインディレクトリに残った版のファイルの変更という手順の順序の誤りで、作り直しの範囲の外 |
| #3106 | cross-review: 作業中の /tmp の worktree の登録が消え、取り込みが push できずに止まる | 範囲外 | 作業中の worktree の登録が消える別の原因（片付けの側）で、作り直しの範囲の外 |
| #3105 | 共有の依存の環境: python の選び方が呼び出しで食い違い、走っているプロセスの最中に環境が作り直されて import が落ちる | 範囲外 | 共有の依存の環境の python の選び方と作り直しの時点の誤りで、作り直しの範囲の外 |
| #3096 | parallel_machine: 回答後・再起動前の保留レーンがマシン全体の上限の予約から外れ、別の計画の起動と重なると上限を超える | 範囲外 | `parallel_machine` の予約の数え方の誤りで、作り直しの範囲の外 |
| #3093 | 設計 PR の本文で、モードの行が「決めたこと」の節の中へ入り pr-body-decisions-check が落ちる | 範囲外 | `pr_mode.with_mode_line` の置き場所の誤りで、DC6 は見出しを表から引くだけで置き場所を変えない |
| #3089 | 並列の計画: レーンが手順書どおり parallel.py await を打つと、既定の待ち時間 0 で待たずに戻る | 範囲外 | `parallel.py await` の待ち時間の既定と手順書の食い違いで、SF9 はオーガナイザーの wait の行き先だけを扱う |
| #3087 | 検査のトリガー: リリースの PR を除いた行数が二重に差し引かれ、範囲の変更行数が実際より小さくなる | 範囲外 | `check-trigger.py` の `changed_lines` の差し引き方の誤りで、作り直しの範囲の外 |
| #3086 | supervise の再開: リリースの依頼の答えの後に --from <プラン>=end で続けると「知らないステップ: end」で止まり、まとめのプランが流れない | 範囲外 | 再開の開始のステップに `end` を受けない決まりの欠けで、SF2 の行き先の表は再開の開始を変えない |
| #3077 | issue-file.py create: 別のリポジトリへの起票で、由来の行が番号だけになり起票先の別の課題を指す | 範囲外 | `issue-file.py create` の由来の行の書き方の誤りで、作り直しの範囲の外 |
| #3064 | 並列の計画: レーンを足して上限を超えた計画で、parallel.py present が上限つきで検査し直し I2 で止まる | 範囲外 | `present` が上限つきで計画を読み直す誤りで、作り直しの範囲の外 |
| #3053 | cross-refactoring: 安全網の上限で止まった実行を --safety-net-minutes を大きくして打ち直しても続かない | 範囲外 | cross-refactoring の安全網の後の再開の欠けで、作り直しの範囲の外 |
| #3047 | まとめのプラン: --from retro で流し直すと deep-due が飛ばされ、振り返りの範囲が無いまま retro が起動する | 範囲外 | `--from retro` が deep-due を飛ばす再開の決まりの欠けで、作り直しの範囲の外 |
| #3039 | 閉じのプラン: 確定仕様化が変更を作らないと pr のステップが No commits between で落ちて止まる | 範囲外 | 確定仕様化が変更を作らないときの pr のステップの扱いの欠けで、SF2 の行き先の表に「変更なし」の種類を足す設計は無い |
| #3038 | cross-refactoring: 最終ゲートが全体テストの失敗を直すために`改修計画`の対象範囲の外のファイルを変える | 範囲外 | cross-refactoring の最終ゲートが範囲の外を変える Skill の手順の誤りで、作り直しの範囲の外 |
| #3035 | 並列の計画: レーン lflow の設計承認ゲートの AskUserQuestion がオーガナイザーへ転送されず、画面で止まる | 範囲外 | レーンの `AskUserQuestion` を転送する hook の欠けで、作り直しの範囲の外 |
| #3023 | 文書だけの Pull Request の検査で、assess が文書だけと判定しても全体テストが走る | 範囲外 | assess の「文書だけ」の判定を全体テストのステップが読まない欠けで、SF2 のテンプレートの書き直しはこの分岐を足さない |
| #3022 | 検査のプランで merge の後の record が片付いた worktree の中で落ち、マージ済みなのに「止まった」になる | 範囲外 | マージの後に片付いた worktree の中で record を打つ作業場所の誤りで、作り直しの範囲の外 |
| #3021 | 並列の計画: レーンの追加の上限の検査が、終わって止まったレーンのレーン数とプラン数も数えて断る | 範囲外 | レーンの追加の上限が終わったレーンを数える誤りで、作り直しの範囲の外 |
| #3017 | mvv-gate.py: #2912 の後の指示文が、レッドライン（C2・C3・C4・C6・P1）に当たる設計を boundary 空の「従う」と判定し、承認ゲートを省ける | 範囲外 | MVV 判定の指示文の誤りで、スクリプトの作り直しでは直らない |
| #2985 | 中継: 起こしたセッションに VS Code の git 認証の環境変数が残り、窓を閉じた後の push と fetch が固まる | 範囲外 | ラッパーが VS Code の環境変数を引き継ぐ起動の環境の誤りで、作り直しの範囲の外 |
| #2984 | 手動確認: worker が確かめ終えても PR 本文の手動確認の項目に済んだ印が付かない | 範囲外 | 手動確認の印を付ける責務が手順に無い Skill の手順の欠けで、作り直しの範囲の外 |
| #2983 | 検査のプラン: 全体テストの上限が test_duration の少ない記録から決まり、実測 244〜371 秒のテストを 226 秒で打ち切る | 範囲外 | 全体テストの上限を少ない記録から決める見積もりの誤りで、作り直しの範囲の外 |
| #2970 | retrospective: メインディレクトリで打つ project-mvv.py signals が本番のブランチの古い MVV の版で兆候を数える | 範囲外 | `project-mvv.py signals` が既定の `--root` で本番のブランチを読む別の原因で、作り直しの範囲の外 |
| #2969 | 検査のプラン: マージで worktree の登録が外れ、続く record のステップが not a git repository で落ちる | 範囲外 | マージの後に片付いた worktree の中で record を打つ作業場所の誤り（#3022 と同じ）で、作り直しの範囲の外 |
| #2968 | new close: --record-pr 0 を偽として読み捨て、まとめのプランに {queue_record} が残る | 範囲外 | `close_plan` が `--record-pr 0` を偽と読む誤りで、子の範囲（`supervise_lib/`）を直す A3 は推奨案では無い |
| #2967 | new close: 進め方 normal のスプリントでも .ndf/pace.json の fast.verify を要求して止まる | 範囲外 | `route_waves` が normal でも `fast.verify` を読む誤りで、作り直しの範囲の外 |
| #2950 | mvv-gate.py: MVV 判定の記録のコメントに boundary が載らず、承認する人がレッドラインの中身を PR から読めない | 範囲外 | `mvv-gate.py` の記録のコメントが boundary を書かない欠けで、作り直しの範囲の外 |
| #2949 | supervise.py queue: プランを相対パスで渡すと {state_dir} が相対のまま埋まり、worktree の中の review-settled が失敗して検査を飛ばす | 範囲外 | `{state_dir}` を相対パスのまま埋める誤りで、作り直しの範囲の外 |
| #2935 | 設計 PR のマージのプランが、ラベル design-approved が無いと approved のステップで止まる | 範囲外 | ラベルが無いリポジトリでラベルを作らない欠けで、DC7 は名前を表から引くだけでラベルを作らない |
| #2934 | parallel.py await が答えの選択肢と note を返さず、案内の read_answer はシステムの python3 で import できない | 範囲外 | `parallel.py await` が答えの中身を返さない欠けで、SF9 はオーガナイザーの wait の行き先だけを扱う |
| #2933 | DooD の環境で cross-refactoring の作業ディレクトリ /tmp/ndf-worktrees がデーモンから見えず compose のテストが走らない | 範囲外 | DooD の環境の作業ディレクトリの置き場の問題で、作り直しの範囲の外 |
| #2932 | 開発ループ: 設計の出し直しで、`作業ツリー`のコミットが設計 PR の head に入らないまま CI の確認と cross-review へ進んで止まる | 範囲外 | 設計の出し直しで push と CI の確認の順序が逆の手順の誤りで、作り直しの範囲の外 |
| #2923 | 開発ループ: モードの判定の根拠が記録されず全周が standard になり、課題を外した後も決め直さない | 範囲外 | 開発ループのモードの判定の根拠を記録しない欠けで、作り直しの範囲の外 |
| #2898 | relay: account --help の副命令の一覧に API キーの副命令（add-api-key ほか）が出ない | 範囲外 | `relay.py account --help` の一覧の欠けで、W6 は語を替えるだけで副命令の一覧を足さない |
| #2896 | リリース後テスト: 利用者の設定の控えが、Claude Code が一時的に書く managed のプラグイン行を変化と数えて止まる | 範囲外 | 利用者の設定の控えが managed の行を変化と数える比べ方の誤りで、作り直しの範囲の外 |
| #2888 | 並列の計画: 答えの後の wait --after に answer の seq を渡す手順で、答えを待つ間に立ったリリースの知らせを読み飛ばし、一時レーンが起動されない | 範囲外 | 手順書が `answer` の seq を `wait --after` へ渡させる誤りで、SF9 の `next` は wait の結果だけに載り answer の後の値を決めない |
| #2838 | PR のマージの hook が、別のリポジトリの PR をセッションのリポジトリの delivery で判定して止める | 範囲外 | マージの hook が宛先のリポジトリの宣言を読まない判定の誤りで、作り直しの範囲の外 |
| #2722 | 開発ループ: 直して出し直す課題が棚卸しの判断待ちの印で選定から外れる | 範囲外 | `sprint_loop_select.exclusion_reason` の優先の順の誤りで、SF11 は断りに種類と `next` を足すだけ |
| #2719 | merge の CI の待ちの上限がランナーの待ちを含まず、CI が落ちていないのにプランが止まる | SF4 | マージの CI の待ちの止まりが `ci_pending`・`infra_wait` で返り、SF2 の行き先がその種類を上限の内で耐久の待ちの後に打ち直す（R08・R09） |
| #2701 | 棚卸し: 親の無い課題を含む付け直しを、親の照会の 404 を読めずに大整理の apply が 1 件も書かずに止める | 範囲外 | `_parent_of` が例外の文の `404` で親の無さを判定する誤りで、A2・A3g の直し方（まとめる・握りつぶさない）では直らない |
| #2680 | cross-refactoring の最終ゲートが、衝突して CI の始まらない Pull Request を上限まで待って止まる | 範囲外 | cross-refactoring の最終ゲートが PR の衝突を見ない欠けで、作り直しの範囲の外 |
| #2655 | 並列リリースの承認資料の「検査の結果」が常に「無し」になり、`承認ゲート 2` の MVV 判定が「判定できない」を返す | 範囲外 | 並列リリースの承認資料が検査の状態を読めない集め方の欠けで、作り直しの範囲の外 |
| #2638 | 開発ループ: 保留の`周回`を締め直す途中で出た問いを loop hold-question が「進行中の`周回`が無い」で断り、答えを当てる場所が無くなる | 範囲外 | 保留したループの 1 回の締め直しの途中の問いを受ける経路の欠けで、SF11 は断りに種類と `next` を足すだけ |
| #2637 | 並列の計画: オーガナイザーのセッションを替えると organizer の印が付かず、同時に動かす数に数えられてレーンの起動が断られる | 範囲外 | オーガナイザーの印の付け方の欠けで、コミット 4aaf8046a で一部を直し、残りは作り直しの範囲の外 |
| #2617 | 並列の計画: 耐久ワークフローを作り直すと保留中の依頼と問いを失い、その後の答えを黙って捨てる | 範囲外 | 耐久ワークフローの作り直しで依頼と問いを引き継がない欠けで、作り直しの範囲の外 |
| #2603 | MVV 改訂の兆候の集計が、worktree で打った判定の記録を数えず「判定できない」を 0 件と出す | 範囲外 | `project_mvv_signals` が worktree の記録を別のリポジトリと数える誤りで、作り直しの範囲の外 |
| #2602 | PR 本文の「残リスク: マージの前に決める」を、自動の工程が判断の記録なしでマージする | 範囲外 | PR 本文の残リスクをマージのステップが読まない欠けで、作り直しの範囲の外 |
| #2601 | 承認資料の「`配布物の変化なし`」が、`配布`されるファイルの変化を見ずに PR 本文の「利用者向けの変化: 無し」だけで付く | 範囲外 | `release_lib/included.py` がリリース対象の変化を PR 本文だけで決める誤りで、作り直しの範囲の外 |
| #2585 | 並列の計画: 起こし直しが失敗していた時間も含めて stall を数え、実際より長い「動いていない」の問いが出る | 範囲外 | stall の時間の数え方の誤りで、作り直しの範囲の外 |
| #2584 | 並列の計画: レーンが新しい版で送った依頼が、古い版のオーガナイザーの wait に届かない | 範囲外 | 版をまたいで依頼を読めない受け渡しの欠けで、作り直しの範囲の外 |
| #2583 | relay: プラグインの更新で`統括役`の版のラッパーの置き場が消え、レーンの起こし直しが何も出さずに失敗する | 範囲外 | 片付けが動いているラッパーの版の置き場を消す欠けで、作り直しの範囲の外 |
| #2576 | サブエージェントの停止の hook が最後の応答を読む前に帳簿を書き、使用量を最後の応答 1 件分少なく数える | 範囲外 | 停止の hook が最後の応答を読む前に帳簿を書く時点の誤りで、作り直しの範囲の外 |
| #2575 | 使用量の帳簿が書き手ごとに別の置き場へ書かれ、サブエージェントの行と claude -p の行を同じ集計で読めない | 範囲外 | 帳簿の置き場が書き手ごとに分かれる別の原因で、DC3 は帳簿の `RUNTIMES` を表から引くだけ |
| #2453 | 設計の結果の触るファイルが分割した設計文書へのリンクを辿らず、実装のプランから変更対象が抜ける | 範囲外 | セルのリンクを形の誤りとして止める直しが `design_results.py` に入っている（`DesignCellError`）。残りの指示文の設計文書は作り直しの範囲の外 |
| #2324 | 中継の Stop の hook が、別のセッションの承認待ちを報告しただけの応答を「本文で承認を待って終わった」と差し戻す | 範囲外 | Stop の hook の文の分類が別のセッションの報告を待ちと読む誤りで、作り直しの範囲の外 |
| #2316 | conductor どうしの排他が無い: Issue・プラン・ファイル・版数などの対象ごとにロックを置き、並列の conductor が同じ物を同時に書き換えないようにする | 範囲外 | conductor どうしの排他の仕組みが無い欠けで、作り直しの範囲の外 |
| #2273 | 設計 PR のマージのプランがベースブランチとの衝突（DIRTY）で取り込みへ戻らず止まり、並行の設計 PR の用語集の衝突を手で解く | SF2 | テンプレートを `on_cause` で書き直し、`base_conflict` の既定の行き先を取り込みのステップにする（R10）ため、設計 PR のマージの衝突が取り込みへ戻る |
| #2176 | 文書検査: mermaid の構文を解析せず、GitHub で描画できない図が`承認ゲート 1` まで届く | 範囲外 | 文書の検査が mermaid の構文を見ない欠けで、作り直しの範囲の外 |
| #2158 | cross-refactoring: 着手前のテストが上限の近くで通ると、`範囲テストの雛形`の試走の残り時間が数秒になり init が止まる | 範囲外 | 範囲テストのテンプレートの試走の上限の決め方の誤りで、作り直しの範囲の外 |
| #2140 | リリースのプランの verify が、メインディレクトリが本番ブランチにいると develop を fast-forward できずに止まる | 範囲外 | リリースのプランの verify がメインディレクトリで pull する手順の誤りで、作り直しの範囲の外 |
| #2118 | 実装のプランの design-tests が、設計で改名を決めたテストを止まりと判定せず、止まった理由を全文で残す | 範囲外 | `design-match` が改名を止まりと判定しない誤りで、作り直しの範囲の外 |
| #2114 | document-rebuild: 読み手役を外部の CLI で立てるときに、止まらず文脈を持たない形で起動できるようにする | 範囲外 | `document-rebuild` の読み手役の起動の形が書かれていない Skill の手順の欠けで、作り直しの範囲の外 |
| #2113 | document-rebuild: 未決の問いがある提案書の冒頭の章が、組み直しと執筆の決まりで食い違わないようにする | 範囲外 | `document-rebuild` の 3 つの文書が食い違う Skill の手順の誤りで、作り直しの範囲の外 |
| #2096 | cross-refactoring: 全体テストを CI に任せる戦略の着手前のテストがディレクトリを {paths} へ入れ、ファイルだけを受けるテンプレートで落ちる | 範囲外 | cross-refactoring の着手前のテストがディレクトリを `{paths}` へ入れる誤りで、作り直しの範囲の外 |
| #2078 | system-repo.md の記録の説明に Marp と比較の言い回しが残り、#2065 の受け入れ条件 15・16 が通らない | 範囲外 | Skill の参照文書の言い回しの誤りで、スクリプトの作り直しでは直らない |
| #2067 | gws-check.py がトークンの無い状態（auth_method: none）を認証済みと判定して exit 0 を返す | 範囲外 | `gws-check.py` の認証の判定の誤りで、A3g の直し方（握りつぶさない・既定値へ倒さない）では直らない |
| #2037 | supervise queue の重なりの判定がファイル単位のため、設計が関数単位で分けた実装のプランも直列になる（スプリント a1 で 58 分が 2 時間 47 分） | 範囲外 | キューの重なりの判定がファイル単位の決まりで、作り直しの範囲の外 |
| #2028 | Skill を足す設計の触るファイルに Skill の数を持つ箇所が入らず、実装のステップが範囲外の失敗で止まる | 範囲外 | Skill の数を持つ箇所が設計の触るファイルに入らない手順の欠けで、作り直しの範囲の外 |
| #1984 | supervise が worker の`途中の報告`の「止まった」の語に反応し、作業の中身（止まったものとみなす実装）でも conductor へ attention を出す | 範囲外 | 進捗ログの語で attention を出す `classify_worker` の誤りで、SF7 は attention に種類を足すだけで語の分類を外さない |
| #1979 | 使用量の帳簿が同じ message.id の最後の usage を捨て、続けたサブエージェントを 2 回数え、日をまたぐ会話を丸ごと採るか落とす → 会話の記録の合計と一致させる | 範囲外 | 使用量の帳簿の数え方の誤りで、作り直しの範囲の外 |
| #1978 | usage-report.py の閉じた課題の数が UTC の日付で数えられ、使用量の集計（JST の日付）と期間がずれる → JST の両端で数える | 範囲外 | `usage_aggregate.closed_issues` の日付の解釈の誤りで、作り直しの範囲の外 |
| #1958 | cross-review の修正の worker が /ndf:fix の手順書を持たない最小構成で起動し、1 回 503k・16 往復を使う → 手順書を確実に読ませ、1 回の費用を実測で下げる | 範囲外 | 修正の worker の起動の構成が手順書を読ませない欠けで、作り直しの範囲の外 |
| #1877 | relay: /goal の続きの応答では stop_hook_active が真になり、背景の作業が残る ndf-next の応答で Stop を止めて知らせる仕組みが働かない | 範囲外 | `/goal` の続きで `stop_hook_active` が真になる hook の判定の欠けで、作り直しの範囲の外 |
| #1811 | supervise: 設計の置き場所のテストのディレクトリが重なりの判定に当たり、スプリントの実装のプランがすべて直列になる | 範囲外 | 重なりの判定がディレクトリの包含を重なりと読む決まりで、作り直しの範囲の外 |
| #1774 | CI のチェックの結論を失敗と数える集合が lib/gh_checks.py と merged_lib/checks.py で別々にあり、取り消しと stale の扱いが食い違う | DC4 | `review_lib/ci.py` の写しを消して持ち主 `lib/gh_checks.py` から引く。`merged_lib/reading.py` の集合は DC4 の行に無く、着手の時点で同じ持ち主へ寄せるかを決める |
| #1756 | release: plugin_dir と changed-plugins がプラグインの置き場を ndf・playwright-kit の名前で決め打ち、他のリポジトリで package-plugin の手順が止まる | 範囲外 | プラグインの置き場を名前で決め打つ誤りで、data の事実の表に置き場の行は無く作り直しの範囲の外 |
| #1755 | CHANGELOG の ndf 10.17.61 の節から移行の手順が消え、利用者が認証のし直しを知れない | 範囲外 | `release-steps.py notes` が版の節を差し替える誤りで、作り直しの範囲の外 |
| #1715 | plan-to-spec: spec-finalize が --spec を 1 つしか受けず、既存の複数ファイルの確定仕様へ溶かすと残りのファイルがコミットに入らないまま ok で終わる | 範囲外 | `spec-finalize` が `--spec` を 1 つしか受けない欠けで、作り直しの範囲の外 |
| #1707 | cross-review: ai-plugins 以外のリポジトリで SKILL.md のとおりに単独起動すると、Skill のディレクトリが git リポジトリでないため PR を取れずに止まる | 範囲外 | cross-review の Skill の手順と cwd の決め方の食い違いで、作り直しの範囲の外 |
| #1659 | cross-refactoring: docs が SKILL.md にあるとする rf / rf_eval の定義が無く、自作すると中断が効かない形を踏みうる | 範囲外 | cross-refactoring の文書が無い定義を指す Skill の手順の誤りで、作り直しの範囲の外 |
| #1653 | supervise: pr のステップの push が一時的な拒否（missing necessary objects）で落ちても再試行せずプランを止める | 範囲外 | push の一時的な拒否の原因が分かっておらず、SF2 の `push_rejected` は pre-push の hook の拒否だけを直すステップへ送る |
| #1595 | backlog-refinement: apply が plan の rejected を依存先として一致した別の候補の値で記録し、却下が効かない | 範囲外 | `RankPlan.candidate` の照合の誤りで、作り直しの範囲の外 |
| #1516 | supervise.py のフェーズレポートが、設計の`関門 1` でも「`関門`: `本番の系`へ届く操作」と書く | 範囲外 | `state.py` がゲートの種類を見ずに固定の文を書く誤りで、W1 は語を `report_fields` へ集めるだけでゲートの種類を見分けない |
| #1502 | mvv-gate: 設計のプランで、設計の前に打った requirements-check の exit 2 を材料に読み、`関門 1` の助言が unknown になる | 範囲外 | `on_fail` で分岐したステップの終了コードを本文が最終の結果として載せる誤りで、作り直しの範囲の外 |
| #750 | cross-refactoring: 提案担当の codex が結果ファイルを提出先に書けず NO_RESULT になる（提出先が読み取り用ディレクトリの外） | 範囲外 | cross-refactoring の提出先が読み取り用ディレクトリの外にある起動の形の誤りで、作り直しの範囲の外 |
| #620 | cross-refactoring: テストを足す項目（add-tests）が pytest の収集しないファイルへテストを足し、実行されないまま検証を通る | 範囲外 | add-tests が収集されないファイルへテストを足す誤りで、作り直しの範囲の外 |
