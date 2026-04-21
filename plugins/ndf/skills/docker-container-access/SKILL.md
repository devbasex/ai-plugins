---
name: docker-container-access
description: |
  Dockerコンテナへのアクセス方法を判定し、適切な接続コマンドを提供します。DinD/DooD環境の自動検出とトラブルシューティング。

  このSkillは以下を提供します:
  - DinD/DooD環境の判定方法
  - 環境に応じた接続方法の選択
  - bind mountの注意点と代替手段
  - curlやPlaywright MCPでの接続例

  Triggers: "docker access", "container connect", "localhost not working", "DinD", "DooD", "Docker接続", "コンテナアクセス", "curl container"
allowed-tools:
  - Read
  - Bash
  - Glob
---

# Docker Container Access Skill

## 概要

ローカル開発環境がDocker開発コンテナ上で動作している場合、他のDockerコンテナへのアクセス方法が通常と異なります。このスキルでは、環境を判定し、適切なアクセス方法を選択するためのガイドラインを提供します。

## クイックリファレンス

```
環境判定 → アクセス方法
────────────────────────────────
ホスト環境           → localhost:port
DinD環境            → localhost:port
DooD環境            → container-name:port または IP:port

ファイル共有（DooD環境）
────────────────────────────────
Dockerfile COPY     → 推奨（ビルド時にコピー）
名前付きボリューム   → 推奨（永続化が必要な場合）
docker cp           → OK（一時的なコピー）
bind mount          → NG（ホストのパスを参照）
```

## 環境判定（最初に実行）

```bash
# 自分がコンテナ内か確認
[ -f /.dockerenv ] && echo "コンテナ内" || echo "ホスト環境"

# DooD判定
ls -la /var/run/docker.sock 2>/dev/null && echo "DooD環境" || echo "DinDまたはホスト"
```

| 環境 | 説明 | コンテナへのアクセス |
|-----|------|-------------------|
| **ホスト** | 通常のDocker環境 | `localhost:port` |
| **DinD** | コンテナ内に独立したDockerデーモン | `localhost:port` |
| **DooD** | ホストのDockerソケットを共有 | `container-name:port` |

## 詳細ガイド

詳細は以下のファイルを参照してください：

| ファイル | 内容 |
|---------|------|
| `01-environment-detection.md` | 環境判定の詳細、判定スクリプト |
| `02-dood-access.md` | DooD環境でのアクセス方法、bind mount注意点 |
| `03-troubleshooting.md` | トラブルシューティング、ベストプラクティス |

## よくある問題（簡易版）

| 症状 | 原因 | 解決策 |
|-----|------|--------|
| `localhost`で接続できない | DooD環境 | コンテナ名を使用 |
| bind mountしたファイルが見えない | DooD環境 | `docker cp`または名前付きボリューム |
| コンテナ間で通信できない | 別ネットワーク | 同じネットワークに接続 |

## 関連Skill

- **python-execution**: Python実行環境の判定
- **corder-code-templates**: Dockerfileテンプレート

## 関連リソース

- [Docker Networking](https://docs.docker.com/network/)
- [Docker in Docker](https://hub.docker.com/_/docker)
