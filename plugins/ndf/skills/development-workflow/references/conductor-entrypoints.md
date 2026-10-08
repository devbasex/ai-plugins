# conductor が直に使うエントリポイント

conductor が自分で打つ NDF のスクリプトはこの一覧に限る。**一覧にあるものは起動指示や引継ぎ文書に
コピーしない。** ここに無い操作を手で組み立てそうになったら、先に一覧の中の近いプランを探す。

例: マージ済みの PR #1211 の不具合を即時修正すると決めたときは、`gh` を手で打たずに次の 3 行で流す。

```bash
python3 "$SCRIPTS/supervise.py" new fix --worktree <作業場所> --tests <テストのパス> \
  --title "Fix: ..." --issue 1211 --escape-of 1211 --out <プラン>.json
python3 "$SCRIPTS/supervise.py" queue <プラン>.json
python3 "$SCRIPTS/supervise.py" wait <プラン>-state/queue-done.json
```

`$SCRIPTS` の決め方は [scripts-lookup.md](scripts-lookup.md) にある。表のパスは `plugins/ndf/` からの
相対で、`scripts/` で始まるものは `$SCRIPTS/` の下、`skills/` で始まるものは `resolve.sh skill` で
決めた Skill のディレクトリの下にある。引数は各スクリプトの `--help` が正である。

## プランを作って流す

| エントリポイント | 使うとき |
| --- | --- |
| `scripts/supervise.py new sprint` | スプリントの工程を 1 本のプランにする |
| `scripts/supervise.py new impl` | worker の実装から始まるプランを作る |
| `scripts/supervise.py new fix` | 即時修正（テスト → PR → `merge-when-green`）のプランを作る。条件は [pace.md](pace.md) |
| `scripts/supervise.py new check` | 検査のプランを作る |
| `scripts/supervise.py new release` | リリース（開発版・本番）のプランを作る |
| `scripts/supervise.py new close` | スプリントを閉じるプランを作る |
| `scripts/supervise.py queue` | プランを同時に `--max` 本まで順に流す |
| `scripts/supervise.py wait` | queue の終わりか attention の行まで待つ。待ち方は [waiting.md](waiting.md) |
| `scripts/supervise.py run` | プランを 1 本だけフォアグラウンドで流す |
| `scripts/supervise.py note` | フェーズレポートから引継ぎ文書の表へ 1 行を足す |
| `scripts/parallel-measure.py capacity` | 並列に起動してよい本数を出す |

## スプリントの状態と承認ゲート

| エントリポイント | 使うとき |
| --- | --- |
| `scripts/sprint-state.py init` | スプリントの状態ファイルを作る |
| `scripts/sprint-state.py update` | 終えたプランと次のプランを状態ファイルへ書く |
| `scripts/sprint-state.py gate` | 承認ゲートの判定（利用者か MVV）を状態ファイルへ書く。利用者の答えは `--what <要約> --by user [--pr <設計 PR>] --outcome approved\|rejected`（差し戻しは承認ゲートを通さず記録だけ残す） |
| `scripts/project-mvv.py` | プロジェクト MVV の判定（`check`）・材料（`collect`）・候補（`propose`）・照合（`vet`）・承認の書き込み（`approve`）・版（`show`）・節（`context`）・改訂の兆候（`signals`）。手順は [project-mvv.md](project-mvv.md) |
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
| `scripts/merged-steps.py merge-when-green` | CI が通るまで待ってマージし、後片付けまで行う。宛先が自動反映の本番チャネルなら `--gate-approved user\|mvv` が無い限り CI を待たずに承認ゲート 2（終了コード 10）で止まる |
| `scripts/merged-steps.py merge-gate` | 宛先へのマージが承認ゲート 2 に当たるかだけを判定する（0 = 進めてよい / 10 = 承認ゲート 2） |
| `scripts/merged-steps.py promote` | 昇格の Pull Request（ベースブランチ → 本番チャネル）を作り、承認ゲート 2 の後にマージする（後片付けはしない） |
| `scripts/merged-steps.py cleanup` | マージ済みの PR の worktree とブランチを片付ける |
| `scripts/release-steps.py approval-facts` | 本番の承認資料のうち機械で作れる部分を書き出す |
| `scripts/release-verification-steps.py verify-install` | 開発版を導入して確かめる |
| `scripts/sprint-close.py` | スプリントを閉じる（ふつうは `new close` のプランが呼ぶ） |

## セッションの切り替えと記録

| エントリポイント | 使うとき |
| --- | --- |
| `scripts/relay.py notice` | 切り替えの前に、利用者へ出す案内を得る。仕組みは [relay.md](relay.md) |
| `scripts/relay.py status` | ラッパーの導入の状態を確かめる |
| `scripts/handoff.py path` | 引継ぎ文書の本体と履歴の、メインディレクトリの絶対パスを得る（`--exists` で本体の有無）。規則は [handoff.md](handoff.md) |
| `scripts/handoff.py init` | 引継ぎ文書が無ければテンプレートから作る |
| `scripts/handoff.py find` | 名指しの無い再開で、入力の語に一致する引継ぎ文書を探す |
| `scripts/handoff.py next` | 引継ぎ文書の「次に実行するコマンド」の節を再開コマンドで置き換える |
| `scripts/handoff.py check` | 引継ぎ文書の節の形と行数を確かめる（`--trim` で「前の会話の進み」を履歴へ移す） |
| `scripts/handoff.py remove` | 対象が閉じた後に、引継ぎ文書の本体と履歴を消す |
| `skills/skill-stats/scripts/skill-stats.py --agents` | 3 層（conductor / supervisor / worker）の context window を出す |
| `skills/external-ai/scripts/external-ai.py run <CLI>` | 外部 AI に 1 回の問いを投げる |
