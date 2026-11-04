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
| `run` | str | 許さない | 取った実行の識別子。`inherited` で使う子の実行は書き換えない。生死と返す権限の判定には使わず、断りの結果に出すためだけに持つ |
| `started_at` / `seen_at` | str | 許さない | 取った時刻 / 最後に動いた時刻（UTC の ISO 8601） |
| `finished_at` | str | 許す | 返した時刻。空は「返していない」 |
| `pid` / `pid_started` | int / float | 許さない | 持ち主のプロセス（単位 `run` は入口のプロセス、`session` はランタイムのセッションのプロセス）と、その開始時刻（epoch 秒） |
| `host` | str | 許さない | 持ち主のプロセスが動くホスト名 |

**返しても記録を消さない。** 片付けがレビュー worktree の持ち主が終わったかを読むためである。
終わった時刻から 30 日を過ぎた記録は、持ち主の部品の `lease.prune` が外す（`durable.KEEP_DAYS` と同じ値）。片付けは `lease.prune` を呼ぶだけで、記録を自分で書き換えない。

### 入口の契約に足すキー

入口の行（`contracts/entrypoints/<パス>.json`）に任意のキー `lease` を足す。無い入口は持ち主を取らない（受け入れ条件 10）。
`lease` は、副命令の名前（副命令の無い入口は `""`）から下の宣言への対応である。

| 鍵 | 型 | 空を許すか | 意味 |
| --- | --- | --- | --- |
| `key` | str | 許さない | 鍵の形。`{<入力の名前>}` を解決した入力で埋める（例 `cross-refactoring:pr={pr}`） |
| `unit` | str | 許さない | 持ち主の単位（`run` / `session`） |
| `window_seconds` | int | 許さない | 寿命の窓の秒。別のホストの記録の生死の判定にだけ使う（I4） |
| `release` | bool | 許す | 真なら、この副命令の終わりで単位 `session` の記録を返す |

生きた持ち主がいるときの扱いは宣言しない。どの入口も止まる（決定 9）。

### 前提 3 の入口の宣言

| 入口 | 鍵の形 | 単位 | 寿命の窓 |
| --- | --- | --- | --- |
| cross-refactoring の `drive.py` と `refactor.py init` | `cross-refactoring:pr={pr}` | `run` | 86400（24 時間） |
| cross-review の `drive.py` と `state.py init` | `cross-review:pr={pr}` | `run` | 86400（24 時間） |
| `supervise.py queue`（`--resume` を含む） | `supervise.queue:done={done}` | `run` | 86400（24 時間） |
| `supervise.py loop` の副命令すべて | `supervise.loop:name={name}` | `session` | 86400（24 時間） |

`{done}` はリポジトリの根からの相対パスに正規化する。`loop stop` と `loop finish` は `release: true` を持つ。

### 状態の一覧に足す行

| 種類 | 置き場 | 寿命 | 片付ける時点 |
| --- | --- | --- | --- |
| `review-worktree`（既存） | 既存のまま。持ち主の記録は `leases/` の鍵 `cross-review:pr=<n>`・`cross-refactoring:pr=<n>` へ移る | 既存のまま（持ち主が終わり、PR が open でなく、作業が無い） | 既存のまま |
| `lease-record`（新設） | `leases/<リポジトリ>/*.json` と `.lock` | 終わった時刻から 30 日 | 片付けが `lease.prune` を呼ぶ |

### 移行

| 変えるもの | 旧い形の扱い | 外す時点 |
| --- | --- | --- |
| 持ち主の記録の置き場（状態ディレクトリの横 → `leases/`） | 読まない（決定 3）。旧い記録は今の片付けがレビュー worktree と一緒に消す | この柱の実装の Pull Request |
| `refactor.py init` と `state.py init` の `--run` | 受けて捨て、標準エラーに「実行の識別子は核が振る」と出す（内部の入口で `public: false`） | 同じ Pull Request で呼ぶ側（drive）から外す |

## 入出力の契約

### 結果 JSON に足す鍵

持ち主の鍵を宣言した入口の結果 JSON は、任意の鍵 `lease` を持つ（鍵は足すだけ。#2335 の互換性の約束）。

| 鍵 | 型 | 意味 |
| --- | --- | --- |
| `lease.key` | str | 持ち主の鍵 |
| `lease.outcome` | str | `acquired`（いなかった・終わっていた）/ `reclaimed`（死んだ持ち主を引き取った）/ `inherited`（祖先のプロセスが持っていた）/ `refused`（生きた持ち主がいて止まった） |
| `lease.holder_run` | str | 生きた持ち主・引き取った持ち主の実行の識別子。いなければ省く |
| `lease.holder_pid` | int | 同上の pid。いなければ省く |
| `lease.holder_seen_at` | str | 同上の最後に動いた時刻。いなければ省く |

### 断るときの案内

`refused` は `status: stopped`・終了コード 3 で、`summary` に持ち主の実行の識別子・pid・最後に動いた時刻を書く。
案内（`next`）は打てる手段だけを書く: 持ち主が終わるのを待ってから同じコマンドを打ち直す（#2321 の受け入れ条件 3）。
`--run` を渡す・`finish` の後に開始する、という今の案内は消す（どちらもプランから打てない）。
持ち主のプロセスは別の実行か利用者のランタイムのセッション（Claude Code・Codex）なので、案内に `kill` を書かない。待てないときは、conductor は止まった理由と持ち主の pid を添えて人へ戻す。持ち主を止めるかどうかは人が決める（共通原則の C4）。

### キューの `--from` つきの再開

```text
supervise.py queue --resume <done> --from <プラン>=<ステップ> [--from ...]
```

| 項目 | 約束 |
| --- | --- |
| 入力 | 今のまま |
| 振る舞い | `--from` があれば、前の起動の途中の耐久ワークフローを子ごと止め、新しい実行の回で指定のステップから流す。`--from` も `--only` も無ければ前の起動を続ける |
| 失敗の形 | 途中を止められない → `stopped`・1。生きた持ち主がいる → `stopped`・3 |
| 互換性 | 引数は変えない。`--from` を渡したときの振る舞いだけが変わる（前の途中を続けなくなる） |
