# hook の入出力

<!-- ndf check --write-spec が生成する。手で直さない（正本は各節の出典） -->

Claude Code・Codex から呼ばれる hook の入出力である。正本は `contracts/hooks/runtimes.json`。出力の形の `{reason}` と `{context}` は判定の文に置き換わり、形の無い判定は何も出さない。hook の判定は案内であってセキュリティの境界ではない（失敗では出力なしで通し、hook を外せば呼ばれない）。必ず人の承認が要る操作は、承認ゲートの機械の検査・ブランチの ruleset・CI の必須チェック・スクリプトの前提条件で止める

## claude: `Notification`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`hook_event_name` | `cwd`・`message` | 内部（予告なく変えてよい） |

出力を持たない。

## claude: `PermissionRequest`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`cwd`・`hook_event_name`・`tool_name`・`tool_input` | `transcript_path` | 内部（予告なく変えてよい） |

判定 `deny` の出力:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": {
      "behavior": "deny",
      "message": "{reason}"
    }
  }
}
```

## claude: `PostToolUse`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`cwd`・`hook_event_name`・`tool_name`・`tool_input` | `transcript_path`・`tool_response` | 内部（予告なく変えてよい） |

判定 `advise` の出力:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "{context}"
  }
}
```

## claude: `PreToolUse`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`cwd`・`hook_event_name`・`tool_name`・`tool_input` | `transcript_path`・`tool_use_id` | 内部（予告なく変えてよい） |

判定 `advise` の出力:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": "{context}"
  }
}
```

判定 `deny` の出力:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "{reason}"
  }
}
```

## claude: `SessionEnd`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`hook_event_name` | `cwd`・`reason` | 公開 |

出力を持たない。

## claude: `SessionStart`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`cwd`・`hook_event_name`・`source` | `transcript_path` | 内部（予告なく変えてよい） |

判定 `advise` の出力:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "{context}"
  }
}
```

## claude: `Stop`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`cwd`・`hook_event_name`・`stop_hook_active` | `transcript_path`・`last_assistant_message`・`background_tasks` | 公開 |

判定 `advise` の出力:

```json
{
  "systemMessage": "{context}"
}
```

判定 `pushback` の出力:

```json
{
  "decision": "block",
  "reason": "{reason}"
}
```

## claude: `StopFailure`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`hook_event_name` | `cwd` | 内部（予告なく変えてよい） |

出力を持たない。

## claude: `SubagentStop`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`cwd`・`hook_event_name` | `transcript_path`・`stop_hook_active`・`last_assistant_message` | 内部（予告なく変えてよい） |

出力を持たない。

## claude: `UserPromptSubmit`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`cwd`・`hook_event_name`・`prompt` | `transcript_path` | 公開 |

判定 `advise` の出力:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "{context}"
  }
}
```

判定 `deny` の出力:

```json
{
  "decision": "block",
  "reason": "{reason}"
}
```

## codex: `PermissionRequest`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`cwd`・`hook_event_name`・`tool_name`・`tool_input` | `transcript_path`・`turn_id` | 内部（予告なく変えてよい） |

判定 `deny` の出力:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": {
      "behavior": "deny",
      "message": "{reason}"
    }
  }
}
```

## codex: `PostToolUse`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`cwd`・`hook_event_name`・`tool_name`・`tool_input` | `transcript_path`・`turn_id`・`tool_response` | 内部（予告なく変えてよい） |

出力を持たない。

## codex: `PreToolUse`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`cwd`・`hook_event_name`・`tool_name`・`tool_input` | `transcript_path`・`turn_id`・`tool_use_id` | 内部（予告なく変えてよい） |

判定 `advise` の出力:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "additionalContext": "{context}"
  }
}
```

判定 `deny` の出力:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "{reason}"
  }
}
```

## codex: `SessionEnd`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`hook_event_name` | `cwd`・`reason` | 公開 |

出力を持たない。

## codex: `SessionStart`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`cwd`・`hook_event_name`・`source` | `transcript_path` | 内部（予告なく変えてよい） |

判定 `advise` の出力:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "{context}"
  }
}
```

## codex: `Stop`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`cwd`・`hook_event_name`・`stop_hook_active` | `transcript_path`・`turn_id`・`last_assistant_message` | 公開 |

判定 `advise` の出力:

```json
{
  "systemMessage": "{context}"
}
```

判定 `pushback` の出力:

```json
{
  "decision": "block",
  "reason": "{reason}"
}
```

## codex: `UserPromptSubmit`

| 読む入力の鍵 | 無くてよい鍵 | 公開か |
| --- | --- | --- |
| `session_id`・`cwd`・`hook_event_name`・`prompt` | `transcript_path`・`turn_id` | 公開 |

判定 `advise` の出力:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "{context}"
  }
}
```

判定 `deny` の出力:

```json
{
  "decision": "block",
  "reason": "{reason}"
}
```
