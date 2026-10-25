# #2087: agy 1.4.2 が NDF のエージェント定義 8 本の model（sonnet / opus）を拒み、agy の利用者に読み込まれず pre-push も落ちる

正は課題の本文（#2087）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何を見つけたか

agy CLI 1.4.2（手元の `~/.local/bin/agy`、2026-10-19 08:01 更新）の `agy plugin validate plugins/ndf/dev.agy` が、エージェント定義 8 本の `model: sonnet` / `model: opus` を受け付けず、exit 1 で落ちる。

```text
✗ agents/code-reviewer.md: model output error: invalid tool call error (invalid_args) Invalid model tier sonnet. The loader skips this agent
...（corder・data-analyst・debugger・devops-engineer・director(opus)・qa・researcher の計 8 本）
Error: validation failed: 8 errors, 5 warnings
```

- **利用者側の影響**: 出力のとおりなら、agy 1.4.2 の利用者には 8 本のエージェントが読み込まれない（`The loader skips this agent`）
- **開発側の影響**: `.githooks/pre-push` が `scripts/validate-runtime-plugins.sh` を通すため、agy の入った環境ではどのブランチも push できない（develop の `/work/ai-plugins/plugins/ndf/dev.agy` でも同じく落ちる）
- CI（`runtime-plugin-validate.yml`）は agy を入れないため、この検査を飛ばして成功している

## 再現の確認（2026-10-25、develop 6a86aa5ab）

- `agy --version` → `1.4.2`（`~/.local/bin/agy`）
- 回避の包みを `PATH` に足さずに `bash scripts/validate-runtime-plugins.sh`（pre-push と同じ検査）→ exit 1。`agy plugin validate plugins/ndf/dev.agy` の段で上と同じ 8 本が `Invalid model tier sonnet` / `opus` で落ち、`agents 3`（`model` を持たない定義だけ）が読み込まれる
- `plugins/ndf/dev.agy/agents` は `../agents` への symlink で、Claude Code の定義と同じファイルを読む。そのため `model` の値を書き換えると Claude Code 側が変わる
- agy の changelog は、エージェント定義の `model` を `flash` / `pro` などの tier 名（既定 `inherit`）と書く。`sonnet` / `opus` は tier に無い
- 手元では、失敗が `Invalid model tier` だけのときに通す包み（`~/.local/state/ndf/sv/s2086/bin/agy`）を `PATH` の先頭に置いて push している。包みは手元の回避で、利用者の側の「読み込まれない」は解消しない

## どこで見つけたか

2026-10-24、スプリント s2086 の設計のプランの `pr` ステップ（`git push` の pre-push）。記録は `~/.local/state/ndf/sv/s2086/1-design-2086-state/05-pr.out`。

## なぜこの変更の範囲外なのか

#2086 は `document-restructuring` と文書系 Skill の本文を変える。エージェント定義（`plugins/ndf/agents/*.md`）と agy の版の追従は受け入れ条件に無い。

## 直さないと何が起きるか

- agy の利用者がエージェントを使えない
- 開発者は push のたびに検査を手で回して `--no-verify` にする必要がある


## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。承認ゲート 1 で利用者が確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | `dev.agy` の agents を symlink でなく生成物にし、`model` を agy の tier 名（`flash` / `pro` / `inherit`）へ変換するか外す | 上の再現の確認と agy の changelog | 受け入れ条件 1〜6 は形を問わない形で書いた。生成物にするか・変換か外すかは未決 1（設計で決める） |
| S2 | CI でも agy の validate を通すか、agy の版を固定するかを決める | 本文の「CI は agy を入れない」 | 含まない（下の「調べて分かったこと」のとおり、両方とも既にある）。PR の時点で agy なしに落とす検査は受け入れ条件 7 |

## 依頼（原文）

> #2087。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示、日付: 2026-10-25。利用者の指示の原文ではない。課題の本文「何を見つけたか」〜「直さないと何が起きるか」と 2 件のコメントは、書き手: 起票者、日付: 2026-10-24〜2026-10-25）

## 調べて分かったこと（2026-10-25、design/issue-2087 d98516e5）

- 手元の agy 1.4.7（`~/.local/bin/agy`）で `agy plugin validate plugins/ndf/dev.agy` を打つと、本文と同じ 8 本が `Invalid model tier sonnet` / `opus` で落ち、集計は `agents 3`・`validation failed: 8 errors, 10 warnings` だった
- **CI は agy の validate を既に 2 か所で回している。** 本文の「CI は agy を入れない」は `runtime-plugin-validate.yml` に限った話である
  - PR の `runtime-smoke (agy)`（`runtime-plugin-smoke.yml`）は `tests/runtime-smoke/cli-versions.env` の `AGY_VERSION=1.3.4` に版を固定して `agy plugin validate` を回す（`tests/runtime-smoke/adapters/agy.sh`）。1.3.4 は `model` を検査しないため通っている
  - 日次の `runtime-smoke-latest (agy)`（`runtime-plugin-smoke-latest.yml`、最新の版）は 2026-10-18 06:00 JST の回から毎日失敗している（直近は run 40140471141、2026-10-25 07:53 JST）。失敗の行は本文と同じ 8 本の `Invalid model tier` である。claude・codex・kiro の行は成功している
- `model` を持たない 3 本（`supervisor`・`supervisor-waits`・`worker`）は agy 1.4.7 で読み込まれる
- `plugins/ndf/dev.agy/agents` は `scripts/build-runtime-plugins.sh` が張る symlink（`../agents`）で、`--check` が向き先を検査する。`plugins/ndf/README.md` の 37 行目がこの形を説明している
- Kiro の導入（`plugins/ndf/dev.kiro/install.sh`）と Codex 版は `model` を読まない

## 目的

以下で「agy に配る定義」は `plugins/ndf/dev.agy/agents/` の下にあり agy が読むエージェント定義を指す（今は `plugins/ndf/agents/` への symlink で、Claude Code の定義と同じファイル）。


- agy 1.4 系の利用者に、NDF のエージェント定義 11 本がすべて読み込まれる
- agy の入った手元で、包みも `--no-verify` も無しに push とプランの `validate` が通る
- Claude Code の定義の `model` を変えずに、agy へ配る定義だけを agy の受け付ける形にする

## 前提

- 前提 1: agy 1.4 系が `model` に受け付ける値は `flash`・`pro`・`inherit` と、欄が無いこと（欄が無いときは既定の `inherit`）の 4 通りである。根拠は 2026-10-25 のコメントの確認（1.4.7 で `flash`・`pro`・`inherit` が通り、`fast`・`default`・`sonnet`・`opus` が落ちた）と、上の 3 本が読み込まれること
- 前提 2: Claude Code の定義（`plugins/ndf/agents/*.md`）の `model: sonnet` / `model: opus` はこの変更で変えない。Claude Code では、エージェントごとに安い型と高い型を選び分けるために置いている値である
- 前提 3: agy に配る定義の `model` が 1.3.4 でも受け付けられる。1.3.4 は `model` を検査しないため、どの形を選んでも PR の `runtime-smoke (agy)` は通り続ける。設計で 1.3.4 の validate を 1 度通して確かめる
- 前提 4: 手元の包み（`~/.local/state/ndf/sv/s2086/bin/agy`）は NDF の配布物ではない。この変更では消さず、直した後は `PATH` に足さなくてよくなる

## 対象範囲

含む:
- agy に配る定義の `model` を、agy の受け付ける値にする仕組み（`plugins/ndf/dev.agy/agents` の形と、それを作る・検査する `scripts/build-runtime-plugins.sh`）
- agy の入っていない環境（PR の CI の単体テスト）でも、agy に配る定義の `model` が受け付けられない値になったら落ちる検査
- `dev.agy/agents` の形を説明する文書（`plugins/ndf/README.md` など）を新しい形に合わせること

含まない:
- Claude Code の定義の `model` の値を変えること（前提 2）
- `tests/runtime-smoke/cli-versions.env` の agy の固定版（1.3.4）を上げること。版の更新は `scripts/runtime-smoke-latest.py` の既存の手順が持つ
- agy 1.4.7 が出す残りの警告（`hooks.json` の matcher が Claude Code の Tool 名を含む 5 件、Skill の本文が 24000 バイトを超える 5 件）。どれも validate を落とさない
- `.githooks/pre-push` と `scripts/validate-runtime-plugins.sh` の検査を緩めること（`Invalid model tier` だけを通す、など）
- 手元の包みを消すこと（前提 4）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 開発者が `plugins/ndf/agents/*.md` を足した・変えた | 開発 | — | — |
| E2 | agy に配る定義が E1 に追従した | `bash scripts/build-runtime-plugins.sh`（形は未決 1） | 追従し忘れたまま E3 へ進む | E1 |
| E3 | pre-push と CI が、agy に配る定義と Claude Code の定義の食い違いを見つけた | `build-runtime-plugins.sh --check`・単体テスト | exit 1 で push と PR が止まる（受け入れ条件 6・7） | E1 |
| E4 | agy が配る定義を検査した | `agy plugin validate`（手元の pre-push、PR の 1.3.4、日次の最新版） | `Invalid model tier` で exit 1（今の不具合） | E2 |
| E5 | 利用者が agy に NDF を導入し、エージェントが読み込まれた | `agy plugin install` | 受け付けない `model` の定義を読み飛ばす（今の不具合） | E4 と同じ定義 |

E2 の引き金は今も手で打つ生成であり、E3 が追従し忘れを塞ぐ。E5 は配布の後に起き、確かめるのはリリース後テストである。

## 受け入れ条件

- [ ] 1. agy 1.4.7 で `agy plugin validate plugins/ndf/dev.agy` を打つと、`Invalid model tier` の行が 0 件で、exit 0 で終わる
- [ ] 2. 同じ出力の集計の行が `agents 11` になる（今は `agents 3`）
- [ ] 3. agy 1.4.7 が `PATH` にあり包みが無い環境で、`bash scripts/validate-runtime-plugins.sh` が exit 0 で終わる
- [ ] 4. PR の `runtime-smoke (agy)`（agy 1.3.4 に固定）が成功する
- [ ] 5. `plugins/ndf/agents/*.md` の `model` の値が変わらない（`git diff develop -- plugins/ndf/agents` に `model:` の行の差が無い）
- [ ] 6. agy に配る定義と Claude Code の定義は、`model` の欄の他（名前・説明・本文・ほかの frontmatter）が一致する。Claude Code の定義を変えて agy に配る定義を追従させないと、`bash scripts/build-runtime-plugins.sh --check` が exit 1 で終わり、食い違うファイルを示す
- [ ] 7. agy に配る定義の `model` が `flash`・`pro`・`inherit`・欄なしのどれでもないと、agy の無い環境で動く単体テスト（`uv run --frozen --project . --all-extras pytest`）が落ちる。`plugins/ndf/agents/` に `model: sonnet` の定義を 1 本足しても、agy に配る定義はこの条件を満たし、テストが通る
- [ ] 8. `dev.agy/agents` の形を説明する文書（`plugins/ndf/README.md` の 37 行目の段落）が、新しい形と一致する
- [ ] 9. マージとリリースの後、日次の `runtime-smoke-latest (agy)` の `agy validate ndf` のステップが成功する

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | エージェント定義の正は `plugins/ndf/agents/` の 1 か所に保つ。agy に配る側を手で直す運用を作らない（受け入れ条件 6） |
| システム環境 | agy 1.3.4（CI の固定版）と 1.4.7（手元と日次の最新版）の両方で validate が通る（受け入れ条件 1・4・9） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。agy の利用者に読み込まれるエージェントが 3 本から 11 本になり、8 本の `model` は agy の tier（または既定）になる。Claude Code・Codex・Kiro の利用者には変わらない。互換を壊さない（今まで読み込まれなかった定義が読み込まれるようになるだけ） |
| データ | 無し |
| 既存の振る舞い | `scripts/build-runtime-plugins.sh` が `dev.agy/agents` に作るもの（今は symlink 1 本）と、その `--check` の検査 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `~/.local/bin/agy plugin validate plugins/ndf/dev.agy`（受け入れ条件 1・2） |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4`（受け入れ条件 6・7） |
| 静的解析・型検査 | `PATH=$HOME/.local/bin:$PATH bash scripts/validate-runtime-plugins.sh`（受け入れ条件 3・6）。PR の CI の `runtime-smoke (agy)`（受け入れ条件 4） |
| 手動確認 | リリース後に、日次の `runtime-plugin-smoke-latest.yml` の agy の行を見る（受け入れ条件 9）。agy 1.4 系の手元で `agy plugin install` の後に `agy plugin list` でエージェントが 11 本あることを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 生成物は `scripts/build-runtime-plugins.sh` が作り、`--check` が食い違いを見る（`plugins/ndf/README.md`）。エージェント定義の実体は `plugins/ndf/agents/` に置く |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。確認は `bash scripts/check-lint.sh`（pre-push と CI の lint と同じ検査） |
| テスト戦略 | 生成と検査の形は単体テスト（`scripts/tests/` か `plugins/ndf/scripts/tests/`、agy なしで動く）で見る。agy の実物での読み込みは CI の smoke（1.3.4）と日次の最新版で見る。`.md` の文言を照合するテストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 生成のし直し（`bash scripts/build-runtime-plugins.sh`）と `--check`、全体テスト、手元の agy 1.4.7 での validate |
| 確認してから行う | agy の利用者に見える `model` の値の決め方（未決 1）。承認ゲート 1 で人が見る |
| 行わない | Claude Code の定義の `model` の変更、検査を緩めること、固定版の更新、範囲外の警告の手直し |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. agy に配る定義の形（symlink をやめて生成した写しにする、など）と、`model` を外すか（既定の `inherit`）・`sonnet`→`flash`・`opus`→`pro` のような対応で変換するか | 設計（`design`）で案を比べ、承認ゲート 1 で人が決める | 設計 PR の承認まで |
