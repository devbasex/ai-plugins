# [ndf:cross-review] 不具合報告 — monitor.py の早期エラー誤検知で codex / gemini が無条件に kill される

- 報告日: 2026-05-19
- 報告者: takemi-ohama
- 対象 plugin: `ndf` (cache パス: `/home/ubuntu/.claude/plugins/cache/ai-plugins/ndf/4.7.1/`)
- 対象 skill: `ndf:cross-review`
- 対象スクリプト: `skills/cross-review/scripts/monitor.py`、`skills/cross-review/scripts/launch-gemini.sh`
- 確認バージョン: 4.7.1 (`/home/ubuntu/.claude/plugins/cache/ai-plugins/ndf/4.7.1`)
- 影響度: **High** — 中規模以上の PR で `/ndf:cross-review` がほぼ毎ラウンド失敗扱いになり、ループが回らない

## 概要

`/ndf:cross-review` でレビューラウンドを回したところ、`monitor.py` の **EARLY_ERROR 検知** が
正常実行中の codex / gemini を「致命的エラー」と誤判定して **SIGTERM → SIGKILL** で
強制終了させる事象が複数のラウンドで再現した。

`monitor.py` は EARLY_ERROR を検知すると対象プロセスを kill するため、AI 側はレビューを
完了できず `result.json` も書き出せない。結果としてラウンド全体が `EARLY_ERROR` で
中断され、cross-review ループが進まない。

実運用では監視を介さずに `result.json` の生成有無で進捗判定するカスタムポーリングに
切替えて回避したが、本来は plugin 側で修正されるべき。

## 影響範囲

| 観測 | 影響 |
|---|---|
| PR #276 round 1 | gemini を 30s 時点で誤 kill (`Error in: mcpServers.github`) |
| PR #276 round 1 (codex 再現) | codex を 105s 時点で誤 kill (`Traceback (most recent call last):` ※diff 本文の引用) |
| PR #276 round 2 | codex / gemini 両方が **同様の誤検知で kill される** ことを再確認 |
| 全 round 共通 | 1 度誤検知が起きると round 全体が EARLY_ERROR 終了し、`/ndf:cross-review` 本体は `final=error` 一歩手前まで進む |

PR #275 (小規模 PR, diff 量が少ない) では再現しなかったため、**diff 量が増えて
err.log にコード片 (Traceback を含む test コード等) や MCP 警告が混在しやすい
中〜大規模 PR** で実害が出る。

## 再現条件

1. `/ndf:cross-review <PR>` を **自分の PR** に対して実行（`event_downgrade=true`）
2. PR の diff にテストコード等が含まれ、codex / gemini の err.log にコード片や
   Traceback 引用が出力される
3. または worktree 内 `.gemini/settings.json` が gemini-cli 最新仕様と非互換で
   起動直後に `Error in: mcpServers.<name>` 警告が出る

## 観測されたログ (代表例)

### 例 1: gemini を `Error in: mcpServers.github` で kill

```
=== /home/ubuntu/.gemini/tmp/pr276/gemini-review-pr276-err.log (Round 1 初回起動) ===
Invalid configuration in /work/worktrees/pr276/.gemini/settings.json:

Error in: mcpServers.github
    Unrecognized key(s) in object: 'disabled'

Error in: mcpServers.awslabs.core-mcp-server
    Unrecognized key(s) in object: 'disabled'
...
Please fix the configuration.
See: https://geminicli.com/docs/reference/configuration/
YOLO mode is enabled. All tool calls will be automatically approved.
YOLO mode is enabled. All tool calls will be automatically approved.
Ripgrep is not available. Falling back to GrepTool.
MCP issues detected. Run /mcp list for status.
```

monitor.py の判定:

```
[gemini] 💥 gemini EARLY_ERROR (15s) — early error in err.log: Error in: mcpServers.github
{"agent": "gemini", "status": "EARLY_ERROR", "exit_code": 4, ...}
```

gemini-cli はこのメッセージを出した後も **YOLO mode is enabled** と表示して
実行を継続する仕様（無効キーは無視するだけで起動失敗ではない）だが、
monitor.py は致命扱いで kill する。

### 例 2: codex を `Traceback (most recent call last):` で kill

PR の diff にテストコード (例: `test_lease_judgment_skipped_when_lease_tier_disabled` の
シミュレートエラー処理) が含まれていると、codex はその diff を err.log に echo する。

```
+    def test_lease_judgment_skipped_when_lease_tier_disabled(self, client, monkeypatch):
+        """lease_tier 失敗時に lease_judgment が自動 skip + error=dependency 'lease_tier' not available"""
...
+        def _failing_execute(self, ctx):
+            raise RuntimeError("simulated lease_tier failure")
...
Traceback (most recent call last):
```

monitor.py の判定:

```
[codex] 💥 codex EARLY_ERROR (105s) — early error in err.log: Traceback (most recent call last):
```

codex は diff 表示の一部としてレビュー中に `Traceback` 文字列を出力しているだけで、
実際には正常稼働中。150〜260 秒後には正常に `tokens used` sentinel に達し、
`result.json` を書く挙動 (実環境で確認済) なのに、105 秒時点で kill されてしまう。

## 根本原因

### A. EARLY_ERROR パターンが広すぎる

`monitor.py:67`

```python
re.compile(r"^(?:Error|FATAL|fatal|panic|PANIC|Traceback)[: ]", re.MULTILINE),
```

このパターンは:

- `Error in: mcpServers.github` — gemini 起動時の **無害な設定警告** にマッチ
- `Traceback (most recent call last):` — **codex がレビュー対象の test コード片
  を echo した行** にマッチ (test の例外処理コードに合法的に含まれる)

### B. BENIGN フィルタが弱い

`monitor.py:81`

```python
EARLY_ERROR_BENIGN = [
    re.compile(r"^[ +-].*[\|`]", re.MULTILINE),  # diff context with `|` or backtick
    re.compile(r"^\s*[-*>]\s", re.MULTILINE),    # markdown list/quote
    re.compile(r"^warning: ", re.IGNORECASE | re.MULTILINE),
]
```

`Traceback (most recent call last):` のような行は **行頭 `T`** で始まり、
benign フィルタの「diff context (` +-`)」「markdown list (`-*>`)」「warning:」の
いずれにもマッチしないため、誤検知を回避できない。

また `Error in: mcpServers.github` も行頭 `E` で benign フィルタにかからない。

### C. EARLY_ERROR 判定が kill とセットになっている

`monitor.py:347-355`

```python
err = _scan_early_errors(paths.err_log)
if err:
    if alive:
        _kill_pid(pid)
    status.status = "EARLY_ERROR"
    status.exit_code = 4
```

EARLY_ERROR 検知時に **必ず対象プロセスを kill** する設計のため、
誤検知 1 回で AI のレビュー実行が完全に断たれ、`result.json` も生成できない。

「致命と確証が取れたパターンのみ kill する」「曖昧なパターンは警告に
留めて待機を続ける」といった段階制御が無い。

### D. 早期エラー検知を無効化するフラグが無い

`monitor.py` には:

- `--timeout` / `MONITOR_TIMEOUT`
- `--stall-timeout` / `MONITOR_STALL`
- `--no-require-result`

のオプションがあるが、**早期エラー検知を無効化する手段が無い** ため、
誤検知が出続けると待機側でも対処できない。

### E. (副次的) `.gemini/settings.json` が gemini-cli 最新仕様で非互換

このリポジトリでは `.gemini/settings.json` の各 mcpServer エントリに
`disabled: false` を書き込んでいたが、gemini-cli 最新版はこのキーを
Unrecognized 扱いにする。

`launch-gemini.sh` 側で「`cd $WORKTREE` してから gemini を起動」する設計上、
worktree の `.gemini/settings.json` を必ず読みに行くため、リポジトリ側に
非互換ファイルがあると毎回 `Error in: mcpServers.*` が出る。

これ自体はリポジトリ側で直すべき問題だが、上記 A〜D と組み合わさることで
**cross-review が起動しないほど影響が大きくなる**。

## 影響の連鎖

```
.gemini/settings.json の disabled key (リポ側の非互換)
      ↓
gemini 起動時に err.log に "Error in: mcpServers.github" を出力 (実害なし、続行可能)
      ↓
monitor.py の EARLY_ERROR_PATTERNS が "Error in:" にマッチ (誤検知)
      ↓
_kill_pid() で gemini を強制終了 (result.json 未生成)
      ↓
ラウンド全体が EARLY_ERROR (exit 4) で終了
      ↓
/ndf:cross-review が進まない / 全 round が同じ理由で失敗
```

## 再現手順

1. 中規模以上の PR (diff にテストコードを含む) を対象に `/ndf:cross-review <PR>` を実行
2. もしくは worktree 内 `.gemini/settings.json` に gemini-cli 最新版で
   非互換のキー (例: `disabled: false`) を含めて実行
3. `monitor.py` が `^Error[: ]` / `^Traceback[: ]` を err.log で見つけて kill する

## 暫定回避策 (今回適用したもの)

1. `.gemini/settings.json` の `disabled` キーをラウンド開始前にローカルで削除し、
   ラウンド終了後 `git restore` で戻す
2. `monitor.py` を使わず、`result.json` ファイルの生成有無を直接ポーリングする
   bash ループに切替え (EARLY_ERROR 検知をバイパス)

```bash
# 暫定: result.json の生成を 600 秒まで待つだけのポーリング
for i in $(seq 1 30); do
  sleep 20
  CODEX_RESULT=$([ -s "$TMP/codex-review-pr$PR-result.json" ] && echo 1 || echo 0)
  GEMINI_RESULT=$([ -s "$TMP/gemini-review-pr$PR-result.json" ] && echo 1 || echo 0)
  if [ "$CODEX_RESULT" = 1 ] && [ "$GEMINI_RESULT" = 1 ]; then
    break
  fi
done
```

## 修正提案

### 提案 1 (最優先): EARLY_ERROR 検知に「致命度」を導入し kill を抑制する

「明確な致命パターン (auth 失敗 / quota / sandbox)」と「曖昧パターン (生の
`Error:` / `Traceback`)」を区別し、後者は **kill せず警告ログのみ** に留める。
プロセスが自走中なら sentinel / result.json / hard timeout の通常経路で判定する。

```python
EARLY_ERROR_FATAL = [
    re.compile(r"^Authentication failed", re.MULTILINE),
    re.compile(r"^.*\b(quota exceeded|rate limit exceeded)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^.*\bAPI key (not found|missing|invalid)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^.*\bsandbox error\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^HTTP/\d\S* (?:401|403|429) ", re.MULTILINE),
    re.compile(r'^Approval mode overridden to "default"', re.MULTILINE),  # gemini 固有
]

EARLY_ERROR_WARN = [
    # 行頭 Error: / Traceback: は警告のみ (kill しない)
    re.compile(r"^(?:Error|FATAL|fatal|panic|PANIC|Traceback)[: ]", re.MULTILINE),
]
```

検知ロジック:

```python
fatal_err = _scan_patterns(err_log, EARLY_ERROR_FATAL)
warn_err = _scan_patterns(err_log, EARLY_ERROR_WARN)
if fatal_err:
    _kill_pid(pid)
    return EARLY_ERROR
elif warn_err and not warned:
    log.warning(f"[{agent}] early-error pattern detected (non-fatal): {warn_err}")
    warned = True
# kill せず通常の sentinel / result.json / timeout 判定を続行
```

### 提案 2: BENIGN フィルタを強化

- gemini の `Error in: mcpServers.\S+` 警告を明示的に除外
- codex がレビュー本文として echo する diff の一部 (前後数行に `+`/`-` で
  始まる行が連続) を benign 判定

```python
EARLY_ERROR_BENIGN += [
    # gemini config validation warning
    re.compile(r"^Error in: mcpServers\.", re.MULTILINE),
    # 直前 3 行以内に diff hunk header (`@@ ...`) があれば diff body と判定
    # (実装イメージ — _scan_early_errors 内で前後行を見て判定)
]
```

### 提案 3: `--no-early-error` フラグを追加

既存の `--no-require-result` と同様、EARLY_ERROR 検知自体を無効化する
フラグを `monitor.py` に追加し、メイン側で誤検知を回避できるようにする。

```python
p.add_argument("--no-early-error", action="store_true",
               help="EARLY_ERROR 検知を無効化 (hard timeout / stall / sentinel のみで判定)")
```

### 提案 4: `launch-gemini.sh` を改善

worktree の `.gemini/settings.json` を読まないよう、`GEMINI_CONFIG_DIR`
等の専用環境変数で gemini-cli の config を上書きする（gemini-cli が対応する
場合）。または launcher が worktree の `.gemini/settings.json` を一時的に
退避してから gemini を起動する仕組みを提供する。

```bash
# 案: launcher が worktree の .gemini/settings.json を mv で退避し、
#     最低限のフォールバックを置く
if [ -f "$WORKTREE/.gemini/settings.json" ]; then
  cp "$WORKTREE/.gemini/settings.json" "$TMP_DIR/.gemini-settings-backup.json"
  echo '{"context_files":["AGENTS.md"]}' > "$WORKTREE/.gemini/settings.json"
  trap 'mv "$TMP_DIR/.gemini-settings-backup.json" "$WORKTREE/.gemini/settings.json"' EXIT
fi
```

### 提案 5: 各ラウンド開始前の env / config 健全性チェック

cross-review 開始時に gemini-cli を `gemini --help` 程度で 1 度起動して
err.log のフォーマットを確認し、`Error in: mcpServers.*` 等の既知警告が
出るなら自動的にパッチを当てるか、ユーザに通知する。

## 関連ファイル

| ファイル | 行 | 内容 |
|---|---|---|
| `skills/cross-review/scripts/monitor.py` | 65-78 | EARLY_ERROR_PATTERNS 定義 |
| `skills/cross-review/scripts/monitor.py` | 81-88 | EARLY_ERROR_BENIGN 定義 |
| `skills/cross-review/scripts/monitor.py` | 208-236 | `_scan_early_errors` 実装 |
| `skills/cross-review/scripts/monitor.py` | 347-355 | EARLY_ERROR 検知 → `_kill_pid` の呼び出し |
| `skills/cross-review/scripts/launch-gemini.sh` | 99-107 | worktree CWD で gemini 起動 (settings.json を読みに行く) |
| `skills/cross-review/SKILL.md` | (アンチパターン節) | "`monitor.py` の多軸判定を使うこと" と書かれているが、その多軸判定そのものが今回 kill の原因 |

## 補足: 実害が出たラウンド一覧

| PR | round | agent | 観測時刻 | 検知パターン | 実際の状態 |
|---|---|---|---|---|---|
| #276 | 1 | gemini | 30s | `Error in: mcpServers.github` | 起動 1 警告のみ、続行可能 |
| #276 | 1 | codex | 105s | `Traceback (most recent call last):` | レビュー実行中 (sentinel 未到達)、150s で正常完了 |
| #276 | 2 | gemini | 30s | `Error executing tool mcp_github_get_pull_request_files: ...` | 一部 MCP 失敗だが gh CLI でリカバリ可能、続行中 |
| #276 | 2 | codex | 90s | `Traceback (most recent call last):` | 同上 |

すべて kill 後にカスタムポーリングで再起動 → `result.json` が **160〜260 秒**
程度で正常生成されたことを確認しているため、誤検知であることは確定的。

## 期待する対応

1. **(最優先)** `monitor.py` の EARLY_ERROR 検知を「警告のみ」と「kill する致命」に
   分離し、`Error:` / `Traceback` のような曖昧パターンは kill 対象から外す
2. `--no-early-error` フラグの追加 (escape hatch として)
3. (推奨) `launch-gemini.sh` が worktree の `.gemini/settings.json` を読みに
   行く副作用を抑制する仕組みを提供
4. (推奨) SKILL.md の「アンチパターン」節に **「`monitor.py` が
   false-positive で kill する場合の手順」** を追記

ご検討よろしくお願いします。
