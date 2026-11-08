# #2623 の設計: データ構造と入出力の契約

設計文書の本体は [issue-2623-design.md](issue-2623-design.md) にある。 `I` で始まる番号は本体の不変条件を指す。

## データ構造

### `delivery` の行に足す欄

どれも任意で、今の宣言はそのまま読める。形が違うときは宣言を読めない問題として扱い、止める側へ倒す（I4）。

| 欄 | 型 | 空を許すか | 意味 |
| --- | --- | --- | --- |
| `manual_reason` | 文字列 | 許す。空白だけは無いものとして扱う | 手動反映の理由。`kind: manual` の行でだけ読む。無ければ「CD を置かないと決めていない」を表す。手順は今の `trigger` が持つ |
| `result` | オブジェクト | 許す | 結果の読み方。`kind: auto` の行でだけ読む。無ければ結果は未確定になる |
| `result.workflow` | 文字列 | `command` と排他 | GitHub Actions のワークフローのファイル名 |
| `result.command` | 文字列 | `workflow` と排他 | シェルで実行するコマンド。`{revision}` を 40 桁のリビジョンへ置き換える。終了コード 0 は成功、75 は待つ、1 は失敗、それ以外は未確定 |
| `result.timeout_seconds` | 整数 | 許す | 待ちの上限。無ければ 1800 秒 |
| `publishes_release` | 真偽値 | 許す | 公開の担い手。`kind: auto`・`versioned: true` の行でだけ読む。真なら、この行の CD がリリースタグと GitHub Release を作る。無ければ偽で、NDF の工程が作る |

### 宣言の例

```json
[
  {"target": "ndf プラグイン（Claude Code・Codex）", "kind": "auto", "channel": "production", "versioned": true,
   "trigger": ".github/workflows/release-cd.yml（main へのマージでタグと GitHub Release を作る）",
   "publishes_release": true,
   "result": {"workflow": "release-cd.yml"}},
  {"target": "AWS（CDK スタック）", "kind": "manual", "versioned": false, "trigger": "cdk deploy",
   "manual_reason": "インフラの変更は人が差分を確かめてから反映する"}
]
```

### 機能とデータの対応

| 機能 | `delivery` の行 | 結果のファイル | リリース記録 | タグと Release |
| --- | --- | --- | --- | --- |
| F2・F5 | R | — | — | — |
| F6 | C / U（利用者）。解析は R して引き継ぐ | — | — | — |
| F7 | R | C | C | R |
| F8・F9 | R | — | — | C |

結果のファイルは計画の状態ディレクトリの `work/cd-result.json` に置き、作り直してよい作業用の値である。過去の結果はリリース記録のコメントが追記の形で残す。宣言の変更の履歴は git が持つため、上書きで失うものは無い。既存の宣言と記録の移行は無い。形の検査は `project-decl.py` の検証と `check-ndf-contracts.py` が行う。

## 入出力の契約

### リリースの経路（`delivery.routes`）

変わるのは版を持つ自動の行の 2 行だけで、ほかの判定（`judge_target`・`dev_channel`）は変えない。

| 宣言 | 今の経路 | 変更後の経路 |
| --- | --- | --- |
| `kind: auto`・`versioned: true`・本番チャネルの行 | `manual`（手で行うステージ） | `merge`・`promote`。`versioned: false` と同じ規則で決める |
| `kind: auto`・`versioned: true`・ベースブランチの行 | `manual` | `merge`・`merged-by-check` |
| `kind: auto`・ブランチが無いか別のブランチ | `manual` | 変えない |
| `kind: manual` | `manual` | 変えない。`note` は `trigger` を持つ |
| `release.form` がある | `template` | 変えない（`delivery` を読まない） |

### `delivery.cd_guidance(decl) -> CdGuidance | None`

上から順に最初に当たった行で決まる。ファイルを読まず、呼ぶたびに宣言から作る。

| 条件 | 戻り値 |
| --- | --- |
| `decl.problems` がある | `None`（読めない理由は既存の経路の判定が示す） |
| 行が無い・不明 | 案内 1 件。何が無いかは「`delivery` の宣言」 |
| 理由の無い手動の行がある | 案内 1 件。`rows` にその行の `target` だけを並べる |
| 残り（`delivery: []`・自動の行だけ・すべて理由あり） | `None` |

案内の 4 つの中身: 何が無いか・CD の設定の手順の参照先（`release` の `references/cd-setup.md`）・`delivery` への記録の例・手動反映の理由を記録する方法。

### コマンド

| 名前 | 入力 | 出力 | 失敗の形 | 互換性 |
| --- | --- | --- | --- | --- |
| `release-steps.py cd-guidance` | `--root` | 結果 JSON。`items` に `kind: cd-guidance` を 0 か 1 件 | 宣言を読めなければ終了コード 3。案内の有無では常に 0 | 追加 |
| `release-steps.py cd-result` | `--base <本番チャネル>`、`--head <ブランチ>`（任意。無ければ `origin/<本番チャネル>` の先端を読む）、`--out <ファイル>` | 結果のファイルと結果 JSON。`metrics.cd_result` は `success` / `failure` / `undetermined` / `none`（本番チャネルへ自動で届く行が無い） | 値にかかわらず 0。読み取りの失敗は `undetermined` にする | 追加 |
| `release-steps.py publish` | `--root`、`--revision <SHA>`、`--plugin <名前>`（任意。無ければ宣言の `release.plugin`） | 結果 JSON。`items` に `tag`・`release` の各 1 件（`created` / `pushed` / `exists`） | 接尾辞あり・別のコミットのタグ・版を読めない・`gh` の失敗は終了コード 1 | 追加 |
| `release-steps.py record` | 今の引数に `--cd-result <ファイル>`（任意）と `--merged`（`--base`・`--prs` と組）を足す | 結果のファイルの値を `CD の結果:` の行へ書く。`--merged` は最後の `--prs` の Pull Request へ書き、版は `<本番チャネル> <短い SHA>` | 値が `failure` なら段階を「反映失敗」で書いた後に終了コード 5。プランは止まる | 引数なしは今と同じ |
| `release-steps.py release --channel prod` | 変わらない | 本番チャネルのマージへ承認の担い手を `--gate-approved` で渡す（承認資料の記録の担い手。`--approved-sha` で受けたときは `user`）。`publishes_by_cd` が真なら公開物を作らず、`metrics.tag` は `null`、`metrics.publisher` は `cd` | 今と同じ | 宣言が手動なら今と同じ |
| `sprint-state.py loop sprint` | `--dev` が任意になる | 経路が版数を要らなければ `lap.versions` は空 | 経路が版数を要るのに `--dev` が無ければ断る | 指定があれば今と同じ |

`publish` の版は `--revision` のコミットの `plugin.json` から、プラグインの名前は `.ndf/supervise.json` の `release.plugin` から読む。題は `<プラグイン> v<版>`、本文は変更履歴の版の節で、`--latest` で作る。

### `publishes_by_cd(decl) -> bool`

本番チャネルへのマージで自動で本番系へ届く行（`reaches_by_merge`）のうち、`versioned: true` かつ `publishes_release: true` の行があるときに真を返す。別の成果物を届ける自動の行はこの判定に数えない。宣言を読めないときは、呼び出し元の `release_decl` がこの判定より先に止める。

### CD のワークフロー

```yaml
name: Release CD
on:
  push:
    branches: [main]
permissions:
  contents: read
concurrency:
  group: release-cd
  cancel-in-progress: false
jobs:
  publish:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
          fetch-tags: true
      - uses: actions/setup-python@v7
        with:
          python-version: "3.13"
      - name: Publish the tag and the GitHub Release
        env:
          GH_TOKEN: ${{ github.token }}
          GIT_COMMITTER_NAME: github-actions[bot]
          GIT_COMMITTER_EMAIL: 41898282+github-actions[bot]@users.noreply.github.com
        run: python3 plugins/ndf/scripts/release-steps.py publish --root . --revision "$GITHUB_SHA"
```

### リリース記録

```markdown
## リリース記録

段階: 本番（2026-11-07 10:00 に承認）
版: main 1a2b3c4 → main 5d6e7f8（昇格）
スプリント: PR #2701 / #2702
CD の結果: 成功（リビジョン 5d6e7f8… https://github.com/.../runs/1）
```

`CD の結果:` の行は `成功` / `失敗` / `未確定` で始まる。失敗のときは段階の行を `段階: 反映失敗（<理由>）` にする。この値は `released()` が閉じてよい段階と読まないため、スプリントは開いたまま残る。読む側は最後の記録を使うため、CD を再実行して `cd-result` と `record` を打ち直すと、成功の記録で閉じられる。CD の行が無いリポジトリの記録にこの行は無い。
