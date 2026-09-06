# プラグイン共通層

`plugins/ndf/scripts/lib/` は、**どの Skill にも属さない部品**を置く。プラグイン
ルート直下にあるため、配る Skill を絞る配布先でも残る。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| プラグインルート | 配布した先で `scripts/` と `skills/` が並ぶディレクトリ |
| 配布の基準 | `plugins/ndf/manifests/*-skills.txt`。配る Skill の名前だけを持つ |
| 収束ループ | `cross-review` / `cross-refactoring` が回す「起動 → 監視 → 判定」の繰り返し |

## 置いてあるもの

| ファイル | 役割 | 読む側 |
| --- | --- | --- |
| [worktree-common.sh](worktree-common.sh) | 作業ツリーの判定・台帳・書き込み先の推定 | `worktree` / hook |
| [projects-common.sh](projects-common.sh) | GitHub Projects の盤面への記録 | `development-workflow` |
| [lock-common.sh](lock-common.sh) | 排他の取得と解放（#293） | 上の 2 つと `development-workflow` |
| [monitor.py](monitor.py) | 別プロセスの多軸監視。対象と命名規則を引数で受ける | 収束ループの 2 つ |
| [launch-cli.sh](launch-cli.sh) | claude / codex / agy / kiro をランタイム名で分岐して背景起動する | 同上 |
| [_tmpdir.sh](_tmpdir.sh) | 一時ディレクトリの解決。環境変数名とディレクトリ名を引数で受ける | 同上 |
| [statefile.py](statefile.py) | 状態ファイルの読み書きと KEY=VALUE 出力 | 同上 |
| [assignment.py](assignment.py) | ホスト判定、役割ごとの母集合の確定、担当の輪番 | 同上 |
| [models.py](models.py) | `--model` の解析、フラグ生成、実測値の突き合わせ | 同上 |
| [metrics.py](metrics.py) | 担当ごとの指標算出と報告の整形 | 同上 |
| [post_queue.py](post_queue.py) | 上限のときに投稿を積む待ち行列と、上限の見分け（#291） | 同上 |
| [closing-issues.sh](closing-issues.sh) | Pull Request の本文から、閉じる語が指す issue を取り出す（#424） | `merged` / `development-workflow` の hook |

## 置いてよいもの・いけないもの

**2 つ以上の読み手が使う部品だけを置く。** Skill 固有の処理を混ぜない。

固有として**置かないもの**の例:

- `cross-review`: 振動検知、Pull Request のローテーション、レビュー観点、修正の指示
- `cross-refactoring`: 提案のマージ、改善項目の管理、適用結果の検証、見送り処理

## プラグインルート直下に置く理由

**配る Skill を絞る配布先がある。** 配布の基準に無い Skill が配布した先に残るかを、
実在する欠落（`official-skills-autoloader`）で測った。

| ランタイム | 基準に無い Skill | 隣の Skill から相対で届くか |
| --- | --- | --- |
| Claude Code | 残る | 届く |
| Codex | 残る | 届く |
| Kiro CLI | `.kiro/skills/` からは消える | 届く（`..` が主ディレクトリの実体へ抜ける） |
| agy | 消える | **届かない** |

Skill の下に共通層を置くと、その Skill を配らない配布先で読み込みが失敗する。
プラグインルートの `scripts/` は配布の基準の対象ではないため、4 ランタイムすべてへ
届く（基準は Skill の名前だけを持ち、`scripts` という語を含まない）。

共通層のための Skill を新設する形は採らない。利用者が呼ぶものではないのに初期一覧へ
載り、発動の候補に混ざる（`disable-model-invocation` を付けても名前は残る）。

## ここを指す書き方

**物理的な解決を求める側と避ける側が、シェルと Python で入れ替わる。** Kiro CLI が
`.kiro/skills/<名前>` を symlink にするためである。シェルの `cd` は `..` を字句で
畳んで symlink の手前へ戻り、Python の `parents[]` は `.resolve()` を通さないと
`.kiro` で止まる。

| 読み込む側の位置 | 言語 | 書き方 |
| --- | --- | --- |
| `<プラグインルート>/skills/<名前>/scripts/` | シェル | `"$DIR/../../../scripts/lib/<名前>"`（文字列のまま渡す） |
| 同上 | Python | `pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"` |
| `<プラグインルート>/skills/<名前>/scripts/lib/` | シェル | `"$DIR/../../../../scripts/lib/<名前>"` |

`DIR` は `$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)` で求めた読み込む側の
ディレクトリである。**`cd` で登った結果を `pwd` で取らない。**

## 収束ループの 2 つの Skill から見た現状

`cross-review` は既存の呼び出しパスを保つため、`scripts/` 側に 2 つのシムを残す。

| 部品 | `cross-refactoring` | `cross-review` |
| --- | --- | --- |
| `monitor.py` | 共通層を直接使う | `scripts/monitor.py` がシムとして共通層を読む |
| `_tmpdir.sh` | 共通層を直接使う | `scripts/_tmpdir.sh` が固有の名前を束ねて共通層を読む |
| `launch-cli.sh` | 使う | `launch-agy.sh` が委譲する。`launch-codex.sh` は未移行 |
| `assignment.py` / `models.py` / `metrics.py` | 使う | 未移行 |
| `statefile.py` | 使う | 未移行 |
| `post_queue.py` | 未移行 | 使う |
