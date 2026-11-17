# #3703 の詳細設計: 本番リリースのマージと trunk のスプリントの計画

設計文書 [issue-3703-design.md](issue-3703-design.md) の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。決定の番号（決定 1〜12）は [issue-3703-design-decisions.md](issue-3703-design-decisions.md) を指す。不変条件の番号（I1〜I9）と受け入れ条件の番号は、設計文書を指す。

この文書で使う語:

| 語 | 意味 |
| --- | --- |
| 本番の取り込み | 承認した head を、検査がそろうのを待ってから、ブランチ戦略の宣言のマージの主体と方式（`land_approved`）で本番ブランチへ入れること（設計文書の用語） |
| 公開の欠け | 本番ブランチへまだ入っていないため、タグと GitHub Release を作らずに `stopped`・終了コード 6 で返すこと |
| trunk の本番の経路 | 版上げの PR が本番の PR を兼ねる取り込み方（`strategy_route.route_of` の `release_pr` が真で `sync` が偽）の `release --channel prod` |
| 分かれた経路 | ベースブランチと本番ブランチが分かれた取り込み方（`sync` が真）の `release --channel prod` |

## ファイル配置

### パッケージ・モジュール構成

触るファイルだけを載せる。`*` は新設である。

```text
plugins/ndf/
├── scripts/
│   ├── release-steps.py
│   ├── lib/
│   │   └── release_version.py
│   ├── release_lib/
│   │   ├── prod_land.py *
│   │   ├── trunk_release.py *
│   │   ├── reviewed.py
│   │   ├── strategy_route.py
│   │   └── materials.py
│   ├── supervise_lib/
│   │   ├── decl.py
│   │   ├── sprint.py
│   │   ├── sprint_routes.py
│   │   ├── sprint_waves.py
│   │   ├── release_templates.py
│   │   ├── release_cd.py
│   │   └── queue_fill.py
│   └── tests/
│       ├── test_release_steps.py
│       ├── test_release_cd.py
│       ├── test_release_selected.py
│       ├── test_release_production_flow.py
│       ├── test_release_version.py
│       ├── test_release_materials.py
│       ├── test_supervise_new.py
│       ├── test_supervise_contract.py
│       └── fixtures/
│           ├── supervise-contract/plans/{sprint,close}/*.json
│           └── release_notes/approval-1863.txt
└── skills/
    ├── release/references/release-steps.md
    └── development-workflow/references/approval-request.md
docs/
├── glossary/glossary.json
└── glossary.md
```

### 依存の向き

依存の向きは一方向で、循環は無い。

- リリースの側は、次の順に読む: `release-steps.py` → `trunk_release.py`・`reviewed.py`・`strategy_route.py` → `prod_land.py` → `land.py`・`reviewed.py` の待ちの関数（`wait_ci_except`・`wait_trail_status`）
- 計画の側は、次の向きで読む: `sprint.py`・`sprint_routes.py`・`sprint_waves.py`・`release_templates.py`・`release_cd.py` → `decl.py` の `trunk_release` → `release_lib/strategy_route.py` の `route_of`
- `prod_land.py` は `reviewed.py` の待ちの関数を読む。`reviewed.py` は `prod_land.py` を読まず、呼び手が組んで渡す操作の集まり（`ProdOps.land`）を通して呼ぶ
- `trunk_release.py` と `strategy_route.py` は、`release-steps.py` のモジュールを操作の集まり（`ops`）として受け取り、`release-steps.py` を読み込まない
- `route_of` は関数の中で遅れて読み込む。計画の組み立ての起動に、GitHub の照会の部品を読ませないためである

### ファイルごとの役割

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/scripts/release_lib/prod_land.py` | 新設 | 本番の取り込みの 1 か所。merge queue の状態を読み、検査を待ち、PR の今の head と照らし、`land_approved` を呼んで結果を写す | `release_lib/land.py`・`release_lib/reviewed.py`・`release_lib/approval.py`・`lib/gh_graphql.py`・`lib/waits.py`・`lib/limits.py` |
| `plugins/ndf/scripts/release_lib/trunk_release.py` | 新設 | CD の無い trunk の本番。版上げの PR を開き、承認が無ければ承認ゲートで止め、承認と head が一致すれば本番の取り込みへ渡す | `release_lib/prod_land.py`・`release_lib/reviewed.py`・`release_lib/approval_trail.py`・`lib/approved_commit.py` |
| `plugins/ndf/scripts/release-steps.py` | 変更 | `release` の経路の振り分けと公開の欠けの出力。本番ブランチ宛ての `wait_and_merge` を外す | `release_lib/prod_land.py`・`release_lib/trunk_release.py` |
| `plugins/ndf/scripts/release_lib/reviewed.py` | 変更 | CD のある本番のマージを `ProdOps.land` へ替え、迂回の記録の二重書きを外す | 無し（`ProdOps` で受ける） |
| `plugins/ndf/scripts/release_lib/strategy_route.py` | 変更 | 選んで集める取り込み方の CD の無い本番のマージを `ops.land` へ替える | 無し（`ops` で受ける） |
| `plugins/ndf/scripts/release_lib/materials.py` | 変更 | 承認資料の欄の名前を「承認前の試用で確かめたこと」にする | 無し |
| `plugins/ndf/scripts/lib/release_version.py` | 変更 | 系列の無い本番の正式版を origin から決める関数を足す | `lib/versions.py` |
| `plugins/ndf/scripts/supervise_lib/decl.py` | 変更 | 計画の引数のスナップショットから trunk の本番の経路かを返す | `release_lib/strategy_route.py` |
| `plugins/ndf/scripts/supervise_lib/sprint.py` | 変更 | trunk では開発版のステージを置かずに本番のステージを組む | `supervise_lib/sprint_routes.py`・`supervise_lib/decl.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_routes.py` | 変更 | trunk の本番のステージと、承認前の試用のコマンドを組む | `supervise_lib/decl.py` |
| `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | 変更 | trunk の本番のプランへ系列を渡さず、試用のコマンドを渡す | `supervise_lib/sprint_routes.py`・`supervise_lib/decl.py` |
| `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | 種類 `prod-trunk`、ステップ `trial`・`release-landed`、系列の無い版の欄 | `supervise_lib/decl.py`・`supervise_lib/release_cd.py` |
| `plugins/ndf/scripts/supervise_lib/release_cd.py` | 変更 | CD のある trunk の本番で、release の 10 の行き先を `trial` にする | `supervise_lib/decl.py` |
| `plugins/ndf/scripts/supervise_lib/queue_fill.py` | 変更 | 明示の版も系列も無い本番のプランの版を決めて記録する | `lib/release_version.py` |
| `plugins/ndf/skills/release/references/release-steps.md` | 変更 | 欄の名前・trunk の本番の流れ・公開の欠け（6）の説明 | 無し |
| `plugins/ndf/skills/development-workflow/references/approval-request.md` | 変更 | 承認資料の欄の名前 | 無し |
| `plugins/ndf/scripts/tests/test_release_steps.py` | 変更 | 本番の取り込みの 4 経路・公開の欠け・打ち直しのテスト | 無し |
| `plugins/ndf/scripts/tests/test_release_cd.py` | 変更 | CD のある本番の `ProdOps.land` と迂回の記録のテスト | 無し |
| `plugins/ndf/scripts/tests/test_release_selected.py` | 変更 | 選んで集める取り込み方の本番の `ops.land` のテスト | 無し |
| `plugins/ndf/scripts/tests/test_release_production_flow.py` | 変更 | trunk の本番のプランを偽のコマンドで流すテスト | 無し |
| `plugins/ndf/scripts/tests/test_release_version.py` | 変更 | `decide_release` と `_fill_prod` の系列の無い版のテスト | 無し |
| `plugins/ndf/scripts/tests/test_release_materials.py` | 変更 | 欄の名前のテストの入力 | 無し |
| `plugins/ndf/scripts/tests/test_supervise_new.py` | 変更 | trunk の `new sprint`・`new close` が開発版のプランを作らないテスト | 無し |
| `plugins/ndf/scripts/tests/test_supervise_contract.py` | 変更 | 計画の契約のコピーを trunk で作り直す | 無し |
| `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/sprint/sprint.json` | 変更 | 契約のコピー（ステージの並び） | 無し |
| `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/sprint/8-release.json` | 変更 | 契約のコピー（開発版のプラン。trunk では消えるか番号が詰まる） | 無し |
| `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/sprint/10-release-prod.json` | 変更 | 契約のコピー（本番のプラン） | 無し |
| `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/close/sprint.json` | 変更 | 契約のコピー（スプリントの終わりのステージの並び） | 無し |
| `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/close/3-release.json` | 変更 | 契約のコピー（スプリントの終わりの開発版のプラン） | 無し |
| `plugins/ndf/scripts/tests/fixtures/supervise-contract/plans/close/4-release-prod.json` | 変更 | 契約のコピー（スプリントの終わりの本番のプラン） | 無し |
| `plugins/ndf/scripts/tests/fixtures/release_notes/approval-1863.txt` | 変更 | 承認資料の入力の欄の名前 | 無し |
| `docs/glossary/glossary.json` | 変更 | 用語「本番の取り込み」を足す | 無し |
| `docs/glossary.md` | 変更 | `glossary.py render` で作り直す | `docs/glossary/glossary.json` |

契約のコピーのファイル名の並び（`8-release.json` などの番号）は、実装で計画を作り直した結果で決まる（設計文書の「未確認のまま残ること」）。

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `release_lib/prod_land.py` | モジュール | 新設: `LandPending`（例外）・`land`・`queue_state`・`wait_queued`・`landed_commit`・`pending_result`。`release-steps.py` から移す: `pr_check_buckets`（シグネチャと責務は変えない） |
| `release_lib/trunk_release.py` | モジュール | 新設: `release_trunk`・`open_release_pr`・`read_approval`・`waiting_out`。`open_release_pr` は `release-steps.py` の `_open_trunk_release_pr` を移したもの |
| `release-steps.py` | モジュール | 変更: `cmd_release`・`_merge_prod_pr`・`wait_and_merge`・`_prod_ops`（`land` を渡し `wait_and_merge` を渡さない）・`cmd_approval_facts`（欄の名前を `materials.TRIAL_ROW` から引く）。削除: `_open_trunk_release_pr`（`trunk_release.open_release_pr` へ移す）・`pr_check_buckets`（`prod_land` へ移し、同じ名前で読み込む） |
| `release_lib/reviewed.py` | `ProdOps` | 変更: 欄 `wait_and_merge` を外し、欄 `land`（`prod_land.land` と同じシグネチャ）を足す |
| `release_lib/reviewed.py` | モジュール | 変更: `merge_on_pr_approval` |
| `release_lib/strategy_route.py` | モジュール | 変更: `_merge_approved`。`release_selected` の説明文の `ops` の一覧を `land` へ直す（振る舞いは変えない） |
| `release_lib/materials.py` | モジュール | 新設: 定数 `TRIAL_ROW`。変更: `write_approval`（欄の名前を `TRIAL_ROW` から引く。シグネチャ・責務・失敗の扱いは変えない） |
| `lib/release_version.py` | モジュール | 新設: `decide_release` |
| `supervise_lib/decl.py` | モジュール | 新設: `trunk_release` |
| `supervise_lib/sprint.py` | モジュール | 変更: `sprint_plans`・`fast_sprint_plans`・`auto_sprint_plans`・`close_waves` |
| `supervise_lib/sprint_routes.py` | モジュール | 新設: `trunk_prod_waves`・`trial_verify` |
| `supervise_lib/sprint_waves.py` | モジュール | 変更: `_release_fields` |
| `supervise_lib/release_templates.py` | モジュール | 新設: `_trial_step`・`_landed_step`・`_trunk_prod_approvals`・定数 `RULE_RELEASE_PROD_TRUNK`・`RELEASE_KINDS` の行 `prod-trunk`。変更: `version_field`・`_release_kind`・`_release_step`・`approve_step`・`plan_release_package_plugin` |
| `supervise_lib/release_cd.py` | モジュール | 変更: `cd_release_wait`・`cd_prod_steps` |
| `supervise_lib/queue_fill.py` | モジュール | 変更: `_fill_prod` |

`ProdOps` に `queue_state` は置かない。merge queue の状態を読むのは `prod_land.land` の中だけで、4 つの呼び出し元のどれも merge queue の状態を自分で読まないためである。設計文書の「構造」の図はこの形に合わせてある。

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `prod_land.land` | `plugins/ndf/scripts/release_lib/prod_land.py` | 新設 | `land(root: Path, a: Namespace, pr: int, head: str, trail_url: str = "") -> str \| None` | 承認した head `head` を、merge queue の状態・検査・PR の今の head を確かめてから宣言の主体と方式で本番ブランチへ入れる | `pr` は本番ブランチ宛ての PR。`head` は呼び手が承認と照らした SHA（trunk と CD の経路は承認した head、分かれた経路は比べた先端）。`a.ci_wait` が秒の数 | 戻ったときは PR が本番ブランチへ入っている。戻り値はマージコミットか、読めなければ `None`（呼び手が `origin/<本番ブランチ>` を読む）。入っていなければ必ず例外で抜ける | GitHub の REST と GraphQL の読み書き（マージ・キューへの投入・コメント・迂回の記録）、`git fetch` |
| `prod_land.queue_state` | `plugins/ndf/scripts/release_lib/prod_land.py` | 新設 | `queue_state(root: Path, pr: int) -> str` | PR の `merged`・`state`・`isInMergeQueue` を 1 回の GraphQL の照会で読み、4 値に写す | `root` の origin から owner/repo を決められる | 戻り値は `merged`・`queued`・`open`・`closed` のどれか | GitHub の GraphQL の読み取り |
| `prod_land.wait_queued` | `plugins/ndf/scripts/release_lib/prod_land.py` | 新設 | `wait_queued(root: Path, pr: int, timeout: float) -> str` | merge queue にある PR が `queued` から外れるまで、上限 `timeout` 秒まで読み直す | `queue_state(root, pr)` が直前に `queued` を返した | 戻り値は最後に読んだ状態。`queued` なら上限に当たった | GitHub の GraphQL の読み取り（間隔は `release-steps.ci-wait` の `interval`） |
| `prod_land.landed_commit` | `plugins/ndf/scripts/release_lib/prod_land.py` | 新設 | `landed_commit(root: Path, pr: int, head: str) -> str \| None` | ruleset の無い `strict` の早送りが PR を閉じて入れた取り込みを、本番ブランチの取り込みの記録（`GhPort.landed_record`）から引く | `pr` は閉じた版上げの PR。`head` は承認の SHA | 記録の head が `head` と同じで、tree が一致し、記録の先端が最初の親のときだけ取り込みのコミットを返す。それ以外は `None` | `git fetch`・`git log`・`git rev-parse` |
| `prod_land.pending_result` | `plugins/ndf/scripts/release_lib/prod_land.py` | 新設 | `pending_result(plugin: str, version: str, prod: str, tag: str, pending: LandPending, nxt: str) -> dict` | 公開の欠けの結果（`stopped`・`gaps` に公開の行・`next` に打ち直し）を組む | `pending.result` が `queued` か `human` | 戻り値は `lib/step_result.py` の形。`items` に `result` が `pending.result` の PR の行が 1 行、`gaps` に `kind: publish` の行が 1 行 | 無し |
| `trunk_release.release_trunk` | `plugins/ndf/scripts/release_lib/trunk_release.py` | 新設 | `release_trunk(ctx: ReleaseCtx, ops) -> tuple[int, str, list[dict], dict]` | CD の無い trunk の本番で、版上げの PR を開き、承認が無ければ承認ゲートで止め、承認と head が一致すれば本番の取り込みへ渡す | `ctx.sync` が偽。`ctx.branch` は push 済みの `release/v<版>`。`ops` は `release-steps.py` のモジュール | 戻ったときは (版上げの PR, マージコミット, 結果の行, 計測) で、PR は本番ブランチへ入っている。呼び手が `_published` で公開する | GitHub の PR の読み書き、承認資料の読み取り、`git fetch`・`git rev-parse` |
| `trunk_release.open_release_pr` | `plugins/ndf/scripts/release_lib/trunk_release.py` | 新設 | `open_release_pr(ctx: ReleaseCtx, ops) -> int` | 開いた版上げの PR（`release/v<版>` → 本番ブランチ）を使い回すか作り、番号を返す | 呼び手がマージ済みの PR の有無を先に確かめた | 戻り値の PR は開いている。同じ head と宛先の PR を 2 本作らない | GitHub の PR の作成 |
| `trunk_release.read_approval` | `plugins/ndf/scripts/release_lib/trunk_release.py` | 新設 | `read_approval(ctx: ReleaseCtx) -> str \| None` | `--approval` の承認資料から承認したコミットを読み、承認がまだ無ければ `None` を返す | `ctx.a.approval` が承認資料のパス（相対なら `ctx.root` から） | 資料が無い・承認の記録が無い・記録の SHA が違うときは `None`。資料の欄の形が壊れていれば例外 | 承認資料の読み取り |
| `trunk_release.waiting_out` | `plugins/ndf/scripts/release_lib/trunk_release.py` | 新設 | `waiting_out(ctx: ReleaseCtx, pr: int, head: str, approved: str \| None, items: list[dict], metrics: dict, moved: bool = False) -> dict` | 承認ゲート（10）の結果を組み、`next` に承認資料の作り直しと承認の取り直しを載せる | 無し | 戻り値は `status: gate` の結果。`items` に `result: awaiting_approval` の PR の行を足したもの | 無し |
| `cmd_release` | `plugins/ndf/scripts/release-steps.py` | 変更 | `cmd_release(a) -> NoReturn`（変えない） | trunk の本番を承認を読む前に `trunk_release.release_trunk` へ回し、どの経路の `LandPending` も公開の欠け（6）で返す | 今と同じ | 戻らない（`emit` で終わる）。`LandPending` が上がったときはタグと GitHub Release を作っていない | 今と同じ |
| `_merge_prod_pr` | `plugins/ndf/scripts/release-steps.py` | 変更 | 変えない | 分かれた経路の CD の無い本番の PR を、比べた先端で `prod_land.land` へ渡す | 今と同じ | 戻ったときは PR が本番ブランチへ入っている。`queued`・`human` は `LandPending` で抜ける | GitHub の PR の読み書き（`prod_land.land` 経由） |
| `wait_and_merge` | `plugins/ndf/scripts/release-steps.py` | 変更 | `wait_and_merge(root, n, ci_wait=3600.0, admin=None) -> str \| None` | ベースブランチ宛ての PR（開発版の PR・`-sync` の PR）を `merge-when-green` で待ってマージする | `n` はベースブランチ宛ての PR | 引数 `expect`・`gate_approved` と `HeadMoved` の分岐を外す（本番ブランチ宛ての呼び出しが無くなり、渡す呼び手が無い） | `merged-steps.py merge-when-green` の起動 |
| `reviewed.merge_on_pr_approval` | `plugins/ndf/scripts/release_lib/reviewed.py` | 変更 | 変えない | CD のある本番の PR を、PR の上の承認と head が一致するときだけ `ops.land` で入れる | 今と同じ | 戻ったときは PR が本番ブランチへ入っている。迂回の記録の行を自分で足さない（`land_approved` が書く。決定 10）。`queued`・`human` は `LandPending` で抜ける | GitHub の読み書き（`ops.land` 経由） |
| `strategy_route._merge_approved` | `plugins/ndf/scripts/release_lib/strategy_route.py` | 変更 | 変えない | 選んで集める取り込み方の CD の無い本番の PR を、比べた先端で `ops.land` へ渡す | 今と同じ | 今と同じ。`queued`・`human` は `LandPending` で抜ける | GitHub の読み書き（`ops.land` 経由） |
| `release_version.decide_release` | `plugins/ndf/scripts/lib/release_version.py` | 新設 | `decide_release(obs: Observed, level: str = "patch") -> Decision` | 最新のタグ・本番ブランチの版・ベースブランチの版の基底のうち最も大きいものを上げ幅で上げた正式版を決める | 無し | `obs.unreadable` があれば `version` が `None` の決定。無ければ接尾辞の無い版で `how` が `decided` | 無し |
| `_fill_prod` | `plugins/ndf/scripts/supervise_lib/queue_fill.py` | 変更 | 変えない | 本番のプランの版を、明示の版・系列の開発版の記録・origin から決めた版のどれかで決めて記録する | 今と同じ | 「版」の欄の `系列` が空で `明示` も空なら `decide_release` で決めて記録（`how: decided`）を書く。記録があれば決め直さない（I7） | 状態ディレクトリの `version.json` の書き込み、`git fetch` |
| `decl.trunk_release` | `plugins/ndf/scripts/supervise_lib/decl.py` | 新設 | `trunk_release(a) -> bool` | 計画の引数のスナップショットから、版上げの PR があり開発版の PR を作るステップが無い取り込み方かを返す | `a.flow` が `pin_flow` か `_strategy_snapshot` で作ったスナップショット | 真なら `route_of(...).release_pr` が真で `sync` が偽 | 無し |
| `sprint.sprint_plans` | `plugins/ndf/scripts/supervise_lib/sprint.py` | 変更 | 変えない | normal のステージを組み、trunk では「デプロイ」とリリース承認ゲートのステージを置かずに本番のステージを置く | 今と同じ | trunk では戻り値にチャネル `dev` のプランが無い | 無し |
| `sprint.fast_sprint_plans` | `plugins/ndf/scripts/supervise_lib/sprint.py` | 変更 | 変えない | fast のステージを組み、trunk では「開発版」のステージを置かない | 今と同じ | trunk では戻り値にチャネル `dev` のプランが無い | 無し |
| `sprint.auto_sprint_plans` | `plugins/ndf/scripts/supervise_lib/sprint.py` | 変更 | 変えない | auto のステージを組み、trunk では「開発版」のステージを置かない | 今と同じ | trunk では戻り値にチャネル `dev` のプランが無い | 無し |
| `sprint.close_waves` | `plugins/ndf/scripts/supervise_lib/sprint.py` | 変更 | 変えない | スプリントの終わりのステージを組み、trunk では「開発版」のステージを置かない | 今と同じ | trunk では戻り値にチャネル `dev` のプランが無い | 無し |
| `sprint_routes.trunk_prod_waves` | `plugins/ndf/scripts/supervise_lib/sprint_routes.py` | 新設 | `trunk_prod_waves(a, repo: str, build: Callable[[], dict], then_of: str, note: str \| None = None, prs: list[str] \| None = None, condition: dict \| None = None) -> list[dict]` | trunk の本番のステージ 1 つ（`then_of` へつなぐ）を組む | `decl.trunk_release(a)` が真 | 戻り値はステージ 1 つの並び。プランは `prod_plans(a, repo, build, prs, condition)` の値 | 無し |
| `sprint_routes.trial_verify` | `plugins/ndf/scripts/supervise_lib/sprint_routes.py` | 新設 | `trial_verify(a) -> str \| None` | fast・auto の承認前の試用のコマンドを、`.ndf/pace.json` の `verify` の `{ref}` を `release/v{release_version}` へ置き換えて返す | `a.pace` が fast か auto のときに値を持つ | normal では `None`。fast・auto では `{ref}` を含まない文字列 | `.ndf/pace.json` の読み取り |
| `sprint_waves._release_fields` | `plugins/ndf/scripts/supervise_lib/sprint_waves.py` | 変更 | 変えない | 本番のプランの引数で、trunk なら系列を渡さず、試用のコマンド（`trial_cmd`）を渡す | 今と同じ | trunk の本番では `series` が `None`、`trial_cmd` が `trial_verify(a)` の値 | `.ndf/pace.json` の読み取り（`trial_verify` 経由） |
| `release_templates.version_field` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | 変えない | 本番で明示の版も系列も無いとき、trunk の計画なら組み、そうでなければ今どおり止める | 今と同じ | trunk の本番では `明示`・`系列` がどちらも `None` の欄を返す | 無し |
| `release_templates._release_kind` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | `_release_kind(dev: bool, on_pr: bool, mvv, trunk: bool = False) -> str` | CD の無い trunk の本番に種類 `prod-trunk` を返す | 無し | `trunk` が真で `dev`・`on_pr` が偽なら `prod-trunk`（MVV 判定の有無によらない）。ほかは今と同じ | 無し |
| `release_templates._release_step` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | `_release_step(a, v: str, dev: bool, repo: str \| None, approval: str, on_pr: release_case.Case \| None = None, trunk: bool = False) -> dict` | 本番の release の 6 を `release-landed` へ送り、CD の無い trunk では 10 を `trial` へ送る | 今と同じ | 本番の `on_exit` に `"6": "release-landed"` がある。CD の無い trunk の normal は `gate_next: trial`、MVV 判定のプランは `on_exit` の `"10": "trial"` | 無し |
| `release_templates._trial_step` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 新設 | `_trial_step(a, repo: str \| None, v: str, rts: str) -> dict` | 版上げの PR の head（`release/v<版>`）から導入を確かめるステップ `trial` を組む | 無し | `a.trial_cmd` があればそのコマンド、無ければ `verify-install --ref release/v<版> --expect <版> --runtimes <rts>`。`next: facts`、`on_fail: judge` | 無し |
| `release_templates._landed_step` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 新設 | `_landed_step(release: dict) -> dict` | release と同じコマンドを打つステップ `release-landed` を組み、6 で止める | `release` は `_release_step` の本番の値 | id が `release-landed`、`on_exit` の `"6"` が `stop`、`gate_next: end`、ほかは `release` と同じ | 無し |
| `release_templates._trunk_prod_approvals` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 新設 | `_trunk_prod_approvals(c: _KindArgs) -> list[dict]` | 種類 `prod-trunk` の trial → facts → explain → others →（MVV 判定）→ approve を組む | `c.a.channel` が `prod` | facts・others は `--head release/v<版>`、explain は `--ref release/v<版>`。normal は others の `next: end`、MVV 判定のプランは others → mvv → note → approve | 無し |
| `release_templates.approve_step` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | `approve_step(a, repo: str \| None, approval: str, facts_from: str \| None = None, by: str = "user", next_id: str = "bump") -> dict` | 承認の記録を書くステップを、facts の出力の場所・担い手・次のステップを指定して組む | 今と同じ | 引数を省けば今と同じ値。trunk では `--facts-from {state_dir}`・`next: release` | 無し |
| `release_templates.plan_release_package_plugin` | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 変更 | 変えない | 本番のプランに `release-landed` を置き、trunk では種類 `prod-trunk` か CD のある種類に `trial` を足す | 今と同じ | trunk の本番では `trial` が release の 10 の行き先で、facts より前にある | 無し |
| `release_cd.cd_release_wait` | `plugins/ndf/scripts/supervise_lib/release_cd.py` | 変更 | 変えない | CD のある本番の release の 10 の行き先を、trunk なら `trial`、そうでなければ `facts` にする | 今と同じ | trunk では `gate_next` か `on_exit` の 10 が `trial` | 無し |
| `release_cd.cd_prod_steps` | `plugins/ndf/scripts/supervise_lib/release_cd.py` | 変更 | 変えない | CD のある本番の承認のステップを組み、trunk なら先頭に `trial` を置く | 今と同じ | trunk では最初のステップが `trial` で、その `next` が `facts` | 無し |

`prod_land.land` が投げる例外は次の 4 つである。

- `HeadMoved`（今の型）
- `LandPending`（新設）
- `StepError`（終了コード 1・75）
- `approval_trail.TrailError` を `StepError` へ包んだもの

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `prod_land.land` | merge queue にあり、上限までにマージされない | merge queue の検査が `--ci-wait` を超える | `wait_queued` の戻り値が `queued` | 止める（`StepError`、75） | `stopped`・75。summary に PR と merge queue に残っていること | 本番ブランチも merge queue も変えない。同じコマンドで打ち直せる |
| `prod_land.land` | PR が閉じている | 承認の後に人が閉じた・merge queue から外れて閉じた | `queue_state` が `closed` | 止める（`StepError`、1） | `stopped`・1。summary に PR が閉じていること | 変えない。開き直して承認を取り直してから打ち直す |
| `prod_land.land` | 検査の中身が失敗 | 承認した head の CI が落ちた | `reviewed.wait_ci_except` | 止める（`StepError`、1） | `stopped`・1。落ちたチェックの名前 | 変えない。直して承認を取り直す |
| `prod_land.land` | 検査が上限までに終わらない | CI の基盤の待ち | `reviewed.wait_ci_except` | 止める（`StepError`、75） | `stopped`・75 | 変えない。同じコマンドで打ち直せる |
| `prod_land.land` | 承認の証跡のチェックが success にならない | 証跡の status が failure か付かない | `reviewed.wait_trail_status` | 止める（`StepError`、1） | `stopped`・1 | 変えない。証跡を書き直して打ち直す |
| `prod_land.land` | PR の今の head が `head` と違う | 承認の後に版上げのブランチへ push された | `GhPort.pull` の `head` と `head` の比較 | 呼び出し元へ返す（`HeadMoved`） | 呼び手の扱いによる（trunk・CD は 10、分かれた経路は比べ直し） | 変えない。呼び手が比べ直すか承認を取り直す |
| `prod_land.land` | `land_approved` が `queued` を返した | 宣言が `merge-queue` | `land_approved` の戻り値 | 呼び出し元へ返す（`LandPending("queued")`） | `cmd_release` が `stopped`・6、`gaps` に公開の行 | merge queue に入ったまま。打ち直しで `queue_state` から続ける |
| `prod_land.land` | `land_approved` が `human` を返した | 宣言が `merge.actor: human`、または照会の後に人がマージした | `land_approved` の戻り値 | 呼び出し元へ返す（`LandPending("human")`） | `cmd_release` が `stopped`・6、reason「人のマージを待つ」 | 変えない。人がマージした後に打ち直すと、マージ済みの再開の経路で公開する |
| `prod_land.land` | `land_approved` が `stopped` を返した | ruleset の食い違い・ドラフト・宛先の違い・迂回の記録の書き込みの失敗など | `land_approved` の戻り値 | 止める（`StepError`、1。理由は `reason`） | `stopped`・1。summary に `reason` | 理由ごとに直してから打ち直せる。迂回の記録だけが落ちた場合は、打ち直すと `land_approved` のマージ済みの分岐が記録を補う |
| `prod_land.land` | 宣言・PR・ruleset・merge queue を読めない | 宣言の誤り・GitHub の照会の失敗 | `repo_lib.branch_strategy_of`・`LandError`・`TrailError` | 止める（`StepError`。宣言は `StrategyError` の終了コード、ほかは 1） | `stopped`・宣言の終了コードか 1 | 変えない。原因を直して打ち直せる |
| `prod_land.queue_state` | GraphQL の照会に落ちた・PR が応答に無い | GitHub の障害・権限・番号の誤り | `gh_graphql.graphql` の `ok` と応答の形 | 止める（`StepError`、1）。merge queue に無いとみなさない | `stopped`・1。照会の失敗の理由 | 変えない。打ち直せる |
| `prod_land.wait_queued` | 読み直しの途中で照会に落ちた | GitHub の障害 | `queue_state` | 呼び出し元へ返す（`StepError` をそのまま上げる） | `stopped`・1 | 変えない。打ち直せる |
| `prod_land.landed_commit` | 本番ブランチの取り込みの記録を読めない | `git fetch` の失敗（ネットワーク・権限）・`git log` の失敗 | `GhPort.landed_record` の `git log` の終了コード（`git fetch` の失敗は手元の `origin/<本番ブランチ>` を読む） | 呼び出し元へ返す（`None`。取り込み済みとみなさない） | 出力しない。`release_trunk` は判定表の 2 以降へ進む。本番ブランチに版が既にあれば 2 で止まる（`stopped`・1） | 本番ブランチを変えない。通信を直して打ち直すと記録を読み直す |
| `prod_land.landed_commit` | 記録はあるが照合が合わない | 記録の head が承認の SHA と違う・tree が違う・最初の親が先端でない（別の取り込み・手で作ったコミット） | `GhPort.landed_record` の `head`・`tree_ok`・`first_parent_is_tip` | 呼び出し元へ返す（`None`。承認と別の中身を公開しない） | 出力しない。`release_trunk` は判定表の 2 以降へ進み、本番ブランチに版が既にあれば 2 で止まる（`stopped`・1） | 変えない。人が本番ブランチの中身を確かめる |
| `trunk_release.release_trunk` | 承認が無い・承認した head と今の head が違う | 承認の前の 1 回目の実行・承認の後の push | `read_approval` が `None`、または承認の SHA と PR の head の比較 | 止める（承認ゲート） | `gate`・10。`next` に承認資料の作り直しと承認の取り直し | PR は開いたまま。承認の後に打ち直す |
| `trunk_release.release_trunk` | 承認ゲートの前の検査の失敗・上限 | CI | `reviewed.wait_ci_except` | 止める（`StepError`、1 か 75） | `stopped`・1 か 75 | PR は開いたまま。直して打ち直す |
| `trunk_release.release_trunk` | マージ済みの PR の head が承認の SHA と違う | 承認と別の中身で人がマージした | `ops.pr_view` で読んだマージ済みの PR の `headRefOid` と承認の SHA の比較（I2。祖先の判定は squash・rebase で外れるため使わない） | 止める（`StepError`、1） | `stopped`・1 | 公開しない。人が中身を確かめる |
| `trunk_release.release_trunk` | マージ済みなのに承認の記録が無い | 承認資料を消した・別の手でマージした | `read_approval` が `None` | 止める（`StepError`、1） | `stopped`・1 | 公開しない。承認資料を戻して打ち直す |
| `trunk_release.release_trunk` | タグか本番ブランチの版が既にある | 同じ版を出し済み | `_merge_new_prod` と同じ確かめ（`refs/tags/<タグ>`・`shipped_note`） | 止める（`StepError`、1） | `stopped`・1 | 版を上げ直す |
| `trunk_release.open_release_pr` | PR を作れない | 権限・同じ head の別の PR | `ops.create_pr` | 呼び出し元へ返す（`StepError`） | `stopped`・1 | 打ち直せる |
| `trunk_release.read_approval` | 承認資料の欄の形が壊れている | 手で書き換えた | `approved_commit.from_material` の `ValueError` | 止める（`StepError`、3） | `stopped`・3 | 資料を作り直して打ち直す |
| `cmd_release` | どの経路でも `LandPending` が上がった | merge queue へ入れた・人のマージを待つ | `cmd_release` の本番の部分を囲む `except` | 止める（公開の欠け） | `stopped`・6。`prod_land.pending_result` の出力 | 打ち直しでマージ済みならマージ済みの再開の経路で公開する |
| `_merge_prod_pr` | 比べ直しの回数を超えて head が動いた | 承認の外のコミット | `HeadMoved` の繰り返し | 止める（承認ゲート） | `gate`・10（今と同じ） | 今と同じ |
| `wait_and_merge` | マージできない | CI の失敗・衝突・基盤の待ち | `merge-when-green` の終了コード | 止める（今と同じ。`HeadMoved` は投げない） | `stopped`・1 か 75。衝突は `Conflicted` | 今と同じ |
| `reviewed.merge_on_pr_approval` | 承認の後に head が動いた | 承認の後の push | `ops.land` の `HeadMoved` | 止める（承認ゲート） | `gate`・10（今と同じ） | 今と同じ |
| `strategy_route._merge_approved` | 比べ直しの回数を超えて head が動いた | 承認の外のコミット | `ops.land` の `HeadMoved` の繰り返し | 止める（承認ゲート） | `gate`・10（今と同じ） | 今と同じ |
| `_fill_prod` | 系列の無い本番で origin を読めない | 取り直しの失敗 | `release_version.observe` の `unreadable` | 止める（`_stopped("unreadable")`） | キューの項目が止まり、理由に読めなかったもの | 記録を書かない。打ち直せる |
| `_fill_prod` | 決めた版がもう出ている | 版の観測の後に別のリリースが出た | `release_version.taken` | 止める（`_stopped("taken")`、今と同じ） | キューの項目が止まり、次に空いている版を示す | 記録を書かない。打ち直せる |
| `sprint_routes.trial_verify` | `.ndf/pace.json` が壊れている・節に `verify` が無い | 設定の誤り | `pace_verify` の `DeclError` | 止める（`DeclError`、計画を書かない） | `supervise.py new` が止まり、理由を返す | 計画を書かない。設定を直して打ち直せる |
| `sprint_waves._release_fields` | 試用のコマンドを組めない | `trial_verify` の `DeclError` | `trial_verify` | 呼び出し元へ返す（`DeclError`） | `supervise.py new` が止まる | 計画を書かない |
| `decl.trunk_release` | スナップショットが無い・壊れている | 計画の引数の誤り | `sprint_flow` の `DeclError` | 呼び出し元へ返す（`DeclError`） | `supervise.py new` が止まる | 計画を書かない |

## 処理の手順

### `prod_land.land` の判定

上から順に当てる。

| # | 条件 | 結果 |
| --- | --- | --- |
| 1 | `queue_state` が `merged` | `merge_commit_of` を読んで返す（照会の直前にキューか人がマージした） |
| 2 | `queue_state` が `closed` | `StepError`（1） |
| 3 | `queue_state` が `queued` | `wait_queued(root, pr, a.ci_wait)` を待つ。戻りが `merged` なら 1、`closed` なら 2、`queued` なら `StepError`（75）、`open` なら 4 へ進む |
| 4 | `queue_state` が `open` | `wait_ci_except`（`ndf/release-approval` を除く）→ `wait_trail_status` の順に待つ |
| 5 | 4 の後に読んだ PR の head が `head` と違う | `HeadMoved` |
| 6 | `land_approved(port, strategy, pr, Approval(head, None, trail_url), pull=pull)` の `result` が `merged` | マージコミットを返す（読めなければ `None`） |
| 7 | 同じく `queued` か `human` | `LandPending(result, reason, pr)` |
| 8 | 同じく `stopped` | `StepError`（1。summary は `reason`） |

### `trunk_release.release_trunk` の判定

| # | 条件 | 結果 |
| --- | --- | --- |
| 1 | `ops.find_pr` がマージ済みの版上げの PR を返す。または閉じた（`merged: false`）版上げの PR を返し、`prod_land.landed_commit` が取り込みのコミットを返す（ruleset の無い `strict` で `merge.commit` が `squash` の早送りは、参照を進めてから PR を閉じるため PR がマージ済みにならない） | `read_approval` が `None` なら `StepError`（1）。あれば `ops.pr_view` で PR の `headRefOid` を読み、承認の SHA と違えば `StepError`（1）。同じならマージコミット（マージ済みは `ops.merge_commit_of`、閉じた PR は `landed_commit` の戻り値。どちらも無ければ `origin/<本番ブランチ>`）と `already_merged` の行・計測（`approved_sha`）を作り、(PR, マージコミット, 行, 計測) を返す。`_resume_merged_prod` の祖先の判定は使わない。取り込みの記録の無い閉じた PR は 1 に当たらず、2 以降へ進む |
| 2 | タグか本番ブランチの版が既にある | `StepError`（1） |
| 3 | 開いた PR を `open_release_pr` で使い回すか作る | 4 へ |
| 4 | `read_approval` が `None`、または承認の SHA が PR の head と違う | `wait_ci_except` の後に `waiting_out` を `emit`（10） |
| 5 | 承認の SHA が PR の head と同じ | `ops.land(root, a, pr, head)`。`HeadMoved` なら `waiting_out(moved=True)` を `emit`（10） |
| 6 | 5 が戻った | (PR, マージコミットか `origin/<本番ブランチ>`, 行, 計測) を返す |

### `cmd_release` の振り分け

承認を読む前に振り分ける。

| チャネル | `route.sync` | CD | 取り込み方が単位を選ぶ | 経路 |
| --- | --- | --- | --- | --- |
| `prod` | 偽 | 無し | いいえ | `_prepare_release_branch` → `trunk_release.release_trunk` → `_published` |
| `prod` | 偽 | あり | いいえ | `reviewed.release_prod`（今と同じ） |
| `prod` | 真 | 無し | いいえ | `approval.approved_of` → `_merge_new_prod` → `_merge_prod_pr`（今と同じ並び） |
| `prod` | 問わない | 問わない | はい | `strategy_route.release_selected`（今と同じ） |
| `dev` | 偽 | 問わない | 問わない | 前提の不足（3。今と同じ） |
| `dev` | 真 | 問わない | 問わない | 開発版（今と同じ） |

### `release_templates.plan_release_package_plugin` の本番の種類と 10 の行き先

| CD | trunk | MVV 判定 | 種類 | release の 10 | release の 6 |
| --- | --- | --- | --- | --- | --- |
| 無し | 偽 | 無し | `prod` | `gate_next: end`（今と同じ） | `release-landed` |
| 無し | 偽 | あり | `prod-mvv` | `gate_next: handoff`（今と同じ） | `release-landed` |
| 無し | 真 | 無し | `prod-trunk` | `gate_next: trial` | `release-landed` |
| 無し | 真 | あり | `prod-trunk` | `on_exit` の `"10": "trial"` | `release-landed` |
| あり | 偽 | 問わない | `prod-cd` | `facts`（`cd_release_wait`。今と同じ） | `release-landed` |
| あり | 真 | 問わない | `prod-cd` | `trial`（`cd_release_wait`） | `release-landed` |

種類 `prod-trunk` のステップの並びは次のとおりである。

```text
bump → bump-others → changelog → notes → snapshot →（sync）→ release → trial → facts → explain → others →（MVV 判定: mvv → note）→ approve → release
```

approve の後の release は、1 回目と同じステップである。0 なら cd-result へ進む。10 なら trial へ戻って承認資料を作り直し、normal は others の後で再び承認ゲートとして終わる。種類 `prod-trunk` は今の種類 `prod` の先頭の approve と `prod-mvv` の先頭の MVV 判定を持たない（承認は others の後に取る）。

### 繰り返しと計算量

- 繰り返しの終わる条件: `wait_queued` は状態が `queued` から外れるか、`release-steps.ci-wait` の間隔で `timeout` 秒に達したら終わる。`_merge_prod_pr`・`_merge_approved` の比べ直しは今の `HEAD_MOVED_RETRIES` 回で終わる。プランの release → trial → … → approve → release の戻りは、承認ゲート（normal）か MVV 判定の承認ゲート（fast・auto）で止まる。プランの実行の基盤のステップの上限でも終わる
- 計算量の注意: `prod_land.land` が 1 回の実行で打つ GraphQL の照会は、次の 2 つだけである。`queue_state` の 1 回（merge queue にあれば待つ間の読み直しの回数分）と、`land_approved` の中の照会（今の `release-steps.py land` と同じ数）

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `LandPending` | 型（例外。属性 `result: str`・`reason: str`・`pr: int`） | `plugins/ndf/scripts/release_lib/prod_land.py` | — |
| `PR_QUEUE_QUERY` | 定数（PR の `merged`・`state`・`isInMergeQueue` を読む GraphQL の照会の文） | `plugins/ndf/scripts/release_lib/prod_land.py` | 設計文書の「merge queue にあるかの読み方」の照会と同じ項目 |
| `PENDING_REASONS` | 定数（`queued` → 「merge queue のマージを待つ」、`human` → 「人のマージを待つ」） | `plugins/ndf/scripts/release_lib/prod_land.py` | 左の 2 行 |
| `TRIAL_ROW` | 定数（承認資料の欄の名前） | `plugins/ndf/scripts/release_lib/materials.py` | `承認前の試用で確かめたこと`。`release-steps.py` の `cmd_approval_facts` はここから引き、同じ文字列を持たない |
| `RULE_RELEASE_PROD_TRUNK` | 定数（種類 `prod-trunk` の規則の文） | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 承認の前に版上げの PR を開いて試用し、承認資料を作ってから承認を待つ旨の文 |
| `RELEASE_KINDS["prod-trunk"]` | 定数（`ReleaseKind`） | `plugins/ndf/scripts/supervise_lib/release_templates.py` | 規則 `RULE_RELEASE_PROD_TRUNK`、承認の手順の id（`trial`・`facts`・`explain`・`others`・`approve`）、`after_branch: facts`、手順 `_trunk_prod_approvals`、承認ゲート `_no_gate` |
| 本番のプランの引数 `trial_cmd` | 設定値（計画を組む時点の値） | `supervise_lib/sprint_waves.py` の `_release_fields` が作り、`release_templates._trial_step` が読む | normal は `None`（`_trial_step` が `verify-install` を組む） |
| merge queue の待ちの間隔 | 設定値 | `plugins/ndf/contracts/limits.json` の `release-steps.ci-wait` の `interval`（今の値を使い、行を足さない） | 15 秒。上限は `--ci-wait` |
| 版の記録の `how` の値 `decided` | 設定値 | `lib/release_version.py` の `Decision.how`（今の値） | 系列の無い本番も `decided` を使い、新しい値を足さない |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ版の `release --channel prod` を 2 つの実行が打つ（打ち直しと release-landed の重なり） | 版上げの PR・本番ブランチ・merge queue | 新しい排他を取らない。キューへの投入は `land_approved` が承認した head（`expectedHeadOid`）で固定し、2 回目は `queue_state` が `queued` を返して待ちへ入る | merge queue にある PR を二重に入れない。マージ済みなら `find_pr` の `MERGED` が、早送りで閉じた PR なら `landed_commit` の取り込みの記録が再開の経路へ回し、無いものだけを公開する |
| merge queue のマージと、打ち直しの `release` | 版上げの PR の状態 | 状態は GitHub が持ち、NDF は読むだけ | `queue_state` が `merged` なら公開へ進む。照会と照会の間にマージされても、次の照会で `merged` を読む |
| 承認の後の版上げのブランチへの push と、本番の取り込み | 版上げの PR の head | 入れる直前に PR の今の head を読み（判定表の 5）、`land_approved` も同じ `pull` で照らす | head が動けば入れない（I2・I9）。承認を取り直して打ち直す |
| キューの版の差し込みを 2 つのキューが同時に埋める | 本番のプランの状態ディレクトリの `version.json` | 今の `write_text_atomic` の原子的な書き込み | 記録があれば決め直さない（I7）。後から書いた側が前の記録を読んで同じ版を使う |
| 計画の組み立て（`new sprint`・`new close`）と宣言の変更 | `.ndf/project.json` の `branch_strategy` | 計画の引数のスナップショット（`a.flow`）を組む時点で固定する（今の `pin_flow`） | 計画は組み直せば今の宣言で作り直される |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `prod_land.land` | 待ちの途中の状態（merge queue の状態・検査の待ち）は出さず、結果は例外で呼び手へ返す。迂回の記録・意図の印・人のマージの記録は `land_approved` が PR のコメントへ書く | PR のコメント（`land_approved` 経由） | トークン・認証の情報 |
| `prod_land.pending_result` | `items` の PR の行（`result: queued`・`human`）、`gaps` の公開の行（タグの名前と理由）、`next` の打ち直しのコマンド、`metrics` の `channel`・`version`・`main_pr` | 結果 JSON（`cmd_release` が `emit`） | トークン |
| `trunk_release.release_trunk` | 承認ゲートの結果（`waiting_out`）と、マージした後の行（`result: merged`）・計測（`approved_sha`・`compared_head`・`main_pr`） | 結果 JSON | 承認資料の本文 |
| `trunk_release.waiting_out` | summary に版・PR・head の先頭 8 文字、`next` に承認資料の作り直しと承認の取り直しのコマンド | 結果 JSON | 無し |
| `cmd_release` | 公開の欠け（6）・承認ゲート（10）・止まった（1・75）の結果 | 結果 JSON | トークン |
| `_fill_prod` | 系列の無い本番で決めた版と観測（`how: decided`・`observed`） | 状態ディレクトリの `version.json` | 無し |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `prod_land.land` | `release_lib/land.py` の `land_approved`・`GhPort`・`Approval` | `grep -n 'def land_approved\|class GhPort\|class Approval' plugins/ndf/scripts/release_lib/land.py` | 使う。主体・方式・履歴の作り方・ruleset の照合・迂回の記録・早送りはここが持つ（決定 8） |
| `prod_land.land` | `release_lib/land.py` の `land_pr`（`release-steps.py land` の本体） | 同じファイルを読んだ | 使えない。承認を証跡から読み直し、承認の無いスプリントの PR を通す分岐を持つ。本番の取り込みは呼び手が照らした head を渡すため、`land_approved` を直接呼ぶ |
| `prod_land.land` | `release_lib/reviewed.py` の `wait_ci_except`・`wait_trail_status` | `grep -n 'def wait_ci_except\|def wait_trail_status' plugins/ndf/scripts/release_lib/reviewed.py` | 使う（決定 1） |
| `prod_land.land` | `release-steps.py` の `wait_and_merge`（`merge-when-green`） | `grep -rn 'wait_and_merge' plugins/ndf/scripts --include=*.py` | 使えない。宣言の主体と方式を読まない（決定 8） |
| `prod_land.landed_commit` | `release_lib/land.py` の `GhPort.landed_record` | `grep -n 'def landed_record' plugins/ndf/scripts/release_lib/land.py` | 使う。`land_approved` が早送りの打ち直しで使う照合（head・tree・最初の親）と同じ条件で読む |
| `prod_land.queue_state` | `release_lib/land.py` の `GhPort.queue`（merge queue の一覧） | 同じファイルの `_QUEUE_QUERY` と `queue` を読んだ | 使えない。merge queue に無い承認済みの版上げの PR も行に足すため、1 つの PR の在否に使えない（設計文書の「merge queue にあるかの読み方」） |
| `prod_land.queue_state` | `lib/gh_graphql.py` の `graphql`・`pr_status_parts` | `grep -n '^def ' plugins/ndf/scripts/lib/gh_graphql.py` | `graphql` を使う。`pr_status_parts` は `isInMergeQueue` を読まないため使わない |
| `prod_land.wait_queued` | `lib/waits.py` の `wait_until` と `limits.policy("release-steps.ci-wait")` | `grep -n 'def wait_until' plugins/ndf/scripts/lib/waits.py` と `wait_ci_except` の使い方 | 使う。間隔は今の行、上限は `--ci-wait` |
| `prod_land.pending_result` | `lib/step_result.py` の `result`（`gaps` の引数） | `grep -n 'def result' -A12 plugins/ndf/scripts/lib/step_result.py` | 使う |
| `trunk_release.release_trunk` | `release_lib/reviewed.py` の `release_prod`（CD のある本番） | 同じファイルを読んだ | 使えない。承認を PR の上の証跡かレビューで読み、CD の無い場合の手元の承認資料を読まない（決定 6） |
| `trunk_release.release_trunk` | `release-steps.py` の `find_pr`・`pr_view`・`merge_commit_of`・`_published` | `grep -n '^def ' plugins/ndf/scripts/release-steps.py` | 使う（`ops` 経由。`strategy_route` が `ops._merge_sync_pr` を呼ぶ前例と同じ） |
| `trunk_release.release_trunk` | `release-steps.py` の `_resume_merged_prod` | 同じ | 使えない。承認の SHA とマージコミットの祖先を比べるため、squash・rebase の宣言では承認した head をマージしても止まる（I2） |
| `trunk_release.open_release_pr` | `release-steps.py` の `_open_trunk_release_pr` | 同じ | 移す。開いた PR だけを使い回す形にし、マージ済みの判定は `release_trunk` に置く |
| `trunk_release.read_approval` | `release_lib/approval.py` の `approved_of`・`_approved_from_material` | `grep -n 'def approved_of\|def _approved_from_material' -A20 plugins/ndf/scripts/release_lib/approval.py` | 使えない。資料が無いと 3、記録が無いと `Gate` を上げて `emit` までの流れを持つ。読む部分の `approved_commit.from_material` と `Unapproved` を使う |
| `trunk_release.waiting_out` | `release_lib/approval.py` の `gate`・`reviewed.release_prod` の `waiting` | 同じファイルを読んだ | 使えない。`gate` は許容の比べ方の判定（`Verdict`）を前提にし、`waiting` は PR の上の承認の手順を案内する。出力の形（`awaiting_approval` の行）は `release_prod` に合わせる |
| `release_version.decide_release` | `release_version.decide_dev`・`_max_base`・`collected_release` | `grep -n 'def decide_dev\|def _max_base\|def collected_release' -A12 plugins/ndf/scripts/lib/release_version.py` | `_max_base` と `versions.next_version` を使う。`decide_dev` は接尾辞つきの版、`collected_release` はベースブランチの版の基底を優先するため使えない |
| `decl.trunk_release` | `release_lib/strategy_route.py` の `route_of`、`supervise_lib/decl.py` の `sprint_flow` | `grep -n 'def route_of\|def sprint_flow' -A12` の 2 ファイル | 使う（取り込み方の分岐の正本を 1 か所にする） |
| `sprint_routes.trunk_prod_waves` | `sprint.prod_plans`・`sprint_routes.normal_prod_waves` | `grep -n 'def prod_plans\|def normal_prod_waves' -A12` の 2 ファイル | `prod_plans` を使う（レーンならリリースの依頼のプランになる）。`normal_prod_waves` はデプロイのステージを前提にするため使えない |
| `sprint_routes.trial_verify` | `sprint_routes.pace_verify` | `grep -n 'def pace_verify' -A12 plugins/ndf/scripts/supervise_lib/sprint_routes.py` | 使う。`{ref}` の置き換えだけを足す |
| `release_templates._trial_step` | `release_templates._release_verify_steps` の verify のステップ | `grep -n 'def _release_verify_steps' -A25 plugins/ndf/scripts/supervise_lib/release_templates.py` | 使えない。verify はベースブランチを `git pull` してから導入する。trial は ref を `release/v<版>` にし、`git pull` を打たない |
| `release_templates._landed_step` | `release_templates._release_step` | 同じファイルを読んだ | 使う（`_release_step` の値をコピーし、id と 6 の扱いだけを変える） |
| `release_templates._trunk_prod_approvals` | `release_templates._dev_approval_steps`・`release_cd.cd_prod_steps`・`release_cd.mvv_gate_steps`・`release_templates.approve_step` | `grep -n 'def _dev_approval_steps\|def approve_step' supervise_lib/release_templates.py`・`grep -n 'def cd_prod_steps\|def mvv_gate_steps' supervise_lib/release_cd.py` | `_dev_approval_steps`・`mvv_gate_steps`・`approve_step` を使う。`cd_prod_steps` は承認資料を PR へ置く `approval-post` と証跡の `approve-pr` を前提にするため使えない |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `prod_land.land` | 宣言 `merge-queue`・検査が成功の偽の Port で `LandPending("queued")`、直接のマージ 0 回。`strict`・`direct` と `merge.commit` の 3 値で Port のマージに渡る履歴の作り方が宣言と同じ | 検査が実行中の間は待ち、成功に変わった 1 回目で入れる。`queue_state` が `queued` から `merged` に変わった時点でマージコミットを返す | 判定表の 2・3（上限）・5・7（`human`）・8 の各行。上限の待ちは 75、閉じた PR は 1、head の違いは `HeadMoved`。どれも直接のマージ・キューへの投入が 0 回 |
| `prod_land.queue_state` | `merged: true` → `merged`、開いて `isInMergeQueue: true` → `queued`、開いて偽 → `open` | 閉じて `merged: false` → `closed` | 照会の失敗・PR の無い応答で `StepError`（`open` を返さない） |
| `prod_land.wait_queued` | 2 回目の読みで `merged` を返す | 上限 0 秒で 1 回だけ読んで `queued` を返す | 照会の失敗を上げる |
| `prod_land.landed_commit` | 記録の head が承認の SHA と同じで tree が一致し、先端が最初の親なら取り込みのコミットを返す | 記録が無い・PR の番号の違う記録だけがあるときは `None` | 記録の head が承認の SHA と違う・tree が違う・最初の親が先端でないときは `None` |
| `prod_land.pending_result` | `queued` で `status: stopped`・`gaps` の reason「merge queue のマージを待つ」 | `human` で reason「人のマージを待つ」 | — |
| `trunk_release.release_trunk` | 承認と head が一致し、`ops.land` が戻れば (PR, マージコミット, 行, 計測) を返し、`ops.land` を 1 回呼ぶ | 開いた PR があれば作らずに使い回す | 判定表の 1（承認なし・マージ済みの PR の head が承認の SHA と違う。squash でマージ済みでも head が一致すれば公開へ進む。宣言が `strict`・ruleset 無し・`squash` で、早送りの後に PR が閉じたまま公開が落ちた打ち直しは、`landed_commit` のコミットで公開へ進み、2 で止まらない）・2・4・5（`HeadMoved`）。4 と 5 は 10 で、`ops.land` を呼ばない（4）か 1 回だけ（5） |
| `trunk_release.open_release_pr` | 開いた PR が無ければ 1 回作る | 開いた PR があれば作らない | `create_pr` の失敗を上げる |
| `trunk_release.read_approval` | 記録が SHA と一致すれば SHA を返す | 資料が無ければ `None` | 記録が無い・違うで `None`、欄の形の誤りで `StepError`（3） |
| `trunk_release.waiting_out` | `status: gate`、`items` の末尾が `awaiting_approval` の行 | `moved=True` で summary が「先端が動いた」 | — |
| `cmd_release` | trunk・CD 無しで `release_trunk` を承認を読む前に呼ぶ（`--approval` の資料が無くても 3 で止まらない） | 4 経路のそれぞれで `LandPending` が 6 になり、タグを作る呼び出しが 0 回 | 打ち直しでマージ済みなら `already_merged` で公開へ進む |
| `_merge_prod_pr` | 比べた先端で `prod_land.land` を 1 回呼び、`wait_and_merge` を本番ブランチ宛てに呼ばない | 先端が許容の中で動けば比べ直して入れる | 回数を超えて動けば 10 |
| `wait_and_merge` | 開発版の PR と `-sync` の PR のマージで呼ばれる | — | `merge-when-green` の 4 で `Conflicted`、75 で 75 |
| `reviewed.merge_on_pr_approval` | `reviewed.release_prod` と `strategy_route._merge_exact` の両方から `ops.land` を 1 回呼ぶ。迂回の宣言でも `admin_record_item` を呼ばない | — | 承認が無ければ 10 で `ops.land` 0 回。`HeadMoved` で 10 |
| `strategy_route._merge_approved` | 比べた先端で `ops.land` を 1 回呼ぶ | 先端が動けば比べ直す | 回数を超えれば 10 |
| `release_version.decide_release` | タグ 10.27.75・本番ブランチ 10.27.75・ベース 10.27.76-dev.2 で `patch` なら 10.27.77 | 上げ幅 `minor`・`major` | `unreadable` があれば `version` が `None` |
| `_fill_prod` | 系列も明示も無い本番で 1 回目に版を決めて記録し、2 回目は記録の版を使う | 明示の版があれば今どおり `explicit` | origin を読めないと `unreadable`、決めた版が出ていれば `taken` |
| `decl.trunk_release` | 取り込み方 trunk の宣言で真 | `approval.target: production_pr`（版上げの PR が無い）で偽 | スナップショットが無いと `DeclError` |
| `sprint.sprint_plans` | trunk・CD 無しで「デプロイ」とリリース承認ゲートのステージが無く、本番のステージが `then_of` でつながる | 分かれた宣言で今と同じステージ（I8・受け入れ条件 13） | — |
| `sprint.fast_sprint_plans` | trunk で「開発版」のステージが無い | 分かれた宣言で今と同じ | — |
| `sprint.auto_sprint_plans` | trunk で「開発版」のステージが無い | 分かれた宣言で今と同じ | — |
| `sprint.close_waves` | trunk で「開発版」のステージが無く、本番のステージが変更のあるときだけ流れる（実行条件） | auto はまとめだけ（今と同じ） | — |
| `sprint_routes.trunk_prod_waves` | 本番のステージ 1 つを返し、`note` を渡せば載る | レーンならリリースの依頼のプラン | — |
| `sprint_routes.trial_verify` | fast で `{ref}` が `release/v{release_version}` に置き換わる | normal で `None` | 節に `verify` が無いと `DeclError` |
| `sprint_waves._release_fields` | trunk の本番で `series` が `None` | 分かれた宣言の本番で `series` が `release` | — |
| `release_templates.version_field` | trunk の本番で `明示`・`系列` が `None` の欄 | 明示の版があれば今と同じ | 分かれた宣言で明示も系列も無いと `DeclError`（今と同じ） |
| `release_templates._release_kind` | trunk・CD 無しで `prod-trunk` | trunk・MVV 判定ありでも `prod-trunk` | — |
| `release_templates._release_step` | 本番の `on_exit` に `"6": "release-landed"` | trunk の normal で `gate_next: trial`、MVV で `on_exit` の 10 が `trial` | — |
| `release_templates._trial_step` | normal で `verify-install --ref release/v<版>` | `trial_cmd` があればそれを使い、`{ref}` が残らない | — |
| `release_templates._landed_step` | id が `release-landed`、コマンドが release と同じ | `on_exit` の 6 が `stop` | — |
| `release_templates._trunk_prod_approvals` | trial が facts の前、explain が `--ref release/v<版>`、others の後に normal は終わる | MVV 判定のプランで others → mvv → note → approve（`--by mvv`） | — |
| `release_templates.approve_step` | 引数を省くと今と同じ値 | trunk で `--facts-from {state_dir}`・`next: release` | — |
| `release_templates.plan_release_package_plugin` | trunk の本番のプランを偽のコマンドで流すと、1 回目の release の 10 で trial へ進み、normal は others の後に結果「承認ゲート」で終わり approve を打たない（受け入れ条件 12） | `--from approve` で続けると release が取り込みへ進む | bump の後の notes が `--approval` を持たない（受け入れ条件 11） |
| `release_cd.cd_release_wait` | trunk で 10 の行き先が `trial` | 分かれた宣言で `facts`（今と同じ） | — |
| `release_cd.cd_prod_steps` | trunk で先頭が `trial` | 分かれた宣言で先頭が `facts`（今と同じ） | — |
