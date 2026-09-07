# 引継ぎ: v10.5.1 の実装

**この文書は、セッションをまたいで v10.5.1 の実装を続けるための引継ぎである。**
新しいセッションでは、この文書を `/goal` の完了条件として渡す。

```
/goal /work/ai-plugins/issues/handoff-v10.5.1.md の「完了条件」をすべて満たす
```

## いまどこにいるか

**設計は終わり、マージ済みである。実装はこれから始める。**

| 工程 | 状態 |
| --- | --- |
| 要求と受け入れ条件 | 完了。`issues/issue-418-417-workflow-gate/01-requirements.md`（253 行） |
| 設計 | 完了。`issues/issue-418-417-workflow-gate/02-design.md`（299 行） |
| 設計レビュー | 完了。PR #419 を `cross-review` 5 ラウンドで収束させ、**人手の承認を得てマージ済み** |
| 計画 | **未着手。ここから始める** |
| 実装以降 | 未着手 |

`develop` の先端は `7361a96`。作業ツリーは 1 つも残っていない。

## 前提となる決定（設計で確定済み。実装で蒸し返さない）

理由は `02-design.md` の「決定の記録」にある。**結論だけをここに写す。**

| # | 決定 |
| --- | --- |
| 1 | `light` のレビューを `cross-review` にする |
| 2 | モードを判定する単位を **Pull Request** にする。束ねる規約は置かない |
| 2-b | モードの高さは列の位置から導かない |
| 3 | 閉じる語の読み取りを `<プラグインルート>/scripts/lib/` へ**移す**（写しは持たない） |
| 4 | 案内は出すが、拒否はしない（拒否は設計 Pull Request のマージだけ） |
| 5 | `check-doc-line-limit.py` は `git ls-files -z` を使う |
| 6 | `install-hooks.sh` は command を組み立て直す（`str.replace` をやめる） |
| 7 | Pull Request の作成時に求めるのは、レビューより前の必須の工程だけ |
| 8 | `light` を gate の検査から外さない |
| 9 | 控えの鍵は**課題のまま**にする（`stage-check.sh` の引数の契約を変えない） |
| 10 | 承認の関門は 2 つで増やさない。**設計 PR のマージはチャネルに依存せず**、本番へ届く操作だけがチャネルで決まる。`production_branch` を新設し `base_branch` は流用しない |
| 11 | 並行開発の方法は担当の判断に任せる |
| 12 | 手法は任せ、**下限 6 つ**と関門を固定する |

## 実装を 3 本へ分ける

**性質で分ける。同じファイルを触るものをまとめる。**

| 本 | 主題 | 課題 | 受け入れ条件 | 主に触るもの |
| --- | --- | --- | --- | --- |
| 1 | 事後レビューの指摘を直す | #417 | D1〜D10 | `scripts/check-doc-line-limit.py` / `plugins/ndf/dev.agy/install-hooks.sh` / `notion-writing/SKILL.md` / `_drive_auth.py` / `tests/runtime-smoke/assertions/` |
| 2 | 工程表とモード | #418 #420 #422 | A1〜A6 / B1〜B6 | `development-workflow/SKILL.md` の工程表 / `lib/workflow-common.sh` の `WF_STAGE_MATRIX` / `references/workflow-modes.md` / 標準フローの図 |
| 3 | gate と承認の関門 | #424 | C1〜C13 / E1〜E12 / F1〜F11 | `workflow-guard.sh` / `lib/workflow-common.sh` の新しい関数 / `references/approval-request.md` / `.ndf/worktree.json` の宣言 / `closing-issues.sh` の移設 |

**1 は 2 と 3 に依存しない。先に出してよい。**
**2 と 3 はどちらも `lib/workflow-common.sh` を触る。順に進める（並行させない）。**

`#391`（文書の再構成）と `#392`（手順書の重複段落）は v10.5.1 に入っているが、
**この 3 本には含めていない。** #392 は `development-workflow/SKILL.md` を触るため、
**本 2 に入れると競合しない。** #391 は新しい Skill の新設で、別の本になる。

## 完了条件

**次をすべて満たしたとき、この引継ぎは終わる。**

- [ ] 1. 本 1（#417）がマージされている
- [ ] 2. 本 2（#418 / #420 / #422 / #392）がマージされている
- [ ] 3. 本 3（#424）がマージされている
- [ ] 4. `01-requirements.md` の受け入れ条件 58 件すべてに、満たしたことの証跡がある
- [ ] 5. 3 本とも `cross-review` を収束させている（未解決スレッド 0 件）
- [ ] 6. 検査 10 本とテストが終了コード 0 で通る
- [ ] 7. `plan-to-spec` で確定仕様へ移してある
- [ ] 8. `release` で版を上げ、**本番への配布は承認を得てから**行っている
- [ ] 9. `release-verification` と `retrospective` が済んでいる

**#391 / #421 / #423 はこの引継ぎの対象外。** v10.5.1 に入っているが、別の設計工程が要る
（モードの母集合を変える #421 / #423 と、新しい Skill を作る #391）。

## 通す工程

**モードは `standard`。** 設計と設計レビューは済んでいるため、次から始める。

```
implementation-plan → tdd-cycle → refactoring → cross-review → quality-gates → pr
  → plan-to-spec → merged → release → release-verification → retrospective
```

**3 本それぞれについて `cross-review` を通す。** 設計レビューはもう要らない。

## 踏んだ落とし穴（同じことを繰り返さない）

| 落とし穴 | 対処 |
| --- | --- |
| **レビュー用の作業ツリーは detached HEAD。** `git push` だけでは何も送られない | `git push origin HEAD:<ブランチ名>` で送り、`git ls-remote` で届いたか確かめる |
| **進行の記録を呼ばないと gate が一度も走らない** | 工程ごとに `bash plugins/ndf/scripts/projects-sync.sh <課題番号> stage "<工程名>"` を呼ぶ |
| **設計 PR のマージは承認の印が無いと拒否される** | `gh pr edit <番号> --add-label design-approved` を**別のコマンドで**実行してからマージする（同じコマンドに混ぜると hook が拒否する） |
| **`develop` へのマージに承認は要らない** | 本番のチャネル（`main`）へ進めるときだけ承認を取る |
| 新しい方針を途中で織り込むと、その追随漏れが major として返る | 方針が固まってから `cross-review` を回す |

## 検証コマンド

```bash
uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest . -q
python3 scripts/check-skill-frontmatter.py
python3 scripts/check-doc-staleness.py
python3 scripts/check-doc-line-limit.py
python3 scripts/check-markdown-links.py --root .
python3 scripts/check-skill-repo-assumptions.py
python3 scripts/check-cross-skill-refs.py
bash scripts/build-runtime-plugins.sh --check
bash scripts/validate-runtime-plugins.sh
claude plugin validate .
```

**合否は終了コードで見る**（出力の末尾だけを読まない）。

## 参照

- 要求と受け入れ条件: [issue-418-417-workflow-gate/01-requirements.md](issue-418-417-workflow-gate/01-requirements.md)
- 設計: [issue-418-417-workflow-gate/02-design.md](issue-418-417-workflow-gate/02-design.md)
- 設計 Pull Request: https://github.com/devbasex/ai-plugins/pull/419
- v10.5.0 の振り返り: https://github.com/devbasex/ai-plugins/pull/414
- マイルストーン: https://github.com/devbasex/ai-plugins/milestone/12
