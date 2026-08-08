# タスク分解

用語は [01-overview.md](01-overview.md)、PR 番号は [06-release-plan.md](06-release-plan.md) を参照。

## Release 0

### Task 0-1: 棚卸台帳と frontmatter 規約

利用実績の実測は完了済み（[02-skill-inventory.md](02-skill-inventory.md)、2026-08-07 時点、1,938 セッション / 80 日）。

- **対象ファイル:** `docs/specifications/ndf-skill-inventory.md`、`plugins/ndf-shared/skills/README.md`、`plugins/ndf-shared/skills/skill-stats/scripts/skill-stats.py`
- **変更内容:**
  - 実測結果を台帳へ転記し、Skill ごとに「行数 / frontmatter 設定 / 起動数 / 機会数 / 判定 / 判定根拠」を記録する
  - `skill-stats` を修正する（不具合と原因は [02-skill-inventory.md](02-skill-inventory.md)「測定ツールの不具合」）
    - トリガ抽出の対象に `when_to_use` を加え、見出し語として `Triggers:` と `明示トリガ:` の双方を受ける
    - `<command-name>` を含む利用者メッセージから明示起動を数え、エージェントの `Skill` ツール呼び出しと合算する。トリガ一致の判定からは従来どおり除外する（明示起動は「機会」ではない）
    - 出力に「計 / 自動 / 明示」の 3 列を持たせ、台帳へそのまま転記できる形にする
    - Task 0-7 で `cross-review` の明示トリガを `description` へ移すため、修正後も `when_to_use` の有無どちらでも同じ結果になることを確認する
  - frontmatter 規約（[02-skill-inventory.md](02-skill-inventory.md)）を明文化する
  - トリガ語の一意性ルールと、広すぎるトリガの禁止例を記載する

### Task 0-2〜0-6: Skill の統合と削除

共通手順:

1. 統合先へ内容を取り込む。単純連結ではなく、重複記述を落として再構成する
2. 統合元ディレクトリを削除する
3. `manifests/{claude,codex,kiro}-skills.txt` から統合元を削除する
4. 他 Skill・エージェント定義・文書からの参照を `grep -rn '<旧 Skill 名>'` で洗い出して更新する
5. `python3 scripts/check-markdown-links.py` でリンク切れがないことを確認する

個別の注意点:

| PR | 注意点 |
| --- | --- |
| 0-2 | `cross-review` が `fix` をループ内で呼ぶ。呼び出し規約を壊さない。`review` は `--branch` 引数でローカル差分レビューに切り替える |
| 0-3 | 外部 AI 呼び出しの差分を `references/cli-codex.md` / `references/cli-gemini.md` に分離する。`cross-review` は両方を呼ぶため呼び出し箇所を更新する |
| 0-4 | 起動 247 回の `merged` を残し、`clean` を吸収する。改名しない。`cherry-pick-pr`(16 回) に `branch-fix-strategy`(4 回) を吸収し、実行コマンド側の名前を残す。`cherry-pick-pr` は明示指示専用のため、`branch-fix-strategy` 由来の核心ルール（環境ブランチへの適用原則、ブランチ汚染の回避）は常時読み込まれる `ndf-policies` へ移し、自然文の質問から参照できなくなる退行を防ぐ |
| 0-5 | ブラウザ自動テストは [02-skill-inventory.md](02-skill-inventory.md) の対応表どおり 4 個へまとめる。`playwright-kit-ops` は実行環境ディレクトリとスクリプトを持つため単独で残し、`build-runtime-plugins.sh` の除外パターンが効く配置を保つ |
| 0-6 | 削除対象は台帳で削除判定した 9 個に限り、判定は [02-skill-inventory.md](02-skill-inventory.md) の判断基準表に従う。うち `sync-main` は 0-4 で処理するため、この PR の対象は 8 個 |

### Task 0-7: frontmatter 一括見直し

- **対象ファイル:** 全 `SKILL.md` の frontmatter、`scripts/check-skill-frontmatter.py`（新規）、`.github/workflows/runtime-plugin-validate.yml`
- **変更内容:**
  - `merged` / `pr` / `review` / `pr-tests` から `disable-model-invocation` を外し、`description` に発動条件を含める
  - `deploy` と `cherry-pick-pr` 相当の破壊的操作は明示指示専用を維持する
  - 主要トリガは `description` に入れる。`when_to_use` は Claude Code 向けの追加トリガが要る Skill にだけ付与し、`description` で足りるものには付けない（[03-runtime-conformance.md](03-runtime-conformance.md)）
  - `plan-to-spec` の長い `description` は要点を残して `when_to_use` へ移す。`cross-review` は逆に、`when_to_use` に置いた明示トリガの要点を `description` へ移す
  - 広すぎるトリガを具体化する
  - `description` の先頭に主要な用途とトリガ語を置き、合計を Codex の初期一覧予算へ収める（[02-skill-inventory.md](02-skill-inventory.md)「Codex の初期一覧予算」）
  - `paths` / `effort` / `arguments` / `license` / `metadata` を導入方針に従って付与する
  - 検査スクリプトを継続的インテグレーションへ組み込む

検査項目:

| 系統 | 失敗条件 |
| --- | --- |
| 仕様準拠 | `name` が親ディレクトリ名と不一致 / 64 文字超 / 文字種違反 / 連続ハイフン |
| 仕様準拠 | `description` が空、または 1,024 文字超 |
| 仕様準拠 | `compatibility` が 500 文字超 |
| 安全性 | frontmatter に `<` または `>` が含まれる |
| 可搬性 | `description` に発動条件を示す語（`Use when` / `使う`）が含まれない |
| 可搬性 | `description` が二重引用符で囲まれていない |
| 可搬性 | `description` の最初の 1 文に主要な用途とトリガ語が含まれない（Codex が短縮しても暗黙起動が働くようにする） |
| 運用 | `description` が 300 文字超 |
| 運用 | Codex の初期一覧に載る合計（全 Skill の `name` + `description` + ファイルパス）が 8,000 文字超 |
| 運用 | `description` + `when_to_use` が 1,536 文字超 |
| 運用 | `when_to_use` があるのに `description` の内容を言い換えただけで、追加トリガを含まない（`when_to_use` は追加トリガがある場合のみ付ける。未設定は失敗としない） |
| 運用 | `SKILL.md` が 500 行超 |
| 運用 | 全 Skill の frontmatter 合計が基準値超 |
| 運用 | `disable-model-invocation` があるのに `argument-hint` がない |
| 運用 | `disable-model-invocation` と `user-invocable: false` が同時指定（誰も起動できなくなる） |
| 運用 | `context: fork` 以外で `agent` / `background` が指定されている |
| 運用 | トリガ語が他 Skill と重複 |
| 運用 | 未知の項目名（`when-to-use` のようなハイフン誤りを弾く） |

### Task 0-8: Codex の規約対応

- **対象ファイル:** `scripts/build-runtime-plugins.sh`、`plugins/ndf-codex/skills/<Skill 名>/agents/openai.yaml`
- **変更内容:**
  - Codex は `agents/openai.yaml` を Skill ディレクトリ配下のファイルとして読む（[Build skills](https://developers.openai.com/codex/skills)）。単一ファイルでは個別 Skill の暗黙起動を制御できないため、`disable-model-invocation: true` を持つ Skill それぞれに `skills/<Skill 名>/agents/openai.yaml` をビルド時に生成する
  - `disable-model-invocation: true` を `policy.allow_implicit_invocation: false` へ変換し、`argument-hint` を `interface.default_prompt` に対応付ける。既存の Codex 用マニフェスト生成処理と同じ形で実装する
  - 生成対象を持たない Skill にはファイルを置かない。`build-runtime-plugins.sh --check` で生成物の差異を検出できる状態を保つ
  - 実装前に Codex CLI 実機でスキーマと配置を検証する。検証できない場合はこの PR を保留し、他を先に進める

### Task 0-9: Kiro 導入方式の修正

検証は完了済み（[03-runtime-conformance.md](03-runtime-conformance.md)、kiro-cli 2.16.1 / 2026-08-07）。

- **対象ファイル:** `plugins/ndf-kiro/install.sh`、`plugins/ndf-kiro/agents/default.json.template`、`plugins/ndf-kiro/README.md`、`scripts/runtime-smoke-test.sh`、`docs/specifications/ndf-skill-inventory.md`
- **変更内容:**
  - エージェント名を `ndf` にする。`--set-default` オプトインを追加し、指定時のみ `kiro-cli agent set-default ndf` を実行する。実行前に現在の既定を表示して確認を取る
  - 完了メッセージを `kiro-cli chat --agent ndf` へ修正する
  - `resources` から `skill://.kiro/skills/**/SKILL.md` を削除する
  - `.kiro/steering/ndf-policies.md` を生成し、`resources` の `file://.kiro/skills/ndf-policies/SKILL.md` を削除する
  - `--scope workspace|global`（既定 `workspace`）を追加する。`global` では Skill を `~/.kiro/skills/`、常時指示を `~/.kiro/steering/ndf-policies.md` へ生成する
  - 既存の `.kiro/agents/default.json` を検出したらバックアップし、移行手順を案内する
  - `README.md` に、プロジェクト配置では `allowed-tools` が事前承認にならないこと（[#6055](https://github.com/kirodotdev/Kiro/issues/6055)）を明記する。あわせて検証日と版数つきで、シンボリックリンクと起動時読み込みは 2.16.1 で問題なしと記録する
  - `runtime-smoke-test.sh` に、`agent list` へ `ndf` が現れること、`--set-default` 後に既定が切り替わること、コンテキスト占有率が基準以内であることの検査を追加する
  - 検証結果（日付 / kiro-cli 版数 / 各項目の結果 / 実測占有率）を台帳へ残す

### Task 0-10: 棚卸の仕上げ

- **対象ファイル:** manifest 3 種、`ndf-policies/SKILL.md`、`AGENTS.md`、`CLAUDE.md`、`KIRO.md`、`docs/ndf-plugin-reference.md`、各 runtime `README.md`、下記のバージョン記載ファイル一式
- **変更内容:**
  - 統合と整理の結果を manifest 3 種すべてへ反映する
  - `ndf-policies` に旧 Skill 名から新 Skill 名への対応表を記載する
  - バージョンを 5.0.0 へ上げる。版数は `plugin.json` 以外にも散在しており、実測で次の箇所にある

| ファイル | 記載箇所 |
| --- | --- |
| `plugins/ndf-claude/.claude-plugin/plugin.json` | `version` |
| `plugins/ndf-codex/.codex-plugin/plugin.json` | `version` |
| `.claude-plugin/marketplace.json` | `description` の本文 |
| `AGENTS.md` | プラグイン概要 |
| `README.md` | 冒頭、プラグイン一覧表、変更点の見出し |
| `docs/presentations/README.md` | 資料一覧の説明 |
| `docs/presentations/2026-08-06-ai-plugins-intro.md` | `header` |

  - 更新漏れを防ぐため、`grep -rn '<旧版数>'` が生成物とプレゼン資料を除いてヒットしないことを確認する。継続的に検査したい場合は版数の一致検査をスクリプト化する

## Release 1

### Task 1-1: `requirements-design`

- **対象ファイル:** `skills/requirements-design/SKILL.md`、`references/spec-template.md`、`references/acceptance-criteria.md`
- **変更内容:**
  - 実装前に、目的・成功条件・実行コマンド・プロジェクト構造・コーディング規約・テスト戦略・「常に行う / 確認してから行う / 行わない」の境界を明文化させる
  - 曖昧な要求をそのまま実装させず、前提条件を明示して検証可能な成功条件へ変換する手順を規定する
  - `acceptance-criteria.md` に条件記述の形式と、受け入れ条件が満たすべき性質（観測可能・一意・テスト可能）を記載する
  - `spec-template.md` は `implementation-plan` と重複させず、「何を満たすか」と「どう分解するか」を分離する

### Task 1-2: `tdd-cycle`

- **対象ファイル:** `skills/tdd-cycle/SKILL.md`、`references/test-quality.md`、`references/testing-levels.md`
- **変更内容:**
  - [04-development-skills.md](04-development-skills.md) の統合方針を本文化する
  - 失敗の証跡（実行コマンド・失敗メッセージ・失敗理由が期待どおりか）を必須化する
  - テスト駆動を適用しない例外（ドキュメント、静的設定、生成物）を明示する
  - `test-quality.md` に脆いテストの例と代替を記載する
  - `testing-levels.md` に単体・結合・契約・端から端までの使い分けを記載する

### Task 1-3: `safe-refactoring`

- **対象ファイル:** `skills/safe-refactoring/SKILL.md`、`references/characterization-tests.md`、`references/code-smells.md`、`references/refactoring-catalog.md`
- **変更内容:**
  - 「テストがなければ構造改善ではなく単なる編集である」を原則として明記する
  - テストがある場合と、テストが乏しい既存コードの場合で手順を分岐する
  - `code-smells.md` に長すぎるメソッド、肥大したクラス、重複、長い引数リスト、他クラスへの過度な関心、基本型への固執、マジックナンバー、深いネスト、デッドコード、過度な相互依存を記載する
  - `refactoring-catalog.md` にメソッド抽出、型による安全化、戦略の切り出し、責務の連鎖などの適用条件を記載する
  - 機能変更と構造改善を同一差分に混ぜない規則を置く

### Task 1-4: `quality-gates`

- **対象ファイル:** `skills/quality-gates/SKILL.md`、`references/definition-of-done.md`
- **変更内容:**
  - 完了宣言の前に、コマンドを実行してその結果（コマンド、終了コード、実行時刻）を根拠として要求する
  - 限定的な検証 → 全体テスト → ビルド・静的解析・型検査・結合テストの段階を定義する
  - 実行していないテストを「通った」と報告することを明示的に禁じる
  - モード別の完了の定義を記載する
  - カバレッジ閾値を Skill 側に持たず、対象プロジェクトのカバレッジツール設定から読む手順を記載する（[04-development-skills.md](04-development-skills.md)）。設定がない場合は閾値判定を行わない

### Task 1-5: ライセンスと上流の固定

- **対象ファイル:** `THIRD_PARTY_NOTICES.md`、`upstream-skills.lock.yaml`、`scripts/build-runtime-plugins.sh`、`plugins/ndf-kiro/install.sh`
- **変更内容:** [04-development-skills.md](04-development-skills.md) の「ライセンスと上流の固定」に記載のとおり
  - 告知の編集元はリポジトリ直下の `THIRD_PARTY_NOTICES.md` 1 ファイルとする
  - `build-runtime-plugins.sh` に、編集元を `plugins/ndf-claude/` / `plugins/ndf-codex/` / `plugins/ndf-kiro/` へ同期する処理を追加する。同期漏れは `--check` で差異として検出できる状態にする
  - Kiro は配布物を直接読ませないため、`install.sh` が同梱の告知を導入先（`--scope workspace` は `.kiro/`、`global` は `~/.kiro/`）へ配置する。Task 0-9 は告知を扱わず、配置処理はこのタスクにまとめる
  - 転用が発生していない時点でも同期経路を先に用意する。転用が生じてから経路を足すと配布物への反映漏れに気づけない

### Task 1-6: `development-workflow`

- **対象ファイル:** `skills/development-workflow/SKILL.md`、`references/workflow-modes.md`
- **変更内容:**
  - 変更内容から 4 モードを判定するフローを定義する。この Skill を判定基準の唯一の置き場所とし、呼び出し側が判定結果だけを受け取れる出力形式にする
  - モードごとに起動する Skill を表で明示する
  - 標準フローを記載する
  - この時点で `design-review` / `domain-modeling` / `object-design` は未実装のため、`architecture` モードは Release 2 で有効化すると現状として明記し、リンク切れを作らない

### Task 1-7: 既存 Skill 改修

- **対象ファイル:** `implementation-plan` / `problem-solving` / `review` / `pr-tests` / `plan-to-spec` / `investigation-rules` の各 `SKILL.md`、manifest 3 種、`plugin.json`
- **変更内容:** [04-development-skills.md](04-development-skills.md) の「既存 Skill の改修」に記載のとおり
  - `review` は Release 0 で `review-branch` を統合済みのため、ここでは二段構成への再編のみ行う
  - `bash scripts/build-runtime-plugins.sh` で生成物を同期し、`--check` で差異がないことを確認する

## Release 2

### Task 2-1〜2-3: 設計品質の 3 Skill

- **対象ファイル:** `design-review` / `domain-modeling` / `object-design` の各 `SKILL.md` と補助ファイル
- **変更内容:**
  - `design-review`: 文脈収集 → 全体構造レビュー → 詳細レビュー → 判定 の流れを、実装前の設計に適用する
  - `domain-modeling`: 共通言語、サブドメイン、境界づけられたコンテキスト、コンテキストマップ、エンティティ、値オブジェクト、集約、不変条件、リポジトリ、ドメインサービス、ドメインイベント、腐敗防止層、クリーンアーキテクチャ、ヘキサゴナルアーキテクチャを扱う。適用条件（境界づけられたコンテキストが複数ある、複雑な状態遷移がある、単純な登録参照更新削除で表せない不変条件がある 等）を先に判定させ、単純な管理画面へ集約を持ち込ませない
  - `object-design`: 設計原則をレビュー質問として使う。パターン採用時は「解決する現在の問題 / 存在する差異 / パターンを使わない単純案 / 採用理由 / 増える複雑性 / 削除条件」の記録を必須化する

### Task 2-4: 振り分けの更新

- **対象ファイル:** `plugins/ndf-claude/agents/director.md`、`skills/cross-review/SKILL.md`、`skills/development-workflow/SKILL.md`、manifest 3 種、`plugin.json`
- **変更内容:**
  - `director` の要求理解フェーズで `development-workflow` を呼び、返ったモードを受け取る手順を記載する。判定基準と振り分け表を `director` 側へ写さない（[04-development-skills.md](04-development-skills.md)「ワークフローの 4 モード」）
  - `development-workflow` を判定の唯一の置き場所として維持する。モードの追加・変更はこの Skill だけを直せば全ランタイムへ効く
  - `cross-review` の起動条件を高リスク変更に限定する。この基準は Task 3-1 の `execute-goal` のレビュー段階の分岐と共有する（[05-goal-workflow.md](05-goal-workflow.md)）
  - `development-workflow` の `architecture` モードを有効化する

## Release 3

### Task 3-1: 一気通貫実行

- **対象ファイル:** `skills/execute-goal/SKILL.md`、`skills/execute-goal/references/goal-conditions.md`、`skills/issue-plan-strategy/SKILL.md`、`plugins/ndf-kiro/README.md`
- **変更内容:** [05-goal-workflow.md](05-goal-workflow.md) に記載のとおり
  - Skill 名は `goal` で始めない。組み込みコマンド `/goal` と同名になる名前、および先頭一致してタブ補完で競合する名前を避ける
  - 継続ループを実装しない。組み込みの `/goal` へ渡す完了条件を組み立てる
  - `goal-conditions.md` に、評価器がツールを呼ばない前提で書く条件文の型と例を置く
  - Kiro には継続ループがないため、段階ごとに続行指示を要する手順として動く旨を明記する
  - レビュー段階は最初のモード判定の結果で呼び先を分ける。`light` / `standard` / `legacy-refactor` は `review`、`architecture` と途中で検出した高リスク変更は `cross-review`。Task 2-4 で限定した `cross-review` の起動条件と同じ基準にする
  - `issue-plan-strategy` を `execute-goal` から呼ばれる手順として整理する。詳細は [04-development-skills.md](04-development-skills.md) の「既存 Skill の改修」に記載のとおり
  - Codex で引数を受け取る `skills/execute-goal/agents/openai.yaml` は、Task 0-8 の生成処理が `disable-model-invocation: true` を持つ Skill を対象にビルド時へ出力する。このタスクの対象ファイルには含めない

### Task 3-2: Skill 挙動評価

- **対象ファイル:** `tests/skill-eval/`、`.github/workflows/skill-eval.yml`
- **評価シナリオ:**
  1. ドキュメント修正でフル工程を要求しない
  2. 新しい振る舞いをテストより先に実装しようとすると止める
  3. バグ修正では再現テストを先に作る
  4. テストのない既存コードでは現状固定テストへ切り替える
  5. 差異が 1 つしかない処理に戦略や生成の抽象化を導入しない
  6. 集約の不変条件を上位層から迂回して変更するとレビューで検出する
  7. 内部メソッドの呼出回数だけを検証する脆いテストを検出する
  8. コマンド実行結果なしの完了報告を拒否する
  9. 機能変更と無関係な大規模構造改善を同じ差分へ混ぜない
  10. `architecture` モードでは設計レビュー前に実装へ進まない
  11. 「レビューして」の自然文で `review` が自動起動する
  12. 一気通貫実行がリリース用プルリクエストを下書きのまま残して完了条件を満たす

### Task 3-3〜3-4: 整合性チェックと文書整備

- **対象ファイル:** `plan-to-spec/SKILL.md`、`docs/specifications/ndf-development-workflow.md`、`AGENTS.md`、`docs/ndf-plugin-reference.md`、各 runtime `README.md`
- **変更内容:**
  - 変更単位で提案・仕様・設計・タスクの整合性を確認する観点を `plan-to-spec` へ追加する。ディレクトリ構造は既存の `issues/` → 確定仕様化 → `docs/` を維持する
  - `bash scripts/runtime-smoke-test.sh` で 3 ランタイムとも新 Skill が読み込まれることを確認する
