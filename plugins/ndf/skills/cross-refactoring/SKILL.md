---
name: cross-refactoring
description: "Let every CLI propose refactorings on a PR once, then one CLI plans, tests, applies, and verifies what fits a time budget. Use when structural improvement should be done across runtimes within a set time（クロスリファクタリング・多AIリファクタリング・時間内の構造改善）."
argument-hint: "[PR番号] --scope PATH... [--budget-minutes N] [--implementer NAME] [--host claude|codex|agy|kiro] [--exclude NAMES] [--include NAMES] [--require-all] [--model RT=MODEL] [--baseline-test CMD] [--round-test CMD] [--ci-check NAME] [--workflow-step]"
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
  - Glob
  - Grep
---

# 多ランタイム・リファクタリング（想定最大時間に収める 1 回の計画実行）

**参加者の全員が 1 度だけ多面的に提案し、選ばれた 1 者が想定最大時間に収まる分を
計画・テスト追加・実装・検証する。** ラウンドを上限まで回す形はとらない（#933）。
所要の上限は `--budget-minutes`（既定 30 分）で利用者が決める。

`refactoring` Skill は「テストで守りながら 1 手ずつ直す」手順を持つが、
**何を直すかの発見**と**直した結果の検証**を持たない。この Skill がその 2 つを補う。
実装を担う側は `refactoring` を手順として読む。

**工程表では `standard` と `legacy-refactor` の構造改善がこの Skill を指す。**
前提を満たせないときの退避先は `development-workflow` の
`references/workflow-modes.md`「構造改善の退避先」にある。

- [docs/01-state-and-propose.md](docs/01-state-and-propose.md) — 初期化・再開・提案
- [docs/02-plan-and-implement.md](docs/02-plan-and-implement.md) — 計画・テスト追加・実装
- [docs/03-review-viewpoints.md](docs/03-review-viewpoints.md) — 最終ゲートの `cross-review` へ渡す観点
- [docs/04-verify-and-report.md](docs/04-verify-and-report.md) — 検証/修正・危険フラグ・最終ゲート・配分テーブル・報告
- [scripts/refactor.py](scripts/refactor.py) — 状態管理（uv 自己完結、標準ライブラリのみ）
- [scripts/prepare-worktrees.sh](scripts/prepare-worktrees.sh) — 作業ディレクトリ準備と Skill 配置
- [scripts/launch-cli.sh](scripts/launch-cli.sh) — フェーズごとのプロンプト組み立てと CLI 起動
- 監視と CLI 起動の実体は `../../scripts/lib/`（プラグインルート直下の収束ループ共通層）

## この Skill で使う語

| 語 | 意味 |
| --- | --- |
| 想定最大時間 | `--budget-minutes`。計画はこの中に収まるように立てる。**時間に関わる数値はすべてここから逆算する**（手順の上限・テスト 1 回の上限・無音の打ち切り） |
| フェーズ | 提案・計画・テスト追加・実装・検証/修正の 5 つ。**それぞれ 1 回だけ**行う（検証と修正の繰り返しを除く） |
| 実装担当 | 計画以降の 4 フェーズを通して担う 1 ランタイム |
| 候補 | 提案を鍵（`path` + `symbol` + `smell`）でまとめたもの。計画へ渡す |
| 改善項目 | 計画が採った候補。`I-001` の形の ID を持ち、**1 改善項目 = 1 コミット**（テストを足す項目は 2 コミット） |
| 配分テーブル | 種類（`test` / `structure/<手法>` / `verify` / `fix`）ごとの 1 件あたりの所要。履歴の直近 10 回から計画のたびに集計する |
| 範囲テスト | 項目が触った箇所に限って走らせるテスト。計画の `test_targets` から進行側が組み立てる |
| 危険フラグ | 範囲テストでは覆えない変更（D1〜D5）。立ったら全体のテストを 1 度だけ走らせる |

**「バッチ」「パッチ」の語は使わない。** 読み手が別の意味で知っている語である。

## 設計方針

参加者・実装担当・時間・検証の単位・取り消し・見積り・判断・範囲・公開の責務・最終ゲートの方針は
[references/design-principles.md](references/design-principles.md) にある。

## 引数

| 引数 | 意味 | 既定 |
| --- | --- | --- |
| `[PR番号]` | 対象の Pull Request | 必須 |
| `--scope PATH...` | 対象範囲。**提案が無制限に広がらないよう必須。** 検証にも効くので、現状固定テストの置き場所も含める | 必須 |
| `--budget-minutes N` | 想定最大時間（分）。1 以上の整数でなければ `init` が止める（終了コード 4） | `30` |
| `--implementer NAME` | 計画以降を担う 1 者。参加者に無ければ `init` が止める | ホスト → 先頭 |
| `--host claude\|codex\|agy\|kiro` | ホストの明示指定。未指定時は環境変数から推定（agy は推定できないため明示する） | 推定 |
| `--exclude NAMES` | 参加者から外す者（カンマ区切り・繰り返し可）。再開で `none` を渡すと空へ戻す | なし |
| `--include NAMES` | 参加者に足す者（例: `--include agy`） | なし |
| `--require-all` | 確認を通らない者が 1 者でもいれば中断する（終了コード 4） | 外して続ける |
| `--model RT=MODEL` | ランタイムごとのモデル。繰り返し指定できる | CLI の既定 |
| `--baseline-test CMD` | 着手前・危険フラグ・最終ゲートで実行する全体のテスト | 必須 |
| `--round-test CMD` | ラウンドのテスト。項目ごとの範囲テストを組み立てる元で、組み立てられない項目はそのまま走らせる。**`--baseline-test` の実行器が `pytest` / `python -m pytest` / `jest` / `vitest` でなければ必須** | `--baseline-test` から組み立てる |
| `--ci-check NAME` | 最終ゲートで手元のテストの代わりに見る検査の名前（排他） | なし |
| `--workflow-step` | `development-workflow` の 1 工程として起動したことを伝える。`cross-review` を省く | 単独起動 |
| `--severity-threshold LEVEL` | この重要度未満は `threshold` で見送る | `minor` |
| `--sync-command CMD` | 生成物を同期するコマンド。push の直前に進行側が実行する | なし |
| `--plan-file PATH` | 改修計画を**ファイル**へ書き出す先（対象リポジトリからの相対）。空文字なら記録しない | PR のコメント 1 件 |

**廃止した引数**: `--max-test-rounds` / `--max-outer-rounds` / `--max-items-per-round` と、
`--max-fix-rounds` / `--test-timeout`（決定 24。修正は締め切りまで、テストの上限は予算から
導く）は、渡すと廃止を知らせる 1 行を出して**値を使わずに続ける**。次の版で外す。

```text
/ndf:cross-refactoring 130 --scope src/services tests/services --round-test "pytest tests/services -q" --baseline-test "pytest -q"
/ndf:cross-refactoring 130 --scope src tests --baseline-test "pytest -q" --budget-minutes 30
/ndf:cross-refactoring 130 --scope src tests --baseline-test "pytest -q" --implementer codex --sync-command "make generate"
/ndf:cross-refactoring 130 --scope src tests --baseline-test "pytest -q" --include agy --exclude kiro
```

**モデルを比べたいなら `--model <ランタイム>=<name>` を参加者の全員に指定する。**
実際に動いたモデルを取得できるのは claude だけで、残る者は指定値で代用する。

## 担当の決め方

| 役割 | 決め方 | ホストごとの既定 |
| --- | --- | --- |
| 提案（参加者） | 既定（codex / kiro とホスト）＋ `--include` − `--exclude`。確認を通らない者は外す | claude: claude / codex / kiro、codex: codex / kiro、agy: codex / agy / kiro、kiro: codex / kiro |
| 計画・テスト追加・実装・修正・最終ゲートの修正 | `--implementer` → ホスト（参加者にいれば）→ 参加者の先頭 | 多くはホスト |

- **agy は既定に入らない**（#664）。戻すときは `--include agy` を渡す
- **確認を通らない者は外して続ける。** 全員が揃わないなら始めたくないときは `--require-all`
- **再開で参加者を作り直し、実装担当が外れたら**、計画の前なら決め方を当て直し、計画の後なら止める

## 前提

- `gh` CLI が認証済みで、`jq` と `uv`（または Python 3.10 以上）が使える
- 参加者の CLI がログイン済みである。`init` が認証状態を確認し、通らない者を外して続ける
  （確認コマンドは claude: `claude auth status` / codex: `codex login status` / agy: `agy models` /
  kiro: `kiro-cli whoami`。誤検知するときは `NDF_SKIP_AUTH_CHECK=1`）
- 対象の Pull Request が Draft で開いている（未作成なら `/ndf:pr` で先に作る）
- 本番コードの差分がある。起動の前に Skill のディレクトリで `assess` を打ち、飛ばしてよいかを見る。**終了コード 3 なら
  起動しない**（2 は判定できなかったことを示し、飛ばしてよいとは読まない）

```bash
python3 scripts/refactor.py assess --base origin/develop; echo "exit=$?"
```

- Jev を使うには、環境変数 `AI_GATEWAY_API_KEY` があり、対象が公開リポジトリであること。
  `NDF_JEV=0` で使わない。使えなければ実装担当が同じ判断をする（止まらない）

## 全体フロー

```mermaid
flowchart TD
    Init([init: 予算・実装担当・Jev・着手前のテスト]):::phase --> P
    P["提案（参加者の全員が 1 度・並行）"]:::phase --> MP{"候補 0 件 ?"}
    MP -->|はい| Gate
    MP -->|いいえ| Plan["計画（実装担当）<br/>等級・足すテスト・範囲テスト"]:::phase
    Plan --> Sel["merge-plan: 順位 → 見積り → 時間に収まる件数 → 締め切り"]
    Sel --> T{"テストを足す項目 ?"}
    T -->|ある| AT["テスト追加（実装担当）"]:::phase --> Impl
    T -->|無い| Impl["実装（実装担当）<br/>順位の順に 1 項目 = 1 コミット"]:::phase
    Impl --> V{"範囲テストが通る ?"}
    V -->|いいえ| Fix["修正（実装担当）"] --> Cap{"上限・残り時間 ?"}
    Cap -->|未達| V
    Cap -->|到達| Drop["その項目だけ取り消す<br/>全体のテストなら危険フラグの項目を新しい順に絞る"]:::stop --> V
    V -->|はい| D{"危険フラグ ?"}
    D -->|立った| W{"全体のテストを 1 度"}
    W -->|通る・揺れ・元からの失敗| Gate
    W -->|変更が原因| Fix
    W -->|取り出せない| DropAll["危険フラグの項目をまとめて取り消す"]:::stop --> Gate
    D -->|無い| Gate{"最終ゲート"}
    Gate -->|単独| CR["全体のテスト → /ndf:cross-review"]
    Gate -->|工程の 1 つ| Whole["全体のテスト（使い回しあり）<br/>--ci-check なら継続的統合"]
    CR --> Fin["finalize: 通った実行だけ配分の履歴へ 1 行"]:::ok
    Whole --> Fin

    classDef phase fill:#eef,stroke:#557
    classDef ok fill:#dfd,stroke:#383
    classDef stop fill:#fdd,stroke:#933
```

**駆動の繰り返しは検証と修正の 1 つだけである。** 見送った提案は理由（`budget` / `rank` /
`duplicate` / `vocabulary` / `threshold` / `no_target` / `test_failed` / `not_done`）と
ともに改修計画に残る。

## 実行

メインが決めるのは `--scope` / `--baseline-test` / `--round-test` / `--sync-command` である。決めたら Skill の
ディレクトリで次の 1 行を打ち、最後の行の結果 JSON の `status` と終了コードを見る。

```bash
python3 scripts/drive.py <PR> --scope <範囲...> --baseline-test "<全体のテスト>" [「引数」の表のうち値のあるもの]
```

待ちはコマンドの中で行う。Claude Code では `run_in_background` で起動し、完了通知を 1 回受ける。Codex / Kiro /
agy では共通層の `scripts/lib/bg-wait.sh` で背景に起動し、区切った待ちを 124 が返るあいだ**別の呼び出しとして**
打ち直す。終わると駆動の出力の全体を出し、駆動の終了コードで終わる。

```bash
RC="${TMPDIR:-/tmp}/cross-refactoring-drive-pr<PR>.rc"
bash ../../scripts/lib/bg-wait.sh run "$RC" -- python3 scripts/drive.py <PR> --scope <範囲...> --baseline-test "<全体のテスト>" [上と同じ引数]
bash ../../scripts/lib/bg-wait.sh wait "$RC"   # 1 回 540 秒以内。124 = まだ終わっていない
```

待ち方の規約は [waiting.md](../development-workflow/references/waiting.md)、待ちから戻った後に同じ応答で次の手順へ
進む規則は [agent-layers.md](../development-workflow/references/agent-layers.md) の supervisor の規則にある。
参加者が全て CLI なので、止まるのは単独起動の最終ゲートだけである。JSON の形と終了コードの表は共通層の
`scripts/lib/drive_pause.py` にあり、cross-review の駆動と同じ表を使う。

| 終了コード（`items[0].pause`） | 止まった地点 | すること |
| --- | --- | --- |
| 0 | finalize まで終わった | `items[0].report`（`refactor.py report` の出力）と `metrics` を「完了報告」へ写す |
| 23（`cross-review`） | 単独起動の最終ゲート | `prompt_file` を読み、`items[0].command`（cross-review の駆動）を回して `result_file` へ最終ステータスを書く。同じコマンドを打ち直し、続けて Draft を解除する |
| 1 | 中断（`metrics.exit` に元の終了コード。4 = 予算の指定の誤り・`--round-test` が要る・旧い状態ファイル・取り消しの失敗・範囲を確定できない など） | `summary` を報告して止まる。**握り潰さない**（検証を通っていない変更が残る） |

再開は同じコマンドを打ち直すだけである（`init` が終わったフェーズを返し、最終ゲートの後の進みは
`$TMP_DIR/drive-rf<ID>.json`）。`metrics` は状態ファイルから数えた件数（`items` / `adopted` / `reverted` /
`deferred` / `fix_rounds` / `final_gate` / `review_status`）である。

最終ゲートの判定の相手は起動のされ方で変わる（[docs/04-verify-and-report.md](docs/04-verify-and-report.md)）。
`--workflow-step` なら全体のテスト（`--ci-check` なら継続的統合）で判定して finalize まで進み、単独起動なら
cross-review の最終ステータスを受けてから finalize を呼ぶ。

## アンチパターン

| してはいけないこと | なぜ |
| --- | --- |
| ホストのサブエージェントで実装する | ホストの作業文脈に差分が載り、提案した者と実装する者の独立性が崩れる |
| `launch-cli.sh` に「ホストなら起動しない」分岐を入れる | ホストは実装担当として起動しうる。分岐はランタイム名だけで行う |
| `--scope` を省く / テストの置き場所を入れない | 提案が発散する。足したテストが範囲外になる。**`init` が止める** |
| 実装担当に生成物を同期させる・push させる | 範囲外の変更が生まれる。検証を通る前に公開される |
| 監視の上限を表の固定値のまま使う・LLM の申告で締め切りを守らせる | 予算の外まで走る。**上限はその手順の終わり + 余裕で、スクリプトが時計で止める。** 止めたときの半端な変更は取り込みが捨て、コミット済みの項目は締め切りで判定する |
| 計画の後に判断のために LLM を呼ぶ（Jev・判定の CLI） | 計画で見積もった時間の外で所要が伸び、結果が再現しない。判断は計画で済ませ、後はスクリプトの規則で決める |
| 検証の中で全体のテストを 2 回以上走らせる | 「原則実施しない」が崩れる。走らせ直すのは落ちたテストだけで、取り消した後の HEAD は最終ゲートが確かめる |
| 結果ファイルの申告（所要・コミット）を材料にする | JSON を書き換えるだけで通る検査になる。対応は `Item-Id`、所要は時計とコミットの時刻 |
| 改修計画の URL を Markdown のリンクで書く | 読み手の画面から URL を取り出せない。**生の URL で書く** |
| 取り消した項目の内訳を Pull Request の文章へ並べる | 同じ一覧が 2 か所になり、片方だけが古くなる |
| `git push --force` / `--no-verify` を使う | 他者の作業を消す。検証を飛ばす |
| 提案フェーズでコードを直す | 提案は読むだけ。直すのは実装担当 1 者に集約する |

## 完了報告

`refactor.py report "$ID"` が次を出す（AC26）。

- フェーズごとの所要と、想定最大時間との差（`cross-review` を除く）
- 改善項目の表（**`<ファイル>#<シンボル>`**・兆候・手法・等級・見積り・状態・危険フラグ・修正の回数）
- 採用・取り消し・見送りの件数と、**見送りの理由別の件数**。内訳は**改修計画の生の URL**
- 着手前の全体のテストの結果（通過か失敗・秒・HEAD）と、検証の中で全体のテストを走らせたか、走らせた理由（危険フラグ）、落ちたときの見分け（揺れ・元からの失敗・変更が原因）と結末
- 判断に Jev を使ったか（使わなかった理由・呼び出しの失敗の数）
- 最終ゲートの結果（`cross-review` の収束、または全体のテスト／継続的統合の合否）
- 監視が手順の上限で CLI を止めた手順と、固定のまま残した値（OS の後始末と通信の待ち）
