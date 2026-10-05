---
name: google-workspace
description: "Export, download, and upload Google Drive and Docs files with the gws CLI, installing it with consent and guiding login. Use when a file must be fetched from or published to Drive（Google Drive・Google Docs・Google Workspace・共有リンク・gws）."
allowed-tools:
  - Read
  - Bash(python3 *)
  - Bash(gws *)
---

# Google Workspace（gws）

Google Workspace の操作は、すべて gws（Google Workspace CLI、npm の `@googleworkspace/cli`）で行う。
gws は Google の公式サポート外の CLI で、版は 0.x である。

## 流れ

1. **確かめる**: 確認のスクリプトを打ち、終了コードと JSON の `metrics.state` で次の手を選ぶ（`$SKILL_DIR` はこの Skill のディレクトリ）

   ```bash
   python3 "$SKILL_DIR/scripts/gws-check.py"
   ```

   | 終了コード | `metrics.state` | 次の手 |
   | --- | --- | --- |
   | 0 | `authenticated` | 4. の操作へ進む |
   | 10 | `missing` | 2. 導入の同意を求める |
   | 11 | `unauthenticated` | 3. 認証を案内する |
   | 3 | `uninstallable` | npm（Node.js）が無く導入できない。理由を示して止まる |
   | 1・2 | — | `summary` と `items` を示して止まる |

2. **同意のうえ入れる**（`missing` のとき）: `presentation_path` の承認資料（取得元・打つコマンド `npm install -g @googleworkspace/cli`・入る先・戻し方）を利用者に示し、同意を得てから打ち直す

   ```bash
   python3 "$SKILL_DIR/scripts/gws-check.py" --install
   ```

   - 利用者が導入を含めて頼んだ（「gws を入れて」）ときは、その依頼を同意とみなす。そのときも承認資料は示す
   - 暗黙の依頼（「Drive から取って」）では、明示の同意を待つ
   - 断られたとき・人がいない起動（`claude -p`、`pace: fast` / `auto` を含む）では入れずに止まる。gws の無い代わりの経路は持たない
   - 失敗（終了コード 1）は出力を示して止まる。`sudo` で打ち直さない。`next` が `PATH` への追加を案内したら、それを利用者に伝えて止まる

   入った後はスクリプトが同じ起動の中で確かめ直し、その状態を返す（多くは `unauthenticated`）。

3. **認証を案内する**（`unauthenticated` のとき）: ブラウザでの承認が要るため、エージェントは `gws auth login` を打たない。利用者に次を頼み、終えたと伝えられたら 1. から打ち直す

   - Claude Code: `! gws auth login`
   - ほかのランタイム: 別の端末で `gws auth login`
   - `metrics.client_config_exists` が `false` のときは、その前に `gws auth setup`（gcloud が要る）か OAuth クライアントの `client_secret.json` の用意を頼む（`next` の文に従う）

   トークンは gws が自分の設定ディレクトリに置く。エージェントはトークンと `client_secret.json` を読まない・書かない。

4. **操作する**（`authenticated` のとき）: 下の対応表のコマンドを打つ。終了コードが 0 でなければ、gws の出力を示して止まる

## 操作の対応表

| 操作 | gws のコマンド |
| --- | --- |
| Doc・Slides・Sheets のエクスポート | `gws drive files export --params '{"fileId":"<ID>","mimeType":"<MIME>"}' --output <ファイル>` |
| ファイルのダウンロード | `gws drive files get --params '{"fileId":"<ID>","alt":"media"}' --output <ファイル>` |
| アップロード（既定は非公開） | `gws drive +upload <ファイル>`（フォルダへは `--parent <フォルダの ID>`、名前は `--name <名前>`） |
| 公開リンクの付与（利用者が公開を明示したときだけ） | `gws drive permissions create --params '{"fileId":"<ID>"}' --json '{"type":"anyone","role":"reader"}'` |

- **アップロードの既定は非公開である。** 公開リンクは、利用者が公開を明示したときだけ、アップロードの応答の `id` に付ける。付与が失敗したら、ファイルは非公開のまま残ると伝える
- 打つ前に要求の形だけを確かめたいときは `--dry-run` を付ける
- 対応表の外の操作（Sheets の編集・Gmail・Calendar など）は `gws <サービス> --help` で引数を確かめてから打つ

### ファイルの ID

URL の `/d/` と次の `/` の間が ID である。

```text
https://docs.google.com/document/d/<ID>/edit
```

### エクスポートの MIME

| 元 | `mimeType` |
| --- | --- |
| Docs | `text/plain`・`text/html`・`application/pdf`・`application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| Sheets | `text/csv`・`application/pdf`・`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| Slides | `application/pdf`・`application/vnd.openxmlformats-officedocument.presentationml.presentation` |
