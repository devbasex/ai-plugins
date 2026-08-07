# 一気通貫実行

用語は [01-overview.md](01-overview.md) を参照。

設計が確定した実装計画を、リリース直前まで自動で進める仕組みを用意する。

## ランタイム組み込みの `/goal` を土台にする

Claude Code と Codex は、完了条件を設定してターンをまたいで作業を継続させる `/goal` を組み込みで持つ。**この継続ループを Skill として作り直さない。** NDF が担うのは、条件文の生成と、ループが回す工程の中身である。

| ランタイム | `/goal` | 動作 |
| --- | --- | --- |
| Claude Code | あり（v2.1.139 以降） | 各ターン終了後に小型モデルが条件の充足を判定し、未充足なら次のターンを開始する。セッション単位の Stop フックとして実装されている |
| Codex | あり | セッションに目的を永続化し、継続・状況確認・完了検証を行う。`/goal pause` `/goal resume` `/goal clear` を持つ |
| Kiro | **なし** | `/goal` は `unrecognized subcommand` で拒否される（kiro-cli 2.16.1 で確認） |

条件文の上限は Claude Code / Codex とも 4,000 文字である。

すでに `/goal /ndf:cross-review 14256` や `/goal /issue-plan-strategy <計画ファイル> 実装開始 ...` のように、組み込みの `/goal` から NDF の Skill を駆動する使い方が 8 回記録されている。この使い方を正式な手順として整備する。

## 名前

新設する Skill の名前は **`execute-plan`** とする。`goal` は Claude Code と Codex の組み込みコマンド名であり、同名を避ける。

## 責務の境界

`execute-plan` は**新しい手順もループも定義しない**。次の 2 つだけを担う。

1. 計画ファイルを読み、その内容に応じた完了条件の文面を組み立てる
2. 各段階で呼ぶ既存 Skill を並べる

| 段階 | 呼び出す Skill |
| --- | --- |
| モード判定 | `development-workflow` |
| リリースブランチと個別プルリクエストの作成 | `issue-plan-strategy` |
| 実装 | `tdd-cycle` |
| 検証 | `quality-gates` |
| レビュー | `cross-review` |
| 指摘対応 | `fix` |

```mermaid
flowchart TD
    A["/goal <条件> を設定"] --> B[execute-plan が計画を読む]
    B --> C[モード判定]
    C --> D[リリースブランチと個別プルリクエスト作成]
    D --> E[失敗するテスト → 最小実装 → 整理]
    E --> F[完了の定義に沿った検証]
    F --> G[相互レビュー]
    G --> H{両者が承認したか}
    H -->|未承認| I[指摘対応]
    I --> G
    H -->|承認| J[リリースブランチへマージ]
    J --> K{残りの計画があるか}
    K -->|ある| E
    K -->|ない| L[リリース用プルリクエスト本文の最終化]
    L --> M([条件充足。ループ終了。下書きのまま引き渡す])
    M -.ターンごとに判定.-> N[/goal の評価器]
    N -.未充足なら次ターン.-> E
```

## 完了条件の書き方

Claude Code の評価器は**ツールを呼ばず、会話に現れた内容だけで判定する**。したがって条件は、エージェント自身の出力で証明できる形で書く必要がある。これは `quality-gates` が要求する証跡（コマンド、終了コード、実行時刻）と同じ性質であり、両者を組み合わせて設計する。

条件に含める要素:

| 要素 | 例 |
| --- | --- |
| 測定可能な終了状態 | 計画中の全プルリクエストがリリースブランチへマージ済み |
| 証明方法 | `gh pr list --base release/<ID> --state open` の出力が空 |
| 変えてはならない制約 | リリース用プルリクエストを下書きのままにする |
| 上限 | 40 ターンを超えたら停止して状況を報告する |

`execute-plan` は計画ファイルから上記を組み立て、`/goal` に渡す文面として提示する。

## 停止させたい境界

組み込みの `/goal` では「停止」は条件の充足で表現する。利用者の判断が要る境界は、条件文の側へ書き込む。

| 停止させたい状況 | 条件文への書き方 |
| --- | --- |
| リリース用プルリクエストをレビュー依頼可能にする直前 | 下書きのままであることを完了状態の一部にする |
| 計画に受け入れ条件がない | `execute-plan` が起動時に検出し、`requirements-design` へ差し戻して `/goal` を設定させない |
| データベース移行・認証・公開インタフェースの破壊的変更を検出 | 検出したら報告して停止する旨を制約に含める |
| 相互レビューが 3 巡で収束しない | ターン上限を条件に含める |
| 想定外のファイルに変更が及んだ | 変更してはならないパスを制約に列挙する |

`/goal` は権限を変更しない。無人で走らせるには自動承認モードとの併用が要る。破壊的操作を含むため、**併用するかは利用者が判断する**前提で手順を書く。

## Kiro での扱い

Kiro には継続ループがないため、`execute-plan` は段階ごとに利用者の続行指示を要する手順書として動作する。この差は `plugins/ndf-kiro/README.md` に明記する。

## 設定

```yaml
name: execute-plan
description: "Read a finalized implementation plan and drive it through coding, review, and merge up to just before release. Emits a completion condition for the runtime's built-in /goal loop. Use when the design is settled and the plan file is ready (計画を実装して / リリース直前まで進めて)."
argument-hint: "[計画ファイルパス | 計画 ID]"
arguments: plan
disable-model-invocation: true   # 破壊的操作を含むため明示指示専用
effort: high
```

`context: fork` は使わない。Claude Code の `/goal` はセッション単位の Stop フックとして動くため、分離した実行単位では評価器が働かない。

## 事前チェックと確認モード

起動時に計画ファイルの存在と受け入れ条件の記載を確認する。なければ `requirements-design` へ差し戻し、`/goal` を設定せずに停止する。

`--dry-run` を指定した場合は、組み立てた完了条件と、作成予定のブランチおよびプルリクエストの一覧だけを出力する。

## 中断と再開

継続ループの状態はランタイムが持つ。Claude Code は `--resume` で条件を復元する（ターン数と使用量の集計はリセットされる）。Codex は `/goal pause` と `/goal resume` を持つ。

NDF 側で重複して状態管理を持たない。再開時は `git branch -a` と `gh pr list` から現在地を判断し、既存のブランチとプルリクエストを重複作成しない手順を本文に書く。
