---
name: cleanup
description: "廃止されたCLAUDE.ndf.mdファイルとインポート行を検出・削除します。CLAUDE.ndf.mdが検出された場合に実行してください。"
disable-model-invocation: true
user-invocable: true
allowed-tools:
  - Bash
  - Read
  - Edit
  - Write
---

# CLAUDE.ndf.md クリーンアップ

CLAUDE.ndf.mdは廃止されました。このスキルは残存するCLAUDE.ndf.mdファイルとインポート行を削除します。

## 手順

### 1. プロジェクトスコープの検出と削除

プロジェクトルート（カレントディレクトリ）で以下を確認:

```bash
# CLAUDE.ndf.md の存在確認
ls -la CLAUDE.ndf.md 2>/dev/null
```

存在する場合:
1. `CLAUDE.ndf.md` を削除: `rm -f CLAUDE.ndf.md`
2. `CLAUDE.md` が存在する場合、`@CLAUDE.ndf.md` を含む行をEdit toolで削除
3. `AGENTS.md` が存在する場合、`@CLAUDE.ndf.md` を含む行をEdit toolで削除

### 2. ユーザースコープの検出と削除

```bash
# ~/.claude/CLAUDE.ndf.md の存在確認
ls -la ~/.claude/CLAUDE.ndf.md 2>/dev/null
```

存在する場合:
1. `~/.claude/CLAUDE.ndf.md` を削除: `rm -f ~/.claude/CLAUDE.ndf.md`
2. `~/.claude/CLAUDE.md` から `@CLAUDE.ndf.md` を含む行をEdit toolで削除

### 3. 結果報告

削除した内容をユーザーに報告:

```
## クリーンアップ結果

### プロジェクトスコープ
- CLAUDE.ndf.md: [削除済み / 未検出]
- CLAUDE.md インポート行: [削除済み / 未検出]
- AGENTS.md インポート行: [削除済み / 未検出]

### ユーザースコープ (~/.claude/)
- CLAUDE.ndf.md: [削除済み / 未検出]
- CLAUDE.md インポート行: [削除済み / 未検出]
```
