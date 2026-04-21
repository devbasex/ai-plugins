# Claude Code Skills 公式ドキュメント詳細調査レポート

調査日: 2026-04-03

調査元URL:
- https://code.claude.com/docs/en/skills (メインSkillsドキュメント)
- https://code.claude.com/docs/en/plugins (プラグインドキュメント)
- https://code.claude.com/docs/en/hooks (Hooksドキュメント)
- https://code.claude.com/docs/en/sub-agents (サブエージェントドキュメント)
- https://github.com/anthropics/skills (公式Skillsリポジトリ)
- https://agentskills.io/specification (Agent Skills仕様)

---

## 1. YAMLフロントマターの全フィールド

### 1.1 Claude Code固有フィールド一覧

公式ドキュメント（code.claude.com/docs/en/skills）の「Frontmatter reference」テーブルから原文引用。

> All fields are optional. Only `description` is recommended so Claude knows when to use the skill.

| Field | Required | Description (原文) |
|:------|:---------|:-------------------|
| `name` | No | Display name for the skill. If omitted, uses the directory name. Lowercase letters, numbers, and hyphens only (max 64 characters). |
| `description` | Recommended | What the skill does and when to use it. Claude uses this to decide when to apply the skill. If omitted, uses the first paragraph of markdown content. Front-load the key use case: descriptions longer than 250 characters are truncated in the skill listing to reduce context usage. |
| `argument-hint` | No | Hint shown during autocomplete to indicate expected arguments. Example: `[issue-number]` or `[filename] [format]`. |
| `disable-model-invocation` | No | Set to `true` to prevent Claude from automatically loading this skill. Use for workflows you want to trigger manually with `/name`. Default: `false`. |
| `user-invocable` | No | Set to `false` to hide from the `/` menu. Use for background knowledge users shouldn't invoke directly. Default: `true`. |
| `allowed-tools` | No | Tools Claude can use without asking permission when this skill is active. Accepts a space-separated string or a YAML list. |
| `model` | No | Model to use when this skill is active. |
| `effort` | No | Effort level when this skill is active. Overrides the session effort level. Default: inherits from session. Options: `low`, `medium`, `high`, `max` (Opus 4.6 only). |
| `context` | No | Set to `fork` to run in a forked subagent context. |
| `agent` | No | Which subagent type to use when `context: fork` is set. |
| `hooks` | No | Hooks scoped to this skill's lifecycle. See Hooks in skills and agents for configuration format. |
| `paths` | No | Glob patterns that limit when this skill is activated. Accepts a comma-separated string or a YAML list. When set, Claude loads the skill automatically only when working with files matching the patterns. Uses the same format as path-specific rules. |
| `shell` | No | Shell to use for `` !`command` `` blocks in this skill. Accepts `bash` (default) or `powershell`. Setting `powershell` runs inline shell commands via PowerShell on Windows. Requires `CLAUDE_CODE_USE_POWERSHELL_TOOL=1`. |

### 1.2 Agent Skills仕様（agentskills.io）のフィールド

Agent Skills仕様はオープンスタンダードとして定義。Claude Codeはこの仕様を拡張している。

| Field | Required | Constraints (原文) |
|:------|:---------|:-------------------|
| `name` | Yes | Max 64 characters. Lowercase letters, numbers, and hyphens only. Must not start or end with a hyphen. |
| `description` | Yes | Max 1024 characters. Non-empty. Describes what the skill does and when to use it. |
| `license` | No | License name or reference to a bundled license file. |
| `compatibility` | No | Max 500 characters. Indicates environment requirements (intended product, system packages, network access, etc.). |
| `metadata` | No | Arbitrary key-value mapping for additional metadata. |
| `allowed-tools` | No | Space-delimited list of pre-approved tools the skill may use. (Experimental) |

**重要な差異**: Agent Skills仕様では`name`と`description`は**必須(Yes)**だが、Claude Code実装では`name`は**任意(No)**（ディレクトリ名をフォールバック）、`description`は**Recommended**。

### 1.3 Claude Code固有の拡張フィールド（Agent Skills仕様にないもの）

- `argument-hint`
- `disable-model-invocation`
- `user-invocable`
- `model`
- `effort`
- `context`
- `agent`
- `hooks`
- `paths`
- `shell`

原文引用:
> Claude Code skills follow the Agent Skills open standard, which works across multiple AI tools. Claude Code extends the standard with additional features like invocation control, subagent execution, and dynamic context injection.

### 1.4 Agent Skills仕様にのみ存在するフィールド

以下はAgent Skills仕様で定義されているが、Claude Codeのフロントマターリファレンスには記載されていない:

- `license`
- `compatibility`
- `metadata`

ただし、公式リポジトリ（anthropics/skills）の実際のスキルでは`license`が使用されている。

---

## 2. description の書き方

### 2.1 文字数制限

原文引用（Claude Code公式）:
> Front-load the key use case: descriptions longer than 250 characters are truncated in the skill listing to reduce context usage.

原文引用（Agent Skills仕様）:
> Must be 1-1024 characters

要約: description自体は最大1024文字まで書けるが、Claude Codeのスキルリスト表示では**250文字で切り詰められる**。重要な用途は先頭に書くべき。

### 2.2 公式ドキュメントの具体例

**Agent Skills仕様の良い例:**
```yaml
description: Extracts text and tables from PDF files, fills PDF forms, and merges multiple PDFs. Use when working with PDF documents or when the user mentions PDFs, forms, or document extraction.
```

**Agent Skills仕様の悪い例:**
```yaml
description: Helps with PDFs.
```

**Claude Code公式ドキュメントの例:**
```yaml
description: Explains code with visual diagrams and analogies. Use when explaining how code works, teaching about a codebase, or when the user asks "how does this work?"
```
```yaml
description: API design patterns for this codebase
```
```yaml
description: Deploy the application to production
```
```yaml
description: Fix a GitHub issue
```
```yaml
description: Summarize changes in a pull request
```
```yaml
description: Research a topic thoroughly
```
```yaml
description: Read files without making changes
```
```yaml
description: Perform operations with security checks
```
```yaml
description: Generate an interactive collapsible tree visualization of your codebase. Use when exploring a new repo, understanding project structure, or identifying large files.
```

### 2.3 公式リポジトリ（anthropics/skills）の実例

```yaml
# skill-creator
description: Create new skills, modify and improve existing skills, and measure
  skill performance. Use when users want to create a skill from scratch, edit,
  or optimize an existing skill, run evals to test a skill, benchmark skill
  performance with variance analysis, or optimize a skill's description for
  better triggering accuracy.
```

```yaml
# pdf
description: Use this skill whenever the user wants to do anything with PDF files.
  This includes reading or extracting text/tables from PDFs, combining or merging
  multiple PDFs into one, splitting PDFs apart, rotating pages, adding watermarks,
  creating new PDFs, filling PDF forms, encrypting/decrypting PDFs, extracting
  images, and OCR on scanned PDFs to make them searchable. If the user mentions
  a .pdf file or asks to produce one, use this skill.
```

```yaml
# claude-api（TRIGGER/DO NOT TRIGGERパターン）
description: "Build apps with the Claude API or Anthropic SDK. TRIGGER when: code
  imports `anthropic`/`@anthropic-ai/sdk`/`claude_agent_sdk`, or user asks to use
  Claude API, Anthropic SDKs, or Agent SDK. DO NOT TRIGGER when: code imports
  `openai`/other AI SDK, general programming, or ML/data-science tasks."
```

```yaml
# webapp-testing
description: Toolkit for interacting with and testing local web applications using
  Playwright. Supports verifying frontend functionality, debugging UI behavior,
  capturing browser screenshots, and viewing browser logs.
```

```yaml
# mcp-builder
description: Guide for creating high-quality MCP (Model Context Protocol) servers
  that enable LLMs to interact with external services through well-designed tools.
  Use when building MCP servers to integrate external APIs or services, whether in
  Python (FastMCP) or Node/TypeScript (MCP SDK).
```

```yaml
# web-artifacts-builder
description: Suite of tools for creating elaborate, multi-component claude.ai HTML
  artifacts using modern frontend web technologies (React, Tailwind CSS, shadcn/ui).
  Use for complex artifacts requiring state management, routing, or shadcn/ui
  components - not for simple single-file HTML/JSX artifacts.
```

### 2.4 「Use when」パターンの公式推奨度

公式ドキュメント原文:
> the `description` helps Claude decide when to load it automatically.

> Check the description includes keywords users would naturally say

Agent Skills仕様原文:
> Should describe both what the skill does and when to use it

> Should include specific keywords that help agents identify relevant tasks

skill-creatorスキル内のガイダンス（公式リポジトリ）:
> This is the primary triggering mechanism - include both what the skill does AND specific contexts for when to use it. All "when to use" info goes here, not in the body.

> currently Claude has a tendency to "undertrigger" skills -- to not use them when they'd be useful. To combat this, please make the skill descriptions a little bit "pushy".

**結論**: 「Use when」パターンは公式として強く推奨されている。descriptionには「何をするか」と「いつ使うか」の両方を含めるべき。Claudeはスキルを使い損ねる傾向があるため、少し積極的な記述が推奨される。

---

## 3. Progressive Disclosure

### 3.1 3層構造

**Agent Skills仕様（agentskills.io）原文引用:**
> Skills should be structured for efficient use of context:
> 1. **Metadata** (~100 tokens): The `name` and `description` fields are loaded at startup for all skills
> 2. **Instructions** (< 5000 tokens recommended): The full `SKILL.md` body is loaded when the skill is activated
> 3. **Resources** (as needed): Files (e.g. those in `scripts/`, `references/`, or `assets/`) are loaded only when required

**skill-creatorスキル（公式リポジトリ）原文引用:**
> Skills use a three-level loading system:
> 1. **Metadata** (name + description) - Always in context (~100 words)
> 2. **SKILL.md body** - In context whenever skill triggers (<500 lines ideal)
> 3. **Bundled resources** - As needed (unlimited, scripts can execute without loading)
>
> These word counts are approximate and you can feel free to go longer if needed.

**Claude Code公式ドキュメント原文引用:**
> In a regular session, skill descriptions are loaded into context so Claude knows what's available, but full skill content only loads when invoked. Subagents with preloaded skills work differently: the full skill content is injected at startup.

### 3.2 500行制限の根拠

3つの情報源で一貫して記載:

公式ドキュメント:
> Keep `SKILL.md` under 500 lines. Move detailed reference material to separate files.

Agent Skills仕様:
> Keep your main `SKILL.md` under 500 lines. Move detailed reference material to separate files.

skill-creatorスキル:
> Keep SKILL.md under 500 lines; if you're approaching this limit, add an additional layer of hierarchy along with clear pointers about where the model using the skill should go next to follow up.

**結論**: 500行はハードリミットではなく推奨値（"ideal"、"recommended"）。3つの公式ソースで一貫している。

### 3.3 Supporting filesの扱い

原文引用:
> Skills can include multiple files in their directory. This keeps `SKILL.md` focused on the essentials while letting Claude access detailed reference material only when needed. Large reference docs, API specifications, or example collections don't need to load into context every time the skill runs.

> Reference supporting files from `SKILL.md` so Claude knows what each file contains and when to load it:
> ```markdown
> ## Additional resources
> - For complete API details, see [reference.md](reference.md)
> - For usage examples, see [examples.md](examples.md)
> ```

推奨ディレクトリ構造（Claude Code公式）:
```
my-skill/
├── SKILL.md (required - overview and navigation)
├── reference.md (detailed API docs - loaded when needed)
├── examples.md (usage examples - loaded when needed)
└── scripts/
    └── helper.py (utility script - executed, not loaded)
```

推奨ディレクトリ構造（Agent Skills仕様）:
```
skill-name/
├── SKILL.md          # Required: metadata + instructions
├── scripts/          # Optional: executable code
├── references/       # Optional: documentation
├── assets/           # Optional: templates, resources
└── ...               # Any additional files or directories
```

Agent Skills仕様の補足:
> Keep file references one level deep from `SKILL.md`. Avoid deeply nested reference chains.

skill-creatorスキルの補足:
> For large reference files (>300 lines), include a table of contents

---

## 4. 動的コンテンツ

### 4.1 `` !`command` `` 構文（シェルコマンド前処理）

原文引用:
> The `` !`<command>` `` syntax runs shell commands before the skill content is sent to Claude. The command output replaces the placeholder, so Claude receives actual data, not the command itself.

> This is preprocessing, not something Claude executes. Claude only sees the final result.

実行フロー原文引用:
> When this skill runs:
> 1. Each `` !`<command>` `` executes immediately (before Claude sees anything)
> 2. The output replaces the placeholder in the skill content
> 3. Claude receives the fully-rendered prompt with actual PR data

公式例:
```yaml
---
name: pr-summary
description: Summarize changes in a pull request
context: fork
agent: Explore
allowed-tools: Bash(gh *)
---

## Pull request context
- PR diff: !`gh pr diff`
- PR comments: !`gh pr view --comments`
- Changed files: !`gh pr diff --name-only`

## Your task
Summarize this pull request...
```

`shell`フィールドとの関連:
> Shell to use for `` !`command` `` blocks in this skill. Accepts `bash` (default) or `powershell`.

### 4.2 文字列置換変数

原文引用（Available string substitutions テーブル）:

| Variable | Description (原文) |
|:---------|:-------------------|
| `$ARGUMENTS` | All arguments passed when invoking the skill. If `$ARGUMENTS` is not present in the content, arguments are appended as `ARGUMENTS: <value>`. |
| `$ARGUMENTS[N]` | Access a specific argument by 0-based index, such as `$ARGUMENTS[0]` for the first argument. |
| `$N` | Shorthand for `$ARGUMENTS[N]`, such as `$0` for the first argument or `$1` for the second. |
| `${CLAUDE_SESSION_ID}` | The current session ID. Useful for logging, creating session-specific files, or correlating skill output with sessions. |
| `${CLAUDE_SKILL_DIR}` | The directory containing the skill's `SKILL.md` file. For plugin skills, this is the skill's subdirectory within the plugin, not the plugin root. Use this in bash injection commands to reference scripts or files bundled with the skill, regardless of the current working directory. |

引数未使用時の挙動:
> If you invoke a skill with arguments but the skill doesn't include `$ARGUMENTS`, Claude Code appends `ARGUMENTS: <your input>` to the end of the skill content so Claude still sees what you typed.

公式例（位置引数）:
```yaml
---
name: migrate-component
description: Migrate a component from one framework to another
---
Migrate the $ARGUMENTS[0] component from $ARGUMENTS[1] to $ARGUMENTS[2].
Preserve all existing behavior and tests.
```

ショートハンド:
```yaml
Migrate the $0 component from $1 to $2.
```

> Running `/migrate-component SearchBar React Vue` replaces `$ARGUMENTS[0]` with `SearchBar`, `$ARGUMENTS[1]` with `React`, and `$ARGUMENTS[2]` with `Vue`.

### 4.3 Extended Thinking の有効化

原文引用:
> To enable extended thinking (thinking mode) in a skill, include the word "ultrathink" anywhere in your skill content.

---

## 5. スキルの配置場所

### 5.1 配置場所と優先順位

原文引用:

| Location | Path | Applies to |
|:---------|:-----|:-----------|
| Enterprise | See managed settings | All users in your organization |
| Personal | `~/.claude/skills/<skill-name>/SKILL.md` | All your projects |
| Project | `.claude/skills/<skill-name>/SKILL.md` | This project only |
| Plugin | `<plugin>/skills/<skill-name>/SKILL.md` | Where plugin is enabled |

> When skills share the same name across levels, higher-priority locations win: enterprise > personal > project. Plugin skills use a `plugin-name:skill-name` namespace, so they cannot conflict with other levels.

> If you have files in `.claude/commands/`, those work the same way, but if a skill and a command share the same name, the skill takes precedence.

### 5.2 ネストされたディレクトリの自動検出

原文引用:
> When you work with files in subdirectories, Claude Code automatically discovers skills from nested `.claude/skills/` directories. For example, if you're editing a file in `packages/frontend/`, Claude Code also looks for skills in `packages/frontend/.claude/skills/`. This supports monorepo setups where packages have their own skills.

### 5.3 --add-dir での例外的動作

原文引用:
> The `--add-dir` flag grants file access rather than configuration discovery, but skills are an exception: `.claude/skills/` within an added directory is loaded automatically and picked up by live change detection, so you can edit those skills during a session without restarting.

> Other `.claude/` configuration such as subagents, commands, and output styles is not loaded from additional directories.

### 5.4 .claude/commands/ との互換性

原文引用:
> Custom commands have been merged into skills. A file at `.claude/commands/deploy.md` and a skill at `.claude/skills/deploy/SKILL.md` both create `/deploy` and work the same way. Your existing `.claude/commands/` files keep working. Skills add optional features: a directory for supporting files, frontmatter to control whether you or Claude invokes them, and the ability for Claude to load them automatically when relevant.

---

## 6. 公式リポジトリの実例

### 6.1 リポジトリ概要

https://github.com/anthropics/skills

原文引用:
> This repository contains skills that demonstrate what's possible with Claude's skills system. These skills range from creative applications (art, music, design) to technical tasks (testing web apps, MCP server generation) to enterprise workflows (communications, branding, etc.).

### 6.2 スキル一覧

```
skills/
  algorithmic-art/    brand-guidelines/   canvas-design/
  claude-api/         doc-coauthoring/    docx/
  frontend-design/    internal-comms/     mcp-builder/
  pdf/                pptx/               skill-creator/
  slack-gif-creator/  theme-factory/      web-artifacts-builder/
  webapp-testing/     xlsx/
```

### 6.3 テンプレート

公式テンプレート（template/SKILL.md）:
```yaml
---
name: template-skill
description: Replace with description of the skill and when Claude should use it.
---

# Insert instructions below
```

### 6.4 Agent Skills仕様の場所

spec/agent-skills-spec.md の内容:
> The spec is now located at https://agentskills.io/specification

### 6.5 インストール方法

原文引用:
> You can register this repository as a Claude Code Plugin marketplace by running:
> ```
> /plugin marketplace add anthropics/skills
> ```

---

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
