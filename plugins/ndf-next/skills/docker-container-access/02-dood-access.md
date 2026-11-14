# DooD環境でのアクセスガイド

## 特徴

- ホストのDockerデーモンを共有
- localhostはホストマシンを指す（他のコンテナではない）
- コンテナ間通信はDockerネットワーク経由

## アクセス方法

### 1. コンテナ名でアクセス（同一ネットワーク内）

```bash
# コンテナ名を確認
docker ps --format "table {{.Names}}\t{{.Ports}}"

# コンテナ名でアクセス
curl http://my-web-container:8080

# docker-composeの場合、サービス名でアクセス
curl http://web:8080
```

### 2. Dockerネットワーク経由

```bash
# ネットワーク一覧を確認
docker network ls

# 特定ネットワークのコンテナを確認
docker network inspect bridge --format '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{"\n"}}{{end}}'

# IPアドレスでアクセス
curl http://172.17.0.2:8080
```

### 3. 同一ネットワークへの参加

```bash
# 自分のコンテナを対象コンテナと同じネットワークに接続
docker network connect my-network $(hostname)

# その後、コンテナ名でアクセス可能
curl http://target-container:8080
```

## curlでのアクセス例

```bash
# NG: localhostは使えない
curl http://localhost:8080  # → Connection refused

# OK: コンテナ名を使用
curl http://my-app-container:8080

# OK: docker-composeのサービス名
curl http://api:3000

# OK: コンテナのIPアドレス
CONTAINER_IP=$(docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' my-container)
curl http://${CONTAINER_IP}:8080
```

## Playwright MCP / Chrome DevTools MCP

```bash
# DooD環境では、コンテナ名またはIPを使用
# URL: http://web-container:3000 (コンテナ名)
# URL: http://172.17.0.3:3000 (IP)
```

---

## bind mountの注意点

### 問題

DooD環境では、`docker run -v`や`docker-compose`のbind mountは**ホストマシンのパス**を参照します。開発コンテナ内のパスではありません。

```yaml
# NG: DooD環境では期待通りに動作しない
volumes:
  - ./local-dir:/app/data  # ホストの./local-dirを参照してしまう
```

### 解決策

#### 1. Dockerfileでコピー（推奨）

```dockerfile
FROM node:18
WORKDIR /app
COPY . .
RUN npm install
CMD ["npm", "start"]
```

#### 2. 名前付きボリュームを使用

```bash
# ボリュームを作成
docker volume create my-data

# ファイルをボリュームにコピー
docker run --rm -v my-data:/data -v $(pwd):/src alpine cp -r /src/. /data/

# ボリュームをマウントしてコンテナ起動
docker run -v my-data:/app/data my-image
```

#### 3. docker cpを使用

```bash
# コンテナにファイルをコピー
docker cp ./local-file.txt my-container:/app/

# コンテナからファイルを取得
docker cp my-container:/app/output.txt ./
```

### docker-compose.yml での対応

```yaml
# DooD環境対応版
version: '3.8'
services:
  app:
    build: .  # Dockerfileでファイルをコピー
    volumes:
      - app-data:/app/data  # 名前付きボリューム使用

volumes:
  app-data:
```
