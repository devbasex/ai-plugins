# 08: 自動レビュー観点

変更ファイルの分類から担当へ渡す追加の観点の一覧。**手順の途中では読まず、分類と観点を確かめるときだけ開く。** 手順は [01-state-and-review.md](01-state-and-review.md) にある。

## 自動レビュー観点テンプレート

`state.py init` は GitHub API の `pulls/<PR>/files --paginate` で変更ファイルを全件取得して分類し、
そのラウンドのレビュー担当に同じ追加観点を渡す。`--focus` /
`--extra-instructions-file` は、この自動テンプレートの後ろに上乗せされる（レビューのプロファイルの観点があっても同じ）。レビューのプロファイルの分類（[02-fix-and-rotation.md](02-fix-and-rotation.md) の「レビューの宣言」）に当たる変更ファイルがあれば、その観点を既定の観点の後ろに足し、`replace_auto_categories` が真なら既定の自動カテゴリ（`common` / `docs_only` を含むすべて）の観点を渡さない（外部の調査の確認の観点は外さない。下の `code`）。

自動カテゴリ:

- `common`: PR 全体の目的、変更範囲、保守性、テスト、ロールバック容易性
- `docs_only`: ドキュメントのみ PR。企画・説明の妥当性、コード/設定/コマンド/他 docs との整合性
- `design`: 設計 PR（`issues/` の `-requirements.md` / `-design.md` / `-design-decisions.md`）。3 文書の対応、状態の書き手と読み手の矛盾、外部ツールの挙動の断定に実測の根拠があるか、用語集どおりか、不変条件を破っていないか、コンテキストの境界を越えていないか。モデルレビューと詳細レビューに分けて渡す（下の「設計 PR のモデルレビューと詳細レビュー」）。詳細レビューには `code` と同じく外部の調査の確認を足す

### 設計 PR のモデルレビューと詳細レビュー

| レビュー | ラウンド | 見るもの | 渡す観点 |
| --- | --- | --- | --- |
| モデルレビュー（`model`） | 1 ラウンド目（`design_has_model` が真のとき） | ドメインモデルの節と、用語集の差分だけ | コンテキストの関係の宣言・集約の持ち主・不変条件どうしの矛盾・ドメインイベントの受け手・用語の表と用語集の一致。節の外への指摘は書かない |
| 詳細レビュー（`detail`） | 2 ラウンド目以降（節が無ければ 1 ラウンド目から） | 残りの節 | `design` の観点。ドメインモデルの節は確定したものとして扱う |

モデルレビューで両スロットが承認しても抜けない。承認ゲートの数とラウンドの上限（設計は 3）は変えない。
- `code`: 設計、正確性、可読性、冗長・重複、言語らしさ、セキュリティ、関数/ファイルの責務とサイズ。既定の分類が `design` か `code` を持つ PR には、`pr-review` の SKILL.md の節 `### 外部の調査の確認` を読んで `## 自動追加レビュー観点: 外部の調査の確認` の見出しで最後に足す（プロファイルの `replace_auto_categories` でも外さない。節を読めなければ `init` が止まる）
- `db_migration`: データ設計、型、NULL/default/制約/index、既存データ、backfill、ロールバック
- `test`: テストの仕様性、境界値、失敗系、flaky リスク
- `dependency`: 依存追加/更新、lockfile、ライセンス、互換性、セキュリティ
- `config_ci`: CI/設定、権限、secret、cache、環境差分
- `api_contract`: API 契約、互換性、schema、status、エラー形式、認可
- `auth_security`: 認証/認可、secret/PII、CSRF/CORS/session/JWT/OAuth
- `frontend`: UI 状態、アクセシビリティ、レスポンシブ、状態管理、表示文言
- `performance`: N+1、I/O、メモリ、ロック、cache、queue、冪等性
- `deletion_rename`: 削除/リネーム参照漏れ、後方互換、移行手順
- `generated`: 生成物、lockfile、再生成手順、差分ノイズ
- `i18n`: 翻訳キー、fallback、変数展開、表示幅、文言整合
- `infra`: IaC / Docker / Kubernetes 等の権限、secret、公開範囲、ロールバック
