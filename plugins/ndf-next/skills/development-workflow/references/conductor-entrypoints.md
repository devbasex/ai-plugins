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

**`ndf` で始まる行は PATH 上のコマンド `ndf` で打つ**（`$SCRIPTS` は要らない）。それ以外の `scripts/` で始まる行は
旧いプラグインの根からの相対で、`$SCRIPTS/` の下にある（決め方は [scripts-lookup.md](scripts-lookup.md)）。
新しいプラグインだけの起動では `$SCRIPTS` が空のため、`scripts/` の行は打てない。引数は各サブコマンドの `--help` が正である。

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
| `scripts/parallel-measure.py capacity` | 並列に起動してよい本数を出す |

## スプリントの状態と承認ゲート

| エントリポイント | 使うとき |
| --- | --- |
| `scripts/sprint-state.py init` | スプリントの状態ファイルを作る |
| `scripts/sprint-state.py update` | 終えたプランと次のプランを状態ファイルへ書く |
| `scripts/sprint-state.py gate` | 承認ゲートの判定（利用者か MVV）を状態ファイルへ書く。利用者の回答は `--what <要約> --by user [--pr <設計 PR>] --outcome approved\|rejected`（承認も変更要求も変更要求の記録へ 1 行書く。変更要求は承認ゲートを通さない。`reject` のステップを持つプランの変更要求は `ndf plan next <プラン> --from reject` が書く） |
| `scripts/gate-rejections.py record` | 状態ファイルの無い進め方で、承認ゲートの変更要求を変更要求の記録へ 1 行書く。打つ場面は [approval-request.md](approval-request.md) の「回答を記録する」 |
| `scripts/gate-rejections.py count` | 承認ゲートの変更要求を、スプリント・課題・PR ごとに設計承認ゲート・リリース承認ゲートに分けて数える（読めなければ件数は「不明」、範囲に回答の行が無ければ「未記録」） |
| `scripts/project-mvv.py` | プロジェクト MVV の判定（`check`）・材料（`collect`）・候補（`propose`）・検証（`vet`）・承認の書き込み（`approve`）・バージョン（`show`）・セクション（`context`）・改訂の兆候（`signals`）。手順は [project-mvv.md](project-mvv.md) |
| `scripts/sprint-state.py status` | 今の状態を出す |
| `scripts/sprint-state.py next` | 切れ目で引継ぎ文書へ置く ndf-next のブロックを作る |
| `scripts/sprint-state.py render` | 状態を表に書き出す |
| `scripts/mvv-gate.py check` | 承認ゲートの前に MVV の判定を行う（`--advise` は `normal` の助言の MVV 判定。[pace.md](pace.md) の「MVV 判定」） |
| `scripts/glossary.py gate` | 設計の工程の始めに用語集があるかを確かめる |

## 検査・マージ・リリース

| エントリポイント | 使うとき |
| --- | --- |
| `scripts/check-trigger.py eval` | 検査を回すかを判定する |
| `scripts/check-trigger.py stats` | 検査のラウンド数と指摘の数を集計する |
| `scripts/merged-steps.py merge-when-green` | CI が通るまで待ってマージし、クリーンアップまで行う。マージ先が自動反映の本番チャネルなら `--gate-approved user\|mvv` が無い限り CI を待たずにリリース承認ゲート（終了コード 10）で止まる |
| `scripts/merged-steps.py merge-gate` | マージ先へのマージがリリース承認ゲートに当たるかだけを判定する（0 = 進めてよい / 10 = リリース承認ゲート） |
| `scripts/merged-steps.py promote` | リリースの Pull Request（ベースブランチ → 本番チャネル）を作り、リリース承認ゲートの後にマージする（クリーンアップはしない） |
| `scripts/merged-steps.py cleanup` | マージ済みの PR の worktree とブランチを片付ける |
| `scripts/release-steps.py approval-facts` | 本番の承認資料のうち機械で作れる部分を書き出す |
| `scripts/release-verification-steps.py verify-install` | 開発版を導入して確かめる |
| `scripts/sprint-close.py` | スプリントを閉じる（ふつうは `new close` のプランが呼ぶ） |

## セッションの切り替えと記録

| エントリポイント | 使うとき |
| --- | --- |
| `ndf relay notice` | 切り替えの前に、利用者へ出す案内を得る。仕組みは [relay.md](relay.md) |
| `ndf relay status` | ラッパーの導入の状態を確かめる |
| `scripts/handoff.py path` | 引継ぎ文書の本体と履歴の、メインディレクトリの絶対パスを得る（`--exists` で本体の有無）。規則は [handoff.md](handoff.md) |
| `scripts/handoff.py init` | 引継ぎ文書が無ければテンプレートから作る |
| `scripts/handoff.py find` | 名指しの無い再開で、入力の語に一致する引継ぎ文書を探す |
| `scripts/handoff.py next` | 引継ぎ文書の「次に実行するコマンド」のセクションを再開コマンドで置き換える |
| `scripts/handoff.py check` | 引継ぎ文書のセクションの形と行数を確かめる（`--trim` で「前の会話の進み」を履歴へ移す） |
| `scripts/handoff.py remove` | 対象が閉じた後に、引継ぎ文書の本体と履歴を消す |
| `skills/skill-stats/scripts/skill-stats.py --agents` | 3 層（conductor / supervisor / worker）の context window を出す |
| `skills/external-ai/scripts/external-ai.py run <CLI>` | 外部 AI に 1 回の質問を投げる |
