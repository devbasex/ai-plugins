# ニーズとシーズの調査

**具体例**: 開発ツールのプラグインは、`materials.needs.usage` に Skill の利用統計を集計するコマンド（`aggregate_only: true`）を、
`seeds.libraries` に pytest（競合の候補 ward）を定義する。調査は「`needs.usage:<名>`」「`seeds.library:pytest`」
「`seeds.alternatives:pytest`」の項目に分かれ、見直しの中で 1 項目ずつ調べて
作業ディレクトリに出典つきで書く。Web サービスなら同じ項目に、アクセス解析の集計のコマンドと帳票のライブラリが入る。

**フローは共通で、何を材料に調べるかはプロジェクトが定義する。** 調査は結果を書くだけで、Issue の起票・クローズ・
ラベル・マイルストーン・順位を変えない。起票は見直しの手順 7 だけが行う（[review.md](review.md)）。

## 材料の設定

`.ndf/project.json` の `materials`。形は `development-workflow/schemas/project.schema.json` が SSOT である。案は
`ndf project measure` が質問（`question`）で出し（README の冒頭・直接の依存・解析の SDK の依存を根拠にする）、
人が確かめて書く（`development-workflow` の `references/project-analysis.md`）。

| キー | 中身 | 調査の項目 |
| --- | --- | --- |
| `product` | プロダクトの種類（`kind`）と利用者（`users`） | すべての項目の前提として渡す |
| `needs.usage[]` | 利用の記録を読むコマンドと範囲（`name`・`command`・`scope`） | `needs.usage:<name>`・`method.unused:usage` |
| `needs.market[]` | 比べる相手の製品（`name`・`url`・`viewpoints`） | `needs.market:<name>` |
| `seeds.libraries[]` | 調べる依存（`name`・`ecosystem`・`alternatives`・`viewpoints`）。直接の依存のうち人が残したものだけ | `seeds.library:<name>`（変化）・`seeds.alternatives:<name>`（競合） |
| `seeds.environment[]` | 法令・業界標準・ブラウザや OS など見ておく変化（`name`・`url`） | `seeds.environment:<name>` |
| `deferred_milestones[]` | 後回しのマイルストーンの題名（見直しの対象から外す） | — |
| `cause_types[]` | 原因の型の一覧（`id`・`name`・`defect`）。無ければ用語集の「原因の型」A〜I | 大整理 |
| `code_measures[]` | 大整理の裏付けの計測のコマンド（`name`・`command`・`types`） | 大整理 |
| `failures[]` | 失敗の記録の読み先（開発ループの記録・振り返り・障害の記録のパス） | `method.failures:loop` |

`materials` があれば、ホットスポット（`method.hotspot:git`。git の履歴を `git log --name-only` で数える）と失敗の集計
（`method.failures:loop`）も調べる。設定の無い側面は `skipped_aspects` に「材料なし（未設定）」で載り、推測で調べない。
`materials` が無いプロジェクトはすべての側面を飛ばす。

シーズの 1 つの依存は 2 つの項目になる。**変化**（新しいバージョンの機能・非推奨・保守の停止・脆弱性・ライセンスの変更）と、
**競合**（置き換え先の候補と比べた観点: 保守の状況・性能・ライセンス・移行の費用。自前の実装を置き換えられるか）である。

## 利用の記録を読む範囲

本番の利用の記録には個人情報と秘密が含まれうる（共通原則 C1・C3）。

- `scope.aggregate_only` が `true` の行だけを読む。`false` の行と、`scope` の無い行は読まず、`skipped_aspects` に理由を書く
- 設定が無ければ利用の記録を何も読まない
- コマンドは調べる側が打ち（期間 `scope.days` は環境変数 `NDF_USAGE_DAYS` で渡す）、`scope.mask`（正規表現。当たった所を
  `***` にする）と秘密の伏せ字（`secret_redact`）を通した出力だけを調査へ渡す。調査の supervisor は読み先へ自分でつながず、
  手元を読む項目には Web の Tool を、Web を読む項目には手元を読む Tool を渡さない
- 読み先の認証は人が済ませる。NDF は認証の設定をしない

## 調べ方

**調査は見直しの中で、その場で行う**（待ち時間の調査とその記録のサブコマンドは無い）。項目ごとに次を守る。

| 決まり | 中身 |
| --- | --- |
| 1 回の量 | 見直し 1 回で調べる項目は、秒の上限（`ndf issues depth-limits review`）に収まる数まで。残った項目は報告の `skipped_aspects` に理由つきで残す |
| 記録 | 出典（`url` / `path` / `command`）と調べた日時のそろった結果だけを、見直しの作業ディレクトリに書く |
| 外部 | Web は読み取りだけ（WebFetch・WebSearch）。外部へ書き込まない |
