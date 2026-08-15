# 収束ループ共通層

`cross-review` と `cross-refactoring` が共有する部品を置く。両者は同じ骨組み
（状態ファイル + 起動 + 監視 + 収束判定）を持つため、実装を二重に持たない。

## 置いてよいもの・いけないもの

**収束ループ一般に必要なものだけを置く。** Skill 固有の処理を混ぜない。

| ファイル | 役割 |
| --- | --- |
| [monitor.py](monitor.py) | CLI プロセスの多軸監視。対象と命名規則を引数で受ける |
| [launch-cli.sh](launch-cli.sh) | claude / codex / gemini / kiro をランタイム名で分岐して背景起動する |
| [_gemini-env.sh](_gemini-env.sh) | gemini の信頼済みディレクトリ設定と設定ファイルの無害化 |
| [_tmpdir.sh](_tmpdir.sh) | 一時ディレクトリの解決。環境変数名とディレクトリ名を引数で受ける |
| [statefile.py](statefile.py) | 状態ファイルの読み書きと KEY=VALUE 出力 |
| [assignment.py](assignment.py) | ホスト判定、役割ごとの母集合の確定、担当の輪番 |
| [models.py](models.py) | `--model` の解析、フラグ生成、実測値の突き合わせ |
| [metrics.py](metrics.py) | 担当ごとの指標算出と報告の整形 |

固有として**置かないもの**の例:

- `cross-review`: 振動検知、Pull Request のローテーション、レビュー観点、修正の指示
- `cross-refactoring`: 提案のマージ、改善項目の管理、適用結果の検証、見送り処理

3 つ目の利用者が現れた時点で、独立したディレクトリへ移す。

## `cross-review` 配下に置く理由

配布物のビルドは Skill ディレクトリ単位でコピーするため、既存の仕組みだけで
両ランタイムへ届く。共通層のための Skill を新設すると、利用者が呼ぶものでは
ないのに初期一覧へ載って発動候補に混ざる（`disable-model-invocation` を付けても
名前は残る）。

## 移行の段取り

**先に `cross-refactoring` を `lib/` の上へ実装し、動くことを確かめてから
`cross-review` を載せ替える。** 逆順にすると、実績のある `cross-review` を
未検証の共通層へ載せることになる。

現時点の状況は次のとおり。

| 部品 | `cross-refactoring` | `cross-review` |
| --- | --- | --- |
| `monitor.py` | `lib/` を直接使う | `scripts/monitor.py` が移設シムとして `lib/` を読む |
| `_tmpdir.sh` | `lib/` を直接使う | `scripts/_tmpdir.sh` が固有の名前を束ねて `lib/` を読む |
| `_gemini-env.sh` | `lib/launch-cli.sh` 経由 | `scripts/launch-gemini.sh` が読む |
| `launch-cli.sh` | 使う | 未移行（作業単位 15 で移す） |
| `assignment.py` / `models.py` / `metrics.py` | 使う | 未移行（作業単位 14〜16 で使う） |
| `statefile.py` | 使う | 未移行（作業単位 13 の残り） |

各段階の完了条件は共通して「**`cross-review` の既存テストを 1 つも変更せずに通す**」。
