# NDF 12.0 の材料: 外部の方法論と競合の調査

- 調べた日: 2026-11-16（競合の版・スター数は同日に `gh api repos/<repo>` と `releases/latest` で取得）
- 書き分け: **【事実】** は出典つき。**【推測】** は調査者の見立てで、出典は無い
- NDF の側の事実はリポジトリ `/work/ai-plugins`（develop、`ccd656a33`）から読んだ

---

## 0. 結論

1. **「外部を参照していない」は半分だけ当たる。** 設計の雛形は既に C4（`plugins/ndf/skills/design/references/system-architecture.md:17`）・MADR（`design/references/decisions.md:62`）・DDD の語（`design-template.md` の「ドメインモデル」節）・arc42（`docs/specifications/ndf-detailed-design-stage.md:50`）を下敷きにしている。**足りないのは「外部の名前と形をそのまま採る」こと**で、内部の語で同じものを作り直している（例: 決定の記録 = ADR だが独立ファイルにならず設計文書に埋まる）
2. **競合の中心の形は 4 つに収束している（2026-11 時点）。** (a) 常設の文脈ファイル（AGENTS.md・steering・constitution）、(b) 仕様 → 計画 → タスクの 3 文書（Spec Kit・Kiro・Superpowers・BMAD）、(c) 実装者と別のコンテキストの評価者（Anthropic の evaluator・Superpowers の 2 段レビュー・Spec Kit の converge）、(d) git の worktree と1 タスク 1 サブエージェント。NDF はこの 4 つをすべて持つが、**名前とファイルの形が独自**で、利用者が既知の知識を持ち込めない
3. **NDF だけが持つのは「運用の自動化」側である。** 5 モードの振り分け、MVV による承認ゲートの代行判定、レーンの並列とオーガナイザー、耐久ワークフロー（DBOS）での再開、複数ベンダーの AI による収束レビュー、リリースと確定仕様化と振り返りまでの一貫、費用とリードタイムの計測。競合はほぼ「1 機能を作る」までで止まる
4. **NDF に無く競合にあるのは「仕様の検証の機械化」と「工程の効き目の評価」である。** Kiro の EARS → 正しさの性質 → property-based test、Spec Kit の analyze（文書間の整合検査）、Anthropic の「部品を 1 つずつ外して効いているかを測る」評価。NDF は費用と速さを測るが、**工程ごとに外したら品質がどう変わるか（ablation）を測っていない**
5. **12.0 の推奨の軸:** 文書の形は外部の標準（AGENTS.md・ADR/MADR・arc42 の節・EARS・Diátaxis）へ寄せて自作の雛形を減らす。検査は既存ツール（import-linter、Hypothesis、mutmut 系、DORA の指標）へ置き換える。自作を残すのは強み（並列・承認の代行・耐久実行・多 AI 収束）だけにし、その効き目を ablation 評価で示す

---

## 1. 設計と文書の方法論

| 方法論 | 何を決めてくれるか | NDF との重なり | NDF に足りないもの |
| --- | --- | --- | --- |
| **arc42**（12 節） | アーキテクチャ文書の節の一覧と順序: 目標と品質目標・制約・文脈と範囲・解決の戦略・構成要素の視点・実行時の視点・配置の視点・横断的な概念・決定・品質要求（品質の木とシナリオ）・リスクと技術的負債・用語集 【事実】[arc42 overview](https://arc42.org/overview) | 目的・適用範囲・構成要素・構造・処理の流れ・非機能の実現方式・決定の記録・用語は `design-template.md` にある | **品質シナリオ（刺激・応答・測定）、配置の視点、リスクと技術的負債の節が無い。** NDF の設計は課題ごとで、システム全体の常設文書（arc42 はこちら）を持たない 【事実: `design-template.md` の見出し一覧】 |
| **C4 model** | 図の 4 つの拡大率（System Context / Container / Component / Code）と記法 【事実】[c4model.com](https://c4model.com/) | `system-architecture.md` が「おおむね対応」と明記 | 名前を借りているが、図の水準の語は独自のまま。C4 の語で書けば読み手の既知で通じる【推測】 |
| **ADR / MADR 4.0.0**（2024-09-17） | 1 決定 1 ファイル（`docs/decisions/NNNN-title.md`）、節: 文脈と問題・決定の要因・検討した案・決定と帰結・確認・各案の得失、状態（proposed / accepted / deprecated / superseded） 【事実】[MADR](https://adr.github.io/madr/) | 「決定の記録」の節（結論・理由・採らなかった案、`作り方:`・`比べた候補:`）は MADR の縮約。`decisions.md:62` が ADR・MADR を参照 | **決定が課題の設計文書に埋まり、状態（superseded）と横断の索引が無い。** 後から「なぜこうなっているか」を引くと、確定仕様 177 本（`docs/specifications/`）を探すことになる 【事実: 件数は `ls` で数えた】 |
| **Diátaxis** | 文書を 4 種（tutorial / how-to / reference / explanation）に分け、混ぜない 【事実】[diataxis.fr](https://diataxis.fr/) | NDF は「AGENTS.md = ナビ、docs/ = 知識、skills/ = 手順」の 3 分（CLAUDE.md の「知識アーキテクチャ」） | リポジトリ内で言及 0 件 【事実: `git grep -i diataxis`】。Skill 本文が how-to と explanation（理由の段落）と reference（表）を 1 ファイルに混ぜ、SKILL.md が長くなる一因【推測】 |
| **docs-as-code** | 文書をコードと同じリポジトリ・レビュー・CI で扱う 【事実】[Write the Docs](https://www.writethedocs.org/guide/docs-as-code/) | 既に徹底している（`instructions-check.py`・`check-skill-frontmatter.py`・`ndf check --write-spec` による生成） | 無し。NDF の強い側 |
| **RFC・設計 doc の文化**（Google design doc、Rust RFC など） | 書いて回覧し、代替案と非目標を明示する 【事実】[Rust RFC process](https://github.com/rust-lang/rfcs) | 設計承認ゲート、「採らなかった案」「含まない・採らない」 | 非目標（Non-goals）は「含まない」で代替済み。差は小さい |
| **DDD（境界づけられたコンテキスト）** | ユビキタス言語、コンテキストの境界と対応（context map）、集約と不変条件 【事実】[Fowler: BoundedContext](https://martinfowler.com/bliki/BoundedContext.html) | `design-template.md` の「ドメインモデル」節（コンテキスト・集約・不変条件・ドメインイベント・用語）、用語集の宣言 `glossary` | context map（コンテキスト間の関係の型: 共有カーネル・腐敗防止層など）が無い。NDF 自身のコード（ndf-next の `domains/`）に境界の検査が無い |
| **ヘキサゴナル / クリーンアーキテクチャ** | 依存の向き（ドメイン ← アダプタ）、ポートとアダプタ 【事実】[Cockburn: Hexagonal](https://alistair.cockburn.us/hexagonal-architecture/) | ndf-next は `src/ndf/adapters/`・`domains/` に分かれている 【事実: `plugins/ndf-next/src/ndf/adapters/github.py`】 | **依存の向きを検査するもの（import-linter の contract）が無い。** import-linter は `docs/specifications/ndf-scripts-import-cycles.md` で 1 回言及されるだけ |

**詳細設計の雛形（`detail-template.md`）について:** 関数ごとの事前条件・事後条件・副作用・異常系の表は、契約による設計（Design by Contract）と FMEA の形に近く、外部にそのまま採れる単一の標準は無い。【推測】ここは自作で妥当だが、関数の粒度の表を人手（AI）で書くより、型（型ヒントと pyright）・docstring・テストで持たせるほうが、コードとずれない。Spec Kit も Kiro も関数の粒度の文書は持たない（`data-model.md`・`contracts/` 止まり）【事実: 下の 3 節の出典】。

---

## 2. 開発プロセスと品質

| 方法・ツール | 何を決めてくれるか | NDF の現状 | 採る価値 |
| --- | --- | --- | --- |
| **Trunk-based development** | 短命のブランチ（1 日以内）を主幹へ頻繁に統合し、未完成はフィーチャーフラグで隠す 【事実】[trunkbaseddevelopment.com](https://trunkbaseddevelopment.com/) | `develop`・`main`・`sprint/<名前>`・課題ごとの worktree の 3〜4 層。ai-plugins 自身は 11.0.0 から GitHub flow へ移行済み（利用者のメモリ） | **高い。** スプリントブランチの層を外すと、マージの往復・宛先のチェック（`check-pr-base.sh`）・release ブランチの手順が要らなくなる【推測】。DORA の研究は trunk-based を高い成果の要因に挙げる 【事実】[DORA: Trunk-based development](https://dora.dev/capabilities/trunk-based-development/) |
| **Shape Up** | 「appetite（かける時間の上限）」を先に決め、shaping → betting → 6 週の cycle、期限で切る（circuit breaker） 【事実】[Shape Up](https://basecamp.com/shapeup) | 見積の Skill（`estimate`）、スプリントは「1 版を 1〜2 日」 | 中。**appetite（上限を先に決めて範囲を削る）** は AI の暴走（収束しない収束ループ）への歯止めとして借りられる【推測】 |
| **DORA の指標** | 変更のリードタイム・デプロイ頻度・変更失敗率・復旧時間（＋2024 から rework rate）。2025 年版は AI 前提の「AI Capabilities Model」（7 つの能力） 【事実】[2025 DORA report](https://cloud.google.com/resources/content/2025-dora-ai-assisted-software-development-report)、[dora.dev](https://dora.dev/guides/dora-metrics-four-keys/) | `docs/metrics/ndf-cost-and-lead-time/` でリードタイム・CI・修正の連鎖（`fix_commit_chains.csv`）を週次で集計している 【事実】 | **高い。** 既に近いものを測っているので、**名前と定義を DORA に合わせる**だけで外部と比べられる |
| **SPACE** | 生産性を 5 軸（満足・成果・活動・協働・効率）で見る、単一指標を避ける 【事実】[ACM Queue: SPACE](https://queue.acm.org/detail.cfm?id=3454124) | 人の満足の軸は無い（人は承認者だけ） | 低〜中。承認者の待ち時間・中断回数を「効率」軸として足す程度【推測】 |
| **TDD・ATDD・BDD（Gherkin）** | 受け入れ条件を Given/When/Then で書き、そのまま実行可能なテストにする 【事実】[Cucumber: Gherkin](https://cucumber.io/docs/gherkin/reference/) | `tdd-cycle` は単体の TDD。受け入れ条件は自由文（「観測可能・一意・テスト可能」の性質だけを課す、`requirements-design/SKILL.md:146`）。Gherkin は `docs/specifications/ndf-issue-items.md` で 1 回言及 | **中〜高。** 受け入れ条件の文型を **EARS か Given/When/Then** に固定すれば、検査（文型の lint）とテストへの写像が機械化できる |
| **EARS**（要求の文型） | `WHEN <事象> THE SYSTEM SHALL <応答>` などの 5 型 【事実】[Kiro Feature Specs](https://kiro.dev/docs/specs/feature-specs/)、原典 Mavin ら（Rolls-Royce, 2009） | 使っていない（`git grep -w EARS` 0 件） | **高い。** Kiro はこれを property 抽出の入力にしている（下の 3 節） |
| **Property-based testing** | 例ではなく性質（不変条件・往復）を書き、入力を生成して反例を縮める 【事実】[Hypothesis](https://hypothesis.readthedocs.io/) | ndf-next のテスト 1 ファイルだけが hypothesis を使う 【事実: `plugins/ndf-next/tests/domains/project/test_project_measure.py`】。設計の「不変条件」の節はあるが、テストへ写す決まりが無い | **高い。** 設計の「不変条件」→ Hypothesis の性質、を工程の決まりにする |
| **Mutation testing** | テストを壊して落ちるかを測る（テストの強さ）。Meta の ACH は LLM で少数の的を絞った変異を作り、生き残った変異を殺すテストを LLM で書く（7 プラットフォームで受け入れ率 73%） 【事実】[arXiv 2501.12862](https://arxiv.org/abs/2501.12862) | `design-template.md` の「テスト設計」に「どう壊したら落ちるべきか」の列がある（＝人手の変異の発想）。ツールは無い（mutmut 0 件） | **高い。** 「どう壊したら落ちるべきか」の列を mutmut / cosmic-ray の実測に置き換えれば、AI が書いたテストの空振りを機械で見つけられる |
| **Fitness functions**（進化的アーキテクチャ） | アーキテクチャの性質を自動テストとして CI で守る 【事実】[Building Evolutionary Architectures](https://evolutionaryarchitecture.com/) | NDF は「決まりはスクリプトで固定する」方針で実質的に多数持つ（`instructions-check.py`・`check-repo-secrets.py`・`test_experimental.py` など） | 中。**考え方は既に実践している。** 名前を借りて一覧化すると、どの決まりが機械で守られているかが見える |
| **アーキテクチャ規則の検査**（ArchUnit / import-linter） | レイヤの依存の向き・独立性・禁止 import を宣言で検査 【事実】[import-linter](https://import-linter.readthedocs.io/)（`seddonym/import-linter`、2026-11-13 に更新、スター 1,262） | ndf-next の `domains`/`adapters` の向きは検査されていない | **高い。** 自作の検査（`ndf-scripts-import-cycles.md` の仕組み）を置き換えられるか比べる価値がある |

---

## 3. AI エージェントの開発フレームワークの競合

### 3.1 一覧（確かめた日 2026-11-16）

| 名前 | 版（日付） | 仕様を何で書くか | 工程の数 | 人の承認 | 並列 | 状態の持ち方 | 評価 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **GitHub Spec Kit** | v1.4.0（2026-11-10）、★149,939 | `constitution` ＋ 機能ごとの Markdown（spec・plan・tasks ほか） | constitution → specify → (clarify) → plan → tasks → (analyze) → implement → converge | 各工程の結果を人が見てから次へ | ドキュメント上は明示なし（取得したページの範囲） | `.specify/` の Markdown | converge を `Converged` まで繰り返す。bug 拡張は `verified/partial/failed` |
| **Kiro** | IDE / CLI（GA 済み） | `requirements.md`（**EARS**）・`design.md`・`tasks.md`、steering（product / tech / structure） | 要求 → 設計 → タスク（設計先行も可、Quick Spec はゲート無し） | 工程の間に承認（Quick Spec で省略） | — | `.kiro/specs/`・`.kiro/steering/` | **EARS → 正しさの性質 → PBT**（Hypothesis / fast-check）。要求・タスク・テストを紐づけ |
| **Superpowers**（obra） | v7.0.2（2026-11-03）、★318,080 | brainstorming の設計文書 → 2〜5 分の粒度の計画 | brainstorm → worktree → write-plan → 実行（タスクごとにサブエージェント） → TDD → レビュー → ブランチの仕上げ | 設計の承認・実行開始の「go」・仕上げの選択 | タスクごとに新しいサブエージェント、`dispatching-parallel-agents` | git と計画ファイル | タスクごとに 2 段のレビュー（仕様の適合 → コード品質）、重大は止める |
| **BMAD-METHOD** | v6.12.4（2026-10-14）、v7 は alpha、★55,402 | brief・PRD・アーキテクチャ・epic・story | 規模で変わる（小は直接 build、大は深い計画）。Clarify → Plan → Build/verify → Learn | 主要な判断を明示し人が持つ（具体のゲートは README に無い） | — | 決定を持続する文脈として保存、`bmad status` | — |
| **Claude Code 公式 feature-dev** | `anthropics/claude-plugins-official` | 会話と TODO | 7 工程（発見 → 探索 → 質問 → 設計 → 実装 → 品質レビュー → 要約） | 5 箇所で人の入力（要約の確認・質問・設計案の選択・実装前の承認・指摘の扱い） | 探索 2〜3・設計 2〜3・レビュー 3 のサブエージェントを並列 | 会話内 | レビュー担当 3 体の統合 |
| **Task Master** | task-master-ai@0.43.1（2026-03-31）、更新は 2026-04 で止まり気味 | PRD → タスク（依存つき） | parse-prd → 複雑さ分析 → 展開 → 実行 | 人がタスクを選ぶ | タグ（workstream） | `.taskmaster/` | — |
| **Codex（OpenAI 公式の進め方）** | — | 短い AGENTS.md ＋ **ExecPlan（PLANS.md）** | Plan mode → 実装 → `/review` | プランを人が見る | worktree・サブエージェント・定期実行 | ExecPlan の「Progress / Surprises & Discoveries / Decision Log / Outcomes & Retrospective」 | 「done when」と観測できる受け入れ |
| **AGENTS.md** | Linux Foundation の Agentic AI Foundation が管理、6 万以上のリポジトリ | 自由な Markdown、近いファイルが勝つ | — | — | — | — | — |
| **Cursor** | — | `.cursor/rules/*.mdc`、BugBot は `BUGBOT.md`（rules を読まない） | — | PR で人がレビュー | クラウドの background agents | — | BugBot が反応から学ぶ |
| **Devin** | — | Knowledge（常設）＋ Playbooks（繰り返す作業の手順） | — | PR と Devin Review | 並列セッション（2026-02） | セッション | Devin Review（2026-01-22） |
| **OpenHands** | v1.40.0（2026-11-12）、★93,531 | — | — | — | SDK | イベントの記録 | **OpenHands Index**（2026-01-29 開始、費用と時間も公表） |
| **SWE-agent / Aider** | SWE-agent v1.1.0（2025-05）、Aider v0.86.0（2025-08） | — | — | — | — | — | SWE-agent は ACI（エージェント向けのツール設計）の出典 |

出典:
- Spec Kit: [github/spec-kit](https://github.com/github/spec-kit)、[Microsoft の解説（2025-09-15）](https://developer.microsoft.com/blog/spec-driven-development-spec-kit/)
- Kiro: [Feature Specs](https://kiro.dev/docs/specs/feature-specs/)、[Steering](https://kiro.dev/docs/steering/)、[Hooks](https://kiro.dev/docs/hooks/)、[Correctness（PBT）](https://kiro.dev/docs/specs/correctness/)、[PBT のブログ](https://kiro.dev/blog/property-based-testing/)
- Superpowers: [obra/superpowers](https://github.com/obra/superpowers)
- BMAD: [bmad-code-org/BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD)
- feature-dev: [README](https://raw.githubusercontent.com/anthropics/claude-plugins-official/main/plugins/feature-dev/README.md)、[claude-plugins-official](https://github.com/anthropics/claude-plugins-official)
- Task Master: [eyaltoledano/claude-task-master](https://github.com/eyaltoledano/claude-task-master)
- Codex: [Best practices](https://learn.chatgpt.com/guides/best-practices)（旧 developers.openai.com/codex/learn/best-practices から 308 で移転）、[ExecPlans（PLANS.md）](https://developers.openai.com/cookbook/articles/codex_exec_plans)
- AGENTS.md: [agents.md](https://agents.md/)
- Cursor: [BugBot rules](https://docs.cursor.com/bugbot/rules)、他は第三者の記事のみ（[spotdev 2026-10-13](https://www.spotdev.co.uk/blog/cursor-rules-skills-subagents-hooks)）
- Devin: [How Cognition Uses Devin to Build Devin](https://cognition.com/blog/how-cognition-uses-devin-to-build-devin)、[SD Times](https://sdtimes.com/ai/devin-gets-a-slew-of-new-features-to-help-with-onboarding-it-into-dev-environments/)
- OpenHands: [Introducing the OpenHands Index](https://openhands.dev/blog/introducing-the-openhands-index)
- 版・スター数: `gh api repos/<repo>` と `gh api repos/<repo>/releases/latest`（2026-11-16 取得）
- SDD の分類: [Böckeler: Understanding Spec-Driven-Development（2025-10）](https://www.martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)（spec-first / spec-anchored / spec-as-source の 3 水準）

### 3.2 Anthropic の記事が言っていること

- **Building effective agents（2024-12-19）** 【事実】[出典](https://www.anthropic.com/engineering/building-effective-agents): 5 つの型（prompt chaining・routing・parallelization・orchestrator-workers・evaluator-optimizer）。**「最も単純な解から始め、効くと示せたときだけ複雑さを足す」**。フレームワークは下の層を隠し、デバッグを難しくする。ツール（ACI）の説明とポカヨケに投資する
- **Effective harnesses for long-running agents（2025-11-26）** 【事実】[出典](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents): 初期化エージェントが `init.sh`・進捗ファイル・機能一覧（**JSON。Markdown よりモデルが上書きしにくい**、すべて failing から始め `passes` だけを書き換えてよい）を作る。以後のセッションは 1 機能ずつ、始めに進捗と git log を読み、E2E を 1 回通してから着手
- **Harness design for long-running application development（2026-03-24）** 【事実】[出典](https://www.anthropic.com/engineering/harness-design-long-running-apps): planner / generator / evaluator。**sprint contract**（着手前に「何を作り、どう確かめるか」を実装者と評価者が合意）。**自己評価は甘い**ので別の評価者を立て、評価者の prompt を人の判断に合わせて数回調整した。**モデルが上がるたびに部品を 1 つずつ外して効いているかを確かめ**、Opus 4.5 で context reset を、Opus 4.6 で sprint 構造を外した（評価者と planner は残した）。費用: 単独 20 分 $9 に対しハーネス 6 時間 $200
- **Demystifying evals for AI agents（2026-01 頃。日付は二次情報で 2026-01-09 と 2026-03-06 が食い違う）** 【事実（本文は二次情報）】[出典](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents): 採点はコード・モデル・人の組み合わせ。能力の eval と回帰の eval を分ける。非決定性には pass@k と pass^k（毎回成功）を分ける

### 3.3 NDF との対応

| 競合の考え方 | NDF で対応するもの | 差 |
| --- | --- | --- |
| 常設の文脈（AGENTS.md・steering・constitution・Knowledge） | AGENTS.md・CLAUDE.md・プロジェクト MVV（`.ndf/` の宣言 `mvv`）・`ndf-policies` | NDF は AGENTS.md を既に採用。**constitution ≒ MVV ＋ レッドライン**。Kiro の inclusion モード（fileMatch・auto）に当たるものは Skill の description 頼み |
| 仕様 → 計画 → タスク | requirements-design → design → detail → implementation-plan → tdd-cycle | **NDF は文書が 1 段多い（設計と詳細設計）。** Spec Kit・Kiro は設計を 1 文書で持つ |
| 文書間の整合検査（Spec Kit analyze） | `design-survey.py check`（節の有無と規則） | NDF は形の検査が中心で、**要求 ↔ 設計 ↔ タスク ↔ テストの紐づけ（Kiro のトレーサビリティ）を機械で検査していない**【推測: 検査の規則の網羅は未確認】 |
| 別のコンテキストの評価者 | cross-review（複数ベンダーの AI で収束まで）、quality-gates | **NDF の方が強い**（ベンダーを分けるので同じ癖を共有しにくい）。ただし評価者の採点基準を人の判断で校正する工程（Anthropic の手法）は無い |
| sprint contract（着手前の合意） | 受け入れ条件 ＋ 設計の「テスト設計」 | 近い。NDF は人と合意し、Anthropic は AI 同士で合意する |
| 1 タスク 1 サブエージェント＋ worktree | supervisor → worker、`.worktrees/` | 同じ |
| 進捗を JSON で持つ | DBOS ＋ JSON の台帳、結果 JSON（`step_result.py`） | **NDF の方が重い。** Anthropic の例はファイル 2 つと git で足りている |
| 規模で工程を変える（BMAD・Kiro Quick Spec） | 5 モード ＋ pace（normal / fast / auto） | 同じ考え。NDF の方が判定の基準が明文化されている |

---

## 4. 評価

### 4.1 外部の評価の作り方

- **SWE-bench 系** 【事実】: 実リポジトリの issue と、修正で通るテスト（FAIL_TO_PASS / PASS_TO_PASS）で採点。[SWE-bench](https://www.swebench.com/)。**OpenAI は 2026-02-23 に Verified の報告をやめ、汚染（学習データへの混入）を理由に SWE-bench Pro を勧めた**（本文は 403 で取得できず、日付と主旨は検索結果の要約と二次情報。[出典](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)）。Pro も採点の誤りの指摘がある（2026-05、単一の二次情報で未確認）
- **OpenHands Index**（2026-01-29〜） 【事実】: issue 解決・新規開発・フロントエンドなど複数の種類。**費用と所要時間も一緒に出す**、軌跡を公開 [出典](https://openhands.dev/blog/introducing-the-openhands-index)
- **METR の開発者の生産性の RCT** 【事実（二次情報）】: 2025 年は「AI ありで 19% 遅く、本人は 20% 速いと感じた」。2026-02 の追試は改善の傾向だが METR 自身が「とても弱い証拠」とし、実験の設計を変えた（自己選択の偏り）[二次情報](https://blog.robbowley.net/2026/04/04/metrs-developer-productivity-research-2026-update/)。**感じ方と実測が食い違うので、測らずに工程の効き目を主張できない**
- **Anthropic の eval** 【事実】: 能力と回帰を分ける、pass^k、採点者の組み合わせ、軌跡（trace）を読む

### 4.2 エージェントの工程の効き目を測る方法（NDF への当てはめ）【推測】

1. **固定の課題集を作る**: NDF 自身の過去の課題から、受け入れ条件とテストが揃ったものを 20〜50 件、コミットを巻き戻して再現できる形で凍結（SWE-bench の作り方。自リポジトリなので汚染は工程の比較では問題にならない）
2. **ablation**: 工程を 1 つずつ外した構成（例: 詳細設計なし・cross-review を 1 AI・設計なし）で同じ課題集を流し、**合格率（隠したテスト）・pass^3・費用・時間・後続の修正コミット数**を比べる。Anthropic が部品を外して確かめたのと同じ手順
3. **本番の観測**: 既にある `fix_commit_chains.csv`・`lead_time_monthly.csv` を DORA の変更失敗率・リードタイムの定義へ揃え、版ごとの前後で比べる
4. NDF は「時間で区切った負荷試験を流さない」方針（メモリ）があるので、評価は回数で決まる課題集の再実行として CI の外（定期実行）に置く

---

## 5. 分析

### 5.1 NDF が自作しているが、外部に答えがあるもの

| NDF の自作 | 外部の答え | 扱い |
| --- | --- | --- |
| 決定の記録（設計文書の節） | ADR / MADR 4.0.0 | **形を借りる → そのまま採る。** 1 決定 1 ファイル、状態と superseded の連鎖、索引。確定仕様の一部は ADR の束に置き換えられる【推測】 |
| 設計の雛形の節（目的・範囲・構成要素・構造・非機能・用語） | arc42 の 12 節 ＋ C4 | **形を借りる。** 常設のアーキテクチャ文書は arc42、課題ごとの設計は差分だけ。品質シナリオ・リスク・配置を足す |
| 受け入れ条件の自由文 ＋ 性質の規則 | EARS / Gherkin | **そのまま採る。** 文型を固定すると検査とテスト写像が機械化できる |
| テスト設計の「どう壊したら落ちるべきか」 | mutation testing（mutmut 等）、Meta ACH | **ツールで置き換える。** |
| 設計の「不変条件」 | property-based testing（Hypothesis、既に依存にある） | **工程の決まりにする。** Kiro と同じ流れ |
| 文書の種類の分け方（docs/ と skills/） | Diátaxis | **形を借りる。** Skill の本文（how-to）と理由（explanation）と表（reference）を分ける |
| 決まりを固定するスクリプト群 | fitness functions、import-linter | **名前を借りて一覧化、依存の検査は import-linter に置き換えを検討** |
| 計測（費用・リードタイム・CI） | DORA の 4（5）指標、SPACE | **定義を合わせる。** |
| プロジェクトの常設の文脈（MVV・宣言・ポリシー） | AGENTS.md（既採用）、Spec Kit の constitution、Kiro の steering の inclusion モード | MVV は残し、**読み込みの条件（パス一致で読む）** は Kiro の形を借りる余地 |
| 進捗の台帳（JSON ＋ DBOS） | Anthropic の進捗ファイル ＋ JSON の機能一覧 ＋ git、Codex の ExecPlan の Progress / Decision Log | 耐久実行は強みなので残す。**ただし台帳の項目は ExecPlan の 4 節（進捗・発見・決定・結果と振り返り）の形を借りると、人と AI の両方が読める**【推測】 |
| ブランチの層（develop / main / sprint / 課題） | trunk-based development | **層を減らす。** ai-plugins 自身は既に GitHub flow へ移った |
| エージェントの型（conductor → supervisor → worker） | Anthropic の orchestrator-workers / evaluator-optimizer | 用語は自作で良いが、**外部の型名との対応表**を用語集へ置くと新しい読み手に通じる |

### 5.2 NDF だけの強み（競合に無い）

- **モードの振り分けの明文化**（5 モード、上が勝つ規則、`operation` と `documentation` という開発以外のモード）。BMAD・Kiro は「規模で変える」と言うだけで基準が無い
- **承認ゲートの代行判定（MVV ＋ レッドライン ＋ ハッシュの一致）**。競合の承認は「人が見て次へ」だけで、人を外す条件を形式化したものは見当たらない
- **複数ベンダーの AI による収束レビュー**（cross-review・cross-refactoring、claude / codex / kiro）。競合のレビューは単一ベンダー内のサブエージェント
- **複数レーンの並列と問いの集約（オーガナイザー、リレーハブ）**。競合の並列はタスクかセッションの単位まで
- **耐久ワークフロー（DBOS）による再開**。Anthropic の例も Spec Kit もファイルと git だけ
- **PR の後（確定仕様化・リリース・リリース後の検証・振り返り）まで工程に含む**。競合はほぼ「機能を作ってブランチを仕上げる」まで
- **費用とリードタイムの実測の蓄積**（`docs/metrics/`）。OpenHands Index と同じ「費用も一緒に出す」思想を自前の運用で持つ
- **公開・内部を分けた契約（`plugins/ndf-next/docs/contracts/`、JSON Schema から生成）**。外部の連携システムから呼べる

### 5.3 競合にあって NDF に無いもの

- **EARS → 性質 → PBT のトレーサビリティ**（Kiro）
- **文書間の整合の検査コマンド**（Spec Kit `analyze`）と **拡張・プリセットの仕組み**（Spec Kit の extension / preset / workflow / catalog。組織の規則をプリセットで差し替える）
- **ゲートを外す軽い経路が利用者の選択として見える**（Kiro Quick Spec。NDF の `pace` が近いが、モードの判定と別の軸で複雑）
- **工程の ablation 評価**（Anthropic）。NDF は工程を足す方向の記録はあるが、外して確かめた記録は見当たらない【推測: `docs/metrics/` の範囲で】
- **評価者（レビュー AI）の採点の校正**（人の判断との一致率を測って prompt を直す）
- **普及と既知性**: Superpowers ★318k・Spec Kit ★150k。利用者がその語彙を既に知っている
- **少ない部品で始められること**: feature-dev は 3 エージェント・7 工程、Anthropic の例はファイル 2 つ。NDF は Skill 49 個（`manifests/claude-skills.txt`）、文書 3.5 万行（`plugins/ndf` の .md）、Python 約 40 万行（`git ls-files` の .py、テスト込み）【事実: `wc` で数えた】

---

## 6. 12.0 への推奨（優先順位つき）

| 順 | 推奨 | 理由 | 自作を減らす量【推測】 |
| --- | --- | --- | --- |
| 1 | **工程の ablation 評価の仕組みを最初に作る**（過去課題の凍結集 ＋ 合格率・pass^k・費用・時間） | 0 から作り直すなら、どの工程を残すかを測ってから決める必要がある。Anthropic は部品を外して確かめ、METR は感じ方と実測が逆になると示した。測らずに作り直すと同じ「独自に作りすぎ」を繰り返す | 工程そのものの削減の根拠になる |
| 2 | **文書の形を外部の標準へ: AGENTS.md（既採用）・ADR/MADR（決定）・arc42 ＋ C4（常設のアーキテクチャ文書）・Diátaxis（Skill と docs の分け方）** | 読み手（人と AI）が既に知っている形になる。雛形と検査の自作を減らせる。MADR 4.0.0 と arc42 は安定していて陳腐化しにくい | 設計の雛形の大半、確定仕様の一部 |
| 3 | **受け入れ条件を EARS（または Given/When/Then）に固定し、不変条件 → Hypothesis の性質、テストの強さ → mutation testing** | Kiro が示す「仕様と実装の一致を機械で確かめる」流れ。AI の自己評価の甘さ（Anthropic）を人のレビューでなく機械で補える | 「テスト設計」の表の人手の記述 |
| 4 | **アーキテクチャの決まりは import-linter（fitness functions）で** | ndf-next の `domains`/`adapters` の向きを宣言で守れる。自作の import 検査を減らせる | 検査スクリプトの一部 |
| 5 | **ブランチを trunk-based（GitHub flow）に寄せ、スプリントブランチの層を既定から外す** | DORA が成果と結びつける実践。宛先の検査・リリースブランチの手順が要らなくなる。ai-plugins 自身が既に移行している | ブランチ運用の手順と検査 |
| 6 | **計測の定義を DORA（＋ SPACE の効率の軸）へ揃える** | 既にある集計を外部と比べられる形にするだけ | 指標の定義 |
| 7 | **Spec Kit の構造（constitution / spec / plan / tasks と extension・preset）を、NDF の外側の互換層として検討** | Spec Kit は Claude Code・Codex も対象。NDF の強み（モード・並列・多 AI レビュー・リリース）を「Spec Kit の拡張」として出せれば、利用者は既知の語で入れる。ただし Spec Kit は版の進みが速い（v1.4.0、2026-11-10）ので、依存する前に安定性の確認が要る | 仕様文書の形の自作 |

**自作を残すべきもの:** モードの判定、承認ゲートの代行判定、複数ベンダーの収束レビュー、レーンの並列とオーガナイザー、耐久実行（DBOS は既存ライブラリで、利用者の「基盤は既存ライブラリを使う」方針とも合う）、確定仕様化からリリースまでの工程、公開契約の生成。ただし 1 の評価で効き目が示せたものに限る。

**自作をやめるべきもの:** 設計・決定・要求の文書の形（外部の標準で足りる）、テストの強さの判定（ツールで足りる）、依存の向きの検査（import-linter）、ブランチの多層運用。

---

## 7. 確かめられなかったこと

- **Spec Kit の現行の成果物のファイル名と `[P]`（並列のタスクの印）の有無**: 取得した README の範囲には無かった。過去の版の第三者記事では `spec.md`・`plan.md`・`research.md`・`data-model.md`・`contracts/`・`tasks.md` とされる（[DEV Community](https://dev.to/x4nent/github-spec-kit-deep-dive-spec-driven-development-the-constitution-speckit-slash-commands-1m71)）
- **BMAD の v6 の具体のエージェント名と工程（analysis / planning / solutioning / implementation）**: 現行 README に無く、古い版の写しにしか無い。v7 は 2026-11 中旬に出る予定とあり、形が変わる見込み
- **Anthropic の "Demystifying evals for AI agents" の本文と日付**（二次情報で日付が食い違う）、**OpenAI の SWE-bench Verified の記事本文**（403）
- **DORA AI Capabilities Model の 7 項目の全文**（検索結果に一覧が無かった）
- **Cursor の background agents の公式の仕様**（第三者の記事のみ）
- **OpenHands の並列エージェントの公式の機能**（公式では並列のツール呼び出しの言及のみ）
- **NDF の `design-survey.py check` が要求 ↔ 設計 ↔ テストの紐づけをどこまで検査しているか**: 本調査では規則の全体を読んでいない
- **Superpowers のスター数（318,080）**: `gh api` の値そのまま。異常に多く見えるが、取得値以上の確認はしていない
