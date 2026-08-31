# 調査結果

2026年8月8日時点での結論です。

**単体で最も要件に近いのは `addyosmani/agent-skills`、最も利用実績が強い開発ワークフローは `obra/superpowers`、Domain-Driven Designの補完には `wshobson/agents` が適しています。**

ただし、いずれか一つを丸ごと入れるより、

> **NDFをワークフローの司令塔として残し、Addyを基本設計、Superpowersを工程の規律、wshobsonをDDD・アーキテクチャ知識、GitHub/MoAIをリファクタリング知識として統合する**

のが最もよい構成です。

巨大プラグインを三つ重ねると、エージェントは賢くなるより先に会議体になります。

---

## おすすめ候補

GitHub starやskills.shのinstall数は、実利用者数そのものではなく、相対的な普及度を見るための指標です。更新や重複インストールも含まれ得ます。

| 優先度   | 候補                                        |                                                       利用実績の目安 | 強い領域                                                 | NDFへの取り込み方                       |
| ----- | ----------------------------------------- | ------------------------------------------------------------: | ---------------------------------------------------- | -------------------------------- |
| **1** | `addyosmani/agent-skills`                 |                          GitHub約8.3万star、skills.sh約30万install | 要件、Spec、段階的実装、TDD、簡素化、レビュー                           | **基本設計として採用**                    |
| **2** | `obra/superpowers`                        |                      GitHub約26.9万star、14 skills合計約260万install | 設計ゲート、詳細計画、TDD、サブエージェント実装、二段階レビュー、完了検証               | **工程の規律だけ抽出**                    |
| **3** | `wshobson/agents`                         | GitHub約3.9万star、175 skills。architecture約2万、review約2.6万install | 本来のDDD、Clean/Hexagonal Architecture、レビュー、ADR、複数レビュアー | **DDD・設計知識を選択導入**                |
| **4** | `github/awesome-copilot` の `refactor`     |                                                    約2万install | コードスメル、段階的リファクタリング、型安全性、デザインパターン                     | **safe-refactoringの参考にする**       |
| **5** | `modu-ai/moai-adk`                        |                                             GitHub約1,200 star | Characterization Test、レガシーコードの安全な改善、品質ゲート            | **legacy-safe-refactoringとして導入** |
| 補助    | `github/spec-kit` / `Fission-AI/OpenSpec` |                                        約12.6万star / 約6.4万star | 要求・設計・タスク成果物の整合性                                     | **成果物形式だけ参考にする**                 |

`Superpowers` は普及度では明らかに頭一つ抜けています。`brainstorming → writing-plans → TDD → subagent-driven-development → code review → verification` を一つの開発方法論としてまとめています。 ([GitHub][1])

`addyosmani/agent-skills` は、Spec Driven Development、incremental implementation、TDD、code simplification、code reviewなどが独立したSkillsとして整理されており、今回のNDFへの組み込みには最も扱いやすい構造です。 ([GitHub][2])

`wshobson/agents` は大規模なSkillsカタログで、特に `architecture-patterns` がClean Architecture、Hexagonal Architecture、Bounded Context、Aggregate、Value Object、Repository、Domain Eventといった**本来のDomain-Driven Design**を明示的に扱っています。 ([GitHub][3])

---

# 1. 中核にするべき候補

## A. `addyosmani/agent-skills`

### 採用したいSkills

* `spec-driven-development`
* `planning-and-task-breakdown`
* `incremental-implementation`
* `test-driven-development`
* `code-simplification`
* `code-review-and-quality`

### 特に優れている点

`spec-driven-development` は、実装前に次を明文化します。

* Objective
* 成功条件
* 実行コマンド
* プロジェクト構造
* コーディング規約
* テスト戦略
* Always / Ask first / Neverの境界

さらに、曖昧な要求をそのまま実装せず、前提条件を明示し、検証可能な成功条件へ変換する設計です。単なる「設計書を書け」ではなく、AIが勝手に仮定を埋めないための仕組みになっています。

`incremental-implementation` は、機能を小さな縦切りのスライスで実装し、各スライスで、

> Implement → Test → Verify → Commit

を繰り返します。不要な周辺リファクタリングを混ぜない、各変更をrevert可能にする、抽象化を先回りしない、といった保守性の高いルールもよくできています。

`test-driven-development` も、リポジトリ固有のテストコマンドを先に調査する、振る舞いをテストする、real implementation・fake・stub・mockの順で優先する、という現実的な内容です。ドキュメントや静的設定にはTDDを適用しない例外も明示されています。

### 評価

今回のNDFでは、**Addy版をベースにするのが一番安全**です。

Superpowersほど儀式的ではなく、それでいて設計・テスト・リファクタリング・レビューの工程が抜けません。

---

## B. `obra/superpowers`

### 採用したいSkills

* `brainstorming`
* `writing-plans`
* `test-driven-development`
* `subagent-driven-development`
* `requesting-code-review`
* `verification-before-completion`

### 特に優れている点

`brainstorming` は、コードを書く前に、

1. 現在のプロジェクトを調査
2. 要求を質問で明確化
3. 2～3案を比較
4. 設計を段階的に提示
5. Specを保存
6. Specをセルフレビュー
7. 実装計画へ移行

という流れを強制します。設計では、アーキテクチャ、コンポーネント、データフロー、エラー処理、テストまで扱います。

`writing-plans` は、タスクごとに正確なファイルパス、インターフェース、テストコード、実行コマンド、期待する失敗、最小実装、コミットまで記述します。各タスクがRED→GREEN→REFACTORを持つ点が非常に強いです。

### ただし、そのまま入れない方がよい理由

Superpowersはかなり厳格です。

* どんな小さな変更でも設計承認を要求する
* テストより先に書いたproduction codeは削除してやり直す
* 軽微な変更にも強いゲートを適用する

という思想です。

品質基準としては立派ですが、NDF全体の標準挙動にすると、設定変更、文言修正、軽微な保守まで重くなります。

したがって、Superpowersからは次を借りるのがよいです。

* REDを実際に失敗させた証拠を確認する
* SpecとPlanを分ける
* タスクを独立してレビュー可能な粒度にする
* 実装レビューを「仕様適合」と「コード品質」の二段階に分ける
* 完了宣言前に新しい検証結果を要求する

---

## C. `wshobson/agents`

### 採用したいSkills

* `architecture-patterns`
* `code-review-excellence`
* `architecture-decision-records`
* `multi-reviewer-patterns`
* 必要に応じて `workflow-patterns`

`architecture-patterns` は、今回の要件にあるDDDに最も直接的に対応しています。

扱っている内容は次のとおりです。

* Ubiquitous Language
* Subdomain
* Bounded Context
* Context Map
* Entity
* Value Object
* Aggregate / Aggregate Root
* Repository
* Domain Service
* Domain Event
* Anti-Corruption Layer
* Clean Architecture
* Hexagonal Architecture
* Ports and Adapters

さらに、Use Caseを実DBなしでテストできることを、正しく設計された境界の判断基準にしています。

`code-review-excellence` は、レビューを、

* Context Gathering
* High-Level Architecture Review
* Line-by-Line Review
* Summary and Decision

に分け、正しさ、セキュリティ、性能、テスト品質、保守性を確認します。現在のNDF `review` の設計レビュー部分を強化する材料として使えます。

### 注意点

`wshobson/agents` 全体は非常に大きいため、丸ごと入れると既存NDFと大量に重複します。

特に以下は既存NDFを正とした方がよいです。

* Git・PR操作
* CI対応
* E2E実行
* 外部AIレビュー
* サブエージェントのルーティング
* デプロイワークフロー

---

# 2. リファクタリング候補

## `github/awesome-copilot` の `refactor`

これは独立した開発方法論ではありませんが、**リファクタリングの技法集として非常に使いやすい**です。

含まれる内容は次のとおりです。

* Long Method
* Large Class
* Duplicated Code
* Long Parameter List
* Feature Envy
* Primitive Obsession
* Magic Number/String
* Nested Conditionals
* Dead Code
* Inappropriate Intimacy
* Extract Method
* 型安全化
* Strategy Pattern
* Chain of Responsibility

また、

> Testsがなければrefactoringではなくeditingである

という原則で、小さく変更して毎回テストする流れを定義しています。

これは後述の `safe-refactoring` のreferencesとして取り込むのがよいです。

---

## `modu-ai/moai-adk`

MoAIには、テストがほとんどない既存システム向けに、

> ANALYZE → PRESERVE → IMPROVE

という優れた流れがあります。

* ANALYZE: 構造・依存・問題を調査
* PRESERVE: Characterization Testで現状の振る舞いを固定
* IMPROVE: 小さく変更し、毎回テストしてコミット

新規開発や一定以上テストがある場合はRED→GREEN→REFACTORを使い、テストの乏しい既存システムではCharacterization Testを安全網にする構成です。

### 重要な注意

MoAIが呼ぶ「DDD」は、

> **Domain-Driven Development**

であり、Evansの、

> **Domain-Driven Design**

ではありません。

NDFに取り込む際は、絶対に `ddd` という名前にせず、

```text
legacy-safe-refactoring
```

または、

```text
characterization-driven-refactoring
```

と命名した方がよいです。

また、MoAIの「coverage 10%未満ならDDD」「最終85%以上」といった固定値も、そのまま採用せず、プロジェクトや変更リスクごとの設定にするべきです。

---

# 3. 人気はあるが、丸ごとの導入を勧めないもの

## `github/spec-kit` と `Fission-AI/OpenSpec`

Spec Kitは、

> constitution → specify → clarify → plan → tasks → implement

という成果物中心の流れです。

OpenSpecは変更ごとに、

* proposal
* specs
* design
* tasks

を管理します。Spec Kitより軽く、既存プロジェクトへの導入を重視しています。 ([GitHub][4])

NDFとの相性は、私の判断では**OpenSpecの方がよい**です。

現在のNDFは、

* `issues/` に実装Planを作る
* 実装後に `plan-to-spec` で確定仕様書にする

という流れをすでに持っています。OpenSpecのchange単位の成果物モデルは、この流れへ無理なく組み込めます。

ただし両者とも、TDD、クラス設計、リファクタリング、コードレビューは十分ではありません。**成果物の整合性チェックだけ借りる**のがよいです。

---

## `affaan-m/ECC` と `BMAD-METHOD`

ECCは約23.8万star、BMAD-METHODは約5.2万starで、どちらもかなり利用されています。要件分析、計画、エージェント分業、TDD、レビュー、テスト、E2Eなどを一式で提供します。

ただし、これらはSkills集というより**開発ハーネスそのもの**です。

NDFにはすでに、

* director
* corder
* researcher
* qa
* debugger
* code-reviewer
* PRワークフロー
* cross-review
* Playwright
* CI・デプロイ支援

があります。

そのためECC/BMADを入れると、planner、reviewer、executor、workflow stateの主導権が二重になります。参考実装・ベンチマークとして読むのは有益ですが、直接統合する必要はありません。

---

## `ramziddin/solid-skills`

今回の要件にかなり直球で、

* TDD
* SOLID
* Clean Code
* Design Patterns
* Object Design
* Code Smells
* Clean Architecture

を一つにまとめています。約2,400 install、約560 starです。 ([Skills][5])

ただし、次のようなルールを強制します。

* メソッドは10行未満
* クラスは50行未満
* インスタンス変数は最大2個
* `else` を極力使わない
* ドメインプリミティブは必ずValue Object化

教材としては面白いのですが、汎用の標準Skillとしては過剰です。

**行数やフィールド数ではなく、凝集度・結合度・変更理由・認知負荷・テスト容易性で判断する**ように書き直すべきです。

---

# 4. 現在のNDFに足りない部分

現在のNDFは、運用面ではすでにかなり強いです。

* `implementation-plan`: 複数ファイル変更、新機能、DB migrationなどでPlanを作成

* `review`: PR差分を可読性、保守性、セキュリティ、テストカバレッジ等でレビュー

* `pr-tests`: PR Test Planを実行して結果を反映

* `problem-solving`: 根本原因を上流まで追い、データとコードの両面から検証

* `plan-to-spec`: 実装後のPlanをas-is仕様へ変換

一方で、現在の主要Skillsには次の層が不足しています。

1. 要求からAcceptance Criteriaを作る工程
2. 実装前の設計レビュー
3. Ubiquitous LanguageやAggregate Invariantを扱う本来のDDD
4. 各実装タスク内のRED→GREEN→REFACTOR
5. 機能実装とは別に行う安全な構造リファクタリング
6. 「仕様適合」と「コード品質」を分けたレビュー
7. テストの少ない既存コード向けCharacterization Test
8. デザインパターンを使う・使わない判断基準

---

# 5. NDFに追加する推奨Skill構成

外部Skillsをそのまま並べず、次の**NDF標準Skills**へ再編集することを勧めます。

```text
plugins/ndf-shared/skills/
├── development-workflow/
│   ├── SKILL.md
│   └── references/
│       └── workflow-modes.md
│
├── requirements-design/
│   ├── SKILL.md
│   └── references/
│       ├── spec-template.md
│       └── acceptance-criteria.md
│
├── design-review/
│   ├── SKILL.md
│   └── references/
│       └── design-review-checklist.md
│
├── domain-modeling/
│   ├── SKILL.md
│   └── references/
│       ├── strategic-ddd.md
│       ├── tactical-ddd.md
│       └── domain-model-template.md
│
├── object-design/
│   ├── SKILL.md
│   └── references/
│       ├── solid-grasp.md
│       ├── class-design-checklist.md
│       └── pattern-selection.md
│
├── tdd-cycle/
│   ├── SKILL.md
│   └── references/
│       ├── test-quality.md
│       └── testing-levels.md
│
├── safe-refactoring/
│   ├── SKILL.md
│   └── references/
│       ├── characterization-tests.md
│       ├── code-smells.md
│       └── refactoring-catalog.md
│
└── quality-gates/
    ├── SKILL.md
    └── references/
        └── definition-of-done.md
```

## 上流ソースとの対応

| NDF Skill              | 主な参考元                                                          |
| ---------------------- | -------------------------------------------------------------- |
| `development-workflow` | Superpowers + Addy incremental implementation                  |
| `requirements-design`  | Addy spec-driven-development + OpenSpec                        |
| `design-review`        | Superpowers brainstorming + wshobson architecture              |
| `domain-modeling`      | wshobson architecture-patterns                                 |
| `object-design`        | SOLID/GRASP + awesome-copilot refactor + solid-skillsの一部       |
| `tdd-cycle`            | Addy TDDを基本に、SuperpowersのRED検証を追加                              |
| `safe-refactoring`     | Addy code-simplification + GitHub refactor + MoAI legacy cycle |
| `quality-gates`        | Addy review + Superpowers verification + 現行pr-tests            |

---

# 6. ワークフローを4モードに分ける

Superpowersの「すべてにフル工程」は重すぎます。変更の性質を最初に分類する構造が必要です。

| モード               | 対象                                   | 必須工程                                                                       |
| ----------------- | ------------------------------------ | -------------------------------------------------------------------------- |
| `light`           | 文言、ドキュメント、設定、局所的で振る舞いを変えない変更         | 成功条件、対象範囲、focused verification                                             |
| `standard`        | 一般的な機能追加・バグ修正                        | Spec、Plan、TDD、micro-refactor、review、full verification                      |
| `architecture`    | 公開API、DB schema、認証、複数モジュール、重要なドメイン変更 | DDD/ADR、design review、TDD、contract/E2E、cross-review                        |
| `legacy-refactor` | テストが少ない既存コード、振る舞い維持型の改善              | ANALYZE、characterization test、incremental refactor、regression verification |

推奨する標準フローは次の形です。

```text
Discover
  ↓
Requirements / Acceptance Criteria
  ↓
Design / Alternatives / Trade-offs
  ↓
Domain Modeling（必要な場合のみ）
  ↓
Design Review
  ↓
Implementation Plan
  ↓
RED → GREEN → REFACTOR
  ↓
Spec Compliance Review
  ↓
Code Quality Review
  ↓
Focused Tests → Full Suite → Build/Lint/Type/E2E
  ↓
PR / plan-to-spec
```

---

# 7. DDDの適用ルール

DDDは「全クラスをEntityとValue Objectにする方法」ではありません。

`domain-modeling` を自動適用する条件は、次のいずれかに絞るべきです。

* 複数の部門・業務で同じ言葉の意味が異なる
* 複数のSubdomainやBounded Contextがある
* 複雑な業務ルールや状態遷移がある
* 複数オブジェクト間で一貫性を守る必要がある
* 外部システムのモデルをそのまま内部へ持ち込みたくない
* 単純CRUDでは表現できないInvariantがある

Skillでは、最低限次を成果物にします。

```markdown
## Ubiquitous Language

## Subdomains

## Bounded Contexts

## Context Map

## Domain Model

### Entity

### Value Object

### Aggregate

### Invariants

### Repository / Port

### Domain Events
```

一方、単純な管理画面やCRUDでは、無理にAggregate、Repository、Domain Eventを導入しないようにします。

---

# 8. クラス設計・デザインパターンの判断基準

`object-design` では、SOLIDやGRASPを絶対規則ではなく、レビュー質問として使うのがよいです。

### クラス設計の確認事項

* このクラスの責務を一文で説明できるか
* 守るべきInvariantは何か
* 変更理由が複数混在していないか
* データと、そのデータに関する振る舞いが不自然に離れていないか
* 外部I/Oとドメインロジックが混ざっていないか
* テストのために大量のmockが必要になっていないか
* 継承よりcompositionで表現できないか
* 抽象化が現在存在するvariationを扱っているか
* 将来のためだけのinterfaceやfactoryを作っていないか

### パターン選択ルール

| パターン                    | 適用する問題                    |
| ----------------------- | ------------------------- |
| Strategy                | 実際に複数の交換可能なアルゴリズムが存在する    |
| Factory                 | 生成処理が複雑、または生成される型が実際に変化する |
| Adapter                 | 外部システムや異なるモデルとの境界を隔離する    |
| State                   | 状態によって許可される操作や振る舞いが明確に変わる |
| Specification / Policy  | 業務ルールを組み合わせたり差し替えたりする     |
| Decorator               | 中核処理を変えず、直交する振る舞いを追加する    |
| Observer / Domain Event | 発生した事実へ複数の独立処理が反応する       |

パターン導入時は、必ず次を記録させます。

```markdown
- 解決する現在の問題
- 存在するvariation
- パターンを使わない単純案
- 採用理由
- 増える複雑性
- 将来不要になった場合の削除条件
```

「Factoryを使った方が設計っぽい」は禁止です。パターンは勲章ではなく、複雑性との交換取引です。

---

# 9. 既存Skillsの具体的な改修

| 現在のSkill              | 追加する内容                                                                                                                                                            |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `implementation-plan` | Goal / Non-goals、Assumptions、Acceptance Criteria、代替案、Domain Glossary、Invariant、API・DB互換性、vertical slice、各TaskのRED/GREEN/REFACTOR、Risk、Rollback、Definition of Done |
| `problem-solving`     | バグ修正前の再現テスト。テスト困難な既存コードではCharacterization Testを先に作る                                                                                                               |
| `review`              | Pass 1: Spec・Acceptance Criteria・Domain Invariant適合、Pass 2: 可読性・設計・テスト品質・セキュリティ・性能                                                                                |
| `pr-tests`            | focused testとfull suiteを区別。実行コマンド、終了コード、実行時刻、結果を証拠として残す                                                                                                           |
| `plan-to-spec`        | Domain Glossary、Invariant、公開Contract、ADRの結論を確定仕様へ残す                                                                                                               |
| `director`            | light / standard / architecture / legacy-refactorを判定してSkillsをルーティング                                                                                               |
| `cross-review`        | すべての変更ではなく、architecture・security・data migrationなど高リスク変更に限定                                                                                                        |

特に `review` は、現在のPR差分レビューを捨てる必要はありません。レビューを次の二段階にするだけで大きく改善します。

```text
Review 1: 仕様適合
- Acceptance Criteriaを満たすか
- Domain Invariantを破っていないか
- Scope外の変更がないか
- テストが仕様を表しているか

Review 2: コード品質
- 責務・凝集度・結合度
- 依存方向
- 可読性・単純性
- コードスメル
- セキュリティ・性能
- テストが実装詳細に結合していないか
```

---

# 10. TDD Skillは一つに統合する

次の3つを並べて自動起動させるのは避けるべきです。

* Superpowers TDD
* Addy TDD
* wshobson workflow-patterns

互いに微妙に異なる強制ルールやcoverage基準があり、同じ場面で複数Skillが起動します。

NDFの `tdd-cycle` を唯一の正とし、次のように統合するのがよいです。

### Addyから採用

* リポジトリ固有のtest commandを最初に調査
* 振る舞いをテストし、実装詳細をテストしない
* real implementation > fake > stub > mock
* ドキュメント・静的設定にはTDDを強制しない
* bug fixは再現テストから始める

### Superpowersから採用

* REDが期待した理由で失敗したことを必ず確認
* GREENは最小実装
* REFACTOR中はテストをgreenに保つ
* 実行していないテストを「通った」と報告しない

### MoAIから採用

* テストの乏しい既存コードでは、先にCharacterization Test
* 大規模変更を一度に行わず、小さな安全状態を積み重ねる

---

# 11. Skills自体の評価も必要

Skillは文章としてもっともらしいだけでは不十分です。

SuperpowersはSkill behavior用のeval harnessを持ち、wshobson/agentsも静的検査、LLM Judge、複数回試行による評価を採用しています。  ([GitHub][6])

NDFでは最低限、次のシナリオを自動評価するとよいです。

1. ドキュメント修正ではフルSpec/TDDを要求しない
2. 新しい振る舞いをテストより先に実装しようとすると止める
3. バグ修正では再現テストを先に作る
4. テストのないレガシーコードではCharacterization Testへ切り替える
5. variationが一つしかない処理にStrategyやFactoryを導入しない
6. AggregateのInvariantをApplication Serviceから迂回して変更するとレビューで検出する
7. private methodの呼出回数だけを検証する脆いテストを検出する
8. コマンド実行結果なしの「完了しました」を拒否する
9. feature変更と無関係な大規模refactorを同じ差分へ混ぜない
10. architecture modeでは設計レビュー前に実装へ進まない

---

# 12. 取り込み方法とライセンス

NDFは現在、`ndf-shared` を編集元として、Claude Code、Codex、Kiro向けにruntime別プラグインを生成しています。この構造はそのまま維持するのがよいです。

外部repositoryをgit submoduleとしてruntimeへ直接載せるより、NDFのcanonical skillへ再編集し、参照元を固定する方が管理しやすくなります。

例えば次を追加します。

```text
THIRD_PARTY_NOTICES.md
upstream-skills.lock.yaml
```

```yaml
sources:
  - name: addyosmani-agent-skills
    repository: addyosmani/agent-skills
    commit: "<pinned-sha>"
    source_paths:
      - skills/spec-driven-development/SKILL.md
      - skills/test-driven-development/SKILL.md
    local_skills:
      - requirements-design
      - tdd-cycle
    license: MIT
    adaptation: "NDF用に再構成・日本語化・runtime非依存化"

  - name: moai-adk
    repository: modu-ai/moai-adk
    commit: "<pinned-sha>"
    local_skills:
      - safe-refactoring
    license: Apache-2.0
    adaptation: "DDDをlegacy-safe-refactoringへ改称"
```

主要候補はMIT Licenseですが、MoAI-ADKはApache-2.0です。直接または実質的に文章を転用する場合は、元のcopyright・license・NOTICEを保持し、変更した旨を記録する必要があります。NDF自身は現在MIT Licenseです。  ([GitHub][7])

---

# 推奨する導入順

## Phase 1：まず開発ループを完成させる

1. `development-workflow`
2. `requirements-design`
3. `tdd-cycle`
4. `safe-refactoring`
5. `quality-gates`

同時に、既存の、

* `implementation-plan`
* `problem-solving`
* `review`
* `pr-tests`

を改修します。

これだけで、要求→実装→リファクタリング→レビュー→テストのループがつながります。

## Phase 2：設計品質を追加する

6. `design-review`
7. `domain-modeling`
8. `object-design`

DDDは全変更へ適用せず、`architecture` modeだけで有効化します。

## Phase 3：運用を安定させる

9. OpenSpec風のSpec・Design・Tasks整合性チェック
10. Skill behavior eval
11. upstream commit固定と更新差分の監査
12. Claude/Codex/Kiroそれぞれのruntime smoke test

---

## 最終的な採用方針

最も現実的な構成は次です。

```text
ワークフロー司令塔:
  NDF development-workflow

要求・実装・レビューの基本:
  addyosmani/agent-skills

工程を飛ばさせない規律:
  obra/superpowers

DDD・Clean/Hexagonal Architecture:
  wshobson/agents

コードスメル・デザインパターンを使った改善:
  github/awesome-copilot refactor

テストの少ない既存コード:
  MoAI ANALYZE-PRESERVE-IMPROVE
  ※ legacy-safe-refactoringへ改称

成果物の整合性:
  OpenSpecを軽量に参考
```

**最初に取り込む5つは `requirements-design`、`tdd-cycle`、`safe-refactoring`、`quality-gates`、`development-workflow` です。**
DDDやデザインパターンより先に、この5つで「必ずリファクタリングと検証まで到達する開発ループ」を作るのが効果的です。

[1]: https://github.com/obra/superpowers?utm_source=chatgpt.com "GitHub - obra/superpowers: An agentic skills framework & software development methodology that works. · GitHub"
[2]: https://github.com/addyosmani/agent-skills?utm_source=chatgpt.com "GitHub - addyosmani/agent-skills: Production-grade engineering skills for AI coding agents. · GitHub"
[3]: https://github.com/wshobson/agents?utm_source=chatgpt.com "GitHub - wshobson/agents: Multi-harness agentic plugin marketplace for Claude Code, Codex CLI, Cursor, OpenCode, GitHub Copilot, and Gemini CLI · GitHub"
[4]: https://github.com/github/spec-kit "https://github.com/github/spec-kit"
[5]: https://www.skills.sh/ramziddin/solid-skills/solid "https://www.skills.sh/ramziddin/solid-skills/solid"
[6]: https://github.com/wshobson/agents "https://github.com/wshobson/agents"
[7]: https://github.com/obra/superpowers "https://github.com/obra/superpowers"
