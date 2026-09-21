# 改修計画 — devbasex/ai-plugins #793

`/ndf:cross-refactoring` が提案し、適用した改善項目の記録である。
理由と手順は提案の時点でしか残らないため、公開の直前に書き出している。

- 対象範囲: plugins/ndf/scripts/lib, plugins/ndf/skills/cross-review/scripts, plugins/ndf/scripts/tests, plugins/ndf/skills/cross-review/tests
- 着手前のテスト: uv run --with pytest pytest scripts/tests plugins/ndf -q

## ラウンド 1（実装 codex / レビュー agy / kiro）

### R1-001 — `plugins/ndf/scripts/lib/assignment.py#detect_host`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| branch | unit | — | agy | 採用 | 1 |

**なぜ**: detect_host は収束ループ共通層においてホストを確定する重要関数であり、誤判定すると母集合が狂う致命的な影響を持つ。しかし共通層テスト（plugins/ndf/scripts/tests/）には単体テストが全く存在しない。明示指定（explicit）、環境変数ヒント（HOST_ENV_HINTS）の順序による推定、および手掛かりがない場合の例外送出の各分岐を共通層単体テストとして固定する必要がある。

**手順**: 1. plugins/ndf/scripts/tests/test_lib_assignment.py に test_detect_host_* を追加する。
2. 明示指定分岐: HOST_RUNTIMES に含まれる名前を指定したときに (host, 'explicit') が返り、無効な名前を指定したときに AssignmentError が送出されることを検証する。
3. 環境変数推定分岐: CLAUDE_PLUGIN_ROOT, CODEX_HOME, KIRO_AGENT 等の環境変数ヒントを含む辞書を渡し、正しいホスト名と 'env' が返ることを検証する。
4. 推定不能分岐: 環境変数が空辞書（またはヒントなし）の場合に、既定値を勝手に置かず AssignmentError が送出されることを検証する。

### R1-002 — `plugins/ndf/scripts/lib/assignment.py#review_seats`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| branch | unit | — | agy | 採用 | 1 |

**なぜ**: assignment.py で新設された review_seats は、cross-review において各ラウンドのレビュワー2席を割り当てるコア関数である。しかし共通層テスト（plugins/ndf/scripts/tests/）には単体テストが一切存在しない（別スキル cross-refactoring のテスト側に暫定配置されているのみ）。len(available) の人数（3者以上の輪番、2者の固定、1者時の fallback または副席 <name>-2 補填、0者時の fallback 2席割当および fallback 空時の例外送出）の全分岐の振る舞いを共通層の単体テストとして固定する必要がある。

**手順**: 1. plugins/ndf/scripts/tests/test_lib_assignment.py に test_review_seats_* を追加する。
2. 3者以上: available=['codex', 'agy', 'kiro'] でラウンド1〜3を実行し、available の順序を保った2席が輪番で選ばれることを検証する。
3. 2者: available=['codex', 'kiro'] で複数ラウンドを実行し、ラウンド番号によらず常にその2者が返ることを検証する。
4. 1者: available=['codex'], fallback=['claude'] で ['codex', 'claude'] が返り、fallback が空または available と重複する場合は ['codex', 'codex-2'] が返ることを検証する。
5. 0者: available=[], fallback=['claude'] で ['claude', 'claude-2'] が返り、fallback も空の場合は AssignmentError となることを検証する。
6. round_no < 1 の場合に AssignmentError が送出されることを検証する。

### R1-003 — `plugins/ndf/scripts/lib/assignment.py#review_seats`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| error | unit | — | codex | 採用 | 1 |

**なぜ**: 0 人でも fallback がある経路は状態初期化から固定されているが、available と fallback がともに空の公開入口が AssignmentError になる経路は未固定である。

**手順**: 1. round_no=1、available=[]、fallback=[] で公開入口を呼ぶ
2. AssignmentError が送出されることを観測する
3. 例外の利用者向け理由から、使える者と埋め合わせ候補がともに無いことを示す要点だけを確認する

### R1-004 — `plugins/ndf/scripts/lib/assignment.py#seat_runtime`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| boundary | unit | — | agy | 取り消し | 1 |

**なぜ**: seat_runtime は正規表現 SEAT_PATTERN（^(claude|codex|agy|kiro)(-[2-9])?$）に従って席名を検証・抽出するが、共通層テストに境界値・異常値のテストが存在しない。接尾辞の数値境界（-1 は不可、-2〜-9 は可、-10 は不可）、区切り文字違い（_2）、未知のランタイム、空文字列等で AssignmentError が送出される境界値の振る舞いを単体レベルで固定する必要がある。

**手順**: 1. plugins/ndf/scripts/tests/test_lib_assignment.py に test_seat_runtime_rejects_malformed_seat を追加する。
2. 接尾辞の数値境界: 'kiro-1'（下限未満）、'kiro-10'（上限超過）で AssignmentError が発生することを検証する。
3. 区切り形式・重複の境界: 'claude_2'（アンダースコア）、'kiro-2-3'（ハイフン重複）、空文字列 '' で AssignmentError が発生することを検証する。
4. 未知のランタイム: 'gemini', 'gpt' 等の ALL_RUNTIMES 外の名称で AssignmentError が発生することを検証する。

### R1-005 — `plugins/ndf/scripts/lib/assignment.py#seat_runtime`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| normal | unit | — | agy | 採用 | 1 |

**なぜ**: assignment.py で新設された seat_runtime(seat: str) は、席名から基底ランタイム名を取り出す共通層関数であり、結果受け口・起動スクリプト・監視処理で広く使われる。しかし共通層テスト（plugins/ndf/scripts/tests/）には単体テストが存在しない。接尾辞なしのランタイム名（claude, codex, agy, kiro）および同一ランタイムの副席名（-2〜-9 接尾辞）から正確にランタイム名が抽出される正常系の振る舞いを共通層単体テストとして固定する必要がある。

**手順**: 1. plugins/ndf/scripts/tests/test_lib_assignment.py に test_seat_runtime_extracts_runtime_name を追加する。
2. ALL_RUNTIMES の全ランタイム名（'claude', 'codex', 'agy', 'kiro'）をそのまま渡した場合に、同一のランタイム名が返ることを検証する。
3. ハイフン付き席名（'kiro-2', 'claude-9', 'agy-3' 等）を渡した場合に、接尾辞を除去した基底ランタイム名が正しく返ることを検証する。

## ラウンド 2（実装 agy / レビュー codex / kiro）

### R2-001 — `plugins/ndf/scripts/lib/assignment.py#assign`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| error | unit | — | codex / agy | 採用 | 1 |

**なぜ**: assign は 8 ラウンド周期の割り当てを行う公開関数であり正常系は固定されているが、round_no < 1（0 や負数）が渡された場合に AssignmentError を送出するエラー経路が scripts/tests 内で固定されていない。

**手順**: 1. 有効な各ホスト（claude, codex, agy, kiro）について assignment.assign(0, host) および assignment.assign(-1, host) を呼び出す
2. どちらも assignment.AssignmentError が送出されることを検証する
3. 送出された例外メッセージに「ラウンド番号は 1 以上です」が含まれることを検証する

### R2-002 — `plugins/ndf/scripts/lib/assignment.py#resolve_participants`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| boundary | unit | — | codex / agy | 採用 | 1 |

**なぜ**: resolve_participants で母集合の全メンバーを exclude に指定し、参加可能なメンバーが 0 件になる下限境界の振る舞い（空一覧で認証確認が呼ばれ、available が空リスト、excluded が固定順で記録されること）が固定されていない。

**手順**: 1. 母集合の全員（例: ['codex', 'agy', 'kiro']）を exclude に指定し、記録用プローブを渡して resolve_participants を呼び出す
2. プローブが空の一覧 [] で 1 回だけ呼ばれることを検証する
3. 戻り値の Participants において available が []、unavailable が {}、excluded が固定順（['codex', 'agy', 'kiro']）で保持されることを検証する

### R2-003 — `plugins/ndf/scripts/lib/assignment.py#review_assign`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| boundary | unit | — | agy / kiro | 採用 | 1 |

**なぜ**: review_assign の round_no < 1 の下限境界条件で AssignmentError を送出する振る舞いが scripts/tests 内で固定されていない。同モジュールの impl_assign や review_seats には round_no < 1 の境界テストがあるが、review_assign だけ抜けている。

**手順**: 1. test_lib_assignment.py で assignment.review_assign(0, "claude") および assignment.review_assign(-1, "claude") を呼び出す
2. どちらの呼び出しでも assignment.AssignmentError が送出されることを検証する
3. 例外メッセージに「ラウンド番号は 1 以上です」が含まれることを検証する

### R2-004 — `plugins/ndf/scripts/lib/assignment.py#review_assign`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| branch | unit | — | agy / kiro | 取り消し | 1 |

**なぜ**: review_assign は適用の役を持たない工程が使う公開入口だが、scripts/tests には直接の固定が無い。in-scope の test_lib_assignment.py は assign / impl_assign / review_seats を固定するだけで、この関数の輪番（母集合3者から dropped=(round_no-1)%3 を外す各分岐）は通っていない。out-of-scope の cross-review テストは _round_reviewers の照合オラクルとして呼ぶだけで、この関数自身の戻り値を固定していない。

**手順**: 1. test_lib_assignment.py の assignment フィクスチャで各ホスト（claude, codex, agy, kiro）について review_assign(round_no, host) を round 1..6 で呼び出す
2. 各ホストで返る担当ペアの一覧が 3 ラウンド周期で循環し、現状の決定結果（例: claude は [['agy', 'kiro'], ['codex', 'kiro'], ['codex', 'agy']] が 2 周する）と完全一致することを検証する
3. 返されるレビュー担当が常に 2 者であり、指定したホスト自身を含まないことを併せて検証する

### R2-005 — `plugins/ndf/scripts/lib/assignment.py#review_assign`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| error | unit | — | codex / agy | 採用 | 1 |

**なぜ**: review_assign に HOST_RUNTIMES に含まれない無効なホスト名が渡された場合、内部の review_pool から AssignmentError（「ホストになれないランタイムです」）が送出されるエラー経路が固定されていない。

**手順**: 1. test_lib_assignment.py で assignment.review_assign(1, "gemini") や assignment.review_assign(1, "unknown") を呼び出す
2. assignment.AssignmentError が送出されることを検証する
3. 例外メッセージに「ホストになれないランタイムです」が含まれることを検証する

## ラウンド 3（実装 kiro / レビュー codex / agy）

### R3-001 — `plugins/ndf/skills/cross-review/scripts/measure.py#_matches`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_parameter_list | introduce_parameter_object | major | kiro | 取り消し | 0 |

**なぜ**: 解決位置の突き合わせ鍵 (pr, round_no, path, line) の 4 引数が _matches・_find_best_match・_add_oracle_match の 3 関数を順に渡り回っている。呼び出し側で順序を取り違えても型で防げず、鍵の項目を増やすたびに 3 関数すべての引数を直すことになる。

**手順**: 1. NamedTuple `MatchKey(pr, round_no, path, line)` を定義する
2. _matches の引数を (finding, key: MatchKey) にし、本体を key.* へ書き換える
3. _find_best_match・_add_oracle_match も MatchKey を受け取る形に変え、呼び出し側（_oracle のループ）で MatchKey を 1 度組み立てて渡す
4. test_measure.py の oracle 系テストで退行を確認する

### R3-002 — `plugins/ndf/scripts/lib/assignment.py#resolve_participants`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | split_into_pipeline | major | codex | 取り消し | 0 |

**なぜ**: 入力の正規化、名前と集合制約の検証、only 適用、認証 probe、利用可否の集計、require_all 判定、結果生成が直列に並び、検証規則と外部 probe の境界を個別に読みにくい。

**手順**: 1. test_lib_participants.py の既存ケースを現状固定として実行する
2. pool/include/exclude の正規化と制約検証を独立した段へ抽出する
3. only を適用して probe 対象を返す段を抽出する
4. probe 結果を available と unavailable へ変換し require_all を判定する段を抽出する
5. resolve_participants は各段の出力を次段へ渡して Participants を返す処理だけにする
6. 対象テストと全体テストで例外文言、順序、probe 呼び出し、戻り値が不変であることを確認する

### R3-003 — `plugins/ndf/skills/cross-review/scripts/state.py#_init_new_state`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | major | codex | 取り消し | 0 |

**なぜ**: 200 行の関数内に PR 所有者判定、レビュー指示生成、worktree と既存コメントの準備、担当決定、初期 state 構築、保存と表示が同居し、補助関数もすべてローカル定義のため各段階を単独で検証できない。

**手順**: 1. 既存の init 経路テストを現状固定として実行する
2. _resolve_pr_and_ownership と _prepare_review_instructions をモジュールレベルへ抽出する
3. _prepare_worktree_and_comments と _prepare_initial_assignment をモジュールレベルへ抽出する
4. _build_initial_review_state と _finalize_initial_state をモジュールレベルへ抽出し、_init_new_state は各段階を順に呼ぶ構成へ縮める
5. init 関連テストと全体テストで公開入口の出力と副作用が不変であることを確認する

### R3-004 — `plugins/ndf/skills/cross-review/scripts/measure.py#_state_file_pr`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| duplication | consolidate_duplication | minor | kiro | 未着手 | 0 |

**なぜ**: _state_file_pr と _prs が同じ pr_history 走査（dict 判定→_as_int(entry.get("pr"))→current_pr へのフォールバック）を別々に持つ。_state_file_pr は実質「_prs の先頭」で、片方だけ直すと状態ファイルの鍵の選び方が食い違う。同じ業務ルール（状態ファイルの鍵の決め方）に由来し、必ず一緒に変わる重複である。

**手順**: 1. _prs を先に評価し、走査ロジックの唯一の持ち主にする
2. _state_file_pr を `prs = _prs(st); return prs[0] if prs else None` へ置き換える
3. test_measure.py の pr/prs を検査するテスト（test_identity_keys_report_state_file_key_and_all_prs 他）で退行を確認する

### R3-005 — `plugins/ndf/scripts/lib/refresh.py#fetch`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| long_method | extract_method | minor | kiro | 取り消し | 0 |

**なぜ**: fetch が opener 呼び出し・期限付き読み取りループ・socket への期限伝播・close の後始末を通しで行う。読み取りループ（deadline 判定・_set_socket_timeout の bounded 蓄積・chunk 蓄積）だけを名前付きの段へ分けると、読み取り部分と取得の骨格を別々に読める。

**手順**: 1. 読み取りループを `_read_until_deadline(response, deadline, timeout) -> bytes` として抽出し、bounded 判定と FetchTimeout の送出をその中へ移す
2. fetch は opener 呼び出しと finally の close を残し、本文取得を抽出関数の呼び出しに置き換える
3. test_refresh.py の refresh/fetch 経路のテストで退行を確認する

## 見送った項目

| ラウンド | 対象 | 兆候・経路 | 理由 |
| --- | --- | --- | --- |
| 1 | `plugins/ndf/scripts/lib/models.py#mismatch_warning` | branch | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/scripts/lib/models.py#observed_model` | branch | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/scripts/lib/models.py#separation_reason` | branch | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/scripts/lib/monitor.py#monitor_agent` | branch | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/scripts/lib/statefile.py#save` | error | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/skills/cross-review/scripts/critique.sh#select_targets` | branch | 1 ラウンドの採用上限 5 件を超えた |
| 1 | `plugins/ndf/scripts/lib/assignment.py#seat_runtime` | boundary | コミット 36dd097d4dff427b0de545bcd0cdc0de0e7b74fb にトレーラーが欠けています: Item-Id, Round, Impl-Runtime, Impl-Model |
| 2 | `plugins/ndf/scripts/lib/assignment.py#review_seats` | boundary | 1 ラウンドの採用上限 5 件を超えた |
| 2 | `plugins/ndf/skills/cross-review/scripts/launch-reviewer.sh#main` | normal | 1 ラウンドの採用上限 5 件を超えた |
| 2 | `plugins/ndf/scripts/lib/assignment.py#review_assign` | branch | コミット 0c3b60c7101ac9b439e0b13b677f8061b81eb851 にトレーラーが欠けています: Item-Id, Round, Impl-Runtime, Impl-Model |
| 3 | `plugins/ndf/scripts/lib/post_queue.py#Queue.flush` | long_method | 1 ラウンドの採用上限 5 件を超えた |
| 3 | `plugins/ndf/skills/cross-review/scripts/measure.py#_matches` | long_parameter_list | どの改善項目にも割り当てられていないコミットが 1 件（1a81a1a）。検証を回避した変更や、状態と実差分の食い違いを Pull Request に残さないため、この適用ラウンドを取り消します |
| 3 | `plugins/ndf/scripts/lib/assignment.py#resolve_participants` | long_method | どの改善項目にも割り当てられていないコミットが 1 件（1a81a1a）。検証を回避した変更や、状態と実差分の食い違いを Pull Request に残さないため、この適用ラウンドを取り消します |
| 3 | `plugins/ndf/skills/cross-review/scripts/state.py#_init_new_state` | long_method | どの改善項目にも割り当てられていないコミットが 1 件（1a81a1a）。検証を回避した変更や、状態と実差分の食い違いを Pull Request に残さないため、この適用ラウンドを取り消します |
| 3 | `plugins/ndf/scripts/lib/refresh.py#fetch` | long_method | どの改善項目にも割り当てられていないコミットが 1 件（1a81a1a）。検証を回避した変更や、状態と実差分の食い違いを Pull Request に残さないため、この適用ラウンドを取り消します |
