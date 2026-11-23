# #4000: stop-wait の本文の待ちを軽いモデル（haiku）で判定する

## 関連リンク

- 要求仕様: [issue-4000-requirements.md](./issue-4000-requirements.md)。受け入れ条件は番号で指す
- 設計書: [issue-4000-design.md](./issue-4000-design.md)・詳細設計書 [issue-4000-design-detail.md](./issue-4000-design-detail.md)
- モード: `standard`（新設するモジュール 2・シグネチャを変える関数 10 で、hook の判定の振る舞いを変える。conductor の判定）

## 修正対象

詳細設計書の「地図」から引き写す。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/src/ndf/adapters/launch_slot.py` | `gate`・`held`・`_wait_for` にスロットを待つ上限 |
| `plugins/ndf/src/ndf/adapters/claude.py` | `call_claude`・`_accounted`・`_prefaced`・`_run_claude`・`_popen` にスロットを待つ上限と組の選び方 |
| `plugins/ndf/src/ndf/domains/hooks/context.py` | `asked_since_prompt`・`HookContext.asked_this_turn` |
| `plugins/ndf/src/ndf/domains/hooks/counter.py` | `pushback_left`・`mark_model_wait`・`model_waited` |
| `plugins/ndf/src/ndf/domains/hooks/dispatch.py` | `GuardServices.deadline`・`run` の `deadline` |
| `plugins/ndf/src/ndf/domains/hooks/main.py` | SIGALRM と同じ締め切りを `dispatch.run` へ渡す |
| `plugins/ndf/src/ndf/domains/hooks/reply_judge.py`（新設） | `judge_text`・`parent_launch`・`Launch`・`ask`・`Judged`・`resting`・`mark_rest` |
| `plugins/ndf/src/ndf/adapters/reply_judge.py`（新設） | `endpoint_refusal`・`classify`・`main`・`run_cases` と質問の文 |
| `plugins/ndf/src/ndf/domains/relay/account_dir.py` | `effective_env` の `keys`・`Resolved.key_origin` |
| `plugins/ndf/src/ndf/domains/hooks/cli.py` | `ndf hooks judge-cases` |
| `plugins/ndf/src/ndf/domains/hooks/guards/stop_wait.py` | 呼ばない条件と判定の選び方 |
| `plugins/ndf/contracts/hooks/reply-judge-cases.json`（新設） | 判定の例の組 |
| `plugins/ndf/contracts/limits.json` | `hook.stop-wait.judge` の行と `hook.budget.stop` の値 |
| `plugins/ndf/contracts/hooks/guards.json` | `stop-wait` の `limits` と `summary` |
| `plugins/ndf/hooks/claude.json`・`codex.json` | `ndf hooks render` で作り直す |
| `plugins/ndf/src/ndf/checks/children.json` | 新設の 2 モジュールの登録（表に足すパッケージの行）。詳細設計の地図に無いが、子の表の検査 `child-deps` が新設のモジュールを照らすため足す |

## タスク分解

### Task 1: スロットを待つ上限と組の選び方
- **対象ファイル:** `adapters/launch_slot.py`・`adapters/claude.py` と `tests/adapters/test_launch_slot.py`・`test_claude.py`・`tests/domains/relay/test_relay_plan_claude.py`（偽の `gate` の引数の追随）
- **変更内容:** 詳細設計の「起動の前置きのスロットを待つ上限」と、CLI の下限の確かめを飛ばす指定（`cli_floor`。設計の決定 3 の「CLI の下限」）。どれも省けば今の振る舞いのまま
- **満たす受け入れ条件:** 9（判定の子の起動の前置き。設計の決定 3・8）
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 2: AskUserQuestion を使ったか・上限の手前か・締め切り
- **対象ファイル:** `domains/hooks/context.py`・`counter.py`・`dispatch.py`・`main.py` と対応するテスト
- **変更内容:** 詳細設計の「hook の文脈・数え手・締め切り」
- **満たす受け入れ条件:** 4
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

### Task 3: 判定のリクエスト（hook の側）
- **対象ファイル:** `domains/hooks/reply_judge.py`（新設）と `tests/domains/hooks/test_reply_judge.py`（新設）
- **変更内容:** 詳細設計の「判定のリクエスト（hook の側）」。設定の行は引数で受け、読む関数を 1 つに置く
- **満たす受け入れ条件:** 1・5・6a・9
- **進め方:** 失敗するテスト（偽の `popen`・`killpg`・`/proc`）→ 通す最小実装 → 整理

### Task 4: 判定の子・判定の例の組・`ndf hooks judge-cases`
- **対象ファイル:** `adapters/reply_judge.py`（新設）・`contracts/hooks/reply-judge-cases.json`（新設）・`domains/hooks/cli.py`・`domains/relay/account_dir.py` と対応するテスト
- **変更内容:** 詳細設計の「判定の子」「実効の設定で指定のキーを定義する層」と `ndf hooks judge-cases`
- **満たす受け入れ条件:** 2・3・9
- **進め方:** 失敗するテスト（偽の `call`・偽の `resolve`）→ 通す最小実装 → 整理

### Task 5: stop-wait の配線と設定
- **対象ファイル:** `domains/hooks/guards/stop_wait.py`・`contracts/limits.json`・`contracts/hooks/guards.json`・`hooks/claude.json`・`hooks/codex.json` と `tests/domains/hooks/test_guards.py`・`test_budget.py`
- **変更内容:** 詳細設計の「guard `stop-wait`」と「設定と登録」
- **満たす受け入れ条件:** 1・4・5・6・7・8・10
- **進め方:** 失敗するテスト → 通す最小実装 → 整理

## レビューの単位

| 単位 | 含むタスク | 満たす受け入れ条件 | 見込みの差分の行数 | 実際の行数 |
| --- | --- | --- | --- | --- |
| u1 起動の前置き | Task 1 | 4000:9 | 約 150 | 281（`plugins/ndf` の下。追加 258・削除 23） |
| u2 hook の文脈 | Task 2 | 4000:4 | 約 250 | |
| u3 判定のリクエスト | Task 3 | 4000:1・4000:5・4000:6a・4000:9 | 約 400 | |
| u4 判定の子 | Task 4 | 4000:2・4000:3・4000:9 | 約 450 | |
| u5 stop-wait の配線と設定 | Task 5 | 4000:1・4000:4・4000:5・4000:6・4000:7・4000:8・4000:10 | 約 300 | |

u1・u2 は互いに依存しない。u3 は u2 の締め切りを使わず単独で通る。u5 は u2・u3 の後に切る。`limits.json` と `children.json` は別のレーンの課題（#4016）が使っている間は変えず、u3・u4・u5 の登録と設定の行はその課題が main に入った後に足す。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | 触るファイルのうち大きいのは `account_dir.py`（973 行）と `claude.py`（710 行）で、足すのは引数 1〜2 個と欄 1 つ。同じファイルを触るタスクは 1 つずつ |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ |
| スプリントの終わり（cross-refactoring） | `domains/hooks/reply_judge.py` の子の起動と組の止め方、`outbox.kick` の起動 | どちらも `[sys.executable, "-P", "-m", <モジュール>]` を新しい組で起動する同じ形 |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 判定の子が `claude` の子孫を残す | 組ごと止めて回収する呼び出しを、偽の `Popen` と `killpg` の単体テストで縛る（Task 3） |
| hook の閉包に `ndf.adapters.claude` が入る | hook の import の表の検査（既存のテスト）で落とす |

## 完了の定義

- [ ] 受け入れ条件 1〜10（6a を含む）をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `ndf hooks judge-cases` の外れが 0 件の出力を実装の PR に貼った（受け入れ条件 3）
- [ ] CI のテストと静的解析が通った
