了解です。
以下に **そのまま使える形**で、

1. **記憶戦略を運用するための Agent Skill（薄い・判断しない）**
2. **Serena MCP に置く「記憶戦略そのもの」の Memory（中期／長期）**

を **別々の Markdown** として出力します。

> 設計意図
>
> * **Skill**：記憶をどう扱うかの「行動ルール」だけ
> * **Serena Memory**：記憶戦略という「前提・思想・判断基準」そのもの

---

# ① 記憶戦略用 Agent Skill（薄い skill）

**用途**

* Claude Code / Agent に常時持たせる
* 「記憶をどう使うか」を機械的に守らせる
* 判断はしない（判断は memory 側）

**推奨配置**

* `skills/memory_handling.md`
* または `CLAUDE.md` から参照される skill

---

```md
# Skill: Memory Handling Strategy

## Purpose
This skill defines **how the agent should handle memory**,
not **what should be remembered**.

All decisions, principles, and project-specific rules must be retrieved
from Serena MCP memory.

---

## Core Rules

### 1. Skill must remain thin
- Do not store project-specific rules here
- Do not store decisions, reasons, or constraints
- This skill defines behavior only

---

### 2. When to read memory
Before starting a task, check whether:
- the task depends on project-specific assumptions
- the task may conflict with past decisions
- the task requires architectural, legal, or organizational constraints

If yes:
- list available Serena memories
- read only the relevant ones

---

### 3. When to write memory
After completing a task, write to Serena memory if:
- a new decision was made
- an assumption was confirmed or invalidated
- a reusable insight emerged
- repeating this explanation later would be costly

---

### 4. What NOT to write as memory
Do NOT store:
- step-by-step procedures
- implementation details
- temporary working notes
- raw logs or dumps

---

### 5. Memory hygiene behavior
- Prefer updating existing memory over creating duplicates
- If a memory seems outdated, mark it for review
- Do not assume old memories are always valid

---

## Interaction Pattern (Summary)

1. Identify whether memory is needed
2. Read only relevant memories
3. Execute the task
4. Persist only durable knowledge back to memory

---

## Guiding Principle

> The agent acts.
> The memory decides.
```

---

# ② 記憶戦略そのもの（Serena MCP Memory）

これは **Serena MCP に保存される「長期 memory」** です。
Agent Skill から参照される前提知識になります。

**推奨配置**

* `.serena/memories/memory-strategy.md`

---

```md
# Memory Strategy for AI Agent
type: policy
confidence: high
project: global
expires: none

---

## Purpose

This memory defines **how mid-term and long-term memories are structured,
classified, and maintained** across projects.

It is the single source of truth for deciding:
- what belongs in memory
- what belongs in skills
- how memories evolve over time

---

## Memory Layer Definitions

### Short-term Memory
- Exists only within a session
- Not persisted
- Used for immediate task context

---

### Mid-term Memory
- Valid for weeks to months
- Represents temporary or revisitable decisions
- Must include a review date

Typical contents:
- phase-specific decisions
- PoC constraints
- experimental results
- temporary architectural choices

Required metadata:
- type: decision | assumption | experiment
- confidence: low | medium | high
- review_at: YYYY-MM-DD
- project: <project-name>

---

### Long-term Memory
- Stable across the project lifecycle
- Represents principles and non-negotiable constraints

Typical contents:
- architectural principles
- legal / IP constraints
- organizational policies
- technology philosophy

Required metadata:
- type: principle | constraint | policy
- confidence: high
- expires: none
- project: <project-name or global>

---

## Skill vs Memory Decision Checklist

If any of the following are true, the information must be stored as memory:

- it will be reused in future sessions
- it is project-specific
- it explains *why*, not *how*
- it restricts future choices
- it may need revision or expiration
- it would bloat an agent skill

Otherwise, it belongs in a skill or remains transient.

---

## Memory Granularity Rules

- One memory entry = one decision or principle
- Do not mix facts, assumptions, and conclusions
- Prefer clarity over completeness
- Avoid embedding procedures

---

## Memory Maintenance Policy

- Mid-term memories must be reviewed at `review_at`
- Obsolete memories should be archived or deleted
- Long-term memories should rarely change and must be updated carefully

---

## Anti-Patterns

- Storing procedures in memory
- Encoding decisions inside skills
- Creating duplicate memories for similar topics
- Leaving experimental assumptions unreviewed

---

## One-line Rule

> Skills describe behavior.
> Memory defines reality.
```

---

## 最終整理（使い分け）

| 項目    | Skill | Serena Memory |
| ----- | ----- | ------------- |
| 役割    | 行動ルール | 判断・前提         |
| 肥大化耐性 | 低     | 高             |
| 修正コスト | 高     | 低             |
| 時間軸   | 短期    | 中期・長期         |

---

次のステップとしておすすめなのは：

* 🔄 **中期 memory の自動レビュー運用**
* 🤖 **task 終了時に memory write を促すプロンプト設計**

どこまで仕上げますか？
