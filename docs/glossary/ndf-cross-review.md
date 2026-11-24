# 用語集: NDF の cross-review（`ndf-cross-review`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

Pull Request のレビューを CLI へ委譲し、新しい指摘が出なくなるまで回す収束ループの語。指摘・反証・総評・修正担当

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| 指摘ファイル | — | レビューする者（`cross-review` の担当・`pr-review` の LLM か外部 AI）が書く、指摘の全件と総評のファイル | 指摘のファイル | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 指摘 | `finding` | 担当が出した 1 件の主張（review_findings[] の 1 要素） | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 反証条件 | — | 何が成り立てばその指摘を棄却できるか（falsification） | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 検証手順 | — | 指摘を実行できる形で確かめる手順（suggested_check） | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 反証 | — | 提案者以外の担当が、各指摘へ返す 1 つの値 | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 指摘区分 | — | 1 件の指摘を分ける 6 つの分類（classification） | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 集約方式 | — | 効果の測定で指摘を採る規則。single / majority / proposed / oracle の 4 つ | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 代表指摘 | — | 統合した組で判定が読む 1 件。統合された側は merged_into を持つ | — | — | `docs/specifications/cross-review-evidence-based.md` |
| 新しい指摘 | — | 直前のラウンドの指摘と一致しない、そのラウンドの指摘。収束ループはこれが 0 件になるまで回す | — | — | `docs/specifications/cross-review-round-inputs.md` |
| 修正担当 | — | 指摘を直してコミットするサブエージェント（/ndf:fix を実行する） | 修正の担当 | — | `docs/specifications/cross-review-writes-to-conductor.md` |
| 投稿キュー | `post_queue` | 送る前に投稿を積み、上限か一時的な失敗で送れなければ残す仕組み（lib/post_queue.py。一時的な失敗は #1843）。スプリント 2c の後、項目は耐久の記録に置く | 投稿の待ち行列 | — | `docs/specifications/cross-review-writes-to-conductor.md` |
| 重複投稿 | — | 送ろうとした投稿と同じものとして、すでに Pull Request にある投稿 | 先客 | — | `docs/specifications/cross-review-writes-to-conductor.md` |
| 総評 | — | レビュー本体に書く文章（body）。差分の行に付けるコメントとは別に置く | — | — | `docs/specifications/cross-review-writes-to-conductor.md` |
| 総評だけの指摘 | — | 差分の行に付くスレッドを持たず、総評にだけ書かれた指摘（review_findings[] の posted_to が body のもの）。差分の外を指す指摘と、HTTP 422 で総評へ移した指摘がこれになる | — | — | `docs/specifications/cross-review-body-findings.md` |
| 決着 | — | 指摘が直した・見送った・却下したのどれかに振り分けられ、マージ（修正のラウンドの merge-fix か最終スイープの verify-sweep）が記録したこと。スレッドの指摘ではスレッドの解決も含む | — | — | `docs/specifications/cross-review-body-findings.md` |
| 決着の記録 | — | 状態ファイルの body_settlements[] の 1 要素。総評だけの指摘 1 件の振り分け（fixed / deferred / waived / separate_pr / rejected）と、記録したラウンドと経路（修正のラウンドの merge-fix か最終スイープの verify-sweep か）を持ち、追記だけで書き換えない | — | — | `docs/specifications/cross-review-body-findings.md` |
| 根拠の行の確かめ | — | Pull Request が足した MVV の根拠の行（指示と結果での呼び名は「根拠の記述」）について、書いたバージョンが承認済みの今のバージョンか、項目がそのバージョンにあるかを、ndf cross-review run が機械で確かめること。誤りは直るまで残り、見送れない | — | — | `plugins/ndf/skills/cross-review/SKILL.md` |
| MVV に反する指摘 | — | cross-review の指摘のうち、承認済みのプロジェクト MVV に反するもの（指摘の基準 5）。Mission・Vision・Value に反するものは修正担当へ渡り、必ず人の承認が要る操作に当たるものは渡さずに人の承認を待つ | — | — | `plugins/ndf/skills/cross-review/docs/01-state-and-review.md` |
| コメントのスナップショット | — | cross-review が取る既存コメントの一覧。2 ラウンド目以降は取り直す | 既存コメントのスナップショット | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| drive の状態 | `drive_state` | 収束ループの drive.py が Pull Request ごとに持つ状態ファイル（drive-pr<N>.json / drive-rf<ID>.json）。stage と init_vars を持つ。スプリント 2c の Q3・Q4 で耐久の記録へ移して廃止する | — | — | — |
| 指摘の基準 | — | 指摘として出してよいものを決める番号つきの条件。既定は 1 利用者が普通に使う経路の誤動作・2 レッドラインに触れるもの（外せない基準）・3 レビューの重点（設定があるときだけ）・4 実装を違えさせる設計の食い違い。レビューのプロファイルは 2・3 以外の番号を差し替える。当たるものが major 以上になる | — | — | `docs/specifications/cross-review-review-profile.md` |
| レビューの重点 | — | プロジェクトが .ndf/review.json で定義した、指摘の基準 3 に使う観点。設定が無ければ基準 3 は無い | — | — | — |
| 見送りの返信 | — | 修正担当が minor / nit の指摘を直さずに閉じるときに書く返信。テンプレート（既定か、レビューのプロファイルの reply）の {inner} に見送りの種類の名前を入れて組む。既定のテンプレートは直す条件（使って困る場面が出たら直す）を定型文で書く | — | — | `docs/specifications/cross-review-review-profile.md` |
| 見送りの種類 | — | 見送りの返信の括弧に書く、指摘の基準に当たらない理由の分類（waive_kind）。既定は 5 分類で、レビューのプロファイルの waive_kinds が差し替える | — | — | `docs/specifications/cross-review-review-profile.md` |
| レビューのプロファイル | `review_profile` | プロジェクトが .ndf/review.json に書く、指摘の基準・書かないもの・見送りの種類・見送りの返信・自動レビュー観点の差し替えの組。version 2 では profile に 1 つ、version 3 では profiles に名前つきで複数を書き、PR ごとに 1 つが効く。書いたキーだけが既定を差し替える。version 3 の最上位の criteria・not_written は、どのプロファイルを選んでも（選ばなかったときは既定の組にも）足す。文中では「プロファイル」と略す | — | — | `docs/specifications/cross-review-review-profile.md` |
| プロファイルの選択 | — | PR の変更ファイルのパスから、その PR に効かせるレビューのプロファイルを 1 つ決めること。設定の profiles の先頭から見て、変更ファイルのどれかに paths が当たる最初のものを選び、paths を持たないものはどの PR にも当たる。選んだ名前を init の出力・レビューの状態ファイル・report の要約に残す | — | — | `docs/specifications/cross-review-review-profile.md` |
| 自動レビュー観点 | — | cross-review が変更ファイルの分類（自動カテゴリ）から選び、レビューの両スロットへ渡す観点の文。--focus と --extra-instructions-file の観点はこの後ろに足す | — | — | `docs/specifications/cross-review-review-profile.md` |
| 外せない基準 | — | 指摘の基準 2（秘密・認証認可・利用者のデータ・戻せない操作）。レビューのプロファイルがあっても消せず、番号 2 のまま出る | — | — | `docs/specifications/cross-review-review-profile.md` |
| 最終スイープ | — | 収束ループを抜けた後に /ndf:fix を通し、open thread を 0 にする工程 | — | — | — |
| 結果を変える引数 | `result_args` | cross-review・cross-refactoring の drive が `init` へ渡す引数のうち、保存先・出力だけを変えるものを除いた残り。完了の記録に残し、打ち直しで今回と比べる | — | — | `docs/specifications/cross-drive-rerun-pause-and-denials.md` |
| レビューの状態ファイル | — | cross-review が <worktree>/.cross_review/cross-review-pr<PR>-state.json に置く、ラウンドの履歴（rounds）と終わり方（final）の記録 | — | — | `docs/specifications/cross-review-reopen-and-hook-deadline.md` |
| 修正の記録 | — | 修正の工程が作る戻り値ファイル（fix-pr<PR>-result.json）を merge-fix がレビューの状態ファイルへ読み込んだもの。ホストが自分で直したときは state.py record-fix が同じ形で作る。修正必須のラウンドの次のラウンドを始める条件 | — | — | `docs/specifications/cross-review-reopen-and-hook-deadline.md` |
| ラウンドを足す | — | final が確定したレビューの状態ファイルの final を外し、履歴を残したまま次の番号のラウンドから収束ループを続けること。state.py init の再開の経路が行い、reopens に 1 件残す | — | — | `docs/specifications/cross-review-reopen-and-hook-deadline.md` |
| 最終ステータス | `review_status` | cross-review の収束の終わり方を表す 1 語（approved / unverified / final の値 / unknown）。loop_drive.review_status が状態ファイルから決める | — | — | — |
| 回答ファイル | — | 駆動が止まりで示す items[0].result_file。止まりへの回答を書き、同じコマンドの打ち直しが読む。担当 CLI の結果ファイルとは別 | — | — | — |
| 席 | — | cross-review の 1 ラウンドの 2 つの担当のスロット。名前は kiro / kiro-2 の形（SEAT_PATTERN） | — | — | `docs/specifications/cross-assignee-reassignment-on-no-result.md` |
| 未解決のスレッド | — | Pull Request のレビュースレッドのうち、解決済みのマーカーが付いていないもの。`gh_parts.py pr-info --with threads` が返す | — | — | `docs/specifications/pr-review-steps-script.md` |
| 本来の判定 | `intent` | レビューの指摘から決めた event（APPROVE / REQUEST_CHANGES / COMMENT）。自分の Pull Request へ COMMENT で送っても結果に残し、収束や報告はこちらを読む | — | — | `docs/specifications/pr-review-steps-script.md` |
| レビューの文脈ファイル | — | `pr-review-steps.py collect` が書く、レビューの対象・差分の在りか・未解決のスレッド（位置と最初のコメントの本文）・指摘ファイルの書き方をまとめたファイル。外部 AI へのプロンプトはこれに観点と委譲の決まりを足して組む | — | — | `docs/specifications/pr-review-steps-script.md` |
| 仕様適合 | — | レビューの 1 つ目の観点。受け入れ条件・不変条件・対象範囲・テストが仕様を表すかを見る。満たさない指摘は指摘ファイルで `stage` の値 `spec` を持つ | — | — | `docs/specifications/pr-review-steps-script.md` |
| 一時的な失敗 | — | 送り直せば届く見込みのある投稿の失敗（HTTP 500・502・503・504、`(HTTP nnn)` の無い本文なしの応答、ネットワークの失敗）。上限とは別に見分け、投稿キューに残して後で流す（#1843） | — | — | — |
| 投稿待ち | — | 担当の結果は記録へ読み込んであり、投稿だけが投稿キューに残っている状態。結果なし（NO_RESULT）とは別である（#1843） | — | — | — |
| レビュー範囲 | — | そのラウンドで担当が見る差分。base と head の SHA の組と、その間の変更ファイル | — | — | `docs/specifications/cross-review-review-scope.md` |
| 読んだファイル数 | — | 担当が結果ファイルの read_files に書く、レビュー範囲のうち差分を読んだファイルの数。担当の自己申告 | — | — | `docs/specifications/cross-review-review-scope.md` |
| 範囲の検証 | — | 判定で、通した担当の読んだファイル数をレビュー範囲のファイル数と突き合わせ、半分に満たない・測れない担当の「通した」を収束に数えないこと | — | — | `docs/specifications/cross-review-review-scope.md` |
