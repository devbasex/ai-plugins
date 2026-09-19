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
| error | integration | — | kiro | 採用 | 1 |

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
| error | integration | — | codex | 採用 | 1 |

**なぜ**: 新 PR 作成失敗時に旧 PR を reopen する経路は light モードだけ固定され、同じ ERR trap を使う squash モードでは未固定である

**手順**: 1. squash の close までは成功し、gh pr create だけが失敗する代替 git・gh と一時 state を用意する
2. rotate-pr.sh execute --mode squash を公開 CLI から実行する
3. 非ゼロ終了、新 PR が存在しないこと、旧 PR の最終状態が open に戻ること、成功用の NEW_PR が出ないことを比較する

## ラウンド 2（実装 agy / レビュー codex / kiro）

### R2-001 — `plugins/ndf/skills/cross-review/scripts/measure.py#measure`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| branch | unit | — | codex / agy | 採用 | 1 |

**なぜ**: measure の oracle 算出において、resolved_thread_positions の要素が辞書形式である経路は固定されているが、リスト内に非辞書要素（文字列や null など）が混在した場合にそれを unmatched として数えて測定を継続する分岐が未固定である

**手順**: 1. resolved_thread_positions に文字列や null などの非辞書要素を含む状態ファイルを用意する
2. measure を実行する
3. oracle の found が 0、unmatched が 1、ambiguous が 0 と計算され、例外を出さずに全体の測定結果が返ることを確かめる

### R2-002 — `plugins/ndf/skills/cross-review/scripts/rotate-pr.sh#cmd_execute`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| boundary | integration | — | agy / kiro | 採用 | 1 |

**なぜ**: cmd_execute の引数解析は不正な --mode 値と未知フラグ（exit 2）は固定済みだが、--mode の直後に値が無いとき ${2:?--mode requires light|squash} で落ちる境界と、そもそも引数が 0 個のときの entrypoint の usage（exit 2）は固定されていない。

**手順**: 1. rotate-pr.sh execute <STATE_PR> --mode を値なしで実行し、終了コードが 0 以外で stderr に --mode requires light|squash が出ることを確かめる
2. rotate-pr.sh を引数なしで実行し、終了コードが 2 で usage が stderr に出ることを確かめる
3. いずれも state.json を用意せず、gh/git を呼ぶ前の引数解析だけで止まることを確かめる

### R2-003 — `plugins/ndf/skills/cross-review/scripts/rotate-pr.sh#execute_light`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| branch | integration | — | agy / kiro | 取り消し | 1 |

**なぜ**: execute_light は prepare.json / newtext.json の有無と title/body の null を 4 本の分岐で弾くが、既存テストは prepare.json と newtext.json が両方揃った成功・失敗経路（_Rotation）しか通していない。前提ファイルが欠ける分岐と、newtext.json の title が空・body が null になる分岐はどのテストも到達していない。

**手順**: 1. test_rotate_pr_queue.py の _Rotation と同じ組み立て（state.json・bin の gh/git 代替）を使い、gh/git は呼ばれる前に止まることを見込む
2. prepare.json を書かずに execute --mode light を実行し、終了コードが 1 で stderr に prepare.json not found が出ることを確かめる
3. prepare.json は置き newtext.json を書かずに実行し、終了コード 1 と stderr の newtext.json not found を確かめる
4. newtext.json に {"title": "", "body": "x"} を書いて実行し、終了コード 1 を確かめる
5. newtext.json に {"title": "x", "body": null} を書いて実行し、終了コード 1 を確かめる
6. いずれの分岐でも gh の呼び出し記録（GH_CALLS）が空で、旧 PR を close していないことを確かめる

### R2-004 — `plugins/ndf/skills/cross-review/scripts/rotate-pr.sh#load_state`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| error | integration | — | agy / kiro | 検証中 | 1 |

**なぜ**: load_state は state.json が不在または空のときに終了コード 1 と state.json not found を返して中断するが、rotate-pr.sh の公開入口を経由してこのエラー経路を通すテストが無い。launch-reviewer.sh 等では固定されているが rotate-pr.sh では未固定である

**手順**: 1. CROSS_REVIEW_TMP_DIR を空の temp ディレクトリに向け、state.json を置かない
2. rotate-pr.sh execute <STATE_PR> を実行する
3. 終了コードが 1 であることを確かめる
4. stderr に state.json not found が含まれることを確かめる
5. gh/git の代替を PATH に置き、呼び出し記録が空（load_state の手前で止まる）であることを確かめる

### R2-005 — `plugins/ndf/skills/cross-review/scripts/state.py#cmd_set_current_pr`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| branch | unit | — | codex / agy | 採用 | 1 |

**なぜ**: cmd_set_current_pr において、pr_history に既に複数の履歴（過去に閉じた PR と現在開いている PR）が存在する場合に、過去 PR のエントリを変更せず直前の現在 PR のみ closed_at と rounds を更新して新 PR エントリを追加する分岐が未固定である

**手順**: 1. 閉じた過去 PR（closed_at 設定済み）と現在の PR（closed_at が None）を順に含む pr_history を持つ状態ファイルを用意する
2. cmd_set_current_pr(pr, new_pr, head_branch) を実行する
3. 過去 PR の closed_at や rounds が変更されず保持されることを確かめる
4. 直前の現在 PR に closed_at が記録され、rounds がその PR のラウンド数と一致することを確かめる
5. 新 PR エントリが closed_at: None、rounds: 0 で末尾に追加されることを確かめる

## 見送った項目

| ラウンド | 対象 | 兆候・経路 | 理由 |
| --- | --- | --- | --- |
| 1 | `plugins/ndf/skills/cross-review/scripts/rotate-pr.sh#execute_squash` | normal | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_check_oscillation` | branch | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_check_oscillation` | normal | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_collect_critiques` | boundary | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_collect_critiques` | error | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_verify_findings` | error | 1 ラウンドの採用上限 5 件を超えた |
| 2 | `plugins/ndf/skills/cross-review/scripts/launch-reviewer.sh#launch_reviewer` | branch | 1 ラウンドの採用上限 5 件を超えた |
| 2 | `plugins/ndf/skills/cross-review/scripts/rotate-pr.sh#execute_light` | branch | コミット 4cd469bc388e45e7c6e77f0793dc45cbad66c08c にトレーラーが欠けています: Item-Id, Round, Impl-Runtime, Impl-Model |
