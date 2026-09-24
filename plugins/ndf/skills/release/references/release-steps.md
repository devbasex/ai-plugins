# 配布の段を走らせる

**リポジトリに固有の配布の手順は、`release` の本文ではなくリポジトリの宣言 `.ndf/release.json` が
段として持つ。** `release` は手順 3 で、宣言された段を 1 行のコマンドで走らせるだけである。
宣言が無いリポジトリでは何も起きない。

## 例: 正式版 2.4.0 を出すリポジトリ

正式版を出すたびに、配布の Pull Request へ計測の記録を載せたいリポジトリがあるとする。

```json
{
  "version": 1,
  "steps": [
    {
      "name": "計測の記録",
      "stage": "production",
      "command": ["python3", "tools/snapshot.py", "--released", "{version}"],
      "writes": ["docs/metrics/"],
      "guide": "docs/metrics/README.md",
      "timeout_seconds": 600
    }
  ]
}
```

`release` の手順 3 で、版と変更履歴を上げた後、配布の Pull Request を作る前に次を打つ。

```bash
python3 "$SCRIPTS/release-steps.py" run --root . --stage production --version 2.4.0
rc=$?
echo "exit=$rc"
(exit "$rc")   # このブロックの終了コードを段の実行の値へ戻す
```

段は `python3 tools/snapshot.py --released 2.4.0` を根で実行する。変えたのが `docs/metrics/` の
中だけなら 0 で終わり、`guide: docs/metrics/README.md` を出す。エージェントはその手引きを読んで
判断の要る部分だけを書き足し、段が書いたファイルと一緒に配布の Pull Request へ入れる。

`$SCRIPTS` の決め方は [scripts-lookup.md](../../development-workflow/references/scripts-lookup.md) にある。

## 段階の値

| `--stage` | いつ渡すか | 走る段の `stage` |
| --- | --- | --- |
| `production` | 本番への配布（正式版として公開する・本番へ反映する） | `production` / `any` |
| `verification` | 検証への配布（開発版として公開する・検証環境へ反映する） | `verification` / `any` |

## 宣言の項目

形は [../schemas/release.schema.json](../schemas/release.schema.json) が定める。

| 項目 | 必須 | 何を決めるか |
| --- | --- | --- |
| `version` | 必須 | 宣言の形の版。`1` だけを読む |
| `steps` | 必須 | 段の並び。書いた順に実行し、最初に落ちた段で止める |
| `steps[].name` | 必須 | 出力と完了報告に出す名前 |
| `steps[].stage` | 必須 | `production` / `verification` / `any` |
| `steps[].command` | 必須 | 引数の配列。シェルを通さない。`{version}` を配る版で置き換える（置き換えるのはこの 1 つだけ） |
| `steps[].writes` | 必須 | 書いてよいパスの前置き（根からの相対）。`[]` は何も書かない段 |
| `steps[].guide` | 任意 | エージェントが判断する部分の手引き |
| `steps[].timeout_seconds` | 任意 | 段 1 つの時間の上限。既定 600、1 以上 |

**`writes` の外を変えた段は失敗にする。** 段は任意のコマンドで、配布の Pull Request に何が
混ざるかを本文から読めない。変えたかどうかは、段の前後で `git status` に出たパスの内容を比べて
決める。段の前から変わっていたパスも、段が中身を変えれば（元へ戻しても）数える。

## 終了コード

| 終了コード | 意味 | どうするか |
| --- | --- | --- |
| 0 | 宣言が無い（何も出力しない）・段階に合う段が無い・すべての段が通った | `guide:` の行があれば、その手引きに従う |
| 1 | 段が 0 以外で終わった・時間切れ・書いてよい場所の外を変えた | 出力を読んで直す。直すまで配布の Pull Request を作らない |
| 2 | `check` だけが返す。宣言が無い | 宣言の有無で分岐する側が使う |
| 3 | 宣言が読めない（壊れた JSON・未対応の `version`・必須の項目が無い・値が不正） | 標準エラーに出た項目を直す |

**1 と 3 を 0 へ畳まない。** 走らなかった段を、通ったと報告しない。

## 走らせる前に確かめる

```bash
python3 "$SCRIPTS/release-steps.py" check --root .                                  # 0 / 2（無い）/ 3
python3 "$SCRIPTS/release-steps.py" run --root . --stage production --version 2.4.0 --dry-run
```

`--dry-run` は実行せず、走らせる段と、版を置き換えた後のコマンドを出す。
