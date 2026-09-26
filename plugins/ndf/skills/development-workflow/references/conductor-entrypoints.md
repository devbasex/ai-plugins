# conductor が直に使うエントリポイント

conductor が自分で打つ NDF のスクリプトはこの一覧に限る。**一覧にあるものは起動指示や引継ぎ文書に
書き写さない。** ここに無い操作を手で組み立てそうになったら、先に一覧の中の近いプランを探す。

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
| `scripts/supervise.py new mission` | ミッションの工程を 1 本のプランにする |
| `scripts/supervise.py new impl` | worker の実装から始まるプランを作る |
| `scripts/supervise.py new fix` | 即時修正（テスト → PR → `merge-when-green`）のプランを作る。条件は [pace.md](pace.md) |
| `scripts/supervise.py new check` | 検査のプランを作る |
| `scripts/supervise.py new release` | リリース（開発版・本番）のプランを作る |
| `scripts/supervise.py new close` | ミッションを閉じるプランを作る |
| `scripts/supervise.py queue` | プランを同時に `--max` 本まで順に流す |
| `scripts/supervise.py wait` | queue の終わりか attention の行まで待つ。待ち方は [waiting.md](waiting.md) |
| `scripts/supervise.py run` | プランを 1 本だけ前景で流す |
| `scripts/supervise.py note` | フェーズレポートから引継ぎ文書の表へ 1 行を足す |
| `scripts/parallel-measure.py capacity` | 並列に起動してよい本数を出す |

## ミッションの状態と承認ゲート

| エントリポイント | 使うとき |
| --- | --- |
| `scripts/mission-state.py init` | ミッションの状態ファイルを作る |
| `scripts/mission-state.py update` | 終えたプランと次のプランを状態ファイルへ書く |
| `scripts/mission-state.py gate` | 承認ゲートの判定（利用者か MVV）を状態ファイルへ書く |
| `scripts/mission-state.py status` | 今の状態を出す |
| `scripts/mission-state.py next` | 切れ目で引継ぎ文書へ置く ndf-next の囲みを作る |
| `scripts/mission-state.py render` | 状態を表に書き出す |
| `scripts/mvv-gate.py check` | 承認ゲートの前に MVV の判定を行う |
| `scripts/glossary.py gate` | 設計の工程の入口で用語集があるかを確かめる |

## 検査・マージ・リリース

| エントリポイント | 使うとき |
| --- | --- |
| `scripts/check-trigger.py eval` | 検査を回すかを判定する |
| `scripts/check-trigger.py stats` | 検査のラウンド数と指摘の数を集計する |
| `scripts/merged-steps.py merge-when-green` | CI が通るまで待ってマージし、後片付けまで行う |
| `scripts/merged-steps.py cleanup` | マージ済みの PR の worktree とブランチを片付ける |
| `scripts/release-steps.py approval-facts` | 本番の承認資料のうち機械で作れる部分を書き出す |
| `scripts/release-verification-steps.py verify-install` | 開発版を導入して確かめる |
| `scripts/mission-close.py` | ミッションを閉じる（ふつうは `new close` のプランが呼ぶ） |

## セッションの切り替えと記録

| エントリポイント | 使うとき |
| --- | --- |
| `scripts/relay.py notice` | 切り替えの前に、利用者へ出す案内を得る。仕組みは [relay.md](relay.md) |
| `scripts/relay.py status` | ラッパーの導入の状態を確かめる |
| `skills/skill-stats/scripts/skill-stats.py --agents` | 3 層（conductor / supervisor / worker）の context window を出す |
| `skills/external-ai/scripts/external-ai.py run <CLI>` | 外部 AI に 1 回の問いを投げる |
