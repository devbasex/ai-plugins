#!/usr/bin/env bash
# NDF plugin: 作業ツリーごとにテスト環境を分けて立てる。
#
#   env <作業ツリー>                       環境名・スロット・ポートを出力し、台帳へ記録する
#   tag [作業ツリー]                       基準のタグを、データ構造を定める資産の内容から計算する
#   bake --tag <値>                        基準を作る
#   up <作業ツリー> [--profile <名前>] [--tag <値>]  起動する
#   test <作業ツリー> --kind <種類> [--out <パス>]   宣言の実行コマンドを走らせる
#   stop <作業ツリー>                      止める。データは残す
#   down <作業ツリー> [--volumes]          破棄し、割り当てを解放する
#   expose <作業ツリー>                    外部公開する
#   unexpose <作業ツリー>                  公開を閉じる
#   reap --idle <時間>                     使われていないテスト環境を止める
#
# 終了コードは 0 を「処理が完了した」、1 を「処理できなかった」、2 を「対象外」に
# 割り当てる。`test` だけは実行したコマンドの終了コードをそのまま返す。
# **テストの成否を包み隠さないため**である。
#
# 宣言ファイル (.ndf/localenv.json) が無い、または testenv の宣言が無いリポジトリ
# では、すべてのサブコマンドが何も出力せず終了コード 0 で終わる。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/worktree-common.sh
. "$SCRIPT_DIR/lib/worktree-common.sh" 2>/dev/null || exit 0

command -v jq >/dev/null 2>&1 || exit 0
command -v git >/dev/null 2>&1 || exit 0

SUBCOMMAND="${1:-}"
# サブコマンドの 1 語だけを外す。残りは対象とオプションとして読む。
[ "$#" -gt 0 ] && shift

TARGET=""
PROFILE=""
KIND=""
OUT=""
TAG=""
IDLE=""
WITH_VOLUMES=0

# 値を要するオプションは、値が無いまま末尾へ来ることがある。その場合に
# `shift 2` は失敗し、ループが同じ引数を読み続ける。値の有無を先に見る。
need_value() {
  if [ "$#" -lt 2 ]; then
    printf '%s に値が要ります\n' "$1" >&2
    exit 1
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile) need_value "$@"; PROFILE="$2"; shift 2 ;;
    --kind) need_value "$@"; KIND="$2"; shift 2 ;;
    --out) need_value "$@"; OUT="$2"; shift 2 ;;
    --tag) need_value "$@"; TAG="$2"; shift 2 ;;
    --idle) need_value "$@"; IDLE="$2"; shift 2 ;;
    --volumes) WITH_VOLUMES=1; shift ;;
    --) shift ;;
    -*) printf '知らないオプションです: %s\n' "$1" >&2; exit 1 ;;
    *) [ -n "$TARGET" ] || TARGET="$1"; shift ;;
  esac
done

MAIN_DIR=$(wt_main_dir) || exit 0
DECLARATION=$(wt_declaration "$MAIN_DIR") || exit 0

decl_get() { printf '%s' "$DECLARATION" | jq -r "$1" 2>/dev/null; }
decl_raw() { printf '%s' "$DECLARATION" | jq -c "$1" 2>/dev/null; }

# testenv の宣言が無いリポジトリでは何もしない。
printf '%s' "$DECLARATION" | jq -e '(.testenv | type) == "object"' >/dev/null 2>&1 || exit 0

[ -n "$TARGET" ] || TARGET=$(pwd -P)
TARGET=$(wt_normalize_path "$TARGET" "$(pwd -P)")

target_branch() { git -C "$TARGET" symbolic-ref --short -q HEAD 2>/dev/null; }
registry() { wt_registry_path "$MAIN_DIR"; }

# 実行中の作業ツリーが握るロックの位置。
inuse_lock() { printf '%s/%s.inuse.d\n' "$(dirname "$(registry)")" "$1"; }

# --- tag --------------------------------------------------------------------

# 基準のタグは、データ構造を定める資産の内容から決める。
# 同じ内容なら同じ値になるため、焼き直しが要るかを内容で判定できる。
do_tag() {
  local -a paths=()
  _wt_read_lines < <(decl_get '.testenv.golden_tag_paths // [] | .[]')
  paths=("${WT_LINES[@]+"${WT_LINES[@]}"}")
  [ "${#paths[@]}" -gt 0 ] || return 2

  local digest
  digest=$(git -C "$TARGET" ls-tree -r HEAD -- "${paths[@]}" 2>/dev/null \
    | (sha1sum 2>/dev/null || shasum 2>/dev/null) | cut -c1-12)
  [ -n "$digest" ] || return 1
  printf '%s\n' "$digest"
}

# --- env --------------------------------------------------------------------

do_env() {
  local branch environment slot band_low band_high ports role role_number port
  branch=$(target_branch) || true
  [ -n "$branch" ] || { printf '作業ツリーのブランチを取れません: %s\n' "$TARGET" >&2; return 1; }

  environment=$(wt_env_name "$MAIN_DIR" "$branch") || return 1
  # この呼び出しで新しく取ったかを覚えておく。採番に失敗したときに、
  # 元からあった割り当てまで解放しないため。
  local had_slot=0
  wt_slot_of "$MAIN_DIR" "$TARGET" >/dev/null 2>&1 && had_slot=1
  slot=$(wt_slot_acquire "$MAIN_DIR" "$TARGET" "$branch" "$environment") || {
    printf '空きスロットがありません（上限 %s）\n' "$((WT_SLOT_MAX + 1))" >&2
    return 1
  }

  band_low=$(decl_get '.testenv.port_band[0] // empty')
  band_high=$(decl_get '.testenv.port_band[1] // empty')
  ports="{}"
  if [ -n "$band_low" ]; then
    while IFS=$'\t' read -r role role_number; do
      [ -n "$role" ] || continue
      port=$(wt_port_for "$band_low" "$slot" "$role_number") || continue
      # 帯を出た番号は、他の用途と衝突する。黙って使わない。
      if [ -n "$band_high" ] && [ "$port" -gt "$band_high" ]; then
        printf '%s\n' "採番が帯を超えました（役割 $role のポート $port が上限 $band_high を超える）" >&2
        # 失敗した呼び出しがスロットを握ったままにしない。
        [ "$had_slot" = 0 ] && wt_slot_release "$MAIN_DIR" "$TARGET"
        return 1
      fi
      ports=$(printf '%s' "$ports" | jq --arg r "$role" --argjson p "$port" '. + {($r): $p}')
    done < <(decl_get '.testenv.port_roles // {} | to_entries[] | "\(.key)\t\(.value)"')
    wt_slot_set_ports "$MAIN_DIR" "$TARGET" "$ports"
  fi

  jq -n --arg environment "$environment" --argjson slot "$slot" \
        --arg worktree "$TARGET" --arg branch "$branch" --argjson ports "$ports" \
    '{environment: $environment, slot: $slot, worktree: $worktree, branch: $branch, ports: $ports}'
}

# 起動と停止で使う共通の値を変数へ入れる。
load_assignment() {
  ENVIRONMENT=""
  SLOT=""
  local row
  row=$(wt_registry_visible "$(registry)" \
    | jq -c --arg wt "$TARGET" \
      '[.assignments[] | select(.released_at == null and .worktree == $wt)] | last' 2>/dev/null)
  [ -n "$row" ] && [ "$row" != "null" ] || return 1
  ENVIRONMENT=$(printf '%s' "$row" | jq -r '.environment')
  SLOT=$(printf '%s' "$row" | jq -r '.slot')
}

compose() {
  local -a files=()
  _wt_read_lines < <(decl_get '.localenv.compose_files // [] | .[]')
  local f
  for f in "${WT_LINES[@]+"${WT_LINES[@]}"}"; do
    [ -n "$f" ] || continue
    files+=(-f "$TARGET/$f")
  done
  [ "${#files[@]}" -gt 0 ] || return 2
  docker compose -p "$ENVIRONMENT" "${files[@]}" "$@"
}

# --- bake -------------------------------------------------------------------

do_bake() {
  [ -n "$TAG" ] || { printf '--tag が要ります\n' >&2; return 1; }
  command -v docker >/dev/null 2>&1 || { printf 'コンテナ実行系が見つかりません\n' >&2; return 1; }

  # 足りないものだけを作る。前回の bake が途中で落ちていると、一部だけが
  # 存在する。1 件目の存在で打ち切ると、残りが作られないまま先へ進む。
  local source_volume golden created=0 existing=0
  while IFS=$'\t' read -r source_volume golden; do
    [ -n "$source_volume" ] || continue
    if docker volume inspect "${golden}-${TAG}" >/dev/null 2>&1; then
      existing=$((existing + 1))
      continue
    fi
    docker volume create "${golden}-${TAG}" >/dev/null || return 1
    # 途中で落ちると中身が空のまま残り、次回は「既にある」として飛ばされる。
    # 失敗したら作りかけを消す。
    if ! docker run --rm -v "${source_volume}:/from:ro" -v "${golden}-${TAG}:/to" \
      alpine sh -c 'cd /from && cp -a . /to'; then
      docker volume rm "${golden}-${TAG}" >/dev/null 2>&1
      printf '%s\n' "基準を作れませんでした: ${golden}-${TAG}" >&2
      return 1
    fi
    printf '基準を作りました: %s\n' "${golden}-${TAG}"
    created=$((created + 1))
  done < <(decl_get '.testenv.golden_volumes // {} | to_entries[] | "\(.key)\t\(.value)"')

  if [ "$created" = 0 ] && [ "$existing" -gt 0 ]; then
    printf '同じタグの基準が既にあります（%s 件）\n' "$existing"
    return 2
  fi
}

# --- up / stop / down -------------------------------------------------------

do_up() {
  load_assignment || { do_env >/dev/null || return 1; load_assignment || return 1; }
  command -v docker >/dev/null 2>&1 || { printf 'コンテナ実行系が見つかりません\n' >&2; return 1; }

  [ -n "$TAG" ] || TAG=$(do_tag) || TAG=""
  if [ -n "$TAG" ]; then
    wt_registry_update "$(registry)" '
      .assignments |= map(
        if .worktree == $wt and .released_at == null then .golden_tag = $tag else . end
      )' --arg wt "$TARGET" --arg tag "$TAG"
  fi

  local -a services=()
  if [ -n "$PROFILE" ]; then
    _wt_read_lines < <(printf '%s' "$DECLARATION" | jq -r --arg p "$PROFILE" '.testenv.profiles[$p] // [] | .[]' 2>/dev/null)
    services=("${WT_LINES[@]+"${WT_LINES[@]}"}")
  fi
  wt_slot_touch "$MAIN_DIR" "$TARGET"
  # 定義に無いコンテナを削除する指定は付けない。稼働中のプロジェクトに定義外の
  # コンテナが属していることがあり、付けると削除される。
  compose up -d "${services[@]+"${services[@]}"}"
}

do_stop() {
  load_assignment || return 0
  command -v docker >/dev/null 2>&1 || return 0
  compose stop
}

do_down() {
  load_assignment || return 0
  if command -v docker >/dev/null 2>&1; then
    if [ "$WITH_VOLUMES" = 1 ]; then
      compose down --volumes
    else
      compose down
    fi
  fi
  wt_slot_release "$MAIN_DIR" "$TARGET"
}

# --- test -------------------------------------------------------------------

# 証跡の置き場所を、その作業ツリー限りの除外設定へ登録する。
exclude_evidence() {
  local git_dir exclude
  git_dir=$(git -C "$TARGET" rev-parse --absolute-git-dir 2>/dev/null) || return 0
  exclude="$git_dir/info/exclude"
  mkdir -p "$(dirname "$exclude")" 2>/dev/null
  grep -qx '.ndf-evidence/' "$exclude" 2>/dev/null && return 0
  printf '.ndf-evidence/\n' >>"$exclude" 2>/dev/null || true
}

do_test() {
  [ -n "$KIND" ] || { printf '--kind が要ります\n' >&2; return 1; }
  local run base_url_env out_env
  run=$(printf '%s' "$DECLARATION" | jq -r --arg k "$KIND" '.testenv.test_kinds[$k].run // empty' 2>/dev/null)
  # 種類の宣言が無いリポジトリでは何もしない（受け入れ条件 39）。
  [ -n "$run" ] || return 0

  load_assignment || { do_env >/dev/null || return 1; load_assignment || return 1; }

  local -a env_pairs=()
  # 初期化を抑止する指定を渡す。渡さないと最初のテストが全体を作り直す構成がある。
  local key value
  while IFS=$'\t' read -r key value; do
    [ -n "$key" ] || continue
    env_pairs+=("$key=$value")
  done < <(printf '%s' "$DECLARATION" | jq -r --arg k "$KIND" \
    '.testenv.test_kinds[$k].skip_reset // {} | to_entries[] | "\(.key)\t\(.value)"' 2>/dev/null)

  base_url_env=$(printf '%s' "$DECLARATION" | jq -r --arg k "$KIND" '.testenv.test_kinds[$k].base_url_env // empty' 2>/dev/null)
  if [ -n "$base_url_env" ]; then
    # 入口の役割名は宣言で決める。`http` 以外の名前を使うリポジトリがある。
    local port_role http_port
    port_role=$(printf '%s' "$DECLARATION" | jq -r --arg k "$KIND" '.testenv.test_kinds[$k].port_role // "http"' 2>/dev/null)
    http_port=$(wt_registry_visible "$(registry)" \
      | jq -r --arg wt "$TARGET" --arg role "$port_role" \
        '[.assignments[] | select(.released_at == null and .worktree == $wt)] | last | .ports[$role] // empty')
    [ -n "$http_port" ] && env_pairs+=("$base_url_env=http://localhost:$http_port")
  fi

  out_env=$(printf '%s' "$DECLARATION" | jq -r --arg k "$KIND" '.testenv.test_kinds[$k].out_env // empty' 2>/dev/null)
  if [ -n "$out_env" ]; then
    # 証跡は作業ツリー配下へ固定する。共有の保管先へは送らない。
    # 外から渡された置き場所も、作業ツリーの中に収まるかを実体で確かめる。
    [ -n "$OUT" ] || OUT="$TARGET/.ndf-evidence/$ENVIRONMENT-$(date -u +%Y%m%dT%H%M%SZ)"
    OUT=$(wt_normalize_path "$OUT" "$TARGET")
    case "$OUT" in
      "$TARGET"/*) ;;
      *)
        printf '%s\n' "証跡の置き場所が作業ツリーの外を指します: $OUT" >&2
        return 1
        ;;
    esac
    mkdir -p "$OUT" 2>/dev/null
    # 追跡対象に入ると差分が埋まる。その作業ツリー限りの除外へ登録する
    # （リポジトリの .gitignore は触らない）。
    exclude_evidence
    env_pairs+=("$out_env=$OUT")
  fi

  wt_slot_touch "$MAIN_DIR" "$TARGET"

  # 実行中は reap の対象から外れるよう、ロックを握ったまま走らせる。
  # `flock` の有無で判定が変わらないよう、印はディレクトリで持つ。
  local lock rc
  lock=$(inuse_lock "$ENVIRONMENT")
  mkdir -p "$(dirname "$lock")" 2>/dev/null
  wt_lock_acquire "$lock" 5 || {
    printf '%s\n' "同じテスト環境で別の実行が動いています: $ENVIRONMENT" >&2
    return 1
  }
  (cd "$TARGET" && env "${env_pairs[@]+"${env_pairs[@]}"}" sh -c "$run")
  rc=$?
  wt_lock_release "$lock"
  wt_slot_touch "$MAIN_DIR" "$TARGET"
  return "$rc"
}

# --- expose / unexpose ------------------------------------------------------

do_unexpose() {
  local close_command url host
  url=$(wt_registry_visible "$(registry)" \
    | jq -r --arg wt "$TARGET" '[.assignments[] | select(.worktree == $wt and (.expose // {}).closed_at == null)] | last | .expose.url // empty')

  close_command=$(decl_get '.testenv.expose.close_command // empty')
  if [ -n "$close_command" ] && [ -n "$url" ]; then
    # 開けるときと同じ値を渡す。URL だけでは、環境名やスロットを資源の名前に
    # 使っている構成で後片付けの対象を特定できない。
    load_assignment || true
    host=${url#https://}
    host=${host#http://}
    (cd "$TARGET" && env "NDF_EXPOSE_URL=$url" "NDF_EXPOSE_HOST=$host" \
      "NDF_EXPOSE_ENVIRONMENT=${ENVIRONMENT:-}" "NDF_EXPOSE_SLOT=${SLOT:-}" \
      sh -c "$close_command") || true
  fi

  wt_registry_update "$(registry)" '
    .assignments |= map(
      if .worktree == $wt and .expose != null and .expose.closed_at == null
      then .expose.closed_at = (now | todate) else . end
    )' --arg wt "$TARGET"
}

do_expose() {
  local enabled public_tag base_domain ttl loaded_tag host
  enabled=$(decl_get '.testenv.expose.enabled // false')
  if [ "$enabled" != "true" ]; then
    printf '拒否: testenv.expose.enabled が有効ではありません\n' >&2
    return 1
  fi

  public_tag=$(decl_get '.testenv.expose.public_tag // empty')
  base_domain=$(decl_get '.testenv.expose.base_domain // empty')
  if [ -z "$public_tag" ] || [ -z "$base_domain" ]; then
    printf '拒否: testenv.expose.public_tag と base_domain が要ります\n' >&2
    return 1
  fi

  load_assignment || { printf '拒否: 割り当てがありません\n' >&2; return 1; }

  loaded_tag=$(wt_registry_visible "$(registry)" \
    | jq -r --arg wt "$TARGET" '[.assignments[] | select(.released_at == null and .worktree == $wt)] | last | .golden_tag // empty')
  if [ "$loaded_tag" != "$public_tag" ]; then
    printf '拒否: 載っている基準（%s）が公開を許す基準（%s）と一致しません\n' \
      "${loaded_tag:-なし}" "$public_tag" >&2
    return 1
  fi

  ttl=$(decl_get '.testenv.expose.ttl // "8h"')
  host="wt${SLOT}.${base_domain}"

  # 実際に口を開ける手段はリポジトリごとに違う（共有の入口の設定、折り返しの
  # 中継など）。宣言が無ければ、記録だけ残して公開したことにはしない。
  local open_command
  open_command=$(decl_get '.testenv.expose.open_command // empty')
  if [ -z "$open_command" ]; then
    printf '%s\n' "公開の手段が宣言されていません（testenv.expose.open_command）" >&2
    return 2
  fi

  # 折り返しを使う公開は先着 1 本で排他する。**判定を排他区間の中で行う。**
  # 区間の外で数えると、同時に走った 2 本が両方とも通り抜ける。
  wt_registry_update "$(registry)" '
    if ([.assignments[] | select(.expose != null and .expose.closed_at == null and .worktree != $wt)] | length) > 0
    then .
    else
      .assignments |= map(
        if .worktree == $wt and .released_at == null then
          .expose = {url: $url, ttl: $ttl, opened_at: (now | todate), closed_at: null}
        else . end
      )
    end' --arg wt "$TARGET" --arg url "https://$host" --arg ttl "$ttl" || return 1

  local opened
  opened=$(wt_registry_visible "$(registry)" \
    | jq -r --arg wt "$TARGET" '[.assignments[] | select(.released_at == null and .worktree == $wt)] | last | .expose.url // empty')
  if [ -z "$opened" ]; then
    printf '拒否: 別のテスト環境が公開中です（同時に開けるのは 1 本）\n' >&2
    return 1
  fi

  # 記録を先に置くのは、先着 1 本の関門を通ったことを示すため。口を開けられ
  # なければ記録を戻す。残すと、次の公開が「別が公開中」で拒まれ続ける。
  if ! (cd "$TARGET" && env "NDF_EXPOSE_URL=$opened" "NDF_EXPOSE_HOST=$host" \
        "NDF_EXPOSE_ENVIRONMENT=$ENVIRONMENT" "NDF_EXPOSE_SLOT=$SLOT" \
        sh -c "$open_command"); then
    do_unexpose >/dev/null 2>&1
    printf '%s\n' "公開の手段が失敗しました。記録を戻しました" >&2
    return 1
  fi
  printf '%s\n' "$opened"
}


# --- reap -------------------------------------------------------------------

do_reap() {
  local idle_seconds
  idle_seconds=$(wt_duration_seconds "$IDLE") || {
    printf '%s\n' '--idle に期間を指定してください（例: 45m）' >&2
    return 1
  }
  command -v docker >/dev/null 2>&1 || return 0

  local worktree environment lock
  while IFS=$'\t' read -r worktree environment; do
    [ -n "$worktree" ] || continue
    TARGET="$worktree"
    ENVIRONMENT="$environment"

    # 実行中の作業ツリーはロックを握っている。握られていれば対象から外す。
    lock=$(inuse_lock "$environment")
    if wt_lock_is_held "$lock"; then
      continue
    fi

    # 起動していないものは止める必要がない。
    if [ -z "$(docker ps -q --filter "label=com.docker.compose.project=$environment" 2>/dev/null)" ]; then
      continue
    fi

    printf '停止します: %s（%s）\n' "$environment" "$worktree"
    compose stop
  done < <(wt_registry_visible "$(registry)" \
    | jq -r --argjson idle "$idle_seconds" '
      [.assignments[]
       | select(.released_at == null)
       | select(((.last_used_at // .assigned_at) | fromdateiso8601) < (now - $idle))]
      | group_by(.worktree) | map(last) | .[]
      | "\(.worktree)\t\(.environment)"')
}

# --- 入口 -------------------------------------------------------------------

case "$SUBCOMMAND" in
  env) do_env ;;
  tag) do_tag ;;
  bake) do_bake ;;
  up) do_up ;;
  test) do_test ;;
  stop) do_stop ;;
  down) do_down ;;
  expose) do_expose ;;
  unexpose) do_unexpose ;;
  reap) do_reap ;;
  *)
    printf '使い方: worktree-testenv.sh <env|tag|bake|up|test|stop|down|expose|unexpose|reap> [対象] [オプション]\n' >&2
    exit 1
    ;;
esac
