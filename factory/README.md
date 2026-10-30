# Neural Development Factory

NDF を Web UI から操作できる Software Factory のサービスとして提供するときのコードを置くディレクトリ。GitHub のリポジトリを取り込み、NDF の開発ワークフローのスプリントを並列に動かし、一覧と詳細で進捗を見せ、承認ゲートで知らせる。

## プラグインとの関係

- プラグインとしての NDF（`plugins/` の下）とは別のディレクトリに置く。サービスは NDF をマーケットプレイスから導入して使い、`plugins/` のファイルをパスで読み込まない
- `plugins/` の下と `.claude-plugin/marketplace.json` は、このディレクトリを参照しない。どのプラグインの配布物にも、このディレクトリは入らない
- 境界は `python3 scripts/check-service-boundary.py` が見る（CI のジョブ `service-boundary-check`）

## ライセンス

このディレクトリのライセンスは [LICENSE](./LICENSE) が持つ。今は根と同じ MIT License である。ライセンスを差し替えるときは、この `LICENSE` と根の `README.md` の「ライセンス」の節だけを書き換える。プラグインの配布物と検査は影響を受けない。

## インフラと秘密

CDK のスタック・ネットワーク・秘密の管理は別の private リポジトリが持つ。このリポジトリにはインフラの定義・秘密・`.env` の実ファイルを置かない（境界の検査が落とす）。インフラから受け取る変数の名前は [.env.example](./.env.example) にあり、値は書かない。秘密は Secrets Manager の ARN だけを受ける。

## 置くもの

| ディレクトリ | 中身 | 作る Issue |
| --- | --- | --- |
| `contracts/` | 進捗の事象・実行ジョブの指示・API の形の機械可読な定義 | #2171 |
| `web/` | ログイン・リポジトリの登録・スプリントの一覧と詳細・承認・プランと請求の画面 | #2170・#2172〜#2174 |
| `api/` | 画面からの要求を受け、アカウントで認可して各集約の持ち主へ渡す処理 | #2170〜#2174 |
| `runner/` | 実行ジョブのコンテナ。リポジトリの取り込み・NDF の導入・conductor の起動・進捗の事象の送信 | #2171 |

全体の設計（構成要素・実行基盤・進捗の正本・構成要素の間の契約）は [docs/specifications/ndf-factory-service-boundary.md](../docs/specifications/ndf-factory-service-boundary.md) にある。
