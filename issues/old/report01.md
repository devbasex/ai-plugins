plugin 経由で MCP サーバを定義する場合は、**「プラグインの中に `.mcp.json` か `mcpServers` 設定を書く」**というのが公式のやり方です。
公式 Plugins Reference をそのままかみ砕いて説明します。([Claude Code][1])

---

## 1. 全体像：プラグインと MCP サーバの関係

Claude Code のプラグインは

* commands（スラッシュコマンド）
* agents（サブエージェント）
* skills
* hooks
* **MCP servers**

を「1パッケージ」にまとめたものです。([Claude Code][1])

**MCP サーバをプラグインに束ねると**：

* プラグインを有効にした時点で MCP サーバが自動起動される
* その MCP が提供するツールが Claude のツール一覧に出てくる
* 個々のユーザーが `.mcp.json` を手で配布しなくて済む（チーム配布が楽）

というメリットがあります。([Claude Code][1])

---

## 2. プラグイン内での MCP サーバ定義の場所

公式リファレンスでは、MCP サーバ定義の場所は 2 パターンあります：([Claude Code][1])

1. **プラグイン直下の `.mcp.json` に書く**
2. `.claude-plugin/plugin.json` の `mcpServers` フィールドで

   * 直接オブジェクトとして書く
   * もしくは `./mcp-config.json` など外部 JSON を参照させる

標準構成はこんな感じです：([Claude Code][1])

```text
my-plugin/
├── .claude-plugin/
│   └── plugin.json        # プラグインマニフェスト
├── commands/
│   └── ...
├── agents/
│   └── ...
├── hooks/
│   └── hooks.json
├── skills/
│   └── ...
├── .mcp.json              # ← MCPサーバ定義（パターン1）
└── servers/               # MCP サーバ本体（バイナリ/スクリプトなど）
    └── ...
```

---

## 3. `.mcp.json` で MCP サーバを定義する（基本パターン）

`.mcp.json` のフォーマットは **「標準 MCP サーバ設定」**で、トップレベルに `mcpServers` オブジェクトを置きます。([Claude Code][1])

公式のサンプル（ほぼそのまま）：

```jsonc
{
  "mcpServers": {
    "plugin-database": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server",
      "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config.json"],
      "env": {
        "DB_PATH": "${CLAUDE_PLUGIN_ROOT}/data"
      }
    },
    "plugin-api-client": {
      "command": "npx",
      "args": ["@company/mcp-server", "--plugin-mode"],
      "cwd": "${CLAUDE_PLUGIN_ROOT}"
    }
  }
}
```

各フィールドの意味は：

* `mcpServers`

  * キー：Claude から見えるサーバ名（例: `"plugin-database"`）
  * 値：その MCP サーバの起動方法

* `command`

  * 実行するコマンド。ローカルバイナリ・Node スクリプト・Python など何でも OK
  * 例: `./servers/db-server`, `node`, `python`, `npx`など

* `args`（任意）

  * コマンドライン引数。設定ファイルパスやモード指定など

* `env`（任意）

  * 環境変数を指定
  * `${CLAUDE_PLUGIN_ROOT}` が使える（プラグインの実インストールディレクトリに展開される）([Claude Code][1])

* `cwd`（任意）

  * プロセスのカレントディレクトリ

この `.mcp.json` を plugin ルートに置いておけば、プラグインが有効化されたときに MCP サーバが自動で起動し、Claude の「ツール」として認識されます。([Claude Code][1])

---

## 4. `plugin.json` に紐づける（パターン2）

MCP 設定を **別ファイルに分けたい／インラインで書きたい**場合は、`.claude-plugin/plugin.json` の `mcpServers` フィールドを使います。([Claude Code][1])

### 4-1. 外部ファイル参照

`plugin.json`：

```jsonc
{
  "name": "my-mcp-plugin",
  "version": "1.0.0",
  "description": "Plugin bundling custom MCP servers",
  "mcpServers": "./mcp.json"   // ここで外部JSONを参照
}
```

`./mcp.json`（内容はさっきの `.mcp.json` と同じ形式）：

```jsonc
{
  "mcpServers": {
    "plugin-database": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server"
    }
  }
}
```

### 4-2. 直接インラインで書く

`plugin.json` にそのままオブジェクトを書くこともできます：([Claude Code][1])

```jsonc
{
  "name": "my-inline-mcp-plugin",
  "version": "1.0.0",
  "description": "Inline MCP config example",
  "mcpServers": {
    "plugin-database": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server",
      "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config.json"]
    }
  }
}
```

この場合、**別途 `.mcp.json` を置く必要はありません。**

---

## 5. 実際に MCP サーバを動かすときの注意点

公式リファレンスに書いてあるポイントを、実務寄りにまとめると：([Claude Code][1])

1. **すべてのパスは plugin ルートからの相対パス or `${CLAUDE_PLUGIN_ROOT}` を使う**

   * 絶対パスは環境ごとに変わるので NG
2. **サーバ実行ファイルに実行権限を付ける**

   * Unixなら `chmod +x servers/db-server`
3. **MCP サーバは MCP プロトコル（JSON-RPC over stdio）で応答する必要がある**

   * ここは従来通りの MCP 実装ルール
4. **デバッグには `claude --debug` や `--mcp-debug` を使う**

   * プラグイン読み込みや MCP 起動エラーがログに出る
5. **プラグイン単位でユーザー・チームに配布できる**

   * `.mcp.json` や設定を手で配るより遥かに楽

---

## 6. ざっくり手順まとめ

1. **プラグインの骨組みを作る**

   * `my-plugin/.claude-plugin/plugin.json` を作成
2. **MCP サーバ本体を用意する**

   * `servers/` 以下に Node/Python/Go などで MCP サーバ実装
3. **MCP 定義を書く**

   * `.mcp.json` か `plugin.json` の `mcpServers` にサーバ起動方法を記述
4. **ローカルマーケットプレイス経由でインストール**

   * `marketplace.json` を用意して `/plugin marketplace add ./my-marketplace`
   * `/plugin install my-plugin@my-marketplace`
5. Claude Code を再起動して、MCP サーバがツールとして見えているか確認

---

もしよければ次のステップとして：

* BigQuery / Slack / GitHub など、**実際の社内ツール向け MCP サーバを束ねた plugin の「雛形リポジトリ」**を一緒に設計することもできます。
* 例えば「kk-generation 用 /takeoff plugin」や「ナイル社内向け plugin」みたいな形で、marketplace + plugin 構成を具体的に書き起こすことも可能です。

[1]: https://code.claude.com/docs/en/plugins-reference "Plugins reference - Claude Code Docs"
