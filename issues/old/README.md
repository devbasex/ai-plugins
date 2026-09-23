# issues/old/ — 完了した記録

完了した issue の実装計画、実機試行の記録、報告書、調査資料を置く。履歴として読むためのもので、
運用判断の根拠には使わない。確定した仕様は [docs/specifications/](../../docs/specifications/) を、
リリース済みの変更点は `CLAUDE.md` を参照する。

## cross-refactoring（issue #113）の記録

`/ndf:cross-refactoring` の設計の初版と、7 回の実機試行、そこで見つけた不具合の修正計画である。
現行の実装計画は [../issue-113-cross-refactoring.md](issue-113-cross-refactoring.md) にある。

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
| [#392](https://github.com/devbasex/ai-plugins/issues/392) | 手順書のほぼ同一の段落を 1 つにする | [issue-418-workflow-stage-matrix.md](issue-418-workflow-stage-matrix.md) |
| [#417](https://github.com/devbasex/ai-plugins/issues/417) | v10.5.0 の事後レビューで出た 8 件を直す | [issue-417-post-review-defects.md](issue-417-post-review-defects.md) |
| [#418](https://github.com/devbasex/ai-plugins/issues/418) / [#420](https://github.com/devbasex/ai-plugins/issues/420) / [#422](https://github.com/devbasex/ai-plugins/issues/422) | 判定の単位を Pull Request にし、`light` にもレビューと gate を課す | [issue-418-417-workflow-gate/](issue-418-417-workflow-gate/01-requirements.md) / [issue-418-workflow-stage-matrix.md](issue-418-workflow-stage-matrix.md) |
| [#424](https://github.com/devbasex/ai-plugins/issues/424) | 並行開発へ対応し、承認を 2 つの関門へ集約する | [issue-424-gate-and-approval.md](issue-424-gate-and-approval.md) |
| [#312](https://github.com/devbasex/ai-plugins/issues/312) / [#315](https://github.com/devbasex/ai-plugins/issues/315) / [#313](https://github.com/devbasex/ai-plugins/issues/313) / [#573](https://github.com/devbasex/ai-plugins/issues/573) / [#610](https://github.com/devbasex/ai-plugins/issues/610) / [#495](https://github.com/devbasex/ai-plugins/issues/495) | 作業ツリー運用の残課題（まとまり「04 worktree 運用の残課題」）。確定仕様は [ndf-worktree-declaration-and-entry-points.md](../../docs/specifications/ndf-worktree-declaration-and-entry-points.md) と [ndf-testenv-lock-and-registry.md](../../docs/specifications/ndf-testenv-lock-and-registry.md) | [milestone-04-worktree/](milestone-04-worktree/issue-312-315-requirements.md) |
| [#561](https://github.com/devbasex/ai-plugins/issues/561) / [#623](https://github.com/devbasex/ai-plugins/issues/623) / [#550](https://github.com/devbasex/ai-plugins/issues/550) / [#657](https://github.com/devbasex/ai-plugins/issues/657) / [#540](https://github.com/devbasex/ai-plugins/issues/540) / [#541](https://github.com/devbasex/ai-plugins/issues/541) / [#621](https://github.com/devbasex/ai-plugins/issues/621) / [#554](https://github.com/devbasex/ai-plugins/issues/554) | 無人運転と工程の測定（まとまり「10 無人運転と工程の測定」、マイルストーン 18）。確定仕様は [ndf-cleanup-and-bundle-closing.md](../../docs/specifications/ndf-cleanup-and-bundle-closing.md) / [ndf-agent-layers-unattended-run.md](../../docs/specifications/ndf-agent-layers-unattended-run.md) / [ndf-context-window-metrics.md](../../docs/specifications/ndf-context-window-metrics.md) / [ndf-execution-plan-and-parallel-capacity.md](../../docs/specifications/ndf-execution-plan-and-parallel-capacity.md) / [ndf-instruction-files-check.md](../../docs/specifications/ndf-instruction-files-check.md) | [milestone-18-unattended/](milestone-18-unattended/issue-561-623-requirements.md)（要求・設計・契約・決定・計画・調査の 24 本。#762 の要件は下の行） |
| [#762](https://github.com/devbasex/ai-plugins/issues/762) | `agent-layers.md` の「並行の本数」の節で、実行計画の持ち主を `issue-plan-strategy` の `execution-plan.md` へ向ける（`light`、マイルストーン 18 の続き。#550 の AC60「`light` の課題 1 件を無人で通す」の確認に使った） | [milestone-18-unattended/issue-762-requirements.md](milestone-18-unattended/issue-762-requirements.md) |
| [#829](https://github.com/devbasex/ai-plugins/issues/829) / [#830](https://github.com/devbasex/ai-plugins/issues/830) | 待つ間の問い合わせを止め、conductor の会話を工程の切れ目で切る（マイルストーン 26「17 トークン消費の削減」のまとまり 1）。確定仕様は [ndf-token-waits-and-context-cut.md](../../docs/specifications/ndf-token-waits-and-context-cut.md) | [milestone-26-token-waits/](milestone-26-token-waits/issue-829-830-requirements.md)（要求・設計・決定・計画の 4 本） |
| [#828](https://github.com/devbasex/ai-plugins/issues/828) / [#680](https://github.com/devbasex/ai-plugins/issues/680) | サブエージェントに Skill 本文を丸ごと読ませず、仕事を分ける器を比べて選ぶ（マイルストーン 26「17 トークン消費の削減」）。確定仕様は [ndf-worker-agent-and-skill-excerpts.md](../../docs/specifications/ndf-worker-agent-and-skill-excerpts.md) | [milestone-26-worker-agent/](milestone-26-worker-agent/issue-828-680-requirements.md)（要求・設計・決定・計画の 4 本） |
| [#892](https://github.com/devbasex/ai-plugins/issues/892) / [#901](https://github.com/devbasex/ai-plugins/issues/901) | cross-review の母集合にホストを入れ、supervisor が worker の途中の通知で止まらないようにする（マイルストーン 26「17 トークン消費の削減」）。確定仕様は [cross-review-participants-and-seats.md](../../docs/specifications/cross-review-participants-and-seats.md) / [ndf-token-waits-and-context-cut.md](../../docs/specifications/ndf-token-waits-and-context-cut.md) | [milestone-26-review-pool-and-interim-wait/](milestone-26-review-pool-and-interim-wait/issue-892-901-requirements.md)（要求・設計・決定・計画の 4 本） |
| [#895](https://github.com/devbasex/ai-plugins/issues/895) | 区間の切れ目の再起動と次のコマンドの入力を前景の中継で自動にする（マイルストーン 26「17 トークン消費の削減」）。確定仕様は [ndf-relay-segment-restart.md](../../docs/specifications/ndf-relay-segment-restart.md) / [ndf-token-waits-and-context-cut.md](../../docs/specifications/ndf-token-waits-and-context-cut.md) | [milestone-26-relay-restart/](milestone-26-relay-restart/issue-895-requirements.md)（要求・設計・決定・計画の 4 本） |
| [#880](https://github.com/devbasex/ai-plugins/issues/880) / [#883](https://github.com/devbasex/ai-plugins/issues/883) / [#494](https://github.com/devbasex/ai-plugins/issues/494) / [#723](https://github.com/devbasex/ai-plugins/issues/723) / [#885](https://github.com/devbasex/ai-plugins/issues/885) | cross-refactoring の群を範囲のテストで検証し、打ち切りを子の終了で戻し、構造改善を飛ばしてよいかを差分から判定し、`.md` の文言固定テストを採らず置かない（マイルストーン 26「17 トークン消費の削減」のまとまり 2）。確定仕様は [cross-refactoring-round-tests-and-assess.md](../../docs/specifications/cross-refactoring-round-tests-and-assess.md) | [milestone-26-cross-refactoring-fixes/](milestone-26-cross-refactoring-fixes/issue-880-885-requirements.md)（要求・設計・決定・計画の 5 本） |

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
