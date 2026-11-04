# プランの参照: プランを作り直さずに版・記録の PR・設定・予算を流す直前の値で使い、承認時の値と食い違えば止まる（#2344）

## 目的

- **今どうなっているか**: プランは版・記録の PR・設定から読んだ値・予算の秒を、組んだ時点の値で持つ。版と記録の PR だけはキューが流す前に差し込みを埋めるが、埋めた値でプランのファイルを上書きし、差し込みの種類ごとに別の関数が解決する。`--from` で始めると実行条件を見ずに流れ（#2349）、開発ループのスプリントは明示の版しか持てない（#2354）
- **誰に効くか**: NDF でスプリントを回す conductor と利用者。並行するスプリントの配布の後にプランを作り直す手当て（#2164 の再発 2 回）と、完了したプランの流し直し（#2349 の 24 分・$0.89）が無くなる
- **変えると何が成り立つか**: プランは値の取り先（参照）を持ち、参照の部品がステップの直前に 1 つの規則で解決・記録・照合する。解決できない・承認時の値と食い違うときは、推定の値で進まずに `stopped` か `gate` で返る

## 適用範囲

- **働く範囲**: NDF の配布物のうち次の 3 つ。配布先のリポジトリでも同じに働く。`factory/` のプランは含まない
  - プランを組むエントリポイント（`supervise.py new sprint` / `new close`、`sprint-state.py loop sprint`）
  - プランを流す処理（キュー・プランの実行・再開のエントリポイント）
  - 配布の本番のエントリポイント
- **プロジェクトごとに違うもの**: 参照の値は各リポジトリの `.ndf/` の設定・origin の版とタグ・GitHub の Pull Request から読む。どの設定のキーを参照にするかは設定の契約（`plugins/ndf/contracts/settings/`）の行が持ち、ai-plugins の値を部品に置かない
- **当たるモード**: `standard`。#2335 の柱 P5 で、柱 P6（#2339 データの契約）・P8（#2341 入力の契約）・P3（#2342 持ち主の記録）のマージの後に実装する

## あるべき姿の根拠

| 根拠 | 種類 | 何を示すか |
| --- | --- | --- |
| 利用者の指示（2026-11-01）「異常系処理とログ出力、異常時の回復処理が統一されていないから起こる不具合・課題が多い。1 件ずつ対処してもきりがない」「出力だけでなく入力もそろえるべき」 | 利用者の指示の原文 | 値の解決を種類ごとの手当てでなく 1 つの仕組みに寄せること |
| 差し込みを解決する関数が `supervise_lib/queue_fill.py` に 5 つ（`_fill_prs`・`_fill_states`・`_fill_work`・`_fill_record`・`fill_release_version`）と `supervise_lib/queue.py` に 1 つ（`fill_queue_prs`）ある。埋めた値は `fill_queue_pr`・`fill_queue_prs`・`_Fill.ensure_filled` の 3 か所でプランのファイルへ上書きされる（`write_text_atomic(Path(plan), …)` と `write_text_atomic(self.path, …)`） | 実測（2026-11-04 の develop のコード） | 解決の規則が 6 か所に分かれ、プランが変わったのが人の編集か解決かを区別できないこと |
| `supervise_lib/engine.py:219` の `if self.plan.get("実行の条件") and not start:` | 実測（同上） | 開始のステップを渡すと実行条件が評価されないこと（#2349 の 1 つ目の原因） |
| `lib/sprint_loop_cli.py:97` の `s.add_argument("--dev", required=True)` と `lib/sprint_loop.py:294` の `"--version", dev` | 実測（同上） | 開発ループが版を参照にできないこと（#2354 の原因） |

要求と受け入れ条件は #2344 の本文にある。コピーは [issue-2344-requirements.md](issue-2344-requirements.md) にある。
この文書は「どう作るか」だけを扱う。決定の記録は [issue-2344-design-decisions.md](issue-2344-design-decisions.md) にある。
上位の設計は [issue-2335-design.md](issue-2335-design.md) である。実行の核・参照の部品（`lib/refs.py`）・不変条件 I14・ドメインイベント E5 はそれに従う。

## ドメインモデル

### コンテキスト

| コンテキスト | 何の語が 1 つの意味に決まるか |
| --- | --- |
| NDF の開発ワークフロー（`ndf-workflow`） | プランの参照・解決の記録・解決の控え・承認時の値・形を決めた設定・プランのハッシュ・版の記録 |
| リリース（`ndf-release`） | 承認したコミット・承認資料（語の意味は変えない。承認資料に「承認した版」の行を足す） |

**リリースが供給者、開発ワークフローが顧客の関係（顧客 / 供給者）。** 参照の部品は承認資料の形（承認したコミットと承認した版の行）と
`lib/approved_commit.py` の比較を、リリースが決めたとおりに読む。参照の部品は承認資料を書かない。

### 集約

| 集約 | 持ち主（書き換えてよいもの） | 根 | エンティティ | 値オブジェクト |
| --- | --- | --- | --- | --- |
| プラン | プランを組むエントリポイントと人（実行の間は誰も書かない） | プランのファイル | ステップ | 参照の宣言・形を決めた設定・実行条件 |
| 解決の控え | 参照の部品（`lib/refs.py`） | プランの状態ディレクトリ | 控えの行（参照の名前ごと） | 解決した値・出所・決め直しの履歴 |
| 実行 | 実行の核（#2335） | 実行の識別子 | 実行の記録の行 | 解決の記録の行・プランのハッシュ |

**集約の間は識別子で参照する。** 解決の控えはプランを状態ディレクトリのパスで、実行の記録の `ref` 行は控えを参照の名前で指す。
1 回の解決が書き換える集約は「解決の控え」だけで、実行の記録へは追記だけを行う。プランは解決で書き換えない。

### 不変条件

| # | 集約 | 条件 | 破れたときの扱い |
| --- | --- | --- | --- |
| I1 | プラン | プランのファイルは参照の解決で書き換わらない。解決した値はメモリ上のステップにだけ入る | 書き込みの経路が無い（解決の部品はプランのパスを書き口として受け取らない） |
| I2 | 実行 | ステップに入る値は、解決した参照・プランの値（明示）・プランの値（旧い形）のどれかの出所を持ち、1 値 1 行で解決の記録に残る | 記録へ書けなければ `stopped`（#2335 の I3） |
| I3 | 実行 | 参照の解決は、持ち主の記録を取った後・実行条件の評価の後に行う。プランの上位の欄（`作業場所`・`branch`）の参照は、形を決めた設定の照合の後・作業ツリーを作る前に 1 回だけ解決する。ステップの参照は、ステップを流す直前に解決する。開始のステップ（`--from`）によらず実行条件を先に評価する | 順序はプランの実行の 1 つの関数が持つ（下の処理の流れ） |
| I4 | 実行 | 解決できない参照を使うステップは流れない。推定の値・空・`0`・`unknown` で埋めない | `stopped`・終了コード 6、`gaps` に `ref:<名前>` と理由 |
| I5 | 実行 | 承認時の値を持つ参照は、解決した値と承認時の値が食い違えば流れない。承認したコミットの後に許したもの（この版の配布の Pull Request）だけが入ったときは食い違いとみなさない | `gate`・終了コード 10（`EXIT_GATE`）、`items` に承認時の値と実行時の値 |
| I6 | 解決の控え | 版の参照は控えがあれば控えの版を使う。決め直すのは #2164 の規則に当たるときだけで、前の版と理由を控えの `history` と解決の記録に残す | 規則に当たらない決め直しの経路が無い（決め直しは `release_version` の判定だけが起こす） |
| I7 | プラン | 形を決めた設定の今の値が、プランが控えた値と違えば、プランの最初のステップから流さない | `stopped`・終了コード 2、`gaps` に設定の名前と、`next` に組み直すコマンド |
| I8 | 実行 | 完了したプランを `--from` なしに流し直さない。完了した実行の記録のプランのハッシュと今のハッシュが違えば流さない | `gate`・終了コード 10（`EXIT_GATE`）、`next` に `--from` で流し直すコマンド |
| I9 | 実行 | 秘密と宣言された設定の値は、解決の記録と結果に書かれず、出所の名前だけが残る | 書く前に値を落とす（#2335 の I9 と同じ経路） |

### ドメインイベント

要求の E1〜E9 を引き継ぐ。右端は #2335 の番号である。

| # | イベント | 発生元 | 受け手 | #2335 |
| --- | --- | --- | --- | --- |
| E1 | プランを参照つきで組んだ | プランを組むエントリポイント | プランのファイル | — |
| E2 | 承認時の値（承認したコミット・承認した版）を承認資料に記録した | 承認ゲート 1・2 の承認（`approved_commit.record_approval`） | 参照の部品（E6） | — |
| E3 | 持ち主の記録を取った | 実行の核（柱 P3） | プランの実行 | E4 |
| E4 | 実行条件を評価した | プランの実行 | プランの実行（飛ばすなら E5〜E8 を行わずに完了） | E5 |
| E5 | 参照を解決した | 参照の部品 | プランの実行・解決の控え | E5 |
| E6 | 解決した値を承認時の値と照らした | 参照の部品 | プランの実行（食い違えば `gate`） | E5 |
| E7 | 解決の記録を書いた | 参照の部品（`ctx.log("ref", …)`） | 実行の記録 | E5 |
| E8 | ステップを流した | プランの実行 | 実行の核 | E7 |
| E9 | 同じプランを流し直し・再開した | 再開のエントリポイント（柱 P3）・キュー | プランの実行（ハッシュの照合） | E13 |

### 用語

| 用語 | 意味 | 用語集への反映 |
| --- | --- | --- |
| プランの参照 | 要求の用語のとおり | 追加済み（要求） |
| 解決の記録 | 要求の用語のとおり。実行の記録の `ref` 行である | 追加済み（要求） |
| 承認時の値 | 要求の用語のとおり | 追加済み（要求） |
| 解決の控え | 流し直しでも同じ値を使う参照（版）の解決した値と決め直しの履歴を、プランの状態ディレクトリに残すファイル（`refs.json`） | 追加（`ndf-workflow`） |
| 形を決めた設定 | プランにどのステップを置くかを決めた `.ndf/` の設定の値。組んだ時点の値をプランが控え、流す前に今の値と照らす | 追加（`ndf-workflow`） |
| プランのハッシュ | プランの型で読んだ中身を、キーを並べ替えた JSON にして取った sha256。完了したプランが変わったかを測る | 追加（`ndf-workflow`） |
| 版の記録 | 解決の控えのうち、版の参照の行 | 意味の変更（`ndf-workflow`） |
| 版の差し込み | 前の版の NDF が組んだプランの `{release_version}`・`{release_base}`。移行関数が版の参照へ読み替える | 意味の変更（`ndf-workflow`） |

## 機能一覧

| # | 機能 | 誰が使うか |
| --- | --- | --- |
| F1 | プランを組むエントリポイントが、版・記録の PR・前のプランの結果・設定・予算を参照で書き、形を決めた設定を控える | `new sprint` / `new close`・開発ループ |
| F2 | ステップの直前に参照を解決し、解決の記録を書いてメモリ上のステップへ値を入れる | プランの実行 |
| F3 | 版の参照の解決を控えに残し、流し直しで同じ版を使い、#2164 の規則のときだけ決め直す | 配布のプラン |
| F4 | 承認時の値と照らし、食い違えば `gate` で返す | 本番の配布（プランからも手動の起動からも） |
| F5 | 実行条件を開始のステップによらず先に評価する | プランの実行・再開 |
| F6 | 完了したプランの流し直しをプランのハッシュで判定する | 再開のエントリポイント・キュー |
| F7 | 旧い形のプラン（差し込み・値を書き込んだもの）を移行関数で読み、値をそのまま流す | 前の版の NDF が組んだプラン |

## 構成要素

| 要素 | 責務 | 新設 / 変更 |
| --- | --- | --- |
| 参照の部品（`lib/refs.py`） | 参照の宣言の型・種類の表・`resolve(ctx, plan, step)`。種類ごとの解決を遅れて読み、解決の記録と解決の控えを書く。外へ書くのは控えと記録だけ | 新設 |
| 参照の種類（`lib/ref_kinds/`） | 種類ごとに 1 モジュール（`version`・`record_pr`・`plan_result`・`setting`・`budget`・`tip`）。値か失敗の理由を返し、終了コードと出力を持たない | 新設（`queue_fill.py` の 5 つの関数と `queue.fill_queue_prs` を移す） |
| 版の判定（`lib/release_version.py`） | 次に空いている版・既出の判定・自分の配布のマージの有無（変えない） | 変えない |
| 承認したコミット（`lib/approved_commit.py`） | 「承認した版」の行の読み書きを足す。`release_lib/approval.py` の `check_approved`・`release_pr_allowed` を移し、`tip` の種類と配布の本番のエントリポイントが同じ関数を呼ぶ | 変更 |
| 承認資料を書くエントリポイント（`release-steps.py approval-facts`） | 承認資料に「承認した版」の行を書く | 変更 |
| プランの型（柱 P6 の `lib/model/` のプラン） | `refs`（参照の宣言）・`pins`（形を決めた設定）の欄を持つ。旧い差し込みを参照へ読み替える移行関数を持つ | 変更（柱 P6 の型に欄を足す） |
| 解決の控えの型（`lib/model/` の `refs-store`） | 控えの形と、`version.json` を控えへ読み替える移行関数 | 新設 |
| プランの実行（`supervise_lib/engine.py`） | ステップの直前に実行条件 → 形を決めた設定 → 参照の解決の順に呼ぶ。開始のステップで実行条件を飛ばさない | 変更 |
| キュー（`supervise_lib/queue.py`） | 差し込みを埋めない。done の項目にプランのハッシュを書き、参照で止まったプランに `unfilled: true` を付ける | 変更（`fill_stage` の埋める処理と `_release_skipped_by_condition`・`fill_queue_prs` を消す） |
| 差し込みの解決（`supervise_lib/queue_fill.py`） | — | 削除（`FINISHED`・`named`・`results_named` は `ref_kinds/plan_result.py` へ移す） |
| 再開（`supervise_lib/queue_resume.py`・柱 P3 の `supervise.py resume`） | 完了したプランをプランのハッシュで照らし、違えば `gate` で返す | 変更 |
| プランを組むエントリポイント（`supervise_lib/templates.py`・`release_templates.py`・`delivery_templates.py`・`sprint_routes.py`・`verify_steps.py`・`decl.py`） | 値の代わりに参照と形を決めた設定を書く。引数で明示した上限とブランチは値のまま、明示した版は版の参照の `explicit` に書く | 変更 |
| 開発ループ（`lib/sprint_loop_cli.py`・`lib/sprint_loop.py`） | `--dev` を任意にし、省いたら `new sprint` に `--version` を、`sprint-state.py init` に `--dev` / `--prod` を渡さない | 変更 |
| 設定の契約（`plugins/ndf/contracts/settings/`）と読み手（`scripts/contracts_lib/model.py`） | キーの行に `plan`（`ref` / `budget` / `shape`）と `secret` を足す。`supervise.json` の `impl_timeout`・`refactor_budget_minutes` の行を足す | 変更 |
| エントリポイントの契約（`plugins/ndf/contracts/entrypoints/`） | 配布の本番のエントリポイントの行に `refs`（`tip` の参照）を足す | 変更 |

### 要素の関係

```mermaid
graph TD
    NEW[プランを組むエントリポイント] -->|参照と形を書く| PL[プランのファイル]
    LOOP[開発ループ] --> NEW
    Q[キュー] --> EN[プランの実行]
    RS[再開] --> EN
    EN -->|読む| PL
    EN --> RF[参照の部品]
    RF --> RK[参照の種類]
    RK --> RV[版の判定]
    RK --> AC[承認したコミット]
    RK --> SC[設定の契約]
    RF -->|書く| ST[解決の控え]
    RF -->|追記| LG[実行の記録]
    AF[承認資料を書くエントリポイント] --> AC
    PROD[配布の本番のエントリポイント] --> RF
```

図に描かない要素は次のとおりである。

| 要素 | 図に描かない理由 |
| --- | --- |
| プランの型と解決の控えの型 | 参照の部品とプランの実行が読み書きに使う |
| エントリポイントの契約 | 核が配布の本番のエントリポイントの `refs` を読む |
| 差し込みの解決 | 消す |

### 置き場所

```text
plugins/ndf/
├── contracts/
│   ├── settings/*.json             # 変更: キーの行に plan・secret
│   └── entrypoints/*.json          # 変更: 配布の本番のエントリポイントに refs
└── scripts/
    ├── lib/
    │   ├── refs.py                 # 新設
    │   ├── ref_kinds/              # 新設: version・record_pr・plan_result・setting・budget・tip
    │   ├── model/                  # 変更（柱 P6）: プランの refs・pins、refs-store
    │   ├── approved_commit.py      # 変更
    │   ├── sprint_loop.py / sprint_loop_cli.py  # 変更
    │   └── release_version.py      # 変えない
    ├── release_lib/approval.py     # 変更: check_approved を approved_commit へ移す
    └── supervise_lib/
        ├── engine.py / queue.py / queue_resume.py  # 変更
        ├── queue_fill.py           # 削除
        └── templates.py ほかプランを組む 6 ファイル  # 変更
```

## 構造

```mermaid
classDiagram
    class RefSpec {
        +name
        +kind
        +params
        +approved
        +sticky
    }
    class Resolution {
        +name
        +value
        +source
        +secret
        +approved_value
    }
    class RefFailure {
        +name
        +reason
        +status
    }
    class StoreRow {
        +name
        +value
        +how
        +observed
        +history
    }
    RefSpec ..> Resolution : 解決する
    RefSpec ..> RefFailure : 解決できない・食い違う
    RefSpec ..> StoreRow : sticky なら読み書き
```

- **`resolve_head(ctx, plan) -> (plan, list[Resolution]) | RefFailure`**: プランの上位の欄（`作業場所`・`branch`）が使う参照を解決し、値を入れたプランのコピーをメモリ上で返す。形を決めた設定の照合の後、作業ツリーを作る前（`ensure_worktree` の前）に 1 回だけ呼ぶ。作業ツリーの準備とその後のステップは、このコピーを使う。版の参照の決め直しを確かめるのはこの解決だけである（下の「流れの決まり」）
- **`resolve(ctx, plan, step) -> (step, list[Resolution]) | RefFailure`**: ステップが使う参照だけを解決し、値を入れたステップのコピーを返す。`plan` は `resolve_head` が返したコピーである。`RefFailure.status` は `stopped` か `gate`
- **`check_pins(ctx, plan) -> RefFailure | None`**: 形を決めた設定を今の値と照らす。プランの最初に流すステップの前に 1 回だけ呼ぶ
- **`plan_digest(plan) -> str`**: プランのハッシュ。プランの型で読んだ値を `json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))` にして sha256 を取る
- **種類のモジュールの形**: `resolve(ctx, spec, plan) -> Resolution | RefFailure`。GitHub と origin を読む種類は、その種類を使うステップでだけ読み込まれる

## データ構造

### 参照の宣言（プランの `refs`）

プランの型の欄 `refs` は、参照の名前 → 宣言の対応である。ステップからは次の形で指す。

| 指す場所 | 書き方 |
| --- | --- |
| ステップの文字列の中 | `{ref:<名前>}`（版の起点は `{ref:<名前>.base}`） |
| プランの上位の欄（`作業場所`・`branch`） | `{ref:<名前>}`。版を省いた配布のプランは `branch` に `release/v{ref:version}` を、`作業場所` に版を含むパスを持つ |
| 数の欄（`timeout`） | `{"ref": "<名前>"}` |

| 種類 | `params` | 解決した値 | `sticky` | 解決できないとき |
| --- | --- | --- | --- | --- |
| `version` | `plugin`・`channel`（`dev` / `prod`）・`level`・`series`（本番が版を読む開発版のプランの名前）・`explicit`（明示の版。任意） | `{version, base}` | 真 | origin の版かタグを読めない・明示の版が既出・系列の版の記録が無い（#2164 の `unreadable` / `taken` / `no_series`） |
| `record_pr` | `issues`（課題の番号の並び） | リリース記録を置いた PR の番号 | 偽 | 記録の PR が見つからない（`record_find` の理由） |
| `plan_result` | `plan`（名前）か `plans: "earlier"`（キューの前のステージ）・`field`（`pr` / `state` / `prs` / `approved`）・`optional` | PR の番号・状態ディレクトリのパス・PR の並び・承認したコミット | 偽 | 名前の一致するプランの完了した結果が無い（`optional` の `state` だけ空を返す） |
| `setting` | `setting`（設定の契約の id とキー。例 `pace.json:auto.verify`） | 設定の値 | 偽 | 設定のファイルかキーが無い・型が違う |
| `budget` | `setting`（任意）・`policy`（上限の宣言の方針の名前）・`unit`（`seconds` / `minutes`）・`margin` | 秒（`margin` が真なら `with_margin` を足す） | 偽 | 設定が型に合わない・方針が無い（`LimitsError`） |
| `tip` | `branch`・`approved_from`（承認したコミットを返す参照の名前）・`allowed_pr`（許すものの参照の名前） | ブランチの先端の SHA | 偽 | 先端を読めない |

`approved`（任意）は承認時の値を持つ参照に付け、`gate-2` を書く。`version` では承認資料の「承認した版」と、`tip` では承認したコミットと
`compare_approved` で照らす。承認資料に「承認した版」の行が無い旧い資料では、#2164 の `series_changed`（承認ゲート 2 の通過の後に系列の版の記録が変わった）で照らす。

**引数で明示した上限とブランチは参照にしない。** 次の引数を渡したときは値のまま書く。解決の記録の出所は「プランの値（明示）」になる（決定 5）。
対象の引数は `--impl-timeout`・`--refactor-budget-minutes`・`--base`・`--production-branch` である。

**`--version` で明示した版は、版の参照の `params.explicit` に値のまま入れる。** 参照の部品は明示の版を置き換えず、版の参照の解決で既出の判定（#2164 の `taken`。既出なら `stopped`・終了コード 6）と、`approved` を付けた参照では承認した版との照合を必ず通す。解決の記録の出所は `plan:explicit`、控えの `how` は `explicit` になる（決定 5）。

### 参照にする設定のキー

設定の契約のキーの行の `plan` が、この表を正として持つ。設計の表は契約からコピーした一覧で、食い違えば契約が正である。
表は `supervise_lib/decl.py` の `apply_decls` と `sprint_routes.py` が読む項目から作った（2026-11-04 の develop）。

| 設定 | キー | ステップのどこに入るか | `plan` |
| --- | --- | --- | --- |
| `pace.json` | `<進め方>.verify` | 導入の確認のコマンド（`sprint_routes.pace_verify`） | `ref` |
| `supervise.json` | `impl_timeout` | 実装のステップの `timeout` | `budget` |
| `supervise.json` | `refactor_budget_minutes` | リファクタリングのステップの `timeout` と `--budget-minutes`（`templates._inspect_steps`） | `budget` |
| `project.json` | `test_duration` | テストと CI の待ちの `timeout`（`verify_steps.plan_limits`） | `budget` |
| `worktree.json` | `base_branch` | 起点・宛先（ステップの数は変えないが、変わればスプリントの宛先が変わる） | `shape` |
| `worktree.json` | `production_branch` | 承認ゲート 2 と本番のステップを置くか | `shape` |
| `supervise.json` | `release` | 配布のテンプレート | `shape` |
| `supervise.json` | `sync_checks`・`format` | sync と format のステップを置くか | `shape` |
| `supervise.json` | `test` | テストのステップ（`project.json` に `test` が無いとき） | `shape` |
| `project.json` | `test`・`delivery` | テストのステップ・配布の経路 | `shape` |
| `pace.json` | `<進め方>.enabled` | 経路を置くか | `shape` |

**形を決めた設定（`pins`）** は `{setting, digest}` の並びで、`digest` は組んだ時点の値を正規化した JSON の sha256 である。値そのものは持たない
（秘密の値をプランへ書かないため）。今の値が無いことも「違う」として扱う。

### 解決の控え（`<状態ディレクトリ>/refs.json`）

| キー | 型 | 空を許すか | 意味 |
| --- | --- | --- | --- |
| `kind` / `version` | str / int | 許さない | `refs-store` / 1（柱 P6 の型の版） |
| `rows.<名前>.value` | object | 許さない | 解決した値（版なら `{version, base}`） |
| `rows.<名前>.how` | str | 許さない | `decided`（次に空いている版）/ `explicit` / `series` |
| `rows.<名前>.observed` | object | 許さない | 決めたときに読んだ origin の版とタグ（`Observed.as_dict()`） |
| `rows.<名前>.at` | str | 許さない | 決めた時刻（UTC の ISO 8601） |
| `rows.<名前>.history` | list | 空のリストを許す | 決め直しごとに `{from, to, reason, existing, observed, left, at}` を追記する。消さない |

**時系列は追記で持つ。** 決め直しは `value` を上書きするが、前の値は `history` に残る。`version.json` は移行関数で `rows.version` へ読み替え、
書くのは `refs.json` だけにする。`version-plan.json`（差し込み前のプランのコピー）は、プランを書き換えないため作らない。

### 実行の記録の `ref` 行

#2335 の実行の記録の行（`event: "ref"`）の `fields` は次の形である。1 つの参照の解決ごとに 1 行。

| キー | 型 | 空を許すか | 意味 |
| --- | --- | --- | --- |
| `step` | str | 許さない | ステップの id。形を決めた設定の照合は `pins` |
| `name` / `kind` | str | 許さない | 参照の名前と種類。プランの値は `kind: "literal"` |
| `source` | str | 許さない | `ref`（解決した）/ `store`（控えの値）/ `plan:explicit` / `plan:legacy` |
| `value` | any | 許す | 解決した値。秘密の設定はキーごと書かない（I9） |
| `outcome` | str | 許さない | `resolved` / `redecided` / `unresolved` / `mismatch` / `match` |
| `approved` | object | 許す | 承認時の値（照らしたときだけ） |
| `reason` | str | 許す | 解決できない・食い違う・決め直した理由 |

実行の記録の `start` 行の `fields` に `plan` と `plan_digest` を足す。キューの done の項目にも `plan_digest` を足す（キーは足すだけ）。

### CRUD

| 機能 | プランのファイル | 解決の控え | 実行の記録 | 承認資料 | done |
| --- | --- | --- | --- | --- | --- |
| F1 組む | C | — | — | — | — |
| F2 解決 | R | R | C（`ref`） | — | — |
| F3 版 | R | C・U | C | — | — |
| F4 承認時の値 | R | R | C | R | — |
| F5 実行条件 | R | — | C | — | — |
| F6 流し直し | R | — | R（`start`） | — | R・C |
| F7 旧い形 | R | R（`version.json`） | C | — | — |

書く相手が 2 つ以上ある機能は F3（控えと記録）である。記録は追記だけのため、控えを書いてから記録を書く順にする。
落ちたら次の解決が控えを読み、同じ版の `ref` 行を書き直す。

## 入出力の契約

| 項目 | 約束 |
| --- | --- |
| `sprint-state.py loop sprint` | `--dev` を任意にする。渡せば今と同じく `new sprint --version <開発版>` を組む。省けば `--version` を渡さず、周回の `versions` は配布の答え（`settle_release`）で埋まる |
| 解決できないときの結果 | ステップを流さず、プランの結果は「止まった」、結果 JSON は `status: "stopped"`・終了コード 6、`gaps` に `ref:<名前>`、`summary` に参照の名前と理由 |
| 形を決めた設定が違うときの結果 | `status: "stopped"`・終了コード 2、`gaps` に `pin:<設定>`、`next` に同じ引数で組み直す `supervise.py new` のコマンド |
| 承認時の値と食い違うときの結果 | `status: "gate"`・終了コード 10（`EXIT_GATE`）、`items` に `{kind: "ref-mismatch", name, approved, actual, reason}` |
| 完了したプランが変わったときの結果 | 再開とキューの `--resume` は `status: "gate"`・終了コード 10（`EXIT_GATE`）、`items` に `{kind: "plan-changed", plan, digest_done, digest_now}`、`next` に `supervise.py resume <実行の識別子> --from <プラン>=<ステップ>` |
| キューの done | 参照で止まったプランの項目は `result: "止まった"`・`unfilled: true`・`reason`。旧い版の `result: "流さなかった"`・`unfilled: true` の項目も読む |
| 承認資料 | 「2. 承認の判断に使うもの」の表に `| 承認した版 | <版> |` の行を足す。承認の記録（`record_approval`）は今と同じ |
| 旧い形のプラン | `{release_version}`・`{release_base}`・`{queue_record}`・`{queue_pr:<名>}`・`{queue_state(?):<名>}`・`{queue_prs:<名>}`・`{queue_approved:<名>}`・`QUEUE_PRS` は、プランの型の移行関数が参照へ読み替える。受けるのは次の正式版まで |

## 処理の流れ

### プランを流す順

```mermaid
sequenceDiagram
    participant C as キュー・再開
    participant E as プランの実行
    participant F as 参照の部品
    C->>E: プランを流す（開始のステップ）
    E->>E: 持ち主の記録を取る（E3、柱 P3）
    E->>E: 実行条件を評価（E4）
    alt 飛ばす
        E-->>C: 完了（実行条件に当たらない）
    end
    E->>F: check_pins（最初のステップの前）
    alt 形を決めた設定が違う
        E-->>C: stopped・2
    end
    E->>F: resolve_head（作業場所・branch）
    alt 解決できない・食い違う
        F-->>E: RefFailure
        E-->>C: stopped・6 か gate・10
    end
    F-->>E: 値を入れたプランのコピー
    E->>E: 作業ツリーを作る（コピーの作業場所・branch）
    loop ステップごと
        E->>F: resolve（ステップ）
        alt 解決できない
            F-->>E: RefFailure（stopped）
            E-->>C: stopped・6
        else 食い違う
            F-->>E: RefFailure（gate）
            E-->>C: gate・10
        end
        F-->>E: 値を入れたステップ
        E->>E: ステップを流す（E8）
    end
    E-->>C: 結果
```

### 参照を解決する中身

`resolve` の中（E5〜E7）は次のとおりである。

```mermaid
sequenceDiagram
    participant F as 参照の部品
    participant K as 参照の種類
    participant S as 解決の控え
    participant R as 実行の記録
    F->>S: sticky の参照の控えを読む
    F->>K: 種類ごとに解決（E5）
    K-->>F: 値か理由
    F->>F: 承認時の値と照らす（E6）
    F->>S: 決めた・決め直した版を書く
    F->>R: ref 行を書く（E7）
```

図に描かない要素は次のとおりである。

| 要素 | 図に描かない理由 |
| --- | --- |
| 版の判定と承認したコミット | 参照の種類の中で呼ぶ |
| プランの型と解決の控えの型 | 読み書きの型 |
| プランを組むエントリポイント・開発ループ・設定の契約 | 流す前の E1 で働く |
| 承認資料を書くエントリポイント | E2 で働く |
| エントリポイントの契約と配布の本番のエントリポイント | 下の「流れの決まり」の 2 つ目の項目 |
| 差し込みの解決 | 消す |

### 流れの決まり

- **実行条件は E4 で 1 回だけ評価する。** 今の `engine.py` の `and not start` を外す。`queue._release_skipped_by_condition`（版を照会する前に条件を打つ手当て）を消す。実行条件が版の参照より先に評価されるため、配布不要のプランは版を照会しない
- **配布の本番のエントリポイントは、エントリポイントの契約の `refs` で同じ参照の部品を通る。** プランから呼ばれても手動で呼ばれても、核の E5 で `tip` の参照を解決して承認したコミットと照らす。`check_approved` を呼ぶ箇所はこの 1 つになる（`release_lib/approval.py` から `lib/approved_commit.py` へ移す）
- **前のプランの結果は、キューが渡す結果でなく状態ディレクトリから読む。** 今の `results_named` が承認ゲートの後に単独で流すときに使う経路（同じディレクトリのプランの `report.md` と `work/`）を唯一の経路にする。`plans: "earlier"` はキューが done の隣に書く一覧（`<done>.plans.json` の `stages`）から前のステージのプランを引く
- **版の参照の解決は #2164 の判定を順に呼ぶ。** 控えが無ければ `release_version.observe` → `decide_dev` か `taken` で決めて控えを書く。控えがあれば `taken` → `own_released` で確かめ、当たれば `decide_dev` で決め直す（明示の版は止める）。本番は明示の版か系列の開発版の控えの `base` を使う
- **版の決め直しを確かめるのは、1 回の起動で最初の解決（`resolve_head`。上位の欄に版の参照が無ければ最初に版を使うステップの解決）だけである。** その後のステップは、その起動で決めた版をそのまま使う。1 回の起動の中で旧版と新版が混ざらない
- **決め直したら、旧版の成果物を退避して新版の先頭から流す。** 次の順で行い、プランのファイルは書き換えない
  1. 状態ディレクトリのうち、解決の控え（`refs.json`）・退避先（`superseded/`）・状態の実体のコピー（`plan.json`）を除くファイル（ステップの記録・結果・`work/`）を `superseded/v<旧版>/` へ移す
  2. 解決の控えの `rows.version` を新版で上書きし、`history` に `{from, to, reason, existing, observed, left, at}` を追記する。`left` は旧版の `作業場所`・`branch`・退避先である。旧版の作業ツリーとブランチは消さずに残す（消すのは戻せない操作のため）
  3. `ref` 行を `outcome: "redecided"` で書く
  4. 上位の欄を新版で解決し直し、新しい作業場所と `branch` で作業ツリーを作り、最初のステップから流す。旧版の済んだステップの記録は 1 で退避したため、再利用されない

  1 の後で落ちても、済んだステップの記録が無いため、次の起動は最初のステップから流れる。控えを書く前に落ちれば控えの判定で同じ決め直しへ戻り、書いた後に落ちれば新版で流れる。開始のステップ（`--from`）を渡した起動で決め直しが要るときは、旧版の済んだステップを前提にできないため流さず、`stopped`・終了コード 6、`gaps` に `ref:version`、`next` に `--from` を外したコマンドを出す

### 流し直しの判定（E9）

```mermaid
stateDiagram-v2
    [*] --> 照合: 再開・--resume
    照合 --> 流す: 前の結果が完了でない
    照合 --> 流す: --from がある
    照合 --> 流さない: 完了でハッシュが同じ
    照合 --> 流さない: 完了で前のハッシュが無い（旧い done）
    照合 --> 人へ戻す: 完了でハッシュが違う
    流す --> [*]
    流さない --> [*]
    人へ戻す --> [*]
```

`--from` は柱 P3 の決まりどおり常に勝つ（#2342 の I9）。流し直す手段は `--from` の 1 つで、「人へ戻す」の結果はそのコマンドを `next` に出す。

## 非機能の実現方式

| 大項目 | 要求の条件 | 実現方式 | 確かめ方 |
| --- | --- | --- | --- |
| 性能・拡張性 | 参照を持たないエントリポイントの起動に足す時間は #2335 の上限 150 ms の内側。GitHub と origin はその参照を持つステップだけが読む | 核はエントリポイントの契約に `refs` が無ければ参照の部品を読み込まない。種類のモジュールは使う種類だけを読み込む | 参照を持たないエントリポイントの `--help` を前後で 10 回ずつ測り、差の中央値を実装の Pull Request に書く。GitHub を呼ぶ関数を差し替え、参照を持たないプランで呼ばれないことをテストで見る |
| 可用性 | 解決できない・食い違うときは待ち続けずに返る | 種類のモジュールは待ちを持たず、外部の呼び出しは上限の部品（柱 P4）の 1 回の上限で打ち切る | 解決できない・食い違うテストが `stopped` / `gate` で返る |
| 運用・保守性 | 解決した・解決できなかった・照合した・食い違ったを実行の識別子つきで書く | `ref` 行の `outcome` に 5 つの値 | 種類ごとのテストで `ref` 行の `outcome` を見る |
| 移行性 | 旧い形のプランは値のまま流す。`version.json` は解決の記録として読む。2 つの Pull Request をそれぞれ revert すれば戻る | 移行関数（プランと控え）。Pull Request は参照の段（参照の部品・種類・プランの実行・キュー）とエントリポイントの移し（プランを組むエントリポイント・開発ループ・設定の契約）に分ける | 前の版が組んだプランと `version.json` の実物で流すテスト |
| セキュリティ | 秘密と宣言された入力は値を書かない。C1・C2 を変えない | 設定の契約の `secret` が真のキーは、`ref` 行の `value` を書かず、`pins` は digest だけを持つ | 秘密と宣言したキーを参照にしたテスト |
| システム環境 | 版の比較は `versions`（extras）。無い環境では `stopped`・69 | `version` の種類だけが `versions` を読み込み、読めなければ `deps` の判定で返す | extras を外した環境で版の参照を持つプランを流すテスト |

## 決定の記録

決定の記録は [issue-2344-design-decisions.md](issue-2344-design-decisions.md) にある。

## テスト設計

| 受け入れ条件・不変条件 | どの振る舞いで縛るか | どう壊したら落ちるべきか |
| --- | --- | --- |
| 1 | 版を明示しない `new sprint`・`new close`・`loop sprint` が組むプランのファイルに、版数・記録の PR の番号・上の表の `ref` / `budget` の値・予算の秒が無く、`refs` と `pins` がある | 組む時点に `with_margin` の秒を書く／`pace_verify` の値を書く |
| 2・I1・I2 | 流す前と後でプランのファイルの sha256 が同じ。ステップに入った値ごとに `ref` 行がある | 解決した値をプランへ書き戻す／`ref` 行を書かずに流す |
| 3・I4 | 記録の PR が無い・origin を読めない・設定のキーが無い・前のプランの完了した結果が無い、のそれぞれでステップが流れず、`stopped` と `gaps` の `ref:<名前>` と理由が出る | 見つからない PR を `0` で埋める／読めない版を前の版で埋める |
| 4・I5 | 承認したコミットの後に許していないコミットがある・承認した版と解決した版が違う、で本番のステップが流れず `gate` と両方の値。許した配布の PR だけのときは流れる | 照合を飛ばして流す／許した PR まで食い違いとする |
| 5・I6 | 同じプランを流し直すと控えの版を使う。#2164 の決め直しに当たるときだけ版が変わり、`history` と `ref` 行に前の版と理由が残る。決め直すと旧版のステップの記録が `superseded/v<旧版>/` へ移り、新版の作業場所で最初のステップから流れる | 流し直しのたびに次に空いている版を決める／決め直しで `history` を上書きする／旧版の済んだステップの記録を新版で再利用する |
| 6・I3 | #2349 の再現の手順: 実行条件で飛ぶプランを `--from <プラン>=<ステップ>` で始めても飛ばされ、結果に飛ばした旨。実行条件は参照より先に評価され、飛ぶプランは版を照会しない。版を省いた配布のプランは、作業ツリーを版の入った `branch` と作業場所で作る | `and not start` を戻す／参照を実行条件より先に解決する／上位の欄を解決せずに作業ツリーを作る |
| 7・I8 | #2349 の再現の手順: 完了したプランを含む done を `--resume` しても流れない。完了の後にプランを書き足すと `gate` と `--from` のコマンド。キーの並びだけを変えたプランは同じハッシュ | ハッシュを見ずに流し直す／ファイルのバイト列でハッシュを取る |
| I7 | 組んだ後に `production_branch` か `release` を変えると最初のステップが流れず、`stopped`・2 と組み直すコマンド | 形を決めた設定を照らさない |
| 8 | #2354 の再現の手順: `loop sprint` を `--dev` なしで起動でき、組んだスプリントのプランが版の参照を持つ。`--dev` を渡せば版の参照の `explicit` に入り、既出の版なら `stopped`・6 | `--dev` を省いたときに既定の版を入れる／明示の版をリテラルで書いて既出の判定を飛ばす |
| 9 | #2164 の受け入れ条件 1〜10 と #2230 の受け入れ条件の既存のテストが通る。`supervise_lib/queue_fill.py` が無く、`queue.py` に `fill_` で始まる関数が無い | 差し込みの関数をキューに残して二重に解決する |
| 10・F7 | 前の版が組んだプラン（差し込みのもの・値を書き込んだもの）と `version.json` を持つ状態ディレクトリで流れ、`ref` 行の `source` が `plan:legacy` / `store` | 移行関数を外す／旧い差し込みを文字のまま流す |
| 11・I9 | 秘密と宣言したキーを参照にすると、記録と結果に値が無く出所の名前だけがある | `secret` を見ずに `value` を書く |
| 12 | （手動）子の課題 4 件の再現の手順を本番へ出した版で通し、課題の本文の表に結果を書く | — |
| 13 | 参照を持たないエントリポイントとプランの振る舞いと結果が前と同じ。全体テスト `uv run --frozen --project . --all-extras pytest . -q -n 4` が前後で通る | 参照を持たないプランでも参照の部品が GitHub を読む |

## 設計の結果

| 課題 | 扱い | 取り込み先 | 触るファイル |
| --- | --- | --- | --- |
| #2344 | 実装する | — | `plugins/ndf/scripts/lib/refs.py`、`plugins/ndf/scripts/lib/ref_kinds/`、`plugins/ndf/scripts/lib/model/`、`plugins/ndf/scripts/lib/approved_commit.py`、`plugins/ndf/scripts/lib/sprint_loop.py`、`plugins/ndf/scripts/lib/sprint_loop_cli.py`、`plugins/ndf/scripts/release_lib/approval.py`、`plugins/ndf/scripts/release-steps.py`、`plugins/ndf/scripts/supervise_lib/`、`plugins/ndf/scripts/tests/`、`plugins/ndf/contracts/settings/`、`plugins/ndf/contracts/entrypoints/`、`scripts/contracts_lib/model.py`、`docs/glossary/glossary.json`、`docs/glossary.md` |

子の課題 4 件（#2230・#2164・#2349・#2354）は表に載せない。表の行はスプリントの Pull Request で閉じる。
子の課題は、本番へ出した版で再現の手順を通してから、閉じるか残すかを決める（受け入れ条件 12）。
#2349 の 3 つ目の事象は、差分の無い実装のプランで `pr` が終了コード 1 になることである。
これは柱 P1（#2337）の結果の契約の問題で、この柱では直さない（要求の「含まない」）。

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 柱 P3・P6・P8 の設計 | 2026-11-04 の時点で P6（#2339）と P8（#2341）の設計文書は無く、P3（#2342）は設計 Pull Request 2480 が開いた状態である。この設計はそれらの語（`ctx.resolve`・`ctx.log`・プランの型・入力の宣言・`supervise.py resume`）にだけ依る。形が変わったら実装の開始時に読み直す（要求の未決 5） |
| 上限の方針の名前 | `budget` の種類が引く方針の名前は柱 P4（#2343）の上限の宣言が決める。この設計は名前を決めない |
| プランのキーの名前 | #2335 の決定 4 でプランの日本語のキーは英語へそろう。この文書の `実行の条件`・`版` などは今の名前で書いた。英語の名前は柱 P6 が決める |
| 形を決めた設定の範囲 | `base_branch` を `shape` に置いたのは、宛先が変わればスプリントの PR の向きが変わるためである。値だけを差し替えて流してよいかは、実装の前に `new sprint` が `base_branch` を書く箇所を数えて確かめる |
| 承認した版の行の書き手 | 承認資料を書く `approval-facts` が、配布の本番のプランの版の参照をどの時点で読むか（承認ゲート 2 の提示の前に版が決まっていること）は、提示のプランの流れで確かめる |
| 設定のキーの一覧の網羅 | 表は `apply_decls` と `sprint_routes` の読む項目から作った。ほかのプランを組む関数が設定を読んでステップへ書いていないかは、実装の開始時に `read_decl` と `read_pace` の呼び出し元を数えて確かめる |
| 並行するスプリントの版の取り合い | 2 つのスプリントが同時に次に空いている版を決めたときは、#2164 の `own_released` の確かめで後から決め直す。版の参照を持ち主の記録で排他にするかは測っていない（#2354 の起票者の推定 2 点目） |
