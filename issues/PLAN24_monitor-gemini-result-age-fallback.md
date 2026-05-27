# PLAN24: monitor.py — gemini result.json 存在 + 経過時間による早期 OK 判定

- 起票日: 2026-05-27
- 対象 plugin: `ndf` v4.9.0
- 対象 skill: `ndf:cross-review`
- 関連 issue: [GitHub #21](https://github.com/devbasex/ai-plugins/issues/21)
- 関連 plan (先行): PLAN21 (per-agent stall timeout, PR #6 merge 済み)

## 背景・課題

gemini がレビュー完了し result.json を書き込み済みなのに、プロセスがハング
（MCP サーバー切断待ち等）して exit しないため、`monitor.py` が hard timeout
(420s) まで待たされて TIMEOUT 扱いになる。

codex には sentinel (`tokens used` in err.log) + result.json で「プロセスが
生きたまま完了」を検知する機構がある (`monitor.py:452-467`)。gemini にはこの
機構がなく、result.json が書かれた後もプロセスがハングすると、sentinel なし・
プロセス alive のまま hard timeout に到達する。

## 再現確認

簡単なプロンプトで gemini CLI を `nohup ... & disown` で起動した結果:
- gemini は回答を stdout に書き込み完了
- しかしプロセスは **ゾンビ状態** (`Z <defunct>`) になった
- `kill -0` はゾンビに対しても成功 → `_pid_alive()` が True を返し続ける
- 根本原因: Docker コンテナの PID 1 が `tail -f /dev/null` (proper init でない)

## 修正内容

### 1. ゾンビ検出 (`_pid_alive()`)
`/proc/<pid>/status` の `State:` 行を読み、`Z` (zombie) なら False を返す。
これにより既存の `not alive → result.json チェック → OK` パスで正しくハンドリング。

### 2. result.json + age fallback
プロセスが truly alive (ゾンビではないが Node.js event loop stuck 等) の場合のため、
result.json の mtime が 30 秒以上前なら完了とみなし kill → OK。

### 3. `_kill_pid()` のゾンビ対応
ゾンビプロセスにはシグナルを送れないためスキップ。

### 4. Docker `--init` の推奨
ドキュメントにゾンビ蓄積防止策として Docker `--init` フラグの使用を推奨。

## 変更ファイル

1. `plugins/ndf/skills/cross-review/scripts/monitor.py` — ゾンビ検出 + fallback
2. `plugins/ndf/skills/cross-review/tests/test_monitor_zombie.py` — ゾンビ検出テスト
3. `plugins/ndf/skills/cross-review/tests/test_monitor_result_age.py` — fallback テスト
4. `plugins/ndf/skills/cross-review/docs/01-state-and-review.md` — ドキュメント追記

## PR 構成

単一 PR: `fix/PLAN24-monitor-gemini-result-age-fallback` → `main`
