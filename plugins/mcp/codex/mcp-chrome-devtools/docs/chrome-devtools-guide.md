# Chrome DevTools MCP ガイド

Chrome DevTools MCPサーバーでブラウザの操作・デバッグ・パフォーマンス測定を行います。

## 主要機能

- ページナビゲーション・DOM操作
- ネットワークリクエストの監視
- コンソールログの取得
- パフォーマンスプロファイリング
- スクリーンショット取得

## 用途

- **researcherエージェント**: JavaScript動的コンテンツの取得、Webスクレイピング
- **qaエージェント**: Webアプリケーションのパフォーマンス測定、UI検証

## 注意事項

- Docker環境（DinD/DooD）では`--no-sandbox`オプションが必要（設定済み）
- localhostへのアクセスはDocker環境に依存。詳細は `docker-container-access` スキルを参照
