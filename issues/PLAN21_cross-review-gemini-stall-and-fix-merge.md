# PLAN21: cross-review の gemini stall タイムアウト分岐 と fix サブエージェント戻り値マージの堅牢化

- 起票日: 2026-05-23
- 対象 plugin: `ndf` v4.7.3
- 対象 skill: `ndf:cross-review`
- 関連 issue: [issues/i18-issue-gemini.md](./i18-issue-gemini.md)
- 関連 plan (先行): [issues/PLAN20_cross-review-worktree-and-result-schema-fix.md](./PLAN20_cross-review-worktree-and-result-schema-fix.md) (PR #4 で merge 済み)
- 報告者: takemi-ohama (`devbasex/devbase#19` の `/ndf:cross-review 19` 実行中に検出)

## 背景・課題

`/ndf:cross-review` を v4.7.2 (PLAN20 適用前) で実走させた際に、cross-review ループ
自体は完走したが、メインセッション側で手動補正が必要だった運用不整合が i18 に 3 点
報告されている。PLAN20 (v4.7.3 で merge) によって以下が解消済み:

- i18 #2 (gemini `result.json` スキーマ揺れ) — `launch-gemini.sh` の明示 JSON ブロック化と
  `state.py cmd_read_result` の `event`/`intent` フォールバック + 欠落 die で対応済

本 PLAN21 は、PLAN20 では扱われなかった **残り 2 件** を対象とする:

### 問題1: gemini の stall タイムアウト既定 (180s) が gemini に対して短すぎる

`scripts/monitor.py:63` の以下が原因:

```python
DEFAULT_STALL = int(os.environ.get("MONITOR_STALL", "180"))   # 3 min no progress
```

- err.log + stdout.log の合計サイズが 180 秒変化しなければ STALLED 扱いで kill する設計
- codex は推論ステップを逐次 err.log に書き出すため stall 検知が機能する
- gemini はリクエスト送信〜レスポンス受領まで err.log に**ほぼ何も書かない** (起動直後の
  `YOLO mode is enabled.` 等 340B のみ)。「サイズ非変化 = stall」前提と相性が悪い
- 結果として gemini プロセスを 1 度目に毎回 STALLED で kill してしまい、孤児プロセスも
  残る (skill 既知の挙動: `pkill` 後も子プロセスが生き残るケースあり)
- 再実行で `--stall-timeout 480 --timeout 900` を明示すると 195 秒で正常終了する実績あり

ユーザはこの挙動を学習して毎回 `--stall-timeout 480` を明示しているが、本来は plugin が
agent ごとの特性に応じた既定値を持つべき。

### 問題2: fix サブエージェント戻り値ファイルのパス指定が散逸し、merge-fix が
silent に fail する

`scripts/state.py:403` (`cmd_merge_fix`) は以下のみを参照する:

```python
ffile = pathlib.Path(args.file or _tmp_dir() / f"fix-pr{pr}-result.json")
```

`_tmp_dir()` は環境変数 `CROSS_REVIEW_TMP_DIR` または `~/.gemini/tmp/<workspace>/` を返す
(例: `/Users/takemi_ohama/.gemini/tmp/pr19/`)。

一方、SKILL.md の Step 5 例文や mermaid 図には `/tmp/fix-pr<#>-result.json` がハードコード
されており、メインセッションが Agent tool に渡すプロンプトでも `/tmp/fix-pr19-result.json`
が使われた結果、サブエージェントは `/tmp/` に書き込み、`state.py merge-fix` は
`~/.gemini/tmp/pr19/fix-pr19-result.json` を見て **「戻り値ファイルが生成されなかった」
で exit 3 (ci-code-fail 扱い)** になる事象が発生した。

加えて、サブエージェント側が `commit_sha`/`fixed` のキーで戻り値を作ってしまうケースが
あり、`cmd_merge_fix` が期待する `fix_commit`/`fixed_count` キーが取れず `fixed=0` で
silent に記録される事象も観測されている。

両方とも i18 issue 第 3 節で実証されており、SKILL.md / docs と `cmd_merge_fix` の二面で
対応する必要がある。

### 補足: i18 issue #2 (gemini `result.json` スキーマ) は PLAN20 で対応済み

PLAN20 で以下が入っているため、本 PLAN21 では再対応しない:

- `scripts/launch-gemini.sh:90` に codex と同等の JSON スキーマブロックを明示
- `scripts/state.py cmd_read_result` で `event`/`intent` フォールバック + 欠落 die
- ユニットテスト (`tests/test_state_read_result.py`)

i18 issue #2 の報告は v4.7.2 環境での観測であり、v4.7.3 では `intent` 別名でも state に
正しく取り込まれる。万一 v4.7.3 でも何らかのスキーマ漏れが残るようなら別 issue として
切り出す。

## ゴール

1. gemini プロセスを 1 度目に STALLED で kill する誤検知を解消する (per-agent 既定の
   stall タイムアウト)
2. fix サブエージェントの戻り値ファイル受け渡しでパス指定ズレや key 名ズレが起きても
   `state.py merge-fix` が **適切な fallback で拾い上げる**か、もしくは明示的に fail
   して原因が分かるようにする
3. SKILL.md / docs から `/tmp/fix-pr<#>-result.json` ハードコードを排除し、
   `$TMP_DIR/fix-pr<PR>-result.json` で統一する
4. 既存の Linux コンテナ環境 / `CROSS_REVIEW_TMP_DIR` 明示環境の挙動は変えない (後方互換)

## 設計方針

### 1. per-agent DEFAULT_STALL の導入 (問題1)

`monitor.py` に agent 別の既定値 env を追加し、`monitor_agent()` 呼び出し前の解決時に
agent 名で切り替える。

`scripts/monitor.py` 設定セクションを以下に変更:

```python
DEFAULT_TIMEOUT = int(os.environ.get("MONITOR_TIMEOUT", "420"))    # 7 min
# 既定 stall timeout (後方互換のため env MONITOR_STALL は残す)。
# `MONITOR_STALL` は両 agent 共通のデフォルトとして引き続き受け付ける。
DEFAULT_STALL = int(os.environ.get("MONITOR_STALL", "180"))         # 3 min no progress
# per-agent 上書き (gemini は stdout/stderr に進捗を出さないため大きめ)。
# `MONITOR_STALL_<AGENT>` が指定されていなければ DEFAULT_STALL_AGENT_BUILTIN を使う。
DEFAULT_STALL_AGENT_BUILTIN = {
    "codex": 180,    # 推論ログを逐次出すので 3 min で十分
    "gemini": 480,   # err.log が静かなため 8 min まで許容
}


def _agent_stall_default(agent: str) -> int:
    env_key = f"MONITOR_STALL_{agent.upper()}"
    if env_key in os.environ:
        return int(os.environ[env_key])
    if "MONITOR_STALL" in os.environ:
        return DEFAULT_STALL
    return DEFAULT_STALL_AGENT_BUILTIN.get(agent, DEFAULT_STALL)
```

`main()` の per-agent ループで、`--stall-timeout` が CLI 引数で明示されていなければ
agent ごとの既定を採用する形に変更:

```python
# argparse 側: default は文字列 "auto" を入れ、明示時のみ int として上書き
p.add_argument("--stall-timeout", default=None,
               help="stall timeout (err.log no progress) in seconds. "
                    "未指定時は agent 別既定 (codex=180, gemini=480) または "
                    "env MONITOR_STALL_<AGENT> / MONITOR_STALL")

# ループ内:
def run(agent: str) -> None:
    stall = int(args.stall_timeout) if args.stall_timeout is not None \
            else _agent_stall_default(agent)
    results[agent] = monitor_agent(
        agent=agent, pr=args.pr,
        timeout=args.timeout, stall_timeout=stall,
        ...
    )
```

**ポイント**:

- CLI 引数 `--stall-timeout` の明示優先 (既存ユーザの上書きを尊重)
- env `MONITOR_STALL_CODEX` / `MONITOR_STALL_GEMINI` で per-agent 上書き
- env `MONITOR_STALL` 共通指定も後方互換で残す (両 agent に同じ値)
- いずれも未指定なら agent 別ビルトイン (codex=180s, gemini=480s)
- 既定が変わるのは gemini のみ (180 → 480)。codex は不変

### 2. ドキュメント追記 (問題1 派生)

`docs/01-state-and-review.md` の monitor 説明 (Step 0〜2 周辺) に以下を追記:

- 「**gemini は err.log がほぼ無音のため、stall timeout の既定は 480 秒** にしている」
- 「上書きしたい場合は `MONITOR_STALL_GEMINI` env か `--stall-timeout` を使う」
- 既存の「`--stall-timeout` または `MONITOR_STALL` で上書き可」記述は per-agent
  既定の説明と整合させる

`SKILL.md` Step 2 の monitor.py 呼び出し例には現状 `--stall-timeout` が無いため
追記不要。

### 3. `cmd_merge_fix` の fallback と key alias (問題2 本体)

`state.py cmd_merge_fix` を以下に変更:

```python
def cmd_merge_fix(args: argparse.Namespace) -> None:
    """Step 5 後段 — fix サブエージェント戻り値を state にマージ + CI 分類。

    Exit code: 0=continue, 3=ci-code-fail (final=error)
    """
    pr = args.pr

    # 戻り値ファイルの探索順:
    #   1. --file 明示
    #   2. $TMP_DIR/fix-pr<PR>-result.json (正規)
    #   3. /tmp/fix-pr<PR>-result.json (メインセッションが旧プロンプトで /tmp を指定した場合の救済)
    candidates: list[pathlib.Path] = []
    if args.file:
        candidates.append(pathlib.Path(args.file))
    candidates.append(_tmp_dir() / f"fix-pr{pr}-result.json")
    candidates.append(pathlib.Path(f"/tmp/fix-pr{pr}-result.json"))

    ffile: pathlib.Path | None = None
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            ffile = c
            break
    if ffile is None:
        die(
            "fix サブエージェントが戻り値ファイルを生成しなかった "
            f"(checked: {[str(c) for c in candidates]})",
            code=3,
        )

    fix = json.loads(ffile.read_text())

    # key 名 fallback (サブエージェントが別名で書いた場合の救済。仕様としては
    # fix_commit / fixed_count が正、commit_sha / fixed は別名)
    fix_commit = fix.get("fix_commit") or fix.get("commit_sha")
    fixed_count = fix.get("fixed_count")
    if fixed_count is None:
        fixed_count = fix.get("fixed", 0)

    st = _load(pr)
    if not st.get("rounds"):
        die("state.rounds が空。`state.py start-round` を先に呼んでください", code=3)
    round_no = st["rounds"][-1]["round"]

    st["rounds"][-1]["fix"] = {
        "commit": fix_commit,
        "fixed": fixed_count,
        "deferred": len(fix.get("deferred", []) or []),
        "rejected": len(fix.get("rejected", []) or []),
        "resolved_threads": len(fix.get("resolved_threads", []) or []),
        "ci": fix.get("ci_status"),
        "ci_failed_checks": fix.get("ci_failed_checks", []) or [],
        "ci_note": fix.get("ci_note"),
        "by_severity": fix.get("by_severity", {}),
    }
    st["rounds"][-1]["ended_at"] = _now()
    for d in (fix.get("deferred") or []):
        st["deferred_nits"].append({**d, "pr": pr, "round": round_no})
    _save(pr, st)

    # 以降 CI 分類は既存のままだが、ローカル変数で書き換え:
    if (fix.get("ci_status") or "").upper() != "FAILURE":
        info(f"✅ fix マージ完了 (commit={fix_commit} fixed={fixed_count})")
        return
    ...
```

**ポイント**:

- 探索順は (1) `--file` 明示 → (2) `_tmp_dir()` → (3) `/tmp/` の 3 段
- 全候補が無ければ list を含めて die (どこを見たか分かる)
- key 別名は `commit_sha` / `fixed` の 1 段のみ。それ以外は将来別名が出てきた時に追加
- 既存仕様 (`fix_commit` / `fixed_count` で書く) はそのまま動く

### 4. SKILL.md / docs のパス記述統一 (問題2 派生)

以下のハードコードを `$TMP_DIR/fix-pr<PR>-result.json` に統一する:

- `SKILL.md:129` (mermaid 図中の `→ /tmp/fix-pr<#>-result.json`)
- `SKILL.md:193` (Step 5 説明コメント `# Step 5: 修正サブエージェント起動 (Agent tool) → /tmp/fix-pr<STATE_PR>-result.json`)
- `SKILL.md:322` (fix 戻り値ファイル節 `(/tmp/fix-pr<PR>-result.json)`)
- `SKILL.md:374` の「`/tmp/` に置き」表現は **「`$TMP_DIR/` (= `_tmp_dir()` 解決先) に置き」** に
  書き換え (general な「すべて tmp dir」の意味のため、ハードコードは避ける)

`docs/02-fix-and-rotation.md` は既に `$TMP_DIR/fix-pr<PR>-result.json` 表現になっており修正不要。

**追加**: `docs/02-fix-and-rotation.md` の Step 5 手順節に **`$TMP_DIR` の解決順
(env `CROSS_REVIEW_TMP_DIR` > `~/.gemini/tmp/<workspace>/` > `/tmp/`) を 1 行で明示**
してメインセッション側がパスを書き出す際の参考になるようにする。

### 5. テスト (軽量)

`plugins/ndf/skills/cross-review/tests/` 配下に以下を追加:

**`tests/test_monitor_stall_default.py`** (新規):

- `_agent_stall_default("codex")` がビルトイン 180 を返すこと
- `_agent_stall_default("gemini")` がビルトイン 480 を返すこと
- env `MONITOR_STALL_GEMINI=600` 設定時に 600 が返ること
- env `MONITOR_STALL=240` 設定時に両 agent ともに 240 が返ること (env 上書き）
- `MONITOR_STALL_GEMINI` と `MONITOR_STALL` の両方が設定された場合は per-agent 優先

**`tests/test_state_merge_fix.py`** (新規):

- 正規パス (`_tmp_dir()/fix-prN-result.json`) + 正規 key (`fix_commit` / `fixed_count`)
  → state に正しくマージされる
- 正規パスに無く `/tmp/fix-prN-result.json` にある場合 → fallback で拾える
- key が `commit_sha` / `fixed` の別名でも fix.commit / fix.fixed が埋まる
- 戻り値ファイルが 1 箇所も無い場合は `SystemExit(3)` で die + 探索 path 一覧が
  stderr に出る

`pytest` は PLAN20 同様 CI 強制せず、開発者ローカルで
`uv run pytest plugins/ndf/skills/cross-review/tests` で全 pass を確認する。

### 6. バージョン更新

- `plugins/ndf/.claude-plugin/plugin.json` を `4.7.3` → `4.7.4` (patch bump、バグ修正のみ)
- 開発履歴 (`CHANGELOG.md` または `README.md` の該当節) に v4.7.4 のエントリ追加

## 実装タスク

### Phase 1: monitor.py per-agent stall (問題1 本体)
- [ ] `scripts/monitor.py` 設定セクションに `DEFAULT_STALL_AGENT_BUILTIN` 辞書と
      `_agent_stall_default(agent)` ヘルパを追加
- [ ] `main()` の `--stall-timeout` argparse default を `None` に変更し、help を更新
- [ ] `main()` `run(agent)` 内で per-agent 既定を解決して `monitor_agent()` に渡す
- [ ] 既存の `DEFAULT_STALL` 定数は残し、`MONITOR_STALL` env 共通指定の挙動を維持

### Phase 2: ドキュメント追記
- [ ] `docs/01-state-and-review.md` の monitor 説明節 (Step 0〜2 周辺) に gemini の
      stall 既定 480 秒 + 上書き方法を追記
- [ ] 既存「`--stall-timeout` または `MONITOR_STALL` で上書き可」記述に per-agent
      既定 + `MONITOR_STALL_<AGENT>` env を追記
- [ ] codex 側既定 180 秒は不変である旨も明記 (gemini だけ調整した意図を残す)

### Phase 3: state.py cmd_merge_fix の堅牢化 (問題2 本体)
- [ ] `cmd_merge_fix` に candidates list ベースの探索ロジックを実装 (3 段 fallback)
- [ ] 全候補不在時に die メッセージへ探索 path 一覧を含める
- [ ] key 別名 (`commit_sha`, `fixed`) フォールバックを `fix_commit` / `fixed_count` に
      対して追加
- [ ] info ログを新しい変数 (`fix_commit`, `fixed_count`) ベースに書き換え
- [ ] CI 分類ロジック以降 (line 430〜) は既存挙動を保つ

### Phase 4: SKILL.md / docs のパス記述統一 (問題2 派生)
- [ ] `SKILL.md:129` の mermaid 中 `→ /tmp/fix-pr<#>-result.json` を
      `→ $TMP_DIR/fix-pr<#>-result.json` に
- [ ] `SKILL.md:193` の Step 5 コメント `→ /tmp/fix-pr<STATE_PR>-result.json` を
      `→ $TMP_DIR/fix-pr<STATE_PR>-result.json` に
- [ ] `SKILL.md:322` `(/tmp/fix-pr<PR>-result.json)` を `($TMP_DIR/fix-pr<PR>-result.json)` に
- [ ] `SKILL.md:374` 周辺の「すべて `/tmp/` に置き」表現を「すべて `$TMP_DIR/`
      (= `_tmp_dir()` 解決先) に置き」に
- [ ] `docs/02-fix-and-rotation.md` Step 5 手順節に `$TMP_DIR` の解決順を 1 行追記

### Phase 5: テスト追加
- [ ] `tests/test_monitor_stall_default.py`:
    - [ ] codex / gemini ビルトイン値ケース
    - [ ] env `MONITOR_STALL_GEMINI` 上書きケース
    - [ ] env `MONITOR_STALL` 共通指定ケース (両 agent に効く)
    - [ ] per-agent env と共通 env 併用時に per-agent 優先となること
- [ ] `tests/test_state_merge_fix.py`:
    - [ ] 正規パス + 正規 key ケース
    - [ ] `/tmp/` fallback パスケース
    - [ ] `commit_sha` / `fixed` 別名 key ケース
    - [ ] 全候補不在で `SystemExit(3)` + 探索 path 一覧 stderr ケース
- [ ] ローカルで `uv run pytest plugins/ndf/skills/cross-review/tests` を回し全 pass

### Phase 6: バージョン更新
- [ ] `plugins/ndf/.claude-plugin/plugin.json` `version` を `4.7.4` に
- [ ] 開発履歴 (CHANGELOG / README の該当節) に v4.7.4 エントリ追加
- [ ] エントリ内容: monitor per-agent stall, merge-fix fallback / key alias, docs 統一

### Phase 7: 検証 (手動)
- [ ] `monitor.py both` を `--stall-timeout` 引数なしで実行し、gemini 側が 480s
      まで stall を待つことを log で確認 (例: 200s 経過時点で stall 判定されない)
- [ ] codex 側は引き続き 180s で stall になることを確認
- [ ] サブエージェントが `/tmp/fix-pr<PR>-result.json` に書いた状態で
      `state.py merge-fix <PR>` を呼び、`$TMP_DIR` ではなく `/tmp/` の戻り値を
      拾って state にマージされることを確認
- [ ] サブエージェントが `{"commit_sha":"abc","fixed":3,...}` で戻り値を書いた状態で
      `state.py merge-fix` を呼び、state.rounds[-1].fix.commit / fixed に値が入る
      ことを確認
- [ ] 全候補に戻り値ファイルが無い状態で `state.py merge-fix` を呼び、exit 3 と
      探索 path 一覧の stderr を確認

## PR 構成

**単一 PR で実装する** (合計差分は ~180 行以内の見込み、release branch 不要):

- branch: `fix/PLAN21-cross-review-gemini-stall-and-fix-merge`
- base: `main`
- 流れ:
  1. 上記 Phase 1〜6 を順次 commit (Phase 単位で分けると review しやすい)
  2. `/ndf:review-branch` でセルフレビュー
  3. `/ndf:pr` で PR 作成 (本 plan を `## Plan` セクションで参照)
  4. レビュー対応後 squash merge

multi-PR 化が必要になりそうな兆候 (例: monitor 側の per-agent 化が大きく波及する、
fixture が増える) が出てきたら、Phase 1〜2 (monitor) と Phase 3〜4 (state + docs) で
分割する余地はあるが、現状の差分規模では単一 PR で十分とみなす。

## 互換性方針

- **後方互換あり**:
    - `--stall-timeout` を CLI で明示しているユーザの挙動は不変
    - `MONITOR_STALL` env 指定ユーザは両 agent ともその値が適用される (既存と同じ)
    - 既存の正規 fix 戻り値 (`fix_commit` / `fixed_count` キー、`$TMP_DIR` 配下) も
      引き続き正規系
- **新規挙動**:
    - 何も指定していない場合の gemini 既定が 180 → 480 秒に増加 (kill されにくくなる)
    - `cmd_merge_fix` が `/tmp/` fallback で戻り値を拾う
    - `commit_sha` / `fixed` 別名 key も受理
    - 戻り値不在時のエラーメッセージに探索 path 一覧が含まれる
- 旧挙動でユーザが暗黙依存していた可能性のあるもの (gemini 既定が 180s であること) は、
  挙動として **緩める方向**のため破壊的ではないと判断。CHANGELOG で言及する。

## リスクと対策

| リスク | 対策 |
|---|---|
| gemini 既定 480s で実際にハングしている時の検出が 5 分遅れる | `DEFAULT_TIMEOUT=420` は据え置きのため hard timeout は変わらない (5 min 経過時点でハード kill) |
| `/tmp/` fallback で他プロセスの古い fix-prN-result.json を拾う | 候補は **size > 0 かつ exists** で確認、かつ 1 番目に `--file` 明示と `_tmp_dir()` を優先するため fallback は最終手段 |
| 別名 key 対応が将来の本物のスキーマ違反を隠す | 別名は `commit_sha` / `fixed` の 1 段のみ。それ以外は silent に None になる現挙動と同じ (悪化しない) |
| per-agent 既定変更でテストや CI が回らなくなる | 既定変更は gemini のみ。codex 利用箇所は影響なし |
| `--stall-timeout` argparse の default 変更 (`int → None`) で他から呼ばれている script が壊れる | `monitor.py` は CLI 経由でのみ呼ばれており、helper として import している箇所は無いことを `grep` で確認の上で対応 |

## 完了の定義

- [ ] `monitor.py both` を引数なしで実行したとき、gemini 側の stall timeout が
      480 秒で適用される
- [ ] codex 側の stall timeout は引き続き 180 秒
- [ ] env `MONITOR_STALL_GEMINI=600` 指定でその値が gemini にだけ反映される
- [ ] `state.py merge-fix <PR>` がサブエージェント戻り値を `$TMP_DIR` → `/tmp/` の
      順で探索し、どちらにあっても拾える
- [ ] サブエージェントが `commit_sha` / `fixed` 別名で書いても fix.commit /
      fix.fixed に値が入る
- [ ] 全候補不在時に exit 3 + 探索 path 一覧の stderr が出る
- [ ] 追加した pytest が全て pass する
- [ ] plugin version が `4.7.4` に上がり、CHANGELOG / 開発履歴に v4.7.4 の節がある
- [ ] SKILL.md から `/tmp/fix-pr<#>-result.json` ハードコードが取り除かれている
      (mermaid / Step 5 例 / 戻り値節 / 「すべて `/tmp/`」記述)

## 参考

- [issues/i18-issue-gemini.md](./i18-issue-gemini.md) — 元 issue (再現手順 / 回避策 / 修正提案を含む)
- [issues/PLAN20_cross-review-worktree-and-result-schema-fix.md](./PLAN20_cross-review-worktree-and-result-schema-fix.md) — 先行 plan (#2 を解消)
- 該当コード:
    - `plugins/ndf/skills/cross-review/scripts/monitor.py:63` (DEFAULT_STALL)
    - `plugins/ndf/skills/cross-review/scripts/monitor.py:510-540` (main / argparse)
    - `plugins/ndf/skills/cross-review/scripts/state.py:397-432` (cmd_merge_fix)
    - `plugins/ndf/skills/cross-review/SKILL.md:129,193,322,374` (`/tmp/fix-pr...` ハードコード)
    - `plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md:28,121,159` (既に `$TMP_DIR`)
- 関連 PR: `devbasex/devbase#19` (再現発生 PR、検証参考。v4.7.2 で観測)
