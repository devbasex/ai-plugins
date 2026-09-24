---
name: language-servers
description: "Set up Serena language servers, once per project. Use when this repository has no Serena languages or the session start notice reports a mismatch: detects languages, writes .serena/project.yml, verifies each server, checks Claude Code LSP（言語サーバを設定・Serenaの言語を直す）."
---

# 言語サーバを設定する

**この Skill はプロジェクト（リポジトリ）ごとに実行する。利用者単位で 1 回ではない。**
言語の設定（`.serena/project.yml`）はリポジトリごとに持つため、プラグインを入れただけでは
どのリポジトリにも言語サーバは設定されない。打ち直すのは、セッションの開始の通知が
食い違い（設定に無い言語・欠けた LSP）を知らせたときである。

| 何が | 単位 |
| --- | --- |
| この Skill の実行（言語の検出・`project.yml` の書き込み・1 言語ずつの起動の検証） | プロジェクト |
| `project.yml` を追跡するか | プロジェクト（追跡すれば、同じリポジトリの他の利用者は打たずに済む） |
| 言語サーバの本体と Claude Code の LSP プラグインの導入 | 利用者（マシン・コンテナ）。この Skill は欠けを示すだけで入れない |

判断の要らない手順はすべて `scripts/serena-lsp.py` が行う。この Skill が持つのは、次の 4 つの
判断だけである。

1. 導入のコマンドを打つか（利用者に確認する）
2. `.serena/project.yml` を追跡するか（利用者に確認する）
3. 検証に失敗した言語の原因と次の手
4. 検出の誤りを名指しで直すか（`--only`）

## 1. プラグインルートを決める

Claude Code だけが `${CLAUDE_PLUGIN_ROOT}` を置き換える。Codex では、**打つ前に
`<この Skill のディレクトリ>` をランタイムから渡された実際のパスへ置き換える**。Kiro CLI は
installer が張った `.kiro/skills/language-servers` の symlink で当たる。

```bash
PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'
case "$PLUGIN_ROOT" in '$'*) PLUGIN_ROOT= ;; esac
ROOT=
for candidate in \
  ${PLUGIN_ROOT:+"$PLUGIN_ROOT/skills/language-servers"} \
  "<この Skill のディレクトリ>" \
  ".kiro/skills/language-servers"
do
  # symlink のまま上がると .kiro/ を指すため、実体へ解決してから 2 つ上がる
  dir=$(cd -P "$candidate" 2>/dev/null && pwd) || continue
  [ -f "$dir/../../scripts/serena-lsp.py" ] || continue
  ROOT=$(cd "$dir/../.." && pwd)
  break
done
[ -n "$ROOT" ] || { echo "mcp-serena のプラグインルートを解決できない" >&2; exit 1; }
echo "$ROOT"
```

以降のコマンドは、同じシェルか、`ROOT` を上の値で置き換えて打つ。

## 2. 検出して書き、1 言語ずつ検証する

```bash
python3 "$ROOT/scripts/serena-lsp.py" configure --root . --json
```

**言語の数 × 120 秒かかることがある。** Claude Code の Bash ツールでは `timeout` に 600000 を
指定して打つ。確認は取らずに打ってよい（書き換えるのは `project.yml` の `language_servers`・
`ignored_paths`・`mcp_serena_excluded` だけ）。

| 終了コード | 意味 | 次の手 |
| --- | --- | --- |
| 0 | 書いた（外した言語があっても 0） | 3 へ |
| 1 | 検証を通った言語が 0 | `failed` の理由とログを読む（下の表） |
| 2 | `git` か `uvx` が使えない | 利用者に `uv` の導入を示して止まる |
| 3 | `project.yml` の形を読めない・`project.local.yml` が `language_servers` を持つ | 出力の `error` を利用者に示し、どちらを直すか聞く |

出力の読み方:

- `detected` / `skipped`: 採った言語と、しきい値（10 ファイル以上かつ 5% 以上）に届かなかった言語
- `failed`: 検証に失敗して外した言語。`reason` は `health_check_exit_<n>` / `timeout` / `serena_unavailable`
- `written.serena_gitignore_added`: `.serena/.gitignore` に足すべき行。空でなければ 4 へ

**検出が誤っているとき**（生成物・同梱したライブラリの言語が採られた、など）は、採る言語を
名指しして打ち直す。名指しされなかった言語は `not_selected` として記録され、以後の通知に出ない。

```bash
python3 "$ROOT/scripts/serena-lsp.py" configure --root . --json --only python,typescript
```

**検証に失敗した言語**は `failed.log` を読んで原因を判断する。

| ログに出るもの | 次の手 |
| --- | --- |
| `No analyzable files found` | 1,000 バイトを超えるその言語のファイルが無い。外したままでよい |
| 言語サーバの取得・展開の失敗 | 利用者の確認を取ってから `.serena/language_servers/` の該当の言語を退避し、打ち直す |
| 依存の不足（`node` / `npm` / `java` など） | 利用者に導入を示す。スクリプトは入れない |

## 3. Claude Code の LSP の導入を検査する

```bash
python3 "$ROOT/scripts/serena-lsp.py" check --root . --json
```

Codex では `--runtime codex` を付ける（Claude Code の LSP の項目を飛ばし、Bash の
`shellcheck` だけを見る）。

終了コード 1 のとき、`missing` の要素ごとに `install` のコマンドを利用者に示し、**確認を
取ってから**打つ。ネットワークへ出て利用者の環境へ書き込むためである。Claude Code の
プラグインを入れた後は、Claude Code の再起動が要る。

## 4. 追跡の方針を決める

`.serena/project.yml` を追跡するかを利用者に聞く。

| 選択 | 結果 | 打つもの |
| --- | --- | --- |
| 追跡する（既定） | 作業ツリー（`.worktrees/<ブランチ名>`）にも同じ設定が揃い、同じリポジトリの他の利用者は打たずに済む | 何も打たない |
| 追跡しない | 作業ツリーでは Serena が印の無い設定を自動で作り、通知も誘導も働かない | `configure --root . --gitignore` |

`written.serena_gitignore_added` が空でなければ、`serena_config.yml`（認証の秘密を持ち得る）・
`logs/`・`language_servers/`・`cache/` が追跡の候補に残っている。確認を取ってから足す。

```bash
python3 "$ROOT/scripts/serena-lsp.py" configure --root . --json --serena-gitignore
```

## 5. Serena に読み直させる

`language_servers` が変わったら、起動中の Serena は古い言語のまま動いている。Claude Code は
`/mcp` で `serena` を再接続するかセッションをやり直し、Codex はセッションをやり直すよう
利用者に示す。

## 使い分け（Claude Code）

| 目的 | 使うもの |
| --- | --- |
| 編集の後の診断 | 公式 LSP プラグイン（次の手番に自動で届く） |
| シンボルの把握・参照・編集 | Serena の `get_symbols_overview` → `find_symbol` → `find_referencing_symbols`、編集は `replace_symbol_body` |
| Bash の診断 | Serena の `get_diagnostics_for_file`（公式の Bash 向け LSP プラグインが無い） |
