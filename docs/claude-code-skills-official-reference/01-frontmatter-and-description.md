# Claude Code Skills 公式ドキュメント詳細調査レポート

**この調査は 3 本に分かれている。**

- [フロントマターと description](01-frontmatter-and-description.md)
- [Progressive Disclosure と配置場所](02-disclosure-and-placement.md)
- [Context Budget と呼び出し制御](03-budget-and-invocation.md)

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
