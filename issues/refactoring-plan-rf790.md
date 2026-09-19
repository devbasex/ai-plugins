# 改修計画 — devbasex/ai-plugins #790

`/ndf:cross-refactoring` が提案し、適用した改善項目の記録である。
理由と手順は提案の時点でしか残らないため、公開の直前に書き出している。

- 対象範囲: plugins/ndf/skills/cross-review/scripts, plugins/ndf/skills/cross-review/tests
- 着手前のテスト: uv run --with pytest pytest scripts/tests plugins/ndf -q

## ラウンド 1（実装 codex / レビュー agy / kiro）

### R1-001 — `plugins/ndf/skills/cross-review/scripts/measure.py#measure`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| boundary | unit | — | codex / agy | 採用 | 1 |

**なぜ**: measure 関数に None や辞書以外の型が渡されたとき、空辞書にフォールバックして例外なく指標辞書（pr, prs, rounds, methods, cost, convergence）を返す境界値の振る舞いが固定されていない

**手順**: 1. evidence_rounds に文字列の有効ラウンド番号、重複する整数、不正値を含み、印付き・印なし両方の findings を持つ state を作る
2. measure を実行する
3. 有効番号が一つの印として扱われ、不正値が無視され、proposed の found・oracle_scope・oracle_base が現在の値になることを比較する

### R1-002 — `plugins/ndf/skills/cross-review/scripts/critique-round.sh#critique-round`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| branch | integration | — | kiro | 採用 | 1 |

**なぜ**: run_round を通す既存テストは 2 本とも指摘 0 件で、collect-critiques が 1 回目に 0 を返して 1 回で抜ける経路しか固定していない。exit 7 と CRITIQUE_RETRY_AGENTS を受けて 2 回目の起動を回すループ本体（for _attempt in 1 2）はどのテストも通っていない。

**手順**: 1. critique-round.sh を temp ディレクトリへ複製し、隣に stub の critique.sh・monitor.py・state.py・_tmpdir.sh を置く（test_wait_review.py と同じ、兄弟スクリプトを差し替える方式）
2. stub の state.py collect-critiques を、呼び出し回数を記録したうえで 1 回目は stdout に CRITIQUE_RETRY_AGENTS='kiro' を出して終了コード 7、2 回目は終了コード 0 を返すようにする
3. stub の critique.sh は渡された担当名を追記で記録し、対応する pid ファイルを作る
4. critique-round.sh に PR・ROUND・agy kiro を渡して実行し、終了コード 0 を確かめる
5. critique.sh の記録が 2 回目は kiro だけへ絞られている（agy は再起動されない）ことと、collect-critiques が 2 回呼ばれたことを比較する

### R1-003 — `plugins/ndf/skills/cross-review/scripts/critique-round.sh#critique-round`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| error | integration | — | kiro | 検証中 | 1 |

**なぜ**: collect-critiques が 7 以外を返したときに exit "$COLLECT_RC" でその終了コードを素通しする分岐が固定されていない。既存テストは 0 で抜ける経路だけを見ており、失敗の終了コードがラウンドの外へ伝わるかを誰も確かめていない。

**手順**: 1. critique-round.sh を temp ディレクトリへ複製し、隣に stub の critique.sh・monitor.py・state.py・_tmpdir.sh を置く
2. stub の state.py collect-critiques を、呼び出し回数を記録して終了コード 5 で終わるようにする
3. critique-round.sh に PR・ROUND・担当を渡して実行する
4. 終了コードが 5（collect-critiques が返した値）と一致することを確かめる
5. collect-critiques が 1 回だけ呼ばれ、2 回目の起動へ進んでいないことを記録から確かめる

### R1-004 — `plugins/ndf/skills/cross-review/scripts/rotate-pr.sh#cmd_prepare`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| normal | integration | — | codex | 採用 | 1 |

**なぜ**: 公開入口 prepare は state、GitHub の PR メタデータ、git log/diff をつないで prepare.json と eval 用の出力を作るが、既存テストは生成済み prepare.json を与えるだけで、この経路を実行していない

**手順**: 1. 一時 worktree と state を作り、gh pr view・git fetch/log/diff を現状の出力を返す代替コマンドへ差し替える
2. rotate-pr.sh prepare を公開 CLI から実行する
3. 終了コード 0、stdout の shell 代入を評価して得る値、prepare.json の PR・branch・draft・round・git 要約を比較する

### R1-005 — `plugins/ndf/skills/cross-review/scripts/rotate-pr.sh#execute_squash`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| error | integration | — | codex | 検証中 | 1 |

**なぜ**: 新 PR 作成失敗時に旧 PR を reopen する経路は light モードだけ固定され、同じ ERR trap を使う squash モードでは未固定である

**手順**: 1. squash の close までは成功し、gh pr create だけが失敗する代替 git・gh と一時 state を用意する
2. rotate-pr.sh execute --mode squash を公開 CLI から実行する
3. 非ゼロ終了、新 PR が存在しないこと、旧 PR の最終状態が open に戻ること、成功用の NEW_PR が出ないことを比較する

## 見送った項目

| ラウンド | 対象 | 兆候・経路 | 理由 |
| --- | --- | --- | --- |
| 1 | `plugins/ndf/skills/cross-review/scripts/rotate-pr.sh#execute_squash` | normal | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_check_oscillation` | branch | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_check_oscillation` | normal | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_collect_critiques` | boundary | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_collect_critiques` | error | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_verify_findings` | error | 1 ラウンドの採用上限 5 件を超えた |
