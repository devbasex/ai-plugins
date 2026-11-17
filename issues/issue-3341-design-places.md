# #3341 の旧い置き場の一覧と退避の規則

設計文書 [issue-3341-design.md](issue-3341-design.md) の受け入れ条件 4・5 の表である。**旧い置き場**は、10 系の NDF（今の `plugins/ndf/`）が状態を書く置き場を指す（用語は設計文書の「用語」）。

例: 10 系はラッパーの記録を `~/.local/state/ndf/relay/` に書く。入れ替えでプラグイン名が `ndf` になると、新しい NDF の状態の置き場も `~/.local/state/ndf` になり、同じディレクトリを指す。下の表の O1 の行は、この置き場を新しい NDF の最初の起動で `~/.local/state/ndf.v10-<日付>` へ名前を変えて退避すると決める。

## 旧い置き場（14 種）

「重なり」の列は、入れ替えでプラグイン名が `ndf` になった後に、新しい NDF が同じパスへ書くかを示す。

| # | 置き場のパスの形 | 決めているコード | 中身 | 重なり | 扱い |
| --- | --- | --- | --- | --- | --- |
| O1 | `${XDG_STATE_HOME:-~/.local/state}/ndf`（`NDF_STATE_HOME` があればその値） | `plugins/ndf/scripts/lib/state_home.py:100-108` | `relay/`（ラッパーの記録と錠）・`wait-notify/`・`unattended/`・`guards/`・`context/`・`leases/`・`parallel/` | 重なる（N1） | 退避する |
| O2 | `$HOME/.claude/ndf/state` | `state_home.py:110`（`CLAUDE_CONFIG_DIR` でなく `$HOME` から求める） | 利用者の台帳（`ask.jsonl`・`mvv-gate.jsonl`・`gate-rejections.jsonl`・`metrics/`・`usage/` など） | 重ならない | 触らない |
| O3 | `<git の共通ディレクトリ>/ndf/state` | `state_home.py:151-160`・`durable_keys.py:34` | 耐久の記録 `dbos/<鍵>.sqlite`・`.lock`、プランの状態 `sv/` | 重なる（N3） | 退避する |
| O4 | `<git の共通ディレクトリ>/ndf/` の直下（`state` を除く） | `estimate_records.py:201`・`slow_step.py:158`・`worktree-registry.sh:25`・`merged_lib/trash.py:47` | 見積・遅い工程の履歴・worktree の台帳・ゴミ箱 | 重ならない（N4 の `state.lock` と名前が違う） | 触らない |
| O5 | `<git の共通ディレクトリ>/ndf-main.lock`・`ndf-branch-strategy.lock` | `merged_lib/main_lock.py:19-21`・`branch_strategy_write.py:30` | 排他の錠 | 重ならない | 触らない |
| O6 | `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/` の `relay.py`・`relay.version`・`relay.current`・`relay-<版>-<digest>/`・`shellrc/` | `relay_lib/common.py:97-99`・`version_dir.py:3-11,50`・`shellrc.py:21` | ラッパーのランチャーと版ごとの複製 | `relay.py` だけ重なる（N6） | 規則 P3 |
| O7 | `${NDF_ACCOUNTS_DIR:-<共有の設定ディレクトリ>/ndf/accounts}` | `claude_accounts.py:145` | アカウントごとの設定ディレクトリ | 重ならない | 触らない |
| O8 | `${NDF_LOCK_DIR:-$HOME/.claude/ndf/locks}` | `lib/gh_parts.py:142` | Issue の本文を書く錠 | 重ならない | 触らない |
| O9 | `${XDG_CACHE_HOME:-~/.cache}/ndf/` | `deps.py:147`・`hook_python.py:32`・`hook-env.py:32` | hook の venv・目印・ログ | 親が重なる（N7）。中の名前は違う（新しい venv は lock の hash 16 字） | 触らない |
| O10 | `$HOME/.claude/ndf-statusline.sh`・`${CLAUDE_CONFIG_DIR:-~/.claude}/.ndf-statusline-auth.json` | `statusline-switch.sh:22`・`statusline.sh:141` | statusline の本体と認証のキャッシュ | 重なる（新しい NDF も同じパス。`statusline/settings.py:23`） | 規則 P4 |
| O11 | 一時ディレクトリの固定名（`ndf-worktree-<セッション>.json`・`ndf-guards`・`ndf-checks`・`ndf-usage`・`ndf-stages`・`ndf-worktrees`・`/tmp/ndf/...`） | `hook_lib/worktree.py:144`・`token_guard.py:73`・`check-trigger.py:201` など 11 か所 | 一時ファイル | 重ならない（新しい NDF は使わない） | 触らない |
| O12 | リポジトリの `.ndf/` の、git に入らないもの（`handoff/`・`parallel/<計画>/`・`backlog/`・`loop`・`tmp/`） | `handoff.py:6`・`parallel.py:30`・`upkeep_depth.py:77` | 引継ぎ・並列の計画・棚卸しの記録 | 重なる（N8。新しい NDF の置き場の表 `contracts/domains/places.json` と同じパス） | 規則 P5 |
| O13 | worktree の `.cross_review/`・`.cross_refactoring/` | `review_lib/store.py:53`・`refactor_lib/paths.py:38` | cross 系の状態 | 未確認 | 触らない（worktree ごとに消える） |
| O14 | `${XDG_DATA_HOME:-~/.local/share}/ndf/relay.py` | `relay_lib/common.py:92` | 10.17.4〜10.17.6 の旧い複製 | 重ならない | 触らない |

起票時に挙がった 5 か所は、次の行に当たる。数え直した結果は 14 種である。

| 起票時に挙がった置き場 | 当たる行 |
| --- | --- |
| `~/.local/state/<名前>` | O1 |
| `.git/ndf/state` | O3 |
| `~/.claude/ndf/state` | O2 |
| `${CLAUDE_CONFIG_DIR}/ndf/` | O6 |
| `~/.cache/ndf/` | O9 |

## 退避の規則

**新しい NDF が同じパスへ書く旧い置き場のうち、状態を持つ O1・O3 を同じ 1 つの規則で退避する。** 同じパスへ書くほかの 3 つ（O6 の `relay.py`・O10・O12）は退避せず、規則 P3〜P5 で扱う。重ならない置き場は新しい NDF が読みも書きもしないため、10 系が使い続けても、残っても新しい NDF を壊さない。

| # | 規則 | 当たる置き場 |
| --- | --- | --- |
| P1 | **退避先の名前は `<元の名前>.v<旧い形の版>-<YYYYMMDD>` にする。** 旧い形の版は、置き場の `format.json` の番号で、`format.json` が無ければ `10` とする。退避は名前の変更だけで行い、中身を消さない | O1 → `ndf.v10-<日付>`、O3 → `state.v10-<日付>` |
| P2 | **退避先が既にあれば、何も変えずに止まる**（止まった理由 `retire_target_exists`。結果に退避先のパスを載せる）。同じ日に 2 回目の退避が要るのは、退避の後に `format.json` の無い置き場がまた作られたとき（10 系がまだ動いている）で、人が確かめる | P1 の全部 |
| P3 | **共有の設定ディレクトリの 10 系のファイルは退避しない。** 新しい NDF は `relay.py` を置き換えるとき、`relay.version` に自分の版を書く。10 系の SessionStart は `relay.version` より古い版で複製を置き直さない（`version_dir.py:362-365`）ため、10 系のセッションが残っていても 11.0 のランチャーを戻さない。`relay.current`・`relay-<版>-*`・`shellrc/` は 10 系の持ち物として残す | O6 |
| P4 | statusline は 1 つの共有の設定（`settings.json` の `statusLine`）から呼ばれるため、退避せず、最後に入れた版の中身で上書きしてよい。同じ設定ディレクトリを使うコンテナは同じ日に上げる（入れ替えの手順 U1） | O10 |
| P5 | リポジトリの `.ndf/` は利用者の作業の記録で、退避しない。新しい NDF は 10 系の計画・引継ぎを読み替えない（後方互換を置かない方針）。入れ替えの前に 10 系の計画を閉じる（入れ替えの手順 S2-1）ため、開いた計画は残らない | O12 |
| P6 | 退避の前に、旧い置き場の錠（O1 の `relay/*/relay.lock` とリレーハブの `relay/hub/hub.lock`、O3 の `dbos/*.lock`）を排他で待たずに取る。1 つでも取れなければ何も変えずに止まる（止まった理由 `legacy_running`。結果に取れなかった錠のパスを載せる）。すべて取れたら、名前の変更と新しい置き場の `format.json` の書き込みが終わるまで錠を持ち続ける | O1・O3 |

**今の新しい NDF の退避は、この規則と 4 点で違う。** 直すのは入れ替えの前の子 R1（設計文書の決定 4）である。

| 今の振る舞い | 根拠 | 直した後 |
| --- | --- | --- |
| `identity.prepare_state_dir` は、O1 を退避する前に旧いラッパーの錠を確かめない | `identity.py:300-330`（自分の錠 307 行のほかに錠の処理が無い） | P6 |
| 退避先が既にあると `T%H%M%S`・`-<n>` を足して退避を続ける | `identity.py:280-292`・`durable_sweep.py:69-78` | P2 |
| `format.json` の無い O3 の退避先が `state.v0-<日付>` | `durable.py:428-435,472` | P1 の `state.v10-<日付>` |
| `config-state`（O2 に当たる置き場）を退避の対象にし、パスを `${CLAUDE_CONFIG_DIR}/ndf/state` で求める | `identity.py:221-228`。10 系の書き手は `$HOME/.claude/ndf/state` | 退避の対象から外す（重ならないため。P1 の適用範囲の外） |

`config-state` を退避すると、共有の設定ディレクトリを使う別のコンテナの 10 系が書いている台帳まで名前が変わる。さらに、`CLAUDE_CONFIG_DIR` がアカウントごとのディレクトリ（例: `~/.claude/ndf/accounts/main`）を指す利用者では、パスが 10 系の書き手とずれて実在しない置き場を見る。

## 新しい置き場（9 種）

| # | 置き場のパスの形 | 決めているコード | 重なる旧い置き場 |
| --- | --- | --- | --- |
| N1 | `<NDF_STATE_HOME か XDG_STATE_HOME か ~/.local/state>/<名前>` | `plugins/ndf-next/src/ndf/identity.py:135-144,174` | O1 |
| N2 | `<N1>.lock` | `identity.py:295-297` | 無し |
| N3 | `<git の共通ディレクトリ>/<名前>/state/` | `core/durable.py:399-407` | O3 |
| N4 | `<git の共通ディレクトリ>/<名前>/state.lock` | `durable_locks.py:58,205-206` | 無し |
| N5 | 旧い状態の根の `relay/`・`context/`（読むだけ） | `relay/registry.py:148-159`・`relay/context_records.py:44-47` | O1 を読む |
| N6 | `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/relay.py`・`relay.root`、`${XDG_BIN_HOME:-~/.local/bin}/ndf-relay` | `relay/install.py:84-90,112` | O6 の `relay.py` |
| N7 | `${XDG_CACHE_HOME:-~/.cache}/<名前>/{venv/<hash>,roots}` | `hooks/env.py:54-66` | O9 の親 |
| N8 | リポジトリの `.ndf/{tmp,handoff,sprints,parallel}`、メインディレクトリの `.ndf/{loop,backlog/state}` | `contracts/domains/places.json` | O12 |
| N9 | プランの宣言の `state_dir` | `core/plan/runner.py:223-226` | 宣言しだい（10 系のプランの宣言は新しい NDF で読まない） |

## 調べ方

2026-11-17 に、両方のプラグインのコードから置き場を決める箇所を集めた。行番号は `design/issue-3341`（`main` の 9a817e18f の上）のものである。

```bash
# 10 系（テストを除く。state_home.py を除いて 212 行）
grep -rn --include=*.py --include=*.sh --include=*.json -E \
  'XDG_STATE_HOME|XDG_CACHE_HOME|XDG_CONFIG_HOME|CLAUDE_CONFIG_DIR|git-common-dir|expanduser|Path\.home|\$HOME|"/tmp/|/tmp/ndf|\.cache/ndf|\.config/ndf|\.claude/ndf|local/state' plugins/ndf
# 新しい NDF（上の式に XDG_DATA_HOME|CLAUDE_PLUGIN_DATA|gettempdir|TMPDIR|NDF_[A-Z_]*(HOME|DIR) を足す）
grep -rn -E '...' plugins/ndf-next/src plugins/ndf-next/bin plugins/ndf-next/hooks plugins/ndf-next/skills
```

- `~/.config/ndf` は両方で 0 件だった
- 新しい NDF の本体（`src/`）で `gettempdir`・`TMPDIR`・`"/tmp` は 0 件だった（当たったのはテストの隔離 `testing/isolation.py` だけ）
- 新しい NDF の `src/` で `relay.current`・`relay.version` は 0 件だった（`grep -rn 'relay.current\|relay\.version' plugins/ndf-next/src`）

## 戻すときのコマンド

戻すのは、入れ替えを戻すと人が決めたとき（入れ替えの手順の B 系）だけである。どれも名前の変更で、新しい NDF の置き場も消さずに `.v11-<日付>` で残す。

```bash
retire() { [ ! -e "$2" ] && mv -T "$1" "$2"; }   # 移す先が既にあれば何もせずに 1 を返す
d=$(date +%Y%m%d); s=${XDG_STATE_HOME:-$HOME/.local/state}
retire "$s/ndf" "$s/ndf.v11-$d" && retire "$s/ndf.v10-<退避した日付>" "$s/ndf"
c=$(git rev-parse --git-common-dir)
retire "$c/ndf/state" "$c/ndf/state.v11-$d" && retire "$c/ndf/state.v10-<退避した日付>" "$c/ndf/state"
```

`mv` だけでは上書きを防げない。移す先が既にある空のディレクトリなら、`mv -T` は確かめずに置き換える（GNU coreutils 9.7 で確かめた）。そのため移す先が無いことを先に確かめる。

打つ前に、新しい NDF のラッパーと耐久の実行を止める（`plugins/ndf/bin/ndf relay stop --all`。待ちを抱えた実行は `plugins/ndf/bin/ndf plan pending` で並べる）。退避した日付は `ls -d "$s"/ndf.v10-*` で確かめる。
