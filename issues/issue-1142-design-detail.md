# #1142 の詳細設計: 主題をまたぐ部品・実装計画・開いている bug の割り当て

> **2026-11-14 に実装計画を置き換えた。** 「実装計画」「流す順序」「レーンへの割り当ての案」は [issue-1142-design-next-design-detail.md](issue-1142-design-next-design-detail.md) の表が持つ。この文書の残りの節は材料として読む。

設計文書 [issue-1142-design.md](issue-1142-design.md) の 3 つの主題と前の企画の残りを、子 Issue と流す順序へ下ろした文書である。書き方は `plugins/ndf/skills/design/references/detailed-design.md` にある。要求は [issue-1142-requirements.md](issue-1142-requirements.md) にある。

**この文書は主題をまたぐものだけを書く。** 主題の中のファイルと関数は、主題の詳細設計が持つ。

| 主題 | 詳細設計 | 子 Issue |
| --- | --- | --- |
| smells | [issue-1142-design-smells-design-detail.md](issue-1142-design-smells-design-detail.md) | A0・A1a・A1b・A2・A3a・A3b・A3c-1・A3c-2・A3d・A3e-1・A3e-2・A3f-1・A3f-2・A3g |
| data | [issue-1142-design-data-design-detail.md](issue-1142-design-data-design-detail.md) | DC1〜DC8（設計文書の移行ステップ DT1〜DT8 との対応は data の詳細設計の冒頭の表） |
| scripted-flow | [issue-1142-design-scripted-flow-design-detail.md](issue-1142-design-scripted-flow-design-detail.md) | SF1a・SF1b・SF2〜SF13（SF3 は既存の #3153） |
| 前の企画の残り | [issue-1142-design-base-design-detail.md](issue-1142-design-base-design-detail.md) | K1〜K3・W1〜W8（旧 K4 は A1a にまとめた） |
| リファクタリング計画の 8 件 | [issue-2963-design-detail.md](issue-2963-design-detail.md) | RF1・RF1b・RF2・RF3・RF4・RF5a・RF5b・RF6a・RF6b・RF7・RF8 |
| ラベル `rebuild-1142` の残り | [issue-1142-design-fixes-design-detail.md](issue-1142-design-fixes-design-detail.md) | N1・N2a・N2b・N3〜N7・N8a・N8b・N9〜N12 |

子 Issue は 72 本で、22 の順番に分けて流す。ラベル `rebuild-1142` の開いている Issue 283 件のうち 277 件を子 Issue の変更で閉じ、6 件は閉じられない（行き先の表は [issue-1142-design-labels.md](issue-1142-design-labels.md) ）。

**この文書で決めたこと（conductor の決定を写したもの）。**

- 旧 K4（撤去）は A1a にまとめる。どちらも `legacy_names.py`・`phase-steps.py`・`bundle-close.py` を消すため。K4 の残り（`post_queue.py` の取り込み・契約の 3 項目・構造チェックの除外）も A1a が持つ
- W1 の `lib/report_fields.py` と data の R3 は食い違わない。R3 は事実の表（`contracts/facts/`）へ移さないという意味で、W1 は 1 つのコンテキストだけが読む語を持ち主のモジュールに置く
- #3191（#3092 の実装）が工程の失敗を終了コード 6 で返す点は、#1142 の IS11（終了コードは止まった理由の種類から引く）を正とし、SF8 で合わせる
- リファクタリング計画の 8 件は、設計承認ゲート（2026-11-13）で推奨どおり #1142 の子 Issue に取り込み、別の設計 PR で設計すると決まった（設計文書の「人へ残す判断」の (d)）。8 件は #2963 の設計 PR で 11 本の子 Issue（`RF*`）にし、下の「実装計画」の表と「流す順序」へ入れた
- #3191 が要る DC7・SF8 は、先に要るものに #3191 を書き、後ろの順番へ置く
- ラベル `rebuild-1142` の Issue（2026-11-13 利用者の受け入れ条件）は、既にある子 Issue の範囲に入るものをその子へ足し（触るファイルを設計の結果と主題の詳細設計に足した）、入らないものを 14 本の新しい子 Issue（N1〜N12）にした。設計は [issue-1142-design-fixes.md](issue-1142-design-fixes.md) にある
- SF2 は解消する Issue が 15 件を超えるため、雛形のステップ固有の 9 件を N11 へ分けた（ラベルの残りの設計文書の決定 71）

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

流す順番（「実装計画」の表）を決める規則である。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| 順番の決め方 | 子 Issue に先に要るもの（主題の詳細設計の列と、#3191）がある | 先に要るものの順番の後の順番へ置く |
| 順番の決め方 | 2 つの子 Issue が同じファイルを触る（ディレクトリは包含で見る。生成物 `contracts/index.md` は除く） | 次の優先の順で前の子 Issue を先の順番へ置き、2 つを同じ順番に置かない |
| 優先の順 | 主題をまたいで重なる | SF3 → A0 → SF1a → DC1 → A1a → A1b → A2 → SF1b → DC2〜DC6 → K3 → K1 → SF2・SF4・SF5・SF9〜SF12・SF7・SF6・SF13 → N1〜N12（ラベルの残り） → A3a〜A3g → DC8 → DC7 → K2 → SF8 → W1〜W7 → W8 |
| 優先の順の理由 | 古い経路を消す A1a・A1b | 子 Issue が移すコードから先に経路を除く（smells の決定 43） |
| 優先の順の理由 | 種類の契約 SF1b | 種類を返す 127 本を先にそろえ、A3 の直し方（握りつぶさずに返す）が種類を使える |
| 優先の順の理由 | A3a〜A3g | ファイルごとに中身を直すため、事実を移す DC と行き先を変える SF の後に置き、同じファイルを 2 度直さない |
| 優先の順の理由 | N1〜N12 | 関数の中身を直すため、事実を移す DC・行き先を変える SF・リファクタリング計画の RF の後、兆候だけを直す A3 と語だけを替える W の前に置く（ラベルの残りの設計文書の決定 70） |
| 優先の順の理由 | W1〜W7 | 語だけを替えるため最後に置き、先の子 Issue が足した文も同じ PR で替える |
| 優先の順の理由 | DC7・SF8 | #3191 のマージを待つため後ろへ置く |
| E9（測り直し） | すべての子 Issue のマージの後 | `scripts/measure/ai-smells.py`・`const-facts.py --json`・`ai-branching.py` を打ち、前後の数を #1142 のコメントへ残す |

- 繰り返しの終わる条件: 子 Issue は 72 本で有限。順番は先に要るものと重なりの有向グラフの最長の道で決まり、輪は無い（優先の順が全順序のため）
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
| 同じ順番の子 Issue の PR | 触るファイル | 取らない。同じ順番には触るファイルが重ならない組だけを置く（「流す順序」の確かめ） | 順番の途中で止まった子 Issue は、同じ順番のほかの子 Issue を止めない |
| 順番をまたぐ子 Issue の PR | 重なるファイル | 前の順番の PR のマージを待ってから、後の順番の子 Issue を起動する（lrebuild が見張る） | 後の順番の PR は base-sync で前の順番の変更を取り込んでから直す |
| DC2〜DC8・SF1a の PR | 生成物 `plugins/ndf/contracts/index.md` | 取らない。後からマージする PR が base-sync の後に `python3 scripts/check-ndf-contracts.py --render` で書き直す | 書き直しは何度打っても同じ中身 |
| A0 の後のすべての PR | 兆候の基準ファイル `scripts/ai-smells-baseline/` | 取らない。後からマージする PR が base-sync の後に `check-ai-smells.py --write-baseline` を打ち直す（smells の詳細設計の「並行と排他」） | 今の数で上書きするため冪等 |
| DC2 の後の DC の PR | 重複の基準 `scripts/const-fact-baseline.json` | 取らない。直した組の行を自分の PR で消す（data の詳細設計） | 行を消すだけ |
| W1 のマージと、流れているプラン・キュー | 結果の値（`関門`） | 取らない。#1142 の実装の間はほかの開発を止める（決定 65） | W1 の前に止まったプランは、W1 の後に頭から流し直す |
| #3191 のマージと DC7・SF8 | `plugins/ndf/scripts/lib/workflow_def.py`・`progress_*.py` | DC7・SF8 は #3191 のマージの後に起動する | #3191 の実装の名前と突き合わせてから起動する |

**順序を破ったときの壊れ方。** 重なる 2 本を同じ順番に置くと、後からマージする PR が衝突し、`base_conflict` で止まる。先に要るものより先に流すと、検査が使う依存（`jsonschema`）や行き先の経路（`causes.json`）がまだ無く、検査か工程が止まる。

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| 各子 Issue の PR | 「この子で解消する Issue」の `Closes` の行と、その Issue の再現の手順を 1 回通した結果 | PR 本文 | 再現できなかった Issue の `Closes`（理由を書いて開いたまま残す） |
| A3a〜A3g・W1〜W7 の PR | 着手の時点で数え直したファイルの一覧（A3 は `check-ai-smells.py`、W は用語チェックと同じ正規表現） | PR 本文 | 一覧の外のファイルの変更 |
| lrebuild の見張り | 順番ごとの開始・マージの時刻と、次の順番の子 Issue の起動 | 並列の計画の台帳（`parallel.py` の記録） | 子 Issue の中身の判断 |
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

子 Issue は 72 本である。触るファイルの全体は設計文書（`RF*` は #2963 の設計文書、`N*` はラベルの残りの設計文書）の「設計の結果」の表にあり、`design-survey.py check` が主題の詳細設計のファイル配置の表と照合する。PR は子 Issue ごとに 1 本で、その revert 1 つで 1 つ前に戻る（I10）。A3a〜A3g と W1〜W7 の触るファイルは、2026-11-13（7da134096）の一覧で、着手の時点で数え直す。

| 子 Issue | 主題 | 中身 1 文 | 触るファイル（数と、主題の詳細設計の節への参照） | 先に要るもの | 並列の組 | PR の単位 | 見直す前の企画の設計文書の節 | 消す古いテスト | この子で解消する Issue |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `A0` | smells | 検査・規則・基準を入れ、兆候を増やす変更を CI で落とす | 9 本。smells の詳細設計の「ファイル配置」 | 無し | 組 S | 1 本 | 無し | 無し | — |
| `A1a` | smells | 旧名を読み替える経路と、旧 K4 の撤去（呼び出し元の無いエントリポイント・投稿キューの移行の前のファイルの取り込み）を消す | 45 本。smells の設計文書の「直す順序と触るファイル」の A1a の行と前の企画の詳細設計の「（A1a。旧 K4）」の行 | `A0` | 組 S | 1 本 | 移行の文書の「撤去（3 件）」・決定 65 | `test_legacy_names.py`・`test_gate_names.py`、`test_step_scripts.py` の phase-steps の節、`test_post_queue.py` の取り込みのテスト | — |
| `A1b` | smells | 古い値を読み替える経路を消す | 20 本。smells の設計文書の「直す順序と触るファイル」の A1b の行 | `A1a` | 組 S | 1 本 | 決定 65 | `test_legacy_values.py` | — |
| `A2` | smells | 範囲の外のループの中の外部呼び出しを 1 回のクエリへまとめるか上限の印を付ける | 23 本。smells の設計文書の「直す順序と触るファイル」の「A2 の 22 本」 | `A0` | 組 S | 1 本 | 無し | `test_merged_probe.py` の head ごとに `gh pr list --head` を呼ぶ場合 | — |
| `A3a` | smells | `scripts/lib/` の a〜fの握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 42 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | #2791・#3115 |
| `A3b` | smells | `scripts/lib/` の g〜oの握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 43 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `A3c-1` | smells | `scripts/lib/` の p〜z の前半の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 26 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `A3c-2` | smells | `scripts/lib/` の p〜z の後半の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 26 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | #2933・#2983 |
| `A3d` | smells | `scripts/hook_lib/`・`scripts/relay_lib/`の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 34 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | #2789・#2898・#2962・#2985・#3035 |
| `A3e-1` | smells | `scripts/` の直下と下位の 5 つのうち名前の前半の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 30 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | #2838・#3077・#3087・#3187 |
| `A3e-2` | smells | 同じく後半の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 28 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | #2424・#2970・#3167 |
| `A3f-1` | smells | `skills/cross-refactoring/scripts/` の前半の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 35 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `A3f-2` | smells | 同じく後半の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 34 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | — |
| `A3g` | smells | cross-refactoring を除く Skill の `scripts/`の握りつぶし・フォールバック・分岐の多い判定・長い関数を直す | 50 本。smells の設計文書の「直す順序と触るファイル」の A3 の表（7da134096 の兆候のあるファイル） | `A0` | 組 S | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合（smells の詳細設計の「単体テストの観点」の後の表） | #2067・#2447・#2701・#3181 |
| `DC1` | data | 事実の表の読み手（`lib/facts.py`・`lib/facts.sh`）と表の共通の形、依存 `jsonschema` を足す | 13 本。data の詳細設計の「ファイル配置」 | 無し | 組 D | 1 本 | 無し | 無し | #2235・#2339 |
| `DC2` | data | 重複の検査 `const-fact`・`fact-table` と基準を入れる（DT1 の後半・DT7） | 15 本。data の詳細設計の「ファイル配置」 | `DC1`、`RF3` | 組 D | 1 本 | 無し | 無し（計測のスクリプトにテストは無い） | — |
| `DC3` | data | ランタイムの表を置き、読み手を表から導く | 21 本。data の詳細設計の「ファイル配置」 | `DC1` | 組 D | 1 本 | 無し | `test_state_review_pool.py` の消す分岐のテスト 2 件と、ランタイムの文字列の集まりを確かめる assert | — |
| `DC4` | data | 収束ループのフェーズ・指摘の重さ・テストの印の表を置く | 21 本。data の詳細設計の「ファイル配置」 | `DC1` | 組 D | 1 本 | 無し | `test_measure.py:687` の一致の確認 | #1339・#1774・#1777・#2449・#2557 |
| `DC5` | data | 閾値を上限の宣言へ移す | 16 本。data の詳細設計の「ファイル配置」 | `DC3` | 組 D | 1 本 | 無し | `test_relay.py:2885`・`test_queue_idempotency.py:52`・`test_testenv_reclaim.py:862` の一致の確認 | — |
| `DC6` | data | 宣言の置き場・環境変数名・PR 本文の見出し・指示書の表を置く | 22 本。data の詳細設計の「ファイル配置」 | `DC1` | 組 D | 1 本 | 無し | 置き場・指示書・見出しの文字列の集まりだけを確かめる assert | #2010・#2011・#2434・#2600・#3093 |
| `DC7` | data | 工程とフェーズ（`metadata.ndf.phase`）・モード・進め方・ブランチの接頭辞・承認ゲートを表と工程の文書から引く | 28 本。data の詳細設計の「ファイル配置」 | #3191、`DC5`・`DC6`・`A1a`・`A1b` | 組 D | 1 本 | 移行の文書の「値として読まれる古い語」の R2 | `test_transcript_agents.py` の 2 件と development-workflow の定義の文字列を読むテスト | #1516・#2935 |
| `DC8` | data | 子 Issue の後に残る組（`sprint_loop_record`・`release_lib`・`parallel_*`・`durable`・`decl`）を持ち主から引く | 11 本。data の詳細設計の「ファイル配置」 | `DC1`、`RF1`・`RF5a`・`RF6a`・`RF6b`・`RF7`・`RF8` | 組 D | 1 本 | 無し | 持ち主へ寄せた定数の一致を確かめる assert | — |
| `SF3` | scripted-flow | CI の監視が分割ジョブの失敗とまとめのチェックの未完了を読み違える不具合を直す（#3153） | 7 本。scripted-flow の設計文書の「子 Issue の案」の SF3 の行 | 無し | 組 F | 1 本 | 無し | `test_ci_watch.py` の `decide_exit` の `after_merge` の関数 | #2224・#2317・#3153 |
| `SF1a` | scripted-flow | 止まった理由の表 `causes.json` と読み手 `lib/causes.py` を契約に置く | 9 本。scripted-flow の設計文書の「子 Issue の案」の SF1a の行 | 無し | 組 F | 1 本 | 無し | 無し | — |
| `SF1b` | scripted-flow | 結果 JSON の `cause` を必須にし、`stopped` を出すすべての手順のスクリプトが種類を返す | 135 本。scripted-flow の設計文書の「子 Issue の案」の SF1b の行と、詳細設計の「SF1b の移す先」の 127 本 | `SF1a`、`RF4`・`RF5a`・`RF5b`・`RF6a`・`RF6b`・`RF7`・`RF8`・`RF1b` | 組 F | 1 本 | 無し | `test_step_result.py`・`test_step_result_readers.py`（ファイルごと）と `test_run_core.py` の関数 | #2337・#2340 |
| `SF2` | scripted-flow | プランの行き先を種類で選び、待てば解ける止まりを打ち直す。テンプレートを `on_cause` で書き直す | 48 本。scripted-flow の設計文書の「子 Issue の案」の SF2 の行（テンプレートの 11 本は詳細設計の「表の外にまとめたファイル」） | `SF1b`、`RF4`・`RF5a` | 組 F | 1 本 | dbos の文書の「落ちた後の打ち直し（実測）」・決定 27 | `test_supervise_step_rerun.py`・`test_push_fix.py`・`test_supervise_limits.py`（ファイルごと）と 19 本の関数 | #1653・#1682・#1983・#2273・#2349・#2680・#2752・#2844・#2867・#3023・#3039 |
| `SF4` | scripted-flow | マージの落ちたチェックをテストとテスト以外に分け、supervisor が CI を手で読む手順を外す | 10 本。scripted-flow の設計文書の「子 Issue の案」の SF4 の行 | `SF2`・`SF3` | 組 F | 1 本 | 無し | `test_ci_watch.py` の終了コード 2・3 の関数・`test_merged_probe.py` の CI の失敗の関数 | #2041・#2602・#2690・#2719・#3188 |
| `SF5` | scripted-flow | 設計のプランの最後の judge を外し、レビューの結果で設計承認ゲートへ渡す | 7 本。scripted-flow の設計文書の「子 Issue の案」の SF5 の行 | `SF2` | 組 F | 1 本 | 無し | 無し | #2444・#2853・#2932 |
| `SF6` | scripted-flow | 判断の呼び出しの答えを選択肢か JSON のスキーマで縛る | 10 本。scripted-flow の設計文書の「子 Issue の案」の SF6 の行 | `SF2`・`SF4`・`DC1`、`RF4`・`RF5a` | 組 F | 1 本 | 無し | `test_slow_step.py`・`test_supervise_slow.py` の答えを読めないときに `wait` へ倒す関数 | — |
| `SF7` | scripted-flow | プランの wait が attention を種類つきで返し、次のコマンドを `next` に載せる | 4 本。scripted-flow の設計文書の「子 Issue の案」の SF7 の行 | `SF1b`・`SF2`、`RF4`・`RF5a` | 組 F | 1 本 | dbos の文書の「`queue`: ステージ・重なり・資源の枠」 | `test_queue_waits.py`（作り直す） | — |
| `SF8` | scripted-flow | 進行表が工程の失敗の種類から行き先を選び、conductor へ `routes` だけを返す。工程の失敗の終了コード 6 を IS11 に合わせる | 5 本。scripted-flow の設計文書の「子 Issue の案」の SF8 の行と `lib/workflow_def.py`（語彙に `on_cause` を足す。設計文書の「進行表（#3092）との受け渡し」） | #3191、`SF2`・`SF13`、`RF1`・`RF1b` | 組 F | 1 本 | —（#3092 の設計） | 無し | — |
| `SF9` | scripted-flow | 並列の計画の待ちの結果の行き先をスクリプトが選ぶ | 7 本。scripted-flow の設計文書の「子 Issue の案」の SF9 の行 | `SF1b`、`RF6a`・`RF6b` | 組 F | 1 本 | 無し | 無し | #2554・#2751・#2781・#2822・#2888・#2934 |
| `SF10` | scripted-flow | 手順 0 の 3 つの確かめを 1 回の結果 JSON にまとめる | 4 本。scripted-flow の設計文書の「子 Issue の案」の SF10 の行 | `SF1b` | 組 F | 1 本 | 無し | 無し | — |
| `SF11` | scripted-flow | 開発ループのサブコマンドの断りと止まりが種類と `next` を返す | 4 本。scripted-flow の設計文書の「子 Issue の案」の SF11 の行 | `SF1b`、`RF7` | 組 F | 1 本 | 無し | `test_sprint_loop.py` の終了コードと `metrics.blocked` を見る関数 | — |
| `SF12` | scripted-flow | 会話の切れ目と中断の点検の行き先を結果で返す | 5 本。scripted-flow の設計文書の「子 Issue の案」の SF12 の行 | `SF1b`・`DC5` | 組 F | 1 本 | 無し | `test_token_guard.py` の案内の文を見る関数 | #2595 |
| `SF13` | scripted-flow | 承認ゲートの結果に答えごとの `routes` を必須で足し、問いの区分と選択肢を結果に載せる | 19 本。scripted-flow の設計文書の「子 Issue の案」の SF13 の行 | `SF1b`・`SF5`・`SF9`、`RF5a`・`RF5b` | 組 F | 1 本 | 無し | 無し | #2147・#2639・#2950 |
| `K1` | 前の企画 | Markdown のコードブロックの判定をアダプター `md` へ移す | 16 本。前の企画の詳細設計の「ファイル配置」の K1 の行 | `SF3`・`DC5`、`RF1b` | 組 B | 1 本 | libraries の文書の「実装の状態と残り」・決定 68 | `test_gh_parts.py` のコードブロックの現状固定のテスト・`test_wait_notify.py` の地の文の行のテスト | — |
| `K2` | 前の企画 | パスの照合とコメントの除去をアダプターへ移し、`comment_mask.py` を消す | 15 本。前の企画の詳細設計の「ファイル配置」の K2 の行 | `K1`・`DC7`、`RF5a` | 組 B | 1 本 | libraries の文書の「実装の状態と残り」・決定 68 | `test_pace.py` の照合のテスト | — |
| `K3` | 前の企画 | HTTP を `notify` の httpx へ移す | 11 本。前の企画の詳細設計の「ファイル配置」の K3 の行 | `RF8` | 組 B | 1 本 | libraries の文書の「実装の状態と残り」・決定 68 | `test_refresh.py`・`test_instructions_check.py` の opener のテスト・`test_jev.py` の `_post` のテスト | — |
| `W1` | 前の企画 | プランの実行の語（フェーズの結果の語を `report_fields` に集める）: 用語集の廃止語を生きた語へ替える | 66 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W1 の行 | 触るファイルの重なる子のすべて（流す順番で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W2` | 前の企画 | cross-review の語: 用語集の廃止語を生きた語へ替える | 28 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W2 の行 | 触るファイルの重なる子のすべて（流す順番で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W3` | 前の企画 | 外部 CLI と記録の語: 用語集の廃止語を生きた語へ替える | 16 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W3 の行 | 触るファイルの重なる子のすべて（流す順番で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W4` | 前の企画 | cross-refactoring の語: 用語集の廃止語を生きた語へ替える | 42 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W4 の行 | 触るファイルの重なる子のすべて（流す順番で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W5` | 前の企画 | worktree と hook の語: 用語集の廃止語を生きた語へ替える | 27 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W5 の行 | 触るファイルの重なる子のすべて（流す順番で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W6` | 前の企画 | ラッパーの語: 用語集の廃止語を生きた語へ替える | 11 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W6 の行 | 触るファイルの重なる子のすべて（流す順番で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W7` | 前の企画 | リリースと文書の語: 用語集の廃止語を生きた語へ替える | 42 本。前の企画の詳細設計の「W1〜W7 の触るファイル」の W7 の行 | 触るファイルの重なる子のすべて（流す順番で決まる） | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」・決定 65・67 | 無し（字面を写す assert だけ直す） | — |
| `W8` | 前の企画 | W1〜W7 で外した廃止語を禁止語にする | 1 本。`.ndf/glossary.json` の 1 本 | `W1`〜`W7` | 組 B | 1 本 | 移行の文書の「スプリント 3 の行き先」 | 無し | — |
| `RF1` | リファクタリング計画 | 判断のループと判断の関数の型を置き、耐久の層で回す代表のテストと DBOS を起こさないテストを足す（#2990） | 3 本。#2963 の設計文書の「子 Issue の行」と、#2963 の詳細設計の「ファイル配置」 | 無し | 組 R | 1 本 | 無し | 無し | — |
| `RF1b` | リファクタリング計画 | 耐久の層の利用者のうち 8 件の組み直しが触らない 4 本の兆候を直す（#2990 から分ける） | 11 本。#2963 の設計文書の「子 Issue の行」と、#2963 の詳細設計の「ファイル配置」 | `A1a`・`RF2` | 組 R | 1 本 | 無し | 握りつぶし・既定値への倒れを期待値に持つ場合 | #2640・#2827・#3053 |
| `RF2` | リファクタリング計画 | 行数の上限を 1,000 の安全網にし、遅延の輪・判断のモジュールの純粋さ・2 つの耐久の層の境界を構造の検査の規則にする（#2991） | 6 本。#2963 の設計文書の「子 Issue の行」と、#2963 の詳細設計の「ファイル配置」 | `A1a` | 組 R | 1 本 | 決定 18（行数の上限） | `test_import_cycles.py`（ファイルごと） | — |
| `RF3` | リファクタリング計画 | 2 本の検査の構文木の解析を 1 回の実行で 1 回にする（#2996） | 9 本。#2963 の設計文書の「子 Issue の行」と、#2963 の詳細設計の「ファイル配置」 | `RF2` | 組 R | 1 本 | 無し | 無し | — |
| `RF4` | リファクタリング計画 | プランの遷移とキューの前提の判断を判断の関数へ移す（#2963） | 12 本。#2963 の設計文書の「子 Issue の行」と、#2963 の詳細設計の「ファイル配置」 | `RF1`・`RF2`・`A1b` | 組 R | 1 本 | 無し | `drive()` を使う 4 ファイルのうち遷移だけを見るテスト | #3086 |
| `RF5a` | リファクタリング計画 | `engine`・`flow`・`queue` の輪をほどき、プランの組み立てと再開・重なりの判断を判断の関数へ移す（#2993） | 17 本。#2963 の設計文書の「子 Issue の行」と、#2963 の詳細設計の「ファイル配置」 | `RF4` | 組 R | 1 本 | dbos の文書の「`queue`: ステージ・重なり・資源の枠」 | `test_supervise_queue.py`・`test_supervise_resume.py` のうち判断だけを見るテスト | #1886・#2876・#2949・#3047・#3114・#3176 |
| `RF5b` | リファクタリング計画 | スプリントの順番の組み方と閉じのプランの判断を判断の関数へ移す（#2993 から分ける） | 8 本。#2963 の設計文書の「子 Issue の行」と、#2963 の詳細設計の「ファイル配置」 | `A1a`・`RF2` | 組 R | 1 本 | 無し | `test_supervise_sprint_chain.py`・`test_supervise_close_auto.py` のうち判断だけを見るテスト | #2967・#2968 |
| `RF6a` | リファクタリング計画 | 並列の計画の耐久の層の判断を判断の関数へ移し、輪 L3 をほどく（#2992） | 18 本。#2963 の設計文書の「子 Issue の行」と、#2963 の詳細設計の「ファイル配置」 | `RF1`・`RF2`・`A1b`・#2936（マージ済み） | 組 R | 1 本 | 無し | `test_parallel_run.py`・`test_parallel_answer.py` のうち判断だけを見るテスト | #2441・#2585・#2730・#2783・#2834・#3020・#3096・#3128・#3136・#3172・#3174 |
| `RF6b` | リファクタリング計画 | 計画の上限・置き場・レーンの判断を判断の関数へ移し、輪 L7・L9 をほどく（#2992 から分ける） | 15 本。#2963 の設計文書の「子 Issue の行」と、#2963 の詳細設計の「ファイル配置」 | `RF6a` | 組 R | 1 本 | 無し | `test_parallel_plan.py`・`test_parallel_lane.py`・`test_parallel_external.py` のうち判断だけを見るテスト | #2831・#2999・#3021・#3064・#3118・#3145 |
| `RF7` | リファクタリング計画 | 周の結果と選定の除外を判断の関数へ移し、周の結果をリリースの事実から導く（#2994） | 14 本。#2963 の設計文書の「子 Issue の行」と、#2963 の詳細設計の「ファイル配置」 | `A1b`・`RF2` | 組 R | 1 本 | 無し | `test_sprint_loop.py` のうち判断だけを見るテスト | #2132・#2400・#2435・#2714・#2722・#2746・#2788・#2820・#2923 |
| `RF8` | リファクタリング計画 | `release-steps.py` の副命令を段階ごとのモジュールへ分ける（#2995） | 15 本。#2963 の設計文書の「子 Issue の行」と、#2963 の詳細設計の「ファイル配置」 | `RF2` | 組 R | 1 本 | 無し | `test_release_steps.py` のうち段階の判断だけを見るテスト | #1755・#1756・#2217・#2289・#2601・#2655・#2896・#3110・#3169 |
| `N1` | ラベルの残り | プランとループの 1 回が今の起動の外にある成果（版・記録の PR・前のステージの PR・検査の件数・問いの当て先・再開の位置・設計の起点）を、流す直前に参照の部品で解決し、解決できなければ種類つきで止まる | 26 本。ラベルの残りの詳細設計の「ファイル配置」 | `SF1a`・`SF2`・`RF4`・`RF5a`・`RF5b`・`RF7`・`RF1b`・`SF1b`・`SF11` | 組 N | 1 本 | 無し | plugins/ndf/scripts/tests/test_release_version.py のうち queue_fill を直に呼ぶ関数（17 か所。refs の version の種類のテストへ作り直す）、test_record_find.py・test_lane_prod_stage.py ほか（詳細設計） | #1592・#2344・#2408・#2489・#2528・#2568・#2569・#2638・#2648・#2663・#2664・#2665・#2806 |
| `N2a` | ラベルの残り | worker への規則文と受け渡し（前景で待つ規則・結果ファイルが無いときの起こし直し・修正の手順書の届け方・PR 本文の下書きの置き場・closing keywordsの範囲）を直し、closing keywordsの形のコミットを push の前に断る子と、conductor がプランを書き換える副命令を足す | 19 本。ラベルの残りの詳細設計の「ファイル配置」 | `SF1a`・`SF1b`・`SF2`・`RF5a` | 組 N | 1 本 | 無し | test_drive_refactor.py の「結果ファイルを書かなかった」で終わることを期待する箇所（233 行目付近） | #1734・#1742・#1958・#1984・#2005・#3111・#3129 |
| `N2b` | ラベルの残り | 設計の結果を読めない承認済みの課題・分割した PR・サブディレクトリの相対パスのテストで止まるか正しく判定し、受け入れ条件の節と区分をプランの中で確かめ、触るファイルの宣言の無いプランを直列にし、マージで消えた作業場所の後のステップと報告をリポジトリの根で打つ | 27 本。ラベルの残りの詳細設計の「ファイル配置」 | `SF1a`・`SF1b`・`SF2`・`RF5a`・`A1b` | 組 N | 1 本 | 無し | test_design_match.py の test_judge_names_test_file_by_path_dir_and_basename の `"sub/test_b.py": "missing"` の期待 ほか（詳細設計） | #1811・#2056・#2165・#2246・#2274・#2357・#2453・#2539・#2553・#2596・#2969・#3022・#3186 |
| `N3` | ラベルの残り | 並列の計画で、止めた計画を同じ名前で再開でき、レーンの生死を一覧の 1 行ではなく開発ループの記録・ラッパーの認証の止まり・止めた後の確かめと照らして決める | 18 本。ラベルの残りの詳細設計の「ファイル配置」 | `SF1a`・`RF6a`・`RF6b`・`SF1b`・`SF9` | 組 N | 1 本 | 無し | test_parallel_run.py のうち見回りの判定を _Run 越しに見るテスト（test_a_stalled_lane_is_asked_once・test_a_stopped_helper_without_settle_is_asked・test_a_helper_*_is_not_asked 系 ほか（詳細設計） | #2477・#2584・#2617・#2637・#2647・#2649・#2808・#2936・#3173・#3243 |
| `N4` | ラベルの残り | 中継（relay）の Stop の hook・区間の切り替え・リレーハブの起動と起こし直し・アカウントの指定・PATH の入口と、共有の依存の環境の python の選び方を、事実で判定し失敗を返す形にそろえる | 25 本。ラベルの残りの詳細設計の「ファイル配置」 | `SF1a`・`DC1`・`DC5`・`SF12`・`SF1b`・`SF6`・`A2` | 組 N | 1 本 | 無し | test_relay_hub.py の test_process_restart_of_running_name_is_taken（動いている名前の restart を断る期待値で、restart をリレーハブの中で止めてから起こす形と食い違う）。test_relay.py（4,884 行）のうち mark ほか（詳細設計） | #1877・#1919・#2324・#2438・#2461・#2464・#2504・#2563・#2583・#2753・#3105 |
| `N5` | ラベルの残り | cross-refactoring の振る舞いの不具合 8 件を直す。足したテストが全体の実行に入るかの確かめ・提案の範囲を課題が触る関数へ絞る・読み取りの担当の結果ファイルの置き場・drive の案内・着手前のテストのパスの種類・雛形の試走の上限・最終ゲートの範囲外の直し・項目の範囲テストの絞り込み | 33 本。ラベルの残りの詳細設計の「ファイル配置」 | `SF1a`・`SF1b`・`DC5` | 組 N | 1 本 | 無し | plugins/ndf/scripts/tests/test_lib_loop_drive.py の test_keep_finished_compares_reopen_head_then_args（案内の字面 --reopen を期待値に持つ） ほか（詳細設計） | #620・#622・#750・#2057・#2096・#2158・#3038・#3049 |
| `N6` | ラベルの残り | cross-review の対象リポジトリの決め方・PR のブランチとレビュー用のworktreeの同期・巻き直しの後の番号の規則・作業中のworktreeの登録の保護を、事実で決めて理由を返す形にそろえる | 16 本。ラベルの残りの詳細設計の「ファイル配置」 | `SF1a`・`SF1b`・`RF1b`・`K1`・`A2`・`SF13`・`DC4` | 組 N | 1 本 | 無し | skills/cross-review/tests/test_state_start_round_sync.py のうち、修正必須の判定で記録が無いときに必ず終了コード 5 で止まることを期待するテスト（結果ファイルが残る場合は取り込む形に変わる） ほか（詳細設計） | #1707・#2440・#2770・#3106・#3109 |
| `N7` | ラベルの残り | 棚卸し（backlog-refinement）の候補・持ち越し・却下・切り出しの境界・見直しの上限と採点の欠けの不具合 6 件を直す | 16 本。ラベルの残りの詳細設計の「ファイル配置」 | `SF1a`・`SF1b`・`K1`・`SF2`・`DC5`・`A2` | 組 N | 1 本 | 無し | plugins/ndf/skills/backlog-refinement/tests/test_upkeep_rank_cli.py の test_rejected_move_is_remembered_by_apply_only（依存先の一致で記録する形を前提にした期待値を持つなら作り直す） ほか（詳細設計） | #1595・#1640・#2064・#2232・#2423・#2540 |
| `N8a` | ラベルの残り | 工程の終わりの記録（確定仕様化・後片付けの工程表・リリース後テストの合否）、引継ぎ文書の大きさと実在の確かめ、用語集の分割の出力を直す | 18 本。ラベルの残りの詳細設計の「ファイル配置」 | `SF1a`・`SF1b`・`SF13`・`A2`・`DC6`・`DC3`・`A1a`・`DC5` | 組 N | 1 本 | 無し | plugins/ndf/scripts/tests/test_handoff.py の test_check_line_limit_and_trim_moves_demoted_section（退避の対象が「前の会話の進み」だけの前提） ほか（詳細設計） | #1616・#1715・#1785・#1835・#1893・#2293 |
| `N8b` | ラベルの残り | 使用量の帳簿（日付の区切り・同じ応答の数え方・置き場・停止の hook の順序）と MVV の記録（worktree の判定・判定の再利用・覆しの数え方）を直す | 8 本。ラベルの残りの詳細設計の「ファイル配置」 | `SF1a`・`SF1b`・`SF13`・`A2`・`DC6`・`DC3`・`A1a`・`DC5` | 組 N | 1 本 | 無し | plugins/ndf/scripts/tests/test_handoff.py の test_check_line_limit_and_trim_moves_demoted_section（退避の対象が「前の会話の進み」だけの前提） ほか（詳細設計） | #1978・#1979・#2575・#2576・#2603・#2614・#2777 |
| `N9` | ラベルの残り | テストの揺れ（壁時計・並列・パスの形・起動の順）を待たなかった場合だけ落ちる形へ直し、本物の状態の置き場からテストを隔離し、残骸と重複のテストを削り、親ごと消えた worktree のテスト環境の割り当てを回収する | 28 本。ラベルの残りの詳細設計の「ファイル配置」 | `RF2`・`DC5`・`SF6`・`SF11`・`RF7`・`SF12` | 組 N | 1 本 | 無し | test_removed_review_phase.py（2 件）・test_no_command_parsing.py（2 件）・test_training_optout.py::test_experimental_copy_is_gone ほか（詳細設計） | #1735・#1967・#2130・#2758・#2776・#2798・#2964・#2974・#3140 |
| `N10` | ラベルの残り | 外部 CLI の監視の照合の範囲と警告の出し方、1 回の実行を識別子 1 つで追う記録（#2338 の設計 PR #2497 のとおり）、マージの hook の対象リポジトリの決め方、kiro の起動がworktreeを書き換えないことを直す | 28 本。ラベルの残りの詳細設計の「ファイル配置」 | `SF1a`・`SF1b`・`A2`・`DC5`・`DC6`・`DC3` | 組 N | 1 本 | 無し | test_monitor_layout.py と skills/cross-review/tests/test_monitor_early_error.py のうち、平文の quota exceeded を利用上限と読む前提の期待値（行頭の ERROR: の無い形）を消す ほか（詳細設計） | #1411・#2059・#2338・#2446・#2448 |
| `N11` | ラベルの残り | SF2 が `on_cause` で書き直したプランの雛形に、検査・リリース・閉じ・実装のプランのステップごとの不具合 9 件の直しを足す | 12 本。ラベルの残りの詳細設計の「ファイル配置」 | `SF2`・`SF1a`・`SF1b`・`RF5a`・`RF1b` | 組 N | 1 本 | 無し | SF2 が作り直すテンプレートのテストのうち、`last[:120]` の切れ・`git pull -q --ff-only origin` を含む verify の cmd の文字列・refactor の cmd に `--sync-command` が無いことを期待値に持つ assert（着手の時点で grep で数える） | #1502・#2049・#2054・#2118・#2140・#2370・#3003・#3108・#3112 |
| `N12` | ラベルの残り | 重いテストの同時実行を作業マシンの上限（枠）に収め、メモリの下限を割ったら OOM Killer より先に NDF が待たせるか止める（#2711） | 18 本。ラベルの残りの詳細設計の「ファイル配置」 | `SF1b` | 組 N | 1 本 | 無し | 無し | #2711 |

**「この子で解消する Issue」の列は、ラベル `rebuild-1142` の Issue の行き先（[issue-1142-design-labels.md](issue-1142-design-labels.md) ）と、ラベルの無い割り当て済みの bug（#3096・#1755）を持つ。** 範囲を広げた子 Issue は、触るファイルを設計の結果と主題の詳細設計のファイル配置に足し、上の数を数え直した。

**smells の範囲の外の残り。** smells の決定 43 は、リファクタリング計画の 8 件の範囲（`supervise.py`・`supervise_lib/`・`parallel.py`・`lib/parallel_*`・`lib/sprint_loop*`・`release-steps.py`・`release_lib/`・`lib/durable.py`・`lib/post_queue.py`・2 本の `drive.py`。兆候のあるファイル 101 本）を A3 から除いた。この範囲は 8 件の子 Issue（`RF*`）が、移すときに直す（受け持ちは #2963 の設計文書の「smells の決定 43 が外した範囲の割り当て」）。E9 で前後の数を残す。

### リファクタリング計画の 8 件

8 件と同じ目的の一部を受け持つ #1142 の子 Issue を次に示す。「受け持たない部分」は 8 件の子 Issue（`RF*`）が受け持つ。関数の粒度の受け持ちの分け方は #2963 の決定 15 にある。

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

8 件を子 Issue に取り込んで変わった点:

- 8 件を 11 本の子 Issue として設計し、SF1b・SF2・SF6・SF7・SF9・SF11・SF13・DC2・DC8・K1〜K3 の先に要るものへ戻すため、順番が増え、#2963・#2993 が SF1b の前、#2996 が DC2 の前に入る
- smells の決定 43 のとおり、上の「smells の範囲の外の残り」の 101 本の兆候は、その範囲の子 Issue が移すときに直す
- 開いている bug のうち 8 件の範囲のもの（下の「開いている bug の割り当て」で範囲外とした行）は、#2963 の設計文書の「開いている bug の割り当て」で 8 件の子 Issue へ割り当てた
- 8 件に依らない子 Issue（順番 1 の SF3・A0・SF1a・DC1、順番 2 の A1a・A2、順番 3 の A1b など）は、8 件の設計を待たずに着手する

### 流す順序

同じ順番の子 Issue は、触るファイルが重ならず、先に要るものが前の順番にある。順番の中の並びは優先の順（「処理の手順」）である。リファクタリング計画の 8 件の子 Issue（`RF*`）を置いた理由は #2963 の設計文書の「流す順序への入れ方」にある。

| 順番 | 同時に流せる子 Issue | 待つ理由（その順番を決めた前の子 Issue と、重なるファイルか先に要るもの） |
| --- | --- | --- |
| 1 | `A0`・`DC1`・`SF3`・`SF1a`・`RF1` | 無し（最初の順番） |
| 2 | `A1a`・`A2` | `A1a` ← `A0`（先に要るもの）、`A2` ← `A0`（先に要るもの） |
| 3 | `A1b`・`RF2` | `A1b` ← `A1a`（先に要るもの）、`RF2` ← `A1a`（先に要るもの） |
| 4 | `RF1b`・`RF3`・`RF4`・`RF5b`・`RF6a`・`RF7`・`RF8` | `RF1b` ← `RF2`（先に要るもの）、`RF3` ← `RF2`（先に要るもの）、`RF4` ← `A1b`（先に要るもの）、`RF5b` ← `RF2`（先に要るもの）、`RF6a` ← `A1b`（先に要るもの）、`RF7` ← `RF2`（先に要るもの）、`RF8` ← `RF2`（先に要るもの） |
| 5 | `DC2`・`RF5a`・`RF6b` | `DC2` ← `RF3`（先に要るもの）、`RF5a` ← `RF4`（先に要るもの）、`RF6b` ← `RF6a`（先に要るもの） |
| 6 | `SF1b` | `SF1b` ← `RF6b`（先に要るもの） |
| 7 | `DC3`・`DC4`・`DC6`・`SF9`・`SF10`・`SF11`・`K3` | `DC3` ← `SF1b`（`scripts/training-optout.py` ほか 2 本）、`DC4` ← `SF1b`（`scripts/mode-materials.py` ほか 1 本）、`DC6` ← `SF1b`（`scripts/glossary.py` ほか 4 本）、`SF9` ← `SF1b`（先に要るもの）、`SF10` ← `SF1b`（先に要るもの）、`SF11` ← `SF1b`（先に要るもの）、`K3` ← `SF1b`（`scripts/release-steps.py`） |
| 8 | `DC5` | `DC5` ← `DC3`（先に要るもの） |
| 9 | `SF2`・`SF12`・`K1` | `SF2` ← `DC5`（`scripts/supervise_lib/verify_steps.py`）、`SF12` ← `DC5`（先に要るもの）、`K1` ← `DC5`（先に要るもの） |
| 10 | `SF4`・`SF5`・`SF7`・`N5` | `SF4` ← `SF2`（先に要るもの）、`SF5` ← `SF2`（先に要るもの）、`SF7` ← `SF2`（先に要るもの）、`N5` ← `SF2`（`skills/cross-refactoring/SKILL.md` ほか 1 本） |
| 11 | `SF6`・`SF13` | `SF6` ← `SF4`（先に要るもの）、`SF13` ← `SF5`（先に要るもの） |
| 12 | `N1`・`N3`・`N6`・`N8b` | `N1` ← `SF6`（`scripts/supervise_lib/engine.py`）、`N3` ← `SF13`（`scripts/parallel.py`）、`N6` ← `SF13`（先に要るもの）、`N8b` ← `SF13`（先に要るもの） |
| 13 | `N2a`・`N7` | `N2a` ← `N1`（`scripts/supervise.py`）、`N7` ← `N1`（`scripts/lib/sprint_loop_record.py`） |
| 14 | `N2b`・`N4`・`N8a`・`A3g` | `N2b` ← `N2a`（`scripts/supervise_lib/paths.py` ほか 3 本）、`N4` ← `N2a`（`scripts/supervise_lib/claude.py`）、`N8a` ← `N7`（`contracts/limits.json`）、`A3g` ← `N7`（`skills/backlog-refinement/scripts/upkeep.py` ほか 5 本） |
| 15 | `N9`・`A3d` | `N9` ← `N4`（`scripts/tests/test_relay.py` ほか 1 本）、`A3d` ← `N4`（`scripts/hook_lib/token_guard.py` ほか 10 本） |
| 16 | `N10` | `N10` ← `N9`（`scripts/tests/test_monitor_layout.py`） |
| 17 | `N11`・`N12`・`A3a`・`A3b`・`DC8` | `N11` ← `N10`（`scripts/check-trigger.py`）、`N12` ← `N10`（`conftest.py` ほか 1 本）、`A3a` ← `N10`（`scripts/lib/durable_keys.py`）、`A3b` ← `N10`（`scripts/lib/monitor_loop.py` ほか 1 本）、`DC8` ← `N10`（`scripts/lib/durable.py`） |
| 18 | `A3c-1`・`A3c-2`・`A3e-1`・`A3e-2`・`A3f-1`・`A3f-2`・`W6` | `A3c-1` ← `N12`（`scripts/lib/procs.py`）、`A3c-2` ← `N12`（`scripts/lib/test_triage.py`）、`A3e-1` ← `N11`（`scripts/behavior-check.py` ほか 2 本）、`A3e-2` ← `N12`（`scripts/test-run.py`）、`A3f-1` ← `N12`（`skills/cross-refactoring/scripts/refactor.py`）、`A3f-2` ← `N12`（`skills/cross-refactoring/scripts/refactor_lib/process.py`）、`W6` ← `A3a`（`scripts/lib/claude_api_keys.py`） |
| 19 | `DC7`・`W4` | `DC7` ← `A3c-1`（`scripts/lib/pace.py` ほか 1 本）、`W4` ← `A3f-1`（`skills/cross-refactoring/scripts/refactor.py` ほか 14 本）、`DC7` は #3191 のマージも待つ |
| 20 | `K2`・`SF8`・`W3`・`W5` | `K2` ← `DC7`（先に要るもの）、`SF8` ← `DC7`（`scripts/lib/workflow_def.py` ほか 1 本）、`W3` ← `DC7`（`scripts/lib/transcript_agents.py`）、`W5` ← `DC7`（`scripts/hook_lib/token_guard.py` ほか 1 本）、`SF8` は #3191 のマージも待つ |
| 21 | `W1`・`W2`・`W7` | `W1` ← `K2`（`scripts/check-trigger.py` ほか 2 本）、`W2` ← `K2`（`scripts/lib/review_criteria.py` ほか 1 本）、`W7` ← `K2`（`skills/fix/scripts/fix-steps.py`） |
| 22 | `W8` | `W8` ← `W7`（先に要るもの） |

確かめ方（リポジトリの根で打つ。2026-11-13 に打ち、「子 Issue 72・順番 22: 同じ順番の重なりと先に要るものの逆転は 0 件」を返した）:

```bash
python3 - <<'EOF'
import itertools, re, sys
D = "".join(open(f"issues/issue-{n}-design.md", encoding="utf-8").read() for n in (1142, 2963))
H = open("issues/issue-1142-design-detail.md", encoding="utf-8").read()
SHARED = {"plugins/ndf/contracts/index.md"}  # 生成物。後からマージする PR が --render で書き直す
files = {}
for line in "".join(d.split("\n## ", 1)[0] for d in D.split("\n## 設計の結果\n")[1:]).splitlines():
    m = re.match(r"\| #\d+ \| 実装する \| — \| （([^。）]+)(?:。[^）]*)?）(.*) \|$", line)
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
bad = [f"順番 {waves[a]}: {a} と {b} が重なる: {hit(a, b)[:3]}" for a, b in itertools.combinations(sorted(waves), 2) if waves[a] == waves[b] and hit(a, b)]
bad += [f"{c} が先に要る {d} より前の順番にある" for c, ds in deps.items() for d in ds if d in waves and waves[d] >= waves[c]]
bad += [f"設計の結果・流す順序・実装計画の子 Issue が食い違う: {sorted(set(files) ^ set(waves) | set(files) ^ set(deps))}"] if set(files) != set(waves) or set(files) != set(deps) else []
print("\n".join(bad) or f"子 Issue {len(files)}・順番 {max(waves.values())}: 同じ順番の重なりと先に要るものの逆転は 0 件")
sys.exit(1 if bad else 0)
EOF
```

スクリプトは、設計文書の「設計の結果」の行から子 Issue ごとの触るファイルを読み（括弧の中の名前が子 Issue）、同じ順番の組の重なりと、上の表の「先に要るもの」が後ろの順番にある逆転を数える。設計の結果の触るファイルが主題の詳細設計のファイル配置の表を含むことは、`design-survey.py check --require-detail` の `detail_file` が確かめる。

### レーンへの割り当ての案

**#1142 の関連レーンは同時に 6 本まで使う**（2026-11-13 利用者。ほかのリポジトリの開発を止め、#1142 に注力する）。#1142 の前に済ませる lflow（#3092・PR #3191）・lbr2・lsw の 3 本を `after` に入れ、3 本が終わってから実装の子 Issue を起動する。上限は `relay.py cap --named 13 --plans 13` に上げてあり、6 本 × プラン 2 本の 12 本が収まる。オーガナイザーは `parallel.py lane p6 add` でレーンを足す。

**レーンは主題で分け、触るファイルが重なる子 Issue の前後は流す順番で決める。** 主題をまたいで同じファイルを触る子 Issue（`supervise_lib/state.py` など）があり、レーンの触るファイルを完全に分けられない。そのため、同じ順番の子 Issue だけを同時に流し（「流す順序」で重なりが 0 件）、lrebuild が前の順番の PR のマージを確かめてから次の順番の子 Issue を受け持つレーンへ知らせる。

| レーン | 受け持つ子 Issue | 子 Issue の数 | 順番の範囲 | 主に触るディレクトリ | after | プランの本数 | 役割 |
| --- | --- | ---: | --- | --- | --- | ---: | --- |
| lrebuild | 組 S（`A0`・`A1a`・`A1b`・`A2`・`A3a`〜`A3g`）と順番の見張り | 14 | 1〜18 | `scripts/`（検査）・`plugins/ndf/scripts/lib/`・`plugins/ndf/scripts/hook_lib/`・`plugins/ndf/scripts/relay_lib/`・`plugins/ndf/skills/*/scripts/` | 既にある（after は今の `[]` のまま。実装の着手は 3 本の後） | 2 | 組 S を流し、**流す順番を見張る**（前の順番の PR のマージを確かめてから、次の順番の子 Issue を受け持つレーンへ知らせる） |
| lr-flow | 組 F（`SF1a`〜`SF13`） | 14 | 1〜20 | `plugins/ndf/scripts/supervise_lib/`・`plugins/ndf/scripts/lib/step_result.py`・`plugins/ndf/contracts/causes.json` | `["lflow", "lbr2", "lsw"]` | 2 | SF3 から流す。SF8 は #3191 のマージの後 |
| lr-data | 組 D（`DC1`〜`DC8`）と組 B の `K1`〜`K3` | 11 | 1〜20 | `plugins/ndf/contracts/`・`scripts/contracts_lib/`・`plugins/ndf/scripts/lib/` | `["lflow", "lbr2", "lsw"]` | 2 | DC7 は #3191 のマージの後 |
| lr-rf | 組 R（`RF1`〜`RF8`）の後に組 B の `W1`〜`W8` | 19 | 1〜22 | `plugins/ndf/scripts/supervise_lib/`・`plugins/ndf/scripts/lib/parallel_*`・`plugins/ndf/scripts/lib/sprint_loop*`・`plugins/ndf/scripts/release_lib/`・`scripts/check-script-structure.py` | `["lflow", "lbr2", "lsw"]` | 2 | RF は順番 1〜5 で終わり、W は順番 18 から始まる。間は組 N の残りを lr-fix-a・lr-fix-b から引き受ける |
| lr-fix-a | 組 N のうち supervise と並列の計画と中継（`N1`・`N2a`・`N2b`・`N3`・`N4`・`N11`） | 6 | 12〜17 | `plugins/ndf/scripts/supervise_lib/`・`plugins/ndf/scripts/lib/parallel_*`・`plugins/ndf/scripts/relay_lib/` | `["lflow", "lbr2", "lsw"]` | 2 | N11 は SF2 の後 |
| lr-fix-b | 組 N のうち Skill と記録とテスト（`N5`〜`N7`・`N8a`・`N8b`・`N9`・`N10`・`N12`） | 8 | 10〜17 | `plugins/ndf/skills/cross-refactoring/`・`plugins/ndf/skills/cross-review/`・`plugins/ndf/skills/backlog-refinement/`・`plugins/ndf/scripts/lib/usage_*`・`plugins/ndf/scripts/tests/` | `["lflow", "lbr2", "lsw"]` | 2 | — |

順番ごとの子 Issue と受け持つレーンは次のとおりである。1 本のレーンが同時に流すプランは 2 本までのため、同じ順番に 3 本以上の子 Issue を持つレーン（順番 4 の lr-rf の 7 本、順番 7 の lr-flow と lr-data の 3〜4 本、順番 18 の lrebuild の 6 本）の残りは、**その順番に子 Issue を持たないレーンへ lrebuild が回す**（`parallel.py lane p6 set` で受け持ちを移す）。

| 順番 | 子 Issue（レーン） |
| --- | --- |
| 1 | `A0`（lrebuild）・`SF3`（lr-flow）・`SF1a`（lr-flow）・`DC1`（lr-data）・`RF1`（lr-rf） |
| 2 | `A1a`（lrebuild）・`A2`（lrebuild） |
| 3 | `A1b`（lrebuild）・`RF2`（lr-rf） |
| 4 | `RF1b`（lr-rf）・`RF3`（lr-rf）・`RF4`（lr-rf）・`RF5b`（lr-rf）・`RF6a`（lr-rf）・`RF7`（lr-rf）・`RF8`（lr-rf） |
| 5 | `DC2`（lr-data）・`RF5a`（lr-rf）・`RF6b`（lr-rf） |
| 6 | `SF1b`（lr-flow） |
| 7 | `SF9`（lr-flow）・`SF10`（lr-flow）・`SF11`（lr-flow）・`DC3`（lr-data）・`DC4`（lr-data）・`DC6`（lr-data）・`K3`（lr-data） |
| 8 | `DC5`（lr-data） |
| 9 | `SF2`（lr-flow）・`SF12`（lr-flow）・`K1`（lr-data） |
| 10 | `SF4`（lr-flow）・`SF5`（lr-flow）・`SF7`（lr-flow）・`N5`（lr-fix-b） |
| 11 | `SF6`（lr-flow）・`SF13`（lr-flow） |
| 12 | `N1`（lr-fix-a）・`N3`（lr-fix-a）・`N6`（lr-fix-b）・`N8b`（lr-fix-b） |
| 13 | `N2a`（lr-fix-a）・`N7`（lr-fix-b） |
| 14 | `A3g`（lrebuild）・`N2b`（lr-fix-a）・`N4`（lr-fix-a）・`N8a`（lr-fix-b） |
| 15 | `A3d`（lrebuild）・`N9`（lr-fix-b） |
| 16 | `N10`（lr-fix-b） |
| 17 | `A3a`（lrebuild）・`A3b`（lrebuild）・`DC8`（lr-data）・`N11`（lr-fix-a）・`N12`（lr-fix-b） |
| 18 | `A3c-1`（lrebuild）・`A3c-2`（lrebuild）・`A3e-1`（lrebuild）・`A3e-2`（lrebuild）・`A3f-1`（lrebuild）・`A3f-2`（lrebuild）・`W6`（lr-rf） |
| 19 | `DC7`（lr-data）・`W4`（lr-rf） |
| 20 | `SF8`（lr-flow）・`K2`（lr-data）・`W3`（lr-rf）・`W5`（lr-rf） |
| 21 | `W1`（lr-rf）・`W2`（lr-rf）・`W7`（lr-rf） |
| 22 | `W8`（lr-rf） |

`lane add` に渡す JSON の形（子 Issue を起票した後に番号を入れる。lr-fix-a の例）:

```json
{"name": "lr-fix-a", "input": {"kind": "sprint", "issues": [<N1・N2a・N2b・N3・N4・N11 の番号>]}, "plans": 2,
 "touches": ["plugins/ndf/scripts/supervise_lib/", "plugins/ndf/scripts/lib/", "plugins/ndf/scripts/relay_lib/"], "after": ["lflow", "lbr2", "lsw"]}
```

## 開いている bug の割り当て

**ラベル `rebuild-1142` の Issue 283 件の行き先は [issue-1142-design-labels.md](issue-1142-design-labels.md) にある。** #1142 の受け入れ条件（2026-11-13 利用者）は、ラベルの Issue が #1142 の改善ですべて閉じることである。この節は件数の要約と、ラベルの無い bug の扱いだけを持つ。

| 行き先 | 件数 |
| --- | ---: |
| 子 Issue そのもの（自分の PR で閉じる） | 55 |
| 既にある子 Issue（A・DC・SF・K・W・RF）の変更で閉じる | 112 |
| ラベルの残りを受け持つ子 Issue（N1〜N12）の変更で閉じる | 110 |
| 閉じられない（理由を添えてオーガナイザーへ送る） | 6（#1571・#1601・#2316・#2341・#3113・#3189） |

2026-11-13 の時点で開いている bug 110 件のうち 96 件はラベルを持ち、上の表の行き先に入る。ラベルの無い 14 件のうち #3096・#1755 は #2963 の設計で `RF6a`・`RF8` へ割り当てた。残りの 12 件は #1142 の範囲の外として開いたまま残し、作り直しの後に閉じるか理由を書く。

| Issue | 題 | 割り当て | 理由 1 文 |
| --- | --- | --- | --- |
| #3175 | 並列の計画: リリース承認ゲートの承認を送った後、約 6 時間リリースが進まなかった | 範囲外 | 原因を確かめていない（一時レーンの起動の待ちか承認のイベントの受け取り）。SF9 の待ちの行き先では直らない |
| #3168 | selected のリリース: 承認の後の直接のコミットと、動いたスプリントの先頭を確かめずに本番へ入れる | 範囲外 | selected の取り込み方の承認と本番の比べ方の欠けで、新しい確かめの機能が要り作り直しの範囲の外 |
| #3089 | 並列の計画: レーンが手順書どおり parallel.py await を打つと、既定の待ち時間 0 で待たずに戻る | 範囲外 | `parallel.py await` の待ち時間の既定と手順書の食い違いで、SF9 はオーガナイザーの wait の行き先だけを扱う |
| #3017 | mvv-gate.py: #2912 の後の指示文が、レッドライン（C2・C3・C4・C6・P1）に当たる設計を boundary 空の「従う」と判定し、承認ゲートを省ける | 範囲外 | MVV 判定の指示文の誤りで、スクリプトの作り直しでは直らない |
| #2984 | 手動確認: worker が確かめ終えても PR 本文の手動確認の項目に済んだ印が付かない | 範囲外 | 手動確認の印を付ける責務が手順に無い Skill の手順の欠けで、作り直しの範囲の外 |
| #2176 | 文書検査: mermaid の構文を解析せず、GitHub で描画できない図が`承認ゲート 1` まで届く | 範囲外 | 文書の検査が mermaid の構文を見ない欠けで、作り直しの範囲の外 |
| #2114 | document-rebuild: 読み手役を外部の CLI で立てるときに、止まらず文脈を持たない形で起動できるようにする | 範囲外 | `document-rebuild` の読み手役の起動の形が書かれていない Skill の手順の欠けで、作り直しの範囲の外 |
| #2113 | document-rebuild: 未決の問いがある提案書の冒頭の章が、組み直しと執筆の決まりで食い違わないようにする | 範囲外 | `document-rebuild` の 3 つの文書が食い違う Skill の手順の誤りで、作り直しの範囲の外 |
| #2078 | system-repo.md の記録の説明に Marp と比較の言い回しが残り、#2065 の受け入れ条件 15・16 が通らない | 範囲外 | Skill の参照文書の言い回しの誤りで、スクリプトの作り直しでは直らない |
| #2037 | supervise queue の重なりの判定がファイル単位のため、設計が関数単位で分けた実装のプランも直列になる（スプリント a1 で 58 分が 2 時間 47 分） | 範囲外 | キューの重なりの判定がファイル単位の決まりで、作り直しの範囲の外 |
| #2028 | Skill を足す設計の触るファイルに Skill の数を持つ箇所が入らず、実装のステップが範囲外の失敗で止まる | 範囲外 | Skill の数を持つ箇所が設計の触るファイルに入らない手順の欠けで、作り直しの範囲の外 |
| #1659 | cross-refactoring: docs が SKILL.md にあるとする rf / rf_eval の定義が無く、自作すると中断が効かない形を踏みうる | 範囲外 | cross-refactoring の文書が無い定義を指す Skill の手順の誤りで、作り直しの範囲の外 |
