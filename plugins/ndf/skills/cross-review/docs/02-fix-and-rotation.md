# 02: 修正・PR の巻き直し・終わり方

`ndf cross-review run` がラウンドの中で行う修正と、終わったときの結果を持つ。ラウンドの進み方は
[01-state-and-review.md](01-state-and-review.md) にある。

**目次:** [修正](#修正) / [送る](#送る) / [終わり方](#終わり方) / [PR に残す記録](#pr-に残す記録) / [PR を巻き直す](#pr-を巻き直す) / [駆動の外で直したとき](#駆動の外で直したとき)

## 修正

**メインは修正コードを書かない。** 判定が修正を要すると、`run` が修正担当（claude）を作業の worktree で起動する。
修正担当へ渡す指示は、収束の妨げになる重さの未解決の指摘（`id`・本文・重要度・場所）と、まだ決めていない足すべき内容
（`--findings`）の一覧と、相手役と同じ「指摘の基準」（[01](01-state-and-review.md) の「指摘の基準」）である。`minor`・`nit` の
指摘は渡さない。担当は指摘ごとに基準に当たるかを確かめ直し、当たれば直し、当たらなければ対象のファイルを変えずに当たらない
理由を添えて見送る。この PR の目的の外の指摘は `/ndf:out-of-scope` で Issue を起票し、その番号を見送りの理由に書く。直したら
コミットする（push しない）。返信と resolve は `run` が行う（下の「PR に残す記録」）ので、担当は GitHub へ書かない。
終えたら修正の結果ファイル（`--state-dir` の `fix-pr<元の番号>-result.json`）へ次の形を書く。

```json
{"pr": 123, "commit": "<最後のコミット>", "findings": [{"id": "r1-1", "outcome": "fixed", "reason": null}, {"id": "r1-2", "outcome": "deferred", "reason": "<見送った理由>"}]}
```

`run` は結果ファイルを読んでジャーナルの指摘ごとの終わり方へ写し、状態ファイルのラウンドへ修正の記録（`commit`・`reported`・
`fixed`）を書いてから、結果ファイルを `-round<n>` の名前へ移す（次の起動が残りと読み違えない）。

| 起きたこと | 扱い |
| --- | --- |
| 修正担当が結果ファイルを書かなかった | `warnings` に載せ、そのラウンドを `errored` で終える。次の起動で修正担当をもう一度起動する |
| 結果ファイルの `pr` が元の番号とも今の番号とも合わない・読めない | 結果ファイルを `-mismatch-round<n>` へ退避し、`errored` で終える |
| 担当が DONE で終わらなかった（タイムアウトなど）が結果ファイルはある | 読み込む（直した・見送ったを失わない） |

## 送る

修正のコミットは `run` が head ブランチへ送る。拒まれたら origin をマージして 1 回だけ送り直す。マージで衝突したら取り消して
止める。送れなかったときは、そのラウンドで直したとした指摘を未解決へ戻し（PR に届いていない）、`errored` で終える。
次の起動は、始めに送れなかったコミットを送り直してから進む。送るだけを打つときは
`ndf cross-review push-fix <worktree> --branch <B> [--commit <C>]` を使う。

## 終わり方

`run` は終わると `--state-dir` へ要約（`summary.json`）を書き、終わり方から結果 JSON を出す。同じ判断を要約から出し直すときは
`ndf cross-review finish --summary <summary.json> [--findings <F>]` を打つ（読むだけ）。

| 終わり方（`final`） | 欠け（`gaps`） | 結果 |
| --- | --- | --- |
| `converged`・`max_rounds`・`oscillating`・`errored` | 0 件 | `status: ok`。上限・振動・誤りで終わったときは、そう決めた理由が `decisions` に載る |
| `converged`・`max_rounds`・`oscillating`・`errored` | 収束の妨げになる重さの未解決の指摘・扱わなかった足すべき内容・理由の無い見送りが 1 件以上 | `status: stopped`・`incomplete`（`gaps` つき）。プランは `incomplete` のルートへ進み、`gaps` は承認資料の「未確認の点」になる |
| `incomplete_reviewers`（起動し直しても読めない相手役がいた） | どれでも | `status: stopped`・`work_stopped`。相手役ごとの理由は `warnings`。直して同じコマンドを打ち直せば次のラウンドから続く |
| `errored` で 1 ラウンドも終わらなかった | — | `status: stopped`・`work_stopped` |

`minor`・`nit` の未解決の指摘は欠けにしない。`summary` に「軽い指摘 <件数>」と載る。

結果 JSON の `items` は指摘ごとの `id` と `outcome`、`metrics` は `rounds`（終えたラウンドの数）と `findings`（指摘の数）、
`process` は起動ごとの過程（相手役ごとの指摘の数・修正担当・push）である。

## PR に残す記録

`run` は終わるときに、PR へ次を書く（#3939）。書けなければ終わり方は変えず `warnings` に残すので、
`ndf cross-review report --state-dir <D> --pr <N>` で書き足す。

- 収束の妨げになる重さの指摘をインラインのスレッドにする。相手役が返した `path`・`line` があればそこに付け、無ければ
  `section` のパスと本文の先頭の `<名前>:<行>` から付ける。`minor`・`nit` の指摘はスレッドにしない
- 直した指摘には修正のコミットを、見送った指摘には理由を返信して resolve する。未解決の指摘のスレッドは開いたまま残す
  （次の実行が引き継ぐ。[01](01-state-and-review.md) の「残った指摘を引き継ぐ」）
- ラウンドと修正の記録・指摘ごとの重要度・場所・終わり方を 1 つのコメントに書く（次の起動は同じコメントを書き換える）。
  コメントの末尾には、未解決の重い指摘の一覧（スレッドの id を含む）を HTML のコメント `<!-- ndf:cross-review-open [...] -->` で
  埋める（スレッドにならなかった指摘を次の実行が引き継ぐ）

書く操作が GitHub の二次レート制限（403・429 で `secondary rate limit`）に当たったら、GitHub が返した秒（無ければ
`contracts/limits.json` の `cross-review.secondary-rate-limit` の `interval`）を待って打ち直す。回数の上限は同じ行の `attempts`。
スレッドを続けて作るときは `cross-review.thread-spacing` の間隔を空ける。

## PR を巻き直す

`run` は PR を巻き直さない。上限まで回して収束しない PR を閉じて出し直すときは、メインが手で行う。

1. 新しい PR を作る（`gh pr create --base <マージ先> --head <ブランチ> --title <題> --body-file <本文>`）。title と body は
   cross-review の経緯でなく、今の差分と実装から書く
2. 旧い PR を閉じる（`gh pr close <旧い番号> --comment "<新しい PR の URL> へ出し直した"`）
3. 状態ファイルへ今の番号を記録する（`ndf cross-review state-file --state-dir <D> --pr <元の番号> --current <新しい番号>`）

状態ファイルと修正の結果ファイルは元の番号のまま残り、`--pr` に今の番号を渡しても元の状態ファイルを引く。

## 駆動の外で直したとき

`run` が止まった後に残った修正の結果ファイルを読み込むときは、`ndf cross-review fix-gate --state-dir <D> --pr <N>` で要否を
確かめ、`next` に返った `ndf cross-review merge-fix --state-dir <D> --pr <N> --file <結果ファイル>` を打つ。`run` と同じ形で
ジャーナルへ写す。ホストが自分で直したときも、同じ形の結果ファイルを書いて `merge-fix` を通す（送るのは `push-fix`）。
