# #4092: #4051 の設計: unindex_idle が正常終了のたびに Git で所在を解き直し、別のレーンの書き込みを待つ

正は課題の本文（#4092）で、この文書はその写しである。本文から作り直す。手で直さない。

## 何が起きたか

#4051（計画の索引を、実行中の行が無くなるまで残す）の詳細設計（PR #4083 で main へマージ済み。実装の前）を、#4044 の設計 PR #4087 の cross-review の相手役が読んだ。詳細設計の `unindex_idle`（`issues/issue-4051-design-detail.md` の 40 行）は、自分の計画の後始末にも `read_lanes(Path(where.cwd), where.plan)` を使う。耐久ワークフローを流す `advance` は解決済みの保存先（`where.root`）・接続先（`where.url`）・計画のステートマシンの ID（`plan_id`）を持っているのに、正常に終わるたびに所在を Git で解き直し、書き込み用の開き方で DB を開く。

## 種類

不具合（#4051 の目的「耐久ワークフローがどう終わっても、実行中の行を持つ計画を索引から数える」を満たす後始末が、#4044 の設計が足す「Git を起こさず、書き手を待たずに読む」経路と食い違う形で設計されている。直すのは設計文書で、コードは無い）

## 今の姿

`machine.read_lanes(cwd, plan)` は、作業ディレクトリから保存先と計画の ID を Git で解き（`git rev-parse`・`git worktree list`・`git remote get-url`）、`store.open_store` で DB を開く。`open_store` は表の用意・目印が古いときの `quick_check` とバックアップを行い、SQLite では書き込みのトランザクション（`BEGIN IMMEDIATE`）で開く。#4044 の設計は、Git を起こさずに読み取りだけで開く `store.open_reader(url, root)` と、索引の保存先・接続先・ID で計画を読む `machine.read_indexed` を足す。

SSOT: main の `issues/issue-4051-design-detail.md`（「索引を消す条件」「耐久ワークフローの終わり」）・`issues/issue-4044-design-detail.md`（「関数」の表の `read_indexed`・`open_reader`）。

## 事象

耐久ワークフローが正常に終わるたびに次が起きる（文書と今の `machine.read_lanes`・`store.open_store` から読んだ手順で、実装は無いので実機では試していない）。

- `read_lanes` が保存先と ID を Git で解き直す（`git rev-parse`・`git worktree list`・`git remote get-url`）
- `store.open_store` を通るので、表の用意・目印が古いときの `quick_check` とバックアップ・SQLite の `BEGIN IMMEDIATE` が走り、別のレーンの書き込みが終わるまで後始末が待つ

## 期待する結果

1. `unindex_idle` が、`advance` の解決済みの所在（保存先・接続先・`plan_id`）で計画を読み取りだけで開く経路を使い、起動の許可と同じ `lanes_of` で数える行の有無を決める
2. 正常終了のときに追加で起こす Git の子プロセスの数（0 本）を縛るテストがある
3. 別の接続が `BEGIN IMMEDIATE` で書き込み中でも、後始末が待たずに確定済みの行で判定するテストがある

## 再現手順

1. main の `issues/issue-4051-design-detail.md` の `unindex_idle`（35〜45 行）と、今の `machine.read_lanes`・`store.open_store` を照らす

## 環境

main の `issues/issue-4051-design-detail.md`（設計文書だけ。#4051 は実装の前）

## なぜ困るか

並列の計画を流すすべてのマシンで、耐久ワークフローの終わりごとに Git の子プロセスが 3 本ほど増え、ほかのレーンが書いている間は後始末が待つ。

## 目的

- 耐久ワークフローの後始末が、計画の所在を解き直さず、ほかのレーンの書き込みを待たずに、数える行の有無を決める
- #4051 と #4044 の実装の順と、読み取りの経路の持ち主が 1 通りに決まる

## 対象範囲

含む:
- #4051 の詳細設計の `unindex_idle`・`_indexed_until_done`・`advance` の呼び方と、その異常系・テストの観点を置き換える設計
- 読み取りの経路（`store.open_reader`）を #4051 と #4044 のどちらの実装が足すかと、実装の順
- 置き換えの中身は、この課題の設計文書（`issues/issue-4092-design.md`・`issues/issue-4092-design-detail.md`）に節ごとに書く

含まない:
- `issues/issue-4051-*`・`issues/issue-4044-*` の文書そのものの書き換え。それぞれの実装の PR で、この課題の設計に合わせて直す
- 索引を書く・消す条件（#4051 の決定 1〜3）と、数える行の規則（`lanes_of`・`held_seats`。#4050・#4091 が持つ）
- `store.open_reader` の仕様（#4044 の詳細設計が持つ）
- `advance` の始めの計画の読み込み（`open_store`）と、起動の許可の読み方（`read_lanes`）

## 前提

- 前提 1: `advance` の `where.url` はパスワードを含みうる解決済みの接続先で、同じプロセスの中で後始末へ渡す。索引のファイルにも結果にも書かない
- 前提 2: #4044 の詳細設計の `store.open_reader` の仕様（読み取りだけ・表を作らない・検査をしない・書き手を待たない）を変えずに使う

## 受け入れ条件

- [ ] 1. 設計が、`unindex_idle` を `advance` の `plan_id`・`where.url`・`where.root` で `store.open_reader` を通して計画を開き、`lanes_of` で数える行の有無を決める形に置き換え、`read_lanes`・`open_store` を呼ばないと書いている
- [ ] 2. 設計のテストの観点に、耐久ワークフローが正常に終わるときの後始末で子プロセス（`subprocess.run`・`subprocess.Popen`）が 0 本であることを縛る行がある
- [ ] 3. 設計のテストの観点に、別の接続が `BEGIN IMMEDIATE` で書き込みを開いたままでも、後始末が待たずに確定済みの行で判定する行がある
- [ ] 4. 設計が、`store.open_reader` を #4051 と #4044 のどちらの実装が足すかと実装の順を書き、#4044・#4091 との境界（触る関数と持ち物）を書いている
- [ ] 5. 設計が、#4051 の設計文書・詳細設計のどの節・決定をどう置き換えるかを節ごとに書き、元の文書は #4051 の実装の PR で合わせると書いている

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（設計文書だけ。#4051 の実装でも `ndf lanes run` の引数と結果は変わらない） |
| データ | 変わらない（索引のファイルの形は #4044 が持つ） |
| 既存の振る舞い | 変わらない（#4051 は実装の前） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 手動確認 | 設計 PR の cross-review と設計承認ゲートで、受け入れ条件 1〜5 を設計文書から読む |
| テスト | #4051 の実装の PR の CI（`uv run --frozen --project . --all-extras pytest plugins/ndf/tests/progress -q -n 4`）で、受け入れ条件 2・3 の観点を縛る |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 読み取りだけで開く経路は #4044 の設計が `store.open_reader` と `machine.read_indexed` として足すので、`unindex_idle` はそれに解決済みの URL を渡す形（接続先は `advance` の `where.url` をそのまま使う）で足りる | [issue-4044-design-detail.md](https://github.com/devbasex/ai-plugins/blob/main/issues/issue-4044-design-detail.md) の「関数」の表 | 前提 1・2・受け入れ条件 1 |
| S2 | #4051 を先に実装するなら、読み取りの経路だけを #4051 で先に足してもよい | 起票者（PR #4087 の cross-review を回した AI） | 受け入れ条件 4 |

## リクエスト（原文）

> #4051 の設計: unindex_idle が正常終了のたびに Git で所在を解き直し、別のレーンの書き込みを待つ

（書き手: 起票者（PR #4087 の cross-review を回した AI）、日付: 2026-11-22。課題の題）

> 1. `unindex_idle` が、`advance` の解決済みの所在（保存先・接続先・`plan_id`）で計画を読み取りだけで開く経路を使い、起動の許可と同じ `lanes_of` で数える行の有無を決める
> 2. 正常終了のときに追加で起こす Git の子プロセスの数（0 本）を縛るテストがある
> 3. 別の接続が `BEGIN IMMEDIATE` で書き込み中でも、後始末が待たずに確定済みの行で判定するテストがある

（書き手: 起票者、日付: 2026-11-22。起票時の本文の「期待する結果」）

## 由来

PR #4087 の cross-review のラウンド 4 の指摘 r4-2（major・`issues/issue-4051-design-detail.md:40`）。PR #4087 は #4044 の設計の PR で、#4051 の設計は PR #4083 で main にマージ済みである。#4044 は索引を書く・消す条件を変えない（#4044 の決定 6）ので、`unindex_idle` の読み方は #4044 の受け入れ条件にも直す対象にも入らず範囲外とした。直さないと耐久ワークフローの終わりごとに CLI の起動と待ちが増えるので起票した。根拠: Value 2 / Value 11（MVV バージョン 11）

