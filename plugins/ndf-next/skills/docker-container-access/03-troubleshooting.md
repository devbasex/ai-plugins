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

**解決策**: 権限を自分で付けず、人へ戻す。Dockerソケットへのアクセスはホスト上のroot相当の操作を許す（DooDではホストのソケットそのものに効く）ため、認可の変更として人の承認が要る。

- ソケットのパーミッションを広げる（`chmod 666` など）手順は案内しない。アクセスできる全プロセスへroot相当の操作を許してしまう
- 承認を得たうえで付けるなら、対象の利用者だけに限る（例: その利用者を `docker` グループへ加える `sudo usermod -aG docker <利用者>`。再ログインが要る）

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
