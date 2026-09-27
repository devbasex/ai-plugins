# Kiro CLI 固有の手順

[← SKILL.md](../SKILL.md)

kiro-cli 2.18.0 の実機で確認した内容である。**他の 3 CLI と最も挙動が違うのは
「終了コードが成否を表さない」点**なので、そこから読む。

## インストールと確認

```bash
which kiro-cli && kiro-cli --version
kiro-cli chat --list-models        # 利用できるモデル一覧（JSON 出力も可）
```

未認証だと**終了コード 1** で即座に終わる。1 が返る経路はこれと「入力なし」だけで、
それ以外の失敗は 0 になる（下記）。

## 非対話実行

```bash
cat prompt.md | kiro-cli chat --no-interactive --trust-all-tools \
  > /tmp/kiro-stdout.log 2> /tmp/kiro-err.log
```

- `--no-interactive` は**標準入力からプロンプトを受け取る**（26KB で実測）。
  コマンド引数でも渡せるが、長いプロンプトのエスケープを避けるため標準入力に統一する
- 作業ディレクトリの隔離が唯一のセキュリティ境界になるため、**必ず隔離した
  worktree / コンテナの中で実行する**

## 4 つの落とし穴

### 1. 終了コードで成否を判定しない

**ツール承認漏れでも、実行したシェルコマンドが失敗しても、kiro-cli は終了コード 0 を返す。**
`exit 1` になるのは未認証と入力なしだけである。

判定は次の 2 つで行う。

- **結果ファイルの有無** — プロンプトで指定した出力先が書かれているか
- **標準エラー出力の照合** — 下の検出語が出ていないか

```text
is rejected because it matches one or more rules on the denied list
WARNING: --trust-tools arg for custom tool
```

前者はツール承認漏れ（停止せず拒否メッセージを出して終了する）、後者はツール名の
綴り違いである。

### 2. 照合の前に ANSI エスケープを除去する

`NO_COLOR=1` も `TERM=dumb` も非 TTY も効かず、**色コードが必ず混ざる**。
除去しないと行頭アンカー（`^Error:` など）が一致せず、致命エラーを取りこぼす。

```bash
sed -r 's/\x1b\[[0-9;?]*[ -\/]*[@-~]//g' /tmp/kiro-err.log
```

### 3. ツールの絞り込みは使わない

`--trust-tools` による絞り込みは**防御にならない**。シェル実行を許可したうえで
ファイル書き込みを拒否した状態でも、`echo ... > file` をシェル経由で実行して
ファイルが作成された（実測）。

さらに**ツール名を綴り間違えても警告が出るだけで終了コード 0** になる。
何も信頼しない状態で全ツールが拒否されるため、「モデルが何もせず正常終了した」ように
見えてしまう。

防御力を持たないまま事故のリスクだけが残るので、`--trust-all-tools` を使い、
**セキュリティ境界は作業ディレクトリの隔離に一本化する**。codex の
`--dangerously-bypass-approvals-and-sandbox` / agy の `--dangerously-skip-permissions` と同じ整理である。

ツール名は内部名（`execute_bash` / `fs_read` / `fs_write`）と表示名（`shell` / `write`）の
どちらでも受け付けるが、上記の理由で使わない。

### 4. `agent set-default` と `agent create` を呼ばない

- `kiro-cli agent set-default` が書き換える既定エージェントは
  `~/.local/share/kiro-cli/data.sqlite3` に保存される**マシン全体の設定**であり、
  利用者の既存設定を奪う
- `kiro-cli agent create` は `$EDITOR`（既定は vim）を対話的に開くため、非対話
  スクリプトから呼ぶと**タイムアウトまで止まる**

エージェント一覧（`kiro-cli agent list`）は**標準エラー出力**に書かれる。
存在確認で標準出力を解析しない。

## Skill を読ませる

**kiro は Skill を配置しただけでは SKILL.md 本文を読まない。**
`description` に書かれた語彙とモデルの一般知識だけで「それらしい」応答を返すため、
**一見すると手順に沿っているように見えて中身が違う**。実測では「テストが乏しければ
現状固定テストを先に書く」という中核の手順を飛ばした。

プロンプトに**明示パスを必ず書く**。

```text
まず .kiro/skills/refactoring/SKILL.md を読み、その手順に従うこと。
```

明示指定すれば本文を読み込み、Skill の分岐判定どおりに振る舞うことを確認している。

## スラッシュコマンドは無い

`/ndf:*` 形式のコマンドは存在しない。プロンプトは**自己完結した平文**にする。

## モデル指定

```bash
kiro-cli chat --no-interactive --trust-all-tools --model claude-opus-5 < prompt.md
```

kiro-cli は claude 系と gpt 系の**両方**を提供する。つまり実行環境（ハーネス）と
モデルは独立に選べる。

> ⚠ **既定モデル `auto` は、実際に選ばれたモデルを取得できない。**
> 標準出力にもセッション一覧にも出ず、唯一の記録先である
> `~/.local/share/kiro-cli/data.sqlite3` の `model_info` にも `auto` としか残らない。
> 消費単位の倍率も 1.0 固定のため、消費量からの逆算もできない（実測）。
> **計測目的の実行では `--model` を必須にする。**

## 出力の回収

三段フォールバック（[SKILL.md](../SKILL.md) の手順 5）では、**結果ファイルを優先**する。
終了コードが使えない以上、「ファイルが書かれたこと」が唯一の確実な完了の証拠になる。

| 優先 | 次点 | 最後の手段 |
| --- | --- | --- |
| `OUTPUT_FILE` | `STDOUT` | 標準エラー出力の末尾（ANSI 除去後） |
