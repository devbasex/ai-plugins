# 並行開発 バッチ 01 — 全体指示

3 件の課題を同時に進めるための指示書である。各担当は自分の担当ファイルだけを読めば
着手できる。この文書は、担当どうしが同じ行を書き換えないための境界と、着手の順序を定める。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 担当 | 1 件の課題を最後まで進める実行主体。1 担当 = 1 作業ツリー = 1 Pull Request |
| 検査 | `scripts/validate-runtime-plugins.sh` が行う機械的な突き合わせ |
| 工程 | `development-workflow` が振り分ける開発の段階 |
| 未解決の指摘 | Pull Request 上で Resolve されていないレビューコメントの塊（review thread） |

## 担当と課題

| 担当 | 課題 | 指示書 | ブランチ |
| --- | --- | --- | --- |
| A | 版を上げるときに古くなる記載を検査の対象へ広げる（#178） | [01-issue-178.md](01-issue-178.md) | `feature/issue-178-staleness-checks` |
| B | レビューの収束判定に未解決の指摘を入れる（#33 / #37） | [02-issue-33-37.md](02-issue-33-37.md) | `fix/issue-33-37-unresolved-threads` |
| C | 証跡リンクの置換が働かない状態を直す（#81） | [03-issue-81.md](03-issue-81.md) | `fix/issue-81-evidence-link-rewrite` |

3 件とも `development-workflow` の判定は `standard` である。テストが十分にある領域への
振る舞いの追加・修正にあたる。

```text
必須工程: worktree → requirements-design → implementation-plan → tdd-cycle
  → refactoring → cross-review → quality-gates → pr
  → plan-to-spec（仕様が変わった場合） → merged
  → release-verification（マージ前に実施できなかった受け入れ条件がある場合）
  → retrospective
```

## 担当どうしの境界

同じ行を書き換える組み合わせは無い。3 件は最後まで並行して進められる。

| 担当 | 書き換えてよいパス | 他の担当が触るため書き換えないパス |
| --- | --- | --- |
| A | `scripts/validate-runtime-plugins.sh` / `scripts/tests/` / `README.md` / `plugins/ndf/README.md` / `docs/` | `plugins/ndf/skills/` 配下すべて / `plugins/playwright-kit/` 配下すべて |
| B | `plugins/ndf/skills/cross-review/` / `plugins/ndf/skills/fix/` | `scripts/` / `README.md` / `plugins/ndf/README.md` / `plugins/playwright-kit/` |
| C | `plugins/playwright-kit/skills/playwright-kit-ops/` | `scripts/` / `README.md` / `plugins/ndf/` 配下すべて |

**3 担当とも、配布 Skill の数を変えない。** `plugins/ndf/manifests/*-skills.txt` と
`plugins/ndf/skills/` へのディレクトリ追加・削除は、このバッチの範囲外である。数が動くと
担当 A の検査が突き合わせる値が変わり、3 者の Pull Request が互いの検査を落とす。

生成物の同期（`bash scripts/build-runtime-plugins.sh`）は各担当が自分の Pull Request の中で
実行する。担当 B と C は Skill の中身を変えるため、同期の結果が配布物へ乗る。

## このバッチに入れなかった課題と、その理由

| 課題 | 理由 | 着手できる時期 |
| --- | --- | --- |
| #116 未配布の Skill 4 個を配布する | 配布 Skill の数が 31 → 35 へ動く。担当 A の検査が突き合わせる値そのものを変える | 担当 A のマージ後 |
| #144 Notion 文書作成の Skill を新設 | 同上。Skill が 1 個増える | 担当 A のマージ後 |
| #161 設計工程の成果物を規約化する Skill | 同上に加えて、設計方針の合意が先に要る | 担当 A のマージ後 |
| #176 進行管理に GitHub Projects を使う | 認証の権限（`read:project`）が現在の設定に含まれるかが未確認 | 権限の確認後 |
| #113 / #156 / #158 / #159 | いずれも規模が大きく、単独でバッチ 1 本にあたる | 個別に判断 |

#116 / #144 / #161 は担当 A のマージ後にバッチ 02 として並行して進められる。3 件とも
配布 Skill の数を動かすため、**バッチ 02 の中では直列**になる。

## 完了条件（バッチ全体）

- [ ] 3 件の Pull Request がいずれもマージされている
- [ ] `bash scripts/validate-runtime-plugins.sh` が終了コード 0 で終わる
- [ ] `claude plugin validate` が通る
- [ ] 3 件それぞれの指示書に書かれた完了条件がすべて満たされている
- [ ] 範囲外と判断した課題が `out-of-scope` で issue になっている

## 共通の進め方

### 作業ツリーを用意する

開発の変更は clone したディレクトリではなく作業ツリーの中で行う。担当ごとに 1 本作る。

```bash
bash "$NDF_SCRIPTS/worktree-setup.sh" init      # 宣言ファイルが無ければ作る
. "$NDF_SCRIPTS/lib/worktree-common.sh"
main_dir=$(wt_main_dir)
git -C "$main_dir" fetch origin
git -C "$main_dir" worktree add -b "<ブランチ名>" \
  "$main_dir/.worktrees/<ブランチ名>" origin/main
cd "$main_dir/.worktrees/<ブランチ名>"
```

`issues/` と `docs/` は clone したディレクトリで編集してよい。この指示書もそこにある。

### テストを動かす

このリポジトリの Python テストは `uv` の仮想環境で動かす。システムの `python3` には
`pytest` が入っていない。

```bash
uv run --with pytest pytest <テストのパス> -q
```

### レビューを回す

`standard` はレビューに `cross-review` を使う。3 件を同時に走らせると、外部の CLI が
6 プロセス並走する。実行の間隔は進行の状況を見て決める。

```bash
/ndf:cross-review <PR番号>
```

### 範囲外の課題を見つけたとき

`out-of-scope` を使い、**見つけたその場で** issue にする。判断は 3 択（起票する /
範囲内へ入れる / 起票しない）に限り、3 つ目も理由を 1 行残す。発見からマージ後まで
時間が空くと、どのファイルのどの行で、なぜ範囲外と判断したのかを思い出せなくなる。

## 参照

- [issues/issue-175-release-verification-retrospective.md](../../issue-175-release-verification-retrospective.md) — 直前の版で追加した工程
- `plugins/ndf/skills/development-workflow/SKILL.md` — 工程の振り分けの基準
