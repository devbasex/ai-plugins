# ブランチ運用の宣言の外部の調査（#3121）

[issue-3121-design.md](issue-3121-design.md) の分冊である。本体の決定と根拠の表が引く STD・PRD・LIB の番号は、この文書の表の行を指す。
語の意味は本体の「用語」と「この文書が参照する語」の表にある。

## 調べた範囲

| 種類 | 調べるか | 理由 |
| --- | --- | --- |
| 標準・仕様 | 調べる | トピックブランチと結合ブランチの運用に git 本家の一次資料がある |
| 主要な製品 | 調べる | 宛先の制限をホスティングの機能（ルールセット）で持てるかが、リポジトリの側の検査の作り方を決める |
| ライブラリとサービス | 既に依存している部品だけを調べる | 範囲の判定は git、宣言の形の検査は pydantic で足り、新しい依存を足さない。ブランチ運用を宣言で受ける既存のツールは調べていない（下の「確かめられなかったこと」） |

### 標準・仕様

| # | 標準・仕様 | 何を定めているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| STD1 | gitworkflows(7) の Graduation・Throw-away integration | トピックブランチを最も古い取り込み先から切り、結合は使い捨てのブランチで試し、安定したものから上のブランチへ入れる | 選んで集める取り込み方の起点を本番チャネルにし、ベースブランチを本番へ流さない（I1・I4）。ベースブランチとの衝突は使い捨ての結合用のブランチで解く（決定 5） | https://git-scm.com/docs/gitworkflows （2026-11-12） |
| STD2 | gitworkflows(7) の「Rewind and rebuild next」 | リリース後に結合を試すブランチ（next）を本番の先頭から作り直してよい。作り直したらアナウンスする | 作り直しは採らず、本番チャネルを戻すマージだけを手順にする（決定 6） | https://git-scm.com/docs/gitworkflows （2026-11-12） |
| STD3 | git-rerere | 長く生きるトピックブランチで同じ衝突を繰り返し解くとき、記録した解き方を再利用する | リリースブランチを組む処理は、結合で解いた衝突を rerere で再利用する（[issue-3121-design-repo-rules.md](issue-3121-design-repo-rules.md) の決定 18） | https://git-scm.com/docs/git-rerere （2026-11-12） |

### 主要な製品

| # | 製品 | どう解いているか | 設計への反映 | 出典 |
| --- | --- | --- | --- | --- |
| PRD1 | GitHub のルールセット | 規則は作成・更新・削除の制限、PR の必須、必須のステータスチェックなど。Pull Request の head のブランチを制限する規則は一覧に無い | 宛先の制限は CI のジョブ（必須のステータスチェック）で持つ（[issue-3121-design-repo-rules.md](issue-3121-design-repo-rules.md) の決定 13） | https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets （2026-11-12） |
| PRD2 | ai-plugins のルールセット（`protect main`・`protect develop`） | 両方とも `guard`（宛先の検査）と `pytest` を必須のステータスチェックに持つ | ルールセットは変えずに、`guard` のジョブの判定を変える（[issue-3121-design-repo-rules.md](issue-3121-design-repo-rules.md) の決定 13・14） | `gh api repos/devbasex/ai-plugins/rulesets/<id>`（2026-11-12） |
| PRD3 | ブランチ運用のパターン（リリーストレイン） | 決まった便に、完成した機能のどれを載せるかを選ぶ | 「どのリリースの単位を載せるか」をリリースの提示で選ぶ（E9） | https://martinfowler.com/articles/branching-patterns.html （2026-11-12） |

### ライブラリとサービス

| # | 候補 | 版 | ライセンス | 最後のリリースの日付 | 終了のアナウンス | 要件を満たすか | 出典 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LIB1 | git（`merge-base --is-ancestor`・`rerere`・`merge --no-ff`） | 手元の版 | GPL-2.0 | 確かめていない | 無し | 満たす。範囲の判定と集める操作の部品として使う | `git --version`・STD1・STD3（2026-11-12） |
| LIB2 | pydantic（宣言の形の検査。NDF の `schema.Shape` が既に依存） | 確かめていない | MIT | 確かめていない | 無し | 満たす。宣言の検査に使う | `plugins/ndf/scripts/supervise_lib/decl.py`（2026-11-12） |

### 確かめられなかったこと

| 対象 | 試した手段 | 結果と理由 |
| --- | --- | --- |
| ブランチ運用を宣言で受け、スプリントの起点とリリースの範囲を切り替える既存のツール | 試していない | 調べる量の上限に達する前に、git の部品（LIB1）と既存の宣言の読み手で足りると判断して打ち切った。既存のツールが見つかれば、`branch_flow.py` の表の読み分けを置き換えられるかを次の棚卸しで比べる |
| LIB1・LIB2 の版と最後のリリースの日付 | 試していない | 既に依存している部品で、新しく足さないため |

調べた量: 候補 8 件・取得の試み 8 回。
