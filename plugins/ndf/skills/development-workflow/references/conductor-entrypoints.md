# conductor が直に使うエントリポイント

conductor が自分で打つ NDF のスクリプトはこの一覧に限る。**一覧にあるものは起動指示や引継ぎ文書に
コピーしない。** ここに無い操作を手で組み立てそうになったら、先に一覧の中の近いプランを探す。

例: マージ済みの PR #1211 の不具合を即時修正すると決めたときは、`gh` を手で打たずに次の 3 行で流す。
差し込みはテンプレート（`contracts/templates/fix.json` の `params`）が受ける名前だけで、課題の番号は PR の題と本文へ書く。
作業場所（`worktree`）を省けばリポジトリの根の `.worktrees/<branch>` を使い、並びの差し込み（`required`・`tests`）は `--add` で 1 つずつ足す。
リポジトリの設定にテストを CI に寄せる値（`test.ci.only`）があれば、プランは手元のテストのステップを持たず、`test_command` は要らない。

```bash
ndf route render immediate_fix --set branch=fix/1211 --set title="Fix: ...（#1211）" --set body_file=<本文>.md \
  --set test_command="<テストのコマンド>" --add required=<待つ必須のチェック> --out <プラン>.json
ndf plan queue <プラン>.json
ndf plan wait <プラン>.json
```

**エントリポイントは PATH 上のコマンド `ndf` で打つ**（有無の確かめは [scripts-lookup.md](scripts-lookup.md)）。
引数は [commands.md](../../../docs/contracts/commands.md) と各サブコマンドの `--help` が正である。

## プランを作って流す

| エントリポイント | 使うとき |
| --- | --- |
| `ndf progress new` | スプリントの工程を 1 本のプランにする |
| `ndf route render implement` | worker の実装から始まるプランを作る |
| `ndf route render immediate_fix` | 即時修正（テスト → PR → `merge-when-green`）のプランを作る。条件は [pace.md](pace.md) |
| `ndf route render check` | 検査のプランを作る |
| `ndf route render release` | デプロイ（開発版・本番）のプランを作る |
| `ndf route render close` | 確定仕様化と PR・CI・マージのプランを作る。振り返り・棚卸し・課題を閉じる `close` のステップを持たないため、スプリントの終わりのまとめは P1b #3092 が `ndf` のサブコマンドに足す。新しいプラグインだけではまだ打てない |
| `ndf plan queue` | プランを同時に `--max` 本まで順に流す |
| `ndf plan wait` | queue の終わりか attention の行まで待つ。待ち方は [waiting.md](waiting.md) |
| `ndf plan next` | プランを 1 本だけフォアグラウンドで流す |
| （無し。M7 #3302 が `ndf` のサブコマンドに足す。新しいプラグインだけではまだ打てない） | フェーズレポートから引継ぎ文書の表へ 1 行を足す |

## スプリントの状態と承認ゲート

| エントリポイント | 使うとき |
| --- | --- |
| `ndf progress show` | スプリントのステートマシンの今の状態を出す |
| `ndf answer` | 承認ゲートの回答（承認・変更要求・不承認）をステートマシンへ書く。打つ場面は [approval-request.md](approval-request.md) の「回答を記録する」 |
| `ndf answer list` | 書いた回答を古い順に出す |
| `ndf records mvv gate` | 承認ゲートの前に MVV の判定の材料を出す（`--gate <design か release>`。[pace.md](pace.md) の「MVV 判定」） |
| `ndf records mvv signals` | プロジェクト MVV の改訂の兆候を出す。手順は [project-mvv.md](project-mvv.md) |

## 検査・マージ・リリース

| エントリポイント | 使うとき |
| --- | --- |
| `ndf loop check-trigger` | 検査を回すかを、範囲の変更行数（除く PR のマージを数えない）で判定する（立つ 0 / 立たない・読めないは止まり） |
| `ndf route render immediate_fix` | CI が通るまで待ってマージするプランを作る（上の「プランを作って流す」） |
| `ndf release-steps materials` | 本番の承認資料のうち機械で作れる部分を書き出す |
| `ndf release-steps present` | 承認資料を、提示した HEAD の SHA を添えてリリースの Pull Request へコメントする |
| `ndf release-steps answer` | 承認の回答を証跡のコメントに書く。マージは workflow（`merge`）が行う。分け方は [release-steps.md](../../release/references/release-steps.md) の「サブコマンド」 |
| `ndf release-steps records` | Pull Request のリリース記録とリリース後テストの記録を読む |
| `ndf worktree merged` | マージ済みの PR の worktree とブランチを片付け、メインディレクトリをマージする |

## セッションの切り替えと記録

| エントリポイント | 使うとき |
| --- | --- |
| `ndf relay notice` | 切り替えの前に、利用者へ出す案内を得る。仕組みは [relay.md](relay.md) |
| `ndf relay status` | ラッパーの導入の状態を確かめる |
| `ndf records handoff check` | 引継ぎ文書のセクションの形と行数を確かめる（`--trim --history <退避先>` で「前の会話の進み」を履歴へ移す）。引継ぎ文書の作成・書き換え・削除は、[handoff.md](handoff.md) の保存先のファイルを手で読み書きする |
