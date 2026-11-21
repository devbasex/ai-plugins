# #3989: 並列の計画の同時実行の制御を稼働中のレーンの数とテストのスロットの 2 層にする

## 関連リンク

- 要求仕様: https://github.com/devbasex/ai-plugins/issues/3989 （写しは [issue-3989-requirements.md](./issue-3989-requirements.md)）。受け入れ条件は番号で指す
- 設計書: [issue-3989-design.md](./issue-3989-design.md)・詳細設計書: [issue-3989-design-detail.md](./issue-3989-design-detail.md)
- モード: `standard`（公開インタフェース（計画の設定・`ndf lanes`・`ndf relay` の引数と結果）が変わり、設計 PR #3993 を承認済み）

## 修正対象

詳細設計書の「地図」の表のとおり（新設は `src/ndf/contracts/named_cap.py`・`tests/contracts/test_named_cap.py`）。確定仕様 `docs/specifications/ndf-parallel-machine-plan-limit.md` は触らない（確定仕様化の工程で直す）。

## タスク分解

### Task 1: 計画とレーンの上限の値を消す

- **対象ファイル:** `progress/lane/types.py`・`fold.py`・`rules.py`・`__init__.py`、`progress/plan/__init__.py`・`cli.py`、`contracts/workflow/parallel-plan.schema.json`、`contracts/schemas/io/lanes.args.schema.json`・`lanes.result.schema.json`、`tests/progress/test_lane.py`・`test_plan.py`・`tests/support/plan_decls.py`
- **変更内容:** `PlanView.max_lanes`・`max_plans`、`LaneSpec.plans`、I20・I2、`ndf lanes limits`、`ndf lanes set --plans` を消す。畳み込みは記録の 3 つの値を読み捨てる
- **満たす受け入れ条件:** 1・2・3・4
- **進め方:** 失敗するテスト（記録に値がある事象の畳み込み・形の断り・after 3 本の init）→ 消す → 整理

### Task 2: 上限の値を 1 つの読み手にし、`ndf relay cap` を足す

- **対象ファイル:** `contracts/named_cap.py`（新設）、`domains/relay/cli.py`、`contracts/schemas/io/relay.args.schema.json`・`relay.result.schema.json`、`contracts/domains/env.json`・`contracts/limits.json`、`tests/contracts/test_named_cap.py`・`tests/domains/relay/test_relay_cli.py`
- **変更内容:** `named_cap.path`・`read`・`fallback`・`parse`・`write` と `ndf relay cap [--named N | --clear]`
- **満たす受け入れ条件:** 10
- **進め方:** 失敗するテスト（読む順・整数の契約・書いた値を次の read が返す）→ 実装 → 整理

### Task 3: リレーハブの判定を判定のたびの上限・置き換えの起動・一覧の錠だけのラッパーにする

- **対象ファイル:** `domains/relay/hub.py`・`hub_decide.py`・`cli.py`、`tests/domains/relay/test_relay_hub.py`・`test_relay_cli.py`
- **変更内容:** `Context.max_named` を消し要求の `cap` を載せる、`spawn --replace --seat [--seated]`、上限の断りの `items`、`listing` の `registered`
- **満たす受け入れ条件:** 7・10
- **進め方:** 失敗するテスト → 実装 → 整理

### Task 4: 起動の許可を稼働中のレーンとほかのラッパーを 1 本ずつ数える形にする

- **対象ファイル:** `progress/lane/machine.py`・`launch.py`、`progress/plan/run.py`、`tests/progress/test_lane_machine.py`・`test_lane_launch.py`・`test_run_lanes.py`
- **変更内容:** 重みを消した `count`・`admit`、`named_cap` で読む `cap`、計画の索引、`lock_free`、`launch_lane` を `spawn --replace` の 1 回にし、ハブの上限の断りを待ちに変える
- **満たす受け入れ条件:** 5・6・7・8・10
- **進め方:** 失敗するテスト → 実装 → 整理

### Task 5: `ndf lanes status` に待ちの理由とマシンの集計を出す

- **対象ファイル:** `progress/plan/__init__.py`、`contracts/schemas/io/lanes.result.schema.json`、`tests/progress/test_plan.py`
- **変更内容:** `machine_waits` の理由（`machine-lanes`・`lock`・`observe`）と `metrics.machine`
- **満たす受け入れ条件:** 9
- **進め方:** 失敗するテスト → 実装 → 整理

### Task 6: 手順書とコントラクトの説明をそろえる

- **対象ファイル:** `skills/issue-plan-strategy/references/parallel-plan.md`、`skills/development-workflow/references/parallel.md`・`parallel-checks.md`・`parallel-cross-repo.md`・`parallel-work.md`、`docs/ndf-parallel-model.md`、`docs/contracts/commands.md`・`env.md`（再生成）
- **変更内容:** 3 つの値と `ndf lanes limits` を消し、2 つの層の説明にする
- **満たす受け入れ条件:** 12
- **進め方:** テスト駆動を適用しない（文言を照合するテストを書かない決まり）。生成物は描画で再生成し、CI の検査で確かめる

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| u1（#3989 全体。課題 1 つを 1 単位） | Task 1〜6 | 3989:1〜13 | 約 1,000 | （実装の後に書き足す） |

課題 1 つを 1 単位にする。タスクは依存の向き（Task 1 → 2 → 3 → 4 → 5 → 6）でコミットを分け、レビューはコミットごとに読める形にする。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触るファイルはどれも 470 行以下（最大 `progress/plan/__init__.py` 467 行）で、消す値の参照は 1 ファイルあたり数か所 |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | 一時ファイルからの改名で書く処理（`named_cap.write`・`machine.index_plan`・`relay.signals.replace_json`） | 同じ形が 3 か所になる。共有の場所へ移すかを見る（詳細設計の「既存の部品の再利用」） |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 手元の p9・p12 の `plan.json` が新しい形で断られる | PR の本文の移行の手順に `jq 'del(.max_lanes, .max_plans) \| .lanes \|= map(del(.plans))'` を書く |

## 完了の定義

- [ ] 受け入れ条件 1〜13（12 の確定仕様の分を除く）をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] PR の CI（pytest・契約の検査・静的解析）が通る
