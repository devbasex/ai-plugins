# ndf plugin (4.7.2) cross-review skill 修正提案

> 報告者: takemi-ohama
> 発見日: 2026-05-23
> 対象 plugin: `ai-plugins/ndf/4.7.2` — `skills/cross-review/`
> 検証 PR: devbasex/devbase #19 (Round 1 で収束。両 AI とも COMMENT/0 critical/0 major)

PR #19 で `/ndf:cross-review` を実走させた際に観測した、運用上の不整合 3 点をまとめる。
いずれも cross-review ループ自体は完走したが、メインセッション側で手動補正が必要だった。

## 1. gemini の stall-timeout デフォルト (180s) が短すぎる

### 現状

- `skills/cross-review/scripts/monitor.py:63`
  ```python
  DEFAULT_STALL = int(os.environ.get("MONITOR_STALL", "180"))   # 3 min no progress
  ```
- 既定 stall = 3 分。err.log + stdout.log の合計サイズに変化が無いと STALLED 扱いで kill される。

### 観測した事象 (PR #19 Round 1)

- gemini プロセス pid=26149 を起動 → 180 秒の間 err.log は 340B から増えず STALLED 判定 → kill
- 1 度目の launch で gemini node プロセスが「子側」だったため、`pkill` 後も実プロセス (pid 26155) が
  生き残っていた (skill `アンチパターン` でも触れている既知の挙動)
- `--stall-timeout 480 --timeout 900` で再起動したところ、**195 秒**で正常終了 (`result.json` 生成済)
  - sentinel は最後まで出ず (`sentinel_seen: false`)、`result_exists=True` のみで判定された
- 観測ログ (抜粋):
  ```
  [gemini] ⏳ gemini elapsed=180s pid=26968 err_log=340B sentinel=-
  [gemini] ✅ gemini OK (195s) — process exited; sentinel=False; result_exists=True
  ```

### 原因の推定

- codex は推論ステップを逐次 err.log に書き出すため `err_log_size` が増え続け stall 検知に引っかからない
- gemini はリクエスト送信〜レスポンス受領まで **err.log にほぼ何も書かない**
  (起動直後の `YOLO mode is enabled.` 等の 340B のみ)。「サイズ非変化 = stall」前提と相性が悪い

### 提案

1. **agent 別に DEFAULT_STALL を分岐**:
   ```python
   DEFAULT_STALL_CODEX  = int(os.environ.get("MONITOR_STALL_CODEX",  "180"))
   DEFAULT_STALL_GEMINI = int(os.environ.get("MONITOR_STALL_GEMINI", "480"))
   ```
   `monitor.py` の per-agent ループで切り替える。
2. 上記が大きすぎる変更なら、**gemini については stall 判定を緩める fallback** を入れる:
   - `result.json` 不在 + プロセス生存 + sentinel 無し のときは hard timeout のみで判定
3. docs (`docs/01-state-and-review.md` 周辺) に「**gemini は err.log が静かなため stall-timeout を
   ≥ 480 秒推奨**」と明記

## 2. launch-gemini.sh プロンプトに result.json スキーマが明示されていない

### 現状

- `skills/cross-review/scripts/launch-gemini.sh:90`
  ```
  - 投稿後、サマリを **$TMP_DIR/gemini-review-pr$STATE_PR-result.json** に書く
    （フォーマットは launch-codex.sh と同じ）
  ```
- codex 側 (`launch-codex.sh:81-90`) は JSON フィールドを inline で例示している:
  ```json
  {
    "event": "REQUEST_CHANGES",
    "posted_as": "COMMENT",
    "comments_count": 5,
    "review_url": "...",
    "by_severity": {"critical": 0, "major": 3, "minor": 2, "nit": 0}
  }
  ```
- `state.py:248` の `cmd_read_result` は `r.get("event")` 等を **codex の field 名前提で** 読む

### 観測した事象 (PR #19 Round 1)

- gemini が以下の **独自スキーマ**で result.json を生成:
  ```json
  {
    "status": "success",
    "intent": "COMMENT",
    "summary": "S3 URIのパース仕様と依存関係の案内メッセージについて、2点修正を提案します。"
  }
  ```
- `state.py read-result 19 gemini` を実行すると `intent=None posted_as=None comments=None` で
  state に登録され、judge 段階で `GEMINI_INTENT=None` (= SKIP 扱い) になりかけた
- 実際には gemini は GitHub にレビュー (id 4350631675) と inline コメント 2 件を正しく投稿していた
  ため、メインセッション側で gh api から拾い直して result.json を手書きする羽目になった

### 提案

1. **launch-gemini.sh のプロンプトに codex と同じ JSON 例を inline 展開**する
   (「launch-codex.sh と同じ」は LLM 側からは読めない参照のため不十分)
2. もしくは `state.py read-result` に **後方互換 field マップ** を入れる:
   - `event` が無く `intent` がある → `intent` を採用
   - `comments_count` が無ければ `review_url` から `gh api` で実数を拾う
3. 最小修正としては (1) で十分。長期的には (1) + (2) 両方が望ましい

## 3. fix サブエージェント戻り値ファイルのパス指定が散逸している

### 現状

- `state.py:368` (merge-fix)
  ```python
  ffile = pathlib.Path(args.file or _tmp_dir() / f"fix-pr{pr}-result.json")
  ```
- `_tmp_dir()` は環境変数 `CROSS_REVIEW_TMP_DIR` または `~/.gemini/tmp/<workspace>/` を返す
  (本検証では `/Users/takemi_ohama/.gemini/tmp/pr19/`)
- 一方、skill 本文 (`docs/02-fix-and-rotation.md` 周辺) の例や、メインセッションが Agent tool に
  渡すプロンプトでは **`/tmp/fix-pr<#>-result.json`** が登場する箇所がある (要監査)

### 観測した事象 (PR #19 Round 1)

- general-purpose サブエージェントが `/tmp/fix-pr19-result.json` に書き込み完了
- メイン側で `state.py merge-fix 19` を実行 → `❌ fix サブエージェントが戻り値ファイルを生成しなかった`
  で exit=3 (= ci-code-fail 扱い) になった
- 手動で `cp /tmp/fix-pr19-result.json $CROSS_REVIEW_TMP_DIR/` してから再実行で復旧

### 提案

1. skill docs (`docs/02-fix-and-rotation.md` および `SKILL.md` の Step 5 例) に
   **「サブエージェントに渡すパスは必ず `$CROSS_REVIEW_TMP_DIR/fix-pr<PR>-result.json`」**
   と明記し、`/tmp/...` の例を排除する
2. `state.py merge-fix` に **fallback** を 1 段追加:
   ```python
   for candidate in [args.file, _tmp_dir() / f"fix-pr{pr}-result.json",
                     pathlib.Path(f"/tmp/fix-pr{pr}-result.json")]:
       if candidate and candidate.exists() and candidate.stat().st_size > 0:
           ffile = candidate; break
   ```
   メインセッションのプロンプト誤りを救済できる
3. さらに、サブエージェントのキー名も `commit_sha`/`fixed` で来てしまうと
   `state.py merge-fix` が `fix_commit`/`fixed_count` を期待して fixed=0 と記録される
   (本検証で発生)。同じく後方互換 map を入れるのが望ましい

## 影響まとめ

| # | 影響 | 回避策 (現状) | 提案後の効果 |
|---|---|---|---|
| 1 | gemini が毎回 1 度目に STALLED で kill される | `--stall-timeout 480` を明示 | 既定で 1 発成功、孤児プロセスも減る |
| 2 | gemini 結果が state に正しく載らず judge 誤動作の恐れ | 手動で result.json を書き直す | スキーマ統一で手動補正不要 |
| 3 | merge-fix が失敗し final=error 扱いになるリスク | 手動 cp で復旧 | fallback または docs 統一で防止 |

## 関連ファイル

- `skills/cross-review/scripts/monitor.py` (#1)
- `skills/cross-review/scripts/launch-gemini.sh` (#2)
- `skills/cross-review/scripts/launch-codex.sh` (#2 参照元)
- `skills/cross-review/scripts/state.py` (#2, #3)
- `skills/cross-review/docs/01-state-and-review.md` (#1 ドキュメント追記)
- `skills/cross-review/docs/02-fix-and-rotation.md` (#3 ドキュメント追記)
- `skills/cross-review/SKILL.md` (#3 メインプロンプト例の修正)

## 補足: 本検証の環境

- macOS Darwin 25.5.0 / bash / claude-opus-4-7[1m]
- ndf plugin: `~/.claude/plugins/cache/ai-plugins/ndf/4.7.2/`
- worktree path: `/work/` が read-only のため `--worktree $HOME/cross-review-worktrees/pr19` を明示指定
  (これは別途 docs 側に「`/work/` 不在時の代替パス例」を追記すると親切)
