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
| normal | unit | — | agy | 未着手 | 0 |

**なぜ**: assignment.py で新設された seat_runtime(seat: str) は、席名から基底ランタイム名を取り出す共通層関数であり、結果受け口・起動スクリプト・監視処理で広く使われる。しかし共通層テスト（plugins/ndf/scripts/tests/）には単体テストが存在しない。接尾辞なしのランタイム名（claude, codex, agy, kiro）および同一ランタイムの副席名（-2〜-9 接尾辞）から正確にランタイム名が抽出される正常系の振る舞いを共通層単体テストとして固定する必要がある。

**手順**: 1. plugins/ndf/scripts/tests/test_lib_assignment.py に test_seat_runtime_extracts_runtime_name を追加する。
2. ALL_RUNTIMES の全ランタイム名（'claude', 'codex', 'agy', 'kiro'）をそのまま渡した場合に、同一のランタイム名が返ることを検証する。
3. ハイフン付き席名（'kiro-2', 'claude-9', 'agy-3' 等）を渡した場合に、接尾辞を除去した基底ランタイム名が正しく返ることを検証する。

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
