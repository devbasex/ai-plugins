# Optional NDF Skills

These skills remain in `../skills/` but are not exposed by default in at least one target runtime.

Add a skill implementation under `../skills/`, then expose it in `.claude-plugin/plugin.json` or `skills-codex/` only when it is actively needed by that runtime.

## Optional Skills

- `data-analyst-export`
- `data-analyst-sql-optimization`
- `deepwiki-transfer`
- `gemini`
- `google-auth`
- `google-chat`
- `google-drive`
- `knowledge-reorg`
- `mcp-builder`
- `ml-model-structure`
- `official-skills-autoloader`
- `playwright-browser-connect`
- `playwright-evidence-drive`
- `playwright-scenario-test`
- `qa-security-scan`
- `skill-stats`

## Codex Exclusions

These Claude-specific or self-delegation skills are intentionally not exposed in Codex:

- `browser-test`
- `codex`
- `statusline`

## Claude/Kiro Exclusions

These minimal Playwright skills are exposed in Codex but kept optional for Claude Code and Kiro by default:

- `playwright-test-planning`
- `playwright-script-creation`
- `playwright-execution`
- `playwright-kit-ops`
- `playwright-report`
