# 用語集: NDF の外部 CLI 委譲（`ndf-agent-cli`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

cross-review / cross-refactoring が参加者の CLI を選び、起動し、監視し、結果を読むときの語。ランタイム・ホスト・参加者プール・起動結果

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| 起動結果 | `launch_outcome` | 担当 CLI の起動 1 回の終わり方。監視の状態と理由、結果ファイルの有無と読めるかを合わせて持つ（LaunchOutcome） | 結末 | — | `docs/specifications/cross-review-launch-outcome.md` |
| リトライ可否 | — | 同じ担当を同じ条件で起動し直せば解ける理由か（assignment.relaunch_same_agent）。起動結果ではなく振り替えの規則が持つ | 起動し直しの可否 | — | `docs/specifications/cross-review-launch-outcome.md` |
| 結果ファイル | `result_file` | 担当 CLI が書く判定の要約の JSON（<stem>-result.json） | — | — | `docs/specifications/cross-review-launch-outcome.md` |
| 監視結果ファイル | `monitor_outcome` | 監視が起動 1 回ごとに状態と理由を書く JSON（<stem>-monitor.json） | 監視の結果ファイル | — | `docs/specifications/cross-review-launch-outcome.md` |
| 監視ログ | — | 起動結果を追記だけで積む記録（monitor-outcomes.jsonl） | 監視の記録 | — | `docs/specifications/cross-review-launch-outcome.md` |
| ランタイム | `runtime` | エージェントの CLI の種類。claude / codex / agy / kiro の 4 つで、並びは固定（ALL_RUNTIMES） | — | — | `docs/specifications/cross-review-participants-and-seats.md` |
| ホスト | `host` | 収束ループを起動している CLI のランタイム（host） | — | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 参加者プール | — | Skill ごとに決まる参加者の出発点。cross-review と cross-refactoring で共通の claude / codex / kiro とホスト（default_pool） | 参加の母集合 | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 参加者 | `participant` | 参加者プールに --include の者を加え、--exclude の者を除いた一覧。認証確認の対象 | — | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 利用可能な参加者 | — | 参加者のうち参加の確認を通った者（participants.available） | 使える者 | — | `docs/specifications/cross-participant-admission-check.md` |
| 認証確認 | — | 参加の確認の最初の種類。確認コマンド（AUTH_PROBES）を走らせ、認証を通っているかだけを見る | 認証の確認 | — | `docs/specifications/cross-participant-admission-check.md` |
| スロット | `slot` | 同時に流せる本数の 1 つ分。1 ラウンドで 1 つの CLI プロセスが 1 つを占める | 枠（単独） | — | `docs/specifications/cross-review-participants-and-seats.md` |
| フォールバック | `fallback` | 利用可能な参加者が 2 者に満たないとき、足りない分を埋める参加者（participants.fallback） | 埋め合わせ | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 実測値 | `observed_model` | CLI の出力かランタイムのセッションの記録から取った、実際に動いたモデル名。状態ファイルの observed | — | — | `plugins/ndf/skills/external-ai/references/cli-claude.md` |
| 指定値 | — | --model で CLI へ渡したモデル名。状態ファイルの requested。未指定なら null | — | — | `plugins/ndf/skills/external-ai/references/cli-claude.md` |
| 主たるモデル | — | 1 回の起動で複数のモデルが動いたとき、実測値として記録する 1 つ | — | — | `plugins/ndf/skills/external-ai/references/cli-claude.md` |
| 分離 | — | 何が動いたか分からない実行を、モデル別の集計に入れず理由だけを報告すること | — | — | `docs/specifications/cross-participant-admission-check.md` |
| 起動の記録 | — | 起動 1 回の開始の時刻・作業ディレクトリ・ランタイムを、起動が自分で書いたファイル（<stem>-launch.json） | — | — | `docs/specifications/ndf-usage-ledger-by-runtime.md` |
| ランタイムの設定 | — | .ndf/runtimes.json。NDF が CLI として起動してよいランタイムの一覧（allowed）と、cross-review の固定の組（review_seats）を持つ。無ければ制限しない | — | — | `docs/specifications/cross-review-participants-and-seats.md` |
| 固定の組 | `review_seats` | ランタイムの設定の review_seats。cross-review の毎ラウンドのスロットを交代させずにこの 2 つにする | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 担当 | — | レビュー・提案・実装を受け持つ CLI の起動の単位。ランタイムと、claude ではアカウントで決まる | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 結果なし | — | 担当が結果ファイルを残さずに終わったこと（judge の verdict = no_result、cross-refactoring の failed_attempts） | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 利用上限 | — | 担当の CLI が使用量の上限に当たって止まったこと（起動結果の理由 usage_limit） | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 振り替え | — | 結果なしの担当の役割を、同じ実行の中で別の担当へ移すこと。同じ担当で起動し直すことは含まない | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 結果なしの記録 | `no_results` | 状態ファイルの no_results。結果なしの 1 回ごとに、担当・理由・振り替えの規則の回答（relaunch / reassign / abort）・振り替え先を追記だけで積む | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 外した担当 | — | 結果なしの記録から導く、同じ実行の残りで割り当てないランタイム（claude ではアカウント） | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 最小の呼び出し | — | 担当を起動するときと同じモデルの指定で、短い固定の質問を 1 回投げて応答が返るかを見る確認。認証確認に続けて行う | — | — | `docs/specifications/cross-participant-admission-check.md` |
| 既定のモデル | — | 利用者の CLI の設定も引数の指定も無いときに、CLI が使うモデル | — | — | `docs/specifications/cross-participant-admission-check.md` |
| 参加の確認 | — | 参加者ごとに、認証確認・最小の呼び出し・（設定のモデルを引けなければ）既定のモデルでの引き直しを順に行い、担当に入れるかと理由を決めること（auth.probe_auth が走らせ、assignment.admit が判断する） | — | — | `docs/specifications/cross-participant-admission-check.md` |
| モデルを引けない | `model_unavailable` | 担当の CLI が、指定か設定のモデルを使えないと返したこと（参加の確認と起動結果の理由 model_unavailable） | — | — | `docs/specifications/cross-participant-admission-check.md` |
| 認証の失効 | `auth_expired` | 認証の状態確認は通るが、更新トークンの失効などで実行のときに認証を作り直せないこと（参加の確認と起動結果の理由 auth_expired） | — | — | `docs/specifications/cross-participant-admission-check.md` |
| 既定のモデルへの切り替え | `default_models` | 引数で明示していないモデルを引けない CLI を、既定のモデルで担当に入れること（participants.default_models） | — | — | `docs/specifications/cross-participant-admission-check.md` |
| 相手役 | — | cross-review のレビュー担当、cross-refactoring の提案・実装の担当として、ホストから CLI プロセスで起動されるランタイム（#2231） | — | — | `docs/specifications/ndf-distribution-runtimes.md` |
| 下限（外部 CLI の） | — | NDF が動くために要る外部 CLI と本体（gh・claude・codex）の最も古いバージョン。NDF は起動の前に比べ、古ければ起動しない | — | — | `docs/specifications/dependency-updates-renovate-and-supply-chain.md` |
| 起動の設定 | — | NDF が AI を起動するときの、リポジトリごとの指定を持つ .ndf/launch.json。工程ごとの MCP と追加の Tool を持つ | — | — | `issues/issue-1959-design.md` |
| モデルと effort の対応表 | — | 層・名前・ランタイムから、起動のモデルと effort を引く NDF の表 | — | — | `issues/issue-2944-design.md` |
| 値の出所 | — | 起動のモデルか effort を決めた元（引数・環境変数・リポジトリの設定・対応表・指定なし） | — | — | `issues/issue-2944-design.md` |
| 値の引き上げ | — | 失敗の後の修正の起動で、モデルか effort を 1 つ上にすること | — | — | `issues/issue-2944-design.md` |
