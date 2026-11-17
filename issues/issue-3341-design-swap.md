# #3341 の入れ替えの手順: 前の子・当日・利用者・移行・戻し方

設計文書 [issue-3341-design.md](issue-3341-design.md) の「処理の流れ」を、打つコマンドと確かめの粒度へ下ろした手順である。#3304 を実装するレーン（lswap）は、この文書と #3304 の本文だけを読んで進める。

例: 手順 S2-3 は `git rm -r -q plugins/ndf` と `git mv plugins/ndf-next plugins/ndf` を打ち、`test ! -e plugins/ndf-next` が 0 で終わることで確かめる。確かめが落ちたら次の手順へ進まず、結果に落ちた確かめを載せて止まる。

## 手順の区切り

| 区切り | 手順 | 担い手 | 使う NDF |
| --- | --- | --- | --- |
| 前の子 | R0・R1 | lswap（`main` へ先に入れる子の PR） | 10 系 |
| 当日（PR まで） | S2-0〜S2-9 | lswap（#3304 のスプリント） | 10 系 |
| 当日（承認とリリース） | S2-10〜S2-15 | 人の操作。リリースは workflow | 10 系の承認の検査、11.0.0 の publish |
| 利用者 | U1〜U5 | 利用者（CHANGELOG の案内に従う） | 11.0.0 |
| 戻し方 | B1〜B6 | 人の判断と操作 | 11.0.0 から 10 系の中身へ |

**S2-9 で計画を閉じてから、NDF は動かさない。** 承認前の試用・承認・マージキューへの投入は人の操作で行う。こうすると、入れ替えの PR を作ったスプリント自身が「動いているスプリントが無い」の前提（S2-1）を破らない。S2-1 が確かめるのは lswap 以外のスプリントとプランで、lswap のプランは S2-9 で閉じる。

S2-10〜S2-15 は S2-13（workflow）を除いてすべて人の操作で、確かめも人が打つ。S2-13 の確かめは S2-14 の前に人が打つ。#3304 は、人が S2-15 で確かめの結果を受け入れ条件に付けて閉じる。lswap は S2-0〜S2-9 の確かめだけを結果に載せる。

## 前の子（入れ替えより前に `main` へ入る）

| 子 | 中身 | 確かめ |
| --- | --- | --- |
| R0 | 外からの参照の検査 `scripts/check-plugin-refs.py`（今のツリーと入れ替えの後のツリーの 2 つのモード）と、書き換えの表 `scripts/plugin-refs-swap.json`（[issue-3341-design-refs.md](issue-3341-design-refs.md) の「実行する参照」の 147 組のうち「同じパス」を除く 94 組）と、表を当てる `apply`。`main` の ruleset が必須にしているジョブ `script-structure-check`（`.github/workflows/script-structure.yml`）へ、2 つのモードを打つステップを足す（新しいジョブを作らず、R0 の時点から必須のチェックで落とす）。`--tree swapped` は「未作成」の行を既知の失敗として数え、それ以外の失敗が 1 件でもあれば落ちる（設計文書の決定 1 の判定の表） | `python3 scripts/check-plugin-refs.py check --tree current` と `--tree swapped` が R0 の PR の head で 0。表に無い `plugins/ndf/scripts/` の参照を workflow へ 1 つ足した試しの差分で `script-structure-check` が落ちる。「未作成」が残っていても 0 で、`--strict` だけが落ちる |
| R1 | 新しい NDF の退避と旧いラッパーの見分けの直し（[issue-3341-design-places.md](issue-3341-design-places.md) の規則 P1〜P3・P6）。`identity.prepare_state_dir` と `durable.prepare_store` が同じ規則で退避し、`relay install` が `relay.version` に自分の版を書く | 新しいプラグインのテスト（`uv run --project plugins/ndf-next pytest`）。テスト設計は設計文書の「テスト設計」 |

## 当日の手順（#3304）

| # | 手順 | 確かめ |
| ---: | --- | --- |
| S2-0 | 前提がそろっているかを確かめる。R0・R1・S1 がマージ済み、機能の対応表のすべての行が行き先か作らない理由を持ち試走を通った、今の機能の不具合を直す Issue（#3395 の決定 127）が閉じた、Factory の固定（F1）が済んだ | `jq -e '[.rows[] \| select((.not_built == null) and (.trial.result != "passed"))] \| length == 0' scripts/ndf-next-parity/parity/features.json` が 0（2026-11-17 は 200 行が未通過で 1）・`python3 scripts/check-plugin-refs.py check --tree swapped --strict` が 0（「未作成」の行が 0）・F1 の `ndf-ask:human-action` の答え |
| S2-1 | 10 系で動いているスプリントとプランが lswap 以外に無いことを確かめる。あれば終わるのを待ち、終わらないものはオーガナイザーへ戻す | `.ndf/parallel/*/plan.json` の計画ごとに `python3 plugins/ndf/scripts/parallel.py status <計画>` の「動いている」が lswap の分を除いて 0・`python3 ~/.claude/ndf/relay.py ls --json` で動いているラッパーが lswap の分だけ |
| S2-2 | `main` の先端から worktree `release/v11.0.0` を作る（10 系の `/ndf:worktree`） | `git rev-parse --abbrev-ref HEAD` が `release/v11.0.0`・`git merge-base HEAD origin/main` が `origin/main` の先端と同じ |
| S2-3 | `git rm -r -q plugins/ndf` の後に `git mv plugins/ndf-next plugins/ndf` を打つ | `test ! -e plugins/ndf-next` が 0・`test -x plugins/ndf/bin/ndf` が 0 |
| S2-4 | `plugin.json` 2 本の `name` を `ndf`、`version` を `11.0.0`、`description` の版を `v11.0.0` にする。名前から作る生成物（hook の登録・Codex の定義）を作り直す: `plugins/ndf/bin/ndf hooks render`・`plugins/ndf/bin/ndf manifests render` | `jq -r .version plugins/ndf/.claude-plugin/plugin.json plugins/ndf/.codex-plugin/plugin.json` が 2 行とも `11.0.0`・`claude plugin validate .` が 0・`plugins/ndf/bin/ndf hooks render --check` と `manifests render --check` が `ok`・`grep -rn 'ndf11' plugins/ndf` が 0 件 |
| S2-5 | 外からの参照を書き換える: `python3 scripts/check-plugin-refs.py apply`。当てた後に、書き換えの表 `scripts/plugin-refs-swap.json` と、`script-structure-check`（`.github/workflows/script-structure.yml`）の `check --tree swapped` のステップを消す（`check --tree current` のステップは残す）。根の検査（実行する参照の表の「根の検査」）とそのテスト 26 本を、新しいプラグインを読む形へ寄せる（`build-runtime-plugins.sh` は `manifests/skills.json` を読む。`check-doc-staleness.py` の `FAMILY` は配る系統の宣言から読む。`ci-heavy-skip.py` の `SELF_TESTED` を消す）。`ndf-next.yml`・`ndf-next-checks.yml`・`ndf-next-release.yml` を消し、リリースの workflow を `release-cd.yml` へまとめる（決定 7）。ルートの `pyproject.toml`・`conftest.py` の 10 系向けの記述を外す。生成物 G1〜G7・G9 を作り直す（G8 は試走の記録で書き換えない） | `python3 scripts/check-plugin-refs.py check --tree current` が 0・`test ! -e scripts/plugin-refs-swap.json` が 0・`grep -c -- '--tree swapped' .github/workflows/script-structure.yml` が 0 件・`test -z "$(ls .github/workflows \| grep -E '^(ndf-next.*\|release-approval)\.yml$')"` が 0（どれも無い）・必須のチェック（`main` の ruleset の 17 個）の名前のジョブがすべて残る（`python3 scripts/check-required-checks.py --branch main --repo devbasex/ai-plugins` の失敗が 0 件）・`bash scripts/build-runtime-plugins.sh --check` が 0 |
| S2-6 | 文書を [docs/ndf-swap-doc-destinations.md](../docs/ndf-swap-doc-destinations.md) の行き先の表どおりに直す。固定の URL の起点は `git merge-base origin/main HEAD` で求める。実験版の置き場の記述（`AGENTS.md` の「安定版と実験版」・`docs/ndf-experiments.md`）と `marketplace.json` の `ndf` の `description`（版と Skill・エージェントの数）も表の行どおりに直す。行き先の判断は #3381・#3382 が持ち、ここでは決めない | `python3 scripts/check-markdown-links.py` が 0・`grep -n 'scripts/experimental' AGENTS.md` が 0 件・`jq -r '.plugins[] \| select(.name=="ndf") \| .description' .claude-plugin/marketplace.json` に `v11.0.0` が入る |
| S2-7 | 利用先が名指すスキーマ 4 本（`skills/development-workflow/schemas/project.schema.json`・`skills/worktree/schemas/worktree.schema.json`・`skills/release/schemas/release.schema.json`・`skills/release/schemas/instructions.schema.json`）を、契約のスキーマ（`contracts/schemas/declarations/`）と同じ中身にする（決定 12） | 4 本それぞれで `cmp plugins/ndf/skills/<Skill>/schemas/<名前>.schema.json plugins/ndf/contracts/schemas/declarations/<名前>.schema.json` が 0 |
| S2-8 | `CHANGELOG.md` に 11.0.0 の節を書く。利用者の手順 U1〜U5、利用先の宣言の移し方、旧い置き場の退避と残る置き場、戻し方の版（決定 10）を含める。`docs/versioning-and-distribution.md` に 11.0.0 の節を足す | `python3 scripts/check-doc-staleness.py` が 0 |
| S2-9 | S2-3〜S2-8 を 1 本の PR（`release/v11.0.0` → `main`）にし、CI を通す。PR を作ったら `ndf-ask:human-action` で 2 つの依頼を出す: (1) 秘密 `NDF_RELEASE_TOKEN` の登録（11.0.1 からの統合した workflow が使う。C1）(2) 承認前の試用と承認の操作（S2-10〜S2-12）。依頼を出したら lswap の計画を閉じる | `gh pr checks <PR>` がすべて成功・PR の head（`gh pr view <PR> --json headRefOid`）で `plugin.json` 2 本の `version` が `11.0.0`（不変条件 I6）・`ndf-ask:human-action` の依頼の記録・`parallel.py status <lswap の計画>` の「動いている」が 0 |
| S2-10 | （人）承認前の試用: `main` のチェックアウトから `python3 plugins/ndf/scripts/release-verification-steps.py verify-install --ref release/v11.0.0 --expect 11.0.0 --runtimes claude,codex` を打つ（隔離した設定ディレクトリと `CODEX_HOME` で入れる。#3672 の決定 17）。PR の head のチェックアウトで `plugins/ndf/bin/ndf-claude` から主要な Skill を 1 回ずつ起動する | `verify-install` の結果が `ok`・`NDF_LAUNCH_LIVE=1` で `tests/bin/test_ndf_claude.py` の確かめが通る（init の `plugins` に `ndf` が 1 つだけで、`path` が `plugins/ndf`） |
| S2-11 | （人）リリース承認ゲート: 承認の証跡のコメントを PR に書く。証跡は `main` の定義（10 系の `release-approval.yml`）が確かめる | PR の commit status `ndf/release-approval` が `success` |
| S2-12 | （人）PR をマージキューへ入れる（`gh pr merge <PR> --merge`）。管理者の迂回を使わない | `gh pr view <PR> --json state -q .state` が `MERGED` |
| S2-13 | （自動）マージのコミットの `release-cd.yml`（入れ替えの後の定義）が、`plugins/ndf/bin/ndf release-steps publish` でタグ `ndf--v11.0.0` と GitHub Release を作る。落ちたら `workflow_dispatch`（`step=publish`）で打ち直す | `gh release view ndf--v11.0.0` が 0・`git ls-remote --tags origin ndf--v11.0.0` が 1 行 |
| S2-14 | （人）ラベル `rebuild-1142` の開いている Issue が #3304 を除いて 0 件であることを確かめる。残れば理由を添えて閉じるか、11.0.0 の後に動かすオーガナイザーへ送る | `gh issue list --label rebuild-1142 --state open --limit 1000 --json number -q 'map(select(.number != 3304)) \| length'` が 0（`--limit` を省くと 30 件で切れる） |
| S2-15 | （人）使わなくなったリポジトリの変数 `NDF_RELEASE_RUNNER` を消す（統合した workflow は読まない）。消したら、S2-10〜S2-15 の確かめの結果を #3304 の受け入れ条件に付けて #3304 を閉じる | `gh variable list` に `NDF_RELEASE_RUNNER` が無い・`gh issue view 3304 --json state -q .state` が `CLOSED` |

どの手順も確かめを持つ。

**S2-3〜S2-8 の間、`main` へ NDF の PR を入れない。** S2-1 で計画がすべて閉じているため、NDF の PR を作るレーンは無い。マージキューは版上げの PR を承認してからキューへ入れるまで、スプリントの PR をキューへ入れない（#3672 の決定 18）。

## 利用者の手順（11.0.0 を入れるとき）

**同じ設定ディレクトリ（`CLAUDE_CONFIG_DIR`。既定は `~/.claude`）を使うコンテナは、同じ日にまとめて上げる。** プラグインの導入先は設定ディレクトリにあり、1 回の更新ですべてのコンテナの次の起動が 11.0 になる。

| # | 手順 | 確かめ |
| ---: | --- | --- |
| U1 | 設定ディレクトリを共有するすべてのコンテナで、10 系を止める。先に、続けたい耐久ワークフロー（下の表）を 10 系で終わらせる。動いている計画は `python3 plugins/ndf/scripts/parallel.py stop <計画>` で止め、ラッパーとセッションを `python3 ~/.claude/ndf/relay.py stop <名前>` で止める。リレーハブは前景のラッパーが兼ねるため、そのラッパーを止めると止まる | 各コンテナで `python3 ~/.claude/ndf/relay.py ls --json` の `metrics.running` が 0 で `metrics.hub` が `false`・各リポジトリで `parallel.py status <計画>` の「動いている」が 0 |
| U2 | 1 つのコンテナで更新する: `claude plugin marketplace update ai-plugins` の後に `claude plugin update ndf@ai-plugins`。Codex は `codex plugin add ndf@ai-plugins` で入れ直す | `claude plugin list` の `ndf` が `11.0.0` |
| U3 | 新しい NDF の最初の起動（どの `ndf` の副命令でもよい）が、旧い置き場 O1・O3 を退避する。止まったら理由で分ける: `legacy_running` は U1 へ戻る、`retire_target_exists` は退避先を見て人が決める | `ls -d ${XDG_STATE_HOME:-~/.local/state}/ndf.v10-*` が 1 行 |
| U4 | コンテナごとにラッパーを入れ直す（`/ndf:install-wrapper`。中では `ndf relay install --replace-old`） | `ndf relay status` が `ok` |
| U5 | 利用先のリポジトリの宣言を移す（下の「利用先のリポジトリ」） | `ndf project check` が 0 |

止めるもの 4 つは、どれも U1 で止め、U2 で更新し、U3・U4 の後に入れ直す。

| 対象 | 止める（U1） | 更新する（U2・U3） | 入れ直す（U4 の後） |
| --- | --- | --- | --- |
| ラッパー | `relay.py stop <名前>` | U2 でプラグインを更新し、U3 で旧い置き場 O1（ラッパーの記録と錠）を退避する | `/ndf:install-wrapper` で入れ直す（U4） |
| リレーハブ | 兼ねている前景のラッパーを止める。錠は O1 の `relay/hub/hub.lock` | O1 と一緒に退避する。登録簿は 10 系のものとして退避先に残り、11.0 は読み替えない | U4 の後に最初に起動したラッパーが兼ねる。`ndf relay status` が `ok` |
| 耐久ワークフロー | 続けたい実行は U1 の前に 10 系で終わらせる。動いているものは、実行を持つ計画とラッパーを止めると止まり、錠（O3 の `dbos/*.lock`）が外れる | O3 と一緒に退避する。止めた後に `PENDING` で残る実行（2026-11-17 の ai-plugins で 52 件）は 10 系の記録として退避先に残り、11.0 は再開も読み替えもしない（後方互換を置かない方針。引継ぎ文書と同じ扱い） | 11.0 で続ける作業は、新しい NDF で計画を立て直して始める |
| 開いているスプリント | `parallel.py stop <計画>` で止める。続けたい作業は U1 の前に 10 系で終わらせる | リポジトリの `.ndf/` は退避しない（規則 P5） | 11.0 で計画を立て直す |

U1 の確かめが通っても錠を持つ 10 系のプロセスが残っていれば、U3 の退避は規則 P6 で `legacy_running` を返して止まり、何も変えない。そのときは U1 へ戻る。

## 移行

### 利用先のリポジトリ

| 対象 | 入れ替えの後に起きること | 扱い |
| --- | --- | --- |
| `.ndf/*.json` の `$schema`（例: `/work/nyle-dx/.ndf/project.json` は `https://raw.githubusercontent.com/devbasex/ai-plugins/main/plugins/ndf/skills/development-workflow/schemas/project.schema.json`） | パスは新しいプラグインにもある。中身は今の新しいプラグインのコピーが古く、`branch_strategy` を持たない（S2-7 で直す） | URL は書き換えない。S2-7 の後は、契約のスキーマと同じ中身が同じ URL で返る |
| `.ndf/project.json` の旧い形（`delivery` など） | 新しい NDF は旧い形を読み替えない | `ndf project branch-strategy migrate` で移す。自動で移せない値（例: nyle-dx の `delivery` が `{"unknown": ...}` の形で、試し打ちが `stopped` を返した）は、出力の `failed` の行を利用者が答えて直す。`.ndf/` の書き換えは利用者の承認で行う（C7） |
| 進行中の引継ぎ文書（`.ndf/handoff/`） | 10 系のスクリプト名を名指す文書は新しい NDF で打てない。2026-11-17 に ai-plugins は 102 本のうち 41 本、nyle-dx は 1 本のうち 1 本が名指していた | 10 系の作業の記録として残し、書き換えない。続ける作業は U1 の前に 10 系で終わらせる（S2-1 と同じ考え） |
| 共有の設定ディレクトリに残る 10 系のファイル（O2・O6 の `relay.current` など） | 新しい NDF は読みも書きもしない | すべてのコンテナが 11.0 になった後、棚卸しで消すかを提案する。消すのは人が確かめてから（C4） |

### Codex

| 項目 | 持ち主 | 入れ替えの PR での扱い |
| --- | --- | --- |
| `.codex-plugin/plugin.json` の `skills` の並び | 新しいプラグインの配る Skill の表 `manifests/skills.json`（`ndf manifests render` が作る） | S2-4 で作り直す。今の 2 本の名前の集合は同じ 45 個 |
| `hooks` | 同じ相対パス `./hooks/codex.json`。中の venv のパスがプラグイン名（`ndf11`）を含む | S2-4 の `hooks render` で作り直す |
| `description` の版数 | リリースの工程（`domains/release/plugins.py`） | S2-4 で `v11.0.0` にする |
| `build-runtime-plugins.sh --check` | 根の検査 | S2-5 で `manifests/skills.json` を読む形にする。今は `manifests/codex-skills.txt` が無い系統を飛ばすため、入れ替えの後は何も見ずに通る |
| 隔離した `CODEX_HOME` での導入 | 10 系の `release-verification-steps.py verify-install` | S2-10 で `--runtimes claude,codex` を打つ |

### Factory（volareinc/nd-factory）

**ai-plugins の側の契約の持ち主は新しいプラグインの契約の表で、volareinc/nd-factory の変更はそのリポジトリの持ち主が行う。** この設計は順番と持ち主だけを決め、volareinc/nd-factory へ書き込まない。

| 契約 | 10 系の置き場 | 新しいプラグインの置き場 | 持ち主の子 |
| --- | --- | --- | --- |
| `NDF_FACTORY_RUN` | `contracts/env/NDF_FACTORY_RUN.json` | `contracts/domains/env.json` の行 | D2（表の行）・M3a（hook） |
| 並列の外部の受け渡し | `contracts/forms/parallel-external.json` | `contracts/domains/ndf-files.json` の `organizer-*` の行 | M7 |
| 無人の報告の形 | `contracts/forms/unattended-report.json` | `contracts/domains/entrypoints.json` の `report-unattended` | M3a |
| `NDF_FACTORY_RUN` の hook 3 本（開始・Stop・終了） | `hooks/claude.json:56,168,181` | `contracts/hooks/guards.json` の Stop の 1 本だけ（開始と終了に当たる guard は 2026-11-17 に見つからなかった） | M3a。S2-0 の対応表の試走で確かめる |

volareinc/nd-factory のランナーは `plugins/ndf/scripts/sprint-state.py` を直に呼ぶ（`factory/runner/src/runtime.ts:147,241,257`）。使う NDF の版の下限は `10.27.37` に置く（`translate.ts:198`）。入れ替えの後はこの呼び出しが切れる。

| 順 | 何を | 誰が | いつ |
| ---: | --- | --- | --- |
| F1 | ランナーが入れる NDF を 10 系の最後のタグ（`ndf--v10.27.<最後>`）に固定する | volareinc/nd-factory の持ち主（S2-0 の前に `ndf-ask:human-action` で依頼する） | 11.0.0 を出す前（S2-0） |
| F2 | ランナーの呼び出しを新しい副命令（`ndf progress`・`ndf answer`）へ移し、版の下限を `11.0.0` にし、コピーした契約（testdata の `parallel-external.json` など）を作り直す | 同上 | 11.0.0 を出した後（S2-13 の後） |
| F3 | F1 の固定を外す | 同上 | F2 の後 |

volareinc/nd-factory#183（承認ゲートの名前を正の名前にする）は 2026-11-17 に閉じている（`gh issue view 183 -R volareinc/nd-factory`）。

## 戻し方

**revert で戻るのはコードだけである。** 退避した置き場・`relay.current`・進行表の DB・閉じた Issue・volareinc/nd-factory の変更は戻らない。それらの扱いは #3378 の決定が持ち、ここでは手順の順番と打つコマンドだけを書く。

| # | 手順 | 確かめ |
| ---: | --- | --- |
| B1 | 人が戻すと決める（E11）。戻す理由と、戻さずに 11.0.1 で直せないかを #3378 の手順で確かめる | 判断の記録 |
| B2 | `main` から `release/v11.0.1` を切り、入れ替えのマージのコミットを `git revert -m 1 <コミット>` で戻す。`plugin.json` 2 本の `version` を `11.0.1` にし、`CHANGELOG.md` に「中身は 10.27.<最後> と同じ」と書く（決定 10） | `git diff <10 系の最後のタグ> -- plugins/ndf ':!plugins/ndf/.claude-plugin' ':!plugins/ndf/.codex-plugin'` が空 |
| B3 | S2-9〜S2-13 と同じ形で PR・承認・マージキュー・publish を通す（戻した後の `release-cd.yml` は 10 系の定義で動く） | `gh release view ndf--v11.0.1` が 0 |
| B4 | 利用者: 11.0 のラッパーを止め（`plugins/ndf/bin/ndf relay stop --all`）、U2 と同じ形で更新し、置き場を戻す（[issue-3341-design-places.md](issue-3341-design-places.md) の「戻すときのコマンド」） | `ls -d ${XDG_STATE_HOME:-~/.local/state}/ndf.v11-*` が 1 行 |
| B5 | 利用者: 10 系のラッパーを入れ直す（10 系の `/ndf:install-wrapper`） | `python3 ~/.claude/ndf/relay.py ls --json` が読める |
| B6 | 入れ替えで閉じた Issue を開き直す。対象は #3304 と、S2-14 で閉じた Issue | `gh issue list --label rebuild-1142 --state closed --limit 1000 --search "closed:>=<S2-13 の日付>"` の一覧を開き直した記録 |

## #3304 の本文の差分

**#3304 の受け入れ条件は、上の S2-0〜S2-15 と 1 対 1 にする。** 本文の書き換えは設計承認ゲートの後に行う（#3395 の決定 124 と同じ扱い）。

| 今の本文の項目 | 扱い |
| --- | --- |
| 受け入れ条件 1〜9 | S2-0〜S2-15 の 16 項目に置き換える。項目ごとに手順の文と「確かめ」の列と担い手（lswap か人か workflow）を写す。S2-10〜S2-15 の項目は人が確かめて付け、S2-15 で #3304 を閉じる |
| 「S2 の PR で理由を添えて閉じる旧い子（drop 21 本）」の表と、その受け入れ条件 | 外す。21 本は 2026-11-16 に閉じた（各課題の本文の「11.0.0 の扱い」）ことへの参照に置き換える |
| 「許可ファイル 526 個を向け直さずに済む」を数える項目 | 外す。生成物は作り直す（[issue-3341-design-refs.md](issue-3341-design-refs.md) の G1〜G3） |
| 「判断 (b) の利用者へのアナウンス」の項目 | S2-8 に含める |
| 「退行していないことを対応表の試走で確かめる」・「不具合の Issue が閉じたことをスクリプトが判定する」 | S2-0 に含める |
| 「入れ替えの後のリリースを workflow が行う」 | S2-5（workflow をまとめる）と S2-13（publish）に含める |
| `ci-heavy-skip.py` の `SELF_TESTED`・`check-doc-staleness.py` の `FAMILY` | S2-5 に含める |
| 追補「M2 のリリースの workflow を動かす人の操作」（秘密の登録・ruleset の例外・変数 `NDF_RELEASE_RUNNER`） | 秘密の登録だけを S2-9 の依頼に残す。ruleset の例外は要らない（決定 7）。変数は S2-15 で消す |
| 追補「入れ替えの後の文書の行き先の表を通す」 | S2-6 に含める |
| 追補「検証の行を `.ndf/project.json` へ足す」（`channel: "verify"`・`verify/next`） | 外す（決定 8） |
| 「触るファイル」 | 下の「#3304 の触るファイル」に置き換える |

## #3304 の触るファイル

- `plugins/ndf/` 全体（`git rm` と `git mv`）
- `plugins/ndf/.claude-plugin/plugin.json`
- `plugins/ndf/.codex-plugin/plugin.json`
- `plugins/ndf/hooks/claude.json`
- `plugins/ndf/hooks/codex.json`
- `.claude-plugin/marketplace.json`
- [issue-3341-design-refs.md](issue-3341-design-refs.md) の「実行する参照」の 48 本のうち書き換える行を持つファイル
- 同じ文書の生成物 G1〜G7・G9
- 根の検査のテスト 26 本
- `.github/workflows/release-cd.yml`
- `.github/workflows/release-approval.yml`（消す）
- `.github/workflows/ndf-next.yml`（消す）
- `.github/workflows/ndf-next-checks.yml`（消す）
- `.github/workflows/ndf-next-release.yml`（消す）
- 文書の行き先の表の S2 の行が名指す文書
- `CHANGELOG.md`
- `docs/versioning-and-distribution.md`
