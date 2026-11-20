# #3846 #3847: 中継の手順書と確定仕様を、11 系のラッパーが実際に書く記録と振る舞いだけにする

## 関連リンク

- 課題: https://github.com/devbasex/ai-plugins/issues/3846 ・ https://github.com/devbasex/ai-plugins/issues/3847
- 要求仕様: [issue-3846-requirements.md](./issue-3846-requirements.md)・[issue-3847-requirements.md](./issue-3847-requirements.md)
- 設計書: [issue-3846-3847-design.md](./issue-3846-3847-design.md)
- 起動の失敗の振る舞いを決める課題: [issue-3842-design.md](./issue-3842-design.md)（#3842）

## モード

standard（pace: normal）

## 目的と非目的

目的は、手順書 `relay-accounts.md` と中継の確定仕様 7 本が、11 系のラッパーが書く記録と振る舞いだけを書き、11 系の正（手順書 `relay.md` と確定仕様 `ndf-relay-plugin-update.md`）を指すことである（要求仕様 2 本の「期待する結果」）。

非目的は、要求仕様 2 本の「対象範囲」の「含まない」のとおりである。ラッパーのコードの変更・`relay-accounts.md` と確定仕様のほかの 10 系の記述（#3864・#3865）・`docs/archive/` の文書・用語集の生成物の手での編集は扱わない。手順書 `relay.md` は #3842・#3814 が直すので、この変更では触らない。

## 前提

- 作業する場所は worktree `/work/ai-plugins/.worktrees/docs/issue-3846-3847`（ブランチ `docs/issue-3846-3847`）で、Pull Request の宛先はスプリントブランチ `sprint/lrelay-r2` である
- 用語集（`docs/glossary/glossary.json` と生成物）は設計 Pull Request で直してある（設計書の決定 2）。2026-11-20 にこの worktree で `ndf records glossary render --check` と `ndf records glossary meanings` を打ち、どちらも `ok` だった。`glossary.json` を `relay_reexec\|relay_version_dir\|relay_handover` で `grep -c` した結果は 0 件だった
- `ndf-relay-segment-restart.md` に書く起動の失敗の振る舞い（停止理由 `precondition`・終了コード 3）は #3842 が決める。#3842 が設計どおりに入らなかったときは、この文書の記述を合わせ直す
- `relay.md` の「記録の読み方」と「プラグインの更新とラッパーのバージョン」の見出しへリンクする。#3814・#3842 がその見出しを変えるとリンクが切れるので、宛先のスプリントブランチを取り込んだ後にリンクの検査を打ち直す
- 文書だけの変更なのでテスト駆動を当てず、テストのコードも足さない（設計書の「テスト設計」）。`.md` の文言を照らすテストは書かない

## 受け入れ条件

番号は要求仕様の「受け入れ条件」の番号で、文はそちらが正である。ここには検証の方法だけを添える。

#3846:

- [ ] 1. `relay-accounts.md` に 10 系のキーが残らない — 検証: 要求仕様の受け入れ条件 1 の `grep` が 0 件
- [ ] 2. 記録の行として `account` の行（`at`・`section`・`kind`・`reason`）だけを書き、`reason` を規則の名前として書かない — 検証: レビューで `wrapper.py` の `_account_for_next`・`_note` と照らす。`api_key` の行と `rule` のキーは `grep` で 0 件を条件にできないので、ここで見る（設計書の「テスト設計」）
- [ ] 3. 画面の `ndf-relay:` の 1 行にアカウントの切り替えの文が無い — 検証: レビュー
- [ ] 4. 記録の読み方を書く箇所が `relay.md` の「記録の読み方」を指す — 検証: レビューと `python3 scripts/check-markdown-links.py`
- [ ] 5. 退行しない — 検証: `python3 scripts/check-markdown-links.py` が通る

#3847:

- [ ] 1. 確定仕様に 10 系の入れ替えの語が残らない — 検証: 要求仕様の受け入れ条件 1 の `grep` が 0 件（2026-11-20 の当たりは 6 ファイル 7 件）
- [ ] 2. `ndf-relay-segment-restart.md` が、更新に失敗しても次のセグメントを起動すること、起動の失敗は #3842 の振る舞いであることを書く — 検証: レビューで `relay.md` の「プラグインの更新とラッパーのバージョン」と #3842 の設計書と照らす
- [ ] 3. 用語集の 3 語が無く、2 語の意味が 11 系になっている — 検証: 設計 Pull Request で済み。この変更では Task 4 で状態を確かめるだけ
- [ ] 4. 確定仕様の「入れ替え」がプラグインの入れ替えの意味だけになる — 検証: 要求仕様の受け入れ条件 4 の `grep` の当たりを 1 件ずつ見る（2026-11-20 の当たりは 11 件）
- [ ] 5. `ndf records glossary render --check` と `ndf records glossary meanings` が `ok` — 検証: 2 つのコマンドを 1 つずつ打つ
- [ ] 6. 退行しない — 検証: `python3 scripts/check-markdown-links.py` が通る

## 代替案と採否

設計書の「決定の記録」（決定 1・決定 2）にある。この計画で新しく決めることは無い。

## 互換性

公開インタフェース・ラッパーの振る舞い・データの形は変わらない（要求仕様 2 本の「影響」）。読み手は、#3864・#3865 が閉じるまで、直した箇所と 10 系の記述が残った箇所が並ぶ文書を読む（設計書の決定 1 の代償）。

## 修正対象

詳細設計の文書は無いので、設計書の「どこを変えるか」と「設計の結果」の触るファイルから決めた。

| ファイル | 課題 |
| --- | --- |
| `plugins/ndf/skills/development-workflow/references/relay-accounts.md` | #3846 |
| `docs/specifications/ndf-relay-segment-restart.md` | #3847 |
| `docs/specifications/ndf-development-loop-limits.md` | #3847 |
| `docs/specifications/ndf-relay-multi-session-supervisor.md` | #3847 |
| `docs/specifications/ndf-relay-lane-parent-session-env.md` | #3847 |
| `docs/specifications/ndf-relay-auth-failure.md` | #3847 |
| `docs/specifications/ndf-relay-install-and-restart.md` | #3847 |
| `docs/specifications/ndf-relay-launch-target.md` | #3847 |

## タスク分解

どのタスクも文書だけの変更で、進め方は「リンクと手順の実行可能性を確認する」である。直した文が指すリンクを `python3 scripts/check-markdown-links.py` で確かめ、文書に残したコマンドは書く前に打って結果を確かめる。

### Task 1: 手順書 `relay-accounts.md` の記録の読み方を `account` の行だけにする

- 対象ファイル: `plugins/ndf/skills/development-workflow/references/relay-accounts.md`
- 変更内容: 設計書の「どこを変えるか」の 1 行目のとおり。要求仕様 #3846 の「事象」の表の行にある 10 系の行・キーと、画面の切り替えの 1 行を文ごと消す。記録を書く箇所は `account` の行を、共通のキー（`at`・`section`）と固有のキー（`kind`・`reason`）に分けて書き、行の種類とキーの一覧として `relay.md` の「記録の読み方」を指す。ほかの 10 系の記述は #3864 へ残す
- 満たす受け入れ条件: #3846 の 1〜5
- 進め方: リンクと手順の実行可能性を確認する。#3846 の受け入れ条件 1 の `grep` を打ち、書いた `jq` などの記録の引き方があれば、11 系の `log.jsonl` の行の形で結果が返ることを確かめる

### Task 2: `ndf-relay-segment-restart.md` の入れ替えと更新・起動の失敗を 11 系にする

- 対象ファイル: `docs/specifications/ndf-relay-segment-restart.md`
- 変更内容: 設計書の「どこを変えるか」の 2 行目のとおり。ラッパーの入れ替え・`update-failed`・`start-failed` の記述を消し、プラグインの更新に失敗しても次のセグメントを起動すると書いて `relay.md` と `ndf-relay-plugin-update.md` を指す。起動の失敗は #3842 の振る舞い（停止理由 `precondition`・終了コード 3）を書く。置き場 `relay_lib`・再起動ループ・記録の形・導入の形は #3865 へ残す
- 満たす受け入れ条件: #3847 の 1・2・6
- 進め方: リンクと手順の実行可能性を確認する。#3847 の受け入れ条件 1 の `grep` でこのファイルの当たりが消えたことを見る

### Task 3: ほかの確定仕様 6 本の入れ替えの記述を 11 系にする

- 対象ファイル: `docs/specifications/ndf-development-loop-limits.md`、`docs/specifications/ndf-relay-multi-session-supervisor.md`、`docs/specifications/ndf-relay-lane-parent-session-env.md`、`docs/specifications/ndf-relay-auth-failure.md`、`docs/specifications/ndf-relay-install-and-restart.md`、`docs/specifications/ndf-relay-launch-target.md`
- 変更内容: 設計書の「どこを変えるか」の 3〜8 行目のとおり、ファイルごとに決めた記述を消すか 11 系の振る舞いへ書き直す。`ndf-development-loop-limits.md` の `relay_lib` を指すほかの行は #3865 へ残す
- 満たす受け入れ条件: #3847 の 1・4・6
- 進め方: リンクと手順の実行可能性を確認する。#3847 の受け入れ条件 1 の `grep` が 0 件、受け入れ条件 4 の `grep` の当たりがプラグインの入れ替えの意味だけであることを見る

### Task 4: 用語集の状態を確かめる

- 対象ファイル: 無し（`docs/glossary/glossary.json` と生成物は変えない）
- 変更内容: 設計 Pull Request で直した用語集が、宛先のスプリントブランチを取り込んだ後も直したままであることを確かめる。食い違えば `glossary.json` を直して `ndf records glossary render` で生成物を作り直す（生成物を手で直さない）
- 満たす受け入れ条件: #3847 の 3・5
- 進め方: リンクと手順の実行可能性を確認する。`ndf records glossary render --check` と `ndf records glossary meanings` を 1 つずつ打つ

## 影響範囲

- 変えるのは手順書 1 本と確定仕様 7 本だけで、ラッパーの振る舞いと公開インタフェースは変わらない（設計書の「影響と危険」）
- `ndf-relay-segment-restart.md` は確定仕様と手順書の 15 か所から参照されている。見出しを変えると参照が切れるので、見出しは変えずに中身だけを直す
- `relay.md` の節へのリンクは、#3814・#3842 が `relay.md` を変えた後も切れないかを、宛先を取り込んだ後のリンクの検査で見る

## リスクと対処

| リスク | 対処 |
| --- | --- |
| #3814・#3842 が `relay.md` の見出しを変えて、この変更のリンクが切れる | タスクごとにテストを通す（タスクごとと、宛先を取り込んだ後にリンクの検査を打つ） |
| 10 系の記述を消しすぎて、#3864・#3865 が持つ記述まで消す | タスクごとにテストを通す（タスクごとに、設計書の「どこを変えるか」の残す記述と差分を照らす） |
| `ndf-relay-segment-restart.md` の起動の失敗の記述が、実装された #3842 と食い違う | 実装の後のリファクタリングで足りる（#3842 の Pull Request の振る舞いと照らし直す） |

## 切り戻し手順

1. スプリントブランチ `sprint/lrelay-r2` でこの Pull Request のマージコミットを `git revert -m 1 <マージコミット>` で戻す Pull Request を出す
2. 文書だけの変更なので、データとラッパーの後始末は要らない。用語集は設計 Pull Request の側にあるので、この切り戻しでは変わらない

## 完了の定義

- #3846 の受け入れ条件 1〜5 と #3847 の受け入れ条件 1〜6 のチェックがすべて付いている
- Pull Request の CI がすべて通っている（`gh pr checks <PR>`）
- 手元で `python3 scripts/check-markdown-links.py`・`ndf records glossary render --check`・`ndf records glossary meanings` が通っている
- 実装の Pull Request のレビューで、直した文を `wrapper.py` の `_account_for_next`・`_note`、`relay.md` の「プラグインの更新とラッパーのバージョン」、#3842 の起動の失敗の振る舞いと照らした結果を書いている
- スプリントブランチ `sprint/lrelay-r2` へマージされている
