#!/usr/bin/env bash
# NDF plugin: ロックのディレクトリを関門にした排他（#293）。
#
# **この手順はリポジトリの中でここ 1 箇所にある。** 読み込む側は台帳の
# `worktree-common.sh` と工程の控えの `workflow-common.sh` の 2 つで、どちらも自分の
# 位置からの相対でこのファイルを指し、既存の名前へ結び直す委譲だけを置く。
#
# **このファイルは読み込まれるだけで、単独では実行しない。**
# **標準出力へは何も書かない。** 読み込む側はいずれも hook かコマンド置換の中で動き、
# 出力を持つと hook の返す JSON へ混ざる。
#
# `flock` を持たないホストがある。`flock` は使わない。使えるホストと使えないホストが
# 混じると、同じ資源に対して別々の仕組みが動き、互いを見落とす。
#
# **関門は 2 段である。** 1 段目のディレクトリの作成はロックの位置を押さえるだけで、
# 持ち主を 1 つに決めない。`mkdir` コマンドは同じ名前で同時に成功することがある
# （uutils coreutils 0.8.0 の実測。overlayfs と ext4 の両方で出る）。持ち主を決めるのは
# 2 段目の `( set -C; : >"$dir/held" )` で、シェル自身の `open(O_CREAT|O_EXCL)` が
# 関門になる。外部コマンドを起動しないため、`mkdir` の実装に左右されない（#297）。

# 待ちの刻み。小数を受けない sleep のホストでは 1 秒へ落ちる。
_ndf_lock_sleep() {
  sleep 0.1 2>/dev/null || sleep 1
}

# 持ち主が消えたまま残るロックを捨ててよいと見なすまでの分数。
# `mkdir` に成功した直後、印を書く前に落ちた持ち主を救うために使う。
NDF_LOCK_STALE_MINUTES=5

# 2 段目の関門。**`set -C` は部分シェルの中だけで張る。** 裸で書くと呼び出し側の
# シェルの `$-` に `C` が残り、以後の上書きの向き先が変わる。元へ戻す処理を書かずに
# 同じ結果になり、費用も 1 段目の `mkdir` コマンドより小さい（#293 の実測）。
#
# 成否は終了コードの値では見ない。既存ファイルへの書き込みの失敗は `bash` が 1、
# `dash` が 2 を返す。
_ndf_lock_hold() {
  ( set -C; : >"$1/held" ) 2>/dev/null
}

# 判定したものと同じロックであることを確かめてから取り除く。
# 単に `rm -rf` すると、先に捨てて取り直した別のプロセスのロックを壊す。
#
# **確かめることと取り除くことを 1 つの関門の中で行う。** 確かめた後に別の担当が同じ
# ロックを捨てて取り直せると、取り除く側は確かめたものとは別の、持ち主が臨界区間に
# いるロックを取り除く。取り除きの関門を通れるのは 1 つだけで、持ち主は既に消えていて
# 自ら離すこともないため、関門の中ではロックが入れ替わらない（#297）。
#
# **名前の付け替えで外へ出してから確かめる形は採らない。** 外へ出している間はロックの
# 名前が空くため、持ち主が臨界区間にいるまま別の担当が関門を通る。戻すより先に取られると
# 戻せず、持ち主のロックはそのまま捨てられる（#297）。
#
# 関門はロックの中に置く。取り除きに成功すればロックごと消えるため、跡が残らない。
_ndf_lock_discard() {
  local dir="$1" seen="$2" mark="$1/discard"
  if ! ( set -C; : >"$mark" ) 2>/dev/null; then
    # 取り除きの途中で落ちた担当が残した関門は、古ければ捨てる。残したままにすると、
    # そのロックはだれにも取り除けなくなる。
    find "$mark" -maxdepth 0 -mmin "+$NDF_LOCK_STALE_MINUTES" 2>/dev/null | grep -q . \
      && rm -f "$mark" 2>/dev/null
    return 1
  fi
  if [ "$(cat "$dir/token" 2>/dev/null)" = "$seen" ]; then
    rm -rf "$dir" 2>/dev/null
    return 0
  fi
  # 別物だった。取り除かず、関門だけを外す。
  rm -f "$mark" 2>/dev/null
  return 1
}

# ロックが捨ててよい状態かを見る。**判定は 1 つのロックについて行う。**
# 見始めたときの印を `seen` で受け取り、判定の間に持ち主が替わっていれば捨てない。
#
# **`kill -0` が偽になるのは、持ち主が離れたときにも起きる。** 離れた後に別の担当が
# 取り直していれば、いま置かれているのは別のロックである。番号だけを見て捨ててよいと
# 読むと、生きているロックを外すことになる（#297）。
_ndf_lock_is_stale() {
  local dir="$1" seen="${2-}" owner
  owner=$(cat "$dir/pid" 2>/dev/null)
  if [ -n "$owner" ]; then
    kill -0 "$owner" 2>/dev/null && return 1
    [ "$(cat "$dir/token" 2>/dev/null)" = "$seen" ] || return 1
    [ "$(cat "$dir/pid" 2>/dev/null)" = "$owner" ] || return 1
    return 0
  fi
  # 印が無いロックは、作った直後に落ちた可能性がある。古ければ捨ててよい。
  find "$dir" -maxdepth 0 -mmin "+$NDF_LOCK_STALE_MINUTES" 2>/dev/null | grep -q . && return 0
  return 1
}

# ロックを取る。取れなければ 1 を返す。第 2 引数は待ちの上限の秒数（既定 5）。
#
# **`mkdir` の成功だけでは持ち主を 1 つに絞れない。** 2 段目の関門を通った 1 つだけが
# 印と持ち主の番号を書く。印を書いてから読み直す手は採らない。長さの違う印が同じ
# ファイルへ同時に書かれると、どの担当のものとも一致しない値が残り、作成に成功した
# 全員が競り負ける（#308）。
ndf_lock_acquire() {
  local dir="${1:-}" timeout="${2:-5}" token seen deadline
  [ -n "$dir" ] || return 1
  # ロックの位置にディレクトリ以外があれば、ロックとして成立しない。取り除く。
  if [ -e "$dir" ] && [ ! -d "$dir" ]; then rm -f "$dir" 2>/dev/null; fi
  # 印は識別のためだけに使う。持ち主の決定には使わないため、桁をそろえる必要はない。
  token="$$-$(date +%s 2>/dev/null)-${RANDOM:-0}"
  # 上限は実時間で測る。刻みが 0.1 秒か 1 秒かで待ち時間が 10 倍変わるため、
  # 回数で数えない。
  deadline=$(( $(date +%s) + timeout ))
  while :; do
    if mkdir "$dir" 2>/dev/null; then
      if _ndf_lock_hold "$dir"; then
        printf '%s\n' "$token" >"$dir/token" 2>/dev/null
        printf '%s\n' "$$" >"$dir/pid" 2>/dev/null
        return 0
      fi
      # 競り負けた。**ロックは消さない。** 相手が持っているため、待つ側へ回る。
    fi
    seen=$(cat "$dir/token" 2>/dev/null)
    # **取り除けたときだけ、間を置かずに取りに戻る。** 取り除けなかったときも戻すと、
    # 上限の判定に着かないまま回り続ける。取り除きの関門は他の担当が持っていることが
    # あり、その間は待つ側へ回る。
    if _ndf_lock_is_stale "$dir" "$seen" && _ndf_lock_discard "$dir" "$seen"; then
      continue
    fi
    [ "$(date +%s)" -ge "$deadline" ] && return 1
    _ndf_lock_sleep
  done
}

ndf_lock_release() {
  [ -n "${1:-}" ] || return 0
  rm -rf "$1" 2>/dev/null
  return 0
}

# 生きている持ち主がロックを握っていれば 0 を返す。
ndf_lock_is_held() {
  local dir="${1:-}" owner
  [ -n "$dir" ] && [ -d "$dir" ] || return 1
  owner=$(cat "$dir/pid" 2>/dev/null)
  [ -n "$owner" ] || return 0
  kill -0 "$owner" 2>/dev/null
}

# **`set -C` が効かないシェルで読み込まれたときは、取得できなかったものとして扱う。**
# 2 段目の関門を張れないと、1 段目だけでは持ち主を 1 つに絞れない。排他が成り立たない
# まま書き込みへ進める代わりに、書き込みを行わない側へ倒す。読み込む側の 3 つの
# 呼び出し元は、いずれも「取得できなかった」ときの分岐を持つ（#293 の決定 3）。
if ! ( set -C; case "$-" in *C*) exit 0 ;; esac; exit 1 ); then
  ndf_lock_acquire() { return 1; }
fi
