# REPORT06: ndf:cross-review の PR ローテーション仕様 修正依頼

## 何のために

`ndf:cross-review` の `--rotate-after` で発火する PR ローテーション (`scripts/rotate-pr.sh`) の挙動が、利用者の意図と乖離している。**「コメント履歴が長くなった PR を AI Agent が読みやすいようにリセットしたい」だけ** のケースで、不要な squash と新ブランチ生成が走り、release branch 戦略 / TODO 参照 / レビューの粒度を壊してしまう。実運用で問題が出た (PLAN03 PR1) ため、ndf 側に修正を依頼する。

## 何を

`scripts/rotate-pr.sh` を **同ブランチで PR を作り直す軽量モード (default)** と、**squash + 新ブランチを作る重量モード (opt-in)** の 2 モードに分割する。または既存の `--rotate-after` を軽量化し、重量モードは別フラグに退避する。

## 発生事象

### 現状の `rotate-pr.sh` の挙動

```bash
# 既存ブランチ feature/PLAN03-infra-gamma-docs-prep を起点に
git checkout -b "${BRANCH}-r$(date +%H%M%S)"   # -rHHMMSS suffix を付与
git reset --soft "origin/$BASE"                 # squash
git commit -m "<title> (cross-review rotation: PR #<OLD> を squash 統合)"
git push -u origin "$NEW_BRANCH"

# 旧 PR を close
gh pr close "$OLD_PR"

# 新 PR を作成
gh pr create --title "$TITLE (rotated)" --body "...automated body..."
```

### 問題点

| # | 観点 | 内容 |
|---|---|---|
| 1 | **不要な squash** | コミット単位レビューがやり辛くなる。元 PR の修正履歴 (round 1〜6 で何を直したか) が 1 commit に潰れる |
| 2 | **時刻 suffix のブランチ名** | `feature/...-r014230` のような可読性低い名前。**release branch 戦略 / TODO.md / 他 PR の参照を破壊** する |
| 3 | **PR title 末尾の `(rotated)`** | サイクル運用の内部用語が PR title に漏れる |
| 4 | **PR body の上書き** | 元 PR で丁寧に書いた「何のために / 何を / Test plan」が automated body に置き換わる |
| 5 | **モード固定** | 利用者が「コメント履歴だけリセットしたい」場合の選択肢がない |

### 実運用での再現

PLAN03 PR1 (`/ndf:cross-review 217 --max-rounds 6 --rotate-after 5`) で、round 5 終了後 `should-rotate` が true を返した。

- release PR (#216) → PR1 (#217) という release branch 戦略 を採用していた
- TODO.md / `08-rollout.md` で PR 番号と branch 名を多数の箇所から参照
- 単純に「履歴が読みづらいから PR を作り直したい」だけだった

このため `rotate-pr.sh` を実行せず、手動で以下を行うことで問題回避した:

```bash
# 同ブランチで close → 新規 PR 作成 (title / body も維持)
gh pr comment 217 --body "ℹ️ レビューコメント履歴が長くなったため..."
gh pr close 217
gh pr create --base "release/PLAN03-docs-import" \
             --head "feature/PLAN03-infra-gamma-docs-prep" \
             --title "$ORIGINAL_TITLE" --body "$ORIGINAL_BODY"
gh pr ready 221 --undo   # Draft 維持
```

## 修正提案

### Option A: モード分離 (推奨)

`rotate-pr.sh` に `--mode` を追加し、default を軽量化:

```
--mode light    (default): 同ブランチで close → 新規 PR 作成。
                            title / body は **現状の差分・実装状態を反映して書き直す**。
                            ただし review-fix サイクルが回ったこと自体や round 番号などの
                            内部用語は出さない (PR を読む人は cross-review を意識しない)。
--mode squash  : 既存挙動 (squash + 新ブランチ + (rotated) suffix)
```

`/ndf:cross-review` 側にも `--rotate-mode light|squash` を生やす。

#### light モードの title / body 生成方針

新 PR の title / body は以下の素材から「PR の最終形」を表現する文書として書き直す:

- `git log $BASE..HEAD` (最終的に含まれる commit メッセージ)
- `git diff $BASE..HEAD --stat` + 主要変更ファイルの内容
- 元 PR の **背景セクション** (「何のために」「Test plan」など、設計意図の部分) は再利用してよい

書いて **よい** こと:
- 何のために (背景・動機) — 元 PR から継承可
- 何を (変更内容) — 現在のブランチの実態を反映
- Test plan — 元 PR から継承可

書いて **はいけない** こと:
- 「round N で〜」「cross-review で〜」「レビュー指摘で〜」など内部運用の文言
- 「(rotated)」のような automated suffix
- 「fix された問題」の列挙 (PR の読者には不要なノイズ)

### Option B: light モードのみに変更 (Breaking change)

squash モードは利用ケースが稀なので default を light に切り替え、squash は廃止 or 別 skill に切り出す。

### Option C: 設定で抑止する逃げ道だけ用意

`--no-rotate` フラグで rotation 自体をスキップできるようにする (現状 should-rotate が true で必ず実行されるため、ユーザが介入できない)。最低限これだけでも欲しい。

## 修正対象ファイル

- `skills/cross-review/scripts/rotate-pr.sh`
- `skills/cross-review/SKILL.md` (rotation セクションの説明更新)
- `skills/cross-review/docs/02-fix-and-rotation.md` (Step 6 の説明更新)
- `skills/cross-review/scripts/state.py` (`should-rotate` / `set-current-pr` の挙動が light モードでも整合するかの確認)

## 期待する default 挙動 (light モード)

```
旧 PR #217 を close → 新 PR #221 を作成
  - branch: feature/PLAN03-infra-gamma-docs-prep (変更なし)
  - title: 現状の差分を反映して書き直す
            (例: scope が広がっていれば title もそれに合わせる)
  - body: 現状の差分・最終実装に合わせて書き直す
          - 何のために / 何を / Test plan を再生成
          - 元 PR の背景セクションは再利用してよい
          - review-fix が発生したこと / その内容は書かない
  - draft 状態: 元 PR と同じ (元が Draft なら新 PR も Draft)
  - 旧 PR への close コメント: 「コメント履歴整理のため新 PR に巻き直し」のような短い説明
```

これで以下が成立する:

- release branch 戦略 / TODO.md / 他 PR の参照が壊れない
- PR の commit 履歴が squash されず、レビュー粒度を維持
- automated な (rotated) suffix が title に付かない
- 最終的な PR の title / body が **現状の実装** を正しく説明している (元 PR から実装が変わっている場合に古い説明が残らない)
- PR を読む人 (将来のレビュアー / 後続 PR を作る人) は cross-review の存在を意識しなくて済む

## 参考: 実運用で書いたフォールバック手順

```bash
# 1. 旧 PR の情報を保存 (base / head / 元 title・body は再生成の素材として保持)
gh pr view "$OLD" --json title,body,headRefName,baseRefName,isDraft > /tmp/pr-info.json

# 2. close 通知 + close
gh pr comment "$OLD" --body "ℹ️ レビューコメント履歴が長くなったため、AI Agent の可読性向上のために本 PR を一度 close し、同じブランチ \`$HEAD\` で新 PR を作り直します。ブランチの内容・base は変えません。"
gh pr close "$OLD"

# 3. title / body は git log $BASE..HEAD と git diff から再生成して使う
#    (cross-review の round や fix 内容は書かない)
gh pr create \
  --base "$(jq -r .baseRefName /tmp/pr-info.json)" \
  --head "$(jq -r .headRefName /tmp/pr-info.json)" \
  --title "<最終実装に合わせて書き直す>" \
  --body  "<何のために / 何を / Test plan を再生成>"

# 4. 元が Draft だった場合 Draft 化
gh pr ready "$NEW" --undo
```

**注**: 今回 PLAN03 PR1 の手動運用では時間都合で元の title/body をそのままコピーしたが、
本来は **現状の最終実装** を反映した title/body に書き直すのが望ましい。
ndf 側で light モードを実装する際はこの再生成を必須の挙動とする。

## 優先度

**Medium**。現状 squash モードでも `--rotate-after` を非常に大きな値にして発火させない運用は可能だが、defaults が「履歴リセット」用途と乖離しているのは UX 問題。

## 関連

- PLAN03 PR1 (`#217` → `#221`) の cross-review 実運用 — 旧 PR は close、新 PR で継続
- `issues/PLAN03/TODO.md` (PR1 行 / PR1 実装セクション / 進捗チェックリスト)
- ndf プラグイン: `skills/cross-review/`
