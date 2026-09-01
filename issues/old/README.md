# issues/old/ — 完了した記録

完了した issue の実装計画、実機試行の記録、報告書、調査資料を置く。履歴として読むためのもので、
運用判断の根拠には使わない。確定した仕様は [docs/specifications/](../../docs/specifications/) を、
リリース済みの変更点は `CLAUDE.md` を参照する。

## cross-refactoring（issue #113）の記録

`/ndf:cross-refactoring` の設計の初版と、7 回の実機試行、そこで見つけた不具合の修正計画である。
現行の実装計画は [../issue-113-cross-refactoring.md](../issue-113-cross-refactoring.md) にある。

| ファイル | 内容 |
| --- | --- |
| [issue-113-cross-refactoring/](issue-113-cross-refactoring/01-overview.md) | 設計の初版。9 ファイルに分割 |
| [issue-113-handoff.md](issue-113-handoff.md) | 設計と事前調査の段階の引き継ぎ |
| [issue-113-task3-cli-verification.md](issue-113-task3-cli-verification.md) | CLI 非対話実行の検証記録 |
| `issue-113-cross-refactoring-*trial*.md` | 1〜7 回目の実機試行の計画と報告 |
| [issue-113-cross-refactoring-defect-fixes.md](issue-113-cross-refactoring-defect-fixes.md) | v8.2.0 で直した不具合 9 件 |
| [issue-113-cross-refactoring-push-ownership.md](issue-113-cross-refactoring-push-ownership.md) | v8.3.0 の公開の責務の一本化 |
| [issue-113-cross-refactoring-re-retrial.md](issue-113-cross-refactoring-re-retrial.md) | v8.5.0 で直した不具合 4 件 |
| [issue-113-cross-refactoring-fix-handoff.md](issue-113-cross-refactoring-fix-handoff.md) | v8.5.4 時点の状態と編集対象 |

## 完了した issue

| issue | 内容 | ファイル |
| --- | --- | --- |
| [#33](https://github.com/devbasex/ai-plugins/issues/33) / [#37](https://github.com/devbasex/ai-plugins/issues/37) | cross-review の収束判定へ未解決の指摘を入れる | [issue-33-37-unresolved-threads.md](issue-33-37-unresolved-threads.md) / [issue-33-cross-review-resume-open-threads.md](issue-33-cross-review-resume-open-threads.md) / [issue-37-cross-review-reply-resolve-guard.md](issue-37-cross-review-reply-resolve-guard.md) |
| [#35](https://github.com/devbasex/ai-plugins/issues/35) | 個別 PR の cross-review がスキップされる | [issue-35-issue-plan-cross-review-required.md](issue-35-issue-plan-cross-review-required.md) |
| [#38](https://github.com/devbasex/ai-plugins/pull/38) | 分析可能なコードスタイルのルール（`refactoring` の `data-representation.md` へ結実） | [issue-38-coding-rule.md](issue-38-coding-rule.md) / [issue-38-chatgpt-response.md](issue-38-chatgpt-response.md) |
| [#81](https://github.com/devbasex/ai-plugins/issues/81) | 証跡リンクの置換が働かない状態を直す | [issue-81-evidence-link-rewrite.md](issue-81-evidence-link-rewrite.md) |
| [#83](https://github.com/devbasex/ai-plugins/issues/83) | `ndf:review` を `pr-review` へ改名する | [issue-83-review-rename.md](issue-83-review-rename.md) |
| [#146](https://github.com/devbasex/ai-plugins/issues/146) | 更新を全て git worktree で行う Skill の作成 | [issue-146-worktree-first/](issue-146-worktree-first/01-spec-and-plan.md) |
| [#163](https://github.com/devbasex/ai-plugins/issues/163) | 多義語を定義せずに使わないルールの追加 | [issue-163-polysemy-rule.md](issue-163-polysemy-rule.md) |
| [#173](https://github.com/devbasex/ai-plugins/issues/173) | 作業ツリー運用の実機確認で見つかった不具合 | [issue-173-worktree-runtime-defects.md](issue-173-worktree-runtime-defects.md) |
| [#175](https://github.com/devbasex/ai-plugins/issues/175) | リリース後テストと振り返りの工程を追加する | [issue-175-release-verification-retrospective.md](issue-175-release-verification-retrospective.md) |
| [#176](https://github.com/devbasex/ai-plugins/issues/176) | 進行の記録に GitHub Projects を使う | [issue-176-github-projects.md](issue-176-github-projects.md) |
| [#178](https://github.com/devbasex/ai-plugins/issues/178) | 版を上げるときに古くなる記載を検査の対象へ広げる | [issue-178-doc-staleness-checks.md](issue-178-doc-staleness-checks.md) |
| [#188](https://github.com/devbasex/ai-plugins/issues/188) | まとめてマージした後の版上げの担い手と時期を決める | [issue-188-release-step.md](issue-188-release-step.md) |
| [#202](https://github.com/devbasex/ai-plugins/issues/202) | 開発の起点ブランチを `.ndf/worktree.json` で宣言する | [issue-202-base-branch.md](issue-202-base-branch.md) |

## 計画と調査資料

| ファイル | 内容 |
| --- | --- |
| [parallel-batch-01/](parallel-batch-01/00-overview.md) | 並行開発バッチ 01（#178 / #33 / #37 / #81）の指示書 |
| [parallel-batch-02/](parallel-batch-02/00-overview.md) | 並行開発バッチ 02（#175 / #176 / #186 / #188）の指示書と引き継ぎ |
| [release-v9.5.0-verification.md](release-v9.5.0-verification.md) | v9.5.0 のリリース後テストの記録 |
| [ndf-development-skills/](ndf-development-skills/01-overview.md) | 開発方法論レイヤーの導入計画（v6.1.0） |
| [ndf-skill-footprint.md](ndf-skill-footprint.md) | frontmatter の圧縮と playwright 系の分割（v7.0.0） |
| [skill-frontmatter-by-runtime.csv](skill-frontmatter-by-runtime.csv) | 上の計画で使った Skill ごとの実測値 |
| [plugin-single-directory-migration.md](plugin-single-directory-migration.md) | 配布物の単一ディレクトリ化 |
| [report01.md](report01.md) | 外部 Skill 集の調査（2026-08-08 時点） |
