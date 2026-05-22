# PLAN20: cross-review の worktree デフォルトパス と gemini result.json スキーマ整合性修正

- 起票日: 2026-05-21
- 対象 plugin: `ndf` v4.7.2
- 対象 skill: `ndf:cross-review`
- 関連 issue: [issues/i17.md](./i17.md)
- 報告者: takemi-ohama (`devbasex/devbase#14` の `/ndf:cross-review 14` 実行中に検出)

## 背景・課題

`/ndf:cross-review` を macOS ホストで実行した際に、独立した 2 件の不具合に遭遇している。
どちらも回避策は確立されているが、毎回手動介入が必要なためプラグイン側で恒久対応する。

### 問題1: worktree デフォルトパスが Linux コンテナ前提でハードコードされている

`scripts/state.py:122` の以下が原因:

```python
worktree = args.worktree or f"/work/worktrees/pr{pr}"
```

macOS では `/work` が SIP で書き込み不可のため、`git worktree add` が `Read-only file system`
で失敗する。SKILL.md / docs / launcher prompt 側にも同じパスが文字列として埋め込まれており、
ユーザは `--worktree` を毎回明示しないと init できない。

### 問題2: gemini の `result.json` スキーマが launcher 間で揺れて intent が欠落

`launch-gemini.sh:90` がスキーマの具体的フィールドを列挙せず「フォーマットは launch-codex.sh
と同じ」とだけ書いているため、gemini が独自スキーマ (`intent` / `comment_count`) で書き出して
しまう。一方 `state.py:244-265` `cmd_read_result` は `event` / `comments_count` しか見ないため、
**intent=None で state に取り込まれ judge で空回り**する。最悪 `max_rounds` 到達まで無駄
round + 無駄レビューコメントが積み上がる。

両不具合とも cross-review skill 配下に閉じており、影響範囲も小さいため **単一 PR** で対応する。

## ゴール

1. macOS / WSL / 非コンテナ環境でも `state.py init <PR>` が `--worktree` 引数なしで成功する
2. gemini が書き出した `result.json` が `state.py read-result` で正しく `intent` を含めて
   state にマージされ、judge が両者の APPROVE を認識する
3. 上記いずれかが将来再発したときに気付けるよう、**`read-result` 側で intent 欠落を検知して
   エラー終了**する (silent な None マージを禁止)
4. 既存のコンテナ環境 (`/work` 書込可) の挙動は変えない (後方互換)

## 設計方針

### 1. worktree デフォルトパス解決ロジック (問題1)

`state.py` に `_default_worktree_base()` を追加し、優先度順に解決する:

```python
def _default_worktree_base() -> pathlib.Path:
    """worktree の親ディレクトリを環境に応じて解決する。

    優先順位:
      1. 環境変数 NDF_WORKTREE_BASE (明示オーバーライド)
      2. /work/worktrees (Linux コンテナ環境互換、書き込み可能ならそれを使う)
      3. $HOME/work/worktrees (macOS / WSL 等のフォールバック)
    """
    env = os.environ.get("NDF_WORKTREE_BASE")
    if env:
        return pathlib.Path(env)
    legacy = pathlib.Path("/work/worktrees")
    try:
        legacy.mkdir(parents=True, exist_ok=True)
        # mkdir 成功 = 書き込み可能 → 既存環境互換でこちらを使う
        return legacy
    except OSError:
        pass
    return pathlib.Path.home() / "work" / "worktrees"
```

`cmd_init` 内の参照を以下に変更:

```python
worktree = args.worktree or str(_default_worktree_base() / f"pr{pr}")
```

**ポイント**:

- Linux コンテナ環境 (既存ユーザ) は `/work/worktrees` が引き続き使われ挙動不変
- macOS / WSL は `$HOME/work/worktrees/pr<PR>` にフォールバック
- `NDF_WORKTREE_BASE=/foo/bar` で明示オーバーライド可能
- 解決した実 path は `state.json` の `worktree_path` に書かれるため、後続スクリプト・サブエージェント
  prompt は既存どおり state.json から読めば追従できる

### 2. ドキュメント・launcher prompt のハードコード除去 (問題1 派生)

launcher prompt は `WORKTREE` を `state.json` から読む構造になっており、修正不要。
**ドキュメントの説明文だけ** 抽象化する:

- `SKILL.md` 「事前確認」表 #2 の `git worktree add /work/worktrees/pr<PR>` を
  `git worktree add <worktree-base>/pr<PR>` + 注記 (`worktree-base` の解決順) に変更
- `SKILL.md` mermaid 図中の `/work/worktrees/pr<PR> を用意` を `<worktree-base>/pr<PR> を用意` に
- `docs/01-state-and-review.md` の Step 0 解説 (`3. worktree 作成（/work/worktrees/pr<PR>）`) と
  state.json サンプル (`"worktree_path": "/work/worktrees/pr123"`) を、説明文側は抽象化しつつ
  JSON 例は **解決例として 1 つだけ** 残す (具体例の方が読みやすいため)
- `docs/01-state-and-review.md:166` の「作業 worktree の絶対パスを使う」記述は、抽象化した上で
  「実 path は state.json の `worktree_path` を参照」と追記

state.json の `worktree_path` を一次ソースとする方針を明文化することで、将来の path 体系変更にも
追従しやすくする。

### 3. gemini result.json スキーマの明示化 (問題2 のうち launcher 側)

`launch-gemini.sh:90` の曖昧な指示を **codex と同一のフィールド列挙ブロック** に置き換える:

```text
- 投稿後、サマリを **$TMP_DIR/gemini-review-pr$STATE_PR-result.json** に
  **必ず以下のキーで** 書く:
  ```json
  {
    "event": "APPROVE",
    "posted_as": "COMMENT",
    "comments_count": 3,
    "review_url": "https://github.com/.../pull/$PR#pullrequestreview-...",
    "by_severity": {"critical": 0, "major": 0, "minor": 0, "nit": 0}
  }
  ```
  - `intent` / `comment_count` 等の別名は使わないこと
  - `event` の値は `APPROVE` / `REQUEST_CHANGES` / `COMMENT` のいずれか
  - `event_downgrade=true` のとき `posted_as` は `COMMENT` にダウングレード可
- payload は **$TMP_DIR/gemini-review-pr$STATE_PR-round$ROUND-payload.json** に保存
```

`launch-codex.sh` 側は既に同等の明示があるため変更不要。ただし将来の整合性のため、
**両 launcher で同一のスキーマブロックをコピペで持たせる** (共通ファイル切り出しは
スコープ過大なので今回はしない)。

### 4. `state.py read-result` の堅牢化 (問題2 のうち state 側)

`cmd_read_result` を以下に変更し、(a) 別名フィールドへフォールバック、(b) intent 欠落時は明示的に
fail させる:

```python
def cmd_read_result(args: argparse.Namespace) -> None:
    agent = args.agent
    pr = args.pr
    rfile = pathlib.Path(args.file or _tmp_dir() / f"{agent}-review-pr{pr}-result.json")
    if not rfile.exists() or rfile.stat().st_size == 0:
        die(f"{agent}: result 未生成 ({rfile})")

    r = json.loads(rfile.read_text())

    # 別名フィールドへのフォールバック (gemini が `intent` / `comment_count` を使う変則 JSON を
    # 書き出す既知のケースに対応する。仕様としては `event` / `comments_count` が正)
    intent = r.get("event") or r.get("intent")
    posted_as = r.get("posted_as") or intent
    comments = r.get("comments_count")
    if comments is None:
        comments = r.get("comment_count")

    if intent is None:
        die(
            f"{agent}: result.json に event / intent フィールドが無い ({rfile})。"
            " launcher prompt のスキーマ違反の可能性。"
        )

    st = _load(pr)
    if not st.get("rounds"):
        die(f"{agent}: state.rounds が空。`state.py start-round` を先に呼んでください")
    st["rounds"][-1][agent] = {
        "intent": intent,
        "posted_as": posted_as,
        "comments": comments,
        "review_url": r.get("review_url"),
        "by_severity": r.get("by_severity", {}),
    }
    _save(pr, st)
    info(f"✅ {agent}: intent={intent} posted_as={posted_as} comments={comments}")
```

**ポイント**:

- 仕様 (`event` / `comments_count`) を**優先**しつつ、別名 (`intent` / `comment_count`) も拾える
- intent が取れないときは `die()` で exit 1 する → judge 段階より早く発見できる
- 既存のスキーマで書かれた result.json は挙動不変

### 5. テスト (軽量)

`scripts/state.py` は現状 unit test を持たない。
今回は `cmd_read_result` 周辺のスキーマ揺れだけを対象に、**軽量な pytest を追加**する。
位置は `plugins/ndf/skills/cross-review/tests/test_state_read_result.py`。

カバレッジ:

1. 正規スキーマ (`event` / `comments_count`) → intent / comments が state に書かれる
2. 変則スキーマ (`intent` / `comment_count`) → 同等に state に書かれる (フォールバック動作)
3. intent / event いずれも無い → `die()` で exit 1 + state 不変
4. (worktree path 側) `NDF_WORKTREE_BASE` が指定されていれば `_default_worktree_base()` が
   その path を返す / `/work` が無い環境では `$HOME/work/worktrees` を返す

`pytest` 実行は CI には組み込まず、開発者がローカルで `uv run pytest plugins/ndf/skills/cross-review/tests`
で回せる形で OK (現状 ndf プラグインに CI 設定が無いため)。

### 6. バージョン更新

- `plugins/ndf/.claude-plugin/plugin.json` を `4.7.2` → `4.7.3` (patch bump、バグ修正のみ)
- `plugins/ndf/CHANGELOG.md` または `plugins/ndf/CLAUDE.md` の開発履歴に v4.7.3 のエントリ追加
  (存在を確認の上、所在に合わせる)

## 実装タスク

### Phase 1: worktree デフォルトパス解決 (問題1 本体)
- [ ] `scripts/state.py` に `_default_worktree_base()` 関数を追加
- [ ] `cmd_init` の `worktree = args.worktree or f"/work/worktrees/pr{pr}"` を
      `worktree = args.worktree or str(_default_worktree_base() / f"pr{pr}")` に変更
- [ ] `import os` / `import pathlib` が既に取り込まれていることを確認 (state.py 冒頭)

### Phase 2: ドキュメントの worktree パス抽象化
- [ ] `SKILL.md`「事前確認」表 #2 を `<worktree-base>/pr<PR>` 表現に変更
- [ ] `SKILL.md` mermaid 図中の `/work/worktrees/pr<PR>` を `<worktree-base>/pr<PR>` に
- [ ] `SKILL.md` 末尾もしくは関連節に `<worktree-base>` の解決順 (env > /work > $HOME) を追記
- [ ] `docs/01-state-and-review.md` Step 0 解説 (line 97 付近) を抽象化、worktree_path は state.json
      参照と注記
- [ ] `docs/01-state-and-review.md` line 166 付近の絶対パス記述を「state.json の `worktree_path`
      を参照」に整理

### Phase 3: gemini launcher のスキーマ明示 (問題2 launcher 側)
- [ ] `scripts/launch-gemini.sh:90` のサマリ書き出し指示を、codex と同一の JSON スキーマ
      ブロックに置き換え
- [ ] `intent` / `comment_count` 等の別名禁止を明記
- [ ] `event_downgrade=true` 時の `posted_as` 扱いを明記

### Phase 4: state.py read-result の堅牢化 (問題2 state 側)
- [ ] `cmd_read_result` を別名フォールバック + intent 欠落時 die に書き換え
- [ ] info ログを新しい変数 (intent / posted_as / comments) ベースに変更

### Phase 5: テスト追加
- [ ] `plugins/ndf/skills/cross-review/tests/__init__.py` (空)
- [ ] `plugins/ndf/skills/cross-review/tests/test_state_read_result.py`:
    - [ ] 正規スキーマケース
    - [ ] 変則スキーマケース (`intent` / `comment_count` 名)
    - [ ] intent 欠落 → die ケース (SystemExit を確認)
- [ ] `plugins/ndf/skills/cross-review/tests/test_default_worktree_base.py`:
    - [ ] `NDF_WORKTREE_BASE` 明示時にそれを返す
    - [ ] `/work` 書き込み不可をモックして `$HOME/work/worktrees` を返す
- [ ] ローカルで `uv run pytest plugins/ndf/skills/cross-review/tests` を回し全 pass を確認

### Phase 6: バージョン更新
- [ ] `plugins/ndf/.claude-plugin/plugin.json` の `version` を `4.7.3` に更新
- [ ] `plugins/ndf/CHANGELOG.md` か `plugins/ndf/README.md` (存在に応じて) に v4.7.3 の節を追加
- [ ] description フィールドに今回の修正内容の要点を追記 (任意)

### Phase 7: 検証
- [ ] macOS 風環境 (もしくは `/work` が存在しないコンテナ) で `state.py init` が `--worktree`
      なしで成功することを確認 (`NDF_WORKTREE_BASE` のセット例も併記)
- [ ] 既存 Linux コンテナ環境で `/work/worktrees/pr<PR>` が引き続き作られることを確認
      (`/work` が書ける状態を維持)
- [ ] `result.json` を変則スキーマ (`{"intent": "APPROVE", "comment_count": 3, ...}`) で
      書き出した状態で `state.py read-result` を呼び、state.json の `rounds[-1].gemini.intent`
      が `"APPROVE"` で取り込まれることを確認
- [ ] 空 result.json で `state.py read-result` を呼んだ際に exit 1 + 「event / intent が無い」
      旨のエラーが出ることを確認

## PR 構成

**単一 PR で実装する** (合計差分は ~250 行以内の見込み、release branch 不要):

- branch: `fix/PLAN20-cross-review-macos-and-result-schema`
- base: `main`
- 流れ:
  1. 上記 Phase 1〜6 を順次 commit (Phase 単位で分けると review しやすい)
  2. `/ndf:review-branch` でセルフレビュー
  3. `/ndf:pr` で PR 作成 (本 plan を `## Plan` セクションで参照)
  4. レビュー対応後 squash merge

## 互換性方針

- **後方互換あり**:
    - `/work/worktrees` が書ける環境は挙動不変 (`mkdir(exist_ok=True)` が成功するため)
    - 既存の正規 result.json スキーマ (`event` / `comments_count`) は引き続き正規系
- **新規挙動**:
    - `NDF_WORKTREE_BASE` env 対応 (任意指定)
    - `intent` / `comment_count` の別名 result.json も受理 (フォールバック)
    - intent / event いずれも無い場合は **die** で fail (silent な None マージは廃止)
- 旧挙動でユーザが暗黙依存していた可能性のあるもの (`intent=None` でも judge が回る挙動) は、
  **本来 bug なので破壊する** 方針。CHANGELOG に明記する。

## リスクと対策

| リスク | 対策 |
|---|---|
| `/work` が書けるが実は別ユーザに所有された共有環境で `mkdir(exist_ok=True)` が成功してしまう | `NDF_WORKTREE_BASE` で明示オーバーライドできる旨を SKILL.md に書く |
| gemini が今度は別の別名 (`reviewEvent` 等) で書く | 別名フォールバックは 1 段のみとし、検知できないケースは intent 欠落 die で発見する |
| read-result の挙動変化で既存 cross-review セッションが中断する | リリース前にローカル `cross-review` を一度通しで実走させ回帰確認 |
| テスト追加で skill ディレクトリに pytest 環境が必要になる | `uv run pytest` を README に追記、CI 強制は今回はしない |

## 完了の定義

- [ ] macOS 環境で `state.py init <PR>` が `--worktree` 引数なしで成功する
- [ ] Linux コンテナ環境で従来どおり `/work/worktrees/pr<PR>` が使われる
- [ ] `result.json` が `intent` / `comment_count` の別名で書かれても `state.py read-result` で
      intent が state に取り込まれる
- [ ] `result.json` から event / intent が両方欠落しているとき `state.py read-result` が
      exit 1 で fail する
- [ ] 追加した pytest が全て pass する
- [ ] plugin version が `4.7.3` に上がり、CHANGELOG / 開発履歴に v4.7.3 の節がある
- [ ] SKILL.md / docs から `/work/worktrees/pr<PR>` のハードコードが取り除かれている
      (state.json サンプル中の解決例 1 箇所のみ許容)
- [ ] `monitor.py` の EARLY_ERROR 検知が SKILL.md / docs 中の Markdown 表セルや
      backtick / 「」 引用内のキーワードを誤検知しない (cross-review 自己レビュー時の
      誤 kill を解消)

## 参考

- [issues/i17.md](./i17.md) — 元 issue (再現手順 / 回避策 / 修正提案を含む)
- 該当コード:
    - `plugins/ndf/skills/cross-review/scripts/state.py:122` (worktree default)
    - `plugins/ndf/skills/cross-review/scripts/state.py:244-265` (cmd_read_result)
    - `plugins/ndf/skills/cross-review/scripts/launch-gemini.sh:90` (gemini result schema 指示)
    - `plugins/ndf/skills/cross-review/scripts/launch-codex.sh:81-92` (codex 側 reference)
- 関連 PR: `devbasex/devbase#14` (再現発生 PR、検証参考)
