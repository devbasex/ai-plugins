# #4068 #4199: 中継の下で待つ子のキャッシュを寿命の手前で起こし、書き直し 1 回分の費用に届いたらやめる

## 関連リンク

- 要求仕様: [issue-4068-requirements.md](./issue-4068-requirements.md)（正は #4068 の本文）、[issue-4199-requirements.md](./issue-4199-requirements.md)（正は #4199 の本文。#4068 へ統合して同じ設計と実装で満たす）。受け入れ条件は `<課題>:<番号>` で指す
- 設計書: [issue-4068-4199-design.md](./issue-4068-4199-design.md)、詳細設計書: [issue-4068-4199-design-detail.md](./issue-4068-4199-design-detail.md)、測った結果: [issue-4068-4199-design-measured.md](./issue-4068-4199-design-measured.md)
- 会話の記録の末尾を読む部品の設計（#4089）: [issue-4089-design-detail.md](./issue-4089-design-detail.md)。#4089 は未実装なので、この計画の単位 U1 でその「記録の読み手」を作る（詳細設計の冒頭の「#4089 との順序」）
- モード: `standard`（上限の設定の `cache.*`・中継が子へ渡す hook の登録・環境変数 `NDF_RELAY_REWAKE`・集計のコマンド `ndf relay cache report` が増え、公開インタフェースが変わる。課題 2 つ・触るファイル 47 で、実装計画を省く条件に当たらない）

## 修正対象

詳細設計書の「地図」の表と、設計書の「設計の結果」の触るファイルから引き写した。パスは `plugins/ndf/` からの相対で書く（`docs/glossary/` だけはリポジトリの根から）。

地図に無いファイルを 2 つ足す。どちらも、今の行数に足すと `ndf check` の構造の規則（`checks/rules.json` の `structure.max_lines` 1,000 行）を超えるためである。

- 中継のラッパーの起動のテストは、`tests/domains/relay/test_relay_wrapper.py`（今 907 行）へ足さず、`tests/domains/relay/test_relay_wrapper_rewake.py` を新設して置く
- UserPromptSubmit の guard（`turn`）の世代と起こす文の見分けのテストは、`tests/domains/hooks/test_guards.py`（今 857 行）へ足さず、`tests/domains/hooks/test_turn_rewake.py` を新設して置く

| ファイル | 変える中身 | 単位 |
| --- | --- | --- |
| `src/ndf/adapters/transcript.py`（新設） | #4089 の詳細設計の「記録の読み手」の `ToolUse`・`Reply`・`Tail`・`ContextRead`・`last_context`・`timestamp_seconds`・`read_tail` | U1 |
| `src/ndf/domains/hooks/context.py` | `read_tail` を `adapters/transcript.py` へ移し、import に替える。呼び手は替えない | U1 |
| `contracts/limits/cache.wake.json`・`cache.write-ratio.json`・`cache.read-ratio.json`・`cache.output-ratio.json`・`cache.wake-cost.json`・`cache.hit.json`・`cache.rewake.json`・`hook.budget.stop-rewake.json`（新設） | 方針のファイル。`cache.rewake` の `versions` は空のまま入れる | U2 |
| `contracts/limits/hook.transcript-tail.json` | `read_by` だけを変える | U2 |
| `contracts/schemas/limits.schema.json`・`src/ndf/contracts/limits.py` | スキーマと `Policy` に `ratios`・`default`・`ratio`・`versions`。`texts`（新設）。`value` が数でない値で `LimitsError` | U2 |
| `src/ndf/checks/rules.json` | 規則 `limit-readers` の `readers` に `ndf.contracts.limits.texts`・`ndf.domains.relay.keep_cache.ratios.get`、`dynamic` に `ndf.domains.hooks.budget.rewake_budget` | U2 |
| `src/ndf/domains/relay/keep_cache.py`（新設） | `LastTurn`・`TurnRead`・`Usage`・`Reply`・`Opening`・`Ratios`・`Step` と `last_turn`・`wake_reply`・`opening`・`is_wake_input`（常に None）・`ratios`・`wake_at`・`n_max`・`next_step`・`hit` | U3 |
| `src/ndf/domains/relay/signals.py` | `read_keep`・`bump_generation`・`revoke_generation`・`update_keep`・`append_cache_log`・`rewake_on`・`launch_version`（新設）、`set_launch` に `rewake`・`claude_version`、定数 `KEEP_PREFIX`・`CACHE_LOG`・`SESSION_ID_RE` | U4 |
| `src/ndf/domains/hooks/guards/turn.py` | 今の起動を突き合わせてから世代を入力の時刻に書き換え、状態が無ければ世代だけの状態を作る。`prompt` の頭が `WAKE_HEAD` の入力を起こす文の入力として見分ける | U4 |
| `contracts/domains/ndf-files.json` | `relay-keep`・`relay-cache-log` の行 | U4 |
| `src/ndf/domains/hooks/rewake.py`（新設） | `run`・`_target`・`_settle`・`_reconcile`・`_wait`・`_finish` | U5 |
| `hooks/entry.py` | `argv[3]` が `rewake` なら `rewake.run` の終了コードを返す | U5 |
| `src/ndf/domains/hooks/registration.py` | `rewake_entry`・`rewake_settings`・`rewake_unreachable`（新設）。`render` は変えない | U5 |
| `src/ndf/domains/hooks/budget.py` | `rewake_budget`・`rewake_reach`・`rewake_registrations`（新設）。`registrations_of`・`budget_registrations`・`_file_registrations`・`_registration_errors` がバックグラウンドの登録を分けて検査し、プラグインの登録の `asyncRewake` を破れとする | U5 |
| `contracts/hooks/runtimes.json` | claude の Stop に `async_rewake`（予算 `stop-rewake`・`timeout_max` 3,300） | U5 |
| `contracts/domains/env.json` | `NDF_RELAY_REWAKE` の行 | U5 |
| `hooks/claude.json`・`docs/contracts/hooks.md`（再生成） | `ndf hooks render`・`ndf check --write-spec` で作り直す（`asyncRewake` は載らないことを確かめる） | U5 |
| `src/ndf/adapters/cli_floor.py` | `record_key`（新設）。`resolve`・`version_at`・`require` は変えない | U6 |
| `src/ndf/domains/relay/wrapper.py`・`wrapper_launch.py`・`account_choice.py` | `_start`・`_try`・`_fallback`（変更）、`account_choice.metered_of`・`version_env`（新設）、`target_env`・`key_target`・`key_child` に `decl` の引数 | U6 |
| `src/ndf/domains/relay/cli_cache.py`（新設）・`src/ndf/domains/relay/cli.py` | `report`・`run`（新設）、`build_parser` にサブコマンド `cache report` | U7 |
| `contracts/schemas/io/relay.args.schema.json`・`relay.result.schema.json` | `cache report` の引数と結果 | U7 |
| `docs/contracts/files.md`・`commands.md`（再生成） | `ndf check --write-spec` で作り直す | U7 |
| `skills/development-workflow/references/relay.md` | hook の一覧に起こす hook、記録の種類に起こす記録、見直しの手順（20 件ごと） | U7 |
| `docs/glossary/glossary.json`・`docs/glossary.md`（再生成） | 要求の新しい 7 語と設計の「用語」の新設の 4 語（`ndf records glossary render` で作り直す） | U7 |

テストのファイルは各タスクの対象ファイルに挙げる。

## タスク分解

テストはすべて、時刻を差し替えた時計と差し替えた眠りの関数で進め、実際に待たない（4068:16・4199:14）。会話の記録と Stop の入力は、実測の形から作った見本のファイルを使う。並行は hook の呼び出しの順を差し替えて流す。境界の値（間隔 − 1 秒・間隔、上限 − 1・上限）を各タスクのテストに入れる。`.md` の文言を照合するテストは書かない。

### Task 1: 今の振る舞いを縛る退行テスト

- **対象ファイル:** `tests/domains/hooks/test_registration.py`、`tests/domains/hooks/test_budget.py`、`tests/domains/relay/test_relay_wrapper_rewake.py`（新設）、`tests/domains/hooks/test_turn_rewake.py`（新設）
- **変更内容:** 機能追加の最初のタスクとして、変えてはいけない振る舞いを先に縛る。(1) プラグインの登録（`render("claude")` と `hooks/claude.json`）に `asyncRewake` の行が無い。(2) 中継のラッパーが、利用者の渡した `--settings` を初回の起動と `--resume` の再開でそのまま 1 つだけ持つ。(3) 利用者の入力では #3995 の数え手が今どおり数え直される。新設のテストのファイル 2 つは「修正対象」の頭のとおり 1,000 行の規則のため
- **満たす受け入れ条件:** 4068:3（プラグインの登録と利用者の `--settings` を変えない側）、4068:10（利用者の入力の側）
- **進め方:** 今のコードで通るテストを書く。後のタスクの前後で同じテストが通ることを確かめる

### Task 2: 会話の記録の末尾を読む部品（#4089 の「記録の読み手」）

- **対象ファイル:** `src/ndf/adapters/transcript.py`（新設）、`src/ndf/domains/hooks/context.py`、`tests/adapters/test_transcript.py`（新設）
- **変更内容:** #4089 の詳細設計の「記録の読み手」と「#4068 との受け渡し」のとおりに `last_context`・`timestamp_seconds` を作り、`read_tail` を `context.py` から移して import に替える。`last_context` が返す 3 つの状態（`current`・`behind`・`none`）と `none` の理由（`compacted`・`partial`・`empty`・`unrecorded`・`form`）を、詳細設計の冒頭の表の見本で確かめる。呼び手（guard・ラッパー・`observe`）の置き換えは #4089 の実装に残す
- **満たす受け入れ条件:** 4068:7（今回の応答を確かめる前提）
- **進め方:** 失敗するテスト（見本ごとの状態と理由）→ 通す最小実装 → 整理。`read_tail` の移動は、既存の `test_context*.py` が移動の前後で同じに通ることで確かめる

### Task 3: 上限の設定に `cache.*` の方針を足す

- **対象ファイル:** `contracts/limits/` の方針のファイル 8 つ（新設）と `hook.transcript-tail.json`、`contracts/schemas/limits.schema.json`、`src/ndf/contracts/limits.py`、`src/ndf/checks/rules.json`、`tests/contracts/test_limits.py`
- **変更内容:** 詳細設計の「上限の設定の方針のファイル」と「読み手の突き合わせ」のとおり。`cache.rewake` の `versions` は空で入れる（実装の PR は働かない状態でマージする）。`limits.texts` が文字列の配列を返し、`value` が数でない値で `LimitsError` を出す
- **満たす受け入れ条件:** 4068:13（値の置き場と読み口。hook とラッパーの読み口の確かめは Task 4・7・8 で行う）
- **進め方:** 失敗するテスト（方針のファイルを差し替えると `texts`・`value` が差し替えた値を返す・数でない値で例外）→ 通す最小実装 → 整理。規則 `limit-readers` の破れ 0 件は `ndf check` で確かめる

### Task 4: 起こす判定（`keep_cache`）

- **対象ファイル:** `src/ndf/domains/relay/keep_cache.py`（新設）、`tests/domains/relay/test_keep_cache.py`（新設）
- **変更内容:** 詳細設計の「判定（`keep_cache`）」のとおり。`last_turn` は状態の寿命を引数に受けず、書き込みが両方 0 の応答では `ttl` を None で返す。`wake_at` は最後の応答の時刻から寿命 − 余白（1 時間で 3,000 秒）、5 分の寿命では起こさない。`n_max` は書き直し 1 回の費用に届く回数（Opus 5.5・1h・`S` 400,000・`d` 2,000 で 35）。`next_step` は費用の照合（`spent` と判定の時の `S` の書き直し）を含む。`wake_reply` は起床の呼び出しを寿命別の書き込みで合計し、項目の欠け・整数でない・負・和の食い違いで `priced` を偽にする。`is_wake_input` は常に None
- **満たす受け入れ条件:** 4068:1・4・6・8・14、4199:2・5・7・8・9・12
- **進め方:** 失敗するテスト（設計書の「テスト設計」の該当する行の見本と境界の値）→ 通す最小実装 → 整理

### Task 5: 起こす状態と起こす記録の読み書き（`signals`）

- **対象ファイル:** `src/ndf/domains/relay/signals.py`、`contracts/domains/ndf-files.json`、`tests/domains/relay/test_keep_cache.py`
- **変更内容:** 会話ごとの状態（`keep-<会話>.json`）と起こす記録（`cache-log.jsonl`）を読み書きする関数。世代は新しい時刻でだけ書き換え、同じ応答の取り合いを `contested` にし、その応答で始まった起こしていない待ちを `wait_void` で取り消す。会話の ID が UUID の形でなければ保存先のファイルの名前を作らない。`set_launch` に `rewake`・`claude_version` を足す
- **満たす受け入れ条件:** 4068:2（世代の書き換えと取り合い）・11（行の項目と `wait_id`・`wake_id`）、4199:11（行が倍率を持つ）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 6: UserPromptSubmit の guard が世代を書き換え、起こす文の入力を見分ける

- **対象ファイル:** `src/ndf/domains/hooks/guards/turn.py`、`tests/domains/hooks/test_turn_rewake.py`
- **変更内容:** 詳細設計の「UserPromptSubmit の guard（`turn`）」のとおり。今の起動を突き合わせてから世代を入力の時刻に書き換え、状態が無ければ世代だけの状態を作る。`session_id` が `NDF_RELAY_SESSION` と違う入れ子の claude では状態を読まず書かない。`prompt` の頭が `WAKE_HEAD` の入力では #3995 の数え手を数え直さない
- **満たす受け入れ条件:** 4068:2（入力で失効させる側）・10
- **進め方:** 失敗するテスト → 通す最小実装 → 整理。Task 1 の退行テストが通り続けることを確かめる

### Task 7: 起こす hook（`rewake`）と登録・予算の検査

- **対象ファイル:** `src/ndf/domains/hooks/rewake.py`（新設）、`hooks/entry.py`、`src/ndf/domains/hooks/registration.py`、`src/ndf/domains/hooks/budget.py`、`contracts/hooks/runtimes.json`、`contracts/domains/env.json`、`hooks/claude.json`（再生成）、`docs/contracts/hooks.md`（再生成）、`tests/domains/hooks/test_rewake.py`（新設）、`tests/domains/hooks/test_registration.py`、`tests/domains/hooks/test_budget.py`
- **変更内容:** 詳細設計の「起こす hook（`hooks/rewake.py`）」と「hook の登録」のとおり。`NDF_RELAY_REWAKE` が無ければ眠らず状態と記録を読まずに 0。待つ子でない Stop（監視だけを含む）は記録を読まずに 0。`_settle` はロックの中で読み直した状態で寿命を補い、補えなければ `stop_waking`（`scope` は `once`）を書いて 0。眠った後にロックの中で世代・起動・質問のファイルを突き合わせ、起こすなら終了コード 2。`budget` はバックグラウンドの登録を `async_rewake` の予算で検査し、プラグインの登録の `asyncRewake` を破れとする
- **満たす受け入れ条件:** 4068:1・2・3（hook の側）・7・8・9・11・14、4199:1・3・4・7・10
- **進め方:** 失敗するテスト（設計書の「テスト設計」の該当する行。呼び出しの順の差し替えで並行を再現する）→ 通す最小実装 → 整理

### Task 8: ラッパーが確かめた版の子にだけ起こす hook を登録する

- **対象ファイル:** `src/ndf/adapters/cli_floor.py`、`src/ndf/domains/relay/wrapper.py`、`src/ndf/domains/relay/wrapper_launch.py`、`src/ndf/domains/relay/account_choice.py`、`tests/adapters/test_cli_floor.py`、`tests/domains/relay/test_relay_wrapper_rewake.py`
- **変更内容:** 詳細設計の「ラッパー（`wrapper._start`）」のとおり。起動先ごとの最終の環境（`version_env`）で解いた `claude` の版が `cache.rewake` の `versions` のどれかと完全に一致する候補だけ、起動の引数に `--settings <rewake_settings の JSON>`、子の環境に `NDF_RELAY_REWAKE=1`、起動の記録に `rewake` 真を入れる。利用者の `--settings` があれば足さない。同じ実行ファイルなら `--version` の起動は `_start` の下限の確かめの 1 回だけ（`record_key` で記録を引く）。`wrapper.py` は今 951 行なので、足す処理は `wrapper_launch.py`・`account_choice.py` の関数に置き、`wrapper.py` には呼び出しだけを足す
- **満たす受け入れ条件:** 4068:3（ラッパーの側）・13（ラッパーが `limits.texts` から `versions` を読む）
- **進め方:** 失敗するテスト（一覧が `2.1.330`・`2.1.332` のとき `2.1.331`・`2.1.333`・`2.1.329` では足さない、など設計書の「テスト設計」の 3 の行）→ 通す最小実装 → 整理。Task 1 の退行テストが通り続けることを確かめる

### Task 9: 集計のコマンド `ndf relay cache report` と文書

- **対象ファイル:** `src/ndf/domains/relay/cli_cache.py`（新設）、`src/ndf/domains/relay/cli.py`、`contracts/schemas/io/relay.args.schema.json`、`contracts/schemas/io/relay.result.schema.json`、`docs/contracts/files.md`・`commands.md`（再生成）、`skills/development-workflow/references/relay.md`、`docs/glossary/glossary.json`、`docs/glossary.md`（再生成）、`tests/domains/relay/test_keep_cache.py`、`tests/domains/relay/test_relay_cli_parse.py`
- **変更内容:** 詳細設計の「集計（`ndf relay cache report`）」のとおり。費用は行に書かれた倍率で出し、今の倍率で計算し直さない。起こした回数は `wake_id` の重複を除いて数える。`wake_result` の欠けた待ちと `priced` が偽の待ちは避けた書き直しに入れず `unpriced_waits` に数える。手順書と用語集は設計の「用語」と詳細設計の地図の表のとおりに足す
- **満たす受け入れ条件:** 4068:12・13（集計が行の倍率で費用を出す）、4199:5・6・8・11
- **進め方:** 失敗するテスト（設計書の「テスト設計」の 12 の行の見本）→ 通す最小実装 → 整理。文書は `ndf check` と再生成の差分 0 件で確かめる（テスト駆動を適用しない。`.md` の文言を照合するテストは書かない決まり）

4068:15・4199:13 は文書どうしの突き合わせなので、設計レビューで確かめ、タスクを置かない。4068:5 は 2026-11-24 に外した。

## レビューの単位

見込みの差分は全体で約 4,000 行あり 1,000 行を超えるため、タスクの依存の向きで分けた。どの単位も単独でテストが通り、`cache.rewake` の `versions` が空なので、どの単位の後も子の振る舞いは変わらない。

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| U1 退行テストと記録の読み手 | Task 1・2 | 4068:7・10（利用者の入力の側） | 約 550 | （実装の後に書き足す） |
| U2 上限の設定 | Task 3 | 4068:13 | 約 300 | （実装の後に書き足す） |
| U3 起こす判定 | Task 4 | 4068:1・4・6・8・14、4199:2・5・7・8・9・12 | 約 800 | （実装の後に書き足す） |
| U4 状態と記録・guard | Task 5・6 | 4068:2・10・11、4199:11 | 約 550 | （実装の後に書き足す） |
| U5 起こす hook | Task 7 | 4068:1・2・3・7・8・9・11・14、4199:1・3・4・7・10 | 約 900 | （実装の後に書き足す） |
| U6 ラッパーの登録 | Task 8 | 4068:3・13 | 約 450 | （実装の後に書き足す） |
| U7 集計と文書 | Task 9 | 4068:12・13、4199:5・6・8・11 | 約 650 | （実装の後に書き足す） |

依存の順は U1・U2 → U3 → U4 → U5 → U6、U7 は U4 の後に流せる。U3・U5 は 400 行の目安を超えるが、判定の関数群と hook はそれぞれ 1 つのモジュールとそのテストで、分けると単位が単独で読めない（判定の一部だけでは hook の振る舞いを確かめられない）。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触る既存のファイルで最も大きいのは `hooks/context.py` 999 行と `relay/wrapper.py` 951 行。`context.py` は Task 2 で `read_tail` を移すので減る。`wrapper.py` を触るのは 9 タスクのうち 1 つ（Task 8）で、足す処理は `wrapper_launch.py`（255 行）・`account_choice.py`（776 行）に置く。ラッパーを覆うテストは `tests/domains/relay/test_relay_wrapper*.py` の 9 ファイルある。テストの 1,000 行の規則に当たる 2 つは新設のファイルで避ける（「修正対象」の頭） |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | (1) 起床の費用の数え方: `rewake.py`（U5）が行に書く寿命別の書き込みと倍率と、`cli_cache.py`（U7）が行から出す費用が、同じ形の計算を 2 か所に持ちうる。(2) 世代の書き換え: `guards/turn.py`（U4）と `rewake.py`（U5）が `signals` の世代の関数を呼ぶ前後の突き合わせ（起動・会話の ID の形）が重なりうる | 複数の単位が同じ計算と同じ状態のファイルを扱うため |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `wrapper.py` に足すと 1,000 行の規則を超える | Task 8 の変更内容のとおり、処理を `wrapper_launch.py`・`account_choice.py` に置き、`wrapper.py` は呼び出しだけにする。`ndf check` で行数を確かめてから単位の PR を出す |
| #4089 の実装が別に進み、`adapters/transcript.py` が二重に作られる | U1 に入る前に main と #4089 の状態を確かめる。#4089 が先に入っていれば Task 2 を外し、作らずに使う（詳細設計の冒頭の「#4089 との順序」） |
| 起こす hook が誤って子を起こし、応答の終わりや承認ゲートを乱す | 実装の PR では `cache.rewake` の `versions` を空、`is_wake_input` を常に None にしてマージし、どの子にも登録しない。Task 1 の退行テストと 4068:3 のテストで、登録が入らないことを縛る |
| 会話の記録の形が Claude Code の版で変わる | 形が違えば起こさず `stop_waking` を残す（設計の決定 4）。版ごとの確かめは有効化の Pull Request の 11 項目の実測で行う |

## 切り戻し手順

- どの単位も `versions` が空なので、単位の PR を revert すれば今の振る舞い（何もしない）に戻る。データの移行は無い（起こす状態と起こす記録は新しいファイルで、既存の記録を変えない）
- 有効化の Pull Request の後に問題が出たときは、`cache.rewake` の `versions` を空に戻せば、どの子にも起こす hook を登録しなくなる

## 完了の定義

- [ ] 4068:1〜4・6〜14 と 4199:1〜12・14 を満たし、条件ごとに設計書の「テスト設計」の行のテストが対応して通っている（4068:5 は外した。4068:15・4199:13 は設計レビューで確かめた）
- [ ] テストはすべて差し替えた時計と呼び出しの順で動き、実際に待たない（4068:16・4199:14）
- [ ] `ndf check`（規則 `limit-readers`・構造の規則を含む）と、`hooks/claude.json`・`docs/contracts/*.md`・`docs/glossary.md` の再生成の差分 0 件
- [ ] 実装の PR は `cache.rewake` の `versions` が空のままマージする。#4068 と #4199 が終わるのは、マージした実装で設計の決定 7 の 11 項目を測り、通った版を一覧に載せる有効化の Pull Request を終えたときである（#4068 の要求仕様の冒頭の注意）。有効化の Pull Request はこの計画の単位に含めない（マージした実装を使って測るため、このスプリントの main へのマージの後に行う）
