# #1290 の設計: 入出力の契約

設計の本体は [issue-1290-design.md](issue-1290-design.md) にある。この文書はその「入出力の契約」の節を分けたものである（1 ファイル 500 行の上限）。

## 入出力の契約

### 最小の呼び出しのコマンド（2026-10-06 に実測）

問いは標準入力から渡す（`Reply with the single word OK.`）。応答の中身は見ない。

| ランタイム | 種類 `model`（明示のモデルがあれば `--model <名前>` を足す） | 種類 `default` | 実測（所要・費用） |
| --- | --- | --- | --- |
| claude 2.1.291 | `claude -p --output-format json --tools "" --system-prompt "Answer briefly." --strict-mcp-config --disable-slash-commands --no-session-persistence` | 種類 `model` に `--model default` | 5.3 秒・0.020 米ドル（既定の起動の形は 4.7 秒・0.44 米ドル）。存在しないモデルは終了コード 1、JSON の `"api_error_status":404` |
| codex 0.160.0 | `codex exec --skip-git-repo-check --ephemeral -s read-only -C <一時ディレクトリ>` | 種類 `model` に `--ignore-user-config` | 3.8〜5.0 秒。標準エラーの見出しに `model: <名前>`。使えないモデルは終了コード 1 と `ERROR: {..."status":400,..."model is not supported ..."}` |
| kiro-cli 2.24.1 | `kiro-cli chat --no-interactive` | 持たない | 5.2〜7.1 秒。`--model` は `[warn] failed to set model '<名前>': Method not found` を出して既定のモデルで答える（`auto` でも同じ） |
| agy 1.2.11 | 持たない（認証確認 `agy models` だけ） | 持たない | この環境が未認証で測れない（未確認のまま残ること） |

今の認証確認の所要は claude 0.1 秒・codex 0.06 秒・kiro 1.0 秒（同じ日の実測）。

### 分類の順序（`auth.run_check`）

1 回の確認の出力（標準出力と標準エラーを合わせた文字列。kiro は色の符号を除く）を次の順に照らし、最初に当たった語を `reason` にする。

| 順 | 条件 | 結果 |
| ---: | --- | --- |
| 1 | 起動できない・時間切れ | `missing_cli` / `timeout` |
| 2 | `monitor_patterns.AUTH_EXPIRED_FATAL` に当たる | `auth_expired` |
| 3 | `MODEL_UNAVAILABLE_FATAL`、claude の標準出力の 404、明示のモデルを渡した kiro の `failed to set model` に当たる | `model_unavailable` |
| 4 | `USAGE_LIMIT_FATAL` か claude の標準出力の 429 に当たる | `ok`（決定 6） |
| 5 | `UNAUTHENTICATED_MARKERS` に当たる | `unauthenticated` |
| 6 | 終了コードが 0 でない、kiro で応答が空 | 種類 `auth` は `unauthenticated`、それ以外は `probe_failed` |
| 7 | どれにも当たらない | `ok` |

### 致命の文言（`monitor_patterns.py`。確認と監視が同じ表を使う）

| 表 | 文言の形（行頭から。`ERROR:` の前置きを許す） | 実物 |
| --- | --- | --- |
| `AUTH_EXPIRED_FATAL` | `Your access token could not be refreshed because your refresh token was (revoked\|invalidated)` | #461（PR #661・#665・#666） |
| `MODEL_UNAVAILABLE_FATAL` | `unexpected status 404 Not Found: The model … does not exist or you do not have access to it` | #461（PR #460） |
| `MODEL_UNAVAILABLE_FATAL` | `{` で始まる JSON で `"status": 400` と `model is not supported` を含む | #1589（PR #1588） |
| `CLAUDE_STDOUT_MODEL_UNAVAILABLE` | `{` で始まる JSON の `"api_error_status": 404`（claude の標準出力だけ） | 2026-10-06 の実測 |

`REASONED_FATAL` は `(理由, err.log の表, claude の標準出力の表)` を `usage_limit` → `auth_expired` → `model_unavailable` の順に並べる。利用上限を先に置くのは今の決まり（#729 の決定 6）のままである。

### `init` / `check` の出力

参加者ごとの 1 行は、全員の確認が終わってから `ALL_RUNTIMES` の順に出す（並行に走らせても出力の並びが変わらない）。行の先頭の `✅` / `❌` は変えない。

```text
✅ claude: 認証とモデル（4.9 秒）
↪ codex: 設定のモデル gpt-5.5 を引けないため、既定のモデル gpt-6.1-sol で担当に入れる（9.1 秒）
❌ kiro: auth_expired — ERROR: Your access token could not be refreshed ...
✅ agy: 認証だけ（モデルの確認を持たない）（1.0 秒）
```

- 詳細は今の確認と同じく先頭 200 文字で、`secret_redact.redact_output` を通す
- 呼び手が続けて出す `⚠ <名前> を担当から外しました（<理由>: <詳細>）` は今のまま（`unavailable` の値が理由を含むようになる）
- `--require-all` の中断の文を「確認を通らない CLI があります」に直す（理由は未認証に限らない）
- `external-ai.py check` は、確認を通らなければ理由に依らず今の `outcome` の `auth` と終了コード `EXIT_PRECONDITION` で終え、`metrics.reason` に理由の語、`summary` に詳細を入れる（`outcome` の語彙は増やさない）
- `external-ai.py check` が既定のモデルへの切り替え（`↪`）で通ったときは、`outcome` の `ok` に加えて `metrics.default_model` に既定のモデルの名前、`metrics.from_model` に引けなかった元のモデルの名前（読めた場合）を入れ、`next` に `external-ai.py run <名前> --model <既定の名前> ...` を案内する。`run` は状態ファイルを持たず、`--model` が無ければ設定のモデルのまま起動するためである（決定 9）。切り替えが無いときは `metrics.default_model` を `null` にする

### 呼び出しの形

| 名前 | 入力 | 出力 | 互換性 |
| --- | --- | --- | --- |
| `auth.probe_auth(runtimes, *, info, env=None, models=None, level="model")` | `models` はランタイム → 明示のモデル。`level="auth"` は認証確認だけ（external-ai の `run`） | `(名前 → 辞書, 飛ばしたか)`。例外を上げない | 今の 3 キーを保つ。呼び手 3 か所は引数を足すだけ |
| `assignment.admit(runtime, *, explicit_model, check)` | `check(step, runtime, model) -> Check` | `Admission` | 新設 |
| `assignment.resolve_participants(...)` | 今のまま | `Participants`（`checks` / `default_models` を足す） | 足すだけ |
| 起動のモデルの式（3 つの起動側） | 状態ファイル・ランタイム | `jq -r --arg rt "$RUNTIME" '(.models // {})[$rt] // (.participants.default_models // {})[$rt] // ""'` | cross-refactoring は `.models[$rt]` を読む今の振る舞いを含む。cross-review は空を渡す今の振る舞いを含む |
