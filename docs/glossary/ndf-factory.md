# 用語集: Neural Development Factory（`ndf-factory`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

Neural Development Factory（別のリポジトリのサービス）が NDF を呼ぶときの API・入出力の規約の語。規約は plugins/ndf/contracts/ が持つ

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| Neural Development Factory | — | NDF を Web UI から操作できる Software Factory のサービスとして提供するときの名前。プラグインとしての NDF と区別するときに使う | — | — | `docs/specifications/ndf-parallel-organizer.md` |
| ランナーのエージェント | — | 1 つの実行ジョブの中で利用者のリポジトリをマージ、NDF を導入して conductor を動かし、NDF の書くファイルを進捗の事象へ翻訳して送るプログラム | — | — | `docs/specifications/ndf-ask-guard.md` |
| 実行ジョブ | — | 実行基盤が 1 回起動するコンテナ 1 つ分の実行。スプリントの開始か承認の回答で始まり、次の承認のリクエスト・止まった・終わったのどれかで終わる | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| 起票のリクエスト | — | 実行ジョブの中で NDF が別のリポジトリへの起票を決めたとき、ランナーのエージェントが進捗の事象 issue.requested でサービスへ送るリクエスト。起票先・題・本文・種類のラベルを持ち、ランナーのエージェントは自分では起票しない | — | — | — |
| 起票のリクエストの結果 | — | 起票のリクエスト 1 件ごとに 1 件残る進捗の事象 issue.request_resolved。サービスが起票した Issue の参照か、下書きへ回した理由を持つ | — | — | — |
| セグメント | — | 1 つの実行ジョブの中でランナーのエージェントが起動する claude -p の 1 回分。conductor の ndf-next で次のセグメントへ移る | — | — | `docs/specifications/ndf-development-loop-limits.md` |
| リリースの実行 | — | Factory のオーガナイザーが、まとめたリリース 1 つのために起動するスプリントの実行。範囲のどの実行でもなく、承認資料を作り、リリースを流し、結果を返して終わる。同じリポジトリで同時に 1 本まで | 配布の実行 | — | `docs/specifications/ndf-deploy-and-release-terms.md` |
| 人のいない実行 | — | 環境変数 NDF_FACTORY_RUN が空でない実行ジョブのセグメント。AskUserQuestion が使えず、応答を人が読まない | — | — | `docs/specifications/ndf-factory-unattended-report.md` |
| 結果の行 | — | 最後の応答の文の中の、最後の「- 結果: <値>」の行。値は 判断が要る / 完了 / できなかった の 3 つで、Factory がセグメントの結果を決める | — | — | `docs/specifications/ndf-factory-unattended-report.md` |
| セグメントの報告 | — | 人のいない実行の conductor がセグメントを終える最後の応答の末尾に置く報告。見出し「## セグメントの報告」の直後の項目が結果の行で、値ごとに要る項目を添える。形は contracts/forms/unattended-report.json | — | — | `docs/specifications/ndf-factory-unattended-report.md` |
| 人のいない実行のマーカー | — | 人のいない実行の conductor のセッションに UserPromptSubmit hook が付ける状態のファイル。Stop hook が変更要求の対象と回数をここで決める | — | — | `docs/specifications/ndf-factory-unattended-report.md` |
| 結果の行の変更要求 | — | 人のいない実行のマーカーのあるセッションで、最後の応答がセグメントの報告の形に合わないとき、Stop hook が Stop を止めて報告を書かせること。形に合わない Stop が続く間は 2 回までで、形に合った Stop で数え直す | — | — | `docs/specifications/ndf-factory-unattended-report.md` |
| 連携システム | — | NDF を外から呼び、`.ndf/` の設定ページを作るシステム（サービス Factory のランナーと、その上の設定画面） | — | — | `docs/specifications/ndf-next-declarations-and-io-contracts.md` |
