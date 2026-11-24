# Claude CLI 固有の手順（ヘッドレス実行）

[← SKILL.md](../SKILL.md)

claude 2.1.233 の実機で確認した内容である。ホストが Claude Code のときでも、
**別プロセスの `claude -p` として起動すればホストの作業文脈から切り離せる**。
`/ndf:cross-refactoring` はこの性質を使って、ホストと同じランタイムを適用担当にしている。

## インストールと確認

```bash
which claude && claude --version
claude --help | grep -A2 -- --model     # 指定できるモデル（別名と正式名の両方を受ける）
```

## 非対話実行

```bash
cat prompt.md | claude -p \
    --permission-mode acceptEdits \
    --allowed-tools "Bash,Read,Write,Edit,Glob,Grep" \
    --output-format json \
  > /tmp/claude-stdout.json 2> /tmp/claude-err.log
```

| 事項 | 内容 |
| --- | --- |
| プロンプトの渡し方 | **標準入力から渡せる。** 長いプロンプトをコマンド引数に載せる必要がない |
| 作業領域の信頼 | `-p` 指定時は信頼確認が省略される。agy の `--add-dir` に相当する設定は不要 |
| 作業ディレクトリ | 起動時の cwd がそのまま作業対象。`--add-dir` で範囲を広げない |

## 権限モードは `acceptEdits` を既定にする

**`bypassPermissions` は root 実行で拒否される。**

```console
$ claude -p --permission-mode bypassPermissions < prompt.md
--dangerously-skip-permissions cannot be used with root/sudo privileges
```

継続的インテグレーションやコンテナは root 実行が多いため、これを既定にできない。
`acceptEdits` と `--allowed-tools` の明示なら root でも通ることを実測している。

ローカルの非 root 環境でだけ動作確認すると、CI / コンテナで初めて詰まる。

## 読むだけの起動（読み手役）

文書を読ませるだけの起動では、上の「非対話実行」の形（コマンドの実行とファイルの編集を許す）を使わない。文書に埋め込まれた指示に従っても、
文書のほかに何も読めず、どこにも書けず、外へ送れないように、読ませるファイルのコピーだけを置いた新しいディレクトリで、Read だけを持たせて起動する。

対象のリポジトリの中で、次の全体を 1 つの Bash の呼び出しで打つ。起動の前に、`external-ai` の「共通の実行手順」の 2（CLI の有無・認証・学習に使わない設定）を確かめる。

```bash
(
  set -u
  # 使ってよいランタイムの設定（メインディレクトリの .ndf/runtimes.json）が claude を許していなければ、文書を写す前に止まる
  MAIN=$(git worktree list --porcelain | sed -n '1s/^worktree //p'); [ -n "$MAIN" ] || exit 1
  POLICY="$MAIN/.ndf/runtimes.json"
  if [ -e "$POLICY" ] || [ -L "$POLICY" ]; then
    jq -e 'keys == ["allowed"] and (.allowed | type == "array" and index("claude") != null)' "$POLICY" > /dev/null || { echo "claude の起動を許していない" >&2; exit 1; }
  fi
  # 写し・指示・結果・エラーログは 1 つの一時ディレクトリに置き、成功でも失敗でも、終わるときに消す
  W=$(mktemp -d) || exit 1
  trap 'rm -rf "$W"' EXIT; trap 'exit 143' HUP INT TERM
  mkdir "$W/docs" "$W/run" || exit 1
  cp -L <読ませるファイル>... "$W/docs/" || exit 1   # 同じ名前のファイルは、重ならない名前に変えて写す
  cat > "$W/run/entry.json" <<'JSON'
<document-rebuild の形の JSON。files には、写したファイルの名前だけを書く>
JSON
  ndf records reading prompt --file "$W/run/entry.json" > "$W/run/made.json" || { cat "$W/run/made.json" >&2; exit 1; }
  jq -er '.metrics.prompt' "$W/run/made.json" > "$W/run/prompt.md" || exit 1
  cd "$W/docs" && timeout 1320 claude -p --restricted --safe-mode --strict-mcp-config --tools Read \
    --permission-mode dontAsk --no-session-persistence --output-format json \
    < "$W/run/prompt.md" > "$W/run/out.json" 2> "$W/run/err.log"
  # 返った文を標準出力に出す。失敗したら、理由を標準エラー出力に出して 0 以外で終わる
  jq -er 'select(.is_error == false) | .result' "$W/run/out.json" || { tail -n 20 "$W/run/err.log" "$W/run/out.json" >&2; exit 1; }
)
```

終了コードが 0 なら、標準出力が読み手役の返した文である。0 以外なら、起動できないものとして扱う。質問の文や、一部だけを抜き出した文書のように、
読ませるために新しく作るファイルも `$W/docs` の中に作る。返った文を一時ディレクトリの外のファイルへ写さない。

| 指定 | 止めるもの |
| --- | --- |
| `--restricted` | 写しのディレクトリ（`$W/docs`）の外のファイルの読み取り（シンボリックリンクの先を含む）。利用者・プロジェクト・ローカルの設定（許したツールの設定を含む）の読み込み |
| `--tools Read` | コマンドの実行・ファイルの編集・Web の取得と検索・サブエージェント |
| `--strict-mcp-config` | MCP サーバー |
| `--safe-mode` | プラグイン・hook・Skill・`CLAUDE.md`（文書を書いた経緯を渡さないためでもある） |
| `--permission-mode dontAsk` | 許されていない操作。確認を求めずに拒む |

2.1.331 で実測した。使えるツールは Read だけで、起動したディレクトリの外のファイルと、外を指すシンボリックリンクの読み取りは拒まれた。
**どれかの指定を受け付けないバージョンでは、指定を外して起動しない。** 起動できないものとして扱う。

## 完了検知は JSON で確定する

`--output-format json` を使えば**完了マーカーのファイルは要らない**。

```bash
jq -r '.is_error, (.permission_denials | length), .subtype' /tmp/claude-stdout.json
```

| 見るところ | 意味 |
| --- | --- |
| `is_error` が真 | 実行が失敗した |
| `permission_denials` が非空 | 承認に失敗した。要求したツールが `--allowed-tools` に無い |
| `subtype` | `success` / `error_during_execution` など |
| `modelUsage` | **実際に使われたモデル名**。指定値との突き合わせに使える |

`modelUsage` には補助で動いたモデル（`claude-haiku-…` など）も並ぶ。主たるモデルは、各モデルの
`inputTokens` + `cacheReadInputTokens` + `cacheCreationInputTokens` + `outputTokens` の和が最大のものとし、
`claude-opus-5[1m]` のような CLI の名前をそのまま記録する（共通ライブラリの `models.observed_model`）。
入力のトークンだけで選ぶと、キャッシュの読み取りを数えず補助のモデルを選ぶ。
計測では指定値と実測値を突き合わせ、食い違ったら警告する。

## Skill を読ませる

claude は配置した Skill を**自動で読む**（実測）。それでも
`/ndf:cross-refactoring` は明示パスを書く。3 ランタイムで同じプロンプトを使うためで、
claude には冗長だが害はない。

対象リポジトリに NDF が入っている保証がないときは `/ndf:*` を呼ばず、
配置した Skill を明示パスで読ませる。

## モデル指定

```bash
claude -p --model claude-opus-5 < prompt.md
```

別名（`opus` / `sonnet` / `fable`）と正式名の両方を受け付ける。
実際に動いたモデルは上記の `modelUsage` で確認できる。

## 実行コスト

| 内容 | 実測 |
| --- | --- |
| 単純な 3 ターンの作業 | 0.259 ドル |
| 実際のリファクタリング 1 件（29 ターン / 218 秒） | 1.42 ドル |

**単純タスクの 5 倍以上**になる。claude を繰り返し起動する仕組みでは、上限値の既定を
保守的に置き、報告に実測コストを出す。

## 出力の回収

結果ファイル → stdout（JSON の `result`。`is_error` が真なら使わない） →
標準エラー出力の順に拾い、実測のモデルは JSON の `modelUsage` のキーから読む。JSON の `result` は
1 本の文字列で長い成果物には向かないため、結果ファイルを書かせる指示を残す。

| 優先 | 次点 | 最後の手段 |
| --- | --- | --- |
| `OUTPUT_FILE` | `STDOUT`（JSON の `result`） | 標準エラー出力 |
