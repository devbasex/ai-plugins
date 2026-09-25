# 配布のコマンドを走らせる

**リポジトリに固有の配布の手順は、`release` の本文ではなくリポジトリの宣言 `.ndf/release.json` が
配布のコマンドとして持つ。** `release` は手順 3 で、宣言されたコマンドを 1 行のコマンドで走らせるだけである。
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
(exit "$rc")   # このブロックの終了コードをコマンドの実行の値へ戻す
```

コマンドは `python3 tools/snapshot.py --released 2.4.0` を根で実行する。変えたのが `docs/metrics/` の
中だけなら 0 で終わり、`guide: docs/metrics/README.md` を出す。エージェントはその手引きを読んで
判断の要る部分だけを書き足し、コマンドが書いたファイルと一緒に配布の Pull Request へ入れる。

`$SCRIPTS` の決め方は [scripts-lookup.md](../../development-workflow/references/scripts-lookup.md) にある。

## 段階の値

| `--stage` | いつ渡すか | 走るコマンドの `stage` |
| --- | --- | --- |
| `production` | 本番への配布（正式版として公開する・本番へ反映する） | `production` / `any` |
| `verification` | 検証への配布（開発版として公開する・検証環境へ反映する） | `verification` / `any` |

## 宣言の項目

形は [../schemas/release.schema.json](../schemas/release.schema.json) が定める。

| 項目 | 必須 | 何を決めるか |
| --- | --- | --- |
| `version` | 必須 | 宣言の形の版。`1` だけを読む |
| `steps` | 必須 | コマンドの並び。書いた順に実行し、最初に落ちたコマンドで止める |
| `steps[].name` | 必須 | 出力と完了報告に出す名前 |
| `steps[].stage` | 必須 | `production` / `verification` / `any` |
| `steps[].command` | 必須 | 引数の配列。シェルを通さない。`{version}` を配る版で置き換える（置き換えるのはこの 1 つだけ） |
| `steps[].writes` | 必須 | 書いてよいパスの前置き（根からの相対）。`[]` は何も書かないコマンド |
| `steps[].guide` | 任意 | エージェントが判断する部分の手引き |
| `steps[].timeout_seconds` | 任意 | コマンド 1 つの時間の上限。既定 600、1 以上 |

**`writes` の外を変えたコマンドは失敗にする。** コマンドは任意のコマンドで、配布の Pull Request に何が
混ざるかを本文から読めない。変えたかどうかは、コマンドの前後で `git status` に出たパスの内容を比べて
決める。コマンドの前から変わっていたパスも、コマンドが中身を変えれば（元へ戻しても）数える。

## 終了コード

| 終了コード | 意味 | どうするか |
| --- | --- | --- |
| 0 | 宣言が無い（何も出力しない）・段階に合うコマンドが無い・すべてのコマンドが通った | `guide:` の行があれば、その手引きに従う |
| 1 | コマンドが 0 以外で終わった・時間切れ・書いてよい場所の外を変えた | 出力を読んで直す。直すまで配布の Pull Request を作らない |
| 2 | `check` だけが返す。宣言が無い | 宣言の有無で分岐する側が使う |
| 3 | 宣言が読めない（壊れた JSON・未対応の `version`・必須の項目が無い・値が不正） | 標準エラーに出た項目を直す |

**1 と 3 を 0 へ畳まない。** 走らなかったコマンドを、通ったと報告しない。

## 走らせる前に確かめる

```bash
python3 "$SCRIPTS/release-steps.py" check --root .                                  # 0 / 2（無い）/ 3
python3 "$SCRIPTS/release-steps.py" run --root . --stage production --version 2.4.0 --dry-run
```

`--dry-run` は実行せず、走らせるコマンドと、版を置き換えた後のコマンドを出す。

## Claude Code のプラグインの形のサブコマンド

宣言のコマンドとは別に、Claude Code のプラグインの形では配布の手順そのものをスクリプトが行う。
どれも結果 JSON を出し、`status` と終了コードで読む（`ok` = 0 で次へ、`gate` = 10 で承認を得る、
`stopped` = 1 / 2 / 3 なら `summary` を読んで直すか報告して止まる）。

```bash
# 手順 2: 公開前の提示物のうち機械で作れる部分を書き出す
python3 "$SCRIPTS/release-steps.py" approval-facts --version <版> --prs <PR番号>... [--prev-tag <タグ>] --root .
# 手順 3: 版数と変更履歴を上げる
python3 "$SCRIPTS/release-steps.py" bump      --plugin <名前> --to <版> --root .
python3 "$SCRIPTS/release-steps.py" changelog --version <版> --prs <PR番号>... --root .
python3 "$SCRIPTS/release-steps.py" notes     --version <版> --prs <PR番号>... --root .
# 手順 2 の提示物の欄を埋める（検証への配布の後）
python3 "$SCRIPTS/release-steps.py" notes     --version <版> --prs <PR番号>... --approval <提示物> \
  --verified claude,codex,kiro --ref develop --root .
# 手順 4: 公開する（bump と changelog の変更をコミットしてから）
python3 "$SCRIPTS/release-steps.py" release --version <版> --channel dev --root .   # 検証への配布
python3 "$SCRIPTS/release-steps.py" release --version <版> --channel prod --root .  # 本番への配布（承認を得てから）
```

- `approval-facts`: `gate` なら `presentation_path` の提示物の「配る中身」と「検証への配布で
  確かめたこと」を `notes --approval` で埋めて利用者へ示し、承認を得てから `next` のコマンドを打つ。`stopped`
  （3 = 前のタグを決められない）なら `--prev-tag` を渡して打ち直す
- `bump`: `items[]` に手で直す箇所が載っていれば直す
- `changelog`: 見出しと PR のタイトルを並べるだけで、本文は書かない。未マージの PR は載せず、番号を
  `metrics.unmerged` へ出す。すべて未マージなら `stopped`（3）で止まるので、マージしてから打ち直すか `--prs` を直す
- `notes`: 各 PR の本文の `## 利用者向けの変化` の箇条（末尾に `（#番号）`）で、CHANGELOG.md の版の節と
  plugin の README の `## v<版> へ更新するとき` の節を組み直す。節が無い・「無し」の PR は題名で代える
  （`metrics.fallback` が件数）。`--approval` を渡すと CHANGELOG と README は変えず、提示物の 2 つの欄と、
  PR の本文の `## 未検証・残る危険` を集めた節を書く。未マージの PR は載せず、番号を `metrics.unmerged` へ出す。
  `stopped`（3）は changelog や approval-facts を先に打つ。渡した PR がすべて未マージのときも 3 で止まるので、
  マージしてから打ち直すか `--prs` を直す
- `release`: `dev` は `release/v<版>` → `develop` の Pull Request を作り、チェックを待ってマージする。
  `prod` は続けて `develop` → `main` をマージし、タグと GitHub Release を作る。`items[]`
  （マージした Pull Request・タグ・GitHub Release）が完了の事実の照会の結果である。
  どちらの Pull Request でも pytest と runtime smoke は省かれて成功（skipping / pass）を返す
  （[版と配布](../../../../../docs/versioning-and-distribution.md#正式版を出す)）。待ちはそれを通ったものとして扱う
