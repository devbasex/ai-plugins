# Issue 113 / Task 3: CLI 非対話実行の検証記録

## 関連リンク

- 実施計画: [issue-113-cross-refactoring-loop.md](issue-113-cross-refactoring-loop.md)
- GitHub Issue: https://github.com/devbasex/ai-plugins/issues/113
- 既存の Kiro 実機検証: [ndf-development-skills/03-runtime-conformance.md](ndf-development-skills/03-runtime-conformance.md)

## 検証環境

検証は 2 つの環境に分かれる。§1・§2・§6 は環境 A、§3 は環境 B での実測である。

| 項目 | 環境 A（claude / worktree / frontmatter） | 環境 B（Kiro 実機確認） |
| --- | --- | --- |
| 実施日 | 2026-08-15 | 2026-08-15 |
| 実行環境 | Claude Code on the web のリモートコンテナ（**root 実行**） | ローカルコンテナ（**非 root**: uid 1000 / `sudo` 可） |
| git | 2.43.0 | 2.43.0 |
| claude | 2.1.233 | 2.1.233 |
| kiro-cli | **未インストール** | **2.18.0**（IAM Identity Center でログイン済み） |
| codex / gemini | 未インストール | インストール済み（本検証では未使用） |

codex / gemini は cross-review で運用実績があるため検証対象外とした。
**Kiro は環境 B で §4 のチェックリスト 6 項目をすべて実測し、未解決項目は残っていない。**

## 1. git worktree の同一ブランチ制約（実測・確定）

設計の前提「読み取り用 worktree は `--detach` が必須」を実測で確認した。

```console
$ git worktree add ../wt1 feature/x
Preparing worktree (checking out 'feature/x')
HEAD is now at 90cbe87 second

$ git worktree add ../wt2 feature/x
Preparing worktree (checking out 'feature/x')
fatal: 'feature/x' is already used by worktree at '.../wt1'
```

**結論**: 同一ブランチを 2 つ目の worktree へ checkout することはできない。
`work/` だけがブランチを持ち、参加ランタイム用は `--detach <sha>` で作る設計は正しい。

## 2. Claude CLI（`claude -p`）— 実測・確定

参加者としての claude をヘッドレス起動する手順を確定した。

### 2-1. プロンプトは stdin から渡せる

```bash
cat prompt.md | claude -p --permission-mode acceptEdits --allowed-tools "Bash,Write" \
    --output-format json > out.json
```

`-p` / `--print` は stdin をプロンプトとして受け取る。長いプロンプトを argv に載せる必要はない
（gemini で問題になった「long prompt が argv に乗る」事象を回避できる）。

`--help` の記載により、**非対話モードでは workspace trust ダイアログが skip される**
（`-p` 指定時、または stdout が TTY でないとき）。gemini の `--skip-trust` に相当する対応は不要。

### 2-2. `bypassPermissions` は root では使えない（重要）

```console
$ cat prompt.md | claude -p --permission-mode bypassPermissions --output-format json
--dangerously-skip-permissions cannot be used with root/sudo privileges for security reasons
(exit 1)
```

`--permission-mode bypassPermissions` は `--dangerously-skip-permissions` と同じ扱いで、
**root 実行時に拒否される**。CI やコンテナは root 実行が多いため、launcher の既定にはできない。

`--permission-mode` の選択肢は `acceptEdits` / `auto` / `bypassPermissions` / `manual` /
`dontAsk` / `plan`。

### 2-3. 採用する起動形式

```bash
cat prompt.md | claude -p \
    --permission-mode acceptEdits \
    --allowed-tools "Bash,Write" \
    --output-format json
```

root 環境で **編集とシェル実行が承認待ちなしに通り、exit 0** となることを確認した。
検証プロンプト（`git rev-parse --abbrev-ref HEAD` を実行して結果を `result.json` に書く）に対し、
実際に `result.json` が `{"ok": true, "branch": "feature/x"}` で生成された。

> 参考: `--allowed-tools` を付けずに `--permission-mode acceptEdits` だけでも同じ結果になったが、
> これは検証コンテナの settings に依存する可能性がある。**launcher では `--allowed-tools` を
> 明示する**（環境差で承認待ちハングに落ちるのを防ぐため）。

### 2-4. 完了検知は `--output-format json` で足りる

`--output-format json` の戻り値には次のキーが含まれる。

```
type, subtype, is_error, result, permission_denials, session_id,
num_turns, duration_ms, total_cost_usd, usage, modelUsage, stop_reason, ...
```

**完了検知は「プロセス終了 + `is_error` / `subtype` の判定」で確定できる**。
codex のような sentinel ファイルによる補助は不要。さらに `permission_denials` が空配列で
ないときは**ツール承認の失敗**なので、`monitor.py` の早期エラー扱いにできる。

### 2-5. 実行コストの実測（運用上の注意）

上記の**ごく単純な 3 ターンのタスクで `total_cost_usd` = 0.259**。
cross-refactoring は 1 提案ラウンドあたり最低でも「提案 3 + 適用 1 + レビュー 2」の 6 回、
指摘修正が入るとさらに増える。claude を参加させる構成（ホストが Codex / Kiro）では
コストが無視できないため、SKILL.md の引数表に**上限値の既定を保守的に置く**方針は維持する。

## 3. Kiro CLI（`kiro-cli chat`）— 実測・確定

kiro-cli 2.18.0 で §4 のチェックリストをすべて実測した。**公開 Issue（#7467 / #7483）を
根拠にしていた前提のうち 2 つが 2.18.0 では成り立たない**ため、設計を再度変更している。

### 3-0. 採用する起動形式（結論）

```bash
cat prompt.md | kiro-cli chat --no-interactive --trust-all-tools > out.txt 2> err.txt
```

- プロンプトは **stdin から渡せる**（26KB で実測）。argv に載せる必要はない
- **終了コードは完了判定に使えない**。判定は `err.txt` のパターン検査で行う（3-5）
- 出力には ANSI エスケープが必ず混ざるため、パース前に除去する（3-7）

**`--trust-tools` での絞り込みは採用しない。** 絞り込み自体は実効性があるが（3-2）、
`execute_bash` を許可する時点で他ツールの制限はシェル経由で迂回できるため、
**防御力を持たないまま綴り違いの事故リスクだけが残る**。実効的な防御は worktree 隔離に
一本化し、承認は codex の `--dangerously-bypass-approvals-and-sandbox` / gemini の
`--skip-trust` と同じ整理で全許可とする。

### 3-1. `--no-interactive` と trust フラグは併用できる（チェック項目 2）

`--no-interactive` と `--trust-all-tools` / `--trust-tools` は同一 `chat` サブコマンドの
オプションとして共存し、**併用してツール実行が通る**。

```console
$ kiro-cli chat --no-interactive --trust-all-tools 'git rev-parse --abbrev-ref HEAD を実行して'
I will run the following command: git rev-parse --abbrev-ref HEAD (using tool: shell)
master
 - Completed in 0.4s
(exit 0)
```

**Kiro を実装フェーズの輪番から外す縮退案（引き継ぎメモ §7-1）は不要**になった。
`impl_capable` に kiro を含めてよい。ファイル編集も実測で通っている（3-8）。

### 3-2. `--trust-tools` の絞り込みは実効性がある（チェック項目 1）

#7467 が報告する「設定が honored されず毎回 `Allow this action?` が出る」現象は、
**2.18.0 のフラグ経路では再現しなかった**。許可したツールは通り、許可しなかったツールは
拒否される。

| 渡した値 | シェル実行 | ファイル書き込み |
| --- | --- | --- |
| `--trust-all-tools` | 通る | 通る |
| `--trust-tools=execute_bash` | 通る | **拒否**（`fs_write` が denied） |
| `--trust-tools=fs_read` | **拒否**（`execute_bash` が denied） | — |
| `--trust-tools=fs_read,fs_write` | — | 通る |
| `--trust-tools=`（空） | 拒否 | 拒否 |
| フラグなし | 拒否 | 拒否 |

**ツール名は内部名・表示名のどちらでも受け付ける。** シェルは内部名 `execute_bash` /
表示名 `shell`、書き込みは内部名 `fs_write` / 表示名 `write` で、`--trust-tools=shell` /
`--trust-tools=write` でも同じく通った。拒否メッセージと `--help` の例は内部名で書かれて
いるため、`--trust-tools` を使う場合は内部名で統一するのがよい。
**ただし launcher は `--trust-all-tools` を採用するため、この綴りに依存しない**（3-0）。

> ⚠ **絞り込みにセキュリティ上の意味はほとんど無い。** `--trust-tools=execute_bash` で
> `fs_write` を拒否したケースでは、モデルが拒否を受けて
> `echo '{"q": 1}' > q.json` をシェル経由で実行し、**結局ファイルを作成した**。
> シェルを許可する以上、他ツールの制限は迂回される。実効的な防御は worktree 隔離だけである。
> **これが `--trust-all-tools` を採用した理由**（3-0）。

> ⚠ **無効なツール名は警告のみで、実行は続行される。**
> `--trust-tools=bogus_tool_xyz` は stderr に
> `WARNING: --trust-tools arg for custom tool bogus_tool_xyz needs to be prepended with @{MCPSERVERNAME}/`
> を出すだけで exit 0。**綴りを間違えると「黙って何も信頼しない」状態で走る**。
> `--trust-all-tools` ならこの事故は起きないが、将来 `--trust-tools` へ戻す場合は
> この WARNING を早期エラーとして扱う必要がある。

なお `--trust-all-tools` と `--trust-tools` を同時に指定すると **`--trust-all-tools` が優先**
される（`All tools are now trusted (!)` が表示され、絞り込みは無視される）。

### 3-3. 承認漏れは「ハング」ではなく「拒否 + exit 0」で現れる（チェック項目 4）

#7483 が報告する「承認待ちで無限にハングする」現象も **2.18.0 では再現しなかった**。
trust フラグ無しでシェル実行を要求しても **9 秒で終了し exit 0** を返す。

```console
$ kiro-cli chat --no-interactive 'execute_bash で pwd を実行して'   # trust フラグなし
(stderr)
Command execute_bash is rejected because it matches one or more rules on the denied list:
  - non-interactive mode (no user to approve)
(stdout)
> execute_bash の実行がブロックされました。非インタラクティブモードで動作しているため…
(exit 0, elapsed 9s)
```

**検知は stderr のパターン照合で行う。** 早期エラーパターンに追加する文字列は
`Allow this action?` ではなく次の 2 つ:

- `is rejected because it matches one or more rules on the denied list`
- `WARNING: --trust-tools arg for custom tool`

stall timeout は「MCP サーバ起動待ちなど別要因のハング」への保険として残すが、
**承認漏れの検知手段としては不要**になった。

### 3-4. 終了コードは完了判定に使えない（チェック項目 4）

実測した全ケースの終了コードは次のとおり。

| ケース | exit |
| --- | --- |
| 正常完了（ツール実行あり） | 0 |
| **ツール承認漏れで拒否された** | **0** |
| **実行したシェルコマンドが `exit 1` を返した** | **0** |
| 未認証（`Not logged in`） | 1 |
| 入力なし（argv も stdin も空） | 1 |

`exit 1` は CLI レベルの致命エラー（未認証・入力なし）だけを表す。**モデルの作業が成功
したかどうかは終了コードに反映されない**ため、claude の `--output-format json`
（`is_error` / `permission_denials`）に相当する機械可読な完了検知は Kiro には存在しない。

- 未認証時のメッセージ: `Failed to open browser for authentication.` /
  `Please try again with: kiro-cli login --use-device-flow` / `error: Permission denied (os error 13)`
- 入力なしのメッセージ: `error: Input must be supplied when running in non-interactive mode`

**結論**: kiro は `monitor.py` の **result.json 軸（成果物の存在）と stderr の早期エラー軸**
で判定する。終了コード軸は「1 なら即エラー」のみに使い、0 を成功とみなさない。

### 3-5. root 実行は拒否されない（チェック項目 5）

`sudo -n env HOME=$HOME PATH=$PATH kiro-cli chat --no-interactive --trust-all-tools 'ok とだけ答えて'`
は exit 0 で正常応答した。claude の `bypassPermissions` のような root 拒否は無い。

### 3-6. 出力の ANSI エスケープは抑制できない（実装上の注意）

`NO_COLOR=1 TERM=dumb` を与え、かつ標準出力がパイプ（非 TTY）でも ANSI が残る。

```console
$ NO_COLOR=1 TERM=dumb kiro-cli chat --no-interactive --trust-all-tools 'ok とだけ答えて' | cat -v
^[[m> ^[[0mok
```

`--wrap never` は改行制御のみで色は消えない。**launcher / monitor は
`sed -r 's/\x1B\[[0-9;]*[A-Za-z]//g'` 相当の除去を通してからパターン照合する。**

### 3-7. worktree・プロンプトサイズ・並列実行（追加実測）

| 項目 | 結果 |
| --- | --- |
| `--detach` worktree での読み取り | 通る（`git rev-parse --abbrev-ref HEAD` → `HEAD`、`cat a.txt` → 内容取得） |
| ブランチ付き worktree での編集 | 通る（`a.txt` を書き換え、`git diff --stat` まで実行） |
| stdin プロンプトのサイズ | **26,044 バイトで通る**（argv 長制限の回避に使える） |
| 2 プロセス同時実行 | 別 worktree で並列実行し、両方 exit 0。セッション DB の競合は観測されず |

### 3-8. 実行コストの実測（運用上の注意）

stderr のフッタに `▸ Credits: 0.06 • Time: 4s` の形式で消費量が出る。単純なタスクで
**0.02〜0.09 Credits**。claude の `total_cost_usd`（同種のタスクで $0.26）とは単位が異なる
ため直接比較はできないが、kiro 側は 1 回あたりの表示値が小さい。

### 3-9. 参考：`kiro-cli agent create` が生成する雛形

専用 agent JSON は生成しない方針だが、スキーマ確認のため実行した結果を残す。
2.18.0 では `allowedTools` に加えて `permissions.rules` が存在する。

```json
{
  "name": "probe_tmp", "description": "", "prompt": null,
  "mcpServers": {}, "tools": ["*"], "toolAliases": {},
  "allowedTools": [], "resources": [], "toolsSettings": {},
  "includeMcpJson": true, "model": null,
  "permissions": { "rules": [] }
}
```

`kiro-cli agent create` は `$EDITOR` を対話的に開くため、**非対話スクリプトから呼んでは
いけない**（本検証では vim が起動したまま止まり、タイムアウトで打ち切った）。
`kiro-cli agent list` は未認証だと `error: You are not logged in` で exit する。

なお `/tools`（対話コマンド）を `--no-interactive` の入力として渡しても**出力は空**で、
ツール名の一覧はこの経路では取得できない。

### 3-10. 旧・公開情報ベースの記述（2.18.0 で不成立）

以下は本 PR の初版が根拠にしていた記述である。**2.18.0 の実測で否定された**が、
判断の経緯として残す。

#### `allowedTools`（agent JSON）は当てにできない

計画の初版では、worktree に専用 agent JSON を生成して `allowedTools` で `execute_bash` を
事前承認する想定だった。しかし公開情報はこれを否定している。

- [kirodotdev/Kiro#7467](https://github.com/kirodotdev/Kiro/issues/7467)（Kiro 2.0.0 / Amazon Linux 2）:
  `~/.kiro/agents/default.json` や `~/.kiro/settings/mcp.json` の設定が **honored されず**、
  毎回 `Allow this action?` が出る。回避策として報告されているのは
  `kiro-cli chat --trust-tools "read,write,aws,report"` という**フラグでの明示指定**。
  ラベルは `pending-maintainer-response` で、公式回答は付いていない。
- 本リポジトリの既存実測（kiro-cli 2.16.1 / `03-runtime-conformance.md`）でも、
  Skill frontmatter の `allowed-tools` が事前承認として機能せず `execute_bash` が拒否された。

**設計変更**: agent JSON の `allowedTools` に依存する案は**破棄**する。
launcher は **`--trust-tools` / `--trust-all-tools` フラグで明示的に承認する**。
これにより `prepare-worktrees.sh` での専用 agent JSON 生成そのものが不要になる
（プロンプトは自己完結させる方針のため、そもそもカスタム agent を必要としない）。

> **実測との差分**: フラグで承認する結論は 3-2 の実測でも変わらない。ただし
> #7467 が言う「毎回 `Allow this action?` が出る」は 2.18.0 では観測されず、
> `--trust-tools` の絞り込みは正しく効いた。agent JSON 自体の検証は行っていない
> （使わない方針のため）。

#### 非対話モードは「全承認」か「ハング」の二択

- [kirodotdev/Kiro#7483](https://github.com/kirodotdev/Kiro/issues/7483):
  `--no-interactive` では `--trust-all-tools`（全自動承認）か、既定動作（承認待ちで**無限に
  ハング**）しかなく、安全な中間がない。`allowedTools` に無いツールを使おうとしたときに
  非ゼロ終了する `--deny-untrusted-tools` フラグが要望として挙がっている（未実装）。

**設計への影響 2 点**:

1. 承認漏れは**エラーではなくハング**として現れる。`monitor.py` の stall timeout が
   唯一の検知手段になるため、kiro の stall 判定は必ず有効にする。あわせて
   `Allow this action?` を早期エラーパターンに追加し、待ちに入った時点で kill する。
2. 承認範囲は事前にフラグで固定する。worktree が隔離済みであることを前提に、
   codex の `--dangerously-bypass-approvals-and-sandbox` / gemini の `--skip-trust` と
   同じ整理で `--trust-all-tools` を既定とし、`--trust-tools` での絞り込みを opt-in にする。

> **実測との差分**: 「ハングする」は 2.18.0 では**再現しない**（3-3）。承認漏れは
> 9 秒で拒否され exit 0 で終わる。したがって設計への影響 1 は撤回し、検知は
> stderr のパターン照合に置き換える。影響 2（フラグで承認範囲を固定する）は維持する。

### 3-11. 参考にした情報源

- [kirodotdev/Kiro#7467](https://github.com/kirodotdev/Kiro/issues/7467) — agent JSON / mcp.json の設定が効かない
- [kirodotdev/Kiro#7483](https://github.com/kirodotdev/Kiro/issues/7483) — `--no-interactive` に安全な中間がない
- [kirodotdev/Kiro#4384](https://github.com/kirodotdev/Kiro/issues/4384) — 既定 agent の trusted command 永続化要望
- [kirodotdev/Kiro#5449](https://github.com/kirodotdev/Kiro/issues/5449) — subagent が親の `allowedTools` を継承しない
- 公式ドキュメント（`kiro.dev/docs/cli/...`）は**このコンテナの egress proxy でブロック**されており参照できなかった

## 4. チェックリストの結果（すべて実測済み・未解決なし）

| # | 確認事項 | 結果 | 節 |
| --- | --- | --- | --- |
| 1 | `--trust-tools` で渡すツール名の正確な綴り | 内部名 `execute_bash` / `fs_read` / `fs_write`。表示名 `shell` / `write` も受理される。無効名は WARNING のみで exit 0。**launcher は `--trust-all-tools` を採用するため綴りに依存しない** | 3-0 / 3-2 |
| 2 | `--trust-all-tools` / `--trust-tools` が `--no-interactive` と併用できるか | **併用できる**。ツール実行・ファイル編集とも通る。Kiro の縮退案は不要 | 3-1 |
| 3 | プロンプトを stdin から渡せるか | **渡せる**（26,044 バイトで実測） | 3-0 / 3-7 |
| 4 | 完了検知の手段 | **終了コードは使えない**（拒否でもシェル失敗でも 0）。stderr のパターン照合と result.json で判定する | 3-3 / 3-4 |
| 5 | root 実行の可否 | **拒否されない**（`sudo` 実行で exit 0）。claude の `bypassPermissions` のような制約は無い | 3-5 |
| 6 | 検証に使った kiro-cli の版数 | **2.18.0**（既存実測は 2.16.1）。#7467 / #7483 の報告は 2.18.0 では再現しない | 3-2 / 3-3 |

確認結果は `external-ai/references/cli-kiro.md` の初版に反映する（Task 3 の残作業）。

## 5. 計画への反映（確定分）

| 項目 | 変更 |
| --- | --- |
| Task 2 | **kiro 専用 agent JSON の生成を削除**する（`allowedTools` に依存しないため不要） |
| Task 4 | `launch-cli.sh` の claude 分岐を「stdin からプロンプト + `--permission-mode acceptEdits` + `--allowed-tools` + `--output-format json`」で確定。kiro 分岐は **`cat prompt.md \| kiro-cli chat --no-interactive --trust-all-tools`** で確定（実測済み） |
| Task 9 | claude は `permission_denials` 非空を早期エラーに追加。**kiro は `is rejected because it matches one or more rules on the denied list` を早期エラーに追加し、判定前に ANSI エスケープを除去する**。kiro の終了コード 0 は成功とみなさない（stall timeout は別要因への保険として残す） |
| リスク表 | 「root で `bypassPermissions` が使えない」を追加。claude 参加時のコスト（単純タスクで $0.26）を運用上の注意として追加。**kiro はシェルを許可すると他ツールの制限を迂回するため、防御は worktree 隔離のみに依存すること**を追加 |

## 6. frontmatter 予算の確認（実測）

新 Skill 追加が `scripts/check-skill-frontmatter.py` の上限に収まるかを確認した。

```console
$ python3 scripts/check-skill-frontmatter.py --report | tail -2
frontmatter 合計: 10612 文字 (上限 11200)
```

- 残余は **588 文字**。
- 比較対象として `cross-review` の frontmatter は **407 文字**（`name` + `description` +
  `argument-hint` + `allowed-tools`）。
- cross-refactoring を同規模で書くと残り約 180 文字となり、`external-ai` の `description` を
  4 CLI 対応へ広げる分（数十文字）を足すとほぼ使い切る。

**方針**: `argument-hint` を短く保ち、`allowed-tools` は必要最小限に絞る。
それでも超える場合は Task 12 で `FRONTMATTER_TOTAL_MAX` の引き上げか、既存 `description` の
圧縮を同 PR で行う（計画 Task 10 に記載済みの分岐）。

## 7. モデル指定フラグ（4 CLI 実測）

ランタイム／モデルの比較を可能にするため（計画 §10）、各 CLI のモデル指定手段を確認した。
**4 CLI すべてにモデル指定オプションがある。**

| runtime | フラグ | 備考 |
| --- | --- | --- |
| claude | `--model <model>` | エイリアス（`opus` / `sonnet` / `fable`）とフルネームの両方を受ける |
| codex | `-m, --model <MODEL>` | `-c model="o3"` の設定経由でも指定できる |
| gemini | `-m, --model <MODEL>` | |
| kiro | `--model <MODEL>` | `--list-models` で候補を列挙。`-f json` / `-f json-pretty` も可 |

### 7-1. kiro はランタイムとモデルが直交する（設計上の要点）

kiro-cli 2.18.0 の `--list-models` は **claude 系と gpt 系の両方**を提供する。

```console
$ kiro-cli chat --list-models
Available models (* = default):
* auto                 1.00x credits      Models chosen by task for optimal usage and consistent quality
  claude-opus-5        2.20x credits      Experimental preview of Claude Opus 5 model with 1M context window
  claude-sonnet-5      1.30x credits      Claude Sonnet 5 model with 1M context window
  claude-opus-4.8      2.20x credits      Claude Opus 4.8 model with 1M context window
  gpt-5.6-sol          2.40x credits      Experimental preview of OpenAI GPT 5.6 Sol with 272k context window
  gpt-5.6-terra        1.00x credits      Experimental preview of OpenAI GPT 5.6 Terra with 272k context window
  gpt-5.6-luna         0.10x credits      Experimental preview of OpenAI GPT 5.6 Luna with 272k context window
  claude-opus-4.7 / claude-opus-4.6 / claude-sonnet-4.6 / claude-opus-4.5 / claude-sonnet-4.5
```

**帰結**: 「ランタイム（ハーネス）」と「モデル」は独立に選べるため、ランタイムを跨いだ
比較はモデルを揃えないと交絡する。`kiro:claude-opus-5` と `claude:opus-5` を比べれば
**ハーネス差**、`kiro:claude-opus-5` と `kiro:gpt-5.6-sol` を比べれば**モデル差**が見える。

### 7-2. kiro の既定は `auto` — 計測時は明示指定が必須

既定モデルは `auto` で、「タスクに応じて最適なモデルを選ぶ」と説明されている。
**ラウンドごとに違うモデルが動きうるため、計測目的の実行では `--model kiro=<name>` を
明示する**。指定しなかった場合、`report` は当該ラウンドを「モデル未確定」として
集計から分離する必要がある。

credits 倍率がモデルごとに違う点（`gpt-5.6-luna` 0.10x 〜 `gpt-5.6-sol` 2.40x）も、
コスト比較の際に効く。
