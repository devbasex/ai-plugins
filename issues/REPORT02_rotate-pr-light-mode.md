# REPORT02 実装プラン: cross-review PR ローテーション light モード対応

**Issue**: [issues/REPORT02.md](REPORT02.md)
**作成日**: 2026-05-21
**ステータス**: Draft

## 1. 何のために

`ndf:cross-review` の `--rotate-after` で発火する `scripts/rotate-pr.sh` の挙動が、利用者の期待 (「コメント履歴が長くなった PR を AI Agent が読みやすいようにリセットしたい」だけ) と乖離している。実運用 (PLAN03 PR1) で以下の副作用が顕在化したため、default を **同ブランチで PR を作り直す light モード** に切り替える。

問題点 (issue より引用):

| # | 観点 | 内容 |
|---|---|---|
| 1 | 不要な squash | コミット単位レビューがやり辛くなる |
| 2 | 時刻 suffix のブランチ名 | `feature/...-r014230` で release branch 戦略 / TODO 参照を破壊 |
| 3 | PR title `(rotated)` suffix | 内部用語が PR title に漏れる |
| 4 | PR body の自動上書き | 元 PR の「何のために / 何を / Test plan」が消失 |
| 5 | モード固定 | 利用者が選べない |

## 2. 何を

`scripts/rotate-pr.sh` を `--mode light` (default) / `--mode squash` (opt-in) の 2 モードに分割する。Option A の方針に従い、`--rotate-after` 利用者の **新 default = light**。

### 設計判断 (ユーザ確認済)

| 項目 | 採用 | 理由 |
|---|---|---|
| Default mode | **light** | 新規利用者の期待値に合致。Breaking だが影響範囲は `--rotate-after` 利用者のみで小さい。squash を残すことで救済可能 |
| `--no-rotate` フラグ (Option C) | **実装しない** | light が default なら rotation の害が最小化されるため不要 |
| light モードの title/body 再生成 | **Claude (general-purpose サブエージェント) で生成** | shell template では「現状の差分・実装状態を反映」が困難。codex/gemini は cross-review 内で既に占有されているため Claude (general-purpose) が最適 |
| 新 PR の Draft 状態 | **元 PR の isDraft をコピー** | issue line 119 の期待挙動と一致 |

## 3. light モードの挙動仕様

```
旧 PR #N を close → 新 PR #M を作成
  - branch: 元と同じ (新ブランチ作らない)
  - base:   元と同じ
  - title:  Claude が現状の git log/diff から書き直す
  - body:   Claude が「何のために / 何を / Test plan」を再生成
            ・元 PR の背景セクションは再利用してよい
            ・round N / cross-review / 「rotated」等の内部用語は禁止
  - draft 状態: 元 PR の isDraft をコピー (元が Draft なら新 PR も Draft)
  - 旧 PR への close コメント: 「コメント履歴整理のため新 PR に巻き直し」
```

squash モードは既存挙動を完全維持 (`(rotated)` suffix / 新ブランチ / squash commit / 自動 body)。

## 4. 実装方針

### 責務分割

rotate-pr.sh だけで Claude を呼べないため、Step 6 (rotation) を **3 段階に分割**:

```
Step 6a: rotate-pr.sh prepare <STATE_PR>
         → 元 PR の title/body/isDraft + git log $BASE..HEAD + git diff --stat を
           $TMP_DIR/rotate-pr<STATE_PR>-prepare.json に dump

Step 6b: メインセッションが Agent(subagent_type="general-purpose") を起動して
         title/body を生成 → $TMP_DIR/rotate-pr<STATE_PR>-newtext.json に書き出す
         (squash モードでは Step 6b をスキップ)

Step 6c: rotate-pr.sh execute <STATE_PR> --mode light|squash
         → mode に応じて旧 PR close + 新 PR 作成。
           light は newtext.json を読んで title/body に流す。
           squash は既存ロジック。
         → stdout に NEW_PR / NEW_PR_URL / NEW_BRANCH を吐く (既存契約維持)
```

互換: 既存呼び出し `rotate-pr.sh <STATE_PR>` (引数 1 つ) は **squash モード相当のショートカット** として残し、deprecate warning を stderr に出す (cross-review SKILL.md からの呼び出しは新形式に書き換える)。

### Step 6b のサブエージェントプロンプト方針

書いて **よい** こと (issue より):
- 何のために (背景・動機) — 元 PR から継承可
- 何を (変更内容) — 現在のブランチの実態を反映
- Test plan — 元 PR から継承可

書いて **はいけない** こと:
- 「round N で〜」「cross-review で〜」「レビュー指摘で〜」等の内部運用文言
- 「(rotated)」のような automated suffix
- 「fix された問題」の列挙

## 5. 修正対象ファイル

| ファイル | 変更内容 |
|---|---|
| `plugins/ndf/skills/cross-review/scripts/rotate-pr.sh` | `prepare` / `execute --mode light\|squash` サブコマンド化。light モードロジック新規追加 |
| `plugins/ndf/skills/cross-review/SKILL.md` | 引数表に `--rotate-mode light\|squash` 追加 (default=light)。Step 6 の bash 骨組みを 3 段階呼び出しに更新。アンチパターン節も併せて更新 |
| `plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md` | Step 6 の手順説明を更新。light/squash の違いと Step 6b の Agent 起動例を追加 |
| `plugins/ndf/skills/cross-review/scripts/state.py` | `should-rotate` / `set-current-pr` は light でもそのまま機能するため、コード変更は不要。ドキュメント (docstring) のみ light モードを明記 |

## 6. テスト計画

`cross-review` は GitHub PR を実際に作成するため自動テストは難しい。**手動検証** を中心に置く:

- [ ] **dry-run スクリプト**: `rotate-pr.sh prepare` 単体で実行し、`prepare.json` が想定の構造になることを確認 (PR は触らない)
- [ ] **シェルチェック**: `shellcheck rotate-pr.sh` でエラー 0
- [ ] **light モード E2E (テスト用 PR)**: 検証用ブランチで Draft PR を作り、`rotate-pr.sh execute --mode light` を実行。以下を確認:
  - 旧 PR が close され「コメント履歴整理のため」コメントが付く
  - 新 PR が **同じブランチ・同じ base** で作成される
  - 新 PR の title / body に内部用語 (round / rotated / cross-review) が含まれない
  - 元が Draft なら新 PR も Draft
  - stdout に `NEW_PR=` / `NEW_PR_URL=` / `NEW_BRANCH=` が KEY=VALUE 形式で出力される
- [ ] **squash モード回帰**: `rotate-pr.sh execute --mode squash` で既存挙動が壊れていないことを確認 (新ブランチに `-rHHMMSS` suffix / `(rotated)` suffix / squash commit)
- [ ] **後方互換**: `rotate-pr.sh <STATE_PR>` (旧形式 1 引数) が squash モード相当で動作し、deprecation warning が出る
- [ ] **state.py 整合**: `should-rotate` → `rotate-pr.sh prepare` → Agent → `rotate-pr.sh execute --mode light` → `state.py set-current-pr` の一連で `state.json.current_pr` が正しく更新される

## 7. PR 分割計画

**単一 PR で十分**。理由:

- 変更ファイルが 4 個と少なく、いずれも `skills/cross-review/` 配下に閉じている
- `rotate-pr.sh` の挙動変更 → SKILL.md / docs の追従は不可分。分割すると中間状態でドキュメントと実装が乖離する
- 差分は ~300 行程度に収まる見込み

→ `/ndf:implementation-plan` + `/ndf:pr` の通常フロー。release branch 戦略は採用しない。

ブランチ: `feature/REPORT02-rotate-pr-light-mode`
base: `main`

## 8. アンチパターン (実装時に避けること)

- ❌ `rotate-pr.sh` 内で `claude` CLI を直接呼ぶ — 環境依存・コスト管理外。メインセッションの Agent tool で行う
- ❌ light モードで `gh pr edit` で旧 PR の title/body を流用するだけにする — issue line 153-155 の通り「最終実装を反映して書き直す」が必須
- ❌ Step 6a/6b/6c を 1 つの shell コマンドに無理に押し込む — Agent 呼び出しは bash から不可
- ❌ squash モードを廃止する — backward compat のために残す (Option A 採用、Option B は不採用)
- ❌ 内部用語 (round / rotated / cross-review) を新 PR の title/body に漏らす — Agent プロンプトで明示的に禁止

## 9. 関連

- 元 issue: [issues/REPORT02.md](REPORT02.md)
- 対象 skill: `plugins/ndf/skills/cross-review/`
- 実運用での再現事例: PLAN03 PR1 (`#217` → `#221`)
- 関連 skill: `/ndf:cross-review`, `/ndf:review`, `/ndf:fix`, `/ndf:implementation-plan`, `/ndf:pr`
