# NDF Plugin CHANGELOG

### v4.14.0 (statusline skill + デフォルト statusline 設定 hook 追加)

コンテナ名 (非コンテナ環境ではホスト名) + project_dir + コンテキスト使用率を表示する
NDF 標準 statusline をプラグインに同梱し、インストールするだけでデフォルトになるようにした。

- **`statusline` skill 追加** (`/ndf:statusline`、47→48個):
  - `status` (デフォルト): 現在の statusLine 設定とバックアップの有無を表示
  - `set`: 既存設定を `~/.claude/.ndf-statusline-backup.json` にバックアップして
    NDF 標準 statusline に切り替え
  - `restore`: バックアップから復元 (バックアップ無しなら statusLine キーを削除)
- **SessionStart hook 追加** (`scripts/statusline-switch.sh ensure`):
  - `statusLine` が**未設定の場合のみ** NDF 標準 statusline を設定。
    既に設定されている場合はそちらを優先し、何も変更しない
  - プラグイン同梱の `scripts/statusline.sh` を `~/.claude/ndf-statusline.sh` に
    差分時のみコピーし、プラグイン更新へ自動追従
    (settings からはプラグインキャッシュパスでなく固定パスを参照)
  - 書き込みは flock + atomic rename (ensure-retention.sh と同パターン)
- **plugin.json: version 4.13.0 → 4.14.0**。marketplace.json / README / AGENTS.md /
  ndf-plugin-reference の version 表記を整合。

### v4.13.0 (issue-plan-strategy: release PR body の self-contained 必須化)

release ブランチ戦略において、release PR の body が「個別 PR リンクの列挙 + plan 参照」
だけになり、人間のレビュアーが PR 単体で変更を把握できない問題に対応 (issue #28)。

- **レビュアー視点の原則を明文化** (`skills/issue-plan-strategy/SKILL.md` Step 3):
  - 個別 PR はセルフレビューで merge される。人間のレビュアーが見るのは release PR
    だけであり、個別 PR の存在をレビュアーに意識させない
- **release PR body テンプレートを self-contained 化**:
  - 「何のために (背景)」「何を (release 全体の変更内容)」を本文の中心に変更
  - 個別 PR チェックリストは `<details>` 折りたたみ内の開発用補足情報に格下げ
- **body 最終化ステップを Step 8 に追加**:
  - 個別 PR 全 merge 後・Draft 解除前に、実装の最終形を反映した body へ更新する
    工程とチェック観点を明記 (cross-review light rotation と同等の原則を適用)
- **アンチパターン 2 件追加**: 「body を個別 PR リンクの列挙だけにする」
  「body 最終化せずに Ready for review にする」
- **plugin.json: version 4.12.1 → 4.13.0**。marketplace.json / README / AGENTS.md /
  ndf-plugin-reference の version 表記を整合。

### v4.12.1 (cross-review デフォルト値調整・説明文整理)

`/ndf:cross-review` のローテーション/打ち切り関連デフォルトを長尺 PR 向けに緩和し、
`--rotate-mode` の説明文を整理。

- **デフォルト値変更** (`skills/cross-review/scripts/state.py`):
  - `--max-rounds` の既定値を `6 → 12` に変更
  - `--rotate-after` の既定値を `5 → 8` に変更
  - 長尺・指摘の多い PR で max_rounds 打ち切りや早期ローテーションに
    到達しにくくし、収束まで回しきれるようにする狙い
- **`--rotate-mode squash` 説明文の整理**:
  - SKILL.md / docs / scripts から「既存挙動」の文言を全削除
  - 反映: `SKILL.md` / `docs/01-state-and-review.md` (サンプル state.json) /
    `docs/02-fix-and-rotation.md` / `scripts/rotate-pr.sh`
- **plugin.json: version 4.12.0 → 4.12.1**。marketplace.json / README / AGENTS.md /
  ndf-plugin-reference の version 表記を整合。

### v4.12.0 (playwright-browser-connect / playwright-evidence-drive Skill 追加)

`/ndf:playwright-browser-connect` と `/ndf:playwright-evidence-drive` を新規 Skill
として追加。CDP リモートブラウザ接続と Google Drive エビデンスアーカイブを
playwright-scenario-test エコシステムから独立した専門 Skill に分離。

- **新規 Skill `playwright-browser-connect`**:
  - `scenario.config.yaml` の `browser.mode: cdp-remote` で既存 Chrome に
    CDP (Chrome DevTools Protocol) 接続するワークフローを提供
  - `conftest.py.template` に browser fixture を追加 (mode 切り替え)
  - `config.py` に `BrowserConfig` dataclass を追加
- **新規 Skill `playwright-evidence-drive`**:
  - テスト実行後のエビデンス (video/trace/screenshot/HAR) を Google Drive に
    自動アーカイブする手順を提供
  - 認証は `ndf:google-auth` に委譲
- **conftest.py.template の改善** (PR #19 クロスレビュー対応):
  - `browser_type_launch_args` を browser fixture の依存に追加し、
    `--headed` / `slow_mo` / `channel` 等が local モードで反映されるよう修正
  - CDP 接続時に Chromium 以外のブラウザが選択された場合の fail-fast チェックを追加
  - 変数名 `b` → `browser` に改名 (可読性向上)
- **config.py の修正** (PR #19 クロスレビュー対応):
  - `BrowserConfig.cdp_endpoint` の既定値を `ws://localhost:9222` →
    `http://localhost:9222` に変更 (Playwright の `connect_over_cdp()` は
    HTTP endpoint から `/json/version` 経由で WebSocket URL を自動解決する)
- **plugin.json: version 4.11.0 → 4.12.0**。marketplace.json / README / AGENTS.md /
  ndf-plugin-reference の version 表記を整合。
- Skills: 45個 → **47個** (main の ml-model-structure 追加分とマージ)

### v4.11.0 (fix/cross-review-early-error-and-final-sweep: cross-review 堅牢性改善)

`/ndf:cross-review` の 2 つの運用課題を修正。

- **monitor.py の EARLY_ERROR 誤検知を解消**
  (`skills/cross-review/scripts/monitor.py`):
  - `_match_is_quoted()` を拡張し、backtick / 「」 に加え **ダブル/シングルクォート
    文字列リテラル** (`"quota exceeded: ..."`) も benign 判定。
  - `EARLY_ERROR_BENIGN` に **grep/ripgrep 形式のソース引用行** (`path/file.py:22:    <code>`)
    パターンを追加。
  - codex がレビュー中に tests/*.py のテスト用文字列 (`"quota exceeded"` 等) を echo
    しても誤 kill しなくなった（PR #23 round 2 で実際に発生した事例の回帰防止テスト追加）。
- **ループ終了時の最終スイープ (Step 7.5) を必須化**
  (`skills/cross-review/SKILL.md` / `docs/02-fix-and-rotation.md`):
  - approved / max_rounds / oscillation / error いずれの終了経路でも `/ndf:fix` を再実行し、
    残った open review thread（最終 APPROVE ラウンドの minor/nit インラインコメント含む）を
    **すべて Resolve** してから終了する。deferred nit も reply + resolveReviewThread で解消。
  - 従来の「deferred nit を最後にユーザ問い合わせ」を「最終スイープで全 thread Resolve +
    report に参考列挙」へ変更。
- **plugin.json: version 4.10.0 → 4.11.0**。marketplace.json / README / AGENTS.md /
  ndf-plugin-reference の version 表記を整合（skill 数 45 は不変）。

### v4.10.0 (feature/add-ml-model-structure-skill: ml-model-structure skill 追加)

機械学習モデル構築・推論API開発の標準ディレクトリ構造を提供する新規 skill を追加。
skill 数を 45 個に更新し、関連ドキュメントのバージョン・skill 数表記を整合。

- **新規 skill `ml-model-structure` 追加**
  (`skills/ml-model-structure/`):
  - 機械学習モデルの版ごと自己完結構造 + 版内 feature SSoT + 推論コンテナ規約。
  - `references/inference-and-contract.md` に train↔serve 契約・推論コンテナ規約を分離。
- **plugin.json: version 4.9.0 → 4.10.0、skills 配列に `ml-model-structure` を追加**、
  description を 45 skills に更新。
- **marketplace.json / README / AGENTS.md / ndf-plugin-reference: skill 数・version 表記を整合**。

### v4.7.6 (fix/review-pr-comments: PRコメント取得を3ソース対応に拡張)

PRレビューコメント取得が review body / PR レベルコメントを含む3ソースに対応。
cross-review の state.py も堅牢性改善。

- **fetch-pr-comments.sh: 3ソース対応の新規スクリプト追加**
  (`skills/fix/scripts/fetch-pr-comments.sh`):
  - review comments / review body / PR-level comments の3ソースから取得。
  - `gh api` の `-q` フラグ誤用を修正し raw JSON → `jq -r` パイプに統一。
  - 取得失敗時に `die()` で中断するエラーハンドリング追加。
- **state.py: 再開検知・パス解決の堅牢化**
  (`skills/cross-review/scripts/state.py`):
  - `cmd_init` の再開検知で `CROSS_REVIEW_TMP_DIR` 環境変数を考慮。
  - `_state_path()` が `_tmp_dir()` を直接使用するよう修正（読み込み元と参照先の乖離防止）。
  - `_resolve_tmp_dir` から mkdir 副作用を除去（`p.exists()` チェックに変更）。
  - `st.get()` の None フォールバックを `or ""` で安全化。
- **fix/SKILL.md, review-pr-comments/SKILL.md: 3ソース対応の説明更新**。
- **cross-review SKILL.md, docs: パス記述の微修正**。

### v4.7.5 (cross-review: TMP_DIR を worktree 内 .cross_review/ に統一)

レビュースクリプトが一時ファイルを書き出す先を、従来の `~/.gemini/tmp/` や `/tmp/`
から **worktree 内 `.cross_review/`** に統一する PATCH リリース。

- **_tmpdir.sh: `_tmp_dir()` の解決先を `.cross_review/` に変更**
  (`skills/cross-review/scripts/_tmpdir.sh`):
  - workspace 未指定時に `git rev-parse --show-toplevel` でフォールバックし、
    worktree ルート直下の `.cross_review/` を使用。
  - worktree 外のゴミファイル散乱と、環境依存のパス不整合を解消。
- **launch-codex.sh / launch-gemini.sh: TMP_DIR パス統一**:
  - 各ランチャーが `.cross_review/` を前提とするよう修正。
- **monitor.py / state.py: パス参照の整合**:
  - `$TMP_DIR` 解決先の変更に伴い、ファイル探索パスを `.cross_review/` 体系に統一。
- **SKILL.md / docs: ドキュメントのパス記述統一**:
  - `SKILL.md`、`docs/02-fix-and-rotation.md`、`skills/merged/SKILL.md` 内の
    旧パス記述を `.cross_review` 体系に修正。
- **.gitignore: `.cross_review/` を追加**。

#### 既存ユーザへの影響

- `CROSS_REVIEW_TMP_DIR` env を明示しているユーザ: **挙動不変**。
- 未指定ユーザ: 一時ファイルの書き出し先が `~/.gemini/tmp/` → worktree 内
  `.cross_review/` に変更。worktree 内に閉じるため管理が容易になる。

### v4.7.4 (cross-review: gemini stall 既定の per-agent 化 + fix 戻り値マージの堅牢化)

`/ndf:cross-review` を実走させた際に、cross-review ループ自体は完走するが
メインセッション側で手動補正が必要だった運用不整合の残り 2 件を恒久対応する
PATCH リリース。

- **monitor.py: per-agent stall timeout 既定の導入** (`skills/cross-review/scripts/monitor.py`):
  - 従来は `MONITOR_STALL=180s` の単一既定値だったため、err.log にほぼ進捗を出さない
    gemini を 1 度目に毎回 STALLED 扱いで kill していた (孤児プロセスも残存)。
  - 既定を agent 別ビルトインに変更: **codex=180s (変更なし) / gemini=480s**。
  - 解決順: CLI `--stall-timeout` (明示優先) > env `MONITOR_STALL_<AGENT>` (per-agent) >
    env `MONITOR_STALL` (両 agent 共通、後方互換) > agent 別ビルトイン。
  - `--stall-timeout` の argparse default を `int` → `None` に変更し、未指定時のみ
    per-agent 解決を行う。
- **state.py merge-fix: fix 戻り値ファイルの探索 fallback と key 別名対応**
  (`skills/cross-review/scripts/state.py`):
  - サブエージェントが `/tmp/fix-pr<PR>-result.json` に書き、`merge-fix` は
    `$TMP_DIR` (= `~/.gemini/tmp/<workspace>/`) のみ参照する不整合で
    exit 3 (ci-code-fail 扱い) になる事象を解消。探索順を **(1) `--file` 明示
    → (2) `$TMP_DIR/` → (3) `/tmp/`** の 3 段に変更。
  - 全候補不在時の die メッセージに探索 path 一覧を含め、原因切り分け可能にした。
  - サブエージェントが `commit_sha` / `fixed` 別名キーで書き出した場合も
    `fix_commit` / `fixed_count` と同等に受理する key alias を追加 (silent な
    `fixed=0` 記録の救済)。
- **SKILL.md / docs のパス記述統一**:
  - `SKILL.md` 内の `/tmp/fix-pr<#>-result.json` ハードコード 3 箇所を
    `$TMP_DIR/fix-pr<#>-result.json` に統一。
  - 「すべて `/tmp/` に置き」の汎用記述も `$TMP_DIR/` (= `_tmp_dir()` 解決先) に修正。
  - `docs/02-fix-and-rotation.md` に `$TMP_DIR` の解決順
    (env `CROSS_REVIEW_TMP_DIR` > `~/.gemini/tmp/<workspace>/` > `/tmp/`) を 1 行明示。
  - `docs/01-state-and-review.md` の monitor 説明表に per-agent stall 既定の
    解決順を追記。
- **pytest 追加** (`skills/cross-review/tests/`):
  - `test_monitor_stall_default.py` — codex/gemini ビルトイン + env 上書き 6 ケース。
  - `test_state_merge_fix.py` — 正規 / `/tmp/` fallback / key 別名 / 全候補不在 /
    `--file` 明示の 5 ケース。
- **関連 issue / plan**:
  - `issues/i18-issue-gemini.md` (再現報告) /
    `issues/PLAN21_cross-review-gemini-stall-and-fix-merge.md` (実装プラン)。

#### 既存ユーザへの影響

- `--stall-timeout` を CLI で明示しているユーザ: **挙動不変**。
- `MONITOR_STALL` env を指定しているユーザ: **挙動不変** (両 agent に同じ値が適用)。
- 何も指定していないユーザ: gemini 側の既定 stall timeout が 180s → 480s に
  **緩和**される (kill されにくくなる方向のため非破壊)。
- 正規パス (`$TMP_DIR/fix-pr<PR>-result.json`) + 正規 key (`fix_commit` /
  `fixed_count`) で書き出していたユーザ: **挙動不変**。
- 旧プロンプトで `/tmp/` ハードコードや別名 key を使っていたユーザ: **silent fail
  していたケースが拾われるようになる** (修復方向の変更)。

### v4.7.3 (cross-review: macOS 対応 worktree base + result.json スキーマ堅牢化)

`/ndf:cross-review` を非 Linux コンテナ環境 (macOS / WSL 等) でも素直に動かせるよう、
worktree のデフォルトパス解決を環境適応型に変更。あわせて gemini が変則スキーマで
result.json を書き出すケースで intent が silent に None マージされて judge が空回り
する不具合を恒久対応する PATCH リリース。

- **worktree デフォルトパスの環境適応** (`skills/cross-review/scripts/state.py`):
  - `state.py init` が以下の優先順で worktree 親ディレクトリを解決:
    1. `NDF_WORKTREE_BASE` 環境変数 (明示オーバーライド)
    2. `/work/worktrees` (Linux コンテナ環境互換。書込可ならこちらを使用)
    3. `$HOME/work/worktrees` (macOS / WSL 等のフォールバック)
  - 既存の Linux コンテナ環境では `/work/worktrees` が引き続き使われ挙動不変。
  - SKILL.md / docs から `/work/worktrees/pr<PR>` のハードコードを除去し、
    `<worktree-base>/pr<PR>` 表記に統一 (state.json サンプル中の解決例 1 箇所のみ残存)。
- **gemini result.json スキーマの明示化** (`skills/cross-review/scripts/launch-gemini.sh`):
  - 「フォーマットは launch-codex.sh と同じ」という曖昧指示を、codex と同一の
    フィールド列挙ブロック (`event` / `posted_as` / `comments_count` / `review_url` /
    `by_severity`) に置き換え。`intent` / `comment_count` 等の別名を使わないことを明記。
- **`state.py read-result` の堅牢化**:
  - 仕様 (`event` / `comments_count`) を優先しつつ、別名 (`intent` / `comment_count`)
    も拾えるようフォールバックを追加。
  - `event` / `intent` いずれも欠落している場合は `die()` (exit 1) で fail する。
    旧挙動 (silent な `intent=None` マージで judge が空回り) は **破壊的に修正**。
- **monitor.py EARLY_ERROR 誤検知の修正** (`skills/cross-review/scripts/monitor.py`):
  - SKILL.md / `docs/01-state-and-review.md` の Markdown 表セル内で FATAL キーワード
    (`「quota exceeded」`「sandbox error」等) を列挙しており、codex がレビュー時に
    それを echo すると err.log 上で `_scan_early_fatal()` が誤発火してプロセスを
    kill していた。以下 2 段の防御で恒久対応:
    1. `EARLY_ERROR_BENIGN` に Markdown 表セル行 (`^\|`) を追加。
    2. マッチ位置が backtick / 日本語「」で引用されている場合に benign 扱いする
       `_match_is_quoted()` ヘルパを追加し、`_scan_patterns()` から呼ぶ。
  - FATAL パターンから `^.*` プレフィックスを外し、`m.start()` をキーワード位置に
    合わせて引用判定が機能するように修正。
- **pytest 追加** (`skills/cross-review/tests/`):
  - `test_state_read_result.py` — 正規/変則/欠落スキーマ 4 ケース。
  - `test_default_worktree_base.py` — env / legacy / fallback の 3 ケース。
  - `test_monitor_early_error.py` — Markdown 表 / backtick / 日本語クォート引用の
    benign 判定と、本物 fatal が依然検知される回帰テスト 7 ケース。
  - ローカル実行: `uv run --with pytest pytest plugins/ndf/skills/cross-review/tests`。
- **関連 issue / plan**:
  - `issues/i17.md` (再現報告) / `issues/PLAN20_cross-review-worktree-and-result-schema-fix.md` (実装プラン)。

#### 既存ユーザへの影響

- `/work/worktrees` が書ける環境 (大半の Linux コンテナ環境): **挙動不変**。
- macOS / WSL 等で `/work` が書けない環境: `--worktree` 引数なしでも
  `$HOME/work/worktrees/pr<PR>` に自動フォールバックして init が成功する。
- gemini が変則スキーマ (`intent` / `comment_count`) で result.json を書く現象を
  観測していたユーザ: フォールバックで自動的に取り込まれるようになる。
- `result.json` から `event` / `intent` が両方欠落しているケースは exit 1 で
  早期 fail する (旧: judge 段階まで silent に None が伝播)。

### v4.7.0 (fix / cross-review: 修正ポリシー刷新 + CI 完了待ち廃止)

`/ndf:fix` と `/ndf:cross-review` の修正方針を見直し、PR の最終的なコード品質を
上げる方向にシフトする MINOR リリース。`/ndf:fix` の自動実行範囲が広がるため、
利用側スクリプトが `--severity-min nit` 相当の挙動を前提にしている場合は要確認。

- **修正対象の拡張** (`skills/fix/SKILL.md`):
  - minor / nit のうち **パフォーマンス・可読性・重複コード排除** に該当する指摘は
    このPR内で対応する (旧: nit は基本 deferred)。
  - 特にトータルのコード行数が減る方向の修正 (重複排除 / 不要分岐除去) は積極実施。
  - ただし修正範囲が **+30 行を超えそうな場合は deferred + ユーザ問い合わせ**
    （スコープ膨張による副作用とレビュー負担を抑える）。
- **重要度ラベルの独自再判定**:
  - AI agent (CodeRabbit / Copilot / codex / gemini) が付けた `[critical/major/minor/nit]`
    ラベルを鵜呑みにせず、コード本体を読んだ上でカテゴリ
    (performance/readability/duplication/security/style/...) と合わせて再判定する。
  - 例: AI が `nit` と付けていても実体が重複排除なら修正対象。AI が `critical` と
    付けていても実害がないスタイル指摘なら deferred 化してよい。
- **CI 完了待ちを廃止**:
  - `/ndf:fix` 内の `gh pr checks --watch` および "PENDING を完了まで待つ" 手順を削除。
  - 各チェックポイントでは **その時点で FAILURE のジョブのみ** を修正対象に取り込み、
    実行中チェックは無視して次ステップへ進む。
  - 戻り値 `ci_status` / `ci_failed_checks` は push 時点での既知失敗のみを反映する
    （メイン context の節約と、長時間ブロック回避が目的）。
  - cross-review 側の judge ロジック (`state.py`) は変更不要 — `ci_status != FAILURE`
    なら継続判定するため、PENDING も成功扱いになる。
- **PR テスト範囲外の flaky テストも修正対象**:
  - 放置するとリポジトリ全体の CI 信頼性が劣化し、後続 PR にも波及するため、
    `/ndf:fix` 実行時に見つけ次第このループで修正する。
- **作業完了報告に PR URL 必須**:
  - `/ndf:fix` の最終報告末尾に `https://github.com/<owner>/<repo>/pull/<番号>` を
    必ず記載する（メインからの追跡性向上）。
- **`cross-review` 側の同期**:
  - `skills/cross-review/docs/02-fix-and-rotation.md` のサブエージェント起動プロンプト
    （ポリシーと「必須実行手順」）を上記方針に合わせて更新。
  - 手順 3 を「CI 状態スナップショット (完了待ちしない)」、手順 7 を
    「CI 再実行は待たない」に書き換え。SKILL.md の手順番号繰り上がり
    (旧 8 → 新 7) も反映。

#### 既存ユーザへの影響

- `/ndf:fix` 単体実行: minor/nit カテゴリのうち performance/readability/duplication
  に該当するものが自動修正されるため、これまで deferred だった指摘が修正コミットに
  入る場合がある。+30 行を超える場合はユーザ問い合わせで止まる。
- `/ndf:cross-review` 自動ループ: CI 完了を待たなくなった分、各 round の所要時間が
  短縮される。一方で push 直後の CI 失敗は次 round の review 段階で再検出される。
- `result.json` の `ci_status` が `PENDING` になる頻度が増える。下流で `ci_status`
  を見ているスクリプトがある場合は確認推奨（state.py の judge は変更不要）。

### v4.6.2 (cross-review: state.py init の TMP_DIR 計算順序バグ修正 + AGENTS.md リネーム)

`/ndf:cross-review` で gemini が **workspace 制約違反で payload を書けず
hard timeout (420s) で常時失敗** していた不具合を修正する PATCH リリース。
合わせて `claude plugin validate` の警告 (plugin root の `CLAUDE.md` は
project context として読み込まれない) に従い `plugins/ndf/CLAUDE.md` を
`plugins/ndf/AGENTS.md` にリネーム。

- 修正 (`skills/cross-review/scripts/state.py` `cmd_init`):
  - 旧実装は `_tmp_dir(args.worktree)` を `args.worktree=None` のまま呼び、
    `os.getcwd()` の basename (= 親リポジトリ名) で `~/.gemini/tmp/<repo>/`
    を採用していた。一方、`launch-gemini.sh` は `cd "$WORKTREE"` してから
    gemini を起動するため、gemini は `~/.gemini/tmp/<worktree-basename>`
    (= `~/.gemini/tmp/pr<PR>`) しか workspace 内として許可せず、
    `write_file` が `Path not in workspace` で拒否されていた。
  - 修正後は `worktree = args.worktree or f"/work/worktrees/pr{pr}"` を
    先に解決してから `_tmp_dir(worktree)` に渡し、tmp_dir の basename を
    worktree basename と一致させる。
  - 副次効果: `state_file` の path も `~/.gemini/tmp/pr<PR>/` 配下に揃うため、
    cross-review が PR ごとに完全に分離した tmp 空間で動く。
- リネーム: `plugins/ndf/CLAUDE.md` → `plugins/ndf/AGENTS.md`。
  Claude Code は plugin root の `CLAUDE.md` を project context として
  読み込まないため、`claude plugin validate` が警告を出していた。
  リポジトリ root の `AGENTS.md` (本体) + `CLAUDE.md` (Claude 固有) と
  揃え、プラグイン側は `AGENTS.md` に一本化する。
  - `plugins/ndf/README.md` のリンクも更新。
  - `claude plugin validate` の警告 0 件を確認。

#### 既存ユーザへの影響

- 旧 tmp_dir (`~/.gemini/tmp/<repo>/`) に途中状態 (`state.json`) が残っている
  場合、v4.6.2 以降は `~/.gemini/tmp/pr<PR>/` を参照するため state を見失う。
  対応: `mv ~/.gemini/tmp/<repo>/cross-review-pr<PR>-* ~/.gemini/tmp/pr<PR>/`
  で移行するか、`/ndf:cross-review <PR>` を再 init する。
- cross-review は 1 PR 単位の短命ステートのため、影響は実行中ループのみ。

### v4.6.1 (cross-review skill 主要処理のスクリプト化)

`cross-review` skill の主要 bash 処理を `scripts/` 配下に外出し、SKILL.md /
docs/01,02 から冗長なインライン bash を排除する PATCH リリース。
SKILL の I/O 契約 (state.json / result.json / payload.json スキーマ) は不変。

- 新規追加:
  - `scripts/state.py` — state.json 操作 CLI (uv 自己完結 / stdlib のみ)
    サブコマンド: `init` / `start-round` / `read-result` / `judge` /
    `check-oscillation` / `merge-fix` / `should-rotate` / `set-current-pr` /
    `report`
  - `scripts/launch-codex.sh` / `scripts/launch-gemini.sh` — レビューランチャ
    (pidfile + sentinel ベース、trusted directory 対策込み)
  - `scripts/monitor.py` — codex/gemini プロセス多軸監視 CLI
    (uv 自己完結 / stdlib のみ)。pidfile + `/proc` cmdline 検証 / codex sentinel /
    早期エラーパターン検出 / err.log stall timeout / hard timeout / result.json
    存在確認の 6 軸を並列スレッドで判定。exit code で失敗種別を区別
    (OK=0 / TIMEOUT=2 / NO_RESULT=3 / EARLY_ERROR=4 / STALLED=5 / PIDFILE_BAD=6)。
    sentinel 単独で完了判定する旧 `wait-review.sh` の取りこぼし
    (codex クラッシュ時の無限ハング、gemini の untrusted directory 静かな失敗、
    pidfile stale 等) を解消。
  - `scripts/wait-review.sh` — `monitor.py` の薄ラッパ（旧 CLI 互換のため残置）
  - `scripts/rotate-pr.sh` — PR ローテーション (squash + 新ブランチ + 新 PR)
- SKILL.md / docs/01,02 を「スクリプト呼び出し」形式に置換。state.json と
  result.json のスキーマは docs に残し、実装は scripts/ にカプセル化。

PR #72 の実機テストで得た codex / gemini からの指摘および追加で見つかったバグの
対応（同 v4.6.1 内で実施）:

- **`monitor.py`**:
  - cmdline 検証順序を「alive 確認後のみ」に変更。プロセスが既に死んでいる場合は
    cmdline 不一致でも PIDFILE_BAD にならず、result.json の有無で OK 判定する
    (旧実装は完了済 launcher を誤って失敗扱いしていた)。
  - EARLY_ERROR パターンを **行頭限定** + benign フィルタに改修。diff / doc 引用に
    `401 Unauthorized` などのキーワードが含まれても誤検知しなくなった。
  - TIMEOUT / STALLED / EARLY_ERROR / PIDFILE_BAD で返るとき、対象プロセスに
    SIGTERM (3 秒後に SIGKILL) を送信。残存プロセスが後から `gh api` 投稿や
    result.json 書き込みを行ってメインと競合する問題を解消。
  - stall 判定を err.log のみから **err.log + stdout.log の合計サイズ** に拡張。
  - **デフォルト値変更**: hard timeout 30 分 → **7 分**、stall timeout 10 分 → **3 分**。
  - 未使用 import `field` を削除。
- **`state.py`**:
  - `gh api --paginate` の JSON ストリーミング処理を `--jq` ベースに変更
    (旧: `json.loads(r.stdout)` は複数ページで JSONDecodeError → 空配列 →
    既存コメントスナップショットが空になり重複指摘禁止が無効化されていた)。
  - `st["rounds"][-1]` への参照前に空チェックを追加し、初期化失敗時の
    IndexError を防止 (read-result / judge / merge-fix の 3 箇所)。
  - 未使用 import `os` を削除。
- **`launch-codex.sh` / `launch-gemini.sh` / `rotate-pr.sh`**:
  - 引数を `STATE_PR` (= state.json の key, 初期 PR) に統一。レビュー対象の
    「現在の PR」は state.json の `current_pr` を内部で読む。
    旧実装は PR rotation 後にメイン側で `PR=$NEW_PR` に切り替えると state.json
    パスが見つからなくなる設計矛盾があった。
- **tmp ディレクトリの gemini workspace 制約対応**:
  - 全 scripts の tmp パスを `/tmp/` 直書きから `$CROSS_REVIEW_TMP_DIR` 経由に変更。
    未設定なら `~/.gemini/tmp/<workspace-basename>/` を自動採用、最終フォールバックは `/tmp/`。
    gemini CLI は `--yolo --skip-trust` でも workspace 外の `read_file` /
    `write_file` がブロックされる (`Path not in workspace`) ため、gemini 公式の
    project temp directory に揃えることで result.json / payload.json の書き出しを
    成立させる。
  - 共通ヘルパ `scripts/_tmpdir.sh` を追加 (bash) / `state.py` と `monitor.py` に
    `_tmp_dir()` 関数を追加 (Python)。`state.py init` は採用した `TMP_DIR` を
    state.json に記録し、stdout の `TMP_DIR=` で呼び出し側に通知。
  - SKILL.md のテンプレートで `eval "$(state.py init ...)"` 後に
    `export CROSS_REVIEW_TMP_DIR="$TMP_DIR"` を行い、後続スクリプトに env として
    伝播させる手順を追加。
- **`SKILL.md` / `docs/01,02`**:
  - bash テンプレートを `$STATE_PR` 固定で書き直し、rotation 後も同じ変数で
    全 scripts を呼ぶ手順に統一。
  - 新デフォルト (timeout=7 分 / stall=3 分) を反映。

### v4.6.0 (cross-review skill 改訂 + review/fix の result.json 拡張)

実運用で得た失敗パターンの対策を `cross-review` skill に反映し、関連する
`/ndf:review` と `/ndf:fix` の result.json schema を拡張する MINOR リリース。

- **`cross-review` skill 改訂**:
  - **「事前確認」セクション新設**: ループ開始前の 4 プリチェック
    1. 自分の PR 判定 → `event` ダウングレード設定 (GitHub の
       `HTTP 422: Can not request changes on your own pull request` を回避)
    2. **worktree 分離** (`/work/worktrees/pr<PR>`) で並行セッション競合回避
    3. **gemini の trusted directory 対策**:
       `GEMINI_CLI_TRUST_WORKSPACE=true` + `--skip-trust` 両方併用必須
       (worktree のような新規パスは untrusted 判定 → YOLO が "default" に降格される)
    4. 既存コメントスナップショット保存 → launcher プロンプトに添付して重複指摘禁止
  - **state.json schema 拡張**:
    - `worktree_path` / `pr_author` / `is_own_pr` / `event_downgrade` を追加
    - 各 round の `codex` / `gemini` を **`intent` + `posted_as` の二重保持** に変更
      (ループ収束判定は `intent`、GitHub 投稿実体は `posted_as`)
    - 各 round に `by_severity` (`{critical, major, minor, nit}` 件数) を追加
    - 各 round の `fix` に `resolved_threads` / `ci_note` を追加
  - **レビュー body 必須 identifier prefix**:
    `## 🤖 cross-review | round <N> | <agent> | <event(intent)>` を先頭固定化
    (人間アカウントから AI が投稿するため、GitHub UI で発信元を可視化)
  - **CI failure の分類**: `pint/larastan/test/build/lint/type/tsc/mypy` 等は
    code-fail として中断、`check_pr_requirements/assignees/reviewers/labels`
    等のメタチェックのみ失敗ならループ継続
  - **waiter を pidfile + sentinel ベースに**:
    - codex は `^tokens used$` sentinel
    - gemini は long `-p` プロンプトが引数に乗るため `pgrep -fa` 不可、
      `pidfile + kill -0` ポーリング必須
  - **`Step 5` サブエージェント責務を 5 点明示**:
    修正コミット / テスト / **reply + `resolveReviewThread` で Resolve** /
    deferred は記録のみ・Resolve しない / 戻り値ファイル書き出し
- **`/ndf:review` SKILL.md 更新**:
  - 結果サマリ (`/tmp/<agent>-review-pr<番号>-result.json`) に `posted_as`
    フィールドを追加
  - `event` (intent / 本来の判定) と `posted_as` (実投稿) の使い分けを文書化
  - 自分 PR ダウングレードフローを `intent="REQUEST_CHANGES"` /
    `posted_as="COMMENT"` で記録する手順を明記
- **`/ndf:fix` SKILL.md 更新**:
  - 戻り値ファイル `/tmp/fix-pr<番号>-result.json` に
    `resolved_threads` (配列) / `ci_failed_checks` (配列) / `ci_note` (string)
    を追加
  - 手順 12 に「resolve した thread_id / comment_id / path / line を
    `resolved_threads[]` に記録」「`deferred` / `rejected` の thread は
    Resolve しない」を明記
  - 手順 13 に `ci_failed_checks` の収集元 (`gh pr checks <PR> --json name,state`)
    を補記
- **アンチパターン 5 件追加**:
  - 自分の PR に `REQUEST_CHANGES` で投稿
  - `gemini --yolo` 単独起動 (`--skip-trust` 併用必須)
  - `pgrep -fa <prompt>` で完了判定
  - fix サブエージェントが Resolve をスキップ
  - review body に identifier prefix を付け忘れる
- Skills: 39個 (変化なし、`cross-review` / `review` / `fix` の中身を更新)

### v4.5.0 (playwright-scenario-test v0.5.0 — Skill 非依存 self-contained 構成 / 名前空間 rename)

> **注意**: 互換性破壊リリース。Python パッケージ名・fixture 名・CLI option・
> 環境変数・内部クラス名がすべて変わる。既存利用者は manual に書き換え必要。
> 詳細は [PLAN19](../../issues/PLAN19.md) を参照。

- **`playwright-scenario-test` v0.5.0** (Skill 非依存化 + 名前空間整理):
  - **目的**: 旧 v0.4.0 までは Skill ディレクトリで `uv sync` する必要があり、
    Skill が消えるとテストが動かない / CI 別マシンで再現性が低い問題があった。
    v0.5.0 では `scripts/init_project.sh` で **利用者プロジェクト直下に
    `scenario-test/` (all-in-one ランタイム)** を埋め込み、Skill 非依存で動作させる。
  - **新規 init / launcher**:
    - `scripts/init_project.sh` / `scripts/init_project.bat`:
      `<PROJECT_ROOT>/<runtime-dir>/` に playwright_kit / scripts / uv.lock /
      runtime templates を rsync ベースでコピーし、初回 uv sync +
      playwright install chromium まで実行。`--runtime-dir <name>` で配置先名
      カスタマイズ可、`--dry-run` で予定差分のみ表示、`scenario.config.yaml` /
      `tests/test_*.py` は既存があれば上書きしない (利用者編集物保護)
    - `templates/run.sh` / `templates/run.bat`:
      `$(dirname BASH_SOURCE)` / `%~dp0` で自身の位置を解決し CWD をランタイム
      内に固定。初回のみ `uv sync` + `playwright install chromium`。
      `--help` / `$@` で pytest 引数素通し
    - `templates/pyproject.toml.runtime` / `templates/runtime-gitignore` /
      `templates/runtime-README.md`: 利用者プロジェクト埋め込み用テンプレート
  - **破壊的 rename** (Phase 0 / 0a):
    - **Python パッケージ**: `scenario_test` → `playwright_kit`
    - **pytest entry-point**: `ndf-scenario-test` → `playwright-kit`
    - **fixture**: `ndf_config` → `pwk_config`, `ndf_role_<id>` →
      `pwk_role_<id>`, `ndf_evidence` → `pwk_evidence`,
      `ndf_a11y_scan` → `pwk_accessibility_scan`,
      `ndf_cwv_measure` → `pwk_web_vitals_measure`,
      `ndf_body_check_scan` → `pwk_body_check_scan`,
      `ndf_out_dir` → `pwk_out_dir`
    - **CLI option**: `--ndf-config` → `--pwk-config`, `--ndf-out-dir` →
      `--pwk-out-dir`, `--ndf-no-evidence` → `--pwk-no-evidence`,
      `--ndf-har-mode` → `--pwk-har-mode`, `--ndf-hud` → `--pwk-overlay`,
      `--ndf-drive-folder` → `--pwk-drive-folder`
    - **env var**: `NDF_CONFIG` → `PWK_CONFIG`
    - **内部クラス**: `NdfTestEntry` → `PwkTestEntry`, `NdfEvidence` → `PwkEvidence`
    - **モジュール rename** (Phase 0a, ドメイン用語の整理):
      - `scenario_test/a11y.py` → `playwright_kit/accessibility.py`
        (a11y は WCAG ドメイン用語のため平易な英語に)
      - `scenario_test/cwv.py` → `playwright_kit/web_vitals.py`
        (CWV → Core Web Vitals)
      - `scenario_test/hud.py` → `playwright_kit/overlay.py`
        (HUD = Heads-Up Display は造語的、overlay の方が直観的)
    - **config schema**: `accessibility:` / `web_vitals:` キーに統一
      (旧 `a11y:` / `cwv:` は廃止)
    - **EvidenceCollectors fields**: `cwv_metrics` → `web_vitals_metrics`,
      `cwv_passed` → `web_vitals_passed`
  - **保持するもの** (W3C / 業界標準):
    - `LCP` / `CLS` / `TTFB` / `longest_task` / `HAR` / `axe-core` —
      データフィールド名・外部仕様名としてそのまま使用 (各 docstring と
      SKILL.md の用語集セクションで正式名称を併記)
  - **SKILL.md / docs**: クイックスタートを `init_project.sh → run.sh` フローに
    全面書き換え、用語集セクションを SKILL.md 上部に新設、ディレクトリ図を
    rename 後 + init 後構造に更新、開発者向け「Skill 単体で uv sync する旧運用」
    節を別出し
  - **検証**:
    - 159 件 pure 関数テスト pass (config / fixtures / pytest plugin / report 全般)
    - 擬似環境 (`/tmp/...`) に init 後、Skill ディレクトリを `mv` で隠した
      状態で `./scenario-test/run.sh --collect-only` が完走 (8 件 collect, exit 0)
    - `--runtime-dir e2e` 配置で複数ランタイム共存 (`scenario-test/` + `e2e/`)
      が独立に動作
    - 再 init で `tests/test_*.py` の利用者編集が保護される (skip)
    - `--dry-run` で実際にはコピーされない
- Skills: 39個 (変化なし、playwright-scenario-test の中身が刷新)

### v4.4.0 (issue-plan-strategy skill 追加)

- **新規 Skill `issue-plan-strategy`**:
  - 1 つの issue から plan を起こし、推奨 PR が複数に分かれる場合の標準ワークフローを規定
  - スラッシュコマンド (`/ndf:issue-plan-strategy <issue-path-or-url>`) でも、
    **(a) issue から plan 作成依頼** / **(b) 既存 plan の実装(実行)依頼** の両方で自動発動する
    (description / Triggers に発動条件を明記)
  - Step 0 で plan ファイル / release branch / Draft PR の有無を見て **作成フェーズ vs 実行フェーズ**
    を切り分け、実行フェーズで入った場合は Step 1 をスキップして Step 3 以降の自動化に直行
  - フロー: issue 取得 → (作成) plan (必要なら plan モード) → 単一/複数 PR 判定 →
    (実行) release branch + Draft release PR 先行作成 → 個別 PR ブランチ + Draft PR 先行作成 →
    git worktree で並行開発 → 個別 PR レビュー (`/ndf:review` / `/ndf:cross-review`) →
    release ブランチで結合テスト相当のレビューのみ → release → default merge
  - 検証環境 (qa/staging) は個別 PR or release PR 単位で `/ndf:cherry-pick-pr` に委譲
  - 関連 skill (`implementation-plan` / `branch-fix-strategy` / `pr` / `cherry-pick-pr` /
    `review` / `cross-review` / `playwright-scenario-test`) との責務分担を明記し、
    本 skill は **multi-PR 運用のメタ手順**に専念
- Skills: 38個 → **39個**

### v4.3.1 (Fix: cross-review / gemini skill 登録漏れ修正)

- PR #67 で追加された `cross-review` / `gemini` skill が `plugin.json` の
  `skills` 配列に登録されておらず、`/ndf:cross-review` / `/ndf:gemini` を
  slash command として呼び出せなかった問題を修正
- 併せて description と CLAUDE.md / AGENTS.md / README.md の skill 数を
  36個 → 38個 に更新
- Skills: 36個 → **38個** (実体は v4.3.0 時点で既に存在、登録のみ追加)

### v4.3.0 (playwright-scenario-test v0.4.0 — body_check 復活)

- **`playwright-scenario-test` v0.4.0**: 旧 v0.2.x の自前 YAML runner にあった
  **`body_check`** 機能 (PHP / SSR がフロントに漏れさせる ``Fatal error`` /
  ``Uncaught`` / ``STRICT:`` / ``Warning:`` / ``Notice:`` / ``File not found``
  等を **テスト失敗として検出**) を **default 有効** で復活させた。
  まだ開発・検証段階の skill のため互換性は重視せず、config 無記述でも
  PHP プロジェクトで素直に効くよう default で `enabled=True` + PHP 系
  パターンを内蔵する。詳細は [PLAN18](../../issues/PLAN18.md) を参照。
  - **新モジュール**:
    - `scenario_test/body_check.py` — 純粋関数 `scan_body` / `is_html_response`
      と `BodyViolation` dataclass。substring match で fatal / warning /
      not_found カテゴリの violation を返す
    - `scenario_test/fixtures/body_check.py` — autouse fixture
      (`_ndf_body_check_autouse`) と明示呼び出し用 helper (`ndf_body_check_scan`)。
      `page.on("response", ...)` で全 HTML レスポンスを監視し、teardown で
      `case_dir/body_check.jsonl` に書き出してから violation 数次第で
      `pytest.fail`
  - **新 config schema** (`scenario.config.yaml`, 省略可):
    ```yaml
    body_check:
      enabled: true                    # default: true (機能無効化したい場合のみ false)
      fatal_patterns: ["Fatal error", "Uncaught", "Parse error"]
      warning_patterns: ["STRICT:", "Warning:", "Notice:", "Deprecated:"]
      warning_head_chars: 300          # warning_patterns は本文先頭 N 文字のみ走査 (PLAN18 のフィールド名 warning_head_bytes も alias で受理)
      not_found_patterns: ["File not found"]
      fail_on_match: true              # false で情報収集モード
    ```
    各キーは**省略すると dataclass の default**が効く (PHP 系パターン内蔵)。
    明示的に空リストを書けばそのカテゴリのみ無効化できる。
  - **新 marker**: `@pytest.mark.no_body_check` で個別テスト opt-out
  - **report.md**:
    - サマリ表に `body_check` カラムを追加 (違反件数)
    - 違反があれば「body_check 違反の詳細」セクション (URL / pattern / snippet)
      を生成 (PASS でも `fail_on_match=false` の情報収集モードで表示)
  - **設計ポイント**:
    - 既存利用者の test 挙動を変えないため `body_check.enabled` の default は
      **False** (opt-in)。設定を書かない限り従来挙動 (検出ロジックなし) が維持される
    - autouse fixture は a11y / cwv と同じく `request.fixturenames` ガードで
      `page` を要求していない test を skip
    - body_check が teardown で `pytest.fail` した場合、call phase は passed
      のまま teardown report が failed/error になるため、`_collect_entries` で
      teardown 失敗を call entry に反映 (`outcome` を passed → failed に昇格)
  - **検証**: 既存 + body_check / report / collect_entries 昇格 / sessionfinish
    upload を含めて **147 件 pure 関数テスト pass** (`uv run pytest -q`)
- Skills: 36個 (変化なし、playwright-scenario-test の中身に opt-in 機能追加)

### v4.2.0 (playwright-scenario-test v0.3.0 — pure pytest-playwright 完全移行)

> **注意**: 互換性破壊リリース。v0.2.5 までの自前 YAML DSL は **完全廃止** し、
> 利用者は通常の pytest-playwright テストを書く形に移行する。詳細は
> [PLAN17](../../../issues/PLAN17.md) を参照。

- **`playwright-scenario-test` v0.3.0** (pure pytest-playwright):
  - **アーキテクチャ全面刷新**: 自前 DSL (testcase YAML / runner / dispatcher /
    locator_steps / cli) をすべて削除。代わりに pytest plugin として実装し、
    利用者は `def test_xxx(page, ndf_role_admin): ...` を直接書く
  - **新モジュール**:
    - `scenario_test/pytest_plugin.py` — pytest11 entry-point。
      `pytest_addoption` (`--ndf-config` / `--ndf-out-dir` / `--ndf-no-evidence`
      / `--ndf-hud` / `--ndf-drive-folder`)、markers (`page_role` / `role` /
      `phase` / `priority`)、`pytest_runtest_makereport` /
      `pytest_terminal_summary` / `pytest_sessionfinish` hook
    - `scenario_test/pytest_report.py` — `report.md` 生成 (`NdfTestEntry` +
      `render_markdown` + `write_report`)
    - `scenario_test/fixtures/auth.py` — `ndf_config` (session) /
      `ndf_role_<id>` (動的生成、storage_state cache 付)
    - `scenario_test/fixtures/evidence.py` — `ndf_evidence` /
      `browser_context_args` override (HAR inject) / `ndf_out_dir`
    - `scenario_test/fixtures/a11y.py` — `_ndf_a11y_autouse` (page_role marker
      が付いた test に限り axe-core 自動実行) + `ndf_a11y_scan` 明示ヘルパ
    - `scenario_test/fixtures/cwv.py` — `_ndf_cwv_autouse` (page_role marker
      autouse で LCP/CLS/TTFB/longest_task 計測)
  - **削除**:
    - `scenario_test/testcase.py` の `Step` / `LocatorSpec` /
      `KNOWN_STEP_KINDS` / `discover_testcases` 等
    - `scenario_test/locator_steps.py` / `runner.py` / `cli.py` /
      `playwright_executor.py` / `report.py` (旧)
    - `scripts/record_to_yaml.py` / `generate_test_plan.py` (DSL 雛形版)
    - `templates/testcase-*.yaml.template` 6 ファイル + `config.example.yaml`
  - **新規 templates** (pytest 雛形):
    - `templates/scenario.config.yaml` — base_url / roles / a11y / CWV 設定
    - `templates/conftest.py.template`
    - `templates/test_auth.py.template` / `test_list.py.template` /
      `test_form.py.template` / `test_dashboard.py.template`
  - **依存追加** (main): `pytest>=8.0`, `pytest-playwright>=0.5`,
    `pytest-xdist>=3.0`
  - **設計上の重要ポイント**:
    - autouse fixture が `page` を直接要求すると pytest-playwright が全 test を
      browser parametrize する問題を、`request.fixturenames` ガード +
      `getfixturevalue` 遅延取得で回避
    - `ndf_role_<id>` の login は session 内 1 回だけ実行し storage_state を
      cache。新 context には cookies/origins を inject して再ログイン回避
  - **検証**: 旧 126 + 新規 26 = **152 件 pure 関数テスト pass**
  - SKILL.md は pytest 中心の構成に全面書き直し
- Skills: 36個 (変化なし、playwright-scenario-test の中身が刷新)

### v4.1.1 (playwright-scenario-test v0.2.5 — locator-first DSL 中間版)

> **Note**: 当初は v0.3.0 として実装したが、Codex の独立レビューで「pure
> pytest-playwright 移行の方が OSS 品質として優れている」と判断 (locator DSL は
> 自前で再実装が必要、artifact 契約も pytest plugin の方が自然) し、v0.3.0 の
> 番号は **pure pytest 完全移行** に予約しなおした (PLAN17 参照)。本リリースは
> evidence/a11y/CWV/slug 衝突回避などの本質的改善のみを v0.2.5 として暫定提供。

- **`playwright-scenario-test` v0.2.5** (locator-first / web-first 中間版):
  - **testcase YAML スキーマを刷新**: 旧 `path/method/data/extract` 構造を廃止。明示的 `kind` (`goto/click/fill/select/check/press/hover/extract/wait_for/wait_ms/expect_visible/expect_hidden/expect_text/expect_no_text/expect_url/expect_count/expect_aria_snapshot`) に統一
  - **Locator 表現**: `{role: button, name: 保存}` / `{label: メールアドレス}` / `{testid: ...}` / `{css: ...}` 等の dict で記述。Playwright 公式の `get_by_*` 系 API に 1 対 1 対応
  - **assertion は `expect()` のみ**: 自前 `body_check` と HTML 文字列 match を全廃。`expect_no_text` step で代替
  - **新モジュール**:
    - `scenario_test/locator_steps.py` — YAML step kind → Locator/expect API dispatcher (テーブル駆動)
    - `scenario_test/evidence.py` — 1 testcase 分の HAR/trace/console/pageerror/axe/CWV を集中管理
    - `scenario_test/a11y.py` — axe-core ランナー内蔵モジュール (page_role に応じて自動実行)
    - `scenario_test/cwv.py` — Core Web Vitals ランナー内蔵モジュール (page_role に応じて自動実行)
  - **削除**:
    - `scenario_test/nav_helpers.py` (全関数を locator-first で置換)
    - `scripts/trace_link.py` (`upload_evidence.py` に統合)
    - 古い `path` ベース step / `body_check` / `slug` config / `enable_scroll_demo` config
  - **新規 scripts**:
    - `scripts/upload_evidence.py` — trace/HAR/video の Drive アップ統合 (拡張子から自動判定)
    - `scripts/record_to_yaml.py` — Playwright codegen Python 出力 → 新スキーマ YAML 変換
  - **Min-2 (slug 衝突)**: `_default_test_id(role, url)` で URL 全 path + sha1[:6] suffix を付与し衝突回避
  - **Maj-7 (責務分離)**: `playwright_executor.py` 678 → 約 380 行へ縮小 (evidence は `EvidenceCollectors` へ完全分離)
  - **report.md** に axe-core 違反 / Core Web Vitals metrics を表示
  - templates/ を 6 ファイル全部新スキーマで書き直し
  - docs/ から「v0.3.0 以降で検討」記述を解消、`navigate_post` / `find_click_target` / `detect_body_errors` 等の旧 helper 言及を削除
- Skills: 36個 (変化なし)

### v4.1.0
- **`playwright-scenario-test` v0.2.0** (理論ベース化):
  - `docs/` 配下に方法論を 6 ファイル (総論 / page role / 技法 / Playwright API / bug report) + checklists 11 ファイル (lp/list/item/edit/form/search/dashboard/auth/cart-checkout/modal-wizard/common) として整備
  - 出典: HTSM v6.3 (James Bach), ISTQB CTFL 4.2, ISO/IEC/IEEE 29119-3:2021, WCAG 2.2, OWASP Top 10:2025, FEW HICCUPPS, Hendrickson Cheat Sheet
  - 新規スクリプト: `classify_page_role.py` (a11y tree から自動 role 判定), `generate_test_plan.py` (Pairwise 込み YAML 自動生成), `run_a11y_scan.py` (axe-core), `check_cwv.py` (LCP/CLS/TTFB), `record_scenario.py` (Playwright codegen ラッパー), `trace_link.py` (trace.zip → playwright.dev URL)
  - 役割別 testcase YAML テンプレート 4 件 (list/edit/form/auth) を追加
  - SKILL.md は実行手順とナビゲーションに集中 (332 → 245 行)
  - pyproject.toml に optional-dependencies `a11y` (axe-playwright-python) を追加
- **公式 Agent Skill 仕様準拠**: 14 skill の frontmatter を Pattern A (description 単体に Triggers 埋め込み) から Pattern B (description + 公式 `when_to_use` フィールド分離) へ移行。対象 skill: codex / data-analyst-export / data-analyst-sql-optimization / deepwiki-transfer / docker-container-access / git-gh-operations / google-auth / google-chat / google-drive / markdown-writing / official-skills-autoloader / playwright-scenario-test / python-execution / qa-security-scan / skill-stats。`description` は概要に集中、`when_to_use` に Trigger phrase を分離して auto-invoke 精度向上 (公式 1,536 字上限内)。`mcp-builder` は Anthropic 公式 (Apache-2.0) のため改変せず。
- **コード品質改善**:
  - `google-chat/scripts/gchat_read.py`: `DEFAULT_SPACE_ID` のハードコード (`AAQA6AWG1iE`) を撤去。env `GCHAT_DEFAULT_SPACE` で指定するか `--space` を required にする運用に変更
  - `google-auth/SKILL.md`: `allowed-tools` から不要な `Bash(pip *)` を削除 (uv で完結)
  - `google-drive/scripts/gdrive_fetch.py`: `upload_file` 内の dead な再 `import os` を削除
  - `playwright-scenario-test/SKILL.md`: 不足していた `allowed-tools` (Read / Bash(uv *) / Bash(python *)) を追加
- **新規 Skill `playwright-scenario-test`** (self-contained uv project):
  - Playwright + curl で Web シナリオ E2E テストを並列実行
  - HUD オーバーレイ (カーソル + 字幕) 焼き込み済み動画 + Markdown レポート生成
  - Drive アップロード自動化までサポート
  - 外部プロジェクトは `config.yaml` + `testcases/*.yaml` のみで利用可能
- **新規 Skill `google-drive`**:
  - Google Drive / Docs のファイルエクスポート / ダウンロード / アップロード (公開共有リンク付与)
  - 認証は `ndf:google-auth` の `get_credentials()` に委譲
- **新規 Skill `google-chat`**:
  - Google Chat スペースのメッセージ・スペース一覧取得 (Chat API)
  - 認証は `ndf:google-auth` の `get_credentials()` に委譲
  - 旧 uttarov 版のハードコードパス (`/work/uttarov2-doc/...`) を撤廃し、
    sibling-skill discovery (`GOOGLE_AUTH_SCRIPTS` env / `~/.claude/skills/google-auth/scripts` /
    隣接スキル) でフォールバック
- **`google-auth` v0.2.0 (互換性破壊)**:
  - Python ライブラリ用法 (`from google_auth import get_credentials`) を追加
  - `--manual` 手動 copy-paste フロー (ローカルサーバ不要、コンテナ環境対応)
  - トークン自動リフレッシュ + スコープ不足検出 / 自動マージ
  - `--show` / `--clear` サブコマンド
  - **トークン保存先を `/tmp/google_token.json` → `~/.config/gcloud/google_token.json` に変更**
    (env `GOOGLE_TOKEN_FILE` で上書き可)
  - `client_secret` パスは `--client-secret` → env `GOOGLE_CLIENT_SECRET` →
    `${CLAUDE_SKILL_DIR}/client_secret.json` → CWD の順
- Skills: 33個 → **36個**

### v4.0.0 (BREAKING: Codex MCP廃止 + レガシー救済機構削除)
- **Codex MCP サーバを削除** (`.mcp.json` から `codex` エントリを削除)
  - 理由: `/ndf:codex` skill (CLI直接実行) で十分であり、MCP 経由の制約 (ホスト側ファイル読み取り制限等) よりも CLI 直接実行の方が有用
  - 影響: `mcp__codex__codex` / `mcp__codex__codex-reply` は利用不可
  - 代替: `/ndf:codex` skill の手順で `codex exec` をバックグラウンド実行、または `corder` エージェント経由で呼び出し
- **corder エージェントを CLI ベースに書き換え**
  - MCP 呼び出しを `/ndf:codex` skill 参照に変更
  - Serena / Context7 MCP は引き続き利用
- 他エージェント (researcher, qa, devops-engineer, debugger, code-reviewer, director) の description から Codex MCP 言及を削除 / CLI ベースに更新
- `skills/codex` の MCP 版との使い分け節を corder エージェントとの使い分けに書き換え
- `skills/qa-security-scan/03-report-template.md` の JS 疑似コードを `codex exec` bash 例に置換
- **レガシー CLAUDE.ndf.md 救済機構を削除** (v3.0.0 で本体廃止、以降の救済装置を除去)
  - `hooks/hooks.json` の CLAUDE.ndf.md 検出 hook を削除
  - `skills/cleanup/` を削除 (`plugin.json` の参照も削除)
  - まだ残っているユーザーは今後手動で `CLAUDE.ndf.md` を削除してください
- Skills: 34個 → **33個** (`cleanup` 削除)

### v3.7.0
- **Transcript保持期間の自動管理**:
  - `SessionStart` hook (matcher: `startup`) + `scripts/ensure-retention.sh` を追加
  - `~/.claude/settings.json` の `cleanupPeriodDays` を最低 90 日に保つ (既に 90 以上ならそのまま)
  - 7 日タイムスタンプガード (`~/.claude/.ndf-retention-checked`) で多重実行防止
  - Claude Code 本体の公開 API/ドキュメントには「プラグインインストール時」hook が存在しないため、`SessionStart + startup` matcher が事実上の最適解
- **`/ndf:skill-stats` skillを追加**:
  - `~/.claude/projects/**/*.jsonl` transcript から NDF skill 利用統計を集計
  - 項目: 呼び出し数 / 関連話題数 / ヒット数 / ヒット率
  - 関連話題判定は SKILL.md frontmatter の `Triggers: '..', '..'` 行を使用 (明示されていない skill は計算対象外)
  - Python 実装、標準ライブラリのみ
  - skill description の網羅性を評価するツールとして機能
- Skills: 33個 → **34個**

### v3.6.0
- carmo-system-consoleから汎用skill/commandを抽出してNDFに統合
- **新規Skills（13個）**:
  - 原則系（5個）: `branch-fix-strategy`, `implementation-plan`, `investigation-rules`, `problem-solving`, `logging-guidelines`
  - ワークフロー系（7個）: `sync-main`, `cherry-pick-pr`, `deploy`, `review-branch`, `review-pr-comments`, `resolve-pr-comments`, `browser-test`
  - 外部AI委譲（1個）: `codex`（CLI直接実行、MCP版corderとの使い分け）
- **既存Skill改修**:
  - `pr`: `--draft`フラグ対応、既存PR説明の自動更新、base非mainの場合`cherry-pick-pr`誘導、`.github/pull_request_template.md`サポート
- Skills: 20個 → **33個**
- PR/コードレビュー系の責務分担を明確化:
  - `review` = PR単位レビュー（Approve/Request Changes判定）
  - `review-branch` = ローカル差分レビュー（PR前のセルフレビュー）
  - `review-pr-comments` = 既存PRコメントの分類（READ-ONLY）
  - `fix` = コメント対応の修正実施
  - `resolve-pr-comments` = 修正完了後の返信+Resolve（クロージング）

### v3.5.0 (破壊的変更: scanner削除)
- Claude Code Read toolのmultimodal/PDF native対応、および v3.4.0で追加された `official-skills-autoloader` により冗長になったAgent/Skillを整理
- **削除Agent**:
  - `scanner` (Office抽出) → autoloader + 公式docx/pptx/xlsx Skillで代替
- **削除Skills**:
  - `scanner-pdf-analysis` → Read tool の PDF native対応で代替
  - `scanner-excel-extraction` → autoloader + 公式xlsx Skillで代替（plugin.jsonのdangling ref整理）
  - `skill-development` → 公式 `skill-creator` Skillで代替（autoloader取得可能）
  - `corder-code-templates`, `corder-test-generation` → Claude本体のコード生成能力で代替
  - `researcher-report-templates` → researcher agent description／Claude本体で代替
- Agents: 9個 → **8個**
- Skills: 25個 → **20個**
- 移行ガイド: `/ndf:scanner` を呼んでいた処理は、autoloaderまたはRead toolへ切替

### v3.4.0
- Anthropic公式の定番Skill `mcp-builder` を取込（Apache-2.0、LICENSE.txt同梱）
- 公式Skillインストーラ `plugins/ndf/scripts/install-official-skills.sh` を追加
  - `--list`: 利用可能Skill一覧（ライセンス分類付き）
  - `--scope user/project`: インストール先選択
  - `--all` / 個別指定: 選択的インストール
  - `--update`: 公式リポジトリの最新化
  - シンボリックリンク方式で軽量
- プロプライエタリSkill（docx/pptx/xlsx/pdf）は再配布せず、上記インストーラで個人利用者環境に配置
- インストール手順・ライセンス方針を `docs/official-skills-installation.md` にまとめ
- `official-skills-autoloader` Skillを追加: Word/Excel/PowerPoint/PDF等の要求時に必要な公式Skillを自動ダウンロード→読込して即使用可能（利用者はインストール作業不要）
- Skills: 23個 → 25個

### v3.3.0
- 定番サブエージェント3個を追加（いずれも `model: sonnet`）
  - **debugger**: エラー・バグの根本原因分析
  - **devops-engineer**: Dockerfile/CI/CD/Kubernetes
  - **code-reviewer**: git diff / PR一般レビュー（corderと差別化: Codex非使用）
- Agents: 6個 → 9個

### v3.2.0
- サブエージェントに `model:` 指定を追加し、コスト最適化
  - director: `opus`（計画・設計判断）
  - corder, data-analyst, researcher, qa: `sonnet`
  - scanner: `haiku`
- scannerエージェントをOffice専用に縮小
  - 画像・PDFはClaude Code built-inのRead tool（multimodal, pages）で処理する方針に変更
- corderのdescriptionを「Codex第二意見レビュー／大規模調査」用途に明確化
- researcherのdescriptionをAWS Docs / Chrome DevTools専用に縮小

### v3.1.0
- Kiro CLI対応（`.kiro/` 配下のインストーラ、プロンプト、スキルリンク）
- `google-auth` スキル追加

### v3.0.0 (破壊的変更)
- Serena MCPを`mcp-serena`プラグインに分離
- memory系スキル5個を廃止（serena, memory-handling, serena-memory-strategy, mem-capture, mem-review）
- CLAUDE.ndf.md注入仕組みを廃止（inject-plugin-guide.js削除）
- `ndf-policies`スキル追加（ポリシー常時注入）
- `/ndf:cleanup`スキル追加（CLAUDE.ndf.md後始末）
- SessionStartフックをCLAUDE.ndf.md検出警告に変更
- Skills: 25個→23個

### v2.8.0
- `deepwiki-transfer`スキル追加
- Skills: 23個→25個（knowledge-reorg含む）

### v2.7.0
- commandsをskillsに統合（Claude Code 2.1.3対応）

### v2.6.0
- NDFプラグインのMCP構成を最適化し個別プラグイン化

### v2.0.0
- GitHub MCP, Serena MCP, Context7 MCPを公式プラグインに移行
