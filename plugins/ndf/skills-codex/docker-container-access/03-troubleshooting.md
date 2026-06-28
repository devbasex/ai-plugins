# トラブルシューティング

## Q: `curl: (7) Failed to connect to localhost port 8080`

**原因**: DooD環境でlocalhostを使用している

**解決策**:
```bash
# コンテナ名またはIPを使用
docker ps  # コンテナ名を確認
curl http://container-name:8080
```

## Q: bind mountしたファイルが見えない

**原因**: DooD環境ではホストのパスを参照している

**解決策**:
```bash
# docker cpでコピー
docker cp ./file.txt container:/app/

# または名前付きボリュームを使用
```

## Q: コンテナ間で通信できない

**原因**: 異なるDockerネットワークに所属している

**解決策**:
```bash
# 同じネットワークに接続
docker network connect my-network container-a
docker network connect my-network container-b
```

## Q: docker.sockへのアクセス権限がない

**解決策**:
```bash
# docker グループに追加（要再ログイン）
sudo usermod -aG docker $USER

# または一時的に権限付与
sudo chmod 666 /var/run/docker.sock
```

---

# ベストプラクティス

## DO（推奨）

- **コンテナアクセス前に環境を判定する**
- **DooD環境ではコンテナ名/サービス名を使用する**
- **ファイル共有はDockerfileのCOPYまたは名前付きボリュームを使用**
- **docker-composeではサービス名でアクセス**

## DON'T（非推奨）

- **環境を確認せずにlocalhostを使用する**
- **DooD環境でbind mountに依存する**
- **IPアドレスをハードコードする（変わる可能性がある）**
