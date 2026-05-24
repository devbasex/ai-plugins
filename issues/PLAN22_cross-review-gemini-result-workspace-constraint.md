# PLAN22: cross-review TMP_DIR を worktree 内 `.cross_review/` に統一

- 起票日: 2026-05-24
- 対象 plugin: `ndf` v4.7.4
- 対象 skill: `ndf:cross-review`
- 関連 issue: [GitHub Issue #7](https://github.com/devbasex/ai-plugins/issues/7)
- 関連 plan (先行): PLAN20 (v4.7.3, PR #4 で merge 済み)
- 報告者: takemi-ohama (`devbasex/devbase#25` の `/ndf:cross-review 25` 実行中に検出)

## 背景・課題

### PLAN20 で行ったこと

PLAN20 で `_tmpdir.sh` / `state.py _tmp_dir()` / `monitor.py _tmp_dir()` の
TMP_DIR 解決先を `/tmp/` → `~/.cross_review/<workspace-basename>/` に統一した。
これは launcher / monitor / state 側のパス統一としては正しく機能している。

### PLAN20 で解決できなかったこと

gemini CLI の `write_file` ツールは **workspace ディレクトリ (= worktree) 内**にしか
書き込みできない。`~/.cross_review/` は gemini の設定ディレクトリであって workspace ではないため、
gemini からの書き込みは依然としてブロックされる:

```
Error executing tool write_file: File path must be within one of the workspace directories: /work/worktrees/pr25
```

検証済み:
- `--include-directories "$TMP_DIR"` → 効果なし
- `GEMINI_CLI_TRUST_WORKSPACE=true` → 効果なし (既に設定済み)

gemini はフォールバックとして worktree 直下に `gemini-review-pr<PR>-result.json` を書くが、
`monitor.py` は `~/.cross_review/<basename>/` のみを参照するため `NO_RESULT` (exit 3) を返し、
cross-review ループが止まる。

### 影響

- 各 round でメインセッション側の手動補完が必要
- cross-review の「AI 直接投稿 + メイン context 節約」設計の前提が崩れる
- context 消費増・中断リスク増

## 修正方針

TMP_DIR を `~/.cross_review/<basename>/` (worktree 外) から **`<worktree>/.cross_review/`** (worktree 内) に変更する。

gemini の workspace 制約を根本的に解消でき、monitor.py の fallback コピー等の追加ロジックも不要。

### なぜこれで全体が連動するか

SKILL.md (メインループ) の L171:
```bash
export CROSS_REVIEW_TMP_DIR="$TMP_DIR"
```

`state.py init` が出力する `TMP_DIR` を env 伝播しているため、`state.py _tmp_dir()` の
解決先を変えるだけで全後続スクリプト (launcher / monitor.py) が連動する。
`CROSS_REVIEW_TMP_DIR` env は `_tmpdir.sh` / `monitor.py _tmp_dir()` の最優先パスなので、
それぞれの fallback ロジックは通過しない。

### 変更 1: `state.py _tmp_dir()` — 解決先を worktree 内に変更

**ファイル**: `plugins/ndf/skills/cross-review/scripts/state.py` L60-82

```python
# Before
base_name = pathlib.Path(workspace or os.getcwd()).name
gemini_root = pathlib.Path.home() / ".gemini" / "tmp"
if gemini_root.is_dir() and base_name:
    d = gemini_root / base_name

# After
ws = pathlib.Path(workspace or os.getcwd()).resolve()
d = ws / ".cross_review"
```

`workspace` が指定されていれば `<workspace>/.cross_review/`、なければ `<cwd>/.cross_review/` を返す。

### 変更 2: `_tmpdir.sh` — fallback を整合

**ファイル**: `plugins/ndf/skills/cross-review/scripts/_tmpdir.sh`

通常フローでは `CROSS_REVIEW_TMP_DIR` env で上書きされるため到達しないが、
直接実行時の fallback も揃える:

```bash
# Before
gemini_root="$HOME/.gemini/tmp"
if [ -d "$gemini_root" ] && [ -n "$base" ]; then
    mkdir -p "$gemini_root/$base"
    echo "$gemini_root/$base"

# After
_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
mkdir -p "$_root/.cross_review"
echo "$_root/.cross_review"
```

### 変更 3: `monitor.py _tmp_dir()` — fallback を整合

**ファイル**: `plugins/ndf/skills/cross-review/scripts/monitor.py` L203-220

同様に fallback を `git rev-parse --show-toplevel` ベースの `<worktree-root>/.cross_review/` に変更。

### 変更 4: `.gitignore` — `.cross_review/` 除外

**ファイル**: `.gitignore`

```
# cross-review gemini workspace tmp
.cross_review/
```

cross-review の一時ファイルディレクトリを除外。

### 変更 5: SKILL.md — ドキュメント整合

**ファイル**: `plugins/ndf/skills/cross-review/SKILL.md` L86

「tmp dir は `~/.cross_review/<workspace>/` を採用」の記述を更新。

## 変更対象ファイル一覧

| ファイル | 変更内容 |
|---|---|
| `plugins/ndf/skills/cross-review/scripts/state.py` | `_tmp_dir()` の解決先を `<workspace>/.cross_review/` に変更 |
| `plugins/ndf/skills/cross-review/scripts/_tmpdir.sh` | fallback を `$PWD/.cross_review/` に変更 |
| `plugins/ndf/skills/cross-review/scripts/monitor.py` | fallback を `cwd/.cross_review/` に変更 |
| `.gitignore` | `.cross_review/` 追加 |
| `plugins/ndf/skills/cross-review/SKILL.md` | ドキュメント整合 |

## launch-gemini.sh は変更不要

`launch-gemini.sh` のプロンプト内のパスは `$TMP_DIR/gemini-review-pr$STATE_PR-result.json` と
変数展開している。`$TMP_DIR` が `<worktree>/.cross_review/` に変わることで、
プロンプト上のパスも自動的に worktree 内を指す。gemini はこのパスに書き込み可能。

## 変更 6 (試行): `launch-codex.sh` — sandbox モード緩和

**ファイル**: `plugins/ndf/skills/cross-review/scripts/launch-codex.sh` L101

TMP_DIR が worktree 内に移ることで、codex が workspace 外に書き込む必要がなくなる。
`--dangerously-bypass-approvals-and-sandbox` を `-s workspace-write` に切り替え可能か試行する。

```bash
# Before
codex exec --dangerously-bypass-approvals-and-sandbox \
  --config reasoning.effort=medium -C "$WORKTREE" \

# After (試行)
codex exec -s workspace-write \
  --config reasoning.effort=medium -C "$WORKTREE" \
```

`codex exec` は非対話モードのため approval bypass は不要と想定。
`-s workspace-write` で `gh api` (ネットワーク + シェル実行) が通るかが判定基準:

- **通る場合**: `-s workspace-write` に切り替え。より安全な sandbox 設定になる
- **通らない場合**: 現行の `--dangerously-bypass-approvals-and-sandbox` を維持

## 変更 7: `/ndf:merged` — worktree クリーンアップ追加

**ファイル**: `plugins/ndf/skills/merged/SKILL.md`

PR マージ後のクリーンアップに、cross-review で作成された worktree の削除を追加する。

現状の手順:
1. PR がマージ済みか確認
2. stash → main checkout → pull
3. feature ブランチ削除 → stash 復元

追加する手順 (3 の前に挿入):
- `git worktree list` で当該 PR 番号に対応する worktree を探す (例: `pr<PR番号>`)
- 見つかれば `git worktree remove <path>` で削除
- worktree 内の `.cross_review/` も worktree ごと消えるため個別削除は不要

## 単一 PR 判定

- 変更ファイル 6〜7 個、差分 ~40〜50 行
- 全変更は TMP_DIR パスの統一 + それに伴う sandbox 緩和 + クリーンアップという 1 つの関心事
- release ブランチ不要。通常の feature branch + PR フローで進行

## ブランチ

```
feature/PLAN22-gemini-result-workspace
```

## テスト計画

- [ ] worktree 上で `/ndf:cross-review` を実行し、gemini が `<worktree>/.cross_review/` に result.json を書くことを確認
- [ ] `monitor.py` が `<worktree>/.cross_review/` の result.json を検出して OK 判定することを確認
- [ ] `state.py read-result` が正常に result.json を読めることを確認
- [ ] `.cross_review/` が `git status` に表示されないことを確認
- [ ] codex 側のフローに影響がないことを確認 (codex は workspace 制約がないが、パスが変わっても動作すること)
- [ ] codex で `-s workspace-write` に切り替え、`gh api` によるレビュー投稿が通ることを確認。通らなければ `--dangerously-bypass-approvals-and-sandbox` を維持
- [ ] `/ndf:merged <PR>` 実行後、対応する worktree (`pr<PR>`) が削除されていることを確認
