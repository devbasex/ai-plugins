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
| 作業領域の信頼 | `-p` 指定時は信頼確認が省略される。agy の `--add-dir` に相当する宣言は不要 |
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

## 完了検知は JSON で確定する

`--output-format json` を使えば**完了印のファイルは要らない**。

```bash
jq -r '.is_error, (.permission_denials | length), .subtype' /tmp/claude-stdout.json
```

| 見るところ | 意味 |
| --- | --- |
| `is_error` が真 | 実行が失敗した |
| `permission_denials` が非空 | 承認に失敗した。要求したツールが `--allowed-tools` に無い |
| `subtype` | `success` / `error_during_execution` など |
| `modelUsage` | **実際に使われたモデル名**。指定値との突き合わせに使える |

`modelUsage` からモデル名を取れるのは 4 CLI のうち claude だけである。
計測では指定値と実測値を突き合わせ、食い違ったら警告する。

## Skill を読ませる

claude は配置した Skill を**自動で読む**（実測）。それでも
`/ndf:cross-refactoring` は明示パスを書く。3 ランタイムで同じプロンプトを使うためで、
claude には冗長だが害はない。

対象リポジトリに NDF が入っている保証がないときは `/ndf:*` を呼ばず、
配置した Skill を明示パスで読ませる。

## モデル指定

```bash
claude -p --model opus-5 < prompt.md
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

三段フォールバック（[SKILL.md](../SKILL.md) の手順 5）では、`--output-format json` を
使うなら `STDOUT` が構造化されているため、**結果ファイルより stdout を優先してよい**。
ただし結果ファイルを書かせる指示は残す。JSON の `result` は 1 本の文字列で、
長い成果物には向かない。

| 優先 | 次点 | 最後の手段 |
| --- | --- | --- |
| `OUTPUT_FILE` | `STDOUT`（JSON の `result`） | 標準エラー出力 |
