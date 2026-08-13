# NDF: frontmatter の圧縮と playwright 系の分割

## 関連リンク

- 棚卸台帳: [docs/specifications/ndf-skill-inventory.md](../docs/specifications/ndf-skill-inventory.md)
- frontmatter 規約: [plugins/ndf-shared/skills/README.md](../plugins/ndf-shared/skills/README.md)
- 直前のリリース: [issues/ndf-development-skills/](ndf-development-skills/)（v6.1.0 で Skill 29 → 34）

## モード

`architecture`。全 Skill の frontmatter に触れ、配布物の構成（プラグインの分割）を変えるため。

## 目的と非目的

達成したい状態:

- NDF が常時注入するコンテキストの取り分を、他プラグインと比べて妥当な水準まで下げる
- 追加のたびに初期一覧の予算を心配せずに済む余裕を作る

やらないこと:

- Skill の削除（起動実績の再測定が済んでいないため、この変更では判断しない）
- `description` の運用上限 300 文字の引き上げ・引き下げ
- Skill 本文（frontmatter 以外）の圧縮

## 前提

- 前提 1: 初期一覧の予算はプラグイン横断で共有される。実測（2026-08-13、この開発環境）では
  公式プラグイン 35 Skill = 7,903 文字 + NDF 30 Skill = 6,582 文字 = 14,485 文字がすべて
  一覧に載っており、`8,000` は「コンテキスト長が不明なときのフォールバック値」として働いている。
  したがって本変更の目的は「上限に収める」ことではなく「取り分を下げる」ことである
- 前提 2: `Triggers:` の列挙を廃止しても、用途文にトリガ語を残せば Codex / Kiro の暗黙起動は
  維持できる。**これは未検証**であり、Task 1 で実測してから全 Skill へ広げる
- 前提 3: `allowed-tools` は Agent Skills 仕様で experimental。Kiro は frontmatter 一覧に
  載せておらず解釈は保証されない（[規約](../plugins/ndf-shared/skills/README.md)）

## 現状の実測（2026-08-13、v6.1.0 時点）

| 項目 | NDF | superpowers | slack | notion |
| --- | ---: | ---: | ---: | ---: |
| Skill 数 | 34 | 28 | 13 | 4 |
| `description` 合計 | 8,044 | 3,722 | 5,219 | 1,029 |
| 1 個あたり平均 | 237 | **132** | 401 | 257 |
| frontmatter 合計 | **13,051** | 4,868 | 5,940 | 1,218 |

NDF のフィールド別内訳:

| フィールド | 合計 | 割合 | 初期一覧への影響 |
| --- | ---: | ---: | --- |
| `description` | 8,044 | 61.6% | **あり** |
| `allowed-tools` | 3,095 | 23.7% | なし |
| `name` | 719 | 5.5% | あり |
| `argument-hint` | 703 | 5.4% | なし |
| `paths` / `when_to_use` / その他 | 490 | 3.8% | なし |

`description` のうち `Triggers:` の列挙が 1,921 文字（claude 配布 30 個分の 29%）を占める。

## 受け入れ条件

- [ ] `description` の 1 個あたり平均が **170 文字以下**になる（現状 237）
- [ ] claude の初期一覧に載る NDF の合計が **5,000 文字以下**になる（現状 7,772）
- [ ] frontmatter 合計が **9,000 文字以下**になる（現状 13,017）
- [ ] `python3 scripts/check-skill-frontmatter.py` がエラー 0 / 警告 0
- [ ] 圧縮前に自動起動していた Skill が、圧縮後も同じ依頼文で起動する（Task 1 の実測手順）
- [ ] `/ndf:` で始まる既存コマンド名が、playwright 系 4 個を除いて変わっていない
- [ ] playwright 系 4 個は新プラグインから同じ Skill 名で起動できる
- [ ] 旧 `/ndf:playwright-*` の移行先が `ndf-policies` の対応表に載っている
- [ ] `bash scripts/build-runtime-plugins.sh --check` / `validate-runtime-plugins.sh` /
      `runtime-smoke-test.sh` が 3 ランタイムで成功する

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | 予算値（`FRONTMATTER_TOTAL_MAX`）を上げるだけ | 不採用 | 予算は自前の運用値で、上げても常時注入の実量は減らない |
| B | 圧縮のみ | 不採用 | 単価は下がるが、playwright 系 4 個が 1,181 文字を占める構造は残る |
| C | 分割のみ | 不採用 | 残り 26 個の単価 237 文字が高いままで、追加のたびに同じ問題が起きる |
| **D** | **圧縮 + playwright 分割** | **採用** | 単価と個数の両方に効く。分割は独立性の高い 4 個に限る |
| E | D に加えて起動 0 の Skill を削除 | 見送り | 起動数が v6.0.0 以前の測定。再測定を別途行ってから判断する |

## 用語

| 用語 | 意味 |
| --- | --- |
| 初期一覧 | ランタイムが起動時に読み込む Skill 一覧。`name` + `description` + ファイルパスからなる |
| 取り分 | 環境内の全プラグインの初期一覧合計に対する、NDF の占める割合 |
| 単価 | Skill 1 個あたりの `description` 文字数 |

## 不変条件

- 起動実績のある Skill のコマンド名を変えない（playwright 系はプラグイン接頭辞のみ変わり、
  Skill 名は変えない）
- 発動判定に必要な情報は `description` に置く（`when_to_use` へ逃がさない）
- モード判定の基準は `development-workflow` の 1 箇所にとどめる

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `/ndf:<name>` のコマンド名 | playwright 系 4 個がプラグイン移動 | **破壊的変更**。メジャーを上げて `ndf-policies` に対応表を 1 リリース分残す |
| Skill 名そのもの | 変えない | `/playwright-` まで打てば従来どおり候補に出る |
| `description` の文言 | 全 Skill で変わる | 発動条件は維持。実測で確認する |
| `allowed-tools` | 一部 Skill から削除 | Claude Code ではツール制限が外れる。読み取り専用を保ちたい Skill には残す |

## PR 分割計画

release branch: `release/skill-footprint`
base branch: `main`

| PR # | branch 名 | 概要 | 依存 | 並行可否 |
| --- | --- | --- | --- | --- |
| 1 | `feature/footprint-trigger-experiment` | トリガ廃止の書式を 3 Skill で試し、3 ランタイムで発動を実測。規約と検査を更新 | なし | ○ |
| 2 | `feature/footprint-compress-desc` | 全 Skill の `description` を新書式へ書き換え | 1 | × |
| 3 | `feature/footprint-allowed-tools` | `allowed-tools` の整理（残す条件を規約化） | なし | ○ |
| 4 | `feature/pwkit-plugin-scaffold` | 新プラグイン `playwright-kit` の骨組み（共通編集元・manifest・build/validate のプラグイン汎用化） | なし | ○ |
| 5 | `feature/pwkit-move-skills` | playwright 系 4 個を新プラグインへ移動。NDF の manifest から除去 | 2, 4 | × |
| 6 | `feature/pwkit-kiro-installer` | Kiro 向け installer と smoke test の対応 | 5 | × |
| 7 | `feature/footprint-finalize` | 対応表、各 README、予算値の再設定、version bump | 3, 6 | × |

PR 1 を先頭に置くのは、トリガ廃止が発動に効くかを確かめないまま 34 個へ広げると、退行に
気づけないためである。PR 4 を先に切るのは、プラグイン汎用化（`build-runtime-plugins.sh` /
`validate-runtime-plugins.sh` が `ndf-*` を前提にしている）が移動そのものより重いためである。

## タスク分解

### Task 1: トリガ廃止の書式を実測する（PR 1）

- **対象ファイル:** `plugins/ndf-shared/skills/{merged,pr,pr-tests}/SKILL.md`、
  `plugins/ndf-shared/skills/README.md`、`scripts/check-skill-frontmatter.py`
- **満たす受け入れ条件:** 圧縮後も同じ依頼文で起動する
- **変更内容:**
  - 書式を「`用途 + Use when 条件`（トリガ語は用途文に埋め込む）」へ変える
  - 自然文の依頼で 3 ランタイムとも同じ Skill が起動することを実測する。手順は
    [08-verification.md](ndf-development-skills/08-verification.md)「自然文からの発動の実測」に従う
  - 起動しなくなった場合は、その Skill だけ `Triggers:` を残す例外として規約へ書く
  - 検査（トリガ語の重複）は宣言トリガがない前提でも動くようにする

### Task 2: 全 Skill の `description` を書き換える（PR 2）

- **対象ファイル:** 全 `SKILL.md` の frontmatter
- **満たす受け入れ条件:** 平均 170 文字以下、初期一覧 5,000 文字以下
- **変更内容:** Task 1 で確定した書式へ揃える。用途とトリガ語を先頭 1 文に置く

### Task 3: `allowed-tools` を整理する（PR 3）

- **対象ファイル:** 全 `SKILL.md` の frontmatter、`plugins/ndf-shared/skills/README.md`
- **満たす受け入れ条件:** frontmatter 合計 9,000 文字以下
- **変更内容:**
  - 残す条件を規約化する（案: 破壊的操作を持たず読み取りに限定したい Skill にのみ付ける）
  - 条件に合わないものから削除する

### Task 4: 新プラグインの骨組み（PR 4）

- **対象ファイル:** `plugins/playwright-kit-shared/`（新規）、`.claude-plugin/marketplace.json`、
  `scripts/build-runtime-plugins.sh`、`scripts/validate-runtime-plugins.sh`、
  `scripts/check-skill-frontmatter.py`、`.github/workflows/runtime-plugin-validate.yml`
- **変更内容:**
  - `ndf-*` を前提にしている箇所をプラグイン名で回すよう一般化する
  - 検査スクリプトを複数の共通編集元に対して実行できるようにする
  - 予算の集計を「プラグイン単位」と「リポジトリ合計」の両方で出せるようにする

### Task 5: playwright 系の移動（PR 5）

- **対象ファイル:** `plugins/ndf-shared/skills/playwright-*`（移動）、manifest 3 種、
  新プラグインの manifest
- **変更内容:**
  - `playwright-planning` / `playwright-authoring` / `playwright-evidence` /
    `playwright-kit-ops` を新プラグインへ移す。**Skill 名は変えない**
  - `playwright-kit-ops` は実行環境（`pyproject.toml` / `uv.lock` / `tests/`）を伴う。
    `build-runtime-plugins.sh` の除外パターン（`.venv` / `__pycache__` 等）が効く配置を保つ
  - NDF 側からの参照（`pr-tests` などの本文）を新しい呼び名へ更新する

### Task 6: Kiro installer と動作確認テスト（PR 6）

- **対象ファイル:** 新プラグインの `install.sh`、`scripts/runtime-smoke-test.sh`
- **変更内容:** Kiro は配布物を symlink する方式のため、新プラグイン用の installer が要る

### Task 7: 仕上げ（PR 7）

- **対象ファイル:** `ndf-policies/SKILL.md`、各 README、`AGENTS.md`、`CLAUDE.md`、
  `plugin.json` 各種、`marketplace.json`、`docs/specifications/ndf-skill-inventory.md`、
  `scripts/check-skill-frontmatter.py`
- **変更内容:**
  - 旧 `/ndf:playwright-*` から新プラグインへの対応表を `ndf-policies` へ 1 リリース分載せる
  - 予算値（`FRONTMATTER_TOTAL_MAX`）を実測へ再設定する
  - メジャーバージョンを上げる（破壊的変更のため）

## 影響範囲

| 対象 | 影響 |
| --- | --- |
| 全 Skill の `description` | 文言が変わる。発動判定は維持する（実測で確認） |
| playwright 利用者 | プラグインの追加インストールが必要になる |
| 常時注入コンテキスト | NDF の取り分が 7,772 → 5,000 文字以下（見込み 3,600 前後） |
| 検査スクリプト | 複数プラグインを対象にできる形へ一般化 |
| 継続的インテグレーション | 既存 3 ワークフローが新プラグインも検査する |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| トリガ廃止で自動起動しなくなる | PR 1 で 3 Skill × 3 ランタイムを実測してから全体へ広げる。退行した Skill は例外として `Triggers:` を残す |
| `allowed-tools` 削除でツール制限が外れる | 読み取り専用を保ちたい Skill には残す。判断条件を規約へ明記する |
| プラグイン分割で playwright が使われなくなる | 各 README とリリースノートに導入手順を書き、対応表を 1 リリース分残す |
| 分割作業が想定より重い | PR 4 の一般化が終わらない場合、PR 5 以降を次のリリースへ送る（圧縮だけ先に出す） |
| `playwright-kit-ops` の実行環境がビルドに巻き込まれる | 現行の除外パターンが効くことを `--check` で確認する |

## 切り戻し手順

- 圧縮（PR 1〜3）: `description` の書き換えのみで、revert すれば元の発動条件へ戻る
- 分割（PR 4〜6）: 新プラグインを削除し、manifest へ playwright 系を戻せば復旧できる。
  利用者側は旧バージョンへ戻すことで従来どおり動く

## 完了の定義

- [ ] 受け入れ条件をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `architecture` モードの検証段階（限定的な検証 → 全体テスト → 静的解析 → 結合）を通す
- [ ] 3 ランタイムの動作確認テストが成功する
- [ ] 対応表と移行手順が各 README に載っている
