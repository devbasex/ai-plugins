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
| boundary | unit | — | agy / kiro | 検証中 | 1 |

**なぜ**: review_assign の round_no < 1 の下限境界条件で AssignmentError を送出する振る舞いが scripts/tests 内で固定されていない。同モジュールの impl_assign や review_seats には round_no < 1 の境界テストがあるが、review_assign だけ抜けている。

**手順**: 1. test_lib_assignment.py で assignment.review_assign(0, "claude") および assignment.review_assign(-1, "claude") を呼び出す
2. どちらの呼び出しでも assignment.AssignmentError が送出されることを検証する
3. 例外メッセージに「ラウンド番号は 1 以上です」が含まれることを検証する

### R2-004 — `plugins/ndf/scripts/lib/assignment.py#review_assign`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| branch | unit | — | agy / kiro | 未着手 | 0 |

**なぜ**: review_assign は適用の役を持たない工程が使う公開入口だが、scripts/tests には直接の固定が無い。in-scope の test_lib_assignment.py は assign / impl_assign / review_seats を固定するだけで、この関数の輪番（母集合3者から dropped=(round_no-1)%3 を外す各分岐）は通っていない。out-of-scope の cross-review テストは _round_reviewers の照合オラクルとして呼ぶだけで、この関数自身の戻り値を固定していない。

**手順**: 1. test_lib_assignment.py の assignment フィクスチャで各ホスト（claude, codex, agy, kiro）について review_assign(round_no, host) を round 1..6 で呼び出す
2. 各ホストで返る担当ペアの一覧が 3 ラウンド周期で循環し、現状の決定結果（例: claude は [['agy', 'kiro'], ['codex', 'kiro'], ['codex', 'agy']] が 2 周する）と完全一致することを検証する
3. 返されるレビュー担当が常に 2 者であり、指定したホスト自身を含まないことを併せて検証する

### R2-005 — `plugins/ndf/scripts/lib/assignment.py#review_assign`

| 兆候・経路 | 手法・階層 | 重要度 | 提案元 | 状態 | コミット |
| --- | --- | --- | --- | --- | ---: |
| error | unit | — | codex / agy | 未着手 | 0 |

**なぜ**: review_assign に HOST_RUNTIMES に含まれない無効なホスト名が渡された場合、内部の review_pool から AssignmentError（「ホストになれないランタイムです」）が送出されるエラー経路が固定されていない。

**手順**: 1. test_lib_assignment.py で assignment.review_assign(1, "gemini") や assignment.review_assign(1, "unknown") を呼び出す
2. assignment.AssignmentError が送出されることを検証する
3. 例外メッセージに「ホストになれないランタイムです」が含まれることを検証する

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
