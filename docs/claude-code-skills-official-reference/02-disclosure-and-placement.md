# Claude Code Skills 公式ドキュメント詳細調査レポート — Progressive Disclosure と配置場所

**この調査は 3 本に分かれている。**

- [フロントマターと description](01-frontmatter-and-description.md)
- [Progressive Disclosure と配置場所](02-disclosure-and-placement.md)
- [Context Budget と呼び出し制御](03-budget-and-invocation.md)

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
