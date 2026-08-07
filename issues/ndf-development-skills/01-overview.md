# NDF 開発方法論レイヤーの導入: 概要

## この文書の構成

| ファイル | 内容 |
| --- | --- |
| `01-overview.md` | 目的、用語、解決したい課題 |
| [02-skill-inventory.md](02-skill-inventory.md) | 既存 Skill の棚卸（統合・削除・設定規約） |
| [03-runtime-conformance.md](03-runtime-conformance.md) | Claude Code / Codex / Kiro の規約差分と対応 |
| [04-development-skills.md](04-development-skills.md) | 開発方法論レイヤーの 8 個と既存 Skill の改修 |
| [05-goal-workflow.md](05-goal-workflow.md) | 設計確定後の一気通貫実行 |
| [06-release-plan.md](06-release-plan.md) | リリース分割と PR 分割 |
| [07-tasks.md](07-tasks.md) | タスク分解 |
| [08-verification.md](08-verification.md) | 影響範囲、リスク、テスト計画、未確認事項 |

## 用語

本文書は AI エージェント向け機能拡張の設定項目を扱うため、識別子をそのまま用いる箇所がある。対応は次のとおり。

| 識別子 | 意味 |
| --- | --- |
| Skill | エージェントに手順や判断基準を与える拡張単位。`SKILL.md` 1 ファイルと補助ファイルからなる |
| frontmatter | `SKILL.md` 冒頭の YAML メタデータ。エージェントがいつ Skill を使うかの判定に使われる |
| `description` | Skill の説明。全 Skill 分が常時エージェントのコンテキストへ読み込まれる |
| `when_to_use` | 発動トリガの補足。Claude Code 独自の項目 |
| `disable-model-invocation` | エージェントによる自動起動を禁じ、利用者の明示指示だけで動かす設定 |
| `paths` | 指定したファイルを扱うときだけ自動起動させる設定。Claude Code 独自 |
| `arguments` | 名前付き引数を宣言し、本文で `$名前` として参照する設定。Claude Code 独自 |
| manifest | ランタイムごとにどの Skill を配布するかを列挙したファイル |
| ランタイム | Skill を実行する処理系。本プロジェクトでは Claude Code / Codex / Kiro の 3 種 |
| 上流 | 参考にした外部の Skill 集リポジトリ |

## 目的

本プランは 4 つの作業を扱う。

1. **既存 Skill の棚卸** — 49 個ある Skill の重複統合、利用実績の乏しい Skill の整理、frontmatter の見直し
2. **開発方法論レイヤーの追加** — 8 個の Skill を新設し、要求定義から検証までの工程を埋める
3. **既存 Skill の改修** — 新レイヤーへの接続
4. **一気通貫実行の整備** — ランタイム組み込みの `/goal` ループを土台に、設計確定後からリリース直前までを自動で進める `execute-plan` を新設する

新設する Skill は、項目 2 の開発方法論レイヤー 8 個と項目 4 の `execute-plan` 1 個をあわせた計 9 個である。段階ごとの Skill 総数は [02-skill-inventory.md](02-skill-inventory.md)「Skill 総数の推移」を唯一の基準とし、本文書では数値を書き下さない。

外部リポジトリは submodule やコピーで取り込まず、独自の Skill として再執筆し、参照元は `upstream-skills.lock.yaml` で固定する。

棚卸を先に行う。整理されていない 49 個の上に新設 9 個を積むと、トリガ衝突とコンテキスト肥大が悪化するためである。

## 解決したい課題

### 開発方法論レイヤーが欠けている

Git 操作、プルリクエスト運用、継続的インテグレーション、レビュー、デプロイといった**運用工程**は揃っているが、その手前の**開発方法論**がない。

1. 要求から受け入れ条件を作る工程がない
2. 実装前の設計レビューがない
3. ドメイン駆動設計の語彙と不変条件を扱う仕組みがない
4. 各実装タスク内の「失敗するテストを書く → 通す → 整理する」サイクルがない
5. 機能実装と分離した安全な構造改善の手順がない
6. レビューが「仕様適合」と「コード品質」に分かれていない
7. テストの少ない既存コード向けの現状固定テストがない
8. デザインパターンを使う / 使わないの判断基準がない

結果として、エージェントが「設計を飛ばして実装」「テストなしで完了宣言」「不要なパターン導入」に流れやすい。

### Skill が重複し肥大している

`plugins/ndf-shared/skills/` の全 49 Skill を計測した結果:

| 項目 | 実測値 |
| --- | --- |
| 総 Skill 数 | 49 |
| `when_to_use` が未設定 | 14 個 |
| `disable-model-invocation: true`（自動起動しない） | 13 個 |
| 上記のうち `when_to_use` も未設定 | 12 個 |
| 300 行超 | 7 個（最大 484 行） |
| ブラウザ自動テスト関連 | 9 個 |

主要な重複:

- `codex`(473 行) と `gemini`(444 行) — 外部 AI への委譲手順。本文の大半が共通
- `review`(337 行) と `review-branch`(129 行) — 対象がプルリクエスト差分かローカル差分かの違いのみで、レビュー観点は同一
- `review-pr-comments` / `fix` / `resolve-pr-comments` — 分類・修正・返信という一連のフローが 3 分割
- `clean`(20 行) / `merged`(29 行) / `sync-main`(48 行) — いずれもマージ後のブランチ整理
- `branch-fix-strategy` と `cherry-pick-pr` — トリガ語が完全重複
- `data-analyst-export`(64 行) と `data-analyst-sql-optimization`(48 行) — 同一エージェント向けの小型 Skill
- `browser-test` と `playwright-execution` — どちらもブラウザ自動テストの実行

### 自然文の依頼で Skill が起動しない

`disable-model-invocation: true` はエージェントからの自動起動を止め、利用者の明示指示専用にする設定である。現状これが付いているのは次の 13 個。

```text
browser-test, cherry-pick-pr, clean, deepwiki-transfer, deploy,
knowledge-reorg, merged, pr, pr-tests, resolve-pr-comments,
review, statusline, sync-main
```

このうち `review` と `pr` は日常的に「レビューして」「プルリクエスト作って」と自然文で依頼される。起動しないため、エージェントが Skill を使わず独自手順で実行する。

`when_to_use` が未設定の 14 個は、英語 1 行の `description` だけで判定されるため日本語の依頼に反応しにくい。ただし `when_to_use` は Claude Code 以外では読まれないため、補うべきは `description` の側である。

逆方向の問題もある。`python-execution` のトリガは `'python'` `'スクリプト'`、`git-gh-operations` のトリガは `'git add'` `'git commit'` で、ほぼ全セッションにヒットする。広すぎるトリガは他 Skill の発動を埋もれさせる。

### 設計確定後も逐次指示が要る

`issue-plan-strategy` がリリースブランチ作成から個別プルリクエスト、相互レビュー、マージまでの手順を文書として定義しているが、実行するのは利用者の逐次指示である。設計が固まった後も各ステップを起動し続ける必要がある。

Claude Code と Codex は完了条件まで作業を継続する `/goal` を組み込みで持ち、`/goal /ndf:cross-review 14256` のように NDF の Skill を駆動した記録が 8 回ある。継続ループは既に存在するため、不足しているのは条件文の組み立てと工程の接続である。

## 外部 Skill 集を丸ごと取り込まない理由

| 上流 | 取り込まない理由 |
| --- | --- |
| [obra/superpowers](https://github.com/obra/superpowers) | 全変更にフル工程を強制するため、文言修正や設定変更まで重くなる |
| [wshobson/agents](https://github.com/wshobson/agents) | 175 個と大きく、Git / プルリクエスト / 継続的インテグレーション / エージェント振り分けが既存機能と全面的に重複する |
| [modu-ai/moai-adk](https://github.com/modu-ai/moai-adk) | 同リポジトリが DDD と呼ぶものは Domain-Driven **Development** であり、Evans の Domain-Driven **Design** とは別物。同名で取り込むと概念が汚染される |

巨大な Skill 集を重ねると、同一場面で複数の Skill が起動し、互いに矛盾する強制ルールを出す。

## 参照

- 調査レポート: [../report01.md](../report01.md)
- 仕様: [Agent Skills Specification](https://agentskills.io/specification)
- 仕様: [Claude Code Skills — Frontmatter reference](https://code.claude.com/docs/en/skills#frontmatter-reference)
- 仕様: [Codex — Build skills](https://learn.chatgpt.com/docs/build-skills)
- 仕様: [Kiro — Skills](https://kiro.dev/docs/skills/)
- 組み込み機能: [Claude Code — Keep Claude working toward a goal](https://code.claude.com/docs/en/goal) / [Codex — Slash commands](https://developers.openai.com/codex/guides/slash-commands/)
- 検証ツール: [skills-ref](https://github.com/agentskills/agentskills/tree/main/skills-ref)
- 上流候補: [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) / [obra/superpowers](https://github.com/obra/superpowers) / [wshobson/agents](https://github.com/wshobson/agents) / [github/awesome-copilot](https://github.com/github/awesome-copilot) / [modu-ai/moai-adk](https://github.com/modu-ai/moai-adk)
