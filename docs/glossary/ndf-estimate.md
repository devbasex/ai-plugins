# 用語集: NDF の工数の見積もり（`ndf-estimate`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

AI エージェントが実施する前提で、作業の A・H・L・C を類似の実績から見積もり、予測と実績を突き合わせるときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| AI の実行量 | — | 作業の調査・設計・実装・検証・修正で、AI エージェントが動いた時間の合計。並列に動いた分は足す（A） | — | — | `docs/specifications/ndf-estimate.md` |
| 人の作業時間 | — | 要件の判断・説明・レビュー・手動の確認・修正への介入に、人が手を動かした時間。判断を待つ時間は含まない（H） | — | — | `docs/specifications/ndf-estimate.md` |
| 完了までの時間 | — | 着手から完了条件に届くまでの経過時間。並列・依存・CI・承認の待ちを含む（L） | — | — | `docs/specifications/ndf-estimate.md` |
| 利用費用 | — | 作業に使った LLM と実行環境の費用（USD）。サブスクリプションのスロットの消費は別に記録する（C） | — | — | `docs/specifications/ndf-estimate.md` |
| 実行の構成 | — | 実績を比べる前提になる、モデル・ハーネス・レビューの方式・テストの方式・並列の数の組 | — | — | `docs/specifications/ndf-estimate.md` |
| 類似の実績 | — | 完了条件・6 項目・実行の構成が近い、過去の作業の A・H・L・C の実測 | — | — | `docs/specifications/ndf-estimate.md` |
| 未校正 | — | 類似の実績が無く、想定の手順と既知のテストの所要から出した暫定の見込みであることのマーカー | — | — | `docs/specifications/ndf-estimate.md` |
| 見積もりの記録 | — | 見積もりの時点の 6 項目・実行の構成・予測した A・H・L・C を残したもの。後で実績と突き合わせる | — | — | `docs/specifications/ndf-estimate.md` |
| 実績の記録 | — | supervise の実行ごとに、始まり・ステップの終わり・実行の終わりを追記する行の列。終わり方（成功・承認ゲート・失敗・取り消し）と秒・USD・実行の構成を持ち、終わりの行の無い実行は、実行中（`step.pid` が生きている）とマージした実行（`run_start` の `imported` が真）を除いて中断として数える | — | — | `docs/specifications/ndf-estimate.md` |
| 着手前の 6 項目 | — | 要件の確定度・必要な情報の取得・変更の波及・検証のしやすさ・環境の準備・手戻りの見込み。それぞれ軽・重・判定できないの 3 値で、加点して時間へ換算せず、類似の実績を選ぶ材料と説明に使う | — | — | `docs/specifications/ndf-estimate.md` |
| 見積もりの完了条件 | — | 見積もる作業がどこまで終われば完了かを、supervise のフェーズの名前（設計・実装・検査・開発版のデプロイ・リリース）で表したもの | — | — | `docs/specifications/ndf-estimate.md` |
| 尺度を超える | — | A の見込みと Size 1 の基準との比が、尺度の最大を超えたときの Size の値。最大へ潰さず、分割を検討するマーカーにする | — | — | `docs/specifications/ndf-estimate.md` |
| Size 1 の基準 | — | Size を A の見込みから丸めるときの単位の秒。設定が無ければ、実績のある課題のうち A の小さい 4 分の 1 の中央値 | — | — | `docs/specifications/ndf-estimate.md` |
