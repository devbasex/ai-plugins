# 推論コンテナ規約と train↔serve 契約書テンプレ

`SKILL.md` の「inference/ の規約」「TRAIN_SPEC.md」の詳細。推論コンテナ (SageMaker 前提) を書く /
レビューするとき、および TRAIN_SPEC.md を起こすときに読む。

## 目次

1. SageMaker 推論ハンドラ
2. Dockerfile (build context = 版ルート)
3. .dockerignore の置き場所
4. model.tar.gz の中身
5. テストの path 解決 (conftest)
6. TRAIN_SPEC.md の章立て

## 1. SageMaker 推論ハンドラ

`vN/inference/inference.py` に標準 4 関数を実装する。

| 関数 | 役割 |
|---|---|
| `model_fn(model_dir)` | `model_dir` からモデル本体 + 学習時統計 (列順/dtype/中央値/版文字列) を読み込む。クラス順 (`classes_`) が期待通りかを fail-fast で検証する |
| `input_fn(body, content_type)` | 生 payload を dict にパースする (変換ロジックは持たない) |
| `predict_fn(data, model)` | `features.to_model_input(data)` → `features.base_features(df, …)` → `predict_proba`。**版が採用していない派生特徴量は呼ばない**。列順は学習時に保存した列順 JSON を唯一の正とする |
| `output_fn(pred, accept)` | クラス確率 + ラベル + 版文字列を返す |

- 特徴量化は `features.to_model_input` → `base_features` に閉じる。呼び出し側 (APIゲートウェイ) は
  生データ (統計・レスポンス項目・申込属性) を**転送するだけ**にする。
- 版文字列 (`model_version`) は学習成果物の JSON から**動的読込**し、欠落時のみ既定値にフォールバックする。
  「学習スクリプトが版文字列を書き出していない」と再学習時に既定値へ化けるので、書き出しを受け入れ基準に含める。

## 2. Dockerfile (build context = 版ルート)

```dockerfile
# build context は版ルート vN/ に固定する。
#   cd <project>/vN && docker build -f inference/Dockerfile -t <img>:vN .
FROM python:3.13-slim
# … 依存インストール (MMS/sagemaker-inference を使うなら JRE と setuptools<81 に注意) …
WORKDIR /opt/ml/code
COPY inference/requirements.txt /opt/ml/code/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
# 版内 SSoT: context 直下の features.py を取り込む (../ は不可)
COPY features.py /opt/ml/code/features.py
COPY inference/inference.py /opt/ml/code/inference.py
# … handler_service.py / dockerd-entrypoint.py 等 …
```

- `COPY ../features.py` は Docker 仕様上**不可** (context 外参照)。context を版ルートにすることで
  `features.py` も `inference/` 配下も同一 context 内で参照できる。
- `inference/features.py` を実体として commit しない。誤コミット検知として CI に
  `find inference/ -name features.py` のガードを置くとよい。

## 3. .dockerignore の置き場所 (重要)

`.dockerignore` は **build context のルート (= `vN/.dockerignore`)** に置く。
BuildKit は「Dockerfile 隣の `<dockerfile>.dockerignore`」か「context ルートの `.dockerignore`」しか読まないため、
`inference/.dockerignore` に置いても**機能しない** (context が肥大化するだけ)。

```
# vN/.dockerignore — 取り込むのは features.py と inference/ の推論コード + requirements のみ
train/
*.md
*.csv
*.parquet
*.cbm
__pycache__/
inference/tests/
export_model_to_s3.py
```

## 4. model.tar.gz の中身

SageMaker の `model_data` (tar.gz) には**モデル成果物のみ**を入れる:

- モデル本体 (`*.cbm` 等) + 推論に必要な学習時統計 (列順 JSON / dtype JSON / 統計 JSON)。
- `inference.py` は**含めない** — ECR イメージ側 (`/opt/ml/code`、`SAGEMAKER_PROGRAM` /
  `SAGEMAKER_SUBMIT_DIRECTORY`) に同梱する。
- パッケージングは専用スクリプト (`export_model_to_s3.py`) で行い、必要ファイルが揃わなければ fail-fast。
  手動 `tar` は禁止 (同期ミスの温床)。`--dry-run` で中身検証だけできるようにしておく。

## 5. テストの path 解決 (conftest)

ローカルとコンテナで import 文を共通化するため、`vN/inference/tests/conftest.py` で path を相対計算する:

```python
_HERE  = os.path.dirname(__file__)      # vN/inference/tests
_INFER = os.path.dirname(_HERE)         # vN/inference
_V    = os.path.dirname(_INFER)         # vN  (features.py 同梱)
for p in (_V, _INFER, _HERE):
    sys.path.insert(0, p)
RESULTS_DIR = os.path.join(_V, "train", "results")
DATASET_CSV = os.path.join(_V, "train", "data", "dataset.csv")
```

- コンテナでは `features.py` と `inference.py` が同じ `/opt/ml/code` に並ぶため、同じ
  `from features import …` が成立する。
- 学習データが存在するときだけ走る train/serve 一致テストを入れ、存在時に pass することを受け入れ基準にする。

## 6. TRAIN_SPEC.md の章立て

版ルート `vN/TRAIN_SPEC.md` に置く。性能考察 (`*_report.md`) や経営層向け概要 (`executive_summary.md`) とは
目的を分け、**train↔serve 契約に限定**する。リンクで相互参照し実体を二重化しない。

```
# <Model> vN TRAIN_SPEC — train↔serve 契約書
0. train↔serve 全体像          … mermaid (学習→成果物→推論の流れ)
1. データソースと対象母集団     … 生成SQL/期間/母集団定義/リーク除外
2. ターゲット定義 (クラス符号化) … 学習整数 ↔ 推論ラベルの対応、クラス順
3. 特徴量契約                   … to_model_input のマッピング、列順の唯一の正
4. 欠損補完規約                 … 0補完/NA補完/中央値補完の対象と実装位置
5. 学習成果物                   … tar.gz 同梱ファイルと生成元
6. train/serve skew の防衛線     … 回帰テストの内容と「再学習で再生成するもの」
7. 再学習手順                   … コマンド列 + 変更時の同期チェックリスト
```

- 「特徴量列を変えたら何を再生成・再ビルドするか」をチェックリストで明示する
  (マッピング辞書・成果物・テスト golden・コンテナ再ビルド)。
- 行番号参照を使う場合、ファイル内容が不変ならパスのみ更新し行番号は維持する。
