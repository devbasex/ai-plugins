以下は **AI Agent（Claude Code / Codex CLI / Gemini CLI / Kiro 等）に直接読ませることを前提にした方針ドキュメント**です。
目的は **AGENTS.md を軽量化しつつ、Serena memory から移行した知識を整理するための標準ルール**を定義することです。

---

# AI Agent Knowledge Architecture Policy

## Purpose

This document defines how knowledge and operational instructions for AI agents are organized within this repository.

The goal is to ensure:

* predictable behavior across multiple AI agents
* minimal context usage
* compatibility with different agent environments
* maintainable long-term documentation

This policy applies to all agents including:

* Claude Code
* Codex CLI
* Gemini CLI
* Kiro
* other MCP-compatible agents

---

# Core Principle

Knowledge must be separated into the following layers:

```
AGENTS.md      → navigation + policy
docs/          → repository knowledge
skills/        → executable workflows for agents
src/           → source code
```

Agents must **not treat AGENTS.md as a full knowledge base**.
AGENTS.md is only an **entry point**.

---

# Knowledge Layer Responsibilities

## AGENTS.md

AGENTS.md is the **entry document for AI agents**.

It must contain only:

* repository overview
* navigation to documentation
* agent behavior rules
* references to skills

AGENTS.md must remain **small and lightweight**.

Recommended size:

* under 300 lines
* ideally under 1500 tokens

AGENTS.md must NOT contain:

* detailed architecture
* database schemas
* long explanations
* operational procedures

Those belong in the docs or skills directories.

---

## docs/

The docs directory contains **repository knowledge**.

This is the canonical location for information previously stored in:

```
.serena/memories/
```

Examples:

```
docs/
  architecture/
  database/
  modules/
  repo-structure.md
  dependencies.md
```

Content suitable for docs:

* system architecture
* repository structure
* module explanations
* dependency relationships
* infrastructure overview
* design philosophy

Docs are written for both humans and AI agents.

Agents may read docs when they need deeper context.

---

## skills/

Skills define **agent execution procedures**.

A skill is used when the agent needs to perform a structured task.

Examples:

```
.claude/skills/
  deploy-service.md
  run-migrations.md
  review-pull-request.md
```

Skill content typically includes:

* step-by-step instructions
* commands to execute
* validation rules
* checklists

Skills should not contain general architecture explanations.

---

# Migration from Serena Memory

Knowledge stored in:

```
.serena/memories/
```

must be redistributed according to the following rules.

| Memory Type              | Destination |
| ------------------------ | ----------- |
| Repository structure     | docs        |
| Architecture explanation | docs        |
| Module descriptions      | docs        |
| Dependencies             | docs        |
| Agent procedures         | skills      |
| Temporary investigations | remove      |
| Task history             | remove      |

Agents must not rely on `.serena/memories`.

---

# Agent Reading Strategy

Agents should follow this reading order when working in the repository.

```
1. AGENTS.md
2. relevant docs
3. skills (if a workflow is required)
4. source code
```

Agents should **not load the entire docs directory unnecessarily**.

Agents should read only relevant documentation sections.

---

# Multi-Agent Compatibility

All documentation must remain compatible with multiple agents.

Therefore:

* avoid tool-specific formats
* avoid Serena-specific knowledge storage
* prefer plain Markdown
* prefer stable directory structures

This ensures the repository works consistently with:

* Claude Code
* Codex CLI
* Gemini CLI
* Kiro
* future agent systems

---

# Serena MCP Usage

Serena MCP is allowed only for:

* LSP features
* symbol search
* semantic code navigation
* safe refactoring

Serena memory features must not be used.

Knowledge must remain in:

```
docs/
skills/
```

This prevents knowledge fragmentation across agent tools.

---

# Documentation Index

The following documents provide deeper repository knowledge.

```
docs/repo-structure.md
docs/architecture/
docs/database/
docs/modules/
docs/infrastructure/
```

Agents should consult these documents when architectural understanding is required.

---

# Summary

This repository follows a layered knowledge architecture:

```
AGENTS.md → navigation and policy
docs/     → repository knowledge
skills/   → executable workflows
src/      → implementation
```

This structure ensures:

* stable agent behavior
* reduced context usage
* long-term maintainability
* compatibility with multiple AI development tools

Agents must respect this structure when reading or writing repository knowledge.


---


以下は **AI Agent（Claude Code / Codex CLI / Gemini CLI / Kiro 等）が最初に読むことを想定した軽量な `AGENTS.md` テンプレート**です。
前のポリシー（AI Agent Knowledge Architecture Policy）に従い、**インデックス＋行動ルールのみ**にしています。

---

# AGENTS.md

## Purpose

This file is the entry point for AI agents working in this repository.

Agents must use this document to understand:

* repository purpose
* where knowledge is located
* how to behave when modifying code
* which skills to use

This file is intentionally lightweight.

Detailed documentation exists in the `docs/` directory.

---

# Project Overview

This repository contains the implementation of the system.

Primary responsibilities of the repository may include:

* application logic
* infrastructure definitions
* database schema
* operational tooling

Agents should read the relevant documentation before performing major modifications.

See:

```text
docs/architecture/
docs/repo-structure.md
```

---

# Repository Structure

High level structure:

```text
src/        → source code
docs/       → repository knowledge
.claude/    → Claude Code skills
.codex/     → Codex CLI skills
infra/      → infrastructure definitions
scripts/    → operational scripts
```

Agents should explore directories before editing files.

See detailed explanation:

```
docs/repo-structure.md
```

---

# Knowledge Architecture

This repository separates knowledge into layers.

```text
AGENTS.md   → navigation + policy
docs/       → repository knowledge
skills/     → executable workflows
src/        → implementation
```

Agents must **not add detailed knowledge to AGENTS.md**.

New documentation should be written in:

```
docs/
```

Operational procedures should be written as:

```
.claude/skills/
```

---

# Agent Behavior Rules

Agents must follow these rules when modifying the repository.

### Read before modifying

Before implementing changes, agents should read:

* relevant documentation in `docs/`
* related source code
* existing implementation patterns

---

### Prefer existing patterns

When implementing new code:

* follow the architecture described in `docs/architecture`
* reuse existing modules
* avoid introducing new patterns without justification

---

### Avoid large refactors unless required

Agents should avoid:

* large-scale refactors
* architectural rewrites
* dependency changes

unless explicitly requested.

---

### Keep changes minimal

Agents should:

* modify only the necessary files
* avoid unrelated changes
* preserve formatting and coding style

---

# Documentation Index

Agents may consult the following documents for deeper understanding.

## Architecture

```
docs/architecture/
```

Contains system design and component relationships.

---

## Repository Structure

```
docs/repo-structure.md
```

Explains the purpose of each major directory.

---

## Modules

```
docs/modules/
```

Describes internal modules and responsibilities.

---

## Database

```
docs/database/
```

Contains schema explanations and data flow.

---

# Skills

Operational workflows are implemented as skills.

Examples:

```
.claude/skills/deploy-service.md
.claude/skills/run-migrations.md
.claude/skills/review-pull-request.md
```

Agents should prefer using skills when performing structured tasks.

---

# Serena MCP Usage

Serena MCP may be used for:

* symbol search
* reference search
* semantic navigation
* safe refactoring

Serena memory must not be used.

Repository knowledge must remain in:

```
docs/
skills/
```

---

# Reading Strategy for Agents

Agents should read repository knowledge in the following order.

```text
1. AGENTS.md
2. relevant docs
3. skills (if needed)
4. source code
```

Agents should avoid loading unnecessary documentation.

---

# When Documentation is Missing

If the required knowledge does not exist:

Agents may create new documentation in:

```
docs/
```

Do not add long explanations to AGENTS.md.

---

# Summary

AGENTS.md acts only as a navigation entry point.

```text
AGENTS.md → navigation
docs/     → knowledge
skills/   → workflows
src/      → code
```

Agents must respect this structure to ensure compatibility with:

* Claude Code
* Codex CLI
* Gemini CLI
* Kiro
* future AI development agents

---

💡補足（重要）

このテンプレートの狙いは次の3つです。

1️⃣ **AGENTS.mdを軽量に保つ（~500〜1200 tokens）**
2️⃣ **docsを知識ベースにする**
3️⃣ **skillsを実行手順にする**

この構造にすると、あなたが使っている

* Claude Code
* Codex CLI
* Gemini CLI
* Kiro
* MCP

すべてで **最も安定して動くリポジトリ構造**になります。

---

もしよければ次に、
**Serena memory → docs へ移すときの「最適ディレクトリ構造」**も作ります。

これは実はかなり重要で、
**AIのコード理解速度が2〜5倍くらい変わることがあります。**
