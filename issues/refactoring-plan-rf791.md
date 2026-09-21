# 改修計画 — devbasex/ai-plugins #791

`/ndf:cross-refactoring` が提案し、適用した改善項目の記録である。
理由と手順は提案の時点でしか残らないため、公開の直前に書き出している。

- 対象範囲: plugins/ndf/scripts/lib, plugins/ndf/skills/cross-review/scripts, plugins/ndf/scripts/tests, plugins/ndf/skills/cross-review/tests
- 着手前のテスト: uv run --with pytest pytest scripts/tests plugins/ndf -q

## ラウンド 1（実装 codex / レビュー agy / kiro）

### R1-001 — `plugins/ndf/scripts/lib/auth.py#check_auth`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| branch | unit | — | kiro | 採用 | 1 |

**なぜ**: 終了コード 0 でも UNAUTHENTICATED_MARKERS を含む出力を未認証と判定する分岐が固定されていない。これはこのモジュールの存在理由そのものだが未固定である

**手順**: 1. subprocess.run を差し替え、returncode 0 かつ stdout に 'not logged in' を含む結果を返す
2. check_auth(['codex'], ...) を呼ぶ
3. results['codex']['ok'] が False であることを確認する
4. failed があるため die が一度呼ばれることを確認する

### R1-002 — `plugins/ndf/scripts/lib/auth.py#check_auth`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| error | unit | — | kiro | 未着手 | 0 |

**なぜ**: 確認コマンドが見つからないとき（FileNotFoundError）に ok=False とし detail を 'コマンドが見つかりません' にする分岐が固定されていない

**手順**: 1. subprocess.run を差し替え、FileNotFoundError を送出させる
2. check_auth(['codex'], ...) を呼ぶ
3. results['codex']['ok'] が False であることを確認する
4. results['codex']['detail'] に見つからない旨が入り、die が一度呼ばれることを確認する

### R1-003 — `plugins/ndf/scripts/lib/monitor_outcome.py#append_journal`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| normal | unit | — | agy | 採用 | 1 |

**なぜ**: monitor_outcome.py には read_journal の単体テストはあるが、ペアとなる append_journal の単体テストが存在しない。親ディレクトリの自動生成や非ASCII文字（UTF-8）の保持を含め、複数回の追記によって順序通りに記録・復元できる正常系が単体レベルで未固定である。

**手順**: 1. 一時ディレクトリと複数の outcome 辞書を準備する
2. append_journal を複数回呼び出して追記する
3. read_journal で読み出し、追記された順序と内容が期待通り一致することを検証する

### R1-004 — `plugins/ndf/scripts/lib/monitor_outcome.py#default_result_path`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| normal | unit | — | agy | 未着手 | 0 |

**なぜ**: read_launch_outcome の既定フォールバック先として使われる公開関数 default_result_path について、パス命名規則（<tmp_dir>/<stem>-result.json）を直接検証する単体テストが存在しない。

**手順**: 1. tmp_dir と stem を準備する
2. default_result_path(tmp_dir, stem) を呼び出す
3. 返された Path が期待される <tmp_dir>/<stem>-result.json と等しいことを検証する

### R1-005 — `plugins/ndf/scripts/lib/monitor_outcome.py#relaunch_same_agent`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| boundary | unit | — | agy | 未着手 | 0 |

**なぜ**: relaunch_same_agent は再試行可否の判定に使われる。既存テストは定義済み語彙（REASONSの9語）のみをテストしており、reason=None や空文字列 ""、未知の理由文字列が渡された場合に True を返す境界値の挙動が固定されていない。

**手順**: 1. None、空文字列 ""、未知の理由文字列を引数として準備する
2. relaunch_same_agent を呼び出す
3. いずれも戻り値が True であることを検証する

## ラウンド 2（実装 agy / レビュー codex / kiro）

### R2-001 — `plugins/ndf/scripts/lib/refresh.py#compare`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| branch | unit | — | kiro | 採用 | 1 |

**なぜ**: compare は取得失敗・前回記録なし・一致・不一致の 4 分岐を返す純関数だが、対象範囲のどこでも固定されていない。instructions-check の既存テストは refresh.fetch をスタブへ差し替えるため、compare 本体は一度も通らない

**手順**: 1. ok=False の FetchResult を作り compare(result, 'sha256:aa') を呼ぶ
2. ok=True で fingerprint を持つ FetchResult を作り、previous を None・空文字・同じ指紋・異なる指紋の 4 通りで呼ぶ
3. 実行して得た戻り値（取得失敗・前回記録なし・一致・不一致を表す文字列）を期待値として固定する

### R2-002 — `plugins/ndf/scripts/lib/refresh.py#fetch`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| error | unit | — | kiro | 取り消し | 1 |

**なぜ**: fetch は取得の失敗を例外にせず FetchResult(ok=False, error=...) へ畳む分岐を持ち、error の文言は _reason が例外の種類ごとに書き分ける。この経路は未固定で、instructions-check のテストは fetch 自体を差し替えるため通らない。opener は差し替え用に設計された引数である

**手順**: 1. opener に OSError を送出する呼び出し可能を渡し fetch(url, timeout, opener) を呼ぶ
2. urllib.error.HTTPError と urllib.error.URLError を送出する opener でも同様に呼ぶ
3. 3 通りとも ok が False で、実行して得た error の文言（種類ごとに異なる）を期待値として固定する

### R2-003 — `plugins/ndf/scripts/lib/refresh.py#refresh`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| normal | integration | — | kiro | 採用 | 1 |

**なぜ**: refresh は fetch・row・compare をつないで全件の 1 行と失敗件数を返す公開入口だが、この配線を通す固定が対象範囲に無い。取れなかった URL を黙って落とさず件数へ数える振る舞いが未固定である

**手順**: 1. 成功と失敗を混ぜて返す opener を差し替えで用意する
2. name/checked_at/claim を持つ複数の source を渡し refresh(sources, timeout, opener) を呼ぶ
3. 返る行数が source 数と一致し、失敗件数が失敗した source 数と一致することを固定する
4. 各行に source の name と取得の成否が含まれることを、実行して得た値で固定する

## ラウンド 3（実装 kiro / レビュー codex / agy）

### R3-001 — `plugins/ndf/scripts/lib/monitor_outcome.py#OUTCOME_KEYS`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| scattered_config | centralize_configuration | major | kiro | 採用 | 1 |

**なぜ**: 監視の結果ファイルのキーの一覧が 3 か所に散っている。monitor_outcome.OUTCOME_KEYS（14 個・runtime では未使用）と、実際に辞書を組み立てる monitor.py の _record_outcome（`phase` を含む 15 個）と、test_monitor_outcome_file.py の独自コピー（15 個）である。正本のはずの OUTCOME_KEYS が `phase` を欠いており既に食い違っている。キーを足すたびにどこかが古くなる。

**手順**: 1. OUTCOME_KEYS へ `phase` を加え、契約文書の並びと揃える
2. monitor.py の _record_outcome が組み立てた辞書のキー集合を OUTCOME_KEYS と突き合わせる assert を置き、食い違いをその場で落とす（値の生成は現状のまま）
3. read_journal / read_outcome の既存テストと test_monitor_outcome_file.py を実行し、キー集合の期待が変わっていないことを確かめる

### R3-002 — `plugins/ndf/skills/cross-review/scripts/state.py#_handle_no_result_round`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | 採用 | 1 |

**なぜ**: 1 関数に理由の集約と出力、再起動不能時の終了処理、再起動済み時の終了処理、再起動対象の記録とシェル向け出力という別々の段階が同居し、状態保存と終了条件が複数箇所に散っている。

**手順**: 1. 担当別理由の収集と NO_RESULT_REASONS 出力を小さな関数へ抽出する
2. final・ended_at・保存・die を行う共通の異常終了処理を抽出する
3. 再起動対象の記録と互換出力を行う処理を抽出する
4. usage_limit、再起動済み、再起動要求の既存テストで終了コード・state・出力順を固定する

### R3-003 — `plugins/ndf/skills/cross-review/scripts/state.py#_print_init_result`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_parameter_list | introduce_parameter_object | major | codex | 採用 | 1 |

**なぜ**: 初期化結果という同じ概念を表す 11 引数を位置で受け取り、特に連続する 3 個の bool と末尾の件数・再開フラグは呼び出し側で順序を取り違えても検出しにくい。新規初期化と再開の 2 経路が同じ組を渡している。

**手順**: 1. 出力対象を表す _InitResult の値オブジェクトを定義する
2. _print_init_result の引数を _InitResult 1 個へ置き換える
3. 新規初期化経路と再開経路で名前付きフィールドから _InitResult を構築する
4. 両経路の既存テストで標準出力が不変であることを確認する

### R3-004 — `plugins/ndf/scripts/lib/monitor.py#monitor_agent`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| duplication | consolidate_duplication | minor | codex | 採用 | 1 |

**なぜ**: 監視ループの 5 つの終了分岐が、同じ _finish_monitor(status, outcome, (config.log_prefix, agent)) 呼び出しを繰り返している。終了時に渡すログ文脈を変更すると各分岐を同時に直す必要がある。

**手順**: 1. status とログ文脈を閉じ込めて outcome を受け取る局所的な finish 処理を定義する
2. PID 不正・完了・timeout・early error・process exit・stall の各終了分岐を同じ入口へ寄せる
3. monitor_agent を通る既存テストで status、ログ、終了結果が不変であることを確認する

### R3-005 — `plugins/ndf/skills/cross-review/scripts/state.py#_verify_findings`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | minor | kiro | 採用 | 1 |

**なぜ**: 1 つの関数が 2 つの独立した段を通しで行う。前段は各指摘の suggested_check を（重複を除いて）実行し verification を記録する反復、後段は束ねた組の代表へ最良の結果を選び直す反復である。段ごとに名前が付き、共有するのは targets と by_id だけである。

**手順**: 1. 前段を _run_finding_checks(targets, allowed, work, codes, run) として抽出し、実行済みコマンドの対応表を関数内へ閉じる
2. 後段を _propagate_best_verification(targets, by_id) として抽出する
3. _verify_findings は codes / run / targets / by_id を用意し、2 つを順に呼ぶだけにする
4. test_verify_findings.py を実行して verification の記録が変わらないことを確かめる

## ラウンド 4（実装 claude / レビュー codex / kiro）

### R4-001 — `plugins/ndf/skills/cross-review/scripts/state.py#_resume_from_state`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | 採用 | 1 |

**なぜ**: 既存stateの探索、旧形式の補完、追加レビュー観点の再計算、引き継ぎ記録、保存、待ち行列flush、worktree同期、機械可読出力までが1関数に直列で置かれ、副作用の順序を長い本体とコメントから追う必要がある。各段階には独立した終了条件と入出力があり、名前を付けて分離できる。

**手順**: 1. cmd_initを通る既存の再開テストで、stateなし、final済み、旧形式補完、追加観点更新、引き継ぎ、flush後の同期と出力を現状固定する
2. stateファイルの探索と再開可否判定を、stateとpathを返す関数へ抽出する
3. 旧形式の補完、manual指示の反映、review_instructions再計算、carried_over記録を、変更有無も返す関数へ抽出する
4. 保存後のauto_flush、tmp_dir解決、登録済みworktree同期を副作用順序が見える関数へ抽出する
5. _resume_from_stateを各段階の呼び出しと_print_init_resultだけにし、再開関連テストと全体テストで出力と保存順序が不変であることを確認する

### R4-002 — `plugins/ndf/skills/cross-review/scripts/state.py#_init_new_state`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | 取り消し | 1 |

**なぜ**: 新規初期化の1関数に、PR所有権の解決、レビュー観点の構築、worktreeと既存コメントの準備、認証確認、state構築、永続化と出力が同居し、さらに5個のローカル関数が本体を約200行へ広げている。各段階は既に名前と入出力を持つため、モジュールレベルへ抽出すれば段階単位で読めて個別にテストできる。

**手順**: 1. 既存の入口テストで、新規worktree、既存worktree、変更ファイル取得fallback、認証失敗、初期state出力の経路を現状固定する
2. _resolve_pr_and_ownership と _prepare_review_instructions をモジュールレベル関数へ抽出し、既存のcontext型を入出力に使う
3. _prepare_worktree_and_comments をモジュールレベル関数へ抽出し、worktree作成より後に_tmp_dirを呼ぶ順序とコメント取得失敗時の停止を保つ
4. _prepare_initial_assignment、_build_initial_review_state、_finalize_initial_state をモジュールレベルへ抽出し、_init_new_stateを段階を順に呼ぶオーケストレーションだけにする
5. 初期化関連テストと全体テストを実行し、標準出力、stateの内容、副作用の順序が不変であることを確認する

### R4-003 — `plugins/ndf/skills/cross-review/scripts/state.py#cmd_check_oscillation`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| conditional_chain | replace_with_lookup_table | minor | kiro | 取り消し | 1 |

**なぜ**: 現ラウンドの各指摘について _finding_match_kind の戻り値 ("exact"/"near"/"body") を if/elif で数えている。種別ごとの集計は種別を増やすたびに分岐を足すことになる。あわせて、collect_keys クロージャは _finding_keys(st, pr, round_no) をそのまま呼ぶだけの指標なしの間接参照で、読み手が本体を追う負荷を増やしている。test_state_check_oscillation.py と test_state_oscillation_matching.py が cmd_check_oscillation / _finding_match_kind を通す。

**手順**: 1. exact/near/same_body の 3 変数と if/elif/elif の加算を、collections.Counter に対する `Counter(_finding_match_kind(k, prev) for k in curr)` へ置き換える
2. overlap_count は None 以外の合計として counts の値の総和から出す
3. info の表示は counts.get("exact", 0) 等から読む
4. collect_keys クロージャを消し、呼び出し 2 箇所を _finding_keys(st, pr, prev_round_no) / (curr_round_no) の直接呼び出しに戻す
5. テストを実行して振る舞い不変を確認する

### R4-004 — `plugins/ndf/skills/cross-review/scripts/state.py#_normalize_fix_result`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | minor | kiro | 採用 | 1 |

**なぜ**: 1 関数に (a) 別名 fallback（fix_commit/commit_sha、fixed_count/fixed）、(b) deferred の list/dict/int による件数の場合分けと dict 要素への正規化、(c) rejected の同じ正規化、(d) 記録用辞書の組み立て、が同居する。deferred と rejected はどちらも _normalize_dict_items + 件数決定という同型の処理で、片方だけ直すと食い違いうる。test_state_merge_fix.py と test_state_ci_classification.py が cmd_merge_fix 経由で通す。

**手順**: 1. 「_normalize_dict_items した項目」と「保存する件数」を組で返す小関数 _normalize_deferred_like(raw) を抽出する（list/dict は展開件数、劣化表現の int/str は _count の値、という現在の規則をそのまま移す）
2. deferred と rejected の両方をこの関数で得る（現状 rejected の件数は常に _count なので、raw の型で分岐する現在の deferred 規則へ揃える形にはせず、抽出関数は deferred の規則を表し、rejected は従来どおり _count を使うなら別に保つ。振る舞いを変えないため、まず deferred 経路だけを抽出する）
3. 別名 fallback（fix_commit/fixed_count）を _resolve_fix_aliases として抽出する
4. 末尾の辞書組み立てを、抽出した値を差し込む形へ整える
5. テストを実行して出力の辞書が不変であることを確認する

## ラウンド 5（実装 codex / レビュー agy / kiro）

### R5-001 — `plugins/ndf/scripts/lib/post_queue.py#Queue.flush`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | 取り消し | 1 |

**なぜ**: 1 件の処理の中に、壊れた JSON の停止判定、既投稿の照合と削除、送信成功時の応答保存と削除、送信失敗時の再試行情報保存と rate limit 判定が直列に並び、flush 自体が順序制御と各項目の状態遷移の両方を担っている。

**手順**: 1. 読み取り済み項目について既投稿・送信成功・送信失敗を処理する部分を Queue の補助メソッドへ抽出する
2. 補助メソッドの戻り値で継続または停止と rate_limited を表し、flush は連番走査と集計だけを担うようにする
3. 壊れた項目で停止する既存経路は flush 側に残し、項目順序と停止位置を変えない
4. test_post_queue.py と cross-review/tests/test_queue_idempotency.py で skipped・sent・failed・remaining とファイル削除順を確認する

### R5-002 — `plugins/ndf/skills/cross-review/scripts/state.py#_sync_worktree`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | 取り消し | 1 |

**なぜ**: PR head の取得方法の決定、strict 時の同期済み判定と失敗処理、worktree の reset・未追跡ファイル掃除、同期結果の表示という独立した段階が 1 関数に同居している。HeadRef と旧来の文字列 head の分岐も取得段階に閉じず、後続の制御へ have_base・target・label の組で持ち越されている。

**手順**: 1. HeadRef と文字列 head から have_base・target・label を解決する取得段階を補助関数へ抽出する
2. strict 時の同期済み早期終了と基準取得失敗の判定を補助関数へ抽出する
3. _sync_worktree は取得、判定、reset、clean、結果表示の順序だけを示す構成にする
4. test_state_sync_worktree.py と test_state_offline_fetch.py で既存の strict／fallback／失敗時終了コードを確認する

### R5-003 — `plugins/ndf/skills/cross-review/scripts/state.py#_load_payload`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| duplication | consolidate_duplication | minor | kiro | 取り消し | 0 |

**なぜ**: payload が dict でない場合と comments が list でない場合の 2 経路が、`info(f"⚠ {agent}: ...形式不正で、判定は中断します")` を出して `return None` する同じ形で並ぶ。返す条件（dict でない / list でない）と型名の埋め込みが繰り返され、警告文の末尾の定型句も重複する。片方の文言だけ直すと 2 経路のメッセージが食い違う。test_review_findings.py が不正 payload での 0 件記録を固定している。

**手順**: 1. `_reject_payload(agent: str, path: pathlib.Path, detail: str) -> None` を追加し、`info(f"⚠ {agent}: {detail}（{path}...）。指摘の記録は 0 件です。review launcher の出力形式不正で、判定は中断します")` を出して None を返す
2. dict でない場合と comments が list でない場合の 2 経路を、type 名を含む detail 文字列を渡す呼び出しへ置き換える
3. 部分不正（items != raw）は継続する経路のため対象にせず、そのまま残す
4. `uv run --with pytest pytest plugins/ndf/skills/cross-review/tests/test_review_findings.py -q` で 0 件記録と警告が不変なことを確認する

### R5-004 — `plugins/ndf/scripts/lib/metrics.py#format_report`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| duplication | consolidate_duplication | minor | kiro | 取り消し | 1 |

**なぜ**: unmeasured と assumed の 2 節が同じ形（見出し + 空行を lines へ足し、dict.fromkeys で重複を除いた項目を `- {w}` で並べる）で並んでいる。片方だけ書式を変えると 2 節の見た目が食い違う。同じ業務ルール（分離・代用の一覧の出し方）に由来し、変わるときは一緒に変わる。既存テスト test_models_and_metrics.py が format_report の出力を固定している。

**手順**: 1. `_emit_bullet_section(lines: list[str], title: str, items: list[str]) -> None` を追加し、items が空でなければ `['', f'## {title}', '']` と `[f'- {w}' for w in dict.fromkeys(items)]` を lines へ足す
2. unmeasured の if ブロックを `_emit_bullet_section(lines, "集計から分離したラウンド", metrics["unmeasured"])` へ置き換える
3. assumed の if ブロックを `_emit_bullet_section(lines, "指定値で代用したラウンド", metrics.get("assumed") or [])` へ置き換える
4. 比較の限界（COMPARISON_CAVEATS）節は常に出るため対象外のまま残す
5. `uv run --with pytest pytest scripts/tests plugins/ndf -q` で出力が不変なことを確認する

### R5-005 — `plugins/ndf/skills/cross-review/scripts/state.py#build_parser`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | minor | codex | 採用 | 1 |

**なぜ**: init の多数のオプション定義と、ラウンド進行・結果取込・検証・報告に属する 11 個の副コマンド登録が 1 関数に連続しており、個別コマンドの引数変更でも 125 行の構築処理全体を読む必要がある。副コマンドごとに独立した名前を付けられる段階になっている。

**手順**: 1. init のパーサ設定を専用の補助関数へ抽出する
2. 各副コマンドの parser 作成・引数追加・set_defaults を用途別の小さな登録関数へ抽出する
3. build_parser はトップレベル parser と subparsers を作り、登録関数を順に呼んで返すだけにする
4. test_state_subcommand_help.py、test_state_review_pool.py、test_findings_pipeline_wiring.py で選択肢・help・func の対応が不変であることを確認する

## ラウンド 6（実装 agy / レビュー codex / kiro）

### R6-001 — `plugins/ndf/scripts/lib/auth.py#check_auth`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | 採用 | 1 |

**なぜ**: スキップ判定、CLI ごとの subprocess 実行、例外の認証結果への変換、結果の集約、全体の失敗通知が 1 関数に同居しており、個別 CLI のプローブ規則と複数 CLI の制御を別々に読めない。既存の test_auth_probe.py が未知 runtime、成功、未認証マーカー、コマンド不在を公開入口から固定している。

**手順**: 1. 1 runtime の probe 実行と FileNotFoundError・TimeoutExpired の認証結果への変換を、runtime と probe を受け取る補助関数へ抽出する
2. ok・detail・command の結果を check_auth が受け取り、既存どおり info 出力、failed 集約、die 判定を行う形へ置き換える
3. test_auth_probe.py を実行し、戻り値、通知文、die 呼び出しが不変であることを確認する

### R6-002 — `plugins/ndf/skills/cross-review/scripts/state.py#_confirm_flushed`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | 採用 | 1 |

**なぜ**: 待ち行列項目の適用可否判定、response からの URL 復元、対象ラウンド探索、GitHub 到達確認、結果なしまたは成功状態への更新、永続化が 1 関数に直列で同居している。投稿確認は収束可否に関わるため、対象特定と状態遷移を独立した名前で読める構造にする価値が高く、test_state_queue_judge.py が送信済み・冪等スキップ・未到達の経路を固定している。

**手順**: 1. item の kind・extra・response を解釈して agent、round、review URL を返す処理を補助関数へ抽出する
2. state の rounds から書き戻し対象を探す処理を補助関数へ抽出する
3. _confirm_flushed は早期 return、到達確認、既存と同じ target 更新、_save の順序だけを担うよう置き換える
4. test_state_queue_judge.py を実行し、queued、review_url、not_posted、保存回数を含む観測結果が不変であることを確認する

### R6-003 — `plugins/ndf/skills/cross-review/scripts/state.py#_guard_previous_round`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | 未着手 | 0 |

**なぜ**: 旧形式 state の verdict 復元、修正記録の必須判定、申告済み Resolve の GitHub 照会、未解決 ID の検出という独立した 2 つのガードが 1 関数に同居している。各ガードは異なる理由で変更され、test_state_round_guard.py が修正記録なし、照会不能、未解決残存、正常通過を固定している。

**手順**: 1. 保存済み verdict が無い場合の no_result・pass からの復元を補助関数へ抽出する
2. fix の resolved_thread_ids と対象 PR を受け、照会不能時の通知または未解決 ID を判定する補助関数へ抽出する
3. _guard_previous_round は修正記録ガードと Resolve 状態ガードを順に呼ぶ構成へ置き換える
4. test_state_round_guard.py を実行し、終了コード、GitHub 照会先、警告、正常通過が不変であることを確認する

### R6-004 — `plugins/ndf/skills/cross-review/scripts/state.py#_is_generated_path`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| scattered_config | centralize_configuration | minor | kiro | 未着手 | 0 |

**なぜ**: 兄弟の判定述語（_is_dependency_path は DEPENDENCY_FILENAMES、_is_infra_path は INFRA_FILENAMES、_is_generated_path 自身も GENERATED_MARKERS）はいずれもモジュール定数を引くのに、_is_generated_path だけ lockfile 名の集合（package-lock.json / go.sum / cargo.lock など 9 件）を関数本体へじか書きしている。うち package-lock.json 等は DEPENDENCY_FILENAMES にも重複して載っており、lockfile を足すとき 2 か所を直す必要があるうえ、どこに定義があるか読み手が探す。定義を 1 か所へ寄せて兄弟と同じ形にする。

**手順**: 1. モジュール定数群（DEPENDENCY_FILENAMES / GENERATED_MARKERS / INFRA_FILENAMES の並び）へ GENERATED_LOCKFILES を新設し、現在インラインにある 9 件の集合をそのまま移す
2. _is_generated_path の本体を `name in GENERATED_LOCKFILES` へ置き換え、インラインの集合リテラルを消す
3. test_gap を埋めるため、先に go.sum / cargo.lock（DEPENDENCY_FILENAMES に無く、インライン集合にだけある名前）で generated カテゴリが立つ現状固定テストを test_state_auto_review_templates.py に追加し、移動の前後で同じ結果になることを確認する

## 見送った項目

| ラウンド | 対象 | 兆候・経路 | 理由 |
| --- | --- | --- | --- |
| 1 | `plugins/ndf/scripts/lib/statefile.py#save` | error | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/scripts/lib/statefile.py#save` | normal | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_read_result` | boundary | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_read_result` | error | 1 ラウンドの採用上限 5 件を超えた |
| 2 | `plugins/ndf/scripts/lib/refresh.py#fetch` | error | テストの期待する振る舞いが変わっています（plugins/ndf/scripts/tests/test_refresh.py）。構造改善では期待出力を変えません。振る舞いの変更は別の変更に分けてください |
| 3 | `plugins/ndf/scripts/lib/monitor.py#_record_outcome` | long_method | 1 ラウンドの採用上限 5 件を超えた |
| 4 | `plugins/ndf/skills/cross-review/scripts/state.py#_init_new_state` | long_method | テストの期待する振る舞いが変わっています（plugins/ndf/skills/cross-review/tests/test_init_body_not_duplicated.py）。構造改善では期待出力を変えません。振る舞いの変更は別の変更に分けてください |
| 4 | `plugins/ndf/skills/cross-review/scripts/state.py#cmd_check_oscillation` | conditional_chain | コミット 95387271f1b307d6c0a88f1cde8f6401fe3a6111 にトレーラーが欠けています: Item-Id, Round, Impl-Runtime, Impl-Model |
| 5 | `plugins/ndf/scripts/lib/post_queue.py#Queue.flush` | long_method | どの改善項目にも割り当てられていないコミットが 1 件（311bf7f）。検証を回避した変更や、状態と実差分の食い違いを Pull Request に残さないため、この適用ラウンドを取り消します |
| 5 | `plugins/ndf/skills/cross-review/scripts/state.py#_sync_worktree` | long_method | どの改善項目にも割り当てられていないコミットが 1 件（311bf7f）。検証を回避した変更や、状態と実差分の食い違いを Pull Request に残さないため、この適用ラウンドを取り消します |
| 5 | `plugins/ndf/scripts/lib/metrics.py#format_report` | duplication | どの改善項目にも割り当てられていないコミットが 1 件（311bf7f）。検証を回避した変更や、状態と実差分の食い違いを Pull Request に残さないため、この適用ラウンドを取り消します |
| 5 | `plugins/ndf/skills/cross-review/scripts/state.py#_load_payload` | duplication | どの改善項目にも割り当てられていないコミットが 1 件（1e8216f）。検証を回避した変更や、状態と実差分の食い違いを Pull Request に残さないため、この適用ラウンドを取り消します |
