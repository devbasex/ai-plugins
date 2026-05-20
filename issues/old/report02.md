結論：
**はい、あります。プラグイン側で「親エージェント向けのシステム指示書」を定義できます。
ただし名前は *CLAUDE.md* ではなく、`agents/` ディレクトリに置く「エージェント定義ファイル」として提供します。**

つまり、**プラグイン経由で “親エージェントの振る舞い” を決めることが可能**です。

以下、公式仕様に基づいて整理します。

---

# ✅ 結論：plugin で「親エージェントへの指示書」を定義できる

Claude Code の plugin では、次のものをバンドルできます：

* **サブエージェント（agents/）**
* **hooks**
* tools / MCP / commands
* skills
* etc.

このうち **エージェント定義ファイル（= エージェントのシステムプロンプト）を plugin 側で持たせられる**ので、
事実上 **「plugin 内に CLAUDE.md 相当の指示書」を同梱できます。**

---

# 📌 仕組み：plugin の `agents/` ディレクトリに “親エージェント” を定義する

プラグイン側では、次のような構造を取れます：

```
my-plugin/
  .claude-plugin/
    plugin.json
  agents/
    main.md          ← 親エージェント (CLAUDE.md 相当)
    data-analyst.md  ← サブエージェント
  mcp.json
  commands/
  hooks/
```

`agents/main.md` の内容に、**親エージェントのシステムプロンプトを直接書けます**。

例：

```markdown
---
name: main
description: >
  親エージェント。BigQuery/dbhub のようなデータ取得タスクは
  必ず data-analyst サブエージェントに依頼する。
  自分で MCP ツールは直接使用しないこと。
---

あなたはプロジェクト全体の調整役として行動します。

# 禁止事項
- BigQuery MCP を直接呼び出してはならない
- dbhub MCP を直接呼び出してはならない
- データ取得や SQL 実行はすべて data-analyst へ委譲すること

# 指示
- データ取得が必要な場合、「data-analyst を呼び出して ○○ を集計して」と明示する。
- data-analyst から返ってきた要約のみを扱い、自分のコンテキストに生データを保持しない。
```

---

# 📌 重要：plugin で定義したエージェントは「プロジェクトに自動追加」される

公式ドキュメントの該当部分（Plugins → agents）では：

* **plugin の agents/ 内に配置したエージェントは「そのプロジェクトで利用可能なエージェント」として追加される**
* name, description, tools, model が指定できる
* tools 範囲を制限できる（→ data-analyst だけ BigQuery MCP を持たせるのはこの仕組み）

と説明されています。

つまり **plugin が “エージェント定義 + ルール” をプロジェクトに差し込む仕組み**があるわけです。

---

# 💡 実際にどうなるか？

### 👇 あなたの目的は：

> 親エージェントから BigQuery/dbhub MCP を見えなくしたい
> （少なくとも、触らせないようにしたい）

### Plugin 構成で実現できること：

* data-analyst サブエージェント → tools に BigQuery/dbhub MCP を列挙
* 親エージェント → tools に BigQuery/dbhub MCP を含めない
* 親エージェントの agent 定義（main.md）に
  **「データ操作はすべて data-analyst に委譲する」**と明記
* plugin インストール時にこの main.md を自動適用

→ **実質的に CLAUDE.md と同じことが plugin 内に含められる**。

これは Claude Code plugin の設計上、正式にサポートされている構成です。

---

# 📝 plugin.json の例

```jsonc
{
  "name": "team-data-workflow",
  "version": "1.0.0",
  "description": "データ分析専用サブエージェント付きワークフロープラグイン",
  "agents": "./agents",
  "mcpServers": "./mcp.json"
}
```

この構成なら、plugin を入れたプロジェクトでは：

* 親エージェント（main）
* data-analyst

が自動で追加され、
「親には BigQuery MCP を触らせない」
という規律が **plugin で一元管理**できます。

---

# 🎯 まとめ

### ✔ plugin で CLAUDE.md と同等の「指示書」を定義できます

→ それは `agents/main.md` という形で表現される

### ✔ 親エージェント専用のプロンプトを書ける

→ BigQuery/dbhub MCP に触らない規律を plugin が強制可能

### ✔ data-analyst には BigQuery/dbhub MCP ツールのみ付与

→ 親とサブで明確に役割分離できる

---

# 📘 必要があれば…

* あなたの plugin 構成（ファイル名や MCP の数）に合わせて
  　**完全な plugin テンプレ構成**
  　**main.md / data-analyst.md の最適化**
  　**tools のリストの正しい記載方法**
  を一式生成できます。

ファイル構成を少し教えていただければ、すぐに最適解を組み上げます。
