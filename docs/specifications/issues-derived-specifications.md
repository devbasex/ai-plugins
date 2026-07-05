# issues 由来の確定仕様

`issues/` 配下に残っていた完了済み issue、plan、report から、現行実装と一致する仕様だけを抽出した確定仕様書群。

`issues/` は作業中の調査、実装計画、依頼文、報告書を含む開発記録として扱う。運用判断では本索引から参照する仕様、`docs/ndf-plugin-reference.md`、`plugins/ndf/README.md`、`plugins/ndf/CHANGELOG.md` を優先する。

Skill の挙動仕様は本ディレクトリでは管理しない。Skill に関する詳細は、対象 Skill の `SKILL.md` を正とする。

## 仕様書一覧

| 仕様書 | 内容 |
|---|---|
| [ndf-knowledge-and-kiro.md](ndf-knowledge-and-kiro.md) | NDF 知識構造、Serena 分離、Kiro CLI 対応 |

## 対象外

未実装、未完了、または現行リポジトリで確定仕様として扱えない依頼は仕様化しない。例: `issues/i11.md` の Redash MCP プラグイン案は作業中 issue として `issues/` に残すため、完了済み仕様への取り込み対象外とする。

Skill に関する完了済み issue / plan / report は、該当 Skill の実装と `SKILL.md` に統合済みとして扱い、本ディレクトリには仕様書を置かない。

## 共通セキュリティ

認証情報、トークン、Slack Webhook / Bot Token、Google OAuth credentials、GitHub token はコミットしない。設定は環境変数または利用者環境の設定ファイルで管理する。

外部 AI、GitHub、Google Drive、Slack などへ情報を送信する機能では、送信内容と共有範囲を事前に確認する。

## 関連リンク

- [NDF Plugin リファレンス](../ndf-plugin-reference.md)
- [プラグイン開発ガイド](../plugin-development-guide.md)
- [AI Plugins プロジェクト概要](../project-overview.md)
- [NDF README](../../plugins/ndf/README.md)
- [NDF CHANGELOG](../../plugins/ndf/CHANGELOG.md)
