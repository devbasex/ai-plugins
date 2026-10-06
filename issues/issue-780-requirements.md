# #780: parallel-measure capacity: 予備の二重計上と SwapFree の残量判定が重なり、メモリ圧の無いホストでも並列数が 1 に固定される

正は課題の本文（#780）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

起票時の本文をそのまま残す（見出しだけを 1 つ下げた）。

### 何を見つけたか

**`parallel-measure.py capacity`（v10.15.1）が、メモリ圧の無いホストで並列数を 1 に固定している。** 実行計画 13（2026-09-19T02:20Z）の測定は「空き 6455 MiB・`swap_low`・起動してよい本数 1」で、同じ VM で動く他のワークフローも同じ結果だった。しかし同じ時刻の VM は、メモリの stall を測る PSI が 0.00、スワップの出入りが 10 秒で 6 ページで、**圧は無い**。

2026-09-19 02:30Z 前後に、このホスト（Docker Desktop の VM、MemTotal 21.5 GiB・9 CPU）で測った値:

| 見た値 | 結果 |
| --- | ---: |
| `MemAvailable` | 5793 MiB |
| `SwapTotal` / `SwapFree` | 2047 / 0 MiB |
| `/proc/pressure/memory` some / full の avg10・avg60・avg300 | すべて 0.00 |
| `pswpin + pswpout` の 10 秒間の増分 | 6 ページ |
| このコンテナの cgroup `memory.current` / `memory.peak` / `memory.max` | 2642 MiB / 9422 MiB / max |
| VM 全体の `AnonPages` のうち、このコンテナの `memory.stat anon` | 14.6 GiB のうち 2.1 GiB |
| `capacity --running 0` | `by_memory=1 allowed=1 limited_by=memory,swap_low,floor` |
| `capacity --running 1` | `by_memory=2 allowed=1 limited_by=memory,swap_low`（**追加できない**） |
| `capacity --running 2` | `by_memory=3 allowed=2 limited_by=memory,max,swap_low` |

**1 になる経路は 3 つの控えめな判定の重なりである。** どれか 1 つなら妥当だが、3 つが同時に効くと、1 本の実測（約 1.2 GiB、#621）に対して **最初の 1 本を足すのに 6 GiB 超の空きを要求する**。

| # | 判定 | いま起きていること |
| ---: | --- | --- |
| 1 | 予備 2048 MiB を空きから先に引く | 予備の根拠は「conductor の claude 本体（約 470 MiB）と他プロセスの揺れ」だが、**本体も動いている担当も既に `MemAvailable` から引かれている**。既にあるものを予備でもう一度引いている |
| 2 | 1 本の見込み 2048 MiB で割る | 実測 1.2 GiB に「テストとコンテナの山」を足した値。#1 の予備と足し合わさるため、1 本目に 4 GiB を要求する形になる |
| 3 | `SwapFree` が 25% を下回れば 1 減らす | **`SwapFree` は今の圧ではなく過去の履歴である。** 一度スワップへ追い出された冷たいページは、触られるか持ち主が終わるまで戻らないため、この VM では `SwapFree` が 0 のまま張り付く。PSI 0.00・スワップ I/O ほぼ 0 でも毎回 `swap_low` が付き、実質「常に 1 本分の追加の余裕を要求する」になっている |

`swap_low` の閾値 25% は 2026-09-17 の 1 点（空き 9.5 GiB・スワップの空き 15% → 2 本）でconductor の選択と一致するように決めた（確定仕様の表）。2 日後の空き 5.8 GiB・スワップの空き 0% では同じ判定が 1 本を返し、conductor の期待（2〜3 本）と食い違う。**1 点で合わせた閾値が別の点で合わない**ので、閾値の値ではなく見ている量が違う。

develop（0447ac50）でも同じ入力で同じ判定になる（MemAvailable 5793 MiB・SwapTotal 2047 MiB・SwapFree 0 の `--meminfo` を渡し、cgroup を無しにして実測）。

```console
$ python3 plugins/ndf/scripts/parallel-measure.py capacity --running 0 --meminfo <上の meminfo> --cgroup-dir /nonexistent
by_memory=1 allowed=1 limited_by=memory,swap_low,floor
$ ... --running 1
by_memory=2 allowed=1 limited_by=memory,swap_low
```

2026-10-03 の develop（37f8d578）でも同じ入力で同じ判定になる（`DEFAULT_RESERVE_MIB=2048`・`PER_LANE=2048`・`MAX=3`・`SWAP_FREE_MIN_PCT=25` のまま）。

### どこで見つけたか

- `plugins/ndf/scripts/parallel-measure.py` の `run_capacity` / `lanes_by_memory`（`DEFAULT_RESERVE_MIB` / `DEFAULT_PER_LANE_MIB` / `DEFAULT_SWAP_FREE_MIN_PCT`）
- `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` の「決定と理由」の初期値の表
- `plugins/ndf/scripts/tests/test_parallel_measure.py` の `test_capacity_reports_the_measured_values`（`swap_low` を期待している）

### 直さないと何が起きるか

- **並列の仕組み（#540 #550 #621）が、この VM では常に逐次で動く。** `--running 1` で `allowed=1` になるため、1 本目が終わるまで 2 本目を起動できない。並行度を上げるための実行計画が、測定によって 1 本に押し戻される
- 空きが 6 GiB を超えないホストでは、`swap_low` が一度付くと外れないので、**利用者が `--swap-free-min-pct 0` を毎回付けるか、既定を疑わずに 1 本で進めるか**の二択になる
- 「測った値」の表に**担当側の使用量（`memory.current`）が残らない**ため、確定仕様が言う「1 本の見込みは測った値の表から直す」ができない。空きだけでは 1 本あたりの実測を導けない

### 提案する決め方

**方針: 定数の当て推量を実測に置き換え、固定の上限を無くす。** 決める量は 3 つで、それぞれ「何を測って決めるか」を持つ。測れない環境だけ引数の初期値へ落ちる。

| 決める量 | いまの決め方 | 提案 |
| --- | --- | --- |
| 足せる本数の元になる空き | `min(MemAvailable, cgroup の残り) − 予備 2048` | 同じ式。**予備は「他プロセスの揺れ」だけにし、既に動いている本体と担当を含めない**（`MemAvailable` が既に引いている）。初期値を 1024 MiB へ下げ、`--reserve-mib` はそのまま残す |
| 1 本の重さ | 定数 2048 MiB（実測 1.2 GiB に山を足した値） | **動いている担当から測る。** `(いまの cgroup anon − 0 本のときの anon) ÷ running` を 1 本の平均とし、山への余裕 `--peak-factor`（初期値 1.5）を掛ける。0 本のときだけ `--per-lane-mib`（初期値 1536）を使う。下限 `--per-lane-min-mib`（初期値 512）を割らない |
| 圧の判定 | `SwapFree` が 25% を下回れば 1 減らす | **PSI で「いま stall しているか」を見る。** `/proc/pressure/memory` の `some avg10` が `--psi-some-max`（初期値 10）を超えたら **`hold`**（今の本数を超えて足さない）。PSI が無いカーネルでは `/proc/vmstat` の `pswpin + pswpout` を `--sample-seconds`（初期値 2）挟んで 2 回読み、`--swap-io-max-pages-per-sec`（初期値 512）を超えたら `hold`。**残量の判定（`swap_low`）は廃止する** |
| 総本数の上限 | 定数 3 | **廃止する。** 1 本の重さを測れるなら、上限は `空き ÷ 重さ` が持つ。`--max` は引数として残すが既定を無くし、渡したときだけ `limited_by` に `max` が付く |
| 1 回の見直しで足す数 | 制限なし（上限 3 が実質の制限） | **`--max-add`（初期値 2）。** 総数の上限ではなく、**測る前に足す数**の制限である。担当の重さは動き出すまで測れないため、足したら次の見直しで測ってからまた足す。0 本からの初回だけ見込みで動くことを許す形にする |
| 落ちた後の縮め方 | `oom_kill` 増で `running − 1` | 変えない |

#### 判定を 3 段にする

| 段 | 条件 | `allowed` | `limited_by` |
| --- | --- | --- | --- |
| `grow` | 圧なし・`oom_kill` 増なし | `running + min(--max-add, ⌊(空き − 予備) ÷ 1 本の重さ⌋)`（`--max` があれば小さい方） | `memory` / `max_add` / `max` |
| `hold` | PSI またはスワップ I/O が閾値を超えた | `max(running, 1)` | `hold_psi` / `hold_swap_io` |
| `shrink` | `oom_kill` が起点より増えた | `max(0, running − 1)`（既存） | `oom_kill_increased` |

いまの `swap_low` は圧の大きさに関係なく 1 本分（2 GiB 相当）を要求し、`floor` と重なると意味が読めない。段に分けると、表の 1 行から「なぜその本数か」が読める。

#### 1 本の重さを測る仕組み

**測る量は cgroup の `memory.stat` の `anon` である**（読み取りは `lib/procs.py` の `procs.cgroup_memory`）。 `memory.current` はページキャッシュ（`file`）を含み、テストや git の操作で膨らんで戻るため、1 本の重さとしては当てにならない。`anon` はこのホストで 5 秒間の揺れが 3 MiB だった。

| 何を | どこに | 誰が |
| --- | --- | --- |
| 0 本のときの `anon`（`cgroup_anon_mib`） | 実行計画の先頭「anon の起点」（`oom_kill の起点` と並べる） | conductor が開始時の `capacity` の出力から写す |
| いまの `anon`・測った 1 本の重さ・使った 1 本の重さ | 「測った値」の表の列 `anon` / `1 本の実測` / `1 本の見込み` | `capacity` が `cgroup_anon_mib` / `per_lane_observed_mib` / `per_lane_used_mib` として出し、conductor が写す |
| 起点 | `--anon-baseline-mib <anon の起点>` | conductor が `--oom-baseline` と同じように渡す |

`running ≥ 1` かつ起点があるときだけ実測を使う。**同じコンテナで動く無関係のセッションは実測を重く見せる方向に働く**（`anon` が増えるが `running` に数えない）ので、外れるのは安全側である。

スプリントを閉じるとき（`concurrency`）に「測った値」の表の `1 本の実測` の最大を「閉じたときの測定」へ写せば、次のスプリントの `--per-lane-mib` の根拠になる。**初期値 1536 はこの列が溜まるまでの暫定値**である。

#### 試算

2026-09-19 のこのホスト（空き 5793 MiB・PSI 0.00・`anon` 2193 MiB）で、起点を 2193 とし、担当 1 本が 1.2 GiB を使ったと仮定した見直しの列:

| 見直し | running | anon | 1 本の実測 × 1.5 | 空き | 足せる数 | allowed | 決めた条件 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 開始 | 0 | 2193 | —（見込み 1536） | 5793 | ⌊(5793−1024)÷1536⌋ = 3 → 2 | 2 | memory, max_add |
| 1 回目 | 2 | 4650 | 1229 × 1.5 = 1843 | 3336 | ⌊(3336−1024)÷1843⌋ = 1 | 3 | memory |
| 2 回目 | 3 | 5880 | 1843 | 2106 | 0 | 3 | memory |

いまの既定は同じ入力で 1 本のまま動かない。上限 3 を外しても、空き 5.8 GiB のホストでは重さの実測が 3 本で止める。空き 12 GiB のホストなら同じ手順で 5〜6 本まで伸び、5〜6 本で落ちた #621 の状況は「1 本の重さを測らずに一度に起動した」ことが原因なので、`--max-add` と見直しごとの実測が代わりに受ける。

#### 変えないこと・範囲外

- **測定は拒否しない**（設計の決定）は変えない。`hold` も案内であって、起動を止めるのは手順である
- 「コンテナを起動する検査を持つ担当は同時に 1 本」（`parallel-work.md` 下限 6）は変えない。コンテナは担当の cgroup の外で動き、`anon` に現れない
- 同じ VM で複数の conductor が同時に測ると、同じ空きを見て同時に起動する競合がある。今回は扱わず、起きたら別に起票する
- `/proc/meminfo`・cgroup v2・PSI のどれも無い環境（macOS など）は、いままでどおり終了コード 3 で「測れない」とし、1 本で進める

### 受け入れ条件の案

- PSI 0.00・スワップ I/O ほぼ 0・`SwapFree` 0 の入力で減点が付かず、`grow` の本数が出る（`swap_low` が出力に現れない）
- PSI の `some avg10` が閾値を超える入力、または PSI が無くスワップ I/O が閾値を超える入力で `hold` が付き、`allowed` が `max(running, 1)` になる
- `--anon-baseline-mib` と `--running ≥ 1` を渡すと、`per_lane_observed_mib` が `(anon − 起点) ÷ running` になり、`per_lane_used_mib` がそれに `--peak-factor` を掛けて `--per-lane-min-mib` で下から抑えた値になる。起点が無いか `--running 0` なら `--per-lane-mib` を使う
- `--max` を渡さないと総本数の上限が掛からず、`--max-add` が 1 回の見直しで足す数を抑える。`limited_by` に `max_add` が付く
- 出力に `cgroup_anon_mib` / `per_lane_observed_mib` / `per_lane_used_mib` が増え、`execution-plan.md` の先頭に「anon の起点」、「測った値」の表に 3 列が増える
- 確定仕様の初期値の表が書き換わり、「予備に既に動いているものを含めない」「上限は測定が持つ」が明文化される
- `test_parallel_measure.py` の 2026-09-17 の例が新しい判定の期待値に更新され、3 段（`grow` / `hold` / `shrink`）それぞれの検査がある

### 由来

v10.15.1 のリリース後、実行計画 13（マイルストーン 13）の起動時の測定で並列数 1 が続いたことの調査。実行計画 13 の前提にも「スワップが 0 MiB で `swap_low` が掛かり、初回は 1 本」と記録されている。

### 関連

- #621 — 並行の本数の下限がホストのメモリを見ていなかった（この測定の起点）
- #540 — 長い待ちの間に依存しない次の設計を進める（並行度を上げる方向。測定が 1 本を返すと効かない）
- #550 — /goal の工程をサブエージェントへ出す 3 層の運転（conductor がこの測定を呼ぶ）
- #760 — worker を CLI へ変える（1 本の重さが変わるため、実測の列が要る）
- #1142 — スプリント 2b で cgroup の位置と使用量の読み取りが `lib/procs.py`（psutil の包み）へ移った。`read_meminfo` は `parallel-measure.py` に残る。判定の式はこの課題が持つ

## 目的

- メモリの圧が無いホストで、`parallel-measure.py capacity` が並列の本数を 1 に押し戻さないようにする。並列の仕組み（#540 #550 #621）が、空きのあるホストで 2 本以上を起動できる状態にする
- 本数の判定が「いまの圧」と「1 本の実測の重さ」を見るようにし、出力の 1 行から「なぜその本数か」を読めるようにする
- 実行計画の「測った値」の表に 1 本の重さの実測が残り、次のスプリントの初期値を直す根拠になる状態にする

## 前提

- 前提 1: 判定の方針と式は、依頼（原文）の「提案する決め方」をそのまま採る。予備メモリの初期値 1024 MiB・0 本のときの 1 本の見込み 1536 MiB・山への余裕 1.5・1 本の下限 512 MiB・PSI の閾値 10・スワップ I/O の閾値 512 ページ/秒・標本の間隔 2 秒・1 回の見直しで足す数 2 は**暫定の初期値**で、すべて引数で上書きできる。値の調整は「測った値」の表の実測が溜まってから別に行う
- 前提 2: 圧の判定は VM 全体の `/proc/pressure/memory` の `some avg10` を見る（cgroup ごとの `memory.pressure` は見ない）。PSI が読めないときだけ `/proc/vmstat` の `pswpin + pswpout` を 2 回読んだ差で判定する
- 前提 3: PSI も `/proc/vmstat` も読めないときは、圧を判定できないものとして `grow` で続け、出力に判定できなかったことを残す（測定は拒否しない設計の決定を保つ。メモリの空きによる本数の制限と `oom_kill` による縮めは働き続ける）
- 前提 4: 1 本の重さの実測に使う量は cgroup の `memory.stat` の `anon` である。読み取りは共通の層（`lib/procs.py` の `procs.cgroup_memory`）に足し、`parallel-measure.py` に別の読み取りを書かない
- 前提 5: `anon` の起点は、実行計画を開始した時点（`--running 0`）の `capacity` の出力から conductor が写す。`oom_kill` が増えて起点を更新するときも、`anon` の起点は更新しない（担当が落ちた分は `anon` から抜けるため、起点を変える理由が無い）
- 前提 6: 廃止する `--swap-free-min-pct` は、渡されても引数の誤りにしない。標準エラーに廃止を知らせて無視する（`cross-refactoring` の廃止した引数と同じ扱い）
- 前提 7: 出力のキーの並びは既存のキーの順を保ち、新しいキーは末尾へ足す。既存のキーの名前と意味は `limited_by` の値の集合を除いて変えない
- 前提 8: `supervise.py queue --max` の既定 3 は、この課題で変えない（`capacity` の上限とは別の値で、プランを流す本数の既定である）

## 対象範囲

含む:
- `parallel-measure.py capacity` の判定の式・引数・出力（予備メモリの意味と初期値、1 本の重さの実測、圧の判定、総本数の上限の既定の廃止、1 回の見直しで足す数、判定の区分 `grow` / `hold` / `shrink`）
- `lib/procs.py` の cgroup の読み取りへの `anon` の追加
- `issue-plan-strategy` の `references/execution-plan.md` の実行計画の形（先頭の「anon の起点」、「測った値」の表の列、`capacity` の呼び方、閉じたときの測定の列）
- 確定仕様 `docs/specifications/ndf-execution-plan-and-parallel-capacity.md` の初期値の表・引数・出力・式
- `plugins/ndf/scripts/tests/test_parallel_measure.py` の期待値と、3 つの判定の区分の検査

含まない:
- 同じ VM で複数の conductor が同時に測ったときの競合（起きたら別に起票する）
- 「コンテナを起動する検査を持つ担当は同時に 1 本」（`parallel-work.md` の必須ルール 6）の変更
- `/proc/meminfo` が無い環境（macOS など）での測り方。いままでどおり終了コード 3 で「測れない」とし、1 本で進める
- `supervise.py queue --max` の既定の変更
- `concurrency` サブコマンドの判定と出力の変更（閉じたときの測定の表へ写す列が 1 つ増えるだけで、コマンドは変えない）
- 暫定の初期値の実測による調整

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | conductor が実行計画の開始時に `capacity --running 0` を測った | 最初の supervisor を起動する前 | `/proc/meminfo` を読めず終了コード 3 → 起動してよい本数を 1 として扱う（既存） | — |
| E2 | conductor が `oom_kill` の起点と `anon` の起点を実行計画の先頭へ写した | E1 の出力 | cgroup の `anon` を読めず `unknown` → 起点の行に `unknown` と書き、以後は `--anon-baseline-mib` を渡さない（1 本の重さは見込みを使う） | E1 |
| E3 | conductor が `allowed − running` の数だけ supervisor を起動した | E1 か E4 の出力 | 起動の失敗は既存の見直しの契機 3（supervisor が止まった）で扱う | E1 または E4 |
| E4 | conductor が見直しで `capacity --running N --oom-baseline <起点> --anon-baseline-mib <起点>` を測った | 見直しの 5 つの契機 | 終了コード 3 → 1 本として扱う（既存） | E2 |
| E5 | `capacity` が 1 本の重さを `(anon − 起点) ÷ running` から測った | E4 で `running ≥ 1` かつ起点がある | `anon` を読めない・起点が無い・`running` が 0 → 1 本の見込み（`--per-lane-mib`）を使う。差が負 → 下限で抑える | E4 |
| E6 | `capacity` が圧を判定した（PSI、無ければスワップ I/O） | E1 または E4 | どちらも読めない → 圧は判定できないとして `grow` で続け、出力に残す（前提 3） | — |
| E7 | `capacity` が判定の区分（`grow` / `hold` / `shrink`）と `allowed` を出した | E5・E6 と `oom_kill` の比較 | — | E5, E6 |
| E8 | conductor が出力を「測った値」の表へ 1 行写した | E7 | — | E7 |
| E9 | conductor がスプリントを閉じるときに、「測った値」の表の 1 本の実測の最大を「閉じたときの測定」へ写した | すべての行がマージ済み | 実測の列がすべて `—` / `unknown` → `—` と書く | E8 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 1 本の重さ | 1 本が使う `anon` の量。実測（`per_lane_observed_mib`）と、判定に使った値（`per_lane_used_mib`）を分けて出す |
| anon の起点 | 0 本のときの cgroup の `anon`。1 本の重さを測る差の基準 |
| 判定の区分 | `grow`（足してよい）/ `hold`（今の本数を超えて足さない）/ `shrink`（1 本減らす）の 3 つ |

## 受け入れ条件

判定（`capacity`）:

- [ ] PSI の `some avg10` が 0.00・スワップ I/O がほぼ 0・`SwapFree` が 0・`MemAvailable` 5793 MiB・cgroup の上限なしの入力で、`--running 0` が `allowed=2` を出し、`limited_by` に `swap_low` が現れない（依頼の試算の「開始」の行）
- [ ] どの入力でも `limited_by` に `swap_low` が現れず、`swap_free_mib` の値が `allowed` を変えない
- [ ] PSI の `some avg10` が `--psi-some-max` を超える入力で、判定の区分が `hold`、`allowed` が `max(running, 1)`、`limited_by` に `hold_psi` が付く
- [ ] PSI が読めず、`/proc/vmstat` の `pswpin + pswpout` の 2 回の差を間隔の秒数で割った値が `--swap-io-max-pages-per-sec` を超える入力で、判定の区分が `hold`、`allowed` が `max(running, 1)`、`limited_by` に `hold_swap_io` が付く
- [ ] PSI も `/proc/vmstat` も読めない入力で、終了コードが 0、判定の区分が `grow`、出力の圧の判定が `unknown` になる
- [ ] `oom_kill` が起点より増えた入力で、判定の区分が `shrink`、`allowed` が `max(0, running − 1)`、`limited_by` に `oom_kill_increased` が付く。圧が閾値を超えていても `shrink` が優先する
- [ ] `--anon-baseline-mib B` と `--running N`（N ≥ 1）を渡し `anon` が A のとき、`per_lane_observed_mib` が `(A − B) ÷ N`、`per_lane_used_mib` が `max(--per-lane-min-mib, per_lane_observed_mib × --peak-factor)` になる（整数の MiB への端数の扱いは設計が決め、確定仕様に書く）
- [ ] `--anon-baseline-mib` が無い、`--running 0`、または `anon` が読めない入力で、`per_lane_observed_mib` が `unknown`（または `—`）になり、`per_lane_used_mib` が `--per-lane-mib` になる
- [ ] `grow` の `allowed` が `running + min(--max-add, ⌊(min(MemAvailable, cgroup の残り) − --reserve-mib) ÷ per_lane_used_mib⌋)` になり、`--max` を渡したときだけその値で上から抑えられ `limited_by` に `max` が付く
- [ ] `--max` を渡さない入力で、空きが大きければ `allowed` が 4 以上になりうる（例: 空き 12 GiB・`--running 3`・1 本の重さ 1843 MiB で `allowed=5`）。足す数が `--max-add` で抑えられたとき `limited_by` に `max_add` が付く
- [ ] 依頼の試算の 3 行（開始・1 回目・2 回目）を入力にした検査が、それぞれ `allowed` 2・3・3 と、表の「決めた条件」を出す
- [ ] `grow` で足せる数が 0 で `running` が 0 のとき、`allowed` が 1 になり `limited_by` に `floor` が付く（既存の下限を保つ）
- [ ] `--swap-free-min-pct` を渡すと、終了コード 0 のまま標準エラーに廃止の知らせが 1 行出て、判定は渡さないときと同じになる
- [ ] 予備メモリ・0 本のときの見込み・山への余裕・1 本の下限・PSI の閾値・スワップ I/O の閾値・間隔・足す数の初期値を持つのは `parallel-measure.py` の定数だけで、`execution-plan.md` と `parallel-work.md` に数値が現れない
- [ ] PSI・`/proc/vmstat`・cgroup の位置は引数で差し替えられ、検査が実ホストの `/proc` を読まずに 3 つの判定の区分を作れる。スワップ I/O の間隔は検査で 0 秒にできる

出力:

- [ ] 出力に既存のキーがすべて同じ順で残り、末尾に `cgroup_anon_mib` / `per_lane_observed_mib` / `per_lane_used_mib` / 判定の区分 / 圧の判定に使った値（PSI の `some avg10` またはスワップ I/O、読めなければ `unknown`）が足される
- [ ] `/proc/meminfo` を読めない・`MemAvailable` が無い入力で、終了コード 3・標準出力が空のまま（既存の振る舞い）

共通の層:

- [ ] `procs.cgroup_memory` が `memory.stat` の `anon` を返し、読めなければ `None` を返す。既存の呼び出し元（`limit` / `current` / `oom_kill` / `unlimited` を読む側）の振る舞いが変わらない

手順と仕様:

- [ ] `execution-plan.md` の文書の形で、先頭に「anon の起点」が「oom_kill の起点」と並び、「測った値」の表に列 `anon` / `1 本の実測` / `1 本の見込み` が増え、出力のキーと列の対応表に 3 行が増える
- [ ] `execution-plan.md` の `capacity` の呼び方に `--anon-baseline-mib <anon の起点>` が加わり、上書きできる引数の一覧から `--swap-free-min-pct` が消えて新しい引数が載る
- [ ] `execution-plan.md` の「閉じたときの測定」の表に「1 本の実測の最大」の列があり、閉じる手順がその値の写し方を書いている
- [ ] 確定仕様の初期値の表・引数・出力・式が新しい判定に書き換わり、「予備メモリに既に動いている本体と担当を含めない」「総本数の上限は測定が持つ」が文として書かれている

検査と退行:

- [ ] `test_parallel_measure.py` の 2026-09-17 の例（空き 9742 MiB）の期待値が新しい判定に更新されている
- [ ] 全体テスト（`uv run --frozen --project . --all-extras pytest . -q -n 4`）が通る
- [ ] `concurrency` サブコマンドの検査が変更なしで通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | PSI が読めるホストでは `capacity` が待たずに返る。PSI が無いホストだけ、スワップ I/O の標本の間隔（初期値 2 秒）だけ待つ |
| 運用・保守性 | 「測った値」の表の 1 行から、判定の区分・効いた条件・1 本の重さが読める。初期値はスクリプトの定数だけが持つ |
| 移行性 | 既存の実行計画（開いているもの）は「anon の起点」が無いまま読める。起点が無ければ 1 本の見込みを使って動く。廃止した `--swap-free-min-pct` を渡す既存の呼び出しも終了コード 0 で動く |
| システム環境 | Linux（cgroup v2 / PSI のあるカーネル）で全機能、PSI の無いカーネルではスワップ I/O、`/proc/meminfo` の無い環境では終了コード 3（既存） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`capacity` の引数（`--swap-free-min-pct` の廃止、`--anon-baseline-mib` などの追加、`--max` の既定の削除）・出力のキー（末尾へ追加）・`limited_by` の値（`swap_low` の廃止、`hold_psi` / `hold_swap_io` / `max_add` の追加）。既存の呼び出しは終了コード 0 のまま動くため互換は保つ（`limited_by` の `swap_low` を読む側は手順の文書だけ） |
| データ | 実行計画（コミットしないファイル）の先頭に 1 行、「測った値」の表に 3 列、「閉じたときの測定」の表に 1 列が増える。既存の実行計画の書き直しは要らない |
| 既存の振る舞い | 圧の無いホストで、起動してよい本数が増える（例: 2026-09-19 のホストで 1 → 2、見直しで 3） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `python3 plugins/ndf/scripts/parallel-measure.py capacity --running 0` を実ホストで打ち、終了コード 0 と出力のキーを確かめる |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests/test_parallel_measure.py -q` と全体の `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析・型検査 | `python3 scripts/check-skill-frontmatter.py`、`python3 plugins/ndf/scripts/instructions-check.py --root .`、リポジトリの CI の必須チェック |
| 手動確認 | 次のスプリントの実行計画で、PSI が低いホストの開始時の測定が 2 本以上を返し、「測った値」の表の 1 本の実測が埋まることを conductor の記録で確かめる（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 判定と定数は `plugins/ndf/scripts/parallel-measure.py`、cgroup の読み取りは `plugins/ndf/scripts/lib/procs.py`（`scripts/script-structure-allow/` の許可の範囲）、手順は `issue-plan-strategy` の `references/`、確定仕様は `docs/specifications/`。実験版の置き場（`scripts/experimental/`）には置かない（既定で動く測定を変えるため） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」と、外部コマンド・入力の形を書く前に実行して確かめる規約。`/proc` の読み取りは procs か既存の `read_meminfo` の形に揃える |
| テスト戦略 | 判定の式は `test_parallel_measure.py` の CLI 実行の検査で、入力ファイル（meminfo / PSI / vmstat / cgroup の雛形）を差し替えて確かめる。`.md` の文言を照合する検査は書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存の検査の実行、初期値をスクリプトの定数だけに置く、手順の文書と確定仕様を同じ変更で揃える |
| 確認してから行う | `concurrency` の出力の変更、`supervise.py` の既定の変更、`parallel-work.md` の必須ルールの変更 |
| 行わない | 起動を拒否する仕組みの追加、複数の conductor の競合の対処、暫定の初期値の実測による調整 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 新しい引数と出力のキーの正式な名前（判定の区分・圧の判定のキー、PSI と vmstat の差し替え口の引数） | 設計（`design`） | 設計 PR |
| 1 本の重さの端数（切り捨て / 切り上げ）と `anon − 起点` が負のときの扱いの文言 | 設計（`design`） | 設計 PR |
| 暫定の初期値の見直し | 「測った値」の表の 1 本の実測が 1 スプリント分溜まった後の振り返り | 次のスプリントの振り返り |
