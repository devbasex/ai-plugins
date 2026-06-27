---
name: ml-model-structure
description: "MLモデル開発の標準ディレクトリ構造を適用する。"
when_to_use: "機械学習モデルの新規構築・再学習・推論API/コンテナ開発・モデルのバージョン管理/並行運用を行うとき。analysis/ 配下に学習スクリプトや推論コードを配置する設計判断が必要なとき。Triggers: 'モデル構築', 'モデル再学習', 'モデルのバージョン管理', '推論コンテナ', '推論API', 'SageMaker', 'feature SSoT', 'train/serve skew', 'analysis ディレクトリ', 'champion challenger', '並行運用'"
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
---

# ML モデル構築・API開発の標準構造

機械学習モデルの学習コードと推論API(コンテナ)を、**版ごとに自己完結する構造**で配置するための基準。
連番スクリプト (`01_…` `10_…`) をプロジェクト直下にフラットに並べると「どれがデプロイ済みの正規コードか」
「どれが旧版・ボツ実験か」が判別できなくなる。本構造はこれを **版 (version) 単位の実体ディレクトリ** で解決し、
将来 v2/v3 を champion/challenger・shadow で**並行運用**しても破綻しないようにする。

## 設計の核 (なぜこの形か)

- **版ごとに自己完結**: 各版 `vN/` は独自の `features.py` / `train/` / `inference/` を持つ。
  並行運用では特徴量ロジック自体が版ごとに進化するため、全版が単一の共通ライブラリを共有する設計は破綻する。
  版を跨いだ共有 (`shared/`) や `current -> vN` の symlink は**作らない**。
- **版内 feature SSoT**: 同一版の学習 (`train/`)・推論 (`inference/`)・テストが **`vN/features.py` だけ** を参照する
  (import / Docker COPY)。train/serve skew (学習時と推論時で特徴量計算がずれる事故) は **版内で** 保証する。
  版間は独立でよい — v3 が別ロジックでも v2 に影響しない。
- **「コードの並行」と「デプロイの並行」は別レイヤー**。デプロイ並行は推論基盤側 (SageMaker production/shadow
  variants・multi-model endpoint 等) で解決する。本構造が担うのは**コード層の独立**のみ。
- **本番版の所在は symlink でなく明示管理**。並行運用では「今の本番」が複数になり得るため、
  本番版はエンドポイント名・環境変数・README の表で管理する。

## 目標ディレクトリ構造

モデルプロジェクト 1 つ (例: `analysis/<yyyymmdd>_<name>/`) を版で分割する。

```
<model-project>/                     # 例: analysis/20260505_oac-prediction/
├── v1/                              # 退役版: read-only アーカイブ
│   ├── train/                       #   旧学習コード (features.py 非依存・自己完結)
│   ├── *_report.md  *.csv           #   旧レポート・成果物
│   └── README.md                    #   「read-only。再実行非想定」を明記
├── v2/                              # 現役版: 自己完結の実体 (symlink なし)
│   ├── features.py                  #   ★版内 feature SSoT (学習・推論が共有)
│   ├── train/
│   │   ├── 01_build_dataset.py      #   canonical: データ生成 (→ train/data/)
│   │   ├── 10_train_model.py        #   canonical: 学習 → train/results/ に成果物
│   │   ├── experiments/             #   採用根拠系の実験 (+ README)。CI非対象・参照専用
│   │   ├── data/                    #   生成データ (gitignore 対象)
│   │   └── results/                 #   モデル成果物 (.cbm / *.json 等)
│   ├── inference/                   #   推論コンテナ一式 (詳細は references)
│   │   ├── Dockerfile               #   build context = vN/、COPY features.py
│   │   ├── inference.py …           #   ハンドラ + tests/
│   │   └── README.md
│   ├── .dockerignore                #   ★build context ルート (= vN/) に置く
│   ├── export_model_to_s3.py        #   成果物を model.tar.gz 化して配布
│   ├── TRAIN_SPEC.md                #   train↔serve 契約書
│   └── README.md  *_report.md  executive_summary.md
└── lab/                             # 版非依存・不採用の探索 (+ README)。「ボツにしたアイデア集」
```

> 図表は ASCII ツリーのみ例外的に許可 (本スキルは `ndf:markdown-writing` 準拠)。フロー図は mermaid を使う。

## 版内 SSoT と train/serve 一致

```mermaid
flowchart LR
    subgraph TR["学習 (vN/train/)"]
        A["01_build_dataset.py"] --> B["train/data/dataset.csv"]
        B --> C["features.base_features"]
        C --> D["10_train_model.py 学習"]
        D --> E["train/results/ 成果物"]
    end
    subgraph SV["推論 (vN/inference/)"]
        P["生 payload"] --> F["features.to_model_input"]
        F --> G["features.base_features"]
        G --> H["predict_proba"]
    end
    C -. 同一関数を共有 .- G
    E -. model.tar.gz 同梱 .-> H
```

- 特徴量化ロジックは **`vN/features.py` に閉じ込め、学習も推論も同じ関数を import** する。
  呼び出し側 (APIゲートウェイ等) は生データを転送するだけにし、列名マッピングや前処理を二重実装しない。
- 学習時のみ必要な統計 (中央値補完値・列順・dtype 等) は学習スクリプトが成果物 (JSON) に書き出し、
  推論時に読み込んで適用する。1行推論で `df.median()` が壊れる等の事故を防ぐ。
- **回帰テスト (主防衛線)**: 「学習データの1行」と「同じ素データを `to_model_input` に通した出力」が
  全学習列で一致することを検証する。列順・クラス順・補完値も版内テストで固定する。

## train/ の規約

- **canonical (本線)**: デプロイ済みモデルを再現する最小経路。`01_build_dataset.py` → `10_train_model.py`
  → `export_model_to_s3.py`。版ディレクトリで版が自明なため、ファイル名から `_v2` 等の冗長な版接尾辞は外す
  (`10_v2_model.py` → `10_train_model.py`)。
- **train/experiments/ (採用根拠系)**: その版の意思決定・解釈に寄与した実験 (ハイパラ探索・不均衡対策比較・
  SHAP・リーク検証等) を版同梱する。連番は履歴安定のため維持。CI 非対象・参照専用を README に明記。
- **train/data/・train/results/**: データは gitignore、成果物 (モデル本体・列順/dtype/統計 JSON) は追跡する。
- **絶対パスと相対解決の使い分け**:
  - 出力先 `OUTPUT_DIR` は `…/<project>/vN/train` の**絶対パス**で固定 (symlink を使わず曖昧さを消す)。
  - `features.py` の解決は `__file__` からの**相対計算**にする (例: train 直下は `parent.parent`、
    `train/experiments/` 配下は `parents[2]` が版ルート `vN/`)。階層を変えても import が壊れにくい。

## inference/ の規約 (要点)

- SageMaker 標準ハンドラ (`model_fn` / `input_fn` / `predict_fn` / `output_fn`) を実装する。
- **build context は版ルート `vN/`** に固定し、Dockerfile は `COPY features.py …` で版内 SSoT を取り込む
  (`COPY ../…` は Docker 仕様上不可)。`inference/features.py` を**実体ファイルとして置かない**
  (build 時に COPY するのみ)。
- **`.dockerignore` は build context ルート (= `vN/.dockerignore`) に置く**。BuildKit が読むのは context ルートの
  `.dockerignore` か `<dockerfile>.dockerignore` のみで、`inference/.dockerignore` という名前では機能しない。
  `train/` 等を除外して context を軽くする。
- `model.tar.gz` には**モデル成果物のみ**を入れ、`inference.py` は ECR イメージ側 (`/opt/ml/code`) に同梱する。

詳細 (ハンドラ雛形・Dockerfile・テスト・TRAIN_SPEC の章立て) は
[`references/inference-and-contract.md`](references/inference-and-contract.md) を参照。

## lab/ と v1/ の扱い

- **`lab/`**: その版が**採用しなかった**探索 (派生特徴量・別アルゴリズム等)。特定の版に属さないため
  版ディレクトリの外 (プロジェクト直下) に置く。README に「不採用の結論」を残す。
- **`v1/` (旧版)**: 退役版は read-only アーカイブとして集約する。当時の `OUTPUT_DIR` 絶対パスは
  **書き換えない** (再現性の歴史的記録)。README に「read-only・再実行非想定・新版で全面置換済み」を明記。

## TRAIN_SPEC.md (train↔serve 契約書)

版ルートに置き、「学習で何がどう作られ、推論がそれをどう使うか」を 1 ファイルに集約する。
推論コンテナ/エンドポイント/API結線の実装者が、学習コードを読まずに契約を把握できることが目的。
章立てテンプレは [`references/inference-and-contract.md`](references/inference-and-contract.md)。

## 新しい版を作る・既存を再編する手順

1. **新版 `vN/` を v(N-1) と対等な実体として作る** (既存版に触れない)。`features.py` / `train/` / `inference/`
   を独自に持たせ、独立してビルド・配布・エンドポイント作成できる状態にする。旧版は並行稼働を続けられる。
2. **既存をこの構造へ再編するときは全移動を `git mv`** で行い履歴を保つ。改名 (`10_v2_model`→`10_train_model`) も `git mv`。
3. 生成データ/成果物の gitignore を新パスに追従させる。
4. パス参照 (絶対 `OUTPUT_DIR`・Docker build context・ドキュメントの相対リンク) を新構造に更新する。
   ディレクトリ階層を変えたら**相対リンクの深さ**もずれるので grep で洗い出す。

## 受け入れ基準 (再編・新規ともに)

- [ ] 版内テストが**全 pass** (特に train/serve 一致の回帰テスト)。
- [ ] 全 `.py` が `py_compile` を通る。
- [ ] 全 `.py` から `features.py` の **import が解決できる** (py_compile では検出されない相対解決の崩れを潰す。
  例: `python -c "import inference.inference"` / pytest collection が通る)。
- [ ] `cd vN && docker build -f inference/Dockerfile …` が**通る** (build smoke)。
  Docker 不可環境では Dockerfile の `COPY` パス実在チェック (`features.py` / `inference/*` の存在 grep) で代替する。
- [ ] `export_model_to_s3.py --dry-run` 等で成果物が新パスから取得できる。
- [ ] canonical 学習スクリプトが**単独完走可** (他の実験スクリプトを import しない)。
- [ ] ドキュメントに **dead link なし** (リポジトリ全体 grep)。
- [ ] 移動は `git mv` で**履歴保持**。

> これらは「構造を変えたら壊れていないことを機械的に確かめる」ための最小セット。
> ファイルを動かした直後に必ず回す。
