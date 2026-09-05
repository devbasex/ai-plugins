# Claude Code Skills 公式ドキュメント詳細調査レポート — Context Budget と呼び出し制御

**この調査は 3 本に分かれている。**

- [フロントマターと description](01-frontmatter-and-description.md)
- [Progressive Disclosure と配置場所](02-disclosure-and-placement.md)
- [Context Budget と呼び出し制御](03-budget-and-invocation.md)

## 7. Context Budget

### 7.1 SLASH_COMMAND_TOOL_CHAR_BUDGET

原文引用:
> Skill descriptions are loaded into context so Claude knows what's available. All skill names are always included, but if you have many skills, descriptions are shortened to fit the character budget, which can strip the keywords Claude needs to match your request. The budget scales dynamically at 1% of the context window, with a fallback of 8,000 characters.

> To raise the limit, set the `SLASH_COMMAND_TOOL_CHAR_BUDGET` environment variable. Or trim descriptions at the source: front-load the key use case, since each entry is capped at 250 characters regardless of budget.

まとめ:
- **デフォルトバジェット**: コンテキストウィンドウの1%（フォールバック: 8,000文字）
- **個別description上限**: 250文字（バジェットに関係なく切り詰め）
- **スキル名**: 常に全て含まれる
- **description**: バジェット内に収まるよう短縮される場合がある
- **カスタマイズ**: `SLASH_COMMAND_TOOL_CHAR_BUDGET`環境変数で上限を引き上げ可能

---

## 8. スキルの呼び出し制御

### 8.1 呼び出し制御マトリクス

原文引用:

| Frontmatter | You can invoke | Claude can invoke | When loaded into context |
|:------------|:---------------|:------------------|:------------------------|
| (default) | Yes | Yes | Description always in context, full skill loads when invoked |
| `disable-model-invocation: true` | Yes | No | Description not in context, full skill loads when you invoke |
| `user-invocable: false` | No | Yes | Description always in context, full skill loads when invoked |

補足原文引用:
> The `user-invocable` field only controls menu visibility, not Skill tool access. Use `disable-model-invocation: true` to block programmatic invocation.

### 8.2 パーミッション制御

原文引用:
> Three ways to control which skills Claude can invoke:

> **Disable all skills** by denying the Skill tool in `/permissions`:
> ```
> Skill
> ```

> **Allow or deny specific skills** using permission rules:
> ```
> # Allow only specific skills
> Skill(commit)
> Skill(review-pr *)
>
> # Deny specific skills
> Skill(deploy *)
> ```

> Permission syntax: `Skill(name)` for exact match, `Skill(name *)` for prefix match with any arguments.

> **Hide individual skills** by adding `disable-model-invocation: true` to their frontmatter. This removes the skill from Claude's context entirely.

---

## 9. Hooks in Skills

### 9.1 スキル内Hooks構文

原文引用（Hooksドキュメント）:
> Hooks can be defined directly in **skills** and **subagents** using frontmatter. These hooks are scoped to the component's lifecycle and only run when that component is active.

> All hook events are supported. For subagents, `Stop` hooks are automatically converted to `SubagentStop` since that is the event that fires when a subagent completes.

> Hooks use the same configuration format as settings-based hooks but are scoped to the component's lifetime and cleaned up when it finishes.

公式例:
```yaml
---
name: secure-operations
description: Perform operations with security checks
hooks:
  PreToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "./scripts/security-check.sh"
---
```

### 9.2 `once`フィールド（スキル専用）

原文引用:
> `once` - If `true`, runs only once per session then is removed (skills only)

### 9.3 利用可能なHookイベント（全26種）

`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PermissionDenied`, `PostToolUse`, `PostToolUseFailure`, `Notification`, `SubagentStart`, `SubagentStop`, `TaskCreated`, `TaskCompleted`, `Stop`, `StopFailure`, `TeammateIdle`, `InstructionsLoaded`, `ConfigChange`, `CwdChanged`, `FileChanged`, `WorktreeCreate`, `WorktreeRemove`, `PreCompact`, `PostCompact`, `Elicitation`, `ElicitationResult`, `SessionEnd`

### 9.4 Hookハンドラタイプ（4種類）

- `command` -- シェルコマンド実行
- `http` -- HTTPリクエスト送信
- `prompt` -- 軽量モデルによるプロンプト評価
- `agent` -- エージェントによる評価

---

## 10. context: fork とサブエージェント連携

### 10.1 Skills と Subagents の関係

原文引用:

| Approach | System prompt | Task | Also loads |
|:---------|:-------------|:-----|:-----------|
| Skill with `context: fork` | From agent type (`Explore`, `Plan`, etc.) | SKILL.md content | CLAUDE.md |
| Subagent with `skills` field | Subagent's markdown body | Claude's delegation message | Preloaded skills + CLAUDE.md |

> With `context: fork`, you write the task in your skill and pick an agent type to execute it. For the inverse (defining a custom subagent that uses skills as reference material), see Subagents.

### 10.2 context: fork の注意点

原文引用:
> `context: fork` only makes sense for skills with explicit instructions. If your skill contains guidelines like "use these API conventions" without a task, the subagent receives the guidelines but no actionable prompt, and returns without meaningful output.

### 10.3 agent フィールドの詳細

原文引用:
> The `agent` field specifies which subagent configuration to use. Options include built-in agents (`Explore`, `Plan`, `general-purpose`) or any custom subagent from `.claude/agents/`. If omitted, uses `general-purpose`.

公式例:
```yaml
---
name: deep-research
description: Research a topic thoroughly
context: fork
agent: Explore
---

Research $ARGUMENTS thoroughly:

1. Find relevant files using Glob and Grep
2. Read and analyze the code
3. Summarize findings with specific file references
```

### 10.4 サブエージェントでのスキルプリロード

原文引用（サブエージェントドキュメント）:
> Use the `skills` field to inject skill content into a subagent's context at startup. This gives the subagent domain knowledge without requiring it to discover and load skills during execution.

> The full content of each skill is injected into the subagent's context, not just made available for invocation. Subagents don't inherit skills from the parent conversation; you must list them explicitly.

> This is the inverse of running a skill in a subagent. With `skills` in a subagent, the subagent controls the system prompt and loads skill content. With `context: fork` in a skill, the skill content is injected into the agent you specify. Both use the same underlying system.

```yaml
---
name: api-developer
description: Implement API endpoints following team conventions
skills:
  - api-conventions
  - error-handling-patterns
---

Implement API endpoints. Follow the conventions and patterns from the preloaded skills.
```

### 10.5 ビルトインサブエージェント

| Agent | Model | Tools | Purpose |
|:------|:------|:------|:--------|
| Explore | Haiku (fast) | Read-only | File discovery, code search, codebase exploration |
| Plan | Inherits | Read-only | Codebase research for planning |
| general-purpose | Inherits | All tools | Complex research, multi-step operations |

---

## 11. バンドルスキル（組み込みスキル）

原文引用:
> Bundled skills ship with Claude Code and are available in every session. Unlike built-in commands, which execute fixed logic directly, bundled skills are prompt-based: they give Claude a detailed playbook and let it orchestrate the work using its tools. This means bundled skills can spawn parallel agents, read files, and adapt to your codebase.

| Skill | Purpose (原文) |
|:------|:---------------|
| `/batch <instruction>` | Orchestrate large-scale changes across a codebase in parallel. Researches the codebase, decomposes the work into 5 to 30 independent units, and presents a plan. Once approved, spawns one background agent per unit in an isolated git worktree. |
| `/claude-api` | Load Claude API reference material for your project's language and Agent SDK reference. Also activates automatically when your code imports `anthropic`, `@anthropic-ai/sdk`, or `claude_agent_sdk`. |
| `/debug [description]` | Enable debug logging for the current session and troubleshoot issues by reading the session debug log. |
| `/loop [interval] <prompt>` | Run a prompt repeatedly on an interval while the session stays open. |
| `/simplify [focus]` | Review your recently changed files for code reuse, quality, and efficiency issues, then fix them. Spawns three review agents in parallel. |

---

## 12. プラグイン内のスキル

### 12.1 ディレクトリ構造

```
my-plugin/
├── .claude-plugin/
│   └── plugin.json
└── skills/
    └── code-review/
        └── SKILL.md
```

原文引用:
> Skills live in the `skills/` directory. Each skill is a folder containing a `SKILL.md` file. The folder name becomes the skill name, prefixed with the plugin's namespace (`hello/` in a plugin named `my-first-plugin` creates `/my-first-plugin:hello`).

### 12.2 名前空間

原文引用:
> Plugin skills are always namespaced (like `/my-first-plugin:hello`) to prevent conflicts when multiple plugins have skills with the same name.

### 12.3 commands/ と skills/ の違い

プラグインの`commands/`と`skills/`はどちらもスキルを定義できる:

| Directory | Location | Purpose (原文) |
|:----------|:---------|:---------------|
| `commands/` | Plugin root | Skills as Markdown files |
| `skills/` | Plugin root | Agent Skills with `SKILL.md` files |

### 12.4 リロード

原文引用:
> After installing the plugin, run `/reload-plugins` to load the Skills.

---

## 13. スキルのコンテンツタイプ

原文引用:

> **Reference content** adds knowledge Claude applies to your current work. Conventions, patterns, style guides, domain knowledge. This content runs inline so Claude can use it alongside your conversation context.

> **Task content** gives Claude step-by-step instructions for a specific action, like deployments, commits, or code generation. These are often actions you want to invoke directly with `/skill-name` rather than letting Claude decide when to run them. Add `disable-model-invocation: true` to prevent Claude from triggering it automatically.

---

## 14. skill-creatorスキルによるスキル作成ガイダンス

公式リポジトリのskill-creatorスキルには、スキル作成に関する詳細なガイダンスが含まれている。

### 14.1 descriptionの書き方に関する追加ガイダンス

原文引用:
> Note: currently Claude has a tendency to "undertrigger" skills -- to not use them when they'd be useful. To combat this, please make the skill descriptions a little bit "pushy". So for instance, instead of "How to build a simple fast dashboard to display internal Anthropic data.", you might write "How to build a simple fast dashboard to display internal Anthropic data. Make sure to use this skill whenever the user mentions dashboards, data visualization, internal metrics, or wants to display any kind of company data, even if they don't explicitly ask for a 'dashboard.'"

### 14.2 ドメイン別リファレンス整理

原文引用:
> **Domain organization**: When a skill supports multiple domains/frameworks, organize by variant:
> ```
> cloud-deploy/
> ├── SKILL.md (workflow + selection)
> └── references/
>     ├── aws.md
>     ├── gcp.md
>     └── azure.md
> ```
> Claude reads only the relevant reference file.

### 14.3 スキル作成プロセス

原文引用（概要）:
> At a high level, the process of creating a skill goes like this:
> - Decide what you want the skill to do and roughly how it should do it
> - Write a draft of the skill
> - Create a few test prompts and run claude-with-access-to-the-skill on them
> - Help the user evaluate the results both qualitatively and quantitatively
> - Rewrite the skill based on feedback
> - Repeat until you're satisfied
> - Expand the test set and try again at larger scale

---

## 15. 確認できなかった項目

1. **`context: share`** -- 公式ドキュメントに記載なし。`context`フィールドの値は`fork`のみ記載されている。
2. **SKILL.mdの厳密なサイズ制限（バイト/文字数のハードリミット）** -- 500行は推奨値であり、ハードリミットの記載はない。Agent Skills仕様では「< 5000 tokens recommended」。
3. **descriptionの合計文字数制限の厳密な計算式** -- 「コンテキストウィンドウの1%、フォールバック8,000文字」以上の詳細な計算ロジックは記載なし。
4. **`license`, `compatibility`, `metadata`のClaude Codeでの処理** -- Claude Codeのフロントマターリファレンスに記載がなく、Agent Skills仕様側のみで定義。実際のスキルでは`license`が使用されているため、少なくとも無視はされていると推測されるが、公式の明示的な説明はない。
