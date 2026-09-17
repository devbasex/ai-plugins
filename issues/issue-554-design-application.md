# #554: このリポジトリへの適用と実装の分け方

設計は [issue-554-design.md](issue-554-design.md)、要求と受け入れ条件は
[issue-554-requirements.md](issue-554-requirements.md) にある。この文書は **`ai-plugins` 自身への適用**と、
**実装の分け方・他の束との重なり**を持つ（配布物の設計とは別立てである）。

## このリポジトリへの適用

**配布物の機能とは別立てである。** 実装の Pull Request には入るが、配布物の振る舞いを決めない。

| # | すること | 受け入れ条件 |
| --- | --- | --- |
| 1 | `.ndf/instructions.json` を置く（「データ構造」の例のとおり） | AC44 / AC45 |
| 2 | `.github/workflows/runtime-plugin-validate.yml` へジョブ `instruction-files-check` を足す。Pull Request では絞り込まずに起動し、push の絞り込みへ `CHANGELOG.md` と `.ndf/**` を足す | AC46 / AC47 |
| 3 | `CLAUDE.md` の「版ごとの判断の記録」へ、版が決まる前の印と検査のコマンドを書く | AC48 |
| 4 | `docs/versioning-and-distribution.md` の「バージョン更新時の手順」へ検査を足し、手順 4 の置き場所を直す | AC49 |
| 5 | `CHANGELOG.md` の冒頭の置き場所を直す | AC51 |
| 6 | ruleset（`protect main and develop`）の必須の検査へ `instruction-files-check` を入れ、**同じ時点で** `docs/versioning-and-distribution.md` の「必須の検査 11 個」の数を直す | **実装の差分に入らない。** 入れると決まっており（利用者の判断）、実行は実装 Pull Request のマージ後に進行側が行う（AC50） |

**数と ruleset は同じ時点で変える。** 文書が「12 個」と書いて ruleset が 11 個しか求めない状態では、
`main` へ進めるときの断りの文言（`X of 11 required status checks …`）と文書が食い違う。

**4 と 5 は、規則と逆のことを書いている記載を直す。** どちらも「判断の理由は `CLAUDE.md` に置く」と書いており、
#551 で決めた置き場所（`docs/ndf-version-decisions.md`）と食い違う。

## 実装の分け方と他の束との関係

**実装は 1 本の Pull Request にする。** 配布物（検査・参照・`release` の 2〜3 行）と適用（宣言・ジョブ・手順の記載）は
同じ規則を指しており、分けると検査だけが入って適用の無い期間か、宣言だけが入って検査の無い期間ができる。

| 束 | 重なり | 扱い |
| --- | --- | --- |
| G1（PR #739） | **`release` SKILL.md を触る** | この設計が触るのは「3. 版と説明文書を更新する」の退避の段落だけである。G1 の実装が同じ節を触るなら、後から入る側が取り込む |
| G2（PR #741） | `scripts/parallel-measure.py` を新設 | 触るファイルが違う |
| G3（PR #740） | `skill-stats` / `retrospective` など | 重ならない |
| 配布の Pull Request | `CHANGELOG.md` の先頭へ版の節を足す | 触るのは冒頭の 1 文で、版の節とは行が離れている。**この検査が入った後の配布から、退避の漏れが配布の Pull Request で落ちる** |
