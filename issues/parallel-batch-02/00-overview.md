# 並行開発 バッチ 02 — 全体指示

4 件の課題（#175 / #176 / #186 / #188）を対象に、同時に進められるものを並行させる。
この文書は、担当どうしが同じ行を書き換えないための境界と、着手の順序を定める。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 担当 | 1 件の課題を最後まで進める実行主体。1 担当 = 1 作業ツリー = 1 Pull Request |
| 工程表 | `plugins/ndf/skills/development-workflow/SKILL.md` の「モードごとに起動する Skill」の表 |
| 配布 | プラグインの版を上げ、利用者が取得できる状態にすること |
| まとまり | 1 度にマージする Pull Request の集合。単独の変更では 1 件 |

## 担当と課題

| 担当 | 課題 | モード | ブランチ |
| --- | --- | --- | --- |
| A | 作業ツリーで相対パス編集すると主ディレクトリ向けの案内が出る（#186） | `standard` | `fix/issue-186-relative-write-target` |
| B | まとめてマージした後の版上げの担い手と時期を決める（#188） | `architecture` | `feature/issue-188-release-step` |
| C | #175 の残りのリリース後テストを実施する | 工程の残りのみ | 作業ツリー不要 |
| D | 進行管理に GitHub Projects を使う（#176） | `architecture` | `feature/issue-176-github-projects` |

## 判定の根拠

```text
#186  mode: standard
根拠: worktree-guard の書き込み先の判定という本番の振る舞いを直す。対象に既存テストがある
#188  mode: architecture
根拠: 工程表へ配布の工程を足す。複数の Skill にまたがり、公開される Skill が 1 個増える
#176  mode: architecture
根拠: 工程の進行を記録する先を新設する。複数の Skill にまたがり、外部サービスへの書き込みを伴う
```

担当 D は着手の条件が後から整った（次節）。指示書を書いた時点では対象外だった。

担当 C は新しい判定を伴わない。#175 は `architecture` として PR #177 で実装済みであり、
残っているのは完了の定義のうち「版の配布後にリリース後テストを実施し、結果を記録する」
の 1 項目だけである。受け入れ条件 15 件と振り返りは満たされている。

| #175 の完了の定義 | 状態 | 根拠 |
| --- | --- | --- |
| 受け入れ条件 1〜15 | 満たす | 3 Skill が実在し、`manifests/*-skills.txt` の 3 ファイルすべてに載る。呼び出し元 6 Skill に案内がある |
| `architecture` の検証の段階 1〜4 | 満たす | PR #177 で実施 |
| リリース後テストの実施と記録 | **未実施** | 担当 C の範囲 |
| 振り返りを `docs/development-history/` へ残す | 満たす | `04-2026-08-31.md` |

## #176 を当初は着手対象から外し、後から入れた経緯

### 当初の判断（この指示書を書いた時点）

Projects の読み書きには `read:project`（書き込みは `project`）スコープが要る。当時の
トークンには含まれていなかった。

```console
$ gh auth status
  ✓ Logged in to github.com account takemi-ohama (GH_TOKEN)
  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'
```

issue #176 の「決めること」8 件のうち、5 件（Projects をどの単位で持つか / フィールドを誰が
更新するか / 認証スコープをどう配るか / 使えない環境での動作 / 既存 issue を遡って登録するか）
は、実際に Projects を読める状態でないと決められない。そのため、スコープを足した後に着手する
ものとして担当を置かなかった。

**スコープを足さずに書ける範囲**（工程表のどこへ結ぶか、`worktree` と同じ宣言ファイル方式に
できるか）も担当 B の工程表の変更と同じ行を触るため、B のマージ後に回す判断だった。

### その後（担当 D として着手）

トークンを classic の個人アクセストークンへ差し替え、`project` スコープが入った。着手の条件が
整ったため、#176 を担当 D としてこのバッチへ入れた。

- 盤面は作成済み: https://github.com/orgs/devbasex/projects/1 （「NDF 開発の進行」）
- 実装プラン: [issues/issue-176-github-projects.md](../issue-176-github-projects.md)
- 実装: PR #194（`feature/issue-176-github-projects`）

工程表の同じ行を触る点は変わらないため、**担当 B のマージ後に積む**という順序はそのまま残る。

## 担当どうしの境界

| 担当 | 書き換えてよいパス | 他の担当が触るため書き換えないパス |
| --- | --- | --- |
| A | `plugins/ndf/scripts/lib/worktree-common.sh` / `plugins/ndf/scripts/worktree-guard.sh` / `plugins/ndf/skills/worktree/` | `plugins/ndf/skills/development-workflow/` / `merged/` / `release-verification/` / `manifests/` / `README.md` |
| B | `plugins/ndf/skills/development-workflow/` / `merged/` / `release-verification/` / `pr/` / 新設する Skill / `manifests/` / `README.md` / `plugins/ndf/README.md` / `CLAUDE.md` / `.claude-plugin/marketplace.json` / 2 つの `plugin.json` | `plugins/ndf/scripts/` 配下すべて / `plugins/ndf/skills/worktree/` |
| C | `docs/development-history/` / `issues/issue-175-release-verification-retrospective.md` | 上記すべて |

**担当 A は配布 Skill の数を変えない。** 担当 B は 1 個増やすため、`README.md` の公開 Skill 数と
`plugins/ndf/README.md` の記載を動かす。両方が数を触ると、#178 で入れた検査
（`scripts/check-doc-staleness.py`）が互いの Pull Request で落ちる。

担当 D はこの表に入れていない。着手が担当 B のマージ後になり、A / B と同時には動かないためで
ある。触るパスは次のとおりで、A と B の両方と重なる（実績は PR #194 の変更ファイル）。

| パス | 用途 |
| --- | --- |
| `.ndf/projects.json` | 盤面とフィールドの宣言 |
| `plugins/ndf/scripts/lib/projects-common.sh` / `plugins/ndf/scripts/projects-sync.sh` | 宣言の読み取り、値の解決、盤面への書き込み |
| 工程の切れ目を持つ 14 個の `SKILL.md` | 進行を書き込む案内を 1 行ずつ入れる |
| `plugins/ndf/skills/development-workflow/` | 記録先としての位置づけ、`references/projects-tracking.md`、判定のテスト |
| `plugins/ndf/skills/issue-plan-strategy/SKILL.md` | 1 issue = 1 アイテムで Pull Request を複数ぶら下げる旨 |

14 個の `SKILL.md` は `worktree` / `requirements-design` / `implementation-plan` /
`tdd-cycle` / `refactoring` / `cross-review` / `pr-review` / `quality-gates` / `pr` /
`plan-to-spec` / `merged` / `release` / `release-verification` / `retrospective` である
（内訳の根拠は [issues/issue-176-github-projects.md](../issue-176-github-projects.md) の
「案 D」の表）。

この 14 個のうち `worktree` は担当 A が、`development-workflow` / `merged` /
`release-verification` / `pr` は担当 B が同じバッチの中で触る。`release` は担当 B が新設する
Skill であり、B のマージ前には存在しない。担当 D を B のマージ後へ置くのは、この重なりを
順序で避けるためである。

生成物の同期（`bash scripts/build-runtime-plugins.sh`）は各担当が自分の Pull Request の中で
実行する。

## 着手の順序

```mermaid
flowchart LR
    C[担当 C: #175 リリース後テスト] --> B[担当 B: #188 配布の工程]
    A[担当 A: #186 相対パス] -.独立.-> M[まとめてマージ]
    B --> D[担当 D: #176 進行管理]
    B --> M
    C --> M
    D --> M
    M --> V[バッチ全体のリリース後テスト]
```

- **担当 C を担当 B より先に始める。** C の対象は配布済みの v9.4.0 である。B がマージされて
  版が上がると、C が確かめる対象が入れ替わる
- **担当 A は独立している。** 触るパスが A と B/C で重ならないため、いつ始めてもよい
- **担当 D は B のマージ後**。工程表の同じ行を触る。着手の条件（`project` スコープ）が
  整ったのは指示書を書いた後である（前掲の経緯を参照）

## このバッチに入れなかった課題と、その理由

| 課題 | 理由 | 着手できる時期 |
| --- | --- | --- |
| #116 / #144 / #161 | いずれも配布 Skill の数を動かす。担当 B と同じ値を突き合わせる検査が落ちる | 担当 B のマージ後 |
| #113 / #156 / #158 / #159 | いずれも規模が大きく、単独でバッチ 1 本にあたる | 個別に判断 |
| #181 / #182 | バッチ 01 の担当が継続中 | 現行の担当に任せる |

#176 は当初この表に入れていた（理由: `read:project` スコープが無く、決めることの過半を
確かめられなかった）。その後スコープが整い、担当 D としてこのバッチへ入れたため、経緯は
前掲の節へ移した。

## 完了条件（バッチ全体）

- [ ] 担当 A / B / D の Pull Request がいずれもマージされている
- [ ] 担当 C のリリース後テストの結果が記録されている
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] `bash scripts/validate-runtime-plugins.sh` が終了コード 0 で終わる
- [ ] `python3 scripts/check-doc-staleness.py` が終了コード 0 で終わる
- [ ] 範囲外と判断した課題が `out-of-scope` で issue になっている

## 共通の進め方

### 作業ツリーを用意する

開発の変更は clone したディレクトリではなく作業ツリーの中で行う。担当ごとに 1 本作る。

```bash
. plugins/ndf/scripts/lib/worktree-common.sh
main_dir=$(wt_main_dir)
git -C "$main_dir" fetch origin
git -C "$main_dir" worktree add -b "<ブランチ名>" \
  "$main_dir/.worktrees/<ブランチ名>" origin/main
cd "$main_dir/.worktrees/<ブランチ名>"
```

`issues/` と `docs/` は clone したディレクトリで編集してよい。担当 C の出力物は
どちらにも収まるため、作業ツリーを作らない。

### テストを動かす

システムの `python3` に `pytest` は入っていない。`uv` の仮想環境で動かす。

```bash
uv run --with pytest pytest scripts/tests plugins/ndf -q
```

### 範囲外の課題を見つけたとき

`out-of-scope` を使い、**見つけたその場で** issue にする。判断は 3 択（起票する /
範囲内へ入れる / 起票しない）に限り、3 つ目も理由を 1 行残す。

## 参照

- [issues/old/parallel-batch-01/00-overview.md](../old/parallel-batch-01/00-overview.md) — 前回のバッチ
- [issues/issue-175-release-verification-retrospective.md](../issue-175-release-verification-retrospective.md) — #175 の実装計画
- `plugins/ndf/skills/development-workflow/SKILL.md` — 工程の振り分けの基準
