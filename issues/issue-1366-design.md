# #1366: プロジェクト MVV を宣言し、候補から定め、判断の地点がそれに従い、改訂できるようにする

要求と受け入れ条件は #1366 の本文にある（コピーは `issues/issue-1366-requirements.md` ）。この文書は「どう作るか」だけを扱う。
決定の記録・テスト設計・未確認のまま残ることは `issues/issue-1366-design-decisions.md` にある。

## 例: ai-plugins でプロジェクト MVV を定め、承認ゲートがそれを読むまで

1. conductor が `development-workflow` の手順 0 で `project-mvv.py check` を打つ。`.ndf/mvv.json` が無いので終了コード 2 と
   「プロジェクト MVV が無い」が出る。LLM も `gh` も呼ばず、何も書かない
2. `project-mvv.py collect` が材料を集める。コミット 3781 件・課題 573 件で閾値（100 件・20 件）を超えるので履歴モードになり、
   `git log` の件名・課題の題名と本文・`docs/ndf-version-decisions.md`・`AGENTS.md`・`README.md` から出典つきの材料を
   状態ディレクトリの `materials.json` へ書く（git 0.03 秒・`gh issue list` 全件 5.9 秒の実測で、60 秒に収まる）
3. `project-mvv.py propose` が最小構成の `claude -p` を 1 回呼び、候補 A・B と「利用者に決めてもらう点」を
   `candidates.json` と `candidates.md` へ書く（2026-09-27 に手で作った `issues/mvv-candidates-ai-plugins.md` と同じ形）
4. conductor が `candidates.md` を示し、分かれる点を `AskUserQuestion` で問う。答えから本文を固める。ai-plugins では
   `issues/mvv-ai-plugins.md` から「上位の原則」と「改訂」の節を除いたものが本文になる。**ここまで `.ndf/` に何も書かない**
5. `project-mvv.py vet --kind candidate --body <本文>` が上位の原則に照らし、「従う」を返す（終了コード 0）
6. 利用者が承認した後に `project-mvv.py approve --body <本文> --by takemi.ohama --reason 初版` を打つ。
   `.ndf/mvv.md`（本文）と `.ndf/mvv.json`（版 1・sha256・日時・承認者・照合の記録）ができ、`check` が 0 を返す
7. 次の設計 PR で `mvv-gate.py check --gate design` が、上位の原則・プロジェクト MVV（版 1）・ミッション MVV の順に並べた
   材料で判定し、`mvv-gate.jsonl` に `"project_mvv": {"status": "approved", "version": 1, ...}` と `"basis": ["Value 1"]` を残す。
   同じ PR の cross-review は担当と修正担当の「指摘の基準」の後ろに MVV の節を受け取り、見送りの返信に「根拠: Value 1（MVV 版 1）」と書く

## ドメインモデル

### コンテキスト

| コンテキスト | 何の語が 1 つの意味に決まるか |
| --- | --- |
| NDF の開発ワークフロー（`ndf-workflow` ） | 上位の原則・プロジェクト MVV・ミッション MVV・MVV の版・MVV の改訂・MVV 候補・傾向モード・MVV の照合・判断の地点・根拠の項目 |
| NDF の cross-review（`ndf-cross-review` ） | 指摘の基準・見送り・見送りの返信 |
| NDF の cross-refactoring（`ndf-cross-refactoring` ） | 提案・候補・見送りの理由・リファクタリング計画 |

関係は**公開された言語**である。開発ワークフローが宣言のスキーマ（`mvv.schema.json` ）と、MVV の節・根拠の項目・状態への写しの形
（`lib/project_mvv.py` ）を公開し、cross-review・cross-refactoring・supervise が同じ形を読む。どの受け手も宣言を書き換えない。

### 集約

| 集約 | 持ち主（書き換えてよいもの） | 根 | エンティティ | 値オブジェクト |
| --- | --- | --- | --- | --- |
| プロジェクト MVV の宣言 | `project-mvv.py approve` だけ | 宣言（`.ndf/mvv.json` ） | 版 | 本文・sha256・承認の記録（日時・承認者）・理由・差分・照合の記録・設定（閾値） |
| 上位の原則 | NDF の配布物（リリースだけが変える） | 原則の文（`scripts/data/mvv-principle.md` ） | — | 原則の文・序列・3 択の規則 |
| MVV 候補の抽出（1 回の実行） | `project-mvv.py collect`（材料）と `propose`（候補） | 抽出の実行 | 候補 | 材料の出典・モード（履歴 / 傾向）・欠け・分かれる点 |
| MVV の照合（1 回） | `project-mvv.py vet` | 照合の記録 | — | 種類（candidate / revision / mission）・対象の sha256・判定・箇所 |
| ミッション状態ファイル（既存） | `mission-state.py` | `mission.json` | 承認ゲートの記録 | プロジェクト MVV の参照（版・sha256）を足す |
| 判断の記録（既存・地点ごと） | 各地点の既存の持ち主（`mvv-gate.py`・`fix-steps.py`・`refactor.py`・supervise の engine） | 各地点の状態ファイル・jsonl | 判断 1 件 | MVV の参照（状態・版・sha256）・根拠の項目 |

**宣言は ID（版と sha256）で参照する。** ミッション状態ファイルも判断の記録も本文を写さず、`{status, version, sha256}` だけを持つ。
本文を読むのは `lib/project_mvv.py` だけで、判断の地点はそこから MVV の節を受け取る。

### 不変条件

| # | 集約 | 条件 | 破れたときの扱い |
| --- | --- | --- | --- |
| I1 | 宣言 | `.ndf/mvv.md` と `.ndf/mvv.json` を書くのは `approve` だけで、同じ sha256 の本文への直近の照合が「従う」（または「判定できない」を `--accept-unknown` で人が引き受けた）ときだけ書く | 照合が無い・「反する疑い」なら終了コード 1 で止まり、箇所を示す。ファイルは作らない（AC5・AC16） |
| I2 | 宣言 | `check` が 0 を返すのは、`mvv.md` の sha256 が `versions` の最後の版の sha256 と一致するときだけである | 一致しなければ終了コード 4「承認と一致しない」（AC6） |
| I3 | 宣言 | 版は 1 から 1 ずつ上がり、`versions` は追記だけで過去の版（本文・理由・差分・承認の記録）を書き換えない。版 2 以降は理由を必ず持つ | `approve` が理由の無い改訂と、本文が現行と同じ改訂を止める（AC10） |
| I4 | 宣言 | 本文は `## Mission` / `## Vision` / `## Value`（番号つきの箇条）/ `## レッドライン`（`R<番号>` の表）を持ち、上位の原則の節を持たない | `approve` が欠けた見出しと原則の節を示して止まる（前提 8） |
| I5 | 上位の原則 | 判断の地点へ渡す MVV の節・候補の生成・照合のプロンプトは、原則の文を先頭に持つ。プロジェクト MVV が無くても持つ | 原則の文が先頭に無いプロンプトができたら誤り（AC16） |
| I6 | 判断の記録 | プロジェクト MVV が無い・承認と一致しない・壊れているとき、mvv-gate 以外の地点は止まらず「MVV なし」として進み、状態をその記録に書く。mvv-gate は承認と一致しない・壊れているなら LLM を呼ばずに 10 を返し、無ければミッション MVV だけで判定する | 地点が止まったら誤り。mvv-gate が一致しない宣言で LLM を呼んだら誤り（AC7・E7） |
| I7 | 判断の記録 | LLM が判断した記録（`mvv-gate.jsonl`・修正の振り分けと見送りの返信・提案と候補と見送り・judge の結果）は根拠の項目を必ず持つ。MVV が無ければ「MVV なし」、あって LLM が返さなければ「根拠なし」を入れ、根拠の欠けで止めない | 根拠の項目が空の記録ができたら誤り（AC9） |
| I8 | MVV 候補の抽出 | `check` と `collect` は LLM を呼ばない。`gh` は読み取りだけに使う。`check` はファイルを書かない | LLM か `gh` の書き込みを呼んだら誤り（AC1・AC2） |
| I9 | MVV 候補の抽出 | コミット数と課題数の両方が閾値に満たないときだけ傾向モードになり、傾向モードの材料は README・指示書・依頼文だけから作る | 片方が閾値以上で傾向モードになったら誤り。傾向モードで履歴の出典が材料に入ったら誤り（AC3） |
| I10 | MVV 候補の抽出 | 候補は 2 案以上で、各案が 4 節を持ち、根拠が材料の出典の ID を指す。分かれる点が 1 つ以上ある | 満たさない応答は終了コード 1 で止まり、材料のパスを示す（AC4・E3） |
| I11 | ミッション状態ファイル | 承認済みのプロジェクト MVV があるときの `init --pace fast` は、ミッション MVV があればその照合（`mission`）が「従う」のときだけ状態を書き、プロジェクト MVV の参照（版・sha256）を残す。`new mission --pace fast` はその sha256 と現行の宣言が食い違えば断る | 「反する疑い」で状態が書かれたら誤り。改訂の後に fast が通ったら誤り（AC12・AC10・前提 6） |
| I12 | 宣言 | 閾値・パス・ブランチ名に ai-plugins の値を既定として持たない。閾値は汎用の既定と、引数と、宣言の `settings` で受ける | 既定に ai-plugins の値があれば誤り（AC14） |
| I13 | 判断の記録 | mvv-gate が同じプロジェクト MVV の sha256 のもとで「判定できない」を `unknown_streak` 回続けて返したら、結果に改訂の提案を載せる。ほかの判定か版の更新で数え直す | 回数に届いて提案が無い、または別の版をまたいで数えたら誤り（AC11） |
| I14 | MVV の照合 | 照合の結果は「従う」で進むか、止まって人へ戻すかだけで、利用者の本文・指示を書き換えも退けもしない | 照合が本文を書き換えたら誤り（前提 9） |
| I15 | 判断の記録 | 承認ゲートの PR が `.ndf/mvv.md` か `.ndf/mvv.json` を変えるとき、mvv-gate は LLM を呼ばずに利用者へ戻す | 宣言を変える PR が MVV 判定で通ったら誤り（R7。I1 を PR 経由で回り込ませない） |

### ドメインイベント

| # | イベント | 発生元 | 受け手 |
| --- | --- | --- | --- |
| E1 | 宣言の有無と新しさを確かめた | `project-mvv.py check`（`development-workflow` の手順 0） | conductor（2 と 4 なら策定か改訂の手順を案内する） |
| E2 | 材料を集めた | `project-mvv.py collect` | `propose` |
| E3 | 候補を書いた | `project-mvv.py propose` | conductor |
| E4 | 候補と分かれる点を利用者へ示した | conductor（`AskUserQuestion` ） | 利用者 |
| E5 | 利用者が答え、本文が固まり、承認した | 利用者 | `vet`・`approve` |
| E6 | 宣言を書いた | `project-mvv.py approve` | `check`・判断の地点 |
| E7 | 判断の地点がプロジェクト MVV を読んだ | `lib/project_mvv.load`（各地点） | 各地点の状態ファイル・jsonl |
| E8 | 判断の記録に根拠の項目を残した | 各地点（`lib/project_mvv.basis` で正規化） | 各地点の記録 |
| E9 | 改訂の契機が立った | `retrospective` の問い・mvv-gate の連続・利用者の指示 | conductor（改訂の手順） |
| E10 | 改訂案を示し、承認して版を上げた | `propose --current`・`vet --kind revision`・`approve --reason` | 宣言・`new mission --pace fast`（参照の食い違いで断る） |
| E11 | ミッション MVV がプロジェクト MVV と矛盾し、ミッションの開始が止まった | `mission-state.py init`（`vet --kind mission` ） | conductor |

### 用語

| 用語 | 意味 | 用語集への反映 |
| --- | --- | --- |
| MVV | Mission / Vision / Value。プロジェクト MVV とミッション MVV の 2 層があり、下位は上位の範囲で具体化する | 意味の変更 |
| MVV 判定 | 承認ゲートのエビデンスが上位の原則・プロジェクト MVV・ミッション MVV に従うかの判定。「従う」でレッドラインが無いときだけ承認ゲートを省き、記録を残す | 意味の変更 |
| MVV の照合 | 本文（候補・改訂案・ミッション MVV）が上位の原則とプロジェクト MVV に従うかを「従う / 反する疑い / 判定できない」の 3 択で判定すること。従う以外は人へ戻す | 追加 |
| MVV の節 | 判断の地点へ渡す塊。上位の原則を先頭に置き、承認済みのプロジェクト MVV の本文か「MVV なし」とその理由を続ける | 追加 |
| 根拠の項目 | 判断の記録に残す MVV の項目の番号（`Mission` / `Vision` / `Value 3` / `R2` ）。MVV が無ければ「MVV なし」、返されなければ「根拠なし」 | 追加 |

## 機能一覧

| # | 機能 | 誰が使うか |
| --- | --- | --- |
| F1 | プロジェクト MVV の有無と新しさを判定する | conductor（手順 0） |
| F2 | 候補の材料を集める（履歴モード / 傾向モード） | conductor |
| F3 | MVV 候補を書く（新規 / 改訂案） | conductor |
| F4 | 候補と分かれる点を示し、対話で本文を固める | conductor と利用者 |
| F5 | 本文を上位の原則とプロジェクト MVV に照らす | conductor・`mission-state.py` |
| F6 | 承認した本文を宣言として書く（版 1 / 改訂で版 N） | conductor（利用者の承認の後） |
| F7 | 過去の版の本文と版どうしの差分を取り出す | 利用者・`retrospective` |
| F8 | 判断の地点へ MVV の節を渡し、根拠の項目を記録する | mvv-gate・cross-review・cross-refactoring・supervise の judge・`out-of-scope` |
| F9 | 改訂の契機を知らせる | `retrospective`・mvv-gate |
| F10 | ミッション MVV がプロジェクト MVV と矛盾すればミッションの開始を止める | `mission-state.py init` |
| F11 | ai-plugins のプロジェクト MVV（版 1）を宣言する | conductor と利用者 |

## 構成要素

| 要素 | 新設 / 変更 | 責務 |
| --- | --- | --- |
| 上位の原則（`scripts/data/mvv-principle.md` ） | 新設 | 原則の 1 文・序列・「機械は照合と 3 択だけ、判定できなければ人へ」の規則。どのプロンプトも先頭にこの本文を置く |
| MVV の読み取りと節（`scripts/lib/project_mvv.py` ） | 新設 | 宣言の読み取り（例外を上げず `approved` / `none` / `mismatch` / `unreadable` を返す）・本文の解析（見出しと項目の番号）・MVV の節の組み立て・根拠の項目の正規化・状態への写し・宣言の型（`lib/schema.py` の `Shape` ）・ミッションの承認の照合（`approved_mvv` と `mvv_refusal` の 2 つを 1 つに寄せる） |
| MVV のエントリポイント（`scripts/project-mvv.py` ） | 新設 | `check` / `collect` / `propose` / `vet` / `approve` / `show` / `context` の 7 副命令。LLM を呼ぶのは `propose` と `vet` だけで、`supervise_lib/claude.py` の `call_claude` を Tool なしで呼ぶ |
| 宣言のスキーマ（`development-workflow/schemas/mvv.schema.json` ） | 新設 | `lib/project_mvv.py` の型から生成し、一致をテストが見る |
| 承認ゲートの判定（`scripts/mvv-gate.py` ） | 変更 | 材料の先頭へ MVV の節を置き、ミッション MVV を続ける。宣言の状態による機械の検査（I6・I15）。`mvv-gate.jsonl` に MVV の参照と根拠の項目。「判定できない」の連続で改訂を提案する |
| ミッション状態ファイル（`scripts/mission-state.py` ） | 変更 | `init` でプロジェクト MVV の参照を書き、ミッション MVV があれば `vet --kind mission` を通す（I11） |
| fast の許可（`supervise_lib/mission.py` ） | 変更 | 照合を `lib/project_mvv.py` の関数へ寄せる。ミッション MVV が無くても承認済みのプロジェクト MVV で許可し、参照の食い違いで断る |
| 指摘の基準（`scripts/lib/review_criteria.py` ） | 変更 | `reviewer_block` / `fixer_block` の後ろへ MVV の節を足し、`waiver_reply` に根拠の項目を足し、`as_state` に MVV の参照を足す |
| cross-review の初期化（`review_lib/commands/init.py` ） | 変更 | 宣言を 1 回だけ読み、状態ファイルの `review_criteria` へ MVV の参照と節を写す |
| 修正の振り分け（`fix/scripts/fix-steps.py` ） | 変更 | 振り分けの `mvv_basis` を正規化して結果と見送りの返信へ渡す。修正担当と最終スイープで共通 |
| cross-refactoring（`refactor_lib/commands/setup.py`・`launch-cli.sh`・`prompts/propose.md`・`prompts/plan.md`・`proposals.py`・`commands/plan.py`・`items.py`・`plan.py` ） | 変更 | 初期化で MVV の参照と節を状態へ写し、`RF_MVV` で提案と計画のプロンプトへ渡す。提案・計画の答えの `mvv_basis` を候補・項目・見送りへ運び、計画の見送りの表に根拠の列を足す |
| supervise の judge（`supervise_lib/steps.py`・`prompts.py`・`state.py` ） | 変更 | judge のプロンプトへ MVV の節を足し、出力に `basis` を足し、`log` へ根拠の項目を、`state.json` へ MVV の参照を書く |
| 手順書（`development-workflow` の `SKILL.md`・`references/project-mvv.md`・`references/conductor-entrypoints.md`・`references/pace.md` ） | 変更・新設 | 手順 0 に `check` を足す。策定と改訂の手順（対話・承認・書き込み）を `project-mvv.md` に置く。fast の許可にプロジェクト MVV を足す |
| `out-of-scope`・`retrospective` の `SKILL.md` | 変更 | 3 択の前に `project-mvv.py context` を読み、起票の本文に根拠の項目を書く。振り返りの観点に「MVV を見直すか」を足す |
| ai-plugins の宣言（`.ndf/mvv.md`・`.ndf/mvv.json`・`AGENTS.md` の方針） | 新設・変更 | 実装の中で、利用者の承認の後に `approve` で書く。`AGENTS.md` の方針に「判断の基準は `.ndf/mvv.md` 、序列は上位の原則が決める」の 1 段落を足す |
| 用語集（`docs/glossary/glossary.json`・`docs/glossary.md` ） | 変更 | 用語の表の 5 語を反映する（設計 PR で済ませる） |

```mermaid
graph TD
    subgraph 配布物
        P[上位の原則]
        L[MVV の読み取りと節]
        E[MVV のエントリポイント]
    end
    subgraph 宣言
        D[プロジェクト MVV の宣言]
    end
    subgraph 判断の地点
        G[承認ゲートの判定]
        R[cross-review の基準と振り分け]
        F[cross-refactoring の提案と計画]
        J[supervise の judge]
        O[範囲外と振り返りの手順]
    end
    M[ミッション状態ファイルと fast の許可]
    E -->|approve だけが書く| D
    E --> L
    L --> P
    L -->|読む| D
    G --> L
    R --> L
    F --> L
    J --> L
    O -->|context| E
    M --> L
    M -->|vet mission| E
```

### 配置

すべて利用者の端末（worktree）で動く。境界をまたぐのは 3 本である。

| 境界 | 流れるもの | 保護の条件 |
| --- | --- | --- |
| `collect` → `gh`（GitHub） | 課題の題名と本文の読み取り | 読み取りだけ。書き込みの副命令を呼ばない |
| `propose` / `vet` / mvv-gate / judge → `claude -p` | 上位の原則・材料・本文 | 材料は `project_lib/secret.py` で秘密を伏せてから渡す。Tool なし・設定を読まない最小構成 |
| `approve` → `.ndf/` | 本文と承認の記録 | 利用者の承認の後に conductor が打つ（レッドライン R7） |

### 置き場所

```text
plugins/ndf/
├── scripts/
│   ├── data/mvv-principle.md                 # 新設
│   ├── lib/project_mvv.py                    # 新設
│   ├── lib/review_criteria.py                # 変更
│   ├── project-mvv.py                        # 新設
│   ├── mvv-gate.py / mission-state.py        # 変更
│   ├── supervise_lib/{mission,steps,prompts,state}.py   # 変更
│   └── tests/test_project_mvv.py             # 新設（ほかは既存のテストへ足す）
└── skills/
    ├── development-workflow/
    │   ├── SKILL.md                          # 変更（手順 0）
    │   ├── schemas/mvv.schema.json           # 新設
    │   └── references/{project-mvv.md（新設）,conductor-entrypoints.md,pace.md}
    ├── cross-review/scripts/review_lib/commands/init.py   # 変更
    ├── fix/scripts/fix-steps.py              # 変更
    ├── cross-refactoring/{prompts,scripts}   # 変更
    └── out-of-scope/SKILL.md, retrospective/SKILL.md      # 変更
```

`project-mvv.py` は安定版の置き場（`scripts/` ）に置く。手順 0 と判断の地点という既定で動くものから呼ぶため、実験版には置けない
（`test_experimental.py` ）。

## 構造

```mermaid
classDiagram
    class ProjectMvv {
        +status
        +version
        +sha256
        +body
        +item_ids()
    }
    class MvvDecl {
        +settings
        +versions
    }
    class MvvVersion {
        +version
        +sha256
        +approved_at
        +approved_by
        +reason
        +vet
        +changes
        +body
    }
    MvvDecl "1" --> "1..*" MvvVersion
    ProjectMvv ..> MvvDecl : 読む
    Focus ..> ProjectMvv : 節を足す
```

| 型・関数 | 責務 |
| --- | --- |
| `ProjectMvv`（`lib/project_mvv.py` ） | 読み取りの結果。`status` が `approved` のときだけ `body` と `version` を持つ |
| `MvvDecl` / `MvvVersion` | `.ndf/mvv.json` の型。`lib/schema.py` の `Shape` を継ぐ |
| `load(root) -> ProjectMvv` | 例外を上げない。`mvv.json` が無ければ `none` 、形が違えば `unreadable` 、sha256 が最後の版と違えば `mismatch` |
| `block(mvv) -> str` | MVV の節。原則の文 → 本文（または「MVV なし（理由）」）→「判断の根拠に項目の番号を書く」の指示 |
| `record(mvv) -> dict` | 状態への写し `{status, version, sha256}` |
| `basis(raw, mvv) -> list[str]` | 根拠の項目の正規化（I7）。本文に無い番号は落とす |
| `approval_refusal(state, root) -> str \| None` | fast の許可の照合。ミッション MVV の 3 者の一致（今の 2 か所の実装）とプロジェクト MVV の参照の一致を見る |

既存の `Focus`（`review_criteria.py` ）は形を変えない。`reviewer_block(focus, mvv)` / `fixer_block(focus, mvv)` が第 2 引数を受け、
省くと今の出力と同じになる。

## データ構造

### 宣言

`.ndf/mvv.md` は本文だけを持つ Markdown で、人が読む。`.ndf/mvv.json` は版の履歴を持ち、機械が読む。

```json
{
  "version": 1,
  "body": "mvv.md",
  "settings": {"trend_commits": 100, "trend_issues": 20, "unknown_streak": 3},
  "versions": [
    {"version": 1, "sha256": "9f2c…", "approved_at": "2026-09-28T01:23:45Z", "approved_by": "takemi.ohama",
     "reason": "初版", "vet": {"verdict": "follow", "at": "2026-09-28T01:20:02Z", "accepted_unknown": false},
     "changes": [], "body": "## Mission\n…"}
  ]
}
```

| 項目 | 型 | 空を許すか | 意味 |
| --- | --- | --- | --- |
| `version` | int | 許さない | 宣言の形の版。1 |
| `body` | str | 許さない | 本文のファイル（`.ndf/` からの相対）。既定 `mvv.md` |
| `settings.trend_commits` / `trend_issues` | int | 許す | 傾向モードの閾値。省くと既定（100 / 20） |
| `settings.unknown_streak` | int | 許す | 改訂を提案する「判定できない」の連続回数。省くと既定（3） |
| `versions[]` | list | 許さない（1 件以上） | 承認の履歴。追記だけ（I3） |
| `versions[].sha256` | str | 許さない | その版の本文の sha256 |
| `versions[].approved_at` / `approved_by` | str | 許さない | 承認の日時（UTC）と承認者 |
| `versions[].reason` | str | 版 1 だけ許す | 改訂の理由。版 1 は「初版」を入れてよい |
| `versions[].vet` | object | 許さない | 承認の前の照合（判定・日時・「判定できない」を人が引き受けたか） |
| `versions[].changes` | list | 許さない（版 1 は空） | 前の版との差分。`{"item": "Value 3", "kind": "added" \| "changed" \| "removed"}` を項目の単位で並べる |
| `versions[].body` | str | 許さない | その版の本文の全文。`show --version N` がここから取り出す |

**時系列は履歴保持型で守る。** 改訂は新しい版を足し、過去の版を書き換えない。現行の本文は `mvv.md` と最後の版の `body` の 2 か所に
あり、sha256 の一致が「承認と一致する」の定義になる（I2）。

### 抽出の作業ファイル

`collect` と `propose` は、リポジトリの外の状態ディレクトリ（`~/.local/state/ndf/project-mvv/<リポジトリの根から決めた名前>/` 。
`--out-dir` で変える）へ書く。承認の前に `.ndf/` へ何も書かないためである（AC5）。

| ファイル | 主な項目 |
| --- | --- |
| `materials.json` | `mode`（`history` / `trend` ）・`counts`（`commits` / `issues` ）・`thresholds` ・`sources[]`（`id` = `S<番号>` ・`kind` = `commit` / `issue` / `doc` / `instructions` / `readme` / `request` ・`ref` = ファイルと行か課題番号かコミットの件名・`text` ）・`missing[]`（集められなかったものと理由）・`seconds` |
| `candidates.json` | `materials`（パス）・`kind`（`new` / `revision` ）・`candidates[]`（`id` ・`recommended` ・`mission` ・`vision` ・`values[]` と `redlines[]` の各行に `evidence`（`S<番号>` の並び））・`questions[]`（問いと選択肢ごとの影響） |
| `candidates.md` | `candidates.json` を `issues/mvv-candidates-ai-plugins.md` と同じ節の並びで書いたもの。利用者へ示す |

### 記録

| 記録 | 足す項目 |
| --- | --- |
| `~/.local/state/ndf/project-mvv.jsonl`（新設。`vet` の履歴） | `at` ・`repo` ・`kind` ・`sha256`（対象）・`project_sha256`（照らしたプロジェクト MVV。無ければ空）・`verdict`（`follow` / `suspect` / `unknown` / `unreadable` ）・`locations[]`（`item` と理由）・`cost_usd` ・`seconds` |
| `mvv-gate.jsonl`（既存） | `project_mvv`（`{status, version, sha256}` ）・`basis[]` |
| `mission.json`（既存） | `project_mvv`（`{version, sha256}` 。承認済みのときだけ）。`mvv` は、プロジェクト MVV があれば空を許す |
| cross-review の状態（既存） | `review_criteria.project_mvv`（参照）と `review_criteria.mvv_block` 。振り分けと結果の各指摘に `mvv_basis` |
| cross-refactoring の状態（既存） | `project_mvv`（参照と節）。`candidates[]` ・`items[]` ・`deferred_items[]` に `mvv_basis` |
| supervise の `state.json`（既存） | `project_mvv`（参照。実行の開始で 1 回）。judge の `log` の行に `basis` |

| 機能 | 宣言 | 作業ファイル | `project-mvv.jsonl` | 各地点の記録 | `mission.json` |
| --- | --- | --- | --- | --- | --- |
| F1 判定 | R | — | — | — | — |
| F2 材料 | R（設定） | C | — | — | — |
| F3 候補 | R（改訂のとき） | R・C | — | — | — |
| F5 照合 | R | — | C | — | — |
| F6 承認 | C・U | — | R | — | — |
| F7 取り出し | R | — | — | — | — |
| F8 配線 | R | — | — | U | — |
| F9 契機 | R（設定） | — | — | R | — |
| F10 開始の検査 | R | — | C | — | C |

書く相手が 2 つある機能は F10 だけで、照合の記録は `vet` が、ミッション状態ファイルは `mission-state.py` が書く。F6 は本文と履歴の 2 ファイルを書くが、どちらも同じ宣言の集約に属する。

## 入出力の契約

`project-mvv.py` の副命令は結果を `lib/step_result.py` の形（`tool` / `status` / `summary` / `items` / `metrics` ）で標準出力へ書く。
引数の誤りは 2 で終わる（`argparse` ）。

| 副命令 | 入力 | 出力 | 終了コード |
| --- | --- | --- | --- |
| `check [--root]` | 宣言 | 状態・版・次にすることの案内（`items` ） | 0 承認済みで一致 / 2 無い / 3 壊れている / 4 承認と一致しない。LLM・`gh` を呼ばず、書かない |
| `collect [--root] [--out-dir] [--request-file] [--trend-commits N] [--trend-issues N] [--budget 60]` | git・`gh`（読み取り）・ファイル | `materials.json` のパス・モード・件数・欠け | 0（欠けがあっても集められた材料で進む）/ 3 宣言の `settings` が壊れている |
| `propose --materials F [--current] [--reason-file F] [--out-dir]` | 材料・原則（`--current` なら現行の本文と改訂の理由） | `candidates.json` / `candidates.md` のパス | 0 / 1 LLM が返さない・形が I10 を満たさない（`items` に材料のパスと「利用者に直接問う」を示す） |
| `vet --body F --kind candidate\|revision\|mission [--root]` | 本文・原則・承認済みのプロジェクト MVV（`mission` と `revision` のとき） | 判定・箇所 | 0 従う / 10 反する疑い・判定できない・読めない |
| `approve --body F --by NAME [--reason TEXT] [--accept-unknown] [--root]` | 本文・`project-mvv.jsonl` の直近の照合 | 書いた版・sha256 | 0 書いた / 1 照合が無い・「反する疑い」・形が I4 を満たさない・版 2 以降で理由が無い・本文が現行と同じ |
| `show [--version N] [--diff M] [--root]` | 宣言 | 本文、または版 M から N への差分 | 0 / 2 その版が無い |
| `context [--root] [--format text\|json]` | 宣言・原則 | MVV の節（`json` なら参照も） | 0（宣言が無くても原則と「MVV なし」を出す） |

**既存の約束へ足すもの（どれも後方互換）。**

| 約束 | 変更 | 互換性 |
| --- | --- | --- |
| mvv-gate の LLM の出力 | `{"verdict","reasons","boundary"}` に `basis` を足す | 無ければ「根拠なし」として記録する |
| mvv-gate の機械の検査 | 承認と一致しない・壊れている宣言（I6）と、宣言を変える PR（I15）で 10 | 宣言の無いリポジトリは今と同じ |
| judge の出力 | `{"decision","reason"}` に `basis` を足す | 無ければ「根拠なし」 |
| 修正の振り分け（`fix-pr<N>-decisions.json` ） | 各指摘に `mvv_basis` を足す | 無ければ「根拠なし」。検査で止めない |
| cross-refactoring の提案と計画の答え | 各提案・各候補に `mvv_basis` を足す | 無ければ「根拠なし」 |
| `mission.json` | `project_mvv` を任意の項目として足す。fast の `mvv` は、承認済みのプロジェクト MVV の参照があれば空でよい | 今の状態ファイルは項目が無いまま読める |
| `waiver_reply(kind, names, basis)` | 返信の末尾に「根拠: Value 1（MVV 版 1）」か「（MVV なし）」を足す | 第 3 引数を省くと今の本文 |

## 処理の流れ

### 策定（F1〜F6）

```mermaid
sequenceDiagram
    participant C as conductor
    participant E as project-mvv.py
    participant L as claude -p
    participant U as 利用者
    C->>E: check
    E-->>C: 2 無い（何も書かない）
    C->>E: collect
    E-->>C: materials.json（欠けは missing へ）
    C->>E: propose
    E->>L: 原則 + 材料
    L-->>E: 候補
    alt 形が I10 を満たさない・返さない
        E-->>C: 1 と材料のパス
        C->>U: 材料を示して直接問う
    else 満たす
        E-->>C: candidates.md
        C->>U: AskUserQuestion（候補と分かれる点）
    end
    U-->>C: 答え（本文を固める。E4 へ戻ることがある）
    C->>E: vet --kind candidate
    E->>L: 原則 + 本文
    alt 反する疑い・判定できない
        E-->>C: 10 と箇所
        C->>U: 箇所を示して本文を直す（判定できないなら引き受けるかを問う）
    else 従う
        E-->>C: 0
    end
    C->>U: AskUserQuestion（本文の承認）
    U-->>C: 承認
    C->>E: approve --by
    E-->>C: 0 版 1（.ndf/mvv.md・mvv.json）
```

承認されなければ、conductor は E4 へ戻り、`approve` を打たない。`approve` が書けなければ（権限・ディスク）止まり、本文を利用者へ示す。

### 判断の地点（F8）

```mermaid
graph LR
    D[宣言] --> LD[load]
    LD --> BL[block: 原則 + 本文か MVV なし]
    LD --> RC[record: 状態・版・sha256]
    BL --> G[mvv-gate の材料の先頭]
    BL --> R[reviewer_block / fixer_block]
    BL --> F[RF_MVV: propose.md / plan.md]
    BL --> J[judge のプロンプト]
    RC --> S[各地点の状態・jsonl]
    RC --> MS[ミッション状態ファイルの init と fast の許可]
    G --> B[basis で正規化した根拠の項目]
    R --> B
    F --> B
    J --> B
    B --> S
```

`mission-state.py init` は参照を書く前に `project-mvv.py vet --kind mission` を呼ぶ（F10）。スキーマ・用語集・手順書・ai-plugins の宣言は呼び出しの順序を持たないため、どの図にも描かない。

`out-of-scope` は判断を LLM の会話の中で行うため、スクリプトの配線を持たない。Skill の手順で `project-mvv.py context` を読み、
起票の本文の「なぜこの変更の範囲外なのか」の節に根拠の項目と版を書く。それが渡した事実の記録になる（AC8）。

### 承認ゲートの判定（mvv-gate の順序）

```mermaid
graph TD
    A[開始] --> P{PR が宣言を変える}
    P -->|はい| X[10 利用者へ]
    P -->|いいえ| S{プロジェクト MVV の状態}
    S -->|mismatch / unreadable| X
    S -->|approved / none| M{ミッション MVV}
    M -->|承認と一致しない| X
    M -->|あり / 無くてプロジェクト MVV が approved| B[既存の機械の検査]
    M -->|無くて none| X
    B -->|外れる| X
    B -->|通る| L[LLM: 原則 → プロジェクト MVV → ミッション MVV → 関門 → 材料]
    L -->|従う・boundary 無し| OK[0 関門の記録]
    L -->|それ以外| X
    L --> K{同じ sha256 で判定できないが unknown_streak 回}
    K -->|はい| SUG[items に改訂の提案]
```

### 改訂（F3・F5・F6・F9・E9〜E10）

```mermaid
sequenceDiagram
    participant T as 契機
    participant C as conductor
    participant E as project-mvv.py
    participant U as 利用者
    T->>C: 振り返りの問い / mvv-gate の提案 / 利用者の指示
    C->>E: collect → propose --current --reason-file
    E-->>C: 改訂案（現行との差分つき）
    C->>U: AskUserQuestion（改訂案と分かれる点）
    U-->>C: 答え
    C->>E: vet --kind revision
    E-->>C: 0 従う（10 なら箇所を示して直す）
    C->>U: AskUserQuestion（改訂の承認）
    U-->>C: 承認
    C->>E: approve --reason
    E-->>C: 0 版 N+1（changes を計算して追記）
    Note over C,E: 承認済みミッションの fast は、次の new mission --pace fast と<br/>承認ゲートで参照の食い違いを検出し、利用者の承認へ戻る
```

承認されなければ現行の版のまま終わる。

### 宣言の状態

```mermaid
stateDiagram-v2
    [*] --> 無い
    無い --> 承認済み: approve（版 1）
    承認済み --> 承認済み: approve（版 N+1）
    承認済み --> 承認と一致しない: mvv.md を手で変える
    承認と一致しない --> 承認済み: 手で戻す / approve（版 N+1）
    承認済み --> 壊れている: mvv.json を手で壊す
    壊れている --> 承認済み: 手で直す
```

`approve` は「壊れている」から版を足さない（履歴を読めないため）。「無い」へ戻る遷移は描かない。宣言を消すのは利用者の手の操作で、
NDF の副命令は消さない。

## 非機能の実現方式

| 大項目 | 要求の条件 | 実現方式 | 確かめ方 |
| --- | --- | --- | --- |
| 性能・拡張性 | 材料集めは ai-plugins で 60 秒以内。候補の生成は最小構成の `claude -p` 1 回 | `collect` は `--budget`（既定 60 秒）の締め切りを持ち、git と `gh` の 1 回を `min(30, 残り)` 秒で打ち切る（`project_lib/measure_repo.py` の形）。課題は `gh_rest.issue_list` で 1 回に取り、本文は 1 件 2000 字で切る。`propose` は `call_claude` を Tool なしで 1 回呼ぶ | ai-plugins で `collect` の `seconds` を読む。`propose` の実行で `usage_ledger` の行が 1 つ増えることを見る |
| 運用・保守性 | 判定・承認・改訂は jsonl と git の履歴に残り、どの版で何を根拠に判断したかをたどれる | 版の履歴は `mvv.json` の `versions` （追記だけ）、照合は `project-mvv.jsonl` 、判断は各地点の記録に `{status, version, sha256}` と根拠の項目 | 判断の記録の版から `show --version N` で本文を取り出し、根拠の項目が本文にあることを見る |
| セキュリティ | レッドラインは MVV があっても承認ゲートを省かない | mvv-gate の既存の検査（`boundary_paths` ・`boundary` ）を残し、宣言を変える PR を常に利用者へ戻す（I15）。材料は `project_lib/secret.py` で秘密を伏せてから LLM へ渡す | 既存の `test_mvv_gate.py` と、宣言を変える PR の試験・秘密を含む材料の試験 |
