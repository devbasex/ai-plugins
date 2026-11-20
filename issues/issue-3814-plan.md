# #3814: 中継のラッパーが回答待ちの質問のあいだ子を止めず、子の端末へ書かない

## 関連リンク

- 課題: https://github.com/devbasex/ai-plugins/issues/3814
- 要求仕様: [issue-3814-requirements.md](./issue-3814-requirements.md)
- 設計書: [issue-3814-design.md](./issue-3814-design.md)
- 詳細設計: [issue-3814-design-detail.md](./issue-3814-design-detail.md)
- 先に入る変更: [issue-3842-design.md](./issue-3842-design.md)（#3842）

## モード

standard（pace: normal）

## 目的と非目的

目的は要求仕様の「目的」のとおりで、承認ゲートの質問を利用者かオーガナイザーが答えるまで、ラッパーが子を止めず子の端末へ書かないことと、手順書 `relay.md` の「承認ゲートを越えない守り」を 11 系の振る舞いだけで書くことである。

非目的は要求仕様の「対象範囲」の「含まない」のとおりである。`/exit` での切り替え・会話の記録から回答の無い質問を推し量ること・質問のファイルを手で消すコマンド・マーカーの後の入力での取り消し・手順書の「上限」（#3815）とリレーハブ（#3818）は扱わない。

## 前提

- 作業する場所は worktree `/work/ai-plugins/.worktrees/fix/issue-3814`（ブランチ `fix/issue-3814`）で、Pull Request の宛先はスプリントブランチ `sprint/lrelay-r2` である
- **#3842 の変更がスプリントブランチへ入った後に、`sprint/lrelay-r2` をこのブランチへマージしてから着手する。** #3842 は `wrapper.py` の `_start` の返り値を停止理由か無し（`Optional[Ended]`）へ変え、`_on_switch`・`_on_resume` がそれを返す形にする。この課題の `_on_handover` は同じ形で `_start` の停止理由を返し（詳細設計の「子を止める操作」）、`_start` に起動の ID を足す
- 同じ hook の場所（`guards.json`・`dispatch.py`）を #3870 が触る。競合は後からマージする側が解く（設計書の「文脈と制約」）
- このリポジトリはテストを CI で打つ設定（`.ndf/project.json` の `test.ci.only: true`）である。テストの合否は Pull Request の CI（`gh pr checks <PR>`）で決める。手元で打つのは静的解析の `bash scripts/check-lint.sh`・`python3 scripts/check-script-structure.py`・`python3 scripts/check-ndf-contracts.py` だけにする。要求仕様の「検証の方法」の pytest のコマンドは CI が打つ
- Claude Code の `AskUserQuestion` の hook の振る舞いは、要求仕様の前提 3（2026-11-20 の実測）を前提にする

## 受け入れ条件

番号は要求仕様の「受け入れ条件」の番号で、文はそちらが正である。ここには検証の方法だけを添える。

- [ ] 1. 回答待ちの質問がある間は、マーカーがあっても子を止めず次のセグメントを起動しない — 検証: 偽の子の単体テスト（設計書の「テスト設計」の再現のテスト。CI）
- [ ] 2. 回答待ちの質問がある間は、文脈の上限を超えても子の端末へ書かない — 検証: 単体テスト（CI）
- [ ] 3. 回答待ちの質問がある間は、止まりの秒を超えても起動し直さない — 検証: 単体テスト（CI）
- [ ] 4. 質問が閉じたら hook がロックの中で質問のファイルを消し、消せなければラッパーは子を止めずに待つ — 検証: hook の単体テストと、実際の入力の形を `ndf.domains.hooks.main` へ通すテスト（CI）
- [ ] 5. 1 つの質問の `forward` と `plan_question` の行が 1 つずつ — 検証: 単体テスト（CI）
- [ ] 6. 子が終わったとき・質問が今の子のものでないとき、ラッパーが質問のファイルを消し、後者は `log.jsonl` に 1 行残す — 検証: 単体テスト（CI）
- [ ] 7. 切り替えは強制終了で行い、`/exit` を書かない — 検証: 単体テスト（CI）
- [ ] 8. 手順書 `relay.md` の守りの表と箇条が 11 系の振る舞いだけを書く — 検証: レビューと `python3 scripts/check-markdown-links.py`（文言のテストは書かない）
- [ ] 9. 退行しない（既存の中継のテストが通り、質問が無いときの判断が今と同じ） — 検証: CI の pytest
- [ ] 10. 質問の hook はロックの中で書けたときだけ質問を通し、ラッパーはロックを持ったまま質問が無いと確かめて子を止め終える — 検証: hook の単体テストと、偽の子 2 つの競合のテスト（CI）

## 代替案と採否

設計書の「決定の記録」（決定 1〜5）にある。この計画で新しく決めることは無い。

## 不変条件

設計書の「ドメインモデル」の「不変条件」（I1〜I7）を守る。各タスクに、確かめる不変条件の番号を添える。

## 互換性

設計書の「影響と危険」のとおりである。要点は次の 4 つ。

- `ndf relay decide` の入力に既定値つきの 2 項目（`question_forwarded`・`question_id`）が増え、今の入力の JSON も読める
- guard の表（`guards.json`）に既定で偽の列 `fail_closed` が増え、今の行の振る舞いは変わらない
- 中継の下の `AskUserQuestion` は、ラッパーが子を止めている最中なら拒否される。中継の外は今と同じ
- 子の環境変数 `NDF_RELAY_LAUNCH`、質問のファイルの項目 `launch`、作業ディレクトリのファイル `launch.json` が増える。シグナルの関数 `dir_env` は `launch_env` に置き換えて消す

## 修正対象

詳細設計の「地図」から引き写した。新設のファイルは `ask_clear.py` だけである。

| ファイル | 変える中身 |
| --- | --- |
| `plugins/ndf/src/ndf/domains/relay/switch_decide.py` | 転送した回答待ちの質問で `wait` を返す |
| `plugins/ndf/src/ndf/domains/relay/signals.py` | 質問のファイルの読み・起動の ID・ロックの中で消す |
| `plugins/ndf/src/ndf/domains/relay/wrapper.py` | ポーリングの質問の扱い・ロックの中で子を止める・引継ぎの起動し直し・例外の経路 |
| `plugins/ndf/src/ndf/domains/hooks/dispatch.py` | `fail_closed` の guard を締め切り・例外・拒否の数えから外す |
| `plugins/ndf/src/ndf/domains/hooks/guards/ask.py` | 書けなければ `AskUserQuestion` を拒否する |
| `plugins/ndf/src/ndf/domains/hooks/guards/ask_clear.py`（新設） | 質問が閉じたら質問のファイルを消す |
| `plugins/ndf/contracts/hooks/guards.json` | 列 `fail_closed` と、`ask-answered`・`ask-closed` の行 |
| `plugins/ndf/contracts/hooks/runtimes.json` | PostToolUse の `optional` に `tool_use_id`、Codex の `skipped` に 2 行（地図に無く、詳細設計の「質問を消す hook」の本文が求める） |
| `plugins/ndf/contracts/limits.json` | `relay.child-exit` |
| `plugins/ndf/skills/development-workflow/references/relay.md` | 承認ゲートを越えない守りを 11 系へ書き直す |
| `plugins/ndf/tests/domains/relay/test_relay_wrapper.py`・`test_relay_decide.py`・`test_relay_parts.py` | 中継のテスト |
| `plugins/ndf/tests/domains/hooks/` | `ask-forward`・`ask-answered`・`ask-closed`・`fail_closed` のテスト |

## タスク分解

Task 1〜8 の進め方は「失敗するテストを書く → 通す最小実装 → 整理」で、テストの合否は Pull Request の CI で確かめる。手元では静的解析の 3 つだけを打つ。並びは実装の順で、後のタスクは前のタスクの部品を使う。

### Task 1: 判定が転送した回答待ちの質問で `wait` を返す

- 対象ファイル: `plugins/ndf/src/ndf/domains/relay/switch_decide.py`、`plugins/ndf/tests/domains/relay/test_relay_decide.py`
- 変更内容: 詳細設計の「判定（`switch_decide`）」のとおり。`SectionState` に既定値つきの 2 項目を足し、転送していない質問は質問の ID を入れた理由で `forward`、転送した質問は `wait` を返す。今の入力の JSON が読めることもテストで見る
- 満たす受け入れ条件: 1・2・3・5・9（I1・I2）
- 進め方: 失敗するテストを書く → 通す最小実装 → 整理。合否は CI

### Task 2: 子ごとの起動の ID と今の起動の記録

- 対象ファイル: `plugins/ndf/src/ndf/domains/relay/signals.py`、`plugins/ndf/src/ndf/domains/relay/wrapper.py`（`_start`）、`plugins/ndf/tests/domains/relay/test_relay_parts.py`、`plugins/ndf/tests/domains/relay/test_relay_wrapper.py`
- 変更内容: 詳細設計の「質問のファイルの読みと書き」の `set_launch`・`current_launch`・`launch_env`・`relay_launch` と `write_question`・`write_json` の変更、「子を止める操作」の `_start` の変更。`_start` は起動の ID を作って `launch.json` に記録し、`NDF_RELAY_LAUNCH` で子へ渡し、起動できなければ記録を外す。`dir_env` の呼び出し元を `launch_env` へ替えて `dir_env` を消し、`test_relay_parts.py` の `dir_env` のテストを `launch_env` へ移す
- 満たす受け入れ条件: 6・10（I5・I7 の前提）
- 進め方: 失敗するテストを書く → 通す最小実装 → 整理。合否は CI

### Task 3: 質問のファイルを質問が閉じるまで残し、1 回だけ転送する

- 対象ファイル: `plugins/ndf/src/ndf/domains/relay/signals.py`、`plugins/ndf/src/ndf/domains/relay/wrapper.py`、`plugins/ndf/tests/domains/relay/test_relay_parts.py`、`plugins/ndf/tests/domains/relay/test_relay_wrapper.py`
- 変更内容: 詳細設計の「質問のファイルの読みと書き」の `read_question`・`question_present`・`QuestionUnreadable`・`question_id`、「ロックの中で消す」の `drop_question`、「ポーリングの質問の扱い」の `_question`・`_state`、「質問を転送する操作」の `_on_wait_like`。最初に書くテストは、マーカーを書いた子が質問を出したまま生き続けても `switch` が出ず子が生きていること（要求仕様の「事象」の再現）
- 満たす受け入れ条件: 1・5・6（I2・I3・I5）
- 進め方: 失敗するテストを書く → 通す最小実装 → 整理。合否は CI

### Task 4: 質問が閉じたら hook が質問のファイルを消す

- 対象ファイル: `plugins/ndf/src/ndf/domains/hooks/guards/ask_clear.py`（新設）、`plugins/ndf/contracts/hooks/guards.json`、`plugins/ndf/contracts/hooks/runtimes.json`、`plugins/ndf/tests/domains/hooks/`
- 変更内容: 詳細設計の「質問を消す hook」のとおり。`ask-answered`（PostToolUse）と `ask-closed`（Stop・UserPromptSubmit）を guard の表へ足す。`runtimes.json` は地図に無いが、PostToolUse の入力の `tool_use_id` を読むために `optional` へ足し、Codex の `skipped` に 2 行を足す（詳細設計の同じ節の箇条が求める）。テストは 2026-11-20 の実測の入力の形を `ndf.domains.hooks.main` へ通す
- 満たす受け入れ条件: 4（I5）
- 進め方: 失敗するテストを書く → 通す最小実装 → 整理。合否は CI

### Task 5: 質問の hook が書けなければ質問を拒否し、失敗したら止める側へ倒す

- 対象ファイル: `plugins/ndf/src/ndf/domains/hooks/guards/ask.py`、`plugins/ndf/src/ndf/domains/hooks/dispatch.py`、`plugins/ndf/contracts/hooks/guards.json`、`plugins/ndf/tests/domains/hooks/`
- 変更内容: 詳細設計の「質問の hook」と「失敗したら止める側へ倒す guard」のとおり。`GuardSpec` に列 `fail_closed` を足し、`run`・`_call` を変える。`ask-forward` の行だけを真にする。`ask.check` は中継の下で書けなければ `QUESTION_NOT_RECORDED` で拒否する。guard の表を読むスキーマは見当たらない（`grep -rln "guards.json\|fail_closed" plugins/ndf/contracts plugins/ndf/src scripts` の当たりは `hooks/` の Python 5 本だけ、2026-11-20）ので、表の `summary` だけを直す
- 満たす受け入れ条件: 10（I7）
- 進め方: 失敗するテストを書く → 通す最小実装 → 整理。合否は CI

### Task 6: ロックの中で質問が無いと確かめてから子を止める

- 対象ファイル: `plugins/ndf/src/ndf/domains/relay/wrapper.py`、`plugins/ndf/contracts/limits.json`、`plugins/ndf/tests/domains/relay/test_relay_wrapper.py`
- 変更内容: 詳細設計の「ロックの中で子を止める」と「子を止める操作」の `_on_switch`・`_on_resume`。`_stop_if_clear` を新設し、`_stop_child` は `terminate(force=True)` の戻り値を見て `relay.child-exit` の秒まで子の終わりを待つ。`_on_switch` はアカウントを止める前に選び、記録の行とマーカーの削除は止めた後にする。止まらない子では `refused` で終わる。テストは `_state` の直後に質問を書く競合と、偽の子 2 つで止めた子の hook を再開する競合（設計書の「テスト設計」の受け入れ条件 10 の行）
- 満たす受け入れ条件: 1・3・7・10（I1・I4）
- 進め方: 失敗するテストを書く → 通す最小実装 → 整理。合否は CI

### Task 7: 引継ぎを子の端末へ書かず、同じ会話の起動し直しで頼む

- 対象ファイル: `plugins/ndf/src/ndf/domains/relay/wrapper.py`、`plugins/ndf/tests/domains/relay/test_relay_wrapper.py`
- 変更内容: 詳細設計の「子を止める操作」の `_on_handover` と `_send` の削除。Task 6 の `_stop_if_clear` で子を止め、`--resume <同じ ID> <引継ぎの 1 行>` で起動し直し、`handover_asked` を立てる。`_start` の停止理由は #3842 と同じ形で返す。テストは、利用者の入力を渡すとき（`_pump`）のほかに `child.write` が呼ばれないことを見る
- 満たす受け入れ条件: 2・10（I6）
- 進め方: 失敗するテストを書く → 通す最小実装 → 整理。合否は CI

### Task 8: 質問の状態を読めないときと例外の経路でも子を止めない

- 対象ファイル: `plugins/ndf/src/ndf/domains/relay/wrapper.py`、`plugins/ndf/tests/domains/relay/test_relay_wrapper.py`
- 変更内容: 詳細設計の「例外の経路」のとおり。`_loop` の例外の経路は `_stop_child` を直接呼ばず `_halt_broken` を返し、`_stop_if_clear` が通るか子が終わるまで待つ。壊れた質問のファイルが回答待ちとして扱われること（Task 3 の `_question`）を、不正な JSON・不正な UTF-8・読む権限の無いファイルで確かめる
- 満たす受け入れ条件: 1・2・3（決定 5、I4）
- 進め方: 失敗するテストを書く → 通す最小実装 → 整理。合否は CI

### Task 9: 手順書の守りを 11 系の振る舞いで書き直す

- 対象ファイル: `plugins/ndf/skills/development-workflow/references/relay.md`
- 変更内容: 詳細設計の「手順書」のとおり。「承認ゲートを越えない守り」を、判定の順と hook の振る舞い（`ask-forward`・`ask-answered`・`ask-closed`・`relay-marker`・`stop-wait`）で書き直し、10 系の語（`question.lock`・`asked`・`wait-held.json`・`/exit` の書き込み・`prose_wait` の行・`ndf-gate` の自動の付与）を消す。#3842 が足した「落ちたときの続け方」の行は残す
- 満たす受け入れ条件: 8
- 進め方: 文書の変更なので、`python3 scripts/check-markdown-links.py` でリンクを確かめ、書いた振る舞いが Task 1〜8 のコードと一致するかをレビューで見る

## 影響範囲

- 中継のラッパーのすべての子を止める操作（`switch`・`account`・`restart`・`handover`・設定が壊れたときの終わり）と、質問の転送
- 中継の下で動く hook の PreToolUse（`AskUserQuestion`）・PostToolUse・Stop・UserPromptSubmit。Stop と UserPromptSubmit は質問のファイルがあればロックを 1 回取る（設計書の「影響と危険」）。hook の登録（`plugins/ndf/hooks/claude.json`）は全事象を matcher なしで登録しているので変えない（2026-11-20 に中身を確かめた）
- hook の枠組み（`dispatch.run`）。`fail_closed` が偽の guard の振る舞いは変えない
- `ndf relay decide` の入力
- 手順書 `relay.md`。#3842 も同じファイルの別の節を変える

## リスクと対処

| リスク | 対処 |
| --- | --- |
| #3842 の `_start` の変更と衝突する | 先に構造を整える（着手の前に `sprint/lrelay-r2` をマージして #3842 の形を取り込む） |
| ロックの順と起動の ID の照らしで、競合のテストが偶然に通る | タスクごとにテストを通す（Task 6 で偽の子 2 つとスレッドで順を固定した競合のテストを書く） |
| `fail_closed` の変更が、ほかの guard の例外・締め切り・案内の振る舞いを変える | タスクごとにテストを通す（Task 5 で偽の guard が今と同じことを確かめる） |
| 引継ぎを起動し直しへ変えて、起動し直しの回数や `handover_asked` の扱いがずれる | タスクごとにテストを通す（Task 7） |
| `wrapper.py` の変更が多く、関数が長くなる | 実装の後のリファクタリングで足りる |
| #3870 と `guards.json`・`dispatch.py` で衝突する | 実装の後のリファクタリングで足りる（後からマージする側が解く） |

## 切り戻し手順

1. スプリントブランチ `sprint/lrelay-r2` でこの Pull Request のマージコミットを `git revert -m 1 <マージコミット>` で戻す Pull Request を出す。#3842 の変更は残る
2. 動いているラッパーが作った `launch.json` と、`launch` の項目を持つ質問のファイルが作業ディレクトリに残りうる。戻した後のラッパーは `launch.json` を読まない。`launch` の項目を持つ質問のファイルを戻した後のコードが読めるかは未確認なので、戻した直後に作業ディレクトリの `launch.json` と質問のファイルが残っていれば消す
3. 動いているラッパーは、利用者が `claude` を打ち直した後に戻した版で動く

## 完了の定義

- 受け入れ条件 1〜10 のチェックがすべて付いている
- Pull Request の CI がすべて通っている（`gh pr checks <PR>`）
- 手元の静的解析（`bash scripts/check-lint.sh`・`python3 scripts/check-script-structure.py`・`python3 scripts/check-ndf-contracts.py`）が通っている
- 設計書の「未確認のまま残ること」の実際の claude での確かめを、リリース後テストの項目として Pull Request に書いている
- スプリントブランチ `sprint/lrelay-r2` へマージされている
