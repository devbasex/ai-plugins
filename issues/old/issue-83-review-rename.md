# `ndf:review` を `ndf:pr-review` へ改名し、命名規約に外部競合の観点を入れる

## 関連リンク

- 対応する issue: [#83](https://github.com/devbasex/ai-plugins/issues/83)（`ndf:review` が自然文で起動せず、組み込みの `code-review` に負ける）
- 自然文発動の結論: [#85](https://github.com/devbasex/ai-plugins/pull/85)（「達成できない」として実測付きで確定）
- 補完の実測: [#83 のコメント](https://github.com/devbasex/ai-plugins/issues/83#issuecomment-5261092956)
- 検出品質の改善は別プラン: [#86](https://github.com/devbasex/ai-plugins/pull/86) / `issues/review-detection-quality.md`

## 概要

issue #83 の対応方針の候補 3（改名）を採用し、`review` Skill を **`pr-review`** へ改名する。
あわせて、同じ失敗を次の Skill で繰り返さないよう **命名規約に「外部競合」の観点**を追加し、
機械検査できる範囲を `scripts/check-skill-frontmatter.py` に足す。

破壊的変更のため **v6.0.0** で出す。

## 問題・背景

issue #83 には 2 つの症状が記録されている。**片方は追わないと決着済み**で、本プランが
扱うのはもう片方である。

### 症状 1: 自然文で起動しない（決着済み・本プランの対象外）

`disable-model-invocation` を外しても、Claude Code 組み込みの `code-review` が常に勝つ。

| 依頼文 | 起動された skill |
| --- | --- |
| このブランチの変更をレビューしてください。 | `code-review` |
| PR を作る前に、このブランチをセルフレビューしてください。 | `code-review` |
| マージ前チェックをしてください。 | `code-review` |

#85 で「達成できないものとして扱い、追わない」と確定し、`08-verification.md` の受け入れ条件も
分割済み。**本プランはこれを覆さない。**

### 症状 2: 明示起動の入力コストが高い（本プランの対象）

`/ndf:` または `/ndf:re` まで打てば `ndf:review` は補完候補に出る。登録自体は正常である。
問題は **`ndf:` を付けずに `review` で絞り込むと埋もれる**ことにある。

`review` を後方に含む候補が同一環境に 7 件以上ある。

- `code-review`（Claude Code 組み込み）
- `coderabbit:code-review` / `coderabbit:coderabbit-review`
- `superpowers:requesting-code-review` / `superpowers:receiving-code-review`
- `security-review`
- `ndf:cross-review` / `ndf:review`

同じ NDF の `ndf:fix` は `fix` に競合が無いため `/fix` で一意に決まる。`review` だけ入力コストが
高いのはこの差による。`pr-review` にすれば `/pr-rev` で一意に確定する。

#### 切り分けで否定した仮説

| 仮説 | 判定 | 根拠 |
| --- | --- | --- |
| `commands/` が無いから補完されない | ✗ | `ndf:fix` も skill のみで補完される |
| `user-invocable: false` | ✗ | `review` に指定なし＝既定 `true` |
| `disable-model-invocation` | ✗ | 指定は `deploy` / `statusline` / `cherry-pick-pr` のみ |
| 設定による無効化 | ✗ | `settings.json` に `disabledSkills` 等なし |
| 旧版キャッシュとの二重ロード | ✗ | 4.20.0 が残存するがロードは 5.0.0 のみ |
| `effort: high`（25 Skill 中 `review` のみが保持） | ✗ | 公式リファレンス上は有効値。補完候補には出るため原因ではない |

### 症状 3: 命名規約が外部競合を見ていない（本プランの対象）

`plugins/ndf-shared/skills/README.md` の「トリガ語の一意性」は NDF 内の重複しか見ない。
#85 で **その限界の説明は追記済み**（同 README「検査できるのは NDF 内の重複だけ」節）だが、
issue #83 が残した「**検査対象に含めるかも検討する**」は未着手である。

規約の記述も「トリガ語」に閉じており、今回判明した **Skill 名そのものが後方一致で埋もれる**
問題は書かれていない。

## 判断

改名する。根拠は次のとおり。

- 自然文発動は #85 で追わないと決めたので、改名の目的は **明示起動時の識別性と入力コスト**に絞られる
- `review` は `cross-review` / `code-review` / `security-review` の部分文字列であり、
  この構造は環境に他プラグインが入るほど悪化する。名前を変えない限り解消しない
- 起動実績上位 5 個（`fix` `cross-review` `merged` `pr` `issue-plan-strategy`）は改名しない方針が
  `08-verification.md` にあるが、`review` はこの 5 個に**含まれない**

`pr-review` を選ぶ理由は、`/ndf:pr` `/ndf:pr-tests` と同じ `pr` 接頭辞に揃い、
PR に対する操作という責務が名前に出るため。

## 修正対象

編集は共通編集元の `plugins/ndf-shared/` に対して行い、3 ランタイムへは
`bash scripts/build-runtime-plugins.sh` で反映する。生成物（`plugins/ndf-claude/` /
`plugins/ndf-codex/` / `plugins/ndf-kiro/`）を直接編集しない。

| 対象 | 実測件数 |
| --- | ---: |
| `plugins/ndf-shared/skills/review/` → `plugins/ndf-shared/skills/pr-review/` | ディレクトリ 1 |
| `plugins/ndf-shared/` 内の参照 | 29 箇所 / 12 ファイル |
| `docs/` 内の参照 | 18 箇所 |
| `plugins/ndf-shared/manifests/{claude,codex,kiro}-skills.txt` | 各 1 行 |
| 各ランタイムの `plugin.json` の `skills` 配列 | 3 ランタイム |
| `plugins/ndf-kiro/prompts/review.md`（手書き管理。生成対象外） | ファイル 1 |
| `plugins/ndf-kiro/install.sh` の `DEPRECATED_PROMPTS`（手書き管理） | 1 行 |
| `plugins/ndf-shared/skills/ndf-policies/SKILL.md`（旧名対応表） | 1 節 |
| `plugins/ndf-shared/skills/README.md`（命名規約） | 1 節 |
| `scripts/check-skill-frontmatter.py`（既知競合名の検査） | 1 関数 |
| 版数 `5.0.0` → `6.0.0` | `plugin.json` 3 / `marketplace.json` / README 系 |

`plugins/ndf-shared/` 内訳（`grep -rc` の実測）:

```
skills/cross-review/SKILL.md              7
skills/review/SKILL.md                    5
skills/issue-plan-strategy/SKILL.md       4
skills/ndf-policies/SKILL.md              3
skills/external-ai/SKILL.md               2
skills/fix/SKILL.md                       2
skills/pr/SKILL.md                        1
skills/playwright-authoring/SKILL.md      1
skills/cross-review/docs/01-state-and-review.md   1
skills/cross-review/scripts/launch-codex.sh       1
skills/cross-review/scripts/launch-gemini.sh      1
skills/skill-stats/scripts/skill-stats.py         1
```

## PR 分割計画

| PR # | branch 名 | 概要 | 依存 | 並行可否 |
| --- | --- | --- | --- | --- |
| 1 | `feature/issue-83-rename-pr-review` | Skill の改名と全参照更新、manifest / plugin.json / 生成物 | なし | ○ |
| 2 | `feature/issue-83-v6-and-migration` | 版数を v6.0.0 へ、旧名対応表の入れ替え | PR1 | ×（PR1 merge 後） |
| 3 | `feature/issue-83-naming-policy` | 命名規約に外部競合の観点を追加、checker に既知競合名の検査 | なし | ○ |

release branch: `release/issue-83`
base branch: `main`

分割の根拠:

- PR1 は機械的な改名で差分が大きい（生成物を含め 3 ランタイム分）。**レビュー観点は「取りこぼしが無いか」の 1 点**に絞られる
- PR2 は改名が入って初めて書ける内容（対応表の移行先が `pr-review` になる）ため PR1 に依存する。
  版数更新も改名と同じリリースに載る必要がある
- PR3 は改名とは独立した規約・検査の追加で、**PR1 が無くても単体で意味がある**。並行して進められる

## タスク分解

### Task 1: Skill の改名（PR1）

- **対象ファイル:** `plugins/ndf-shared/skills/review/` 一式
- **変更内容:**
  - `git mv plugins/ndf-shared/skills/review plugins/ndf-shared/skills/pr-review`
  - `SKILL.md` の frontmatter `name: review` → `name: pr-review`
  - `description` の `Triggers:` を見直す。現在の `'レビューして'` `'PRレビュー'` は
    組み込み `code-review` と競合して機能していないことが #85 で確定しているため、
    **明示起動を前提とした語へ寄せる**（`'PRレビュー'` `'マージ前チェック'` は残し、
    単独の `'レビューして'` は外す）
  - `effort: high` は維持する（補完に出ない原因ではないことが切り分け済み）

### Task 2: 参照の更新（PR1）

- **対象ファイル:** `plugins/ndf-shared/` 内 12 ファイル（29 箇所）、`docs/` 内 18 箇所
- **変更内容:**
  - `/ndf:review` → `/ndf:pr-review`、`skills/review/` → `skills/pr-review/` を置換する
  - **`cross-review` 系の 10 箇所は機械置換で済ませない。** `cross-review` 自身の名前に
    `review` が含まれるため、`grep -w` や単純な `sed s/review/pr-review/` は
    `cross-review` を `cross-pr-review` に壊す。`/ndf:review` と `skills/review/` の
    2 パターンに限定して置換し、置換後に `grep -rn "cross-pr-review"` で 0 件を確認する
  - **限定置換は「壊す側」だけでなく「取りこぼす側」も確認する。** 2 パターンに
    絞ると、名前が単独で現れる箇所を拾えない。実際に `plugins/ndf-kiro/prompts/review.md`
    （Kiro 向けプロンプト。`build-runtime-plugins.sh` の生成対象ではなく手書き管理）が
    漏れ、round 1 で gemini が検出した。置換後に次で洗い直す:

    ```bash
    find plugins -name 'review*' -not -path '*/node_modules/*'
    git grep -nE '(^|[^-a-z])review([^-a-z]|$)' -- plugins/
    ```

  - 併せて `plugins/ndf-kiro/install.sh` の `DEPRECATED_PROMPTS` へ旧ファイル名を足す。
    利用環境に残った旧プロンプトを再インストール時に削除するための一覧である
  - `skill-stats.py` の 1 箇所は集計対象名の可能性があるため、旧名の実績が
    失われないか（旧名の集計が必要か）を確認したうえで直す

### Task 3: manifest / plugin.json / 生成物（PR1）

- **対象ファイル:** `plugins/ndf-shared/manifests/{claude,codex,kiro}-skills.txt`、
  各ランタイムの `plugin.json`、生成物一式
- **変更内容:**
  - 3 つの manifest の `review` 行を `pr-review` へ。**行の並び順が配布順序に影響しないか**を
    確認する（`claude-skills.txt` では 4 行目、`codex-skills.txt` では 23 行目にある）
  - `plugins/ndf-claude/.claude-plugin/plugin.json` の `skills` 配列の `"./skills/review"` を
    `"./skills/pr-review"` へ。codex / kiro 側も同様に確認する
  - `bash scripts/build-runtime-plugins.sh` を実行し、3 ランタイムの生成物へ反映する
  - 旧 `plugins/*/skills/review/` が残っていないことを確認する

### Task 4: 版数を v6.0.0 へ（PR2）

- **対象ファイル:** `plugins/ndf-claude/.claude-plugin/plugin.json`、
  `plugins/ndf-codex/.codex-plugin/plugin.json`、kiro 側の版数保持箇所、
  `.claude-plugin/marketplace.json`、`README.md` / `AGENTS.md` / `CLAUDE.md`、
  `docs/presentations/` 配下、`tests/runtime-smoke/assertions/assert-kiro-agent.sh`
- **変更内容:**
  - `5.0.0` → `6.0.0`。`plugin.json` の `description` に埋め込まれた `(v5.0.0)` も直す
  - Kiro 配布物の版数は #84 で導入した仕組みに従う。**版数の持ち方を勝手に変えない**
  - `assert-kiro-agent.sh` は版数を assert しているため、更新漏れが smoke テストで落ちる

### Task 5: 旧名対応表の入れ替え（PR2）

- **対象ファイル:** `plugins/ndf-shared/skills/ndf-policies/SKILL.md`
- **変更内容:**
  - 現在の節は「## v5.0.0 で変わったコマンド名（v6.0.0 で削除）」で、
    `/ndf:review-branch` → `/ndf:review --branch` など v4.20.1 由来の 2 行を持つ。
    v6.0.0 を出す本プランで **この節を削除する**（予告どおり）
  - 代わりに「## v6.0.0 で変わったコマンド名（v7.0.0 で削除）」を作り、
    `/ndf:review` → `/ndf:pr-review` を載せる
  - 同 SKILL.md の 51-52 行目にある「`review` は自然文では選ばれない」の記述も
    新名で書き直す

### Task 6: 命名規約に外部競合の観点を追加（PR3）

- **対象ファイル:** `plugins/ndf-shared/skills/README.md`
- **変更内容:**
  - 「検査できるのは NDF 内の重複だけ」節（138-150 行）は #85 で追記済みなので**残す**。
    ここに **Skill 名そのものの後方一致**という観点を足す
  - 規約として次を明文化する
    - Skill 名は、ランタイム組み込み・主要プラグインの Skill 名と**後方一致させない**
    - 後方一致すると、利用者が `/` 補完でその語を打ったとき候補に埋もれる
    - 実例として `review`（`code-review` / `security-review` / `cross-review` の部分文字列）と、
      競合の無い `fix` の対比を載せる
  - トリガ語の規則とは別の観点なので、**節を分ける**（現在の「トリガ語の規則」配下ではなく
    「命名の規則」として立てる）

### Task 7: 既知競合名の検査を checker へ（PR3）

- **対象ファイル:** `scripts/check-skill-frontmatter.py`
- **変更内容:**
  - 既知の外部 Skill 名を定数リストで持ち、NDF の Skill 名がそのいずれかと
    **後方一致する場合に警告**を出す（エラーにはしない）
  - 対象は現時点で観測できているものに限る:
    `code-review` / `security-review` / `coderabbit-review` / `requesting-code-review` /
    `receiving-code-review`
  - **この検査は網羅ではない**ことをコードのコメントと README の両方に書く。
    配布先に何が入っているかは検査時点では分からないため、リストは手動更新である
  - 既存の `--strict` 相当の扱い（警告 0 を要求する運用）と衝突しないか確認する。
    衝突する場合は警告ではなく情報表示にする

## 影響範囲

| 対象 | 影響 |
| --- | --- |
| 利用者 | `/ndf:review` が使えなくなる。`ndf-policies` の対応表で移行先を案内する |
| `cross-review` | 内部で `review` を直接呼ぶ経路がある（`SKILL.md` に 7 箇所）。**呼び出し名の更新漏れは cross-review の全停止に直結する**ため、PR1 のレビューで最優先に見る |
| `execute-goal`（Release 3） | `08-verification.md` 157 行に「レビュー段階は `review` を明示的に呼ぶ」と申し送りがある。名前が変わるので同ドキュメントも直す |
| 3 ランタイム | Claude Code / Codex / Kiro の全てに配布済み。manifest 3 本と生成物すべてが対象 |
| 既存の cross-review state | `state.json` は Skill 名を保持しないため影響なし（`rounds[].codex` / `.gemini` はエージェント名） |
| 旧名の利用実績 | `docs/specifications/ndf-skill-inventory.md` の集計は旧名で記録済み。**過去の実績を書き換えない**。改名した事実を注記する |

## テスト計画

- [ ] `git grep -nE "/ndf:review([^-a-zA-Z]|$)" -- plugins/` が 0 件

      `\b` は使わない。`\b` は `-` の直前にもマッチするため、`ndf-policies` の対応表に残す
      **旧コマンド名** `/ndf:review-branch` / `/ndf:review-pr-comments` まで拾ってしまう。
      同じ理由で、一括置換に `sed 's|/ndf:review\b|/ndf:pr-review|g'` を使うと旧名が
      `/ndf:pr-review-branch` に壊れる（round 1 で codex が検出）。置換後は
      `git grep -n "/ndf:pr-review-"` が 0 件であることも確認する
- [ ] `git grep -n "cross-pr-review" -- plugins/ docs/` が 0 件（置換事故の検出）
- [ ] `git grep -n "skills/review\b" -- plugins/ docs/` が 0 件
- [ ] `docs/` と `issues/` に残る `/ndf:review` が、**v5.0.0 時点の記録**（棚卸台帳・受け入れ条件・
      リリースノート・日付入りプレゼン資料）だけであることを目視確認する
- [ ] `python3 scripts/check-skill-frontmatter.py` がエラー 0 / 警告 0
- [ ] `bash scripts/build-runtime-plugins.sh --check` が差異を検出しない
- [ ] `python3 scripts/check-markdown-links.py --root .` が成功する
- [ ] `bash scripts/validate-runtime-plugins.sh` が成功する
- [ ] `plugins/{ndf-claude,ndf-codex,ndf-kiro}/skills/pr-review/SKILL.md` が存在し、
      旧 `skills/review/` が 3 ランタイムとも残っていない
- [ ] 3 つの manifest に `pr-review` があり `review` が無い
- [ ] `bash tests/runtime-smoke/...` の Kiro assertion が v6.0.0 で通る
- [ ] `claude plugin validate` が成功する
- [ ] `/ndf:pr-review --branch` が起動し、統合後の報告形式が出る（手動）
- [ ] `/ndf:cross-review <PR>` が内部の `pr-review` 呼び出しで動く（手動・本プランの PR で実施）

## やらないこと

- **自然文発動を再び追うこと** — #85 で「達成できない」と確定済み。改名しても組み込み
  `code-review` との用途重複は残るため、暗黙起動は前提にしない
- **`cross-review` / `fix` / `pr` / `merged` / `issue-plan-strategy` の改名** —
  `08-verification.md` が起動実績上位として改名しない方針を定めている
- **`effort: high` の削除** — 補完に出ない原因ではないことが切り分け済み
- **外部競合の自動検出（配布先環境のスキャン）** — 配布先に何が入っているかは
  検査時点で分からない。Task 7 は手動更新のリストによる best-effort に留める
- **旧名のエイリアス提供** — Skill 名のエイリアス機構が無く、ディレクトリを 2 つ置くと
  manifest と実績集計が二重になる。移行は対応表の案内で行う
- **日付入りプレゼン資料の書き換え** — `docs/presentations/2026-08-06-ai-plugins-intro.*` は
  2026-08-06 に行った v5.0.0 紹介の**発表記録**である。当日話していない名前へ書き換えると
  記録として不正確になり、`build.sh` による PDF / 単一ファイル HTML の再生成（Chromium 必須）で
  巨大なバイナリ差分も出る。`diagrams/pr-flow.mmd` を含めて旧名のまま残す
- **v5.0.0 時点の記録の書き換え** — 棚卸台帳（`ndf-skill-inventory.md`）、受け入れ条件
  （`08-verification.md`）、`README.md` の「NDF v5.0.0 の主な変更」節は、その時点の事実の記録。
  旧名のまま残し、**改名した事実を注記で足す**にとどめる。v6.0.0 の変更は PR2 で
  「NDF v6.0.0 の主な変更」節として別に書く
