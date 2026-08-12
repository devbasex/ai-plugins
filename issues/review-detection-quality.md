# review の検出品質を上げてクロスレビューの往復を減らす

## 関連リンク

- 実測の出典: v5.0.0 の Skill 棚卸（release PR [#66](https://github.com/devbasex/ai-plugins/pull/66)）で回した 17 本の cross-review
- 関連 issue: [#83](https://github.com/devbasex/ai-plugins/issues/83)（`ndf:review` が自然文で起動しない件。本プランは発動ではなく検出品質を扱う）
- 比較対象: Claude Code 組み込みの `code-review`

## 概要

`ndf:review` に、**指摘を投稿する前に落とす仕組み**を入れる。具体的には次の 3 つ。

1. 指摘ごとに「なぜ問題か」の根拠を必須にする（バグ系は再現条件、文書系は矛盾する対象）
2. 投稿前に各指摘を反証する自己検証パスを置き、反証できたものは投稿しない
3. 一度却下された論点を次ラウンドのレビューへ渡し、**新しい根拠がない限り再提出させない**

あわせて `cross-review` が検証結果を記録し、往復回数と却下率を継続的に測れるようにする。

## 問題・背景

### 誤指摘が PR に載ってから消えている

現在の `ndf:review` は、指摘を組み立てたらそのまま GitHub へ投稿する。投稿前の検証がない。
誤指摘は後段の `/ndf:fix` が「重要度ラベルを鵜呑みにせず独自に再判定する」ことで弾いているが、
**その時点では既に PR 上にインラインコメントとして載っており、Resolve 操作も必要になる**。

`/ndf:fix` の戻り値には `rejected`（bot 指摘が不適切で修正しなかったもの）という区分が
あり、これが恒常的に発生している前提の設計になっている。

### 実測

v5.0.0 の棚卸で 17 本の PR に cross-review を回した結果。

| 指標 | 値 |
| --- | ---: |
| PR 数 | 17 |
| 合計ラウンド数 | 70 |
| 1 本あたり平均 | 4.1 ラウンド |
| 最大 | 10 ラウンド（[#69](https://github.com/devbasex/ai-plugins/pull/69) / [#75](https://github.com/devbasex/ai-plugins/pull/75)） |
| 4 ラウンド以上かかった PR | 6 本（#67 #68 #69 #70 #75 #80） |
| 1〜2 ラウンドで収束した PR | 8 本 |

1 ラウンドは codex と gemini の並列レビュー + 修正サブエージェント 1 本で、実測で 3〜10 分。
**平均 4.1 ラウンドは、1 PR あたり 12〜41 分をレビューの往復に使っている**ことを意味する
（3 分 × 4.1 ≒ 12 分、10 分 × 4.1 ≒ 41 分）。

往復が伸びた PR では、同じ論点が繰り返し提出される事象も起きた。#69 では「version bump を
この PR でやるべき」という同趣旨の指摘が round 1〜5 で 5 回連続して出て、いずれも
「対応主体は Task 0-10」として却下している。#70 でも同じ論点が 3 回出た。

### 組み込み `code-review` が持っていて `ndf:review` が持たない仕組み

組み込みの本文は読めない（ハーネス提供でファイルとして存在しない）が、`ReportFindings`
ツールのスキーマから次が分かる。

| 仕組み | 内容 | `ndf:review` の現状 |
| --- | --- | --- |
| `failure_scenario` | **必須項目**。「具体的な入力・状態 → 誤った出力/クラッシュ」を書かせる | なし。`[重要度 / カテゴリ] 修正提案` の 1 文で完結する規約 |
| `verdict` | `CONFIRMED` / `PLAUSIBLE`。検証パスが走ったときに付く | なし。投稿前の検証がない |
| `category` | `correctness` / `simplification` / `efficiency` / `test-coverage` の機械可読な分類 | 観点リストはあるが分類として構造化していない |

本プランは上の 2 つ（`failure_scenario` と `verdict`）を取り込む。`category` の構造化は
GitHub のインラインコメントが本文しか持てない以上、効果が薄いため対象外とする。

## 修正対象

- `plugins/ndf-shared/skills/review/SKILL.md`
- `plugins/ndf-shared/skills/fix/SKILL.md`
- `plugins/ndf-shared/skills/cross-review/SKILL.md`
- `plugins/ndf-shared/skills/cross-review/docs/01-state-and-review.md`
- `plugins/ndf-shared/skills/cross-review/scripts/state.py`
- `plugins/ndf-shared/skills/cross-review/scripts/launch-codex.sh`
- `plugins/ndf-shared/skills/cross-review/scripts/launch-gemini.sh`
- `plugins/ndf-shared/skills/cross-review/tests/`（既存 pytest の更新）
- `docs/specifications/ndf-skill-inventory.md`（効果測定の記録先）

`launch-codex.sh` / `launch-gemini.sh` は落としてはならない。**cross-review 経由の実レビューが
実際に使う書式とスキーマはこの 2 本に埋め込まれている**（`launch-codex.sh` の
「インラインコメントの書式」節が `[重要度 / カテゴリ]` プレフィックスを規定し、同じく
result.json のキー一覧を `{event, posted_as, comments_count, review_url, by_severity}` に
固定している。`launch-gemini.sh` も同じ構造）。`review/SKILL.md` だけ直しても、
cross-review 経由のレビューは旧書式のまま動き続ける。

編集は共通編集元の `plugins/ndf-shared/` に対して行い、3 ランタイムへは
`bash scripts/build-runtime-plugins.sh` で反映する。

## タスク分解

### Task 1: 指摘の根拠を必須にする

- **対象ファイル:** `plugins/ndf-shared/skills/review/SKILL.md`、
  `plugins/ndf-shared/skills/cross-review/scripts/launch-codex.sh`、
  `plugins/ndf-shared/skills/cross-review/scripts/launch-gemini.sh`
- **変更内容:**
  - インラインコメント本文の書式を、`[重要度 / カテゴリ]` の 1 文だけから、
    **指摘 + 根拠**の 2 部構成へ変える
  - 根拠の形はカテゴリで分ける。**一律に再現条件を求めない**

    | カテゴリ | 必須の根拠 | 例 |
    | --- | --- | --- |
    | correctness / security / performance | **再現条件**: 具体的な入力・状態 → 誤った出力・挙動 | `再現: main に checkout した状態で手順 7 を実行 → 元ブランチの未コミット変更が main へ展開される` |
    | 整合性 / 正確性（文書・設定） | **矛盾する対象**: どのファイルのどの記述と食い違うか | `矛盾: plugins/ndf-shared/manifests/claude-skills.txt に qa-security-scan が無い` |
    | 可読性 / 言語慣用性 | **代替案**: どう書くか | 既存どおり |

  - 根拠を書けない指摘は **投稿しない**。書けないなら「気になるが確証がない」であり、
    Resolve 義務を伴うインラインコメントにする価値がない
  - 上記を外部 AI へ渡すプロンプトのテンプレートにも反映する。実体は
    `launch-codex.sh` の「インラインコメントの書式」節と `launch-gemini.sh` の対応箇所で、
    **両方を同時に直さないと codex / gemini で書式が食い違う**

  この分け方が要る理由は実測にある。v5.0.0 の棚卸はドキュメント変更が大半で、
  再現条件を一律必須にすると **有効だった指摘の多くが投稿できなくなる**。
  実際に価値のあった指摘には両方の型が含まれていた。

  - 再現条件型: `merged` が `main` に checkout したまま `stash pop` し、作業ブランチの
    未コミット変更を `main` へ展開する（#70）
  - 矛盾指摘型: `plugin.json` の `skills` 配列に manifest 追加分の 2 個が入っておらず、
    Claude Code から使えない（#76）

### Task 2: 投稿前の自己検証パスを入れる

- **対象ファイル:** `plugins/ndf-shared/skills/review/SKILL.md`、
  `plugins/ndf-shared/skills/cross-review/docs/01-state-and-review.md`、
  `plugins/ndf-shared/skills/cross-review/scripts/launch-codex.sh`、
  `plugins/ndf-shared/skills/cross-review/scripts/launch-gemini.sh`
- **変更内容:**
  - ペイロードを組み立てたあと、投稿の前に **各指摘を反証する**手順を必須化する
  - 反証の問いを指摘の型ごとに定める

    | 型 | 反証の問い |
    | --- | --- |
    | 再現条件型 | その入力・状態は実際に到達可能か。既存のコードが先に弾いていないか |
    | 矛盾指摘型 | 参照先を実際に開いて、本当に食い違っているか。既に別 PR で解消済みでないか |
    | 可読性型 | プロジェクトの既存コードがその書き方を採っていないか（慣例なら指摘しない） |

  - 判定を `confirmed` / `plausible` の 2 値で持ち、**`plausible` は投稿しない**。
    ただし件数だけは結果サマリへ残す（検証パスが効いているかを測るため）
  - 結果サマリに `by_verdict: {"confirmed": N, "plausible": M}` を追加する。
    **書き出し先は 2 系統あり、両方を直す**

    | 呼び出し経路 | result.json のパス | 規定している場所 |
    | --- | --- | --- |
    | `/ndf:review <PR> codex\|gemini` の直接委譲 | `/tmp/<agent>-review-pr<番号>-result.json` | `review/SKILL.md` |
    | `cross-review` 経由 | `$TMP_DIR/<agent>-review-pr<PR>-result.json` | `01-state-and-review.md`、`launch-*.sh` |

    `$TMP_DIR` は `CROSS_REVIEW_TMP_DIR` が無ければ `<worktree>/.cross_review/` で、
    `state.py` の `_resolve_tmp_dir()` もここを読む。**`/tmp` 側だけ直すと
    `by_verdict` は state.json に一切入らず、Task 4 の計測が常に 0 になる**
  - `--focus` の指定があるときも検証は省略しない
  - **全指摘が `plausible` になった場合の `event` を定義する。`APPROVE` とする。**
    intent を `REQUEST_CHANGES` のまま残すと、`state.py judge` の `is_pass()` が
    pass を返さないため `cross-review` が continue し、しかし投稿された指摘が 0 件なので
    `/ndf:fix` は修正コミットを作れず、差分が変わらないまま `--max-rounds`（既定 12）
    まで空回りする。結果サマリには `by_verdict` で plausible 件数が残るので、
    「指摘はあったが全部反証された」ことは追跡できる

  **PR の範囲外を理由に却下される指摘**（#69 の version bump など）はこの検証では
  落とせない。それは Task 3 で扱う。

### Task 3: PR の範囲外の指摘を繰り返させない

- **対象ファイル:** `plugins/ndf-shared/skills/review/SKILL.md`、
  `plugins/ndf-shared/skills/fix/SKILL.md`、
  `plugins/ndf-shared/skills/cross-review/docs/01-state-and-review.md`、
  `plugins/ndf-shared/skills/cross-review/scripts/state.py`、
  `plugins/ndf-shared/skills/cross-review/scripts/launch-codex.sh`、
  `plugins/ndf-shared/skills/cross-review/scripts/launch-gemini.sh`
- **変更内容:**
  - レビュープロンプトに **「この PR の担当範囲外」として既に却下された論点**を渡す。
    現在は既存コメントのスナップショットを渡しているが、`rejected` として Resolve せずに
    残したスレッドと、Summary コメントに書いた却下理由が区別なく混ざっている
  - **`state.py` が `rejected[]` の本文を保持できるようにする（本タスクの前提条件）。**
    現在の `cmd_merge_fix` は

    ```python
    "rejected": _count(fix.get("rejected")),
    ```

    と件数 int へ潰しており（同箇所のコメントも「resolved_threads / rejected は件数しか
    保存せず後段ループが無いため `_count()` で可」と明記）、却下理由の本文が state.json に
    残らない。`deferred_nits` と同じ per-item 蓄積（`st["rejected_topics"]` を新設し、
    `cmd_merge_fix` で `pr` / `round` を付けて append）へ変える。
    **件数を返す既存互換（int / 数値文字列で返された場合）は `_count()` のまま維持する**
  - **`fix` の `rejected[]` に `path` / `line` / `severity` を追加する。**
    現在の要素は `{comment_id, summary, reason_for_rejection}` のみで、`deferred` と違って
    位置情報を持たない。このままレビュー側へ渡しても「どのファイルのどの箇所の話か」が
    分からず、再提出の照合ができない
  - `cross-review` が蓄積した却下済み論点を、次ラウンドのレビュープロンプトへ
    「却下済み論点」として明示的に渡す（`launch-*.sh` のプロンプト組み立てに追加）
  - レビュー側の規約に「却下済み論点として渡されたものは、**新しい根拠がない限り
    再提出しない**」を追加する

  #69 で同じ論点が 5 回出たのは、却下の記録が次ラウンドの入力になっていなかったためである。

  **`check-oscillation` の感度低下に注意。** `state.py check-oscillation` は payload の
  `comments[].path:line` の前ラウンド重複率が 0.5 以上のとき `final="oscillation"` で
  中断し、無限ループを止める安全弁になっている。Task 2 の plausible 除外と本タスクの
  再提出抑止はどちらも重複率を下げるため、**この guard が発火しにくくなる**。
  合意できない論点を抱えた PR が中断されず max-rounds まで走り切る劣化が起きうるので、
  Task 5 の実測では「oscillation 中断が減って max_rounds 終了が増えていないか」も見る。

### Task 4: 効果を測れるようにする

- **対象ファイル:** `plugins/ndf-shared/skills/cross-review/scripts/state.py`、
  `plugins/ndf-shared/skills/cross-review/SKILL.md`
- **変更内容:**
  - `state.py report` の出力に **総指摘数 / rejected 数 / plausible として投稿しなかった数**
    を加える。**総ラウンド数は追加不要**（`cmd_report` が既に
    `## 総ラウンド数: {total} / PR数: ...` を出力し、PR 履歴行にも PR 単位の
    ラウンド数を出している）。`rejected` 数も既に `rounds[].fix.rejected` として
    state に入っているので、report 側の表示追加だけで済む
  - `state.json` に `by_verdict` を保持する。**実装点は `cmd_read_result`**
    （現在は review result から `by_severity` のみを取り込んでいる）
  - 導入前の基準値として、本プランの「実測」節の数値（17 本 / 平均 4.1 ラウンド）を
    `docs/specifications/ndf-skill-inventory.md` へ記録する

### Task 5: 変更が効いたかを実測する

- **対象ファイル:** `docs/specifications/ndf-skill-inventory.md`
- **変更内容:**
  - Task 1〜3 の適用後、**同種の PR を 5 本以上**回して平均ラウンド数と rejected 数を測り、
    導入前と並べて記録する
  - 悪化していた場合（根拠の必須化で有効な指摘まで落ちた等）は、どの型の指摘が
    落ちたかを記録したうえで Task 1 の分類を見直す

  比較対象は「同種の PR」に限る。棚卸の 17 本はドキュメント変更が大半なので、
  コード変更中心の PR と混ぜて平均を取らない。

## 影響範囲

| 対象 | 影響 |
| --- | --- |
| `review` | 指摘の書式が変わる。既存の `[重要度 / カテゴリ]` プレフィックスは維持し、その後ろに根拠を足す形なので、`fix` 側の重要度判定は壊れない |
| `fix` | 入力の質が上がる。`rejected` の区分自体は残す（検証を抜けた誤指摘は依然ありうる） |
| `cross-review` | state に `by_verdict` と却下済み論点が増える。既存の state.json は該当キーが無くても動く後方互換が要る。`rounds[].fix.rejected` は件数 int から本文リストへ意味が変わるため、**旧 state.json を読んだときに落ちないこと**を明示的に確認する |
| `check-oscillation` | plausible 除外と再提出抑止で `path:line` の重複率が下がり、oscillation 中断が発火しにくくなる（Task 3 参照） |
| 外部 AI（codex / gemini） | プロンプトのテンプレートが変わる。実体は `launch-codex.sh` / `launch-gemini.sh` で、両方に同じ書式を渡す必要がある |
| 3 ランタイム | `review` / `fix` / `cross-review` はいずれも 3 ランタイムへ配布済み。`SKILL.md` の行数上限（500 行）は `skills/README.md` が「推奨ではなく必須条件」と定めている。`review` は現在 356 行だが、**`cross-review` は 475 行で残り 25 行しかない**。Task 3/4 で `cross-review/SKILL.md` に加筆する場合は `docs/` への退避を同じ PR に含める |

## テスト計画

- [ ] `python3 -m pytest plugins/ndf-shared/skills/cross-review/tests/` が全件成功する
      （`test_state_merge_fix.py` は `rejected` が int / 数値文字列で返る劣化ケースを
      `_count()` が件数へ正規化することを assert している。本プランで `rejected` を
      list 前提の保存へ変えるため、**このテストの更新と再実行が必須**）
- [ ] `python3 scripts/check-skill-frontmatter.py` がエラー 0 / 警告 0
- [ ] `bash scripts/build-runtime-plugins.sh --check` が差異を検出しない
- [ ] `python3 scripts/check-markdown-links.py --root .` が成功する
- [ ] `bash scripts/validate-runtime-plugins.sh` が成功する
- [ ] `SKILL.md` が 500 行を超えていない（`review` / `cross-review` の両方）
- [ ] 既存の `state.json`（`by_verdict` を持たず `fix.rejected` が int）で `cross-review` が動く
- [ ] `/ndf:review <PR>` の投稿結果に、再現条件型と矛盾指摘型の根拠が入っている
- [ ] 根拠を書けない指摘が投稿されない（意図的に曖昧な指摘を作って確認する）
- [ ] 全指摘が plausible のとき `event: APPROVE` になり、ループが空回りしない
- [ ] `cross-review` 経由の `$TMP_DIR/<agent>-review-pr<PR>-result.json` に `by_verdict` が出る
- [ ] `state.py report` が総指摘数 / rejected 数 / plausible 数を出力する
- [ ] 却下済み論点が次ラウンドのレビュープロンプトへ渡っている

## フォローアップ（PR 外・Task 5）

以下はマージ前に満たせないため、上のテスト計画とは分けて追跡する。

- [ ] 適用後に 5 本以上の PR で実測し、平均ラウンド数を導入前（4.1）と比較した
- [ ] oscillation 中断が減って max_rounds 終了が増えていないか確認した

## やらないこと

- **`ndf:review` の発動条件の変更** — 組み込み `code-review` との競合は [#83](https://github.com/devbasex/ai-plugins/issues/83) で扱う。本プランは明示起動と `cross-review` からの内部呼び出しだけを前提にする
- **`category` の機械可読な構造化** — GitHub のインラインコメントは本文しか持てず、構造化しても受け取り側が使えない
- **effort レベルの導入** — `review` は `effort: high` 固定。網羅性と確信度のトレードオフを呼び出し側に持たせるかは、本プランの効果測定の結果を見てから判断する
- **`--fix` 相当の統合** — レビューと修正を分けている設計（`review` は修正しない）は `cross-review` のサブエージェント分離と結び付いており、変えると context 設計ごと見直しになる
