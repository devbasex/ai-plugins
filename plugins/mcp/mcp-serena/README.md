# mcp-serena

Serena MCP サーバを提供するプラグインです。シンボル単位の読み書き（定義・参照・シンボル単位の
置換）と、言語サーバの診断を Claude Code・Codex・Kiro CLI で使えるようにします。

## 最初に: 言語サーバの設定はプロジェクトごとに行う

**プラグインを入れただけでは、どのリポジトリでも言語サーバは設定されません。** 言語の設定
（`.serena/project.yml`）はリポジトリごとに持つため、使うリポジトリごとに 1 度、導入の Skill を
実行します。利用者（マシン・コンテナ）単位で 1 回打てば済むものではありません。

```text
/mcp-serena:language-servers
```

Skill は次を行います。

1. 追跡しているファイルの拡張子を数え、10 ファイル以上かつ 5% 以上の言語を採る
2. `.serena/project.yml` の `language_servers` を書き、1 言語ずつ `serena project health-check` で起動を確かめる。失敗した言語は外し、理由を `mcp_serena_excluded` に残す
3. Claude Code の公式 LSP プラグインと言語サーバの本体の導入をチェックし、欠けを導入のコマンドとともに示す（入れるのは利用者の確認を取ってから）

`project.yml` を追跡すれば、同じリポジトリの他の利用者は打たずに同じ設定を使えます（言語サーバの
本体は各自に要ります）。設定していないリポジトリでは、セッションの開始時に 1 度だけ未設定を
知らせ、誘導の hook は何も数えません。

## 役割の分担（Claude Code）

| 目的 | 担うもの |
| --- | --- |
| 編集の後の診断 | 公式 LSP プラグイン（`pyright-lsp` など。次の手番に自動で届く） |
| シンボルの把握・参照・編集 | Serena |
| Bash の診断 | Serena（bash-language-server + shellcheck。公式の Bash 向け LSP プラグインが無い） |

Codex には公式 LSP プラグインが無いため、診断も Serena の `get_diagnostics_for_file` で取ります。

## インストール

### Claude Code

```bash
/plugin install mcp-serena@ai-plugins
```

### Codex

```bash
codex plugin add mcp-serena@ai-plugins
```

Codex では `--context codex` で起動します（`.codex.mcp.json`）。

**Codex では、プラグインの hook は信頼するまで動きません**（Codex 0.156 で実測。何も表示されずに走らないだけになる）。対話の Codex で `/hooks` を開き、mcp-serena の hook を信頼してください。起動時に出る「Review hooks」の案内からも信頼できます。

### Kiro CLI

repository を clone した後、対象のプロジェクトの根で installer を実行します。

```bash
bash plugins/mcp/mcp-serena/dev.kiro/install.sh
```

### 前提

- `uv`（`uvx`）。Serena は `uvx --from serena-agent==1.7.0` で起動します
- Serena 側の言語サーバは Serena が `.serena/language_servers/` へ自分で入れます
- Claude Code の LSP プラグインと本体は利用者が入れます（Skill がコマンドを示します）

## 公式の `serena` プラグインと併用しない

公式マーケットプレイスにも `serena` があります。両方を入れると Serena が 2 つ起動し、同じ
ツールが 2 組並びます。どちらか一方にしてください。

| 項目 | mcp-serena（このプラグイン） | 公式の `serena` |
| --- | --- | --- |
| 版 | `serena-agent==1.7.0` に固定 | 最新を取得 |
| プロジェクトの有効化 | `--project-from-cwd` で自動 | `activate_project` を呼ぶ |
| memory・onboarding | 使わない（`no-memories` / `no-onboarding`） | 使う |
| 言語の設定 | `/mcp-serena:language-servers` が検出して書く | 自動の推定 |
| 誘導の hook | 設定した言語のファイルだけを数える | 固定の拡張子の一覧で数える |
| Codex | `--context codex` の定義と hook を持つ | — |

## 起動の定義

| ランタイム | 定義 | 文脈 |
| --- | --- | --- |
| Claude Code / Kiro CLI | `.mcp.json` | `claude-code` |
| Codex | `.codex.mcp.json` | `codex` |

どちらも `SERENA_HOME=.serena`（起動したディレクトリからの相対）で動きます。作業ツリーごとに
`.serena/language_servers/` を持つため、作業ツリーでの初回は言語サーバの取得が走ります。

## hook

| イベント | 振る舞い |
| --- | --- |
| SessionStart | 検出した言語と設定の食い違い、導入の欠けがあるときだけ知らせる。未設定のリポジトリでは未設定を知らせる |
| PreToolUse | 設定した言語のファイルの grep・読み込みが続くと 1 度だけ止め、シンボル単位の手順を示す。Claude Code の許可のモードが `acceptEdits` / `auto` のとき、Serena のツールを自動で許可する |

## 注意事項

- Serena の memory は使いません。知識は `docs/` に、手順は `skills/` に置いてください
- 使い方の詳細は `docs/serena-guide.md` を参照してください

## v2.1.1 へ更新するとき

**正式版です。** `main` に載ります（ndf 10.17.9 と同じ配布。#818）。中身は開発版 `2.1.0-dev.1` と同じで、版数の接尾辞だけを外しました。

| 変わったこと | 中身 |
| --- | --- |
| **Serena の版を固定しました** | `uvx --from serena-agent==1.7.0` で起動します。以前は GitHub の最新を取っていました |
| **起動の文脈を変えました** | Claude Code / Kiro CLI は `--context claude-code`、Codex は `--context codex`（`.codex.mcp.json`）で起動し、`--project-from-cwd` で起動したディレクトリのプロジェクトを自動で有効にします。`activate_project` を呼ぶ必要はありません |
| **memory と onboarding のツールを出しません** | `--add-mode no-memories` / `no-onboarding` で起動します。知識は `docs/` に、手順は `skills/` に置きます |
| **言語サーバの設定の Skill を足しました** | `/mcp-serena:language-servers`。リポジトリごとに 1 度実行します（「最初に: 言語サーバの設定はプロジェクトごとに行う」） |
| **hook を入れ替えました** | SessionStart は設定の食い違いと導入の欠けだけを知らせます。PreToolUse は設定した言語のファイルの grep・読み込みが続くと 1 度だけ止め、シンボル単位の手順を示します（「hook」） |

**Codex では hook を信頼し直してください。** hook の定義が変わったため、対話の Codex で `/hooks` を開いて
mcp-serena の hook を信頼するまで動きません。更新したあとは Claude Code / Codex を起動し直してください。

```bash
claude plugin marketplace update ai-plugins
claude plugin update mcp-serena@ai-plugins

codex plugin marketplace upgrade ai-plugins
codex plugin add mcp-serena@ai-plugins
```

手元で確かめるコマンドです。`$ROOT` は導入先の `mcp-serena` のディレクトリで、どれもファイルを書き換えません。

```bash
grep -q '"version": "2.1.0"' "$ROOT/.claude-plugin/plugin.json"; echo "exit=$?"   # 0 なら この版が入っている
grep -qF 'serena-agent==1.7.0' "$ROOT/.mcp.json"; echo "exit=$?"   # 0 なら Serena の版が固定されている
python3 "$ROOT/scripts/serena-lsp.py" detect --json >/dev/null; echo "exit=$?"   # 0 なら 言語の検出が動く（今いるリポジトリを数えるだけ）
```

## 以前の版: v2.0.1 へ更新するとき

Claude Code の起動時に出ていた `hooks.json: unknown key ... ignored` の警告を消しました。
hook が実行する内容は変えていません。`claude plugin update mcp-serena@ai-plugins` のあとに
Claude Code を起動し直すと反映されます。

## 以前の版: v2.0.0 へ更新するとき

配布ディレクトリが `plugins/mcp/{shared,claude,codex,kiro}/mcp-serena/` から
`plugins/mcp/mcp-serena/` へ変わりました。マーケットプレイスの参照先が変わるため、**導入済みの
環境では再インストールが要ります**。Kiro CLI の installer は `dev.kiro/install.sh` へ移りました。

MCP サーバの定義（`.mcp.json`）の内容は変えていません。
