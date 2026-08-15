# Issue 113 / Task 3: CLI 非対話実行の検証記録

## 関連リンク

- 実施計画: [issue-113-cross-refactoring-loop.md](issue-113-cross-refactoring-loop.md)
- GitHub Issue: https://github.com/devbasex/ai-plugins/issues/113
- 既存の Kiro 実機検証: [ndf-development-skills/03-runtime-conformance.md](ndf-development-skills/03-runtime-conformance.md)

## 検証環境

| 項目 | 値 |
| --- | --- |
| 実施日 | 2026-08-15 |
| 実行環境 | Claude Code on the web のリモートコンテナ（**root 実行**） |
| git | 2.43.0 |
| claude | 2.1.233 |
| codex / gemini / kiro-cli | **未インストール**（このコンテナでは実機検証不可） |

コンテナに codex / gemini / kiro-cli が無いため、この 3 つは実機検証できていない。
codex / gemini は cross-review で運用実績があるため対象外とし、**Kiro のみ公開情報からの
調査**に留めた。Kiro の実機確認は kiro-cli が入った環境で別途行う必要がある（下記
「未解決」を参照）。

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

## 3. Kiro CLI — 公開情報の調査（実機未確認）

このコンテナに kiro-cli が無いため、以下は公開情報からの整理である。**実機確認前に実装へ
進んではならない**項目を「未解決」に分けた。

### 3-1. `allowedTools`（agent JSON）は当てにできない

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

### 3-2. 非対話モードは「全承認」か「ハング」の二択

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

### 3-3. 参考にした情報源

- [kirodotdev/Kiro#7467](https://github.com/kirodotdev/Kiro/issues/7467) — agent JSON / mcp.json の設定が効かない
- [kirodotdev/Kiro#7483](https://github.com/kirodotdev/Kiro/issues/7483) — `--no-interactive` に安全な中間がない
- [kirodotdev/Kiro#4384](https://github.com/kirodotdev/Kiro/issues/4384) — 既定 agent の trusted command 永続化要望
- [kirodotdev/Kiro#5449](https://github.com/kirodotdev/Kiro/issues/5449) — subagent が親の `allowedTools` を継承しない
- 公式ドキュメント（`kiro.dev/docs/cli/...`）は**このコンテナの egress proxy でブロック**されており参照できなかった

## 4. 未解決（kiro-cli のある環境で確認する）

| # | 確認事項 | 確認方法 |
| --- | --- | --- |
| 1 | `--trust-tools` で渡すツール名の正確な綴り | `kiro-cli chat --help` と `/tools` の一覧。既存実測では `execute_bash`、#7467 の例では `read,write,aws,report` と表記ゆれがある |
| 2 | `--trust-all-tools` / `--trust-tools` が `--no-interactive` と併用できるか | `kiro-cli chat --no-interactive --trust-all-tools "git status --short を実行して"` |
| 3 | プロンプトを stdin から渡せるか（argv 必須か） | 上記を `cat prompt.md \| kiro-cli chat --no-interactive --trust-all-tools` で試す |
| 4 | 完了検知の手段（終了コード / 出力ファイル） | 正常終了・ツール拒否・未認証の 3 ケースで終了コードと stderr を記録 |
| 5 | root 実行の可否 | claude と同様の root 拒否がないかを確認 |
| 6 | 検証に使った kiro-cli の版数 | `kiro-cli --version`。2.16.1 と挙動が変わっていないか |

確認結果は本ファイルに追記し、`external-ai/references/cli-kiro.md` の初版に反映する。

## 5. 計画への反映（確定分）

| 項目 | 変更 |
| --- | --- |
| Task 2 | **kiro 専用 agent JSON の生成を削除**する（`allowedTools` に依存しないため不要） |
| Task 4 | `launch-cli.sh` の claude 分岐を「stdin からプロンプト + `--permission-mode acceptEdits` + `--allowed-tools` + `--output-format json`」で確定。kiro 分岐は `--no-interactive --trust-all-tools` を既定に置き、ツール名が確定したら `--trust-tools` へ絞る |
| Task 9 | claude は `permission_denials` 非空、kiro は `Allow this action?` を早期エラーパターンに追加。kiro は承認漏れがハングとして現れるため stall timeout を必須にする |
| リスク表 | 「root で `bypassPermissions` が使えない」を追加。claude 参加時のコスト（単純タスクで $0.26）を運用上の注意として追加 |

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
