# CD を設定して方式を記録する

CD の案内が出たら読む。**環境へ届ける手順は CD が持ち、NDF はマージ・リリース承認ゲート・結果の確かめを受け持つ。**
案内は工程を止めない。次のどちらかを行えば、次のデプロイから案内は出ない。

| 選ぶこと | 向いているリポジトリ | すること |
| --- | --- | --- |
| CD を設定する | マージを起点に環境へ届けられる | 下の「手順」の 3 つ |
| あえて CD を置かない | 人が差分を確かめてから手で反映する（インフラの定義など） | 下の「CD を置かない理由を記録する」 |

## 手順

使う CI は問わない。決めるのは次の 3 つである。

### 1. 起点を決める

CD を動かすブランチを決める。本番チャネルへのマージで動く CD は、マージがリリースそのものになる。NDF はそのマージの前に
リリース承認ゲートを置く（`form-service.md`「反映の起動によって工程の形が変わる」）。

**例（GitHub Actions）:**

```yaml
on:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - run: ./deploy.sh
```

### 2. 結果の読み方を決める

NDF はマージの後に CD の状態を読み、成功・失敗・未確定のどれかをリリース記録へ残す。読み方は 2 通りある。

| 読み方 | `result` の欄 | どう読むか |
| --- | --- | --- |
| GitHub Actions のワークフロー | `{"workflow": "<ファイル名>"}` | リビジョンの実行を `gh run list` で読む。完了して `success` なら成功、ほかの結論なら失敗、実行が無いか進行中なら待つ |
| コマンド | `{"command": "<シェルのコマンド>"}` | `{revision}` を 40 桁のリビジョンへ置き換えて実行する。終了コード 0 は成功、75 は待つ、1 は失敗、それ以外は未確定 |

待ちの上限は `timeout_seconds`（既定は 1800 秒）で変えられる。1 回の読み取りは全行の合計で 7200 秒を上限にし、上限に当たった行は未確定になる。`result` が無いと結果は常に未確定になり、リリース後テストが
本番の状態を直接確かめる。

### 3. `delivery` へ記録する

`.ndf/project.json` の `delivery` へ行を書く。

```json
{"delivery": [
  {"target": "本番（ECS）", "kind": "auto", "channel": "production", "versioned": false,
   "trigger": ".github/workflows/deploy.yml（main へのマージ）",
   "result": {"workflow": "deploy.yml"}}
]}
```

| 欄 | 意味 |
| --- | --- |
| `kind` | `auto` がマージで届く CD を表す |
| `channel` | `production`（本番チャネル）か `base`（ベースブランチ）。ほかのブランチで動くなら `branch` に名前を書く |
| `versioned` | 届けるものが版数を持つか |
| `trigger` | CD の名前。読み手が場所を探せる語で書く |
| `result` | 結果の読み方 |
| `publishes_release` | `versioned: true` の行で真にすると、リリースタグと GitHub Release をこの行の CD が作る。無ければ NDF の工程が作る |

## CD を置かない理由を記録する

手動の行の `manual_reason` に理由を、`trigger` に手順を書く。これが「あえて CD を置かない」という判断の記録になる。

```json
{"delivery": [
  {"target": "AWS（CDK スタック）", "kind": "manual", "versioned": false, "trigger": "cdk deploy",
   "manual_reason": "インフラの変更は人が差分を確かめてから反映する"}
]}
```

工程は手で行うステージへ進み、`trigger` の手順で反映する。
