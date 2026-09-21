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
| error | integration | — | agy / kiro | 採用 | 1 |

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

## ラウンド 3（実装 kiro / レビュー codex / agy）

### R3-001 — `plugins/ndf/skills/cross-review/scripts/state.py#_verify_findings`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | 未着手 | 0 |

**なぜ**: 検証コマンドの正規化・重複実行の抑止・実行結果の分類・各 finding への記録・統合グループ代表の最良結果選択という独立した段階が 1 関数に連続し、実行キャッシュと統合関係の走査を同時に追う必要がある。

**手順**: 1. 1 finding の verification record を生成し、コマンド実行キャッシュを利用して結果を分類する helper を抽出する
2. merged_into の関係をたどって代表へ最良の verification を選ぶ処理を別 helper へ抽出する
3. _verify_findings は対象抽出、各 finding の検証、代表結果の集約という 3 段階だけを並べる
4. test_verify_findings.py と findings pipeline の既存テストで、同一コマンドの実行回数、結果優先順位、finding_id、ran_at が不変であることを確認する

### R3-002 — `plugins/ndf/skills/cross-review/scripts/state.py#_resume_from_state`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | 未着手 | 0 |

**なぜ**: 再開 state の探索・互換フィールドの補完・未解決指摘の引き継ぎ・待ち行列の flush・worktree 同期・結果出力という複数段階が 1 関数に同居し、書き戻しと flush の順序制約まで同じ本体で管理している。

**手順**: 1. state の互換フィールド補完と review_instructions 再構成を、state と変更有無を返す helper へ抽出する
2. 書き戻し後の auto-flush と worktree 同期を、順序を保持した再開準備 helper へ抽出する
3. _resume_from_state は state の有無・完了判定、各 helper の呼び出し、既存の _print_init_result だけを順に行う構成へ縮める
4. 既存の再開・carried-over・worktree 同期・run metrics のテストで出力と副作用順が不変であることを確認する

### R3-003 — `plugins/ndf/skills/cross-review/scripts/state.py#_thread_ids`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| duplication | consolidate_duplication | minor | agy | 未着手 | 0 |

**なぜ**: _thread_ids における入力データ（リスト、単一辞書、数値等）の辞書要素抽出・正規化ロジックが、同モジュール内の共通関数 _normalize_dict_items と同じ関心をインラインで再実装しており重複している。_thread_positions と同様に _normalize_dict_items を呼び出す形に統一することで、入力値の正規化処理を一元化し一貫性と保守性を高められる。

**手順**: 1. _thread_ids 内の辞書要素抽出処理を _normalize_dict_items(value) の呼び出しに置き換える
2. 既存の test_state_thread_ids.py を実行し、各種入力に対する戻り値が変わらないことを確認する

### R3-004 — `plugins/ndf/skills/cross-review/scripts/state.py#_is_generated_path`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| magic_value | introduce_named_constant | minor | agy | 未着手 | 0 |

**なぜ**: パス分類判定関数群（_is_dependency_path, _is_config_ci_path, _is_infra_path 等）がモジュール定数（DEPENDENCY_FILENAMES, CONFIG_CI_FILENAMES, INFRA_FILENAMES 等）を参照しているのに対し、_is_generated_path 内にのみロックファイル名の一覧 set リテラルがハードコードされている。名前付きモジュール定数 GENERATED_LOCK_FILENAMES を定義して参照させることで、定数管理の一貫性と保守性を向上できる。

**手順**: 1. モジュール定数 GENERATED_LOCK_FILENAMES を定義する
2. _is_generated_path 内の set リテラルを GENERATED_LOCK_FILENAMES の参照に置き換える
3. 既存テストでパス分類の判定動作が不変であることを確認する

### R3-005 — `plugins/ndf/skills/cross-review/scripts/state.py#_absorb`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| duplication | consolidate_duplication | minor | kiro | 未着手 | 0 |

**なぜ**: 「2 つの指摘のうち検証結果 (reproduced > not_reproduced > not_run) が高い方を採る」という同じ業務ルールが _absorb (3645-3646 行) と _verify_findings の代表選び直しループ (3137-3138 行) の 2 箇所に _VERIFY_RANK.get(...) > _VERIFY_RANK.get(...) の比較として書かれている。_VERIFY_RANK の順位定義を変えるときや、片方だけ result の取り出し方 (_verify_result vs best.get('result')) を直したときに、もう片方だけ取り残される。両者の docstring がどちらも同じ順位を根拠に挙げており、同じ理由で一緒に変わる重複である。

**手順**: 1. _VERIFY_RANK 定義の直後に、2 つの verification dict を受け取り順位の高い方を返すヘルパー _higher_ranked_verification(current, candidate) を追加する（rank は _verify_result で正規化して比較する）
2. _verify_findings の代表選び直しループ (3135-3140) を、best と member['verification'] をヘルパーへ渡して best を更新する形へ置き換える
3. _absorb の verification 継承部 (3644-3648) を、同じヘルパーで rep['verification'] を更新する形へ置き換える
4. test_verify_findings.py / test_merge_duplicates.py / test_state_merge_fix.py を実行し、reproduced/not_reproduced/not_run の組で代表が採る値が変わらないことを確認する

## ラウンド 4（実装 claude / レビュー codex / kiro）

### R4-001 — `plugins/ndf/skills/cross-review/scripts/state.py#COUNTED_CLASSIFICATIONS`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| scattered_config | centralize_configuration | major | codex | 採用 | 1 |

**なぜ**: 収束判定が数える区分の組が state.py と measure.py に重複し、両者の一致をテストで監視している。区分追加時に片方だけ変わると、実行時の収束判定と事後測定が異なる集合を数える。

**手順**: 1. scripts 配下の小さな共有モジュールへ COUNTED_CLASSIFICATIONS を移す
2. state.py と measure.py は共有定義を import して各判定に使う
3. 値そのものと両経路の既存出力を既存テストで固定する

### R4-002 — `plugins/ndf/skills/cross-review/scripts/state.py#_finding_keys`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | split_into_pipeline | major | codex | 採用 | 1 |

**なぜ**: レビュワーごとのファイル解決、JSON 読み込み、payload と comments の境界検証、path・line・本文の正規化が1つの二重ループに入り、入力境界の失敗とキー変換の責務が分離されていない。

**手順**: 1. payload ファイルの読み込みと dict 検証を第1段へ抽出する
2. comments 要素の検証と3要素キーへの変換を第2段へ抽出する
3. _finding_keys はレビュワー列挙から各段をつなぐ処理だけにする
4. 不正 payload・不正 comment・欠損位置・正常な振動照合の既存テストを各段階で実行する

### R4-003 — `plugins/ndf/skills/cross-review/scripts/state.py#_init_new_state`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | 取り消し | 1 |

**なぜ**: 新規初期化の1関数内に PR 所有権解決、レビュー条件作成、worktree と既存コメントの準備、担当認証、初期 state 構築、保存と表示がネスト関数として同居し、各段階を単独で参照・テストできない。

**手順**: 1. ネストされた各段階を同じ入出力のモジュールレベル関数へ順に移す
2. _init_new_state はコンテキストを段階間で受け渡すオーケストレーションだけにする
3. init の再開・新規作成・既存 worktree・コメント取得失敗の既存テストを各抽出後に実行する

### R4-004 — `plugins/ndf/skills/cross-review/tests/conftest.py#_no_github`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| test_bypasses_module_boundary | move_responsibility | minor | codex | 採用 | 1 |

**なぜ**: autouse fixture が state_mod を引数に取るため、monitor.py や measure.py だけを検査するテストまで state.py を共通入口から読み込み、GitHub 照会の内部関数を一律に差し替えている。

**手順**: 1. subprocess の gh 実行ガードと state.py の既定差し替えを別 fixture に分ける
2. state.py の差し替えは state_mod を利用するテスト経路だけが要求する形へ移す
3. monitor・measure のテストが state.py を読み込まず、state 系テストでは従来どおり実 GitHub 呼び出しを防ぐことを確認する

### R4-005 — `plugins/ndf/skills/cross-review/scripts/measure.py#_proposed`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | minor | codex | 採用 | 1 |

**なぜ**: 証拠ラウンドの検査、採用 finding 集合の作成、oracle のラウンド別分母への絞り込み、出力メタデータ付与を1関数が連続して担い、分母規則だけを独立に検証しにくい。

**手順**: 1. finding_id から round を引き分母を絞る処理を _scoped_oracle_ids として抽出する
2. oracle が未計算の場合と evidence_rounds が一部だけの場合の戻り値を明示する
3. _proposed は採用集合の作成と出力組み立てだけに残し、既存の measure テストを実行する

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
| 3 | `plugins/ndf/skills/cross-review/scripts/state.py#_apply_classification` | long_method | 1 ラウンドの採用上限 5 件を超えた |
| 4 | `plugins/ndf/skills/cross-review/scripts/state.py#_finding_keys` | duplication | 1 ラウンドの採用上限 5 件を超えた |
| 4 | `plugins/ndf/skills/cross-review/scripts/state.py#_print_init_result` | long_parameter_list | 1 ラウンドの採用上限 5 件を超えた |
| 4 | `plugins/ndf/skills/cross-review/scripts/state.py#_init_new_state` | long_method | テストの期待する振る舞いが変わっています（plugins/ndf/skills/cross-review/tests/test_init_body_not_duplicated.py）。構造改善では期待出力を変えません。振る舞いの変更は別の変更に分けてください |
