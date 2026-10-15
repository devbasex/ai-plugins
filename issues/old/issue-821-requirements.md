# #821: Slack 通知を回答・承認待ちのときだけ送り、セッションと issue / PR の URL を載せる

正は課題の本文（#821）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 背景

Slack 通知は、Claude Code の `Stop` フックが `scripts/slack-notify.js session_end` を実行して送っている（`plugins/ndf/hooks/claude.json`）。

困っていることは 2 つある。

1. **応答が終わるたびに通知が来る。** 利用者が手を動かす必要のない通知が大半を占め、本当に待たれている通知が埋もれる
2. **通知からセッションへ戻れない。** 本文は `[リポジトリ名] 要約` だけで、どのセッションが何を待っているのか、どこを見ればよいのかが分からない

## 要望

- **通知するのは、利用者に回答か承認を求めるときだけにする。** 応答の終わりごとの通知は廃止する
- **通知にセッションの URL を載せる**
  - 回答を求める場合は、関連する issue / Redmine の URL も載せる
  - 承認を求める場合は、Pull Request の URL も載せる

## 調査結果

### 1. 現状の `Stop` は「セッション終了」ではなく「応答の終わりごと」に動く

- `Stop` は Claude が応答を終えるたびに発火する。セッションの終了は `SessionEnd` が別にある（公式 hooks ドキュメント）
- 引数 `session_end` と `description: "Send Slack notification when Claude Code exits"` は実際の発火と食い違っている。`README.md` の「Claude Code の Stop hook は終了時に Slack 通知スクリプトを実行します」も同じく食い違う
- 重複の抑止は **cwd 単位のロック**と 5 秒のクールダウンだけ（`getLockFilePath()`）。応答ごとの発火そのものは止めていない
- 通知のたびに `claude --model haiku -p` を起動して 40 文字の要約を作っている（最大 60 秒）

### 2. 「回答・承認を求めている」ことを捉えられるフック

| 待ちの種類 | 捉えるフック | matcher / 判定 | 備考 |
| --- | --- | --- | --- |
| ツール実行の権限確認 | `Notification` | `permission_prompt` | 約 6 秒応答がないと発火 |
| 選択式の質問 | `PreToolUse` | `AskUserQuestion` | 専用の Notification 種別は公式ドキュメントに見当たらない |
| 計画の承認 | `PermissionRequest` | `ExitPlanMode` | |
| MCP の入力フォーム | `Notification` | `elicitation_dialog` / `elicitation_url_dialog` | |
| **文章で確認を求めて応答を終える** | `Stop` | `last_assistant_message` の中身で判定 | **NDF の承認の関門はほぼこの形**（`/ndf:pr` のブランチ・ファイル提示、リリース承認など） |
| 放置 | `Notification` | `idle_prompt` | 応答終了から約 60 秒入力がないと発火。**これだけでは回答待ちか完了かを区別できない** |

最後の「文章で確認を求める」形を捉えないと、NDF で一番多い承認待ちが通知されない。`Stop` の入力には最終応答の全文 `last_assistant_message` が入るので、これを見て「利用者の回答・承認を待って終わったか」を判定する必要がある。判定方法（末尾の疑問形・定型句の照合か、haiku による分類か）は設計で決める。

### 3. セッション URL の取得元

Claude Code 2.1.280 の本体を調べた結果（`~/.local/share/claude/versions/2.1.280` を grep）:

| セッションの種類 | 取れる値 | URL |
| --- | --- | --- |
| Remote Control 中のローカル CLI | 環境変数 `CLAUDE_CODE_BRIDGE_SESSION_ID`（`session_...` 形。接続時に `process.env` へ設定される） | `https://claude.ai/code/<id>` |
| クラウド（claude.ai/code）のセッション | 環境変数 `CLAUDE_CODE_REMOTE_SESSION_ID` | 同上 |
| Remote Control なしのローカル CLI | `session_id`（フック入力 / `CLAUDE_CODE_SESSION_ID`） | **URL は無い。** 代わりに `claude --resume <session_id>` とホスト名・cwd を載せる |

- 本体がコミット・PR に付ける `Claude-Session:` のリンクも、上の 2 つの ID から `…/code/<id>` を組み立てている（`attribution.sessionUrl`）
- `cse_` 形と `session_` 形の ID 変換が本体にあるため、URL に使う形は実装時に実機で確かめる
- 公式ドキュメント由来として「フック入力に `session_url`（`https://claude.ai/s/<id>`）が入る」という情報もあったが、2.1.280 の本体に `claude.ai/s/` は見つからなかった。**実機のフック入力で確かめるまで使わない**

### 4. issue / Redmine / PR の URL の取得元

- **最終応答の本文から抜く。** 承認を求める応答には PR の URL を必ず添える運用になっている。issue も `#番号` か URL で本文に出る
- **PR は `gh pr view --json url` で補える。** 本文に無くても、cwd のブランチに PR があれば取れる
- **Redmine は URL の形がリポジトリで決まらない。** ホストを環境変数（例 `REDMINE_URL`）で受けて照合するか、本文中の URL をそのまま載せるかを設計で決める。課題追跡の抽象化（マイルストーン 08）の設定があればそれを読む

### 5. 他ランタイム

- Codex: `hooks/codex.json` の `Stop` → `scripts/codex-slack-notify.js`（`NDF_CODEX_SLACK_NOTIFY=true` のときだけ）。同じく応答の終わりごとに動き、`session: <id>` と `cwd` は載せている
- Kiro: `dev.kiro/install.sh --with-slack` が `hooks.stop` に `slack-notify.js session_end` を入れる
- 方針（待ちのときだけ通知・URL を載せる）は 3 ランタイムで揃える。ただし捉えられるフックはランタイムごとに違うため、Claude Code を先に直し、Codex / Kiro は取れる範囲を実測してから決める

## 受け入れ条件

- [ ] 利用者の入力を待たずに応答が終わった場合（作業完了の報告など）、Slack へ通知しない
- [ ] 権限確認・`AskUserQuestion`・`ExitPlanMode` の承認待ちで通知する
- [ ] 文章で回答・承認を求めて応答を終えた場合に通知する（判定の誤検知・見逃しを実例で確かめる）
- [ ] 通知に「回答待ち」か「承認待ち」かが分かる印を付ける
- [ ] Remote Control 中・クラウドのセッションでは、通知に `https://claude.ai/code/<id>` が載り、開くと当該セッションへ行ける
- [ ] Remote Control なしのローカル CLI では、`claude --resume <session_id>` とホスト名・cwd が載る
- [ ] 回答待ちの通知には、応答に出た issue / Redmine の URL が載る
- [ ] 承認待ちの通知には、PR の URL が載る（本文に無ければ現在のブランチの PR）
- [ ] 同じ待ちに対して二重に通知しない（例: `AskUserQuestion` と `idle_prompt` の両方で送らない）
- [ ] `hooks/claude.json` の description・引数、`README.md` の Slack 通知の説明を実際の発火に合わせる
- [ ] Codex / Kiro の扱い（揃える・対象外にする）を決め、決めた内容を README に書く

## 未確定の点

- 文章での回答・承認待ちの判定方法（規則か、モデル分類か）。モデル分類なら現行の要約生成（haiku）と 1 回の呼び出しにまとめられるか
- 「作業完了」も通知したい利用者向けに、旧来の動作を環境変数で残すか
- フック入力に `session_url` が実際に入るか（上記 3）

## 関連

- `plugins/ndf/hooks/claude.json`
- `plugins/ndf/scripts/slack-notify.js`
- `plugins/ndf/scripts/codex-slack-notify.js`
- `plugins/ndf/hooks/codex.json`
- `plugins/ndf/dev.kiro/install.sh`
- `plugins/ndf/README.md`「Slack 通知」
