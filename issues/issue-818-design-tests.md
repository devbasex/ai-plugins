# #818: Claude Code と Codex で言語サーバを使えるようにする — テスト設計と未確認

| 文書 | 中身 |
| --- | --- |
| [issue-818-requirements.md](issue-818-requirements.md) | 要求と受け入れ条件 |
| [issue-818-design.md](issue-818-design.md) | 設計 |
| [issue-818-design-decisions.md](issue-818-design-decisions.md) | 決定の理由 |

## テスト設計

単体と結合は `plugins/mcp/mcp-serena/tests/` に置く。走らせるコマンドは次のとおりである。

```bash
uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest plugins/mcp/mcp-serena -q -n 4
```

`serena` と `git` の結合は一時ディレクトリの git リポジトリと偽の `serena`（`--serena` で差し替える。受けた引数を記録し、言語ごとに決めた終了コードを返す）で行う。実機は手動確認で、claude は `env -i` と一時の HOME で隔離する。

| 受け入れ条件 | 何で確かめるか | 階層 |
| --- | --- | --- |
| AC1・AC2 | `.mcp.json` / `.codex.mcp.json` を読み、引数の並びを照らす。`git+` を含まない | 単体 |
| AC3 | Claude Code の起動定義で Serena を起動し、MCP の `tools/list` の名前を照らす | 手動（実機の Serena） |
| AC4 | このリポジトリで `detect --json` の `detected` と `project.yml` の `language_servers` を照らす | 結合 |
| AC5 | 拡張子の件数の組（9/10 ファイル・4.9%/5.0%・分母に `.md` を含めない）で採否と `skipped.reason` を照らす | 単体 |
| AC6 | `project.yml` が無い / 雛形どおり / 注釈つき / 流れの形の非空の配列の 4 つで、書き換えの前後の差分が 2 キーだけか（最後は終了コード 3）を照らす | 単体 |
| AC7 | 偽の `serena` が `bash` だけ 1 を返すとき、`failed` に `bash`、`language_servers` に残りが入る。検証の途中で例外を投げても、最後に通った言語だけが書かれる | 結合 |
| AC8 | 実機の Serena で `bash` の言語サーバのキャッシュを壊し（`~/.serena` を一時の HOME へ写して壊す）、`configure` の後に `python` の `find_symbol` / `find_referencing_symbols` が通る | 手動 |
| AC9 | `--dry-run` の前後でリポジトリの全ファイルのハッシュが変わらない | 結合 |
| AC10 | `--gitignore` なしで `.gitignore` が変わらず、ありで `.serena/project.yml` の行が 1 度だけ足される | 結合 |
| AC11 | ai-plugins（python + bash）と carmo-system-serverside（python + typescript）の写しで、`configure` → `check` を通す | 手動 |
| AC12 | `.worktrees/<名前>` の作業ツリーで起動した Serena の `find_symbol` の結果が作業ツリーのパスを指す | 手動 |
| AC13 | `installed_plugins.json` の有無・中身の組と PATH の組で `missing` と終了コード 0 / 1 / 2 を照らす | 単体 |
| AC14 | 偽の `typescript-language-server` の隣に `typescript` の `package.json`（版 7.0.2 / 5.9.3）を置き、`typescript_major_5` の有無を照らす | 単体 |
| AC15 | PATH に `shellcheck` が無い / ある で `missing` を照らす | 単体 |
| AC16 | 配布物の全 `plugin.json` と `marketplace.json` に `lspServers` が無い | 単体 |
| AC17・AC18 | 食い違いあり / 欠けあり / 揃っている / `project.yml` が無い の 4 つで、SessionStart の出力の有無を照らす | 単体 |
| AC19 | `.md` / `.json` / `.py`（採った言語）/ `.ts`（採っていない言語）の `Read` を 3 回ずつ送り、拒否が `.py` だけで出る | 単体 |
| AC20 | grep 3・読み込み 3・混在 4 で拒否し、数が 0 に戻る。拒否から 119 秒は拒否せず、120 秒で再び数える（時刻を差し替える） | 単体 |
| AC21 | `permission_mode` が `acceptEdits` / `auto` / `default` / `bypassPermissions` のとき、Serena のツールで `allow` が出るのは前の 2 つだけ | 単体 |
| AC22 | `hooks/codex.json` の matcher と、Codex の入力（`tool_name: "Bash"`・`command: "sed -n 1,80p a.py"`）で読み込みとして数えられる | 単体 |
| AC23 | 1 万ファイルの一時リポジトリで SessionStart を 5 回、PreToolUse を 20 回走らせ、最大の所要を照らす | 結合 |
| AC24 | Python / TypeScript（`typescript@5`）/ PHP の小さなリポジトリで、隔離した claude に型を壊す編集をさせ、記録の `<new-diagnostics>` を見る | 手動 |
| AC25 | 同じ 3 つのリポジトリで `codex exec` に `find_referencing_symbols` と `get_diagnostics_for_file` を呼ばせ、記録（`~/.codex/sessions`）で結果を見る | 手動 |
| AC26・AC27 | #818 §3 の題材を、`project.yml` を直した ai-plugins の写しで、指示なしの 2 条件（LSP のみ / Serena のみ）× 3 回、`claude -p --output-format stream-json` で走らせ、ツールの呼び出しとツール結果の文字数を数える。`initial_instructions` が読まれた回数も数える | 手動 |
| AC28・AC29 | `claude plugin validate .`・`bash scripts/build-runtime-plugins.sh --check`・`bash plugins/mcp/mcp-serena/dev.kiro/install.sh --dry-run` の終了コード | 結合（継続的統合） |
| AC30 | `git ls-files .serena` | 結合（済） |

`.md` の文言を照合するテストは書かない（`AGENTS.md`）。Skill の `SKILL.md` は `python3 scripts/check-skill-frontmatter.py` と `check-markdown-links.py` が見る。

## 未確認のまま残ること

| # | 項目 | 内容 | いつ決まるか |
| --- | --- | --- | --- |
| U1 | Codex での `.mcp.json` のプラグインルートの展開 | 隔離した Codex で試したが、起動の記録が残らず判定できなかった。決定 4 でラッパーを作らないため、この課題の実装は依存しない | 上流の変化で乗り換えを考えるとき |
| U2 | Codex の hook の入出力 | SessionStart の `additionalContext` と PreToolUse の `permissionDecision: "deny"` を Codex 0.156 が読むか。Serena 公式の `serena-hooks` は `--client codex` を持つため、読む前提で作る | 実装（AC22・AC25 の手動確認） |
| U3 | Codex の文脈での memory のツール | `no-memories` のモードで、一覧に残った memory のツールを呼んだときに拒まれるか | 実装（AC25 と同じ手順で 1 度呼ぶ） |
| U4 | 作業ツリーに `project.yml` が無いときの Serena | `--project-from-cwd` が `.git` を持つ作業ツリーの根を選び、設定を自動で作るのか、主ディレクトリの設定を読むのか | 実装（AC12） |
| U5 | PHP の型の診断 | intelephense の無料版が型の食い違いを返さないのか、設定で返すのか（#818 の本文の未確認） | 実装（AC24・AC25）。返さなければ前提 4 のまま |
| U6 | `initial_instructions` の費用 | 検査・仕上げ・取り込みの supervisor で読まれるか。読まれて持ち出しなら、外す案を別の課題にする（決定 11） | 実装（AC26・AC27） |
| U7 | 効果の割合 r | 見積もりの r = 0.6 は、指示した条件（#818 §3 の D・E）の値である。誘導の hook だけでどこまで近づくか | 実装（AC26・AC27） |
| U8 | 10 言語目以降の対応表の値 | `go` / `ruby` などは公式プラグインの定義から写すだけで、起動の検証をこの課題では通さない | 利用者が導入したとき（SessionStart の通知で気付く） |
