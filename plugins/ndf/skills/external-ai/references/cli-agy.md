# agy CLI 固有の手順

共通手順（プロンプトの書き出し、バックグラウンド起動、三段フォールバック回収、待機間隔、
プロンプトテンプレート）は [../SKILL.md](../SKILL.md) を参照。本ファイルは agy CLI に固有の
差分だけを扱う。

## この文書での語

| 語 | 意味 |
| --- | --- |
| 作業領域 | agy が読み書きしてよいと宣言されたディレクトリ。`--add-dir` が決める |
| print モード | `-p` を渡して 1 往復だけ実行し、応答を標準出力へ出す非対話の実行 |

## インストールとログイン

```bash
which agy && agy --version   # 1.1.25 で確認した

# 認証済みかどうかは models で確かめる。認証を通ったときだけ一覧を返す
agy models
```

初回の認証は対話モードで `agy` を起動して行う。

## 起動コマンド

**プロンプトは `-p=<本文>` の値として渡す。** 標準入力からは受け取らない。

```console
$ agy --output-format text -p="" < prompt.md
Error: Error: empty prompt. Usage: agy --print "your prompt here"
```

**`-p` より後ろにフラグを置かない。** 後ろの引数がプロンプトとして読まれる。

```console
$ agy -p --dangerously-skip-permissions "1+1は？"
Error: -p took "--dangerously-skip-permissions" as its prompt, so the intended prompt was
left as an argument and ignored.
```

```bash
agy --dangerously-skip-permissions --output-format text \
  --print-timeout 900s \
  --add-dir /path/to/worktree \
  -p="$(cat /tmp/agy-prompt.md)" \
  > /tmp/agy-stdout.md 2> /tmp/agy-err.log &
PID=$!
```

| オプション | 用途 |
| --- | --- |
| `--add-dir <dir>` | 作業領域へディレクトリを足す。**繰り返し指定できる** |
| `--dangerously-skip-permissions` | tool の承認要求を自動で承認する |
| `--output-format text` | 最終応答の本文をそのまま標準出力へ出す |
| `--output-format json` | 1 オブジェクトの JSON を出す（下の「出力ストリーム」） |
| `--print-timeout <時間>` | print モードの待ち時間。**単位が要る**（既定 `5m0s`） |
| `--mode plan` | 読み取り専用。書き込みを伴う tool は自動で拒否される |
| `--model <名前>` | モデルを明示指定する。一覧は `agy models` |

## 作業領域は必ず宣言する

**agy は現在地を作業領域にしない。** `--add-dir` を渡さないと、利用者の見えない場所で
作業する。渡す範囲は、担当のディレクトリと結果ファイルの置き場所に限る。

```console
$ cd "$D/cwd" && agy --output-format text --add-dir "$D/a" --add-dir "$D/b" \
    -p="$D/a/one.txt と $D/b/two.txt をそれぞれ作って"
両方のファイルを正常に作成できました。作成できなかったファイルはありません。
```

**宣言は書き込みの範囲を狭めない。** 実測では、`--dangerously-skip-permissions` を
付けなくても、`--sandbox` を付けても、作業領域の外のファイルが作られた。書き込みを
止めるのは `--mode plan` だけで、そちらは結果ファイルの書き出しもできなくなる。

```console
$ agy --output-format text --mode plan --add-dir "$D/workA" -p="作業領域に plan.txt を作り…"
jetski: no output produced — a tool required the "write_file" permission that headless
mode cannot prompt for, so it was auto-denied.
```

> ⚠️ **`--dangerously-skip-permissions` のセキュリティ注意**: 全 tool の自動承認は、任意の
> シェル実行とファイル編集を無確認で許可する。コンテナ・仮想機械・継続的統合の実行環境・
> 隔離した作業ツリーのいずれかの**中でだけ**使う。プロンプトへ「リポジトリ編集禁止」と
> 書くことは有効だが、隔離の代わりにはならない。

## 実行時間の上限には単位を付ける

`--print-timeout` は数字だけでは受け付けない。

```console
$ agy --print-timeout 900 --output-format text -p="ping"
invalid value "900" for flag -print-timeout: time: missing unit in duration "900"
```

既定の 5 分は、収束ループの監視の上限（`cross-review` は 420 秒、`cross-refactoring` は
900 秒と 3600 秒）より短い。**CLI が先に打ち切ると結果ファイルが残らず、起動できなかった
場合と区別が付かない。** 呼び出し側がフェーズの上限以上の値を渡す。

## 出力ストリーム

| ストリーム | `--output-format text` | `--output-format json` |
| --- | --- | --- |
| 標準出力 | 最終応答の本文 | 下の 1 オブジェクト |
| 標準エラー出力 | 警告のみ | 同左 |

```console
$ agy --output-format json -p="1+1は？数字だけ答えて"
{"conversation_id":"…","status":"SUCCESS","response":"2\n","duration_seconds":3.33,
 "num_turns":1,"usage":{"input_tokens":13842,"output_tokens":41,"thinking_tokens":40,
 "cache_read_tokens":0,"total_tokens":13883}}
```

**実際に動いたモデル名は出力に載らない。** `--model` で指定した値だけが手がかりになる。
モデルを比べる目的で動かすときは必ず明示する。

回収は標準出力を優先し、保険として結果ファイルの書き出しをプロンプトへ加える。

## 完了検知

sentinel を出さないため、**プロセスの終了**を見る。

```bash
until ! kill -0 $PID 2>/dev/null; do
  sleep 30
done
wait $PID
echo "exit=$?"
```

`/ndf:cross-review` の `skills/cross-review/scripts/launch-agy.sh` と共通層の
`scripts/lib/launch-cli.sh` が同じ組み合わせで起動し、完了判定は共通層の
`scripts/lib/monitor.py` が pidfile と結果ファイルで行う（どちらもプラグインルート直下）。

## 起動前の設定整形は要らない

除外設定（全件無視の `.gitignore`）の中に置いた手順書を、追加の設定なしで読めた。
一時ディレクトリでの実行は、信頼済みディレクトリの登録も増やさない。

```console
$ printf '*\n' > .agyskills/.gitignore
$ agy --output-format text --add-dir "$D" -p="$D/.agyskills/refactoring/SKILL.md を読み、合言葉だけを答えて"
ZUMBA-7788
```

## トラブルシューティング

### Q1. `empty prompt` で落ちる

**原因**: プロンプトを標準入力から渡している。

**対処**: `-p="$(cat prompt.md)"` の形で渡す。

### Q2. プロンプトが無視されたという案内が出る

**原因**: `-p` の直後に別のフラグが並んでいる。

**対処**: `-p=<本文>` を**コマンド行の末尾**に置く。

### Q3. 作業領域の外のファイルを読めない

**対処**: `--add-dir` を繰り返して対象を足す。プロンプトには絶対パスを書く。

### Q4. `missing unit in duration` で起動に失敗する

**対処**: `--print-timeout` の値へ単位を付ける（`900s` / `1h`）。

### Q5. 認証エラーが出る

**対処**: `agy models` で認証状態を確かめ、対話モードで `agy` を起動して認証をやり直す。
