# 1142: NDF のスクリプトをモデルとクラスから組み直す

要求と受け入れ条件は #1142 の本文にある（コピーは [issue-1142-requirements.md](issue-1142-requirements.md) ）。
この文書は「どう作るか」だけを扱う。エントリポイントの一覧と移行の順序（移行ステップごとに触るファイル）は
[issue-1142-design-migration.md](issue-1142-design-migration.md) 、決定の記録は [issue-1142-design-decisions.md](issue-1142-design-decisions.md) にある。

数は 2026-09-26 03:30 UTC に測った値である（`python3 /tmp/ndf-measure-1142/structure.py plugins/ndf`）。
ファイル 128 本・48,394 行（本文の表から 531 行増えた）、1000 行を超えるファイル 6 本、2 つ以上のファイルで
同じ名前を持つ最上位の関数 86 名（199 件）。

## ドメインモデル

### コンテキスト

スクリプトを 8 つのコンテキストとライブラリに分ける。要求が初期値に挙げた 6 つに、外部 CLI の起動と
文書の検査を足した。どのスクリプトがどこに属するかは移行の文書の「エントリポイントの一覧」が持つ。

| コンテキスト | 用語集の `id` | 何の語が 1 つの意味に決まるか |
| --- | --- | --- |
| ライブラリ | `ndf-workflow` | 時刻・JSON・git / gh の呼び出し・リポジトリの識別・結果 JSON・使用量の帳簿 |
| プランの実行 | `ndf-workflow` | プラン・ステップ・フェーズレポート・ミッション状態ファイル・承認ゲート・検査のトリガー |
| 収束ループ | `ndf-cross-review` / `ndf-cross-refactoring` | ラウンド・指摘・駆動の状態・レビューの状態・リファクタリング計画 |
| リリース | `ndf-release` | 版・リリースプラン・リリース記録・マージの待ち |
| worktree | `ndf-worktree` | 宣言・書き込み先・テスト環境の台帳 |
| ラッパー | `ndf-relay` | セッション・シグナルファイル・ラッパーの束 |
| 記録と測定 | `ndf-workflow` | 実行の要約・会話の記録・待ち行列・通知 |
| 外部 CLI の起動 | `ndf-agent-cli` | 席・起動の上限時間・監視の結果 |
| 文書の検査 | `ndf-instructions` / `ndf-issue-upkeep` | 用語集・指示書・仕様のコピー・課題の棚卸し |

**コンテキストの間は「公開された言語」でやり取りする。** 言語は結果 JSON（`lib/README.md` の契約）と
状態ファイルの形である。プランの実行は `drive.py` の結果 JSON の `metrics` だけを読み、レビューの状態の
ファイルを直に読まない。**ライブラリは全コンテキストの共有カーネルで、どのコンテキストにも依存しない**（I1）。

**コンテキストマップ。** 契約の形を決めるのは上流で、下流はその形に従う。形を変えるときは上流の PR が
下流の読み手とそのテストを同じ PR で直す（I3・I10）。

| 上流（契約を決める） | 下流（従う） | 関係 | 契約 |
| --- | --- | --- | --- |
| ライブラリ | ほかの 8 つすべて | 共有カーネル | 関数の引数と戻り値・結果 JSON の形（`lib/README.md`） |
| 収束ループ | プランの実行 | 公開ホストサービス / 順応者 | `drive.py` の結果 JSON（`metrics`） |
| 外部 CLI の起動 | 収束ループ | 公開ホストサービス / 順応者 | 監視の結果（`*-monitor.json`）と起動の上限時間 |
| リリース | プランの実行 | 公開ホストサービス / 順応者 | `release-steps.py` などの結果 JSON |
| worktree | プランの実行・収束ループ | 公開ホストサービス / 順応者 | 宣言と書き込み先の出力 |
| 文書の検査 | プランの実行 | 公開ホストサービス / 順応者 | 検査のスクリプトの結果 JSON |
| プランの実行 | 記録と測定 | 公開ホストサービス / 順応者 | 実行の状態・ミッション状態ファイルの形（I11） |
| ラッパー | プランの実行 | 公開ホストサービス / 順応者 | `ndf-next` の囲みの形とシグナルファイル（`next.json`）。囲みを解析して `next.json` へ写すのは `relay.py mark`（Stop hook）で、`mission-state.py next`・`token-guard.sh` はその形に従って囲みを出す |
### 集約

集約はどれも状態を持つファイルである。値オブジェクトはコードの中の型で、ファイルの形は変えない（I11）。

| 集約 | 持ち主（書き換えてよいもの） | 根 | エンティティ | 値オブジェクト |
| --- | --- | --- | --- | --- |
| プラン | `supervise_lib.templates`（`new` が書く） | `plan.json` | ステップ | ステップの型・条件 |
| 実行の状態 | `supervise_lib.engine.RunState` | `<プラン>-state/` | ステップの記録 | `ClaudeCall`・件数 |
| ミッション状態ファイル | `mission-state.py` | `mission.json` | プラン・承認ゲートの記録 | 版 |
| レビューの状態 | `review_lib.store.ReviewStore` | `cross-review-pr<N>-state.json` | ラウンド・指摘 | `final`・`sweep` |
| 駆動の状態 | 各 Skill の `drive.py` の `Drive` | `drive-pr<N>.json` / `drive-rf<ID>.json` | — | 段階・`init_vars` |
| リファクタリング計画 | `refactor_lib`（今のまま） | 状態ファイル | 改善項目 | 配分 |
| 検査の記録 | `check-trigger.py` | `checks/<owner>__<repo>.jsonl` | 評価・検査の行 | 件数 |
| 使用量の帳簿 | `lib/usage_ledger.UsageLedger` | `usage/<owner>__<repo>.jsonl` | — | `UsageRecord` |
| テスト環境の台帳 | `lib/worktree-registry.sh` | 台帳のディレクトリ | スロット | ポート |
| ラッパーの記録 | `relay_lib.record.RelayRecord`（`run.Relay` と `mark` はこれを通して書く） | `log.jsonl`・`next.json` | セッション | ブロック |
| ラッパーの束 | `relay_lib.bundle.Bundle` | `~/.claude/ndf/relay.current` | 版ごとの束 | 束の digest |
| 版 | `release-steps.py bump` | `plugin.json` の `version` | — | 版数 |

### 不変条件

| # | 集約 | 条件 | 破れたときの扱い |
| --- | --- | --- | --- |
| I1 | （ライブラリ） | ライブラリのモジュールは、ライブラリと標準ライブラリだけを import する | 構造チェックが落とす |
| I2 | ラッパーの束 | 束の中のモジュールは、束の中と標準ライブラリだけを import する | 構造チェックが落とす |
| I3 | （全体） | エントリポイントのパス・副命令・引数・出力の形は移行の前後で同じ。例外は移行の文書の「変えるエントリポイント」だけ | 契約テストが落とす |
| I4 | （全体） | 本体の同じ関数が 2 つのファイルに無い。同じ名前で本体の違う関数は例外リストに載る | 構造チェックが落とす |
| I5 | （全体） | テストを除くスクリプトは 1000 行以下。例外は例外リストに理由付きで載る | 構造チェックが落とす |
| I6 | （全体） | 既定で動くものは `experimental/` を import しない。向きは試行 → 安定だけ | 既存の `test_experimental.py` が落とす |
| I7 | 駆動の状態 | 段階が `sweep` か `done` で `init_vars` があるとき、`drive.py` は `init` を打たずに `init_vars` を使う。これでレビューの状態の `final` を同じ PR の空の状態で上書きさせない | `sweep` から打ち直して `metrics.rounds` が 0 でないテストが落とす |
| I8 | 使用量の帳簿 | `claude -p` の 1 回の呼び出しにつき 1 行を追記し、既存の行を書き換えない | 追記に失敗しても呼び出しの結果は返し、stderr に 1 行を出す |
| I9 | ラッパーの束 | 束を書き終えてから `relay.current` を 1 回の原子的な書き込みで替える | 書き終わらない束は `relay.current` から指されず、次の起動で消える |
| I10 | （移行） | 移行ステップは 1 本の PR で閉じ、その revert 1 つで 1 つ前に戻る | 戻せない形の PR は分ける |
| I11 | 状態ファイル | 状態ファイルの形は変えない。変えるのはキーの追加だけ | 読む側のテストが落とす |
| I12 | （全体） | 1 つの集約のファイルを書くモジュールは 1 つ | 構造チェックの書き手の表で見る（テスト設計） |

### ドメインイベント

番号は要求の「ドメインイベント」を引き継ぐ。

| # | イベント | 発生元 | 受け手 |
| --- | --- | --- | --- |
| E1 | 移行の前の基準を測った | conductor（計測の 2 本） | 設計（この文書の数） |
| E2 | スクリプトの語を用語集に揃えた | `requirements-design` | 設計（用語の表） |
| E3 | 設計を承認した（承認ゲート 1） | 利用者 | 移行ステップ S1〜S3 |
| E4 | ライブラリを 1 か所へまとめた | 移行ステップ L0 | コンテキストの移行ステップ C1〜C7 |
| E5 | コンテキストごとのモジュールへ移した | C1〜C7 | 語の移行ステップ W1〜W7 |
| E6 | エントリポイントの形が変わらないことを確かめた | 各移行ステップの契約テスト | 開発版のリリースプラン |
| E7 | 開発版を導入して確かめた | `release-verification-steps.py verify-install` | 本番のリリースプラン |
| E8 | 本番を承認した（承認ゲート 2） | 利用者 | 次のミッション |
| E9 | 移行の後を同じ物差しで測り直した | conductor（E1 と同じ 2 本） | 振り返り（課題のコメント） |

### 用語

| 用語 | 意味 | 用語集への反映 |
| --- | --- | --- |
| 使用量の帳簿 | `claude -p` の 1 回の呼び出しごとに、版・プラン・ステップ・usage を 1 行で追記する jsonl | 追加（`ndf-workflow`） |
| ラッパーの束 | `~/.claude/ndf/` に版ごとに置く、ラッパーの `relay_lib/` と使うライブラリの写し | 追加（`ndf-relay`） |
| 構造チェック | テストを除くスクリプトの行数と、本体の同じ関数・同じ名前で本体の違う関数を構文木で数える継続的統合のチェック | 追加（`ndf-workflow`） |

## 機能一覧

| # | 機能 | 誰が使うか |
| --- | --- | --- |
| F1 | 時刻・JSON・git / gh・リポジトリの識別・駆動の部品を 1 つの関数で呼ぶ | スクリプトを直す worker |
| F2 | 1 つの責務を 1000 行以下のモジュールで読む | スクリプトを直す worker |
| F3 | 行数と重複を継続的統合で落とす | レビューする者 |
| F4 | 即時修正を「テスト → PR → `merge-when-green`」のプランで流す（不足 a） | conductor |
| F5 | conductor が直に使うエントリポイントを 1 か所で引く（不足 b） | conductor |
| F6 | 本番のリリースプランが、差分のある ndf 以外のプラグインの版も上げる（不足 c） | conductor |
| F7 | 検査のフェーズレポートと `check-trigger.py stats` が実際のラウンド数と指摘数を返す（不足 d） | conductor・振り返り |
| F8 | 設計の MVV の判定に設計文書を材料として渡す（不足 e） | conductor |
| F9 | プランが起動した `claude -p` の消費を版ごとに集計する（不足 f） | 振り返り |
| F10 | ラッパーを複数のモジュールに分けたまま、1 回の導入で複製して動かす | 利用者 |
| F11 | 移行の前後を同じコマンドで測る | 振り返り |

## 構成要素

| 要素 | 責務 |
| --- | --- |
| `lib/clock.py`（新設） | 今の時刻・ISO の書き出しと読み取り・秒の差。書き出しの形は引数で選ぶ |
| `lib/jsonio.py`（新設） | JSON の読み（無いとき・壊れたときの扱いを引数で選ぶ）と原子的な書き込み |
| `lib/proc.py`（新設） | 子プロセス・git の起動。失敗は `StepError(msg, code)` に揃える |
| `lib/repo.py`（新設） | メインディレクトリ・`owner/repo`・slug・宣言のベースブランチ |
| `lib/loop_drive.py`（新設） | 2 つの `drive.py` が使う `call`・`parse_vars`・`review_status` |
| `lib/usage_ledger.py`（新設） | 使用量の帳簿の追記と読み取り |
| `lib/limits.py`（変更） | 外部 CLI の起動の上限時間を 1 つの関数で決める（`resolve_cli_timeout`） |
| `lib/step_result.py`・`lib/gh_parts.py`・`lib/statefile.py`（変更） | 結果 JSON・GitHub の部品・KEY=VALUE の出力に絞る。git と時刻は新設の 3 本へ移す |
| `scripts/supervise.py` と `scripts/supervise_lib/`（分割） | エントリポイントは引数の解析だけ。実行・雛形・queue は下のパッケージ |
| `skills/cross-review/scripts/state.py` と `review_lib/`（分割） | エントリポイントは副命令の解析だけ。状態・GitHub・指摘・副命令の本体は下のパッケージ |
| 2 つの `drive.py`（変更） | `final` の後に `init` を打たない（I7）。共通の部品は `lib/loop_drive.py` |
| `scripts/relay.py` と `relay_lib/`（分割） | 起動の口だけを持つランチャーと、束に入るパッケージ。`log.jsonl`・`next.json` を書くのは `relay_lib.record` だけ（I12） |
| `lib/worktree-common.sh`（分割） | 読み込みの口として残し、字句解析・書き込み先・ブランチ・台帳の 4 本を読み込む |
| `lib/monitor.py`（分割） | 照合の表を `lib/monitor_patterns.py` へ出す（`supervise.py` も読む） |
| `instructions-check.py`（分割） | 型と宣言と観点を隣の `instructions_lib/model.py` へ出す（読み手が 1 つのためライブラリに置かない） |
| `release-steps.py`（変更） | 差分のあるプラグインを列挙する副命令 `changed-plugins` |
| `mvv-gate.py`（変更） | 設計の判定で PR の `issues/` の設計文書を材料に足す |
| `scripts/check-script-structure.py`（新設、リポジトリ根） | 構造チェック。例外リストは `scripts/script-structure-allow.json` |
| `scripts/measure/`（新設、リポジトリ根） | E1 と E9 の計測の 2 本（`structure-baseline.py`・`claude-p-usage.py`） |
| `development-workflow/references/conductor-entrypoints.md`（新設） | conductor が直に使うエントリポイントの一覧 |

**構成要素図**（依存は矢印の向きだけ。ライブラリから外へ向かう矢印は無い）

```mermaid
graph TD
    subgraph エントリポイント
        SV[supervise.py]
        ST[cross-review の state.py]
        RL[relay.py ランチャー]
        DR[2 つの drive.py]
        RP[refactor.py]
        OT[ほかの手順のスクリプト]
    end
    subgraph コンテキストのパッケージ
        SL[supervise_lib]
        RV[review_lib]
        RB[relay_lib]
        RF[refactor_lib]
    end
    subgraph ライブラリ
        CK[clock / jsonio]
        PR[proc / repo]
        LD[loop_drive / limits]
        UL[usage_ledger]
        SR[step_result / gh_parts / statefile]
    end
    SV --> SL
    ST --> RV
    RL --> RB
    DR --> LD
    RP --> RF
    OT --> SR
    SL --> UL
    SL --> PR
    SL --> SR
    RV --> PR
    RV --> SR
    RF --> CK
    LD --> PR
    RB --> CK
    UL --> CK
    SR --> PR
    PR --> CK
```

**文脈**（外部の系は変えられないものだけ）

```mermaid
graph LR
    CD[conductor と利用者] --> NDF[NDF のスクリプト]
    HK[Claude Code / Codex / Kiro / agy の hook] --> NDF
    NDF --> GH[GitHub の API と gh]
    NDF --> CL[claude / codex / kiro / agy の CLI]
    NDF --> ST[利用者の端末の状態の置き場]
    CI[継続的統合] --> NDF
```

**配置**（ラッパーだけが、プラグインのキャッシュの外で動く）

```mermaid
graph TD
    subgraph プラグインのキャッシュ
        P1[scripts/relay.py と relay_lib と lib]
    end
    subgraph 利用者の設定の置き場
        C1[~/.claude/ndf/relay.py ランチャー]
        C2[relay.current]
        C3[relay-版-digest の束]
    end
    subgraph 状態の置き場
        S1[~/.local/state/ndf/usage と sv と checks]
    end
    P1 -- install と startup が束を写す --> C3
    P1 -- 束を書き終えてから替える --> C2
    C1 -- 起動時に読む --> C2
    C1 -- 全モジュールを先に import --> C3
```

## 構造

### パッケージ・モジュール構成

新設は `+`、分割して中身を移すものは `>`、中身を薄くするエントリポイントは `=` で示す。

```text
plugins/ndf/scripts/
├── = supervise.py                # 引数の解析と main（約 450 行。説明文を短くする）
├── + supervise_lib/              # __init__・paths・decl・prompts・claude・plan・slow・steps・engine・templates・mission・queue・commands
├── = relay.py                    # ランチャー（約 80 行）。プラグインでも複製でも同じバイト列
├── + relay_lib/                  # __init__・common・proc・record・mark・claude・terminal・run・bundle・install
├── > instructions-check.py       # 約 870 行
├── + instructions_lib/           # __init__・model
└── lib/
    ├── + clock.py  + jsonio.py  + proc.py  + repo.py  + loop_drive.py  + usage_ledger.py
    ├── + monitor_patterns.py      # monitor.py は約 860 行
    ├── = worktree-common.sh      # 約 465 行。下の 4 本を読み込む
    └── + worktree-branch.sh  + worktree-shell-lex.sh  + worktree-write-target.sh  + worktree-registry.sh
plugins/ndf/skills/cross-review/scripts/
├── = state.py                    # 副命令の解析と main（約 220 行）
└── + review_lib/                 # __init__・store・workspace・github・categories・participants・findings・commands/（9 本）
scripts/                          # リポジトリの根（開発用）
├── + check-script-structure.py  + script-structure-allow.json
└── + measure/structure-baseline.py  + measure/claude-p-usage.py
```

見積りの行数は移行の文書の各移行ステップにある。**どのファイルも 1000 行以下に収まり、行数の例外は 0 件
である。** 分けたファイルの最大は `worktree-write-target.sh` の約 950 行、変えないものの最大は `gitfacts.py` の 991 行。

### クラス図: プランの実行

`Supervisor` の 1018 行を、ステップの型ごとのハンドラーと、状態の持ち主と、claude の呼び出しに分ける。

```mermaid
classDiagram
    class Engine {
        +run(plan) Report
        -next_of(step)
    }
    class StepHandler {
        <<interface>>
        +kind
        +execute(ctx, step) StepOutcome
    }
    class RunState {
        +record(step, outcome)
        +write_report(text)
    }
    class ClaudeRunner {
        +call(prompt, mode) ClaudeCall
    }
    class ClaudeCall {
        +usage
        +model_usage
        +cost
    }
    Engine "1" --> "5" StepHandler
    Engine "1" --> "1" RunState
    StepHandler ..> ClaudeRunner
    ClaudeRunner ..> ClaudeCall
    ClaudeRunner ..> UsageLedger
```

`StepHandler` の実装は `RunStep`・`WorkStep`・`DriveStep`・`PrStep`・`JudgeStep` の 5 つ（ステップの型と
1 対 1）。遅れの見張り（`SlowWatch`）は `Engine` が持ち、ハンドラーからは `ctx` 越しに使う。ハンドラーは
`Engine` を import しない。雛形（`templates`・`mission`）は `Engine` を import しない。

### クラス図: 収束ループ

`state.py` の 225 個の関数を、状態の持ち主と外部の呼び出しと指摘の規則に分ける。辞書の形はそのまま使い、
`ReviewState` はその上に読み方を足す薄い型にする（I11）。

```mermaid
classDiagram
    class ReviewStore {
        +load(pr) ReviewState
        +save(state)
    }
    class ReviewState {
        +rounds
        +final
        +sweep
        +current_round()
        +findings_total()
    }
    class GitHubClient {
        +pr_metadata(pr)
        +unresolved_threads(pr)
        +check_runs(sha)
    }
    class Drive {
        +run()
        -init_or_restore()
    }
    ReviewStore ..> ReviewState
    Drive ..> ReviewStore : 数えるときだけ読む
    Drive ..> GitHubClient : state.py の副命令越し
```

`Drive` は今も `state.py` の副命令をファイルのパスで起動する。直に import するのは件数を数える `counts()`
のための読み取りだけで、書き込みは `ReviewStore` だけが行う（I12）。

## データ構造

### 使用量の帳簿

置き場所は `${XDG_STATE_HOME:-~/.local/state}/ndf/usage/<owner>__<repo>.jsonl`（`check-trigger.py` の記録と
同じ解決）。**1 行 1 呼び出しの事象の記録にする。** 合計は読む側が導く。

| 列 | 型 | 空を許すか | 意味 |
| --- | --- | --- | --- |
| `at` | string（UTC・`Z`） | 許さない | 呼び出しが終わった時刻 |
| `ndf_version` | string | 許さない | 起動したスクリプトの `plugin.json` の版 |
| `source` | string | 許さない | `supervise` / `mvv-gate` |
| `plan` / `step` | string | 許す | 空は「プランの外の呼び出し」（`mvv-gate`） |
| `kind` | string | 許さない | `work` / `full` / `judge` / `slow` / `pr` / `mvv` |
| `model` | string | 許す | 空は「`modelUsage` が無かった」 |
| `usage` | object | 許さない | `claude -p` の `usage` をそのまま（`cache_creation.ephemeral_5m/1h_input_tokens` を含む） |
| `model_usage` | object | 許す | `modelUsage` をそのまま |
| `cost_usd` / `turns` / `seconds` | number | 許す | 空は「結果に無かった」。0 と区別する |
| `session_id` | string | 許す | `--no-session-persistence` でも返る値 |

### 実行の状態の置き場所

`<プラン>-state/` のパスは変えない（I3）。**プランのファイルが一時ディレクトリの下にあるときだけ、実体を
`${XDG_STATE_HOME}/ndf/sv/<stem>-<プランの絶対パスの sha256 の先頭 8 字>/` に作り、`<プラン>-state` を
そこへのシンボリックリンクにする。** 実体にはプランの写し `plan.json` も置く。`state.json` の `log[]` の
各ステップの `llm` には、キー `cache_write_5m`・`cache_write_1h`・`models` を足す（I11）。

### 駆動の状態

`drive-pr<N>.json` にキー `init_vars`（`state.py init` の KEY=VALUE を dict にしたもの）を足す。
段階が `sweep` か `done` で `init_vars` があるとき、`drive.py` は `init` を打たずにこれを使う（I7）。

### ラッパーの束

```text
~/.claude/ndf/
├── relay.py                      # ランチャー（プラグインの scripts/relay.py と同じバイト列）
├── relay.current                 # 使う束のディレクトリ名 1 行。原子的に書き換える
├── relay.version                 # 今のまま（版の記録）
└── relay-<版>-<digest 8 字>/
    ├── relay_lib/                # 束の中身
    ├── lib/clock.py  lib/jsonio.py
    └── MANIFEST                  # 束のファイルと sha256。digest はこの内容の sha256
```

時系列の扱い: 帳簿と `log.jsonl` は事象の記録（追記だけ）。束は版ごとに新しいディレクトリを作り、古い
束は `startup` が「`relay.current` が指さず、動いている `run` の pid ファイルも指さない」ものを 2 つを残して消す。

### CRUD 図

| 機能 | 使用量の帳簿 | 実行の状態 | 駆動の状態 | レビューの状態 | 検査の記録 | ラッパーの束 | 版 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F4 即時修正のプラン | — | C | — | — | C（`--escape-of` のとき） | — | — |
| F6 他のプラグインの版 | — | U | — | — | — | — | U |
| F7 検査の件数 | — | U | U | R | C | — | — |
| F8 設計の MVV | C | — | — | — | — | — | — |
| F9 版ごとの集計 | R | R | — | — | — | — | — |
| F10 ラッパーの複製 | — | — | — | — | — | C / U / D | R |

## 入出力の契約

変えるエントリポイント（追加 5・読み替え 5・撤去 2）と、足す約束の入力・出力・失敗の形・互換性は、移行の文書の
「変えるエントリポイント」にある。

## 処理の流れ

図に現れない構成要素は、呼び出しの順序を変えずに置き場所だけが変わるもの（ライブラリの変更・`worktree-common.sh`・
`monitor.py`・`instructions-check.py`）と、1 つのスクリプトの中で閉じるもの（`release-steps.py`・`mvv-gate.py`・構造チェック・計測・一覧）である。

### claude の呼び出しと帳簿

```mermaid
sequenceDiagram
    participant H as StepHandler
    participant C as ClaudeRunner
    participant P as claude -p
    participant L as UsageLedger
    participant S as RunState
    H->>C: call(prompt, mode)
    C->>P: 起動（JSON の出力）
    P-->>C: usage と modelUsage と cost
    C->>L: append(UsageRecord)
    alt 追記に失敗
        L-->>C: 例外
        C->>C: stderr に 1 行（I8）
    end
    C-->>H: ClaudeCall
    H->>S: record(step, outcome)
```

### 収束した検査の再開（不足 d）

```mermaid
graph TD
    A[drive.py run] --> B{駆動の状態の段階}
    B -- sweep / done かつ init_vars あり --> C[init_vars を使う]
    B -- それ以外 --> D[state.py init を打つ]
    D --> E[init_vars を保存]
    C --> F[段階の続き]
    E --> F
    F --> G[counts でレビューの状態を数える]
    G --> H[結果 JSON の metrics]
```

入れ子の最終ゲート（`item.command`）の件数は、`DriveStep` が内側の `metrics` を `counts` の `inner` に残す。

### ラッパーの束の切り替え

```mermaid
sequenceDiagram
    participant H as startup か install
    participant B as Bundle
    participant F as ~/.claude/ndf
    participant R as 動いている run
    H->>B: ensure(版)
    B->>F: relay-版-digest.tmp へ写す
    B->>F: rename で relay-版-digest へ
    B->>F: relay.current を原子的に書く（I9）
    Note over R: 起動時に全モジュールを import 済みのため影響しない
    B->>F: 指されない古い束を 2 つ残して消す
```

ランチャーの起動: `relay.py` の隣に `relay_lib/` があればそれを使う（プラグインのキャッシュ）。無ければ
`relay.current` の束を `sys.path` の先頭に置く。どちらでも、`run` は処理の前に束の全モジュールを import する。

### 移行ステップの流れ

```mermaid
graph LR
    A[触るファイルが他の PR と重ならないか見る] --> B[移すものに現状固定テストが無ければ足す]
    B --> C[移す・テストの差し替え先を移す]
    C --> D[範囲テスト・全体テスト・構造チェック・契約テスト]
    D -- 落ちた --> E[その移行ステップを戻す]
    D -- 通った --> F[PR と merge-when-green]
```

## 非機能の実現方式

| 大項目 | 要求の条件 | 実現方式 | 確かめ方 |
| --- | --- | --- | --- |
| 性能・拡張性 | worker がスクリプトを 1 つ直すときに読むファイルの大きさの中央値が、移行の前より小さい | 1000 行を上限に責務ごとのモジュールへ分け、エントリポイントは解析だけにする | E1 と E9 で `measure/structure-baseline.py` の中央値を比べる |
| 運用・保守性 | 並列のミッションが同じファイルを触った件数が、移行の後の 10 本の PR で移行の前より少ない | コンテキストのパッケージに分け、ライブラリの変更を L0 に集める | 移行の前後 10 本の PR の変更ファイルの重なりを `measure/structure-baseline.py --prs` で数える |
| 移行性 | 途中の版を導入した利用者の手順が変わらない。どの移行ステップでも 1 つ前へ戻せる | エントリポイントを残し中身だけ移す。語は読む側が新旧を受ける。1 移行ステップ 1 PR（I10） | 開発版ごとの `verify-install`。各 PR の revert を手元で当てて全体テスト |
| システム環境 | Python 3.10 以上・bash 3.2 で動く。依存パッケージを足さない | `clock.parse` が `Z` を置き換えてから `fromisoformat` を呼ぶ。シェルは既存の移植性テストの対象の `lib/` 直下に置く | Python 3.10 で全体テスト（`uv run --python 3.10`）。`test_portability.py` |

## テスト設計

| 受け入れ条件・不変条件 | 何で確かめるか |
| --- | --- |
| 計測と振り返りがコメントに残る | E1 のコメント（済み）。E9 は X2 のコメント |
| 計測のスクリプトが同じコマンドで打ち直せる | S1 が `scripts/measure/` に置き、`--help` と 1 回の実行を PR のテスト計画に載せる |
| 移行の後に測り直して比べる | X2 で E1 と同じ 2 本を打ち、表で比べる |
| 設計文書にモデル・責務・移行の順序がある | 設計 PR のレビュー |
| エントリポイントの一覧と変えるものがある | 設計 PR のレビュー（移行の文書） |
| 1000 行を超えるファイルが無い（I5） | `check-script-structure.py` の行数の検査（継続的統合） |
| 概念ごとに実装が 1 つ・チェックが継続的統合で走る（I4） | 同じ構造チェックの重複の検査。本体の比較は docstring・型注釈・関数名を除く |
| 即時修正を 1 つのエントリポイントで流せる（a） | `test_supervise_new.py` に `new fix` のプランの形のテスト |
| 一覧が 1 か所にある（b） | `check-markdown-links.py` と、一覧に載るスクリプトが実在するかのテスト |
| 他のプラグインの版を上げる（c） | `changed-plugins` のテスト（mcp-serena だけを変えた一時リポジトリ）と、本番の雛形のテスト |
| 実際のラウンド数と指摘数を返す（d・I7） | `sweep` まで進めた駆動の状態から `drive.py` を打ち直し、`metrics.rounds` が 0 でないテスト |
| 設計文書が材料に渡る（e） | `issues/x-design.md` を変更ファイルに持つ PR を差し替えた `mvv-gate.py` のテスト |
| 消費が版ごとの集計に入る（f・I8） | `ClaudeRunner` のテスト（帳簿に 1 行・5 分と 1 時間が分かれる）、追記の失敗のテスト、`token-usage.py` が帳簿を読むテスト、一時ディレクトリの下のプランの状態がシンボリックリンクになるテスト |
| 既存のテストが置き換え先の変更だけで通る | 各移行ステップの差分のうち `tests/` の変更が import と差し替え先だけかを、レビューの観点に入れる |
| エントリポイントの形が変わらない（I3） | `scripts/tests/test_entrypoints.py`（新設）が一覧の各エントリポイントの `--help` の副命令と、代表の出力の形を固定する |
| `build-runtime-plugins.sh --check` が通り、配布物に新しいモジュールが入る | `--check` と、開発版の `verify-install` の後に 3 ランタイムの導入先で `supervise_lib` などの有無を見る |
| 複製が 1 回の導入で動く（F10・I2・I9） | `test_relay.py` に束の作成・切り替え・古い束の削除・書きかけの束が指されないテスト。手動確認はラッパーの下でセッションを 1 回切り替える |
| I1・I2 | 構造チェックの import の検査（ライブラリと束の中身の import 先を構文木で見る） |
| I6 | 既存の `test_experimental.py` |
| I10 | 各 PR のテスト計画に revert の確認を載せる |
| I11 | 状態ファイルを読む側の既存テストと、足したキーの読み書きのテスト |
| I12 | 構造チェックが、集約のファイル名を書き込み先に持つ関数の置き場所を表にし、集約の表と照らす |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| 並列のミッションとの重なり | 移行ステップの着手の時点で開いている PR の変更ファイルを見るまで決まらない。重なれば順序を入れ替える（E5） |
| bash 3.2 の実機 | 継続的統合に bash 3.2 が無い。検査は文字列の照合だけで、実機での読み込みは macOS の利用者の開発版の確認で見る |
| hook の速さ | `worktree-common.sh` を分けても、guard は全部を読むため速くならない。ほかの hook が速くなるかは C5 で測る |
| `claude -p` の出力の形 | `modelUsage` と `cache_creation` のキーは 2026-09-26 に 1 回確かめただけ。帳簿は受けた形をそのまま残し、読む側で欠けを許す |
| ラッパーの束と macOS | `rename` と原子的な書き込みの振る舞いは Linux でだけ確かめる。macOS の利用者の開発版の確認で見る |
| 計測の 2 本の置き場所 | 本文は「設計 PR で `scripts/` へ移す」と書くが、設計 PR は文書だけを載せる。S1 で移す（決定 12） |
