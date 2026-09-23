# #892 / #901: cross-review の母集合にホストを入れ、supervisor が worker の途中の通知で止まらないようにする — 実装計画

## 関連リンク

- 要求と受け入れ条件: [issue-892-901-requirements.md](issue-892-901-requirements.md)
- 設計: [issue-892-901-design.md](issue-892-901-design.md)
- 決定の記録: [issue-892-901-design-decisions.md](issue-892-901-design-decisions.md)
- 設計 PR: #902（develop へマージ済み）

## モード

standard（conductor の判定。設計 PR を関門 1 で承認済み）

## 目的と非目的

達成したい状態:

- cross-review の母集合をホストを含む 4 者にし、Claude Code から `--exclude agy` で起動したときに 3 者で輪番を回す（#892）
- supervisor が worker の途中の通知を受けても応答を終えて止まらない（#901）

やらないこと:

- 既定の母集合から agy を外すこと（#786）
- cross-refactoring の母集合の変更
- worker の定義 `plugins/ndf/agents/worker.md` の規則の抜粋の書き換え（要求の「含まない」。起動指示の `置き場所` の項目で worker へ届く）
- `docs/specifications/cross-review-participants-and-seats.md` の書き換え（確定仕様化で `plan-to-spec` が行う）
- 版上げ（マイルストーン 26 の配布で行う）

## 前提

- 前提 1: #828 の実装（f7f27c8b）が develop に入り、worker の規則は 6 個になった。AC9 の「規則の数は 10 と 5 のまま」は、設計の時点の数を指す。**規則を足さない**という意味に読み、supervisor 10 個・worker 6 個のまま保つ
- 前提 2: 設計の「`prompt` の行の『守る規則 4 個』を 5 個へ直す」は #828 の実装で「6 個」に直っており、この変更では触らない

## 実装中に決めたこと

- **再開で担当の引数（`--only` / `--include` / `--exclude` / `--require-all`）を渡したときは、今の母集合（ホストを含む）で作り直す。** `_apply_resume_args_block` は保存された `pool` ではなく `review_pool(host)` から参加者を作り直すため、変更の前に始めたループを `--exclude agy` で再開すると使える者にホストが加わる。担当の引数を渡すのは明示の作り直しであり、要求の互換の条件（AC6・AC7）は引数を渡さない再開に限っている。保存された `pool` を使う形はコードと設計の追記が要るため採らない。`test_state_resume_args.py` の 4 件の期待値をこの形へ合わせた

## 受け入れ条件

要求の AC1〜AC12 をそのまま使う（[issue-892-901-requirements.md](issue-892-901-requirements.md) の「受け入れ条件」）。

## 修正対象

| ファイル | タスク |
| --- | --- |
| `plugins/ndf/scripts/lib/assignment.py` | 1 |
| `plugins/ndf/skills/cross-review/scripts/state.py` | 1・2・3 |
| `plugins/ndf/skills/cross-refactoring/tests/test_assignment.py` | 1・3 |
| `plugins/ndf/skills/cross-review/tests/test_state_review_pool.py`（新設） | 1・2・3 |
| `plugins/ndf/skills/cross-review/SKILL.md` / `docs/04-contracts.md` / `docs/05-pool-and-convergence.md` | 4 |
| `CLAUDE.md` の cross-review の節 | 4 |
| 生成物（`bash scripts/build-runtime-plugins.sh`） | 4 |
| `plugins/ndf/skills/development-workflow/references/waiting.md` | 5 |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md` | 5 |

## タスク分解

### Task 1: 母集合にホストを入れる（F1）

- **変更内容:** `review_pool(host)` が `list(ALL_RUNTIMES)` を返す。モジュールの docstring の表と `test_assignment.py` の docstring を直す
- **満たす受け入れ条件:** AC1・AC2・AC3・AC4
- **進め方:** 失敗するテスト（4 ホストで `ALL_RUNTIMES`・`init` の母集合にホスト・`--exclude <ホスト>` の成功・`--exclude agy` の 3 ラウンドの組）→ 実装

### Task 2: 席の埋め合わせでホストを確かめない（F2）

- **変更内容:** `_resolve_reviewers` のホストの確認と `fallback = [host]` を消す。0 者は終了コード 1、1 者は `<その者>-2`。info とコメントを直す
- **満たす受け入れ条件:** AC5
- **進め方:** 失敗するテスト（確認を差し替え、1 者で `fallback == []` と席、0 者で終了コード 1、ホストの確認が呼ばれない）→ 実装

### Task 3: 変更の前の状態ファイルの再開で席を保つ（F3）

- **変更内容:** `_round_reviewers` の順 4 を `[r for r in ALL_RUNTIMES if r != host]` の式にする。docstring の表を直す
- **満たす受け入れ条件:** AC6・AC7
- **進め方:** `test_review_seats_match_the_previous_rotation_for_three_available` の母集合を式へ置き換え、`host` だけの状態と `participants` を持つ状態で 4 ホスト × ラウンド 1〜12 を固定するテスト → Task 1 の後で失敗することを確かめ → 実装

### Task 4: cross-review の文書と生成物を直す（F1・F2）

- **変更内容:** 設計の構成要素の表のとおり。`description` を変えるため生成物を同期する
- **満たす受け入れ条件:** AC1〜AC5 の文書の側
- **進め方:** テスト駆動を適用しない（文書。文言を固定するテストは書かない）。`grep -rn "ホストを除\|other than the host" plugins/ndf CLAUDE.md` が cross-review について 0 件になることで確かめる

### Task 5: supervisor の途中の通知の手と worker の報告の写し（F4・F5）

- **変更内容:** `waiting.md` に `### 途中の通知を受けたとき` を新設し「2 回目の通知を待つ」を conductor に限る。`agent-layers.md` の supervisor の規則 4・worker の規則 5・起動指示の `置き場所`・作業の報告の表の `置き場所` の行を直す
- **満たす受け入れ条件:** AC8・AC9・AC10
- **進め方:** テスト駆動を適用しない（文書）。設計の「入出力の契約」の文と照らして読む

### Task 6: 2 段の再現（AC11）

- **変更内容:** コードは変えない。`claude -p --output-format stream-json` で supervisor 役 → worker 役（規則 5 に反して `sleep 30` を背景に残して応答を終える）を動かし、記録を実装の Pull Request に残す
- **満たす受け入れ条件:** AC11

## 影響範囲

- cross-review の `init`（Claude Code から起動すると `claude -p` が席に入る）
- 進行中の cross-review のループの再開（席は変わらない）
- 3 層で進める全持ち場（supervisor の待ち方・worker の報告の写し）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `state.py` は 4800 行を超える 1 ファイル | 触るのは 2 関数とコメント 1 か所で狭く、既存テストが厚い。**実装の後の構造改善で足りる**（今回は cross-refactoring を通さない利用者の指示） |
| 既存テストがホストを除く母集合を前提に書かれている | 全体テストを Task ごとに通し、前提を持つテストを洗い出して直す |
| #828 で書き換わった `agent-layers.md` と文がずれる | develop の最新（4ef28ff0）から始めており、衝突は無い。規則の数は前提 1 のとおり保つ |

## 切り戻し手順

- Pull Request の revert で戻る。状態ファイルの形を変えないため、変更の後に作った状態ファイル（`fallback` が空）も変更の前のコードで再開できる

## 完了の定義

- [ ] AC1〜AC7・AC12 を pytest で、AC8〜AC10 を文書の読み合わせで、AC11 を `claude -p` の再現の記録で確かめ、条件ごとに証跡が対応している
- [ ] `claude plugin validate .` が終了コード 0
