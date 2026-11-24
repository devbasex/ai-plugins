# 用語集: NDF の開発ワークフロー（`ndf-workflow`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

NDF が提供する工程・承認ゲート・モード・ステップの語。development-workflow/references/glossary.md が持つ

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| インライン実行 | — | 仕事を渡す実行方式の 1 つ。いまの会話の文脈で行う | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| オーケストレーター | — | `cross-refactoring` と `cross-review` で、公開・生成物の同期を持ち、担当を回す側。3 層では conductor に当たる | 進行側、レビューを回す側 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 承認ゲート | `approval_gate` | 人手の承認を求める点。設計承認ゲート（設計 Pull Request のマージ）とリリース承認ゲート（本番系へ届く操作）の 2 つだけ | 関門 | `gate` | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 承認資料 | — | 承認を求めるときに示すもの。対象を開くためのものと、承認の判断に使うものの 2 層を持つ | 提示物 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| エビデンス | — | 承認資料のうち機械で作れる部分と、conductor が確かめて足した事実。MVV 判定へ渡す | 事実の材料 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リファクタリング | — | 振る舞いを変えずに構造を直す工程 | 構造改善 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| コードレビュー | — | 実装の差分をレビューし、新しい指摘が出なくなるまで直す工程 | 実装レビュー | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 進捗記録 | — | 工程に入った時点で 1 回打つ記録。リリース後テストだけは、受け入れ条件ごとの記録を投稿してその工程を終えた時点で打つ。課題の本文の「進行」も同じ 1 回で更新される | 進行の記録、記録のコマンド | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| フェーズレポート | — | supervisor（またはプラン）が最後に返す報告 | フェーズの報告 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 安定版と実験版 | — | NDF の変更の 2 つの経路（stable / experimental）。実験版の保存先は `experimental/ndf/`（配るディレクトリの `plugins/ndf/` の外） | 安定と試行 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 即時修正 | — | 4 つの条件（`development-workflow` の SKILL.md の「即時修正」）を満たす不具合を、起票せず、その場のプランで直すこと | その場で直す | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 手順 | — | 1 つの Skill の中で順に通す作業の単位。cross-refactoring の提案・リファクタリング計画・テスト追加・実装・検証/修正の 5 つ、document-rebuild の決める（文書形式・文書の目的・言語）・測る・組み直す・整える・測り直すの 5 つ（手順 0〜4） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| SSOT | `source` | その事柄の定義を持つ唯一の文書。食い違ったときはこれを正とする | 正本 | — | `docs/specifications/doc-consistency-checks.md` |
| conductor | `conductor` | スプリントを持ち、プランを流す役割。人へ問えるかは置かれた場所で決まる（計画の外のセッションは自分で質問、レーンは質問をオーガナイザーへ送る） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| supervisor | `supervisor` | 1 つのフェーズを通す役割。supervise.py が主に担い、supervise.py で対応できないとき（プランのテンプレートが無いときなど）だけサブエージェント（ndf:supervisor）が代行する。人間へ問わない | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| worker | `worker` | 1 つの作業（調査・修正・検証・集計）を行うサブエージェント。別のサブエージェントを起動しない | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| フェーズ | `phase` | supervisor 1 つ（またはプラン 1 本）が通す、連続する工程のグループ。設計・実装・検査・マージ・仕上げ・リリースの 6 つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 作業種別 | — | worker 1 つが行う作業の分類。調査 / 修正 / 検証 / 集計、どれにも当たらなければその他 | 作業の種類 | — | `docs/specifications/ndf-agent-layers-unattended-run.md` |
| 実行前確認 | — | Skill の手順の途中で、操作の対象を示して利用者の同意を得ること。承認ゲートとは別 | — | — | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| 取り消せる操作 | — | 失う状態を git 自身が拒むか、事後の手段（ハッシュからの復元・Restore branch・reopen）で元へ戻せる操作。実行前確認なしで進める | — | — | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| 最終工程 | — | 棚卸しの前にその実行で最後に通る工程。振り返りを通るなら振り返り（retrospective）、通らずリリース後テストを通るならリリース後テスト（release-verification）、どちらも通らなければデプロイ（release） | 終わりの工程 | — | `docs/specifications/ndf-cleanup-and-bundle-closing.md` |
| 企画承認 | — | documentation モードの設計承認ゲート。構成案と体裁設計を載せた設計 Pull Request のマージ | — | — | `docs/specifications/ndf-documentation-mode.md` |
| 制作物承認 | — | documentation モードのリリース承認ゲート。production が真の提出先への操作 | — | — | `docs/specifications/ndf-documentation-mode.md` |
| 下書き先 | — | production が偽の提出先。承認の前に書き込んでよい唯一の場所 | — | — | `docs/specifications/ndf-documentation-mode.md` |
| 工程 Skill | — | development-workflow の工程表が起動する Skill | 工程の Skill | — | `docs/specifications/ndf-worker-agent-and-skill-excerpts.md` |
| 中間通知 | — | 背景の処理を残したまま応答を終えたサブエージェントについて、親へ届く 1 回目の通知 | 途中の通知 | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 報告コピー | — | worker が起動指示の保存先のファイルの末尾へ書く作業の報告のセクション。最後の応答の報告と同じ中身 | 報告のコピー | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 完了マーカー | — | worker が報告コピーを書き終えた後に作る空のファイル（<置く場所>.done） | 完了の目印 | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 待ちのテンプレート | — | waiting.md が持つ、背景で起動して条件が成り立つまで待つ until ループのコマンド。成り立てば 0、上限に達すれば 124 で終わり、exit=<終了コード> を出す | — | — | `plugins/ndf/skills/development-workflow/references/waiting.md` |
| 横断 Skill | — | 工程表に載らず、どの工程からも呼ばれる Skill（progress-tracking / out-of-scope など） | 工程の外の Skill | — | `docs/specifications/ndf-worker-agent-and-skill-excerpts.md` |
| 抜粋 | — | 呼ぶ側が要る部分だけを Skill の本文から取り出したもの | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 実行方式 | — | 仕事を渡す先の実行の形。インライン実行 / サブエージェント / CLI 実行 / 最小構成の claude -p / スクリプト | — | — | `docs/specifications/ndf-worker-agent-and-skill-excerpts.md` |
| スケルトン | — | SKILL.md の実行のセクションに置く bash。利用者はこれをコピーして起動する | 骨組み | — | `docs/specifications/ndf-workflow-blockers.md` |
| 通過記録 | — | 通過工程を課題ごとに残したファイル | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 必須ルール | — | 分割と並行の手法を担当へ任せたうえで、それでも守る規則 | — | — | `docs/specifications/ndf-workflow-unit-and-gates.md` |
| 収束ループ | — | 新しい指摘が出なくなるまで回すレビューと修正。リファクタリング・コードレビュー・ドキュメントレビューが持つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ボード | — | 進行を記録する GitHub Projects のプロジェクト 1 つ。設定が無ければ何も動かない | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 通過工程 | — | ある課題について、進捗記録が実際に書かれた工程の集合 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 承認ラベル | — | 人間が設計を承認したことを表す Pull Request のラベル。無ければ hook が設計 Pull Request のマージを拒む | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| マイルストーン | `milestone` | 着手の順序を表す単位。スプリントはこの中から切り出す | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 工程 | `stage` | 工程表（モードごとに起動する Skill の表）の 1 行。要求と受け入れ条件・設計・実装・リリースなど | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スイッチポイント | — | フェーズの中で supervisor を替える点。収束ループの前で hook が決める | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 3 層 | — | スプリントの中の工程を conductor → supervisor → worker の順に起動して通す形。ロール層のうち conductor・supervisor・worker の 3 つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 最小構成の `claude -p` | — | Tool と指示を絞った 1 回の判断。judge のステップと MVV 判定が使う | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 実行レベル | — | 仕事を渡す実行方式の LLM の使い方の水準。レベル 1 = スクリプト、レベル 2 = 分類の判断、レベル 3 = インライン実行の LLM | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| モード | `mode` | 変更の目的物で決める工程の振り分け。上から operation / documentation / standard / legacy-refactor / light | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 検査 | — | リファクタリング・コードレビュー・完了判定・Pull Request を通すフェーズ。fast ではトリガーが立ったときだけ、前回の検査からの差分に流す。コードレビューだけは開発版ごとに流す（--review-only） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 完了判定 | — | コマンドの証跡で完了を判定する工程。スクラムの完了の定義に当たる | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 実装計画 | — | implementation-plan が issues/ に書く、実装の前の計画 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 確定仕様化 | — | 完了した実装計画を docs/ の確定仕様へ書き直す工程 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 振り返り | — | 進め方で変えることを記録し、起票の取りこぼしを拾う工程 | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スプリント | `sprint` | 1 回のリリースとして出す課題と Pull Request のセット。版数を持つプロジェクトでは 1 つのバージョンになる。期間ではなく、1 回のリリースとして出す中身で切る。工程はスプリント単位で 1 回ずつ通し、モードもスプリントで 1 つにする | ミッション | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| プロジェクト | — | 主リポジトリ 1 つとサブリポジトリの集まり（Neural Development Factory の定義）。リポジトリが 1 本だけのときもその場合として含む。GitHub Projects・Claude Code の project など他のサービスの名前は、そのサービスの意味のまま使う | — | — | — |
| スプリント状態ファイル | `sprint_state` | スプリントのプラン・done・承認ゲートの記録・MVV・バージョンを持つファイル（パスは呼ぶ側が決め、手順書の例は `sprint-state.json`。目録 `sprint.json` とは別のファイル） | ミッション状態ファイル、ミッションの状態 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| スプリント課題 | — | 「スプリントを閉じる」が閉じる対象にする課題。スプリントの Pull Request の本文が GitHub の closing keywords（PR 本文に `Closes #123` のように書くと、マージ時にその課題を自動で閉じる機能）か閉じる予定の参照で指す課題と、--issues で渡した課題の和 | ミッション課題、ミッションの課題 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| closing keywords | `closing_words` | GitHub の機能。Pull Request の本文に `Closes #123` のように書くと、マージのときにその課題を自動で閉じる。キーワードは Closes / Fixes / Resolves と活用形で、課題の参照が続く。GitHub を使う人なら誰でも知っている機能ではないため、文書では使う箇所ごとに説明を添える | 閉じる語 | — | `docs/specifications/ndf-sprint-close-referenced-issues.md` |
| 参照だけの課題 | `referenced_only` | スプリントの Pull Request の題か本文が番号で参照し、スプリント課題に入らない開いた課題 | — | — | `docs/specifications/ndf-sprint-close-referenced-issues.md` |
| 承認の証跡 | — | 承認ゲートで利用者が承認したことを示す、プランの外で残った記録。スプリント状態ファイルの承認ゲートの記録（`--by user --outcome approved`）・承認ラベル・承認資料の承認の記録のどれか。プランのステップは承認の証跡があるときだけ、承認を前提にした操作へ進む | — | — | `docs/specifications/ndf-supervise-queue-resume-and-gate-evidence.md` |
| 手動確認の行 | — | 要求の検証手段の表で、項目が「手動確認」で始まり、2 列目が「無し」の形（「無し」「なし」「不要」「ない」「-」「—」「―」の単独か、直後に括弧・句読点・空白が続く形）でない行。項目が「手動確認（マージ前）」ならマージ前、それ以外はリリース後テストに確かめる | — | — | — |
| hook の上限 | — | ランタイムの hook の定義（hooks/*.json・dev.agy/hooks.json）の timeout。過ぎるとランタイムが hook を打ち切る | — | — | `docs/specifications/cross-review-reopen-and-hook-deadline.md` |
| hook の締め切り | — | NDF の hook が自分で決める 1 回の実行の時間の上限（3.5 秒）。hook の上限より短く、過ぎたら残りの判定を飛ばして通し、標準エラーへ 1 行残す | — | — | `docs/specifications/cross-review-reopen-and-hook-deadline.md` |
| 設計との突き合わせ | — | 完了判定で、設計に名前の出るテスト・既存の確定仕様と変更履歴・設計の決定を実装と照らし、食い違いを残すこと。確認 (a)(b)(c) の 3 つからなる | — | — | `docs/specifications/quality-gates-design-match.md` |
| 独立に求めた値 | — | 段階 4 のうち実データに対する結合確認で動かすコードを通さずに、設計・要求の段階で実データについて求めた件数・金額・行数などの値。出力から逆算した値は当たらない | — | — | `plugins/ndf/skills/quality-gates/references/definition-of-done.md` |
| 独立に求めた値との突き合わせ | — | standard の完了判定で、段階 4 のうち実データに対する結合確認の出力を独立に求めた値と比べ、出所・期待値・出力の値・一致したかを証跡に残すこと。値が無ければ「突き合わせる値が無い」と書き、実データに対する結合確認が無ければ「対象が無い」と書く。フィクスチャで回す結合テストの出力は比べない。設計との突き合わせ (a)(b)(c) には含めない | — | — | `plugins/ndf/skills/quality-gates/references/definition-of-done.md` |
| 既存の確定仕様 | — | 比べるベースブランチとの merge-base の時点で確定仕様の保存先にあった文書。同じ差分で足した確定仕様は含まない | — | — | `docs/specifications/quality-gates-design-match.md` |
| 設計と違う点 | — | 実装 PR の本文のセクション。設計の決定・設計に名前の出るテスト・既存の確定仕様・受け入れ条件と実装の違いを、箇所・違い・理由の行で並べる | — | — | `docs/specifications/quality-gates-design-match.md` |
| 受け入れ条件の番号 | — | 課題の本文の `## 受け入れ条件` のセクションで、コードブロックの外にある行頭のチェックボックスの行を上から数えた順（1 から）。リリース後テストの表の `受け入れ条件` の列は、セルの頭に `<番号>.` で書く | — | — | `docs/specifications/ndf-sprint-close-acceptance-criteria.md` |
| 受け入れ条件のマーカー | — | 受け入れ条件の番号に合格の行がそろった課題を閉じるとき、sprint-close.py がその行のチェックボックスへ付ける `[x]` | — | — | `docs/specifications/ndf-sprint-close-acceptance-criteria.md` |
| 起票者の推定 | — | 課題の題・本文に、利用者の指示の原文に無く起票者（課題を書いた AI）が文書・コード・経緯から推して足した条件。設計承認ゲートで確かめるまで利用者の決定として扱わない。扱い（受け入れ条件・前提・含まない・採らない）を書いた推定は設計者が決めた判断として読み、MVV 判定が承認ゲートを省いたときはその判定が確かめた記録になる | — | — | `docs/specifications/ndf-design-ai-decisions-and-mvv-gate.md` |
| 承認ゲートの変更要求 | — | 承認ゲートで利用者が承認せず、対象を直すよう戻したこと。MVV 判定の有無を問わず 1 回を 1 件と数える。MVV 判定との食い違いである覆しとは別の数 | — | — | `docs/specifications/ndf-gate-rejections-and-filer-assumptions.md` |
| 変更要求の記録 | — | 承認ゲートの変更要求を 1 回 1 行で追記する JSONL（NDF_MVV_STATE_DIR の gate-rejections.jsonl）。pace と状態ファイルの有無によらず唯一の保存先で、覆しの記録とは別のファイル | — | — | `docs/specifications/ndf-gate-rejections-and-filer-assumptions.md` |
| 作業の報告 | `work_report` | worker が応答の最後に書く「## 作業の報告」のセクション。人と conductor が読む。成否は作業の結果ファイルで決め、このセクションの文からは決めない（#2337） | — | — | `docs/specifications/ndf-supervise-work-step-web-tools-and-report-stop.md` |
| 問い合わせの区分 | `ask_category` | AskUserQuestion の metadata.source に AI が付ける値。ndf-gate:<番号>（承認ゲート）・ndf-ask:redline:<番号>（レッドライン。設定で外せない）・ndf-ask:<名前>（設定が許す区分）の 3 つの形。hook は質問の中身を読まず、この値が許された区分かだけを見る（#2206） | — | — | `docs/specifications/ndf-ask-guard.md` |
| 問い合わせの設定 | `ask_declaration` | プロジェクトの .ndf/ask.json。人へ回してよい問い合わせの区分と hook の働き方（enforce / advise / off）を書く（#2206） | — | — | `docs/specifications/ndf-ask-guard.md` |
| 代わりの判断 | `proxy_decision` | hook が質問を止めた後か、手順が「AI が決める」とした場面で AI が自分で下した判断。質問・選んだ回答・理由を質問の記録へ書く（#2206） | — | — | `docs/specifications/ndf-ask-guard.md` |
| 問い合わせの記録 | `ask_ledger` | 状態の保存先の ask.jsonl。hook の判定の行と代わりの判断の行を追記だけで持ち、振り返りと Factory のランナーのエージェントが読む（#2206） | — | — | `docs/specifications/ndf-ask-guard.md` |
| 最終工程の記録 | — | 最終工程を終えたことを示す、GitHub に残った記録。デプロイはリリース記録の `段階:`、リリース後テストはその課題の行、振り返りは課題の本文の `振り返り: <コメントの URL>` の行のうち、指すコメントの識別子が記録の PR の最後のリリース記録のコメントの識別子以上のもの（その実行の振り返り）。sprint-close.py が閉じる前に確かめる | — | — | `plugins/ndf/skills/progress-tracking/SKILL.md` |
| 閉じる予定の参照 | — | Pull Request の本文に `最終工程の後に閉じる: #<番号>` と書く形。GitHub は閉じず、sprint-close.py がスプリント課題として集める。マージ先が既定ブランチの Pull Request では、NDF の手順が closing keywords をこの形へ置き換える | — | — | `plugins/ndf/skills/progress-tracking/SKILL.md` |
| Skill の対応表 | — | 工程（ステップの stage）から、そのステップの worker へ届ける Skill の一覧を引く表。NDF の scripts/lib/stage-skills.json の 1 か所に置き、プランの worker の起動と ndf:worker の起動指示の両方が読む（#2376） | — | — | `docs/specifications/ndf-worker-stage-skills.md` |
| Skill の渡し方 | — | Skill を worker へ届ける形。プランの worker には本文を system プロンプトへ入れ（本文）、ndf:worker には SKILL.md の絶対パスを起動指示の「読む Skill」に書く（パス。#2376） | — | — | `docs/specifications/ndf-worker-stage-skills.md` |
| 読む Skill | — | supervisor が ndf:worker の起動指示に書く項目。その作業で読む規則の Skill の名前と SKILL.md の絶対パスを並べ、worker はここに挙がった SKILL.md だけを読む（#2376） | — | — | `docs/specifications/ndf-worker-stage-skills.md` |
| 読む Skill の guard | — | supervisor が ndf:worker を起動する Agent の呼び出しを PreToolUse の hook で見て、「読む Skill」の欄が無い起動と、Skill の対応表を引かずに Skill を挙げた起動を止める判定。止めた理由に SSOT の「supervisor → worker」のセクションを添える（#2399） | — | — | `docs/specifications/ndf-supervisor-worker-skills-guard.md` |
| 規則の Skill | — | 手順を持たず、書き方・記録の仕方のような規則だけを持つ Skill（document-style・logging-guidelines など）。Skill の対応表に置けるのはこれだけで、工程 Skill は置かない（#2376） | — | — | `docs/specifications/ndf-worker-stage-skills.md` |
| 作業の結果ファイル | — | worker が報告を終える前に書く、結果 JSON の形の 1 行のファイル。supervise と supervisor はこのファイルの status だけで作業の成否を決める（#2337） | — | — | — |
| 設計承認ゲート | — | 設計 Pull Request のマージの前に人の承認を求める承認ゲート。documentation モードでは企画承認に当たる | 承認ゲート 1、ゲート 1、関門 1 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| リリース承認ゲート | — | 本番系へ届く操作（本番へのリリース・operation の実行・自動反映の本番チャネルへのマージ）の前に人の承認を求める承認ゲート。documentation モードでは制作物承認に当たる | 承認ゲート 2、ゲート 2、関門 2 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 人へ出す文 | — | Skill・references・エージェントの定義の本文と、スクリプト・hook が人へ見せる文（質問・承認資料・報告・案内・エラーの文）。機械が読む識別子と対になる | — | — | `docs/specifications/ndf-approval-gate-names.md` |
| 機械が読む識別子 | — | スクリプトが値の一致で読む文字列（質問の区分・コントラクトの種類の値・記録の名前・ラベルなど）。人へ出す文と対になる | — | — | `docs/specifications/ndf-approval-gate-names.md` |
| 書いた日の記録 | — | 書いた日時点の内容を残し、以後の構成変更に追随しない文書。発表資料・記事・アーカイブ・開発履歴・変更履歴の出したバージョンの項・issues/ の作業記録 | — | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| 利用先のリポジトリ | — | NDF を導入して使っているリポジトリ | 配布先のリポジトリ | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| 読み込まれたプラグイン | — | 起動したセッションに Claude Code が読み込んだ NDF。--plugin-dir で指したディレクトリか、導入したプラグインのキャッシュのディレクトリで、起動時の init の plugins の ndf の path に出る | — | — | `docs/specifications/ndf-supervisor-docs-guard.md` |
| 規約の文書 | — | supervisor が worker の起動指示を組むために読む、NDF の Skill の SKILL.md と references/ の下の文書（development-workflow の references/agent-layers.md、design の references/design-template.md など） | — | — | `docs/specifications/ndf-supervisor-docs-guard.md` |
| 規約の文書の guard | — | supervisor が Read と Bash で読み込まれたプラグインの外の規約の文書を読もうとしたとき、PreToolUse の hook で止め、読み込まれたプラグインの中の同じ文書の絶対パスを理由で返す判定（#2813） | — | — | `docs/specifications/ndf-supervisor-docs-guard.md` |
| 機械的なターン | — | AI が判断を伴わずに行う Tool の呼び出し。出力の読み解き・状態の検証・スクリプトを 1 行打つ・手書きの待ち・本文の転記 | — | — | `docs/specifications/ndf-mechanical-turns-to-scripts.md` |
| ターンの型 | — | 機械的なターンを、何をしたか（変換・検証・往復・待ち・転記）と打ったコマンドで分けた分類 | — | — | `docs/specifications/ndf-mechanical-turns-to-scripts.md` |
| 手順の連続実行 | — | 判断の要らない NDF の手順のスクリプトを、結果 JSON が ok の間だけ続けて打ち、gate か stopped で止まって理由を返すこと（supervise.py advance） | — | — | `docs/specifications/ndf-mechanical-turns-to-scripts.md` |
| 判定表 | — | development-workflow の「上から順に条件を判定する」の表。順・モード・該当条件の 3 列を持ち、最初に当たった行のモードを採る | — | — | `docs/specifications/ndf-sprint-mode-from-judgment-basis.md` |
| 判定の根拠 | — | 1 つの課題について、判定表の当たった行（順とモード）・当たった条件の文・触るファイルの一覧の組 | — | — | `docs/specifications/ndf-sprint-mode-from-judgment-basis.md` |
| 判定の材料 | — | 判定の根拠を選ぶためにスクリプトが集める事実。触るファイルの候補・公開インタフェースの変更の有無・対象のテストの有無 | — | — | `docs/specifications/ndf-sprint-mode-from-judgment-basis.md` |
| スプリントのモード | — | 1 つのスプリントに掛けるモード。スプリントの課題ごとの判定のうち、判定表で最も上の行のモード | — | — | `docs/specifications/ndf-sprint-mode-from-judgment-basis.md` |
| 判定の根拠のファイル | — | conductor が課題ごとの判定の根拠を書き、開発ループの loop sprint に --judgments で渡す JSON のファイル | — | — | `docs/specifications/ndf-sprint-mode-from-judgment-basis.md` |
| モードの決め直し | — | ループの 1 回から課題を外したとき、残った課題の判定の根拠からスプリントのモードを決め直すこと | — | — | `docs/specifications/ndf-sprint-mode-from-judgment-basis.md` |
| 区分の無い質問 | — | 問い合わせの区分（承認ゲート・レッドライン・費用など）のどれにも当たらない質問。AI が決めて記録する | — | — | `docs/specifications/ndf-factory-unattended-report.md` |
| 文字列 | — | 文字の並びとして持つ値 | 字で持（単独） | — | `docs/specifications/ndf-glossary-deprecated-meaning.md` |
| バージョン | — | ソフトウェア・文書・形式・設定を区別する番号か名前 | 版（単独） | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| 保存先 | `location` | ファイル・ディレクトリを置く場所。ndf-next では保存先の表（contracts/domains/locations.json）が ID ごとに持つ | 置き場 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| 定義 | — | コントラクトの表やスキーマに書いて決めること（「表に定義する」） | 宣言 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| キー | `key` | 行・記録・ロックを引く識別子 | 鍵（単独） | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| 起動 | `launch` | プロセス・セッションを始めること。始め直すことは再起動と呼ぶ。課題を新しく作ることは起票と呼ぶ | 起こす、起こし、起こさ、起こせ | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| マージ | — | ブランチを合わせること | 取り込み、取り込む、取り込ん、取り込ま、取り込め | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| 移行先 | — | 旧い機能が ndf-next で移る先（同等性の表の to の列） | 行き先 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| クォータ | `budget` | 期間・対象ごとに使ってよい量（5 時間のクォータ・週のクォータ・品質のクォータ） | 枠（単独） | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| マーカー | `markers` | 状態を示すファイル・ブランチ・チェックボックス | 印（単独） | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| フラグ | — | 引数や語で真偽を示すもの（導入の同意のフラグ・互換なしのフラグ） | 印（単独） | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| コントラクト | `contracts` | 入出力・結果の形の約束と、それを書いた表（contracts/） | 契約 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| バックアップ | `backup` | 戻すために取ったコピー | 控え、控える | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| スナップショット | `snapshot` | 比べる・同じ値を使い回すために残したコピー（設定のスナップショット・解決のスナップショット） | 控え、控える | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| 検証 | — | 2 つの値・内容を突き合わせて確かめること | 照合 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| マッチ | — | パターンに一致するかを見ること | 照合 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| 変更要求 | `request_changes` | 承認・レビューで直しを求める判定。承認ゲートの回答の値は request_changes | 差し戻し、差し戻す、差し戻さ、差し戻せ | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| 基盤 | — | 共通の部品・仕組み | 土台 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| オーナー | `owner` | 書き換えてよい者・持つ者 | 持ち主 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| テンプレート | `template` | 書き始めるときに写す型の文書・ファイル | 雛形 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| セグメント | — | 中継の下で 1 回の起動から切り替えまで | 区間 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| 期間 | — | 測る時間の範囲（復元の期間・引継ぎの期間） | 区間 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| 停止理由 | `cause` | 結果 JSON の cause。止まった原因の種類と説明 | 止まる理由、止まった理由 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| ジャーナル | `journal` | 追記で状態を持つ記録（計画のジャーナル・反映の記録・指摘のジャーナル） | 台帳 | — | `plugins/ndf-next/skills/development-workflow/references/glossary.md` |
| HEAD | — | ブランチの最新のコミット | 先端（単独） | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
| バージョンアップ | — | リリースの単位のバージョンを上げること（version bump） | バージョン上げ | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
| バージョン種別のラベル | — | バージョンアップの種別（major・minor・patch）を示す PR のラベル | 上げ幅のラベル | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
| パース | — | 設定や入力を読んで値を取り出すこと・解決すること | 読み分け | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
| セクション | — | 文書や設定ファイルの中の区切り | 節（単独） | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
| クリーンアップ | — | マージやリリースの後に、不要になったブランチ・worktree・一時ファイルを消すこと | 後片付け | — | `docs/specifications/ndf-next-terms-and-settings-renames.md` |
