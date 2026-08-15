# 実行の骨組みとデータ形式

[← 01-overview.md](01-overview.md)

## 1. 実行の骨組み

`cross-review` と同じ形で、1 本の bash が全体を駆動する。

```bash
PLUGIN_ROOT="${PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT}}"
SCRIPTS="$PLUGIN_ROOT/skills/cross-refactoring/scripts"

# init はホストを確定し、次を eval 用の KEY=VALUE で返す。
#   RUNTIMES   … 提案・レビューの母集合（ホストを除く 3 つ）と RUNTIMES_CSV
#   IMPL_POOL  … 適用の母集合（gemini を除く 3 つ）
# --model は <ランタイム>=<モデル> を繰り返し渡せる（未指定は CLI の既定モデル）。
eval "$("$SCRIPTS/refactor.py" init "$PR" --scope "$SCOPE" ${HOST:+--host "$HOST"} \
          --max-outer-rounds "$MAX_OUTER" --max-fix-rounds "$MAX_FIX" \
          --max-items-per-round "$MAX_ITEMS" ${MODEL_ARGS:+$MODEL_ARGS})"
export CROSS_REFACTORING_TMP_DIR="$TMP_DIR"
"$SCRIPTS/prepare-worktrees.sh" "$PR"        # 作業ディレクトリ準備 + Skill 配置

while :; do                                  # 提案ラウンドの繰り返し
  # start-round は ROUND / IMPL / REVIEWERS / REVIEWERS_CSV を返す
  eval "$("$SCRIPTS/refactor.py" start-round "$PR")" || break
  for a in $RUNTIMES; do
    "$SCRIPTS/launch-cli.sh" "$a" propose "$PR" "$ROUND"
  done
  "$SCRIPTS/monitor.py" "$PR" --agents "$RUNTIMES_CSV" \
      --tmp-dir "$TMP_DIR" --stem-template '{agent}-propose-rf{id}'
  "$SCRIPTS/refactor.py" merge-proposals "$PR" || break   # exit 2 = 採用 0 件で収束

  # 適用は 1 ラウンド 1 回。実装担当が採用項目を優先度順に直列適用する
  "$SCRIPTS/launch-cli.sh" "$IMPL" apply "$PR" "$ROUND"
  "$SCRIPTS/monitor.py" "$PR" --agents "$IMPL" --tmp-dir "$TMP_DIR" \
      --stem-template "{agent}-apply-r$ROUND"
  "$SCRIPTS/refactor.py" merge-apply "$PR" "$ROUND" || continue  # 全件失敗なら次ラウンド

  while :; do                                # レビュー収束の繰り返し
    for r in $REVIEWERS; do
      "$SCRIPTS/launch-cli.sh" "$r" review "$PR" "$ROUND"
    done
    "$SCRIPTS/monitor.py" "$PR" --agents "$REVIEWERS_CSV" --tmp-dir "$TMP_DIR" \
        --stem-template "{agent}-review-r$ROUND"
    "$SCRIPTS/refactor.py" judge-review "$PR" "$ROUND" && break   # 0 = 2 者とも承認
    if "$SCRIPTS/refactor.py" should-abandon "$PR" "$ROUND"; then
      # 指摘が残る項目だけ取り消す（合意済みの項目は残す）
      "$SCRIPTS/refactor.py" abandon-items "$PR" "$ROUND"; break
    fi
    "$SCRIPTS/launch-cli.sh" "$IMPL" fix "$PR" "$ROUND"
    "$SCRIPTS/monitor.py" "$PR" --agents "$IMPL" --tmp-dir "$TMP_DIR" \
        --stem-template "{agent}-fix-r$ROUND"
    "$SCRIPTS/refactor.py" merge-fix "$PR" "$ROUND"
  done
done

# Step 7: 最終ゲート — /ndf:cross-review <PR> をホストが実行
# Step 8: 報告と Draft 解除
"$SCRIPTS/refactor.py" report "$PR"
```

状態ファイルに全ての状態が入るため、どこで落ちても同じコマンド列を叩き直せば再開できる。

`monitor.py` と `launch-cli.sh` の実体は `cross-review` 配下の共有層にあり、本 Skill の
`scripts/` からはそこを読む。範囲と理由は
[09-cross-review-alignment.md](09-cross-review-alignment.md) を参照。

## 2. 振る舞い不変の担保

適用のプロンプトは `refactoring` Skill の手順をそのまま踏ませる。状態管理スクリプト側では
次を**機械的に検証**し、満たさない適用結果は失敗として扱う。

| 検証 | 方法 |
| --- | --- |
| 着手前にテストが成功している | `baseline_test` を状態ファイルに記録。失敗ならラウンドの適用に着手せず全項目を `blocked` にする |
| テストが無い経路は先に現状固定テスト | `test_gap` が真の項目は、固定テストの追加コミットが先行しているかを `git log` で確認 |
| 1 手 1 コミット | 適用結果の `commits[]` が 1 件以上あり、各コミットでテストが成功している |
| 実行主体の明記 | 各コミットに `Item-Id` / `Round` / `Impl-Runtime` / `Impl-Model` のトレーラーが揃っている |
| 差分予算 | `estimated_diff_lines` の 2 倍を超えたら失敗（範囲の逸脱を検知） |
| 機能変更の混入なし | レビュー観点で判定する（機械判定は不可能なためレビュー担当に委ねる） |

## 3. 状態ファイル

配置は `<worktree>/work/.cross_refactoring/cross-refactoring-rf<ID>-state.json`。
`<ID>` は最初に初期化した Pull Request 番号である。

```json
{
  "id": 130,
  "repo": "devbasex/ai-plugins",
  "current_pr": 130,
  "base_branch": "main",
  "head_branch": "refactor/cross-refactoring-target",
  "worktree_root": "/tmp/ndf-worktrees/devbasex--ai-plugins/rf130",
  "worktrees": {"work": "...", "codex": "...", "gemini": "...", "kiro": "..."},
  "target_scope": ["plugins/ndf-shared/skills/cross-review/scripts"],
  "host": "claude",
  "host_detection": "env",
  "runtimes": ["codex", "gemini", "kiro"],
  "impl_capable": ["claude", "codex", "kiro"],
  "models": {"claude": "opus-5", "codex": "gpt-5.5", "gemini": null, "kiro": "claude-opus-5"},
  "skills": {
    "required": ["refactoring", "tdd-cycle", "quality-gates"],
    "claude": {"refactoring": "provisioned", "tdd-cycle": "provisioned", "quality-gates": "provisioned"},
    "codex":  {"refactoring": "preexisting", "tdd-cycle": "provisioned", "quality-gates": "provisioned"},
    "kiro":   {"refactoring": "provisioned", "tdd-cycle": "provisioned", "quality-gates": "provisioned"}
  },
  "max_outer_rounds": 3,
  "max_fix_rounds": 3,
  "max_items_per_round": 5,
  "severity_threshold": "minor",
  "baseline_test": {"command": "pytest -q", "status": "green", "checked_at": "..."},
  "outer_round": 2,
  "phase": "review",
  "rounds": [
    {
      "round": 1,
      "impl": "codex",
      "impl_model": {"requested": "gpt-5.5", "observed": "gpt-5.5"},
      "reviewers": ["gemini", "kiro"],
      "reviewer_models": {"gemini": {"requested": null, "observed": null},
                          "kiro": {"requested": null, "observed": null}},
      "proposed": {"codex": 9, "gemini": 7, "kiro": 8},
      "merged": 14, "adopted": 5, "deferred": 9,
      "items": ["R1-001", "R1-002"],
      "apply": {"applied": ["R1-001", "R1-002"], "failed": [], "base_sha": "aaa1111", "head_sha": "ccc3333"},
      "fix_rounds": 1,
      "durations": {"propose": 182, "apply": 461, "review": 205, "fix": 133},
      "reviews": [
        {"round": 1, "gemini": "REQUEST_CHANGES", "kiro": "APPROVE",
         "findings": [{"item_id": "R1-002", "thread_id": "PRRT_x", "resolved": false}]},
        {"round": 2, "gemini": "APPROVE", "kiro": "APPROVE", "findings": []}
      ]
    }
  ],
  "items": [
    {
      "item_id": "R1-001",
      "round": 1,
      "path": "scripts/state.py",
      "symbol": "cmd_merge_fix",
      "smell": "long_method",
      "technique": "extract_method",
      "severity": "major",
      "rationale": "...",
      "plan": "1. ... 2. ...",
      "test_gap": false,
      "estimated_diff_lines": 40,
      "proposed_by": ["codex", "gemini"],
      "status": "done",
      "commits": ["abc1234", "def5678"]
    }
  ],
  "deferred_items": [],
  "final": null
}
```

`runtimes` は**提案・レビューの母集合**（全ランタイム − ホスト）、`impl_capable` は
**適用の母集合**（全ランタイム − gemini）で、**両者は別物**である。上の例はホストが
Claude Code のケースで、`claude` は `runtimes` に居ないが `impl_capable` には居る。
`impl_capable` はホストによらず常に `["claude", "codex", "kiro"]` になる。

`models` は初期化時に確定する**指定値**で、未指定は `null`（CLI の既定モデル）を意味し、
全ラウンドを通して変えない。`rounds[].impl_model` と `reviewer_models` は `requested`
（指定値）と `observed`（出力から取れた実測値。取れなければ `null`）を分けて持ち、
食い違いを報告時に警告できるようにする。`durations` は計測用の秒数で、監視スクリプトが
持つ開始・終了時刻から求める。

`items[]` は**実装担当・レビュー担当・修正ラウンド数・レビュー履歴を持たない**。
これらはラウンド単位の属性なので `rounds[]` に置く。`items[].commits` は取り消し範囲を
決めるために必須で、**項目ごとに 1 手 1 コミットへ分ける**前提を状態側から支える。

`status` の遷移は `pending` → `applying` → `reviewing` →（`fixing` → `reviewing`）* →
`done` / `abandoned` / `blocked`。適用に失敗した項目はラウンドを止めず、その項目だけ
`abandoned` にして残りの適用を続ける。

## 4. 提案の提出形式

3 ランタイム共通で次の形を使う。

```json
{
  "items": [
    {
      "path": "src/foo/bar.py",
      "symbol": "BarService.handle",
      "smell": "long_method",
      "technique": "extract_method",
      "severity": "major",
      "rationale": "1 メソッドに入力検証・変換・永続化が同居し、分岐が 7 本ある",
      "plan": "1. 検証部を validate_request として抽出\n2. 変換部を to_entity として抽出",
      "test_gap": false,
      "estimated_diff_lines": 40
    }
  ]
}
```

- `smell` はスメル語彙に、`technique` は手法カタログの語彙に**限定する**
  （配置した Skill の `references/` を読ませる）。語彙外はマージ時に `unknown` として警告し、
  最低の重要度へ降格させる。語彙を固定しないと重複排除が効かない。
- 重複排除の鍵は `path` + `symbol` + `smell`。同一の鍵を持つ提案は提案元を統合し、
  `rationale` と `plan` は最も具体的なものを採る。
- 優先度は「合意したランタイム数 → 重要度 → 推定差分行数の昇順」。
  **小さく合意の多いものから直す。**
- `severity_threshold`（既定は `minor`）未満は採用せず、見送り項目として記録する。

## 5. レビュー観点

`docs/03-review-viewpoints.md` に置き、レビューのプロンプトへ埋め込む。

| 観点 | 具体的に見るもの |
| --- | --- |
| 振る舞い不変 | 公開インタフェースの入出力、例外種別、副作用の順序、境界条件 |
| テストの妥当性 | 現状固定テストが実際にその経路を通しているか、実装詳細に結合していないか |
| 手法の適合 | 宣言されたスメルに対して手法が妥当か、別の手法の方が適切でないか |
| 範囲の逸脱 | 提案した手順の範囲を超えた変更が混ざっていないか、機能変更が混入していないか |
| 改善の実質 | 行数が動いただけでなく、責務・依存・可読性が実際に改善しているか |
| コミット分割 | 改名と中身の変更が同一コミットに混ざっていないか、1 手 1 コミットか |
| 性能退行 | ループの入れ替え、呼び出し回数の増加、N+1 の発生 |

判定は `cross-review` と同じく `APPROVE` と `REQUEST_CHANGES` の 2 値とし、`COMMENT` は
使わない。インラインコメントの最小化と、本文に「良い点」を書かない規約も継承する。
