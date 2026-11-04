# #2342 の設計: データ構造と入出力の契約

設計文書の本体は [issue-2342-design.md](issue-2342-design.md) にある。
決定の記録は [issue-2342-design-decisions.md](issue-2342-design-decisions.md) にある。

## データ構造

### 持ち主の記録（`leases/<リポジトリ>/<鍵>.json`）

| 鍵 | 型 | 空を許すか | 意味 |
| --- | --- | --- | --- |
| `version` | int | 許さない | 記録の形の版（1） |
| `key` | str | 許さない | 持ち主の鍵（例 `cross-refactoring:pr=2318`） |
| `entry` | str | 許さない | 取った入口（契約の一覧の入口のパスと副命令） |
| `run` | str | 許さない | 取った実行の識別子。子の実行が使うときも根の実行の識別子のまま |
| `started_at` / `seen_at` | str | 許さない | 取った時刻 / 最後に動いた時刻（UTC の ISO 8601） |
| `finished_at` | str | 許す | 返した時刻。空は「返していない」 |
| `pid` / `pid_started` | int / float | 許さない | 持ち主のプロセス（単位 `run` は入口のプロセス、`session` はランタイムのセッションのプロセス）と、その開始時刻（epoch 秒） |
| `host` | str | 許さない | 持ち主のプロセスが動くホスト名 |

**返しても記録を消さない。** 終わった記録は、鍵ごとの最後の実行の索引として旧い再開の形が引く（決定 5）。
片付けが、終わった時刻から 30 日を過ぎた記録を外す（`durable.KEEP_DAYS` と同じ値）。

### 入口の契約に足すキー

入口の行（`contracts/entrypoints/<パス>.json`）に任意のキーを 2 つ足す。無い入口は持ち主を取らず再開もできない（受け入れ条件 11）。

**`lease`**: 副命令の名前（副命令の無い入口は `""`）から下の宣言への対応。

| 鍵 | 型 | 空を許すか | 意味 |
| --- | --- | --- | --- |
| `key` | str | 許さない | 鍵の形。`{<入力の名前>}` を解決した入力で埋める（例 `cross-refactoring:pr={pr}`） |
| `unit` | str | 許さない | 持ち主の単位（`run` / `session`） |
| `on_busy` | str | 許さない | 使用中のときの扱い（`wait` / `stop`） |
| `wait_seconds` | int | `on_busy` が `wait` のときだけ要る | 待つ上限の秒（前提 5。柱 P4 が上限の宣言へ移す） |
| `legacy` | str | 許す | 旧い置き場の記録の形（例 `{review_base}/rf{pr}.owner.json`）。次の正式版で消す |
| `release` | bool | 許す | 真なら、この副命令の終わりで単位 `session` の記録を返す |

**`resume`**: 再開できる入口だけが持つ。形は `{"from": "<位置の形>"}` で、位置の形は次のどれかである。

| 位置の形 | `--from` に渡す値 |
| --- | --- |
| `step` | ステップの id |
| `plan-step` | `<プラン名>=<ステップ>` を 1 つ以上 |
| `round` | `round` だけ。新しい実行の回でラウンドを足す |

### 前提 3 の入口の宣言

| 入口 | 鍵の形 | 単位 | 使用中のとき | 旧い置き場 |
| --- | --- | --- | --- | --- |
| cross-refactoring の `drive.py` と `refactor.py init` | `cross-refactoring:pr={pr}` | `run` | `stop` | `{review_base}/rf{pr}.owner.json` |
| cross-review の `drive.py` と `state.py init` | `cross-review:pr={pr}` | `run` | `stop` | `{review_base}/pr{pr}.owner.json` |
| backlog-refinement の `upkeep.py` の見直しの持ち主の副命令 | `backlog-refinement:repo` | `session` | `stop` | `.ndf/backlog/owner.json` |
| `supervise.py queue`（`--resume` を含む） | `supervise.queue:done={done}` | `run` | `stop` | — |
| `supervise.py run` | `supervise.run:plan={plan}` | `run` | `stop` | — |
| `supervise.py loop` の副命令すべて | `supervise.loop:name={name}` | `session` | `stop` | — |

`{done}` と `{plan}` はリポジトリの根からの相対パスに正規化する。旧い置き場の `{review_base}` は入力ではない。
片付けと同じ `run_state.review_base(<リポジトリの根>)` が返す、レビュー worktree の置き場（`<base>/<slug>`）で埋める。
`loop stop` と `loop finish` は `release: true` を持つ。
見直しの持ち主の寿命の窓は今の `.ndf/backlog.json` の `depths.owner.ttl` を引き継ぐ。どれも `stop` にする理由は決定 2 にある。

### 状態の一覧に足す列と行

`Kind` に列 `lease`（結び付く鍵の形の並び。空は「持ち主の記録に結び付かない」）を足す。

| 種類 | 置き場 | `lease` | 寿命 / 残す理由 | 片付ける時点 |
| --- | --- | --- | --- | --- |
| `review-worktree`（既存） | 既存のまま。記録は `leases/` へ移る | `cross-review:pr={pr}`・`cross-refactoring:pr={pr}` | 既存のまま（持ち主が終わり、PR が open でなく、作業が無い） | 返した後（E8）・既存の時点 |
| `lease-record` | `leases/<リポジトリ>/*.json` と `.lock` | — | 終わった時刻から 30 日 | sweep |
| `approval-doc` | `issues/approval-<plugin>-v<版>.md`（メインディレクトリ） | — | 書いた配布のプランが後片付けのステップに達した | 配布のプランの後片付けのステップ（#2009） |
| `presentation` | `<tmp>/ndf/` | — | 残す。承認ゲートで人が読む資料（`step_result.presentation_dir`。答えを待つ間に読まれる）と Skill の出力が同じ置き場にあり、最終更新だけでは読み終えたかを決められない | — |
| `stage-fallback` | `${TMPDIR:-/tmp}/ndf-stages` | — | 残す。状態の置き場が書けないときの通過記録の退避先で、中身は記録である | — |
| `guard-fallback` | `${TMPDIR:-/tmp}/ndf-guards` | — | 残す。hook が作る状態で、hook を核へ移さない（#2335 の「含まない」） | — |
| `check-fallback` | `${TMPDIR:-/tmp}/ndf-checks` | — | 残す。状態の置き場が使えないときの検査の事象の退避先で、中身は記録である | — |
| `usage-fallback` | `${TMPDIR:-/tmp}/ndf-usage` | — | 残す。状態の置き場が使えないときの使用量の帳簿の退避先で、中身は記録である | — |

既存の `out-of-scope` の行は、上の 5 行（`presentation` 以下）に分けて消す。各行の `creators` は今の `out-of-scope` の行の作り手を置き場ごとに振り分ける。

### 移行

| 変えるもの | 旧い形の受け方 | 外す時点 |
| --- | --- | --- |
| 持ち主の記録の置き場（状態ディレクトリの横 → `leases/`） | `lease.acquire` と片付けが、新しい記録が無いか終わっているときに `legacy` の記録を読み、旧い規則（`finished_at`・寿命の窓・pid）で生死を判定する。旧い記録へは書かない（決定 10） | この柱を含む正式版の次の正式版 |
| `refactor.py init` と `state.py init` の `--run` | 受けて捨て、標準エラーに「実行の識別子は核が振る」と出す（内部の入口で `public: false`） | 同じ Pull Request で呼ぶ側（drive）から外す |
| 旧い再開の形（前提 7） | 別名として再開の入口へ渡す（入出力の契約の表） | この柱を含む正式版の次の正式版（#2335 の決定 8） |
| `.ndf/backlog/owner.json` | 上の旧い置き場として読む。書かない・消さない | 同上 |

## 入出力の契約

### 結果 JSON に足す鍵

持ち主の鍵を宣言した入口の結果 JSON は、任意の鍵 `lease` を持つ（鍵は足すだけ。#2335 の互換性の約束）。

| 鍵 | 型 | 意味 |
| --- | --- | --- |
| `lease.key` | str | 持ち主の鍵 |
| `lease.outcome` | str | `acquired`（いなかった）/ `reclaimed`（死んだ持ち主を片付けた）/ `inherited`（根の実行が持っていた）/ `waited`（待ってから取った）/ `refused`（止まった）/ `wait-timeout`（待つ上限に達した） |
| `lease.holder_run` | str | 生きた持ち主・片付けた持ち主の実行の識別子。いなければ省く |
| `lease.waited_seconds` | number | 待った秒（待たなければ 0） |
| `lease.cleanup` | list | 返した後の片付けの結果（`run_state` の `items` と同じ形。`removed` / `kept` と理由） |

`refused` と `wait-timeout` は `status: stopped`・終了コード 3 で、`summary` に持ち主の実行の識別子・pid・最後に動いた時刻を書く。
案内（`next`）は打てる手段だけを書く: 持ち主が終わるのを待つ、または `kill <pid>` の後に同じコマンドを打ち直す（#2321 の受け入れ条件 3）。

### 再開の入口

```text
supervise.py resume <実行の識別子> [--from <位置>]
```

| 項目 | 約束 |
| --- | --- |
| 入力 | 実行の識別子は根の実行のもの。`--from` の形は入口の契約の `resume.from` に従い、`plan-step` は繰り返して渡せる |
| 出力 | 再開した入口の結果 JSON をそのまま標準出力へ 1 行で返す（`run_id` は前の実行の識別子） |
| 失敗の形 | 実行の記録が無い（30 日を過ぎた・識別子の誤り）・入口が `resume` を持たない・`--from` の形が違う → `stopped`・2。持ち主が生きている → `stopped`・3（E5）。耐久の記録が無いか壊れている → `stopped`・2。途中を止められない → `stopped`・1 |
| 互換性 | 新設。旧い形は下の表のとおりこの入口へ寄せる |

### 旧い再開の形

| 旧い形 | 寄せ方 |
| --- | --- |
| `supervise.py run <plan> --from <ステップ>` | 鍵 `supervise.run:plan={plan}` の `last_run` があれば `resume <その識別子> --from <ステップ>`。無ければ今と同じく、指定のステップから新しい実行として流す |
| `supervise.py queue --resume <done> [--from <プラン>=<ステップ>]...` | 鍵 `supervise.queue:done={done}` の `last_run` で `resume <その識別子> [--from ...]`。無ければ `stopped`・2（「このキューの実行の記録が無い」） |
| cross-review の `drive.py <PR> --reopen` | 鍵 `cross-review:pr={pr}` の `last_run` で `resume <その識別子> --from round` |

どれも標準エラーに `旧い形: 次の正式版から supervise.py resume <実行の識別子> ... を使う` を 1 行出す（受け入れ条件 9）。

### 環境変数

| 名前 | 誰が設定するか | 意味 |
| --- | --- | --- |
| `NDF_RUN_ROOT` | 根の実行の核 | 根の実行の識別子。子孫はそのまま受け継ぐ。子の核は I6 の判定と、打ち切りで止めるプロセスの印に使う |
| `NDF_RESUME` | 再開の入口 | 引き継ぐ実行の識別子。核はこの値を自分の実行の識別子にする（#2335 の E13） |
| `NDF_RESUME_FROM` | 再開の入口 | `--from` の値（`plan-step` の並びは改行で区切る）。核が `ctx.resume.from_` として本体へ渡す |

3 つとも利用者が設定しない内部の値で、`NDF_RESUME*` を読むのは実行の核だけである（`resume-source` の検査）。
