<!-- ndf-excerpt: progress-tracking -->
# progress-tracking の抜粋

## 呼び出し

ndf progress show <ステートマシンの ID>

- 工程の記録はステートマシンが持つ。工程の終わりは工程を流すプランが付け、課題の本文の `## 進行` と
  スプリントの Pull Request の `## 通した工程` を終了のマーカーから書き直す
- supervisor と worker は記録を打たない。現在地を確かめるときだけ上の 1 行を打つ

## 結果の読み方

標準出力は 1 行の結果 JSON である（形と終了コードの意味は `plugins/ndf/docs/contracts/exit-codes.md` と
`plugins/ndf/contracts/schemas/result.schema.json`）。止まっていたら `summary` と `next` を読む。

| 状態 | 次にすること |
| --- | --- |
| `ok` | 先へ進む |
| `ok` 以外 | `summary` を報告の「見つけたもの」へ載せて先へ進む（記録が理由で工程を止めない） |

## 判断の基準

- 本文の `## 進行` を手で書き換えない（次の書き直しで消える）
- 工程を飛ばす・やり直す・終える操作は conductor だけが行う（`ndf progress disable` / `retry` / `finish`）
- 課題を閉じるのは「スプリントを閉じる」だけで、最終工程を出るときに 1 度だけ行う。worker は行わない
