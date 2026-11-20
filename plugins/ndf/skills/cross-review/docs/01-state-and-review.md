# 01: 状態ファイルとレビューのラウンド

`SKILL.md` の「実行」が打つ `ndf cross-review run` が、1 回の起動で何をするかを持つ。**ラウンドの手順は
`run` の中にあり、メインが手順ごとにコマンドを打つことはない。** 修正と終わり方は
[02-fix-and-rotation.md](02-fix-and-rotation.md) にある。

**目次:** [始め](#始め) / [1 ラウンド](#1-ラウンド) / [範囲](#範囲) / [判定](#判定) / [状態ファイル](#状態ファイル) / [ラウンドを足す](#ラウンドを足す) / [部分の命令](#部分の命令)

## 始め

`run` はラウンドへ入る前に次を行う。どれかで止まれば、結果 JSON の `status` が `stopped` になる。

| 順 | すること | 止まったとき（`cause.kind`） |
| ---: | --- | --- |
| 1 | `--worktree`（省けば打った場所）が git に登録された worktree かを確かめる | `precondition`（`ndf cross-review check-worktree <worktree>` と同じ確かめ） |
| 2 | worktree を origin の head ブランチの先頭へ合わせる。自分のコミットは載せ直す | `precondition`（衝突したファイルを挙げる） |
| 3 | 前の起動が残した修正の結果ファイルを、修正の記録の無い最後のラウンドへ読み込む | `work_stopped`・`invalid` |
| 4 | 前の起動が送れなかった修正のコミットがあれば送る | `work_stopped` |

状態の保存先は `--state-dir` で、状態ファイル（`cross-review-pr<元の番号>-state.json`）・ラウンドごとの差分と
相手役の出力（`round<n>-<相手役>.*`）・修正の結果ファイル・終わったときの要約（`summary.json`）をそこへ置く。
プランから打つときの保存先は `<プランの実行の保存先>/cross-review` である。

## 1 ラウンド

1 ラウンドは次の順に進む。ラウンドは `--max-rounds`（既定 3）まで、1 回の起動の中で続けて回す。

1. **範囲を決める**（下の「範囲」）。`--base...HEAD` の差分をラウンドの差分のファイルへ書く
2. **相手役を並べて起動する。** 相手役は `--reviewer`（繰り返して複数）で名指しし、省けばランタイムの表の既定の席から
   読むだけの起動を持つ者を、修正担当（claude）を除いて並べる。相手役は読むだけで、最後の行に
   `{"findings": [{"text": "<指摘>", "section": "<パス#見出し か 空>"}]}` を 1 行出す
3. **指摘をジャーナルへ入れる。** 同じ趣旨の指摘はキーで重ねない。理由を添えて見送った指摘は、出し直されても扱わない。
   直した指摘が戻ってきたら未解決へ戻す
4. **判定する**（下の「判定」）。修正が要れば修正担当を起動する（[02](02-fix-and-rotation.md)）

**相手役が 1 者も回答しなかったラウンドは、収束の側へ数えない。** 結果 JSON の `warnings` と `process` に、回答しなかった
相手役と理由（タイムアウトなど）が載り、そのラウンドで `errored` として終わる。

## 範囲

設計の PR（`--purpose` が設計のプランのテンプレートの purpose）は、変わった Markdown のセクションが詳細レビューを通るまで
全差分を渡す。詳細レビューを通ったセクションは状態ファイルの `reviewed_sections` に残る。範囲だけを読むときは
`ndf cross-review review-scope <worktree> --purpose <P> --base <B> --state-dir <D> --pr <N>` を打つ（読むだけ）。

## 判定

| ラウンドの状態 | 判定 | 次 |
| --- | --- | --- |
| 未解決の指摘も、まだ決めていない足すべき内容（`--findings`）も無い | `converged` | 終わる |
| 前々ラウンドの指摘が前ラウンドで消えて戻った | `oscillating` | 終わる |
| それ以外 | 修正が要る | 修正担当を起動し、送れたら次のラウンドへ |

上限まで回って修正が要るまま残れば `max_rounds` で終わる。終わり方と結果 JSON の対応は [02](02-fix-and-rotation.md) の「終わり方」にある。

## 状態ファイル

状態ファイルは `{"pr": 元の番号, "current_pr": 今の番号, "target_repo": "<所有者>/<リポジトリ>", "rounds": [...],
"reviewed_sections": [...], "findings": [...]}` の形で、ラウンドごとに書き足す。

| キー | 中身 |
| --- | --- |
| `rounds[]` | `round`・`head`（レビューした head）・`scope`・`verdict`・`fix`（修正の記録: `commit`・`reported`・`fixed`・送れたら `pushed`） |
| `findings[]` | ジャーナル。指摘ごとの `id`（`r<ラウンド>-<通し番号>`）・`key`・`text`・`section`・`round`・`outcome`（`fixed` / `deferred` / `unresolved`）・`reason`・`by` |
| `reviewed_sections` | 詳細レビューを通ったセクション（`<パス>#<見出し>`） |
| `threads` | 指摘の番号ごとに、PR に作ったスレッドの最初のコメントの id（`comment`）と、返信・resolve を済ませたか（`replied`・`resolved`）。打ち直しても二重に書かない（#3939） |
| `report_comment` | PR へ書いた記録のコメントの URL。次からは同じコメントを書き換える（#3939） |

状態ファイルが JSON の辞書として読めなければ、既定へ倒さずに止まる。PR を巻き直したときの番号の引き方は
[02](02-fix-and-rotation.md) の「PR を巻き直す」にある。

## ラウンドを足す

**同じコマンドを打ち直すと、状態ファイルの最後のラウンドの続きから回す。** 前のラウンドは消さず、次の番号のラウンドから
`--max-rounds` の数だけ回す。差分を足した PR にもう一度回すときも、同じコマンドを打ち直すだけである。状態ファイルを
開かない・`mv` しない・書き換えない。

## 部分の命令

`run` が中で行う確かめを、1 つだけ打つ命令である。止まった後に原因を確かめるときに使う。引数は
[commands.md](../../../docs/contracts/commands.md) の `ndf cross-review` にある。

| 命令 | すること |
| --- | --- |
| `ndf cross-review state-file --state-dir <D> --pr <N>` | 状態ファイルと修正の結果ファイルのパス（読むだけ） |
| `ndf cross-review fix-gate --state-dir <D> --pr <N>` | 次のラウンドへ進める前に、修正の記録が要るか（結果ファイルが残っていれば `next` に `merge-fix` を返す） |
| `ndf cross-review review-scope <worktree> ...` | 次のラウンドの範囲（読むだけ） |
| `ndf cross-review check-worktree <worktree>` | worktree の管理情報の確かめ |
| `ndf cross-review target-repo [--repo R]` | レビューの対象のリポジトリ（`--repo`・状態ファイル・origin の順で決める） |
| `ndf cross-review head --pr <N>` | PR の head |
