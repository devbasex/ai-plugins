# cross-refactoring 不具合修正の引継ぎ

NDF v8.5.4 時点の状態と、次に触る人が知っておくことをまとめる。
6 回の実機試行で見つかった不具合は 20 件で、すべて対応済みである。
6 回目は提案から Draft 解除までを 1 度も中断せずに通した。

経緯は次の 6 つにある。

| 回 | 記録 | 到達点 |
| --- | --- | --- |
| 1 | [trial-report](issue-113-cross-refactoring-trial-report.md) | 適用フェーズまで。不具合 9 件 |
| 2 | [retrial](issue-113-cross-refactoring-retrial.md) | 公開の責務を進行側へ一本化 |
| 3 | [re-retrial](issue-113-cross-refactoring-re-retrial.md) | 生成物の同期で停止。不具合 4 件 |
| 4 | [4th-trial-report](issue-113-cross-refactoring-4th-trial-report.md) | レビューまで。収束ループが終わらない経路を発見 |
| 5 | [5th-trial-report](issue-113-cross-refactoring-5th-trial-report.md) | 修正フェーズと再レビューまで。投稿の不具合 3 件 |
| 6 | [6th-trial-report](issue-113-cross-refactoring-6th-trial-report.md) | 全工程を完走。新しい不具合なし |

## 編集対象

編集元だけを直し、配布物は生成する。

```
plugins/ndf-shared/skills/
├── cross-refactoring/
│   ├── scripts/refactor.py            # 検証・取り消し・状態記録・判定
│   ├── scripts/launch-cli.sh          # フェーズごとのプロンプト組み立て
│   ├── scripts/prepare-worktrees.sh   # 作業ディレクトリと手順書の配置
│   ├── prompts/                       # 提案 / 適用 / レビュー / 修正のプロンプト
│   ├── tests/                         # 現状固定テスト
│   └── docs/                          # 手順書本文（挙動を変えたら追従する）
└── cross-review/scripts/lib/          # 収束ループ共通層
```

```bash
bash scripts/build-runtime-plugins.sh   # 配布物を生成する
```

## 直近 4 版で入った仕組み

いずれも実機試行で踏んだ経路への対処である。手を入れるときは前提として押さえる。

| 版 | 入れたもの | 押さえどころ |
| --- | --- | --- |
| v8.5.1 | 修正フェーズの起点を 1 箇所で記録する | 変更要求の出口は 2 つある。片方だけに記録を置くと `merge-fix` が範囲を確定できず、修正ラウンドが進まないまま往復し続ける |
| v8.5.2 | 自分の Pull Request では投稿の event を `COMMENT` へ倒す | 判定と投稿の event は別物。判定は `APPROVE` / `REQUEST_CHANGES` のまま結果ファイルへ残す |
| v8.5.3 | 投稿されたことを GitHub 側で確かめる | 「無い」と「取得できない」を区別する。判定済みの照合鍵には投稿の確認結果も混ぜる |
| v8.5.4 | 差分予算の倍率を手法別にする | 抽出系は固定費が乗って見積の 2 倍をわずかに超える。広げるのは抽出系だけで、全体を広げると範囲外の変更まで通る |

**判定済みの照合鍵に外部要因を入れ忘れない。** `judge-review` は同じ入力で叩き直しても
同じ出口を返すよう、結果ファイルの内容から鍵を作って記録する。GitHub 側の状態のように
結果ファイルの外で決まる要素を鍵へ入れずに出口を分岐させると、状態が変わっても再判定されず
進行が止まる。v8.5.3 の実装でこの経路を 1 度作り、レビューで見つけて直した。

## 再検証の手順

```bash
# 着手前テスト（実測 502 件成功 / 26.2 秒）
uv run --with pytest python -m pytest \
  plugins/ndf-shared/skills/cross-refactoring/tests \
  plugins/ndf-shared/skills/cross-review/tests -q

# 実機
/ndf:cross-refactoring <PR番号> \
  --scope plugins/ndf-shared/skills/cross-refactoring/scripts \
          plugins/ndf-shared/skills/cross-refactoring/tests \
          plugins/ndf-shared/skills/cross-review/scripts/lib \
          plugins/ndf-shared/skills/cross-review/tests \
  --model kiro=claude-sonnet-5 \
  --sync-command "bash scripts/build-runtime-plugins.sh" \
  --baseline-test "<上のテストコマンド>" \
  --max-outer-rounds 3
```

実行前に確認すること。

| 確認 | 理由 |
| --- | --- |
| プラグインキャッシュの版 | `claude plugin update ndf@ai-plugins` で最新にする。キャッシュが古いと直したはずの経路を踏む |
| 参加する CLI のログイン | `init` が確認して中断する。kiro は `kiro-cli login` がブラウザ認証のため、利用者に実行してもらう |
| 進行を駆動する作業ディレクトリのブランチ | 対象ブランチを掴んでいると作業ディレクトリを作れない。`main` へ戻してから始める |
| `--scope` に現状固定テストの置き場所を含める | 範囲は検証にも効く。含めないとテストを触った項目が範囲外として失敗する |
| `--sync-command` を渡す | 生成物を持つリポジトリでは、渡さないと pre-push の検査であらゆる push が落ちる |

## 検証済みの範囲

6 回目までに実機で通ったもの。

- 提案の並列実行、重複排除、採否の判定
- 適用の項目単位のコミットと、差分予算・範囲・テストによる検証
- 項目単位の取り消しと積み直し（同一ファイルの隣接行を触る項目どうしはラウンド全件へ退避する）
- 生成物の同期を進行側のコミットとして積み、進行側だけが push すること
- レビュー担当 2 者の並列実行、指摘の投稿、承認判定
- 変更要求から修正フェーズへ入り、修正ラウンドが進むこと
- レビュー結果が 2 回続けて欠けたときの中断
- 実装担当の輪番（ラウンド単位）。6 回目は 3 者すべてが 1 ラウンドずつ担当した
- 集計値の出力
- ラウンド上限による終了と、収束後の `/ndf:cross-review`（Step 7）
- Draft の解除と完了報告（Step 8）

## 未検証の範囲

一度も到達していない。次の再検証ではここまで通す。

| 対象 | 内容 |
| --- | --- |
| 提案の収束 | 新しい提案が出なくなって `advance` が繰り返しを終えるところ。重複率による判定も未到達。到達させるには `--max-outer-rounds` を上げるか `--scope` を狭める |
| 修正フェーズ | 6 回目は 3 ラウンドとも初回で承認され、修正そのものに入っていない。5 回目までに修正ラウンドが進むところまでは確認済み |
| 修正ラウンドの上限 | `should-abandon` から `abandon-items` へ進み、指摘が残る項目だけを取り消すところ |
| 投稿の失敗の記録 | 投稿が失敗したときに `post_error` 付きの結果ファイルを残す経路（v8.5.3）。6 回目は投稿がすべて成功した |
| 他者の Pull Request | 6 回とも自分の Pull Request を対象にした。投稿の event を倒さない経路は実機で未確認（v8.5.4 で指示の組み立てからプロンプトまでは現状固定テストで固めた） |

## 運用上の傾向

再検証で繰り返し現れているもの。仕様の問題ではないが、結果の読み方に影響する。

| 事象 | 内容 |
| --- | --- |
| 差分予算の超過 | `long_method` の抽出は見積より膨らみやすい。**v8.5.4 で抽出系の倍率を 3 へ広げた**効果は 6 回目で確かめられ、採用 12 件のうち 5 件が見積の 2.08〜2.91 倍だった。一方で予算を超える外し方も 2 件出ている（抽出系で 3.31 倍、`flatten_conditional` で 3.01 倍）。倍率をさらに広げると範囲外の変更を通すため、次に触るなら見積の精度を上げる側 |
| kiro の既定モデル | `auto` は実際に選ばれたモデルを取得できず、そのラウンドは集計から分離される。モデルを比べるなら `--model kiro=<name>` を指定する。**v8.5.4 から `init` が着手前に警告する**（進行は止めない） |
| 1 回の実行内の比較 | 改善項目の難易度が揃わないため、ランタイムの優劣を読む材料にはならない |
| 提案の偏り | この対象範囲では提案の大半が抽出系の手法になる。6 回目は 14 件中 13 件が抽出系だった |
| プラグインキャッシュの版 | セッション開始後に `claude plugin update` を実行しても、その会話の Skill 一覧は古い版を指したままになる。`PLUGIN_ROOT` に新しい版のパスを渡せば、スクリプトとプロンプトは新しい版が使われる |
