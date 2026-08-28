# 環境判定ガイド

## Step 1: 自身の環境を確認

```bash
# 自分がコンテナ内で動作しているか確認
cat /proc/1/cgroup 2>/dev/null | grep -q docker && echo "コンテナ内" || echo "ホスト環境"

# または
[ -f /.dockerenv ] && echo "コンテナ内" || echo "ホスト環境"
```

## Step 2: Docker環境の種類を判定

自身がコンテナ内の場合、以下のいずれかの環境です：

| 環境 | 説明 | 判定方法 |
|-----|------|---------|
| **DinD** (Docker in Docker) | コンテナ内に独立したDockerデーモン | `docker info`でDocker rootが`/var/lib/docker` |
| **DooD** (Docker outside of Docker) | ホストのDockerソケットを共有 | `/var/run/docker.sock`がマウントされている |

```bash
# DooD判定: docker.sockがマウントされているか
ls -la /var/run/docker.sock 2>/dev/null && echo "DooD環境の可能性" || echo "DinDまたはホスト環境"

# Docker rootディレクトリの確認
docker info 2>/dev/null | grep "Docker Root Dir"
```

## DinD環境でのアクセス

DinD環境では、**localhost**で他のコンテナにアクセスできます。

### 特徴
- コンテナ内に独立したDockerデーモンが動作
- ネットワークは通常のDocker環境と同じ
- `localhost:ポート`でアクセス可能

### アクセス例

```bash
# Webサーバーへのアクセス
curl http://localhost:8080

# データベースへの接続
mysql -h localhost -P 3306 -u user -p

# Playwright MCPでのアクセス
# URL: http://localhost:3000
```

## 環境判定スクリプト

```bash
#!/bin/bash
# Docker環境判定スクリプト

echo "=== Docker環境判定 ==="

# 自分がコンテナ内かチェック
if [ -f /.dockerenv ] || grep -q docker /proc/1/cgroup 2>/dev/null; then
    echo "実行環境: Dockerコンテナ内"

    # DinD/DooD判定
    if [ -S /var/run/docker.sock ]; then
        echo "Docker形式: DooD (Docker outside of Docker)"
        echo ""
        echo "→ 他のコンテナへのアクセスにはコンテナ名を使用してください"
        echo "→ bind mountはホストのパスを参照するため注意が必要です"
    else
        echo "Docker形式: DinD (Docker in Docker)"
        echo ""
        echo "→ localhostで他のコンテナにアクセス可能です"
    fi
else
    echo "実行環境: ホストマシン"
    echo ""
    echo "→ 通常のDocker操作が可能です"
fi

echo ""
echo "=== 利用可能なコンテナ ==="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || echo "Dockerが利用できません"
```
