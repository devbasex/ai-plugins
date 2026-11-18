# リリースを利用者のリポジトリから起動する

NDF（ndf-next）のリリースの書く操作（リリースブランチの組み立て・マージ・タグ）は、利用者のリポジトリの GitHub Actions の
workflow が行う。会話のセッションは承認資料を組んで見せ、回答を書くだけを持つ。

## 例: Web サービスのリポジトリで 1.2.3 を出す

ベースブランチ `dev` で開発し本番ブランチ `prod` へ出す Web サービスのリポジトリ（`package.json` の `version` が 1.2.2）を例にする。
設定 `.ndf/project.json` の `branch_strategy` は `branches` が `{"base": "dev", "production": "prod"}` で、`release` は次のとおりとする。

```json
{
  "form": "service",
  "version_store": {"kind": "json", "path": "package.json", "pointer": "/version"},
  "tag": "v{version}",
  "github_release": true,
  "cd": {"workflow": "ndf-release.yml"}
}
```

1. テンプレート [ndf-release.yml](../assets/workflows/ndf-release.yml) を `.github/workflows/ndf-release.yml` へ写し、
   `NDF_TAG`・本番ブランチの名前・`YOUR_RELEASE_TOKEN` を書き換える（下の「テンプレートの置き方」）
2. 会話のセッションで承認資料を組む: `ndf release-steps materials --base dev --production prod --channel production --out materials.md`
3. Actions の画面か `gh workflow run ndf-release.yml -f step=assemble` で組み立てる。`release/v1.2.3` のブランチと `prod` 宛ての
   リリースの PR ができ、`package.json` の `version` が 1.2.3 になる
4. 承認の後に `gh workflow run ndf-release.yml -f step=merge -f pr=<リリースの PR の番号>` でマージする
5. `prod` への push でテンプレートが `publish` を流し、タグ `v1.2.3` と GitHub Release を作る

## テンプレートの置き方

テンプレートは、NDF のリポジトリにある再利用可能なワークフロー（`.github/workflows/ndf-release-reusable.yml`）を `uses:` で
呼ぶだけの短いファイルである。ジョブの中身を写さないため、NDF のバージョンを上げるときは参照を書き換えるだけでよい。
設定 `.ndf/worktree.json` の `remote` が `origin` でなければ、再利用可能なワークフローが取り出したリポジトリのリモートをその名前に変える。

| 書き換える所 | 値 |
| --- | --- |
| `NDF_TAG`（`uses:` の `@` の後と `ndf_ref`） | 動かす NDF のタグ。2 か所に同じ値を書く。ブランチの名前でなくタグで固定する |
| `on.push.branches` の値 | 本番ブランチの名前（`branch_strategy.branches.production`）。`publish` はこのブランチに入ったコミットだけを公開する |
| `YOUR_RELEASE_TOKEN` | リリースのトークンを入れた秘密の名前（「秘密の名前の決め方」） |

テンプレートの入力（`workflow_dispatch`）:

| 入力 | 使うステップ | 意味 |
| --- | --- | --- |
| `step` | すべて | `assemble`・`merge`・`publish` |
| `channel`・`level` | `assemble` | チャネル（`production`・`dev`）と上げる幅（`patch`・`minor`・`major`） |
| `units` | `assemble` | `release_branch` の設定で集めるスプリントブランチ（空白区切り） |
| `pr` | `merge` | マージするリリースの PR の番号 |

## 要る権限

テンプレートの `permissions` は workflow の既定を `contents: read` にし、ジョブに要るものだけを足す。再利用可能なワークフローの
各ジョブは、その中でさらに絞る。

| ステップ | 権限 |
| --- | --- |
| `assemble` | `contents: write`（リリースブランチを送る）・`pull-requests: write`（リリースの PR）・`checks: read`・`statuses: read` |
| `merge` | `contents: write`・`pull-requests: write`・`checks: read`・`statuses: write`（承認の判定を書く） |
| `publish` | `contents: write`（タグと GitHub Release）・`pull-requests: read`（保存先 `tag` で公開するバージョンをリリースの PR から読む） |

`release_token` を渡さず `github.token` で `assemble` を流すときは、リポジトリの Settings → Actions → General の
Workflow permissions で **Allow GitHub Actions to create and approve pull requests** を有効にする。既定（個人のリポジトリ。
組織のリポジトリは組織の設定を受け継ぐ）では `pull-requests: write` があっても PR を作れず、リリースブランチだけが残る。

トークンを秘密で渡すときは、そのトークンにも同じ権限（Fine-grained token なら Contents・Pull requests・Commit statuses の
書き込みと Checks の読み取り）を付ける。

## 秘密の名前の決め方

再利用可能なワークフローは秘密を `release_token` の 1 つで受け、名前を決め打たない。呼び出し側のテンプレートで、
自分のリポジトリの秘密を割り当てる。

```yaml
    secrets:
      release_token: ${{ secrets.ACME_RELEASE_TOKEN }}
```

- 名前はリポジトリの決まりで決めてよい（組織の秘密を使うなら組織の名前の規則に合わせる）
- 渡すトークンは、リポジトリに write 以上の権限を持つ利用者に紐づくもの（その利用者の Fine-grained token など）にする。
  `assemble` と `merge` を同じ `release_token` で通すため、`merge` が本番ブランチへ入れる前に確かめる書き手（write 以上の
  権限を持つ利用者）に当たらない GitHub App のインストールトークンは使えない
- 渡さないと、`assemble`・`publish` は `github.token` で動く。`github.token` が作ったリリースの PR の CI は承認待ちで止まり、
  実行の記録に警告が出る。リポジトリに書き込み権限のある人が、PR の画面の **Approve workflows to run** で起動する
- タグの push や GitHub Release の作成で別のワークフロー（デプロイ・ストアへの提出など）を起動する構成では、`publish` にも
  `release_token` を渡す。`github.token` が作ったタグと GitHub Release のイベントは、新しいワークフローの実行を作らない
  （`workflow_dispatch` と `repository_dispatch` を除く。[GitHub の説明](https://docs.github.com/en/actions/concepts/security/github_token#when-github_token-triggers-workflow-runs)）。
  渡さないと、タグと Release だけが作られて後のデプロイが起動しない
- `merge` は秘密が無いと本番ブランチへ入れる前に止まる。`github.token` が書いたリリース記録は、読む側が信用しないためである
- `assemble` が流す `.ndf/release.json` の手順へは、このトークンを渡さない（環境から消し、作業場所の `.git/config` にも残さない）

## 会話のセッションとの分け方

| 場 | 持つサブコマンド |
| --- | --- |
| 会話のセッション | `materials`（承認資料を組む）・`present`（承認資料を見せる）・`answer`（回答を書く） |
| workflow | `assemble`・`merge`（と `record`）・`publish` |

## ほかの CI から呼ぶ

GitHub Actions 以外の CI のテンプレートは無い。同じ並びのサブコマンドを、NDF の `bin/ndf` で順に呼ぶ。

```bash
ndf release-steps assemble --root . --channel production --level patch --base dev --production prod
ndf release-steps merge --root . --pr <リリースの PR の番号> --budget 3600
ndf release-steps record --root . --pr <リリースの PR の番号> --production prod --production-sha <本番へ入れたコミット>
ndf release-steps publish --root . --revision <本番ブランチへ入ったコミット>
```

`release_branch` の設定では `assemble` と `materials` に `--units <スプリントブランチ>...` を、承認の対象が `production_pr` の設定では
`materials` に `--pr <本番の PR の番号>` を渡す。引数と停止理由は [release-steps.md](release-steps.md) の
「設定したリリースの形で組むサブコマンド」にある。
