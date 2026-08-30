#!/usr/bin/env bash
# NDF plugin: 作業ツリーでのローカル動作検証を支える。
#
#   setup <作業ツリー>        宣言に従って設定と依存物を複製する
#   verify [作業ツリー]       環境へ載っているコードと対象が一致するかを照合する
#   healthcheck [作業ツリー]  照合し、一致したときだけ宣言の動作確認コマンドを実行する
#   aim <作業ツリー>          環境が指すコードを対象へ向ける
#   mode [作業ツリー]         変更の一覧から相乗りと分離のどちらかを提示する
#
# 終了コードは 0 を「処理が完了した」、1 を「処理できなかった」、2 を「対象外」に
# 割り当てる。`verify` の 3 状態だけは意味が異なり、0 一致 / 1 不一致 /
# 2 未起動または適用外である。**「未起動」と「不一致」を同じ値にしない。** 環境が
# 動いていないことと、別のコードが載っていることは、次の手が違う。
#
# 宣言ファイル (.ndf/localenv.json) が無いリポジトリでは、すべてのサブコマンドが
# 何も出力せず終了コード 0 で終わる (詳細設計 06 の決定 9)。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/worktree-common.sh
. "$SCRIPT_DIR/lib/worktree-common.sh" 2>/dev/null || exit 0

SUBCOMMAND="${1:-}"
TARGET="${2:-}"

command -v jq >/dev/null 2>&1 || exit 0
command -v git >/dev/null 2>&1 || exit 0

MAIN_DIR=$(wt_main_dir) || exit 0
DECLARATION=$(wt_declaration "$MAIN_DIR") || exit 0

decl_get() { printf '%s' "$DECLARATION" | jq -r "$1" 2>/dev/null; }

# compose 以外の実行系は未対応として扱う。
KIND=$(decl_get '.localenv.kind // empty')
[ "$KIND" = "compose" ] || exit 0

# 対象の作業ツリー。省略時は現在地を使う。
if [ -z "$TARGET" ]; then
  TARGET=$(pwd -P)
fi
TARGET=$(wt_normalize_path "$TARGET" "$(pwd -P)")

target_branch() {
  git -C "$TARGET" symbolic-ref --short -q HEAD 2>/dev/null
}

# --- setup ------------------------------------------------------------------

# 宣言に書かれた複製対象が、主ディレクトリと作業ツリーの中に収まるかを見る。
# 宣言の誤りで外側を読み書きしないよう、絶対パスと上位への移動を弾く。
is_safe_relative() {
  case "$1" in
    "" | /*) return 1 ;;
    "." | "..") return 1 ;;
    ../* | */.. | */../*) return 1 ;;
    "~"*) return 1 ;;
  esac
  return 0
}

# 書き込み先が本当に作業ツリーの中かを、実体で確かめる。
# 字面の検査だけでは、途中に置かれた symlink をたどって外へ書き込めてしまう。
destination_is_safe() {
  local rel="$1" to="$TARGET/$1" parent resolved

  # 宛先そのものが symlink のときは、たどらずに断る。
  if [ -L "$to" ]; then
    printf '中断: %s は symlink です。たどらずに終わります\n' "$rel" >&2
    return 1
  fi

  # 実在する最も近い上位ディレクトリを実体解決し、作業ツリーの中かを見る。
  parent=$(dirname "$to")
  while [ ! -d "$parent" ] && [ "$parent" != "/" ] && [ -n "$parent" ]; do
    parent=$(dirname "$parent")
  done
  resolved=$(cd "$parent" 2>/dev/null && pwd -P) || {
    printf '中断: %s の置き場所を解決できません\n' "$rel" >&2
    return 1
  }
  case "$resolved" in
    "$TARGET" | "$TARGET"/*) return 0 ;;
  esac
  printf '中断: %s の書き込み先が作業ツリーの外（%s）を指します\n' "$rel" "$resolved" >&2
  return 1
}

# 既にあるパスの内容が主ディレクトリと食い違うかを見る。
# 食い違うときは上書きせず中断する。
differs_from_main() {
  local rel="$1"
  [ -e "$TARGET/$rel" ] || return 1
  if diff -rq "$MAIN_DIR/$rel" "$TARGET/$rel" >/dev/null 2>&1; then
    return 1
  fi
  printf '中断: %s の内容が主ディレクトリと異なります。手で確かめてください\n' "$rel" >&2
  return 0
}

# 1 つのパスを主ディレクトリから作業ツリーへ複製する。
# ハードリンクを試し、使えない配置ではファイル複製へ退避する。
copy_one() {
  local rel="$1" from="$MAIN_DIR/$1" to="$TARGET/$1"
  [ -e "$from" ] || return 0
  destination_is_safe "$rel" || return 1

  if [ -e "$to" ]; then
    if differs_from_main "$rel"; then
      return 1
    fi
    return 0
  fi

  mkdir -p "$(dirname "$to")" 2>/dev/null
  # `cp -al` はハードリンクでの複製。テストから差し替えられるようにしておく。
  if [ -n "${WT_LINK_COMMAND:-}" ]; then
    if "$WT_LINK_COMMAND" "$from" "$to" 2>/dev/null; then
      return 0
    fi
  elif cp -al "$from" "$to" 2>/dev/null; then
    return 0
  fi
  # ハードリンクが途中で失敗すると、作りかけのディレクトリが残る。そのまま
  # `cp -a` すると、上書きではなくその中へ複製されて階層が二重になる。
  rm -rf "$to" 2>/dev/null
  cp -a "$from" "$to" 2>/dev/null || {
    printf '複製できませんでした: %s\n' "$rel" >&2
    return 1
  }
}

# 書き換えられるパスは、ハードリンクを外して実体で置き換える。
replace_with_real_copy() {
  local rel="$1" from="$MAIN_DIR/$1" to="$TARGET/$1"
  [ -e "$from" ] || return 0
  destination_is_safe "$rel" || return 1
  # 作業ツリー側で書き換えられていたら、置き換えずに中断する。
  if [ -e "$to" ] && differs_from_main "$rel"; then
    return 1
  fi
  rm -rf "$to" 2>/dev/null
  mkdir -p "$(dirname "$to")" 2>/dev/null
  cp -a "$from" "$to" 2>/dev/null || {
    printf '複製できませんでした: %s\n' "$rel" >&2
    return 1
  }
}

do_setup() {
  [ -d "$TARGET" ] || { printf '作業ツリーがありません: %s\n' "$TARGET" >&2; return 1; }
  local rel rc=0
  _wt_read_lines < <(decl_get '.localenv.copy_from_main // [] | .[]')
  for rel in "${WT_LINES[@]+"${WT_LINES[@]}"}"; do
    [ -n "$rel" ] || continue
    if ! is_safe_relative "$rel"; then
      printf '中断: copy_from_main の %s は作業ツリーの外を指します\n' "$rel" >&2
      return 1
    fi
    copy_one "$rel" || rc=1
  done
  [ "$rc" = 0 ] || return 1

  _wt_read_lines < <(decl_get '.localenv.copy_as_real // [] | .[]')
  for rel in "${WT_LINES[@]+"${WT_LINES[@]}"}"; do
    [ -n "$rel" ] || continue
    if ! is_safe_relative "$rel"; then
      printf '中断: copy_as_real の %s は作業ツリーの外を指します\n' "$rel" >&2
      return 1
    fi
    replace_with_real_copy "$rel" || rc=1
  done
  return "$rc"
}

# --- verify -----------------------------------------------------------------

# 0 一致 / 1 不一致 / 2 未起動または適用外
do_verify() {
  local probe loaded branch
  probe=$(decl_get '.localenv.branch_probe // empty')
  [ -n "$probe" ] || return 2

  branch=$(target_branch)
  [ -n "$branch" ] || return 2

  loaded=$(cd "$MAIN_DIR" && sh -c "$probe" 2>/dev/null) || return 2
  # 前後の空白を落とす。
  loaded=$(printf '%s' "$loaded" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  [ -n "$loaded" ] || return 2

  if [ "$loaded" = "$branch" ]; then
    printf '一致: 環境に %s が載っています\n' "$branch"
    return 0
  fi
  printf '不一致: 環境に載っているのは %s で、対象は %s です\n' "$loaded" "$branch"
  return 1
}

# --- healthcheck ------------------------------------------------------------

do_healthcheck() {
  local command rc
  do_verify >/dev/null
  rc=$?
  [ "$rc" = 0 ] || return "$rc"

  command=$(decl_get '.localenv.healthcheck // empty')
  [ -n "$command" ] || return 2
  (cd "$MAIN_DIR" && sh -c "$command")
}

# --- aim --------------------------------------------------------------------

# 環境が指すコードを対象の作業ツリーへ向ける。実行系を叩くため、単体テストでは
# 扱わない（詳細設計 06 のテスト設計）。手順の詳細は
# references/local-environment.md にある。
do_aim() {
  local layout build service src_target project container current_link
  local reload_process reload_signal
  # 存在しない対象へ向けると、コンテナ内のコード位置が実在しないパスを指す。
  [ -d "$TARGET" ] || { printf '作業ツリーがありません: %s\n' "$TARGET" >&2; return 1; }
  git -C "$TARGET" rev-parse --git-dir >/dev/null 2>&1 || {
    printf '作業ツリーではありません: %s\n' "$TARGET" >&2
    return 1
  }
  layout=$(decl_get '.localenv.layout // empty')
  service=$(decl_get '.localenv.app_service // empty')
  src_target=$(decl_get '.localenv.src_target // empty')
  project=$(basename "$MAIN_DIR")

  command -v docker >/dev/null 2>&1 || {
    printf 'コンテナ実行系が見つかりません\n' >&2
    return 1
  }

  # 切り替える前に、追跡されない生成物を作る。
  _wt_read_lines < <(decl_get '.localenv.build_before_aim // [] | .[]')
  for build in "${WT_LINES[@]+"${WT_LINES[@]}"}"; do
    [ -n "$build" ] || continue
    (cd "$TARGET" && sh -c "$build") || {
      printf '資産のビルドに失敗しました: %s\n' "$build" >&2
      return 1
    }
  done

  case "$layout" in
    indirect)
      [ -n "$src_target" ] || { printf 'localenv.src_target が要ります\n' >&2; return 1; }
      # コードの位置を指しているコンテナだけを張り替える。
      # 値をシェル文字列へ連結しない。宣言値やパスに引用符が混じると、
      # コンテナの中で意図しないコマンドが動く。
      for container in $(docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Names}}' 2>/dev/null); do
        current_link=$(docker exec "$container" readlink -- "$src_target" 2>/dev/null)
        # 主ディレクトリと、その配下の作業ツリーのどちらを向いていても切り替える。
        case "$current_link" in
          "$MAIN_DIR" | "$MAIN_DIR"/*)
            docker exec -u root "$container" ln -sfn -- "$TARGET" "$src_target" || return 1
            ;;
        esac
      done
      ;;
    direct|host)
      printf '%s 型の切り替えは references/local-environment.md の手順で行います\n' "$layout" >&2
      return 1
      ;;
    *)
      printf 'localenv.layout が indirect / direct / host のいずれでもありません\n' >&2
      return 1
      ;;
  esac

  # 実行中のプロセスがコードの位置を保持している場合は、再読み込みを促す。
  reload_process=$(decl_get '.localenv.reload_signal.process // empty')
  reload_signal=$(decl_get '.localenv.reload_signal.signal // empty')
  if [ -n "$reload_process" ] && [ -n "$reload_signal" ] && [ -n "$service" ]; then
    for container in $(docker ps --filter "label=com.docker.compose.project=$project" --filter "label=com.docker.compose.service=$service" --format '{{.Names}}' 2>/dev/null); do
      docker exec -u root "$container" pkill "-${reload_signal}" -x -- "$reload_process" 2>/dev/null || true
    done
  fi

  printf '環境が指すコードを %s へ向けました\n' "$TARGET"
}

# --- mode -------------------------------------------------------------------

# 0 相乗り / 1 分離
do_mode() {
  local pattern path matched=""
  _wt_read_lines < <(decl_get '.localenv.isolate_when // [] | .[]')
  local patterns=("${WT_LINES[@]+"${WT_LINES[@]}"}")

  if [ "${#patterns[@]}" -eq 0 ]; then
    printf '相乗り: 分離を促す条件が宣言されていません\n'
    return 0
  fi

  # `git status --porcelain` は空白や非 ASCII を含むパスを引用符で囲む。
  # `-z` で null 区切りにして、引用されない生のパスを読む。
  local entry status take_raw=0
  local -a changed=()
  while IFS= read -r -d '' entry; do
    if [ "$take_raw" = 1 ]; then
      # 改名と複製の続くレコードは、状態を持たない変更前のパスである。
      # 条件の対象から外さない。対象のディレクトリから外へ移す変更を
      # 見落とさないため、変更前の位置でも判定する。
      take_raw=0
      changed+=("$entry")
      continue
    fi
    status=${entry:0:2}
    changed+=("${entry:3}")
    case "$status" in
      R*|C*) take_raw=1 ;;
    esac
  done < <(git -C "$TARGET" status --porcelain -z --untracked-files=all 2>/dev/null)

  for path in "${changed[@]+"${changed[@]}"}"; do
    [ -n "$path" ] || continue
    for pattern in "${patterns[@]}"; do
      [ -n "$pattern" ] || continue
      # shellcheck disable=SC2254
      case "$path" in
        $pattern)
          # 1 つのパスが複数の条件に当たっても、一覧へは 1 度だけ載せる。
          matched="$matched  - $path（$pattern）
"
          break
          ;;
      esac
    done
  done

  if [ -n "$matched" ]; then
    printf '分離: 次の変更が分離を促す条件に当たります\n%s' "$matched"
    printf '手順は /ndf:worktree の references/local-environment.md にあります\n'
    return 1
  fi
  printf '相乗り: 分離を促す条件に当たる変更はありません\n'
  return 0
}

# --- 入口 -------------------------------------------------------------------

case "$SUBCOMMAND" in
  setup) do_setup ;;
  verify) do_verify ;;
  healthcheck) do_healthcheck ;;
  aim) do_aim ;;
  mode) do_mode ;;
  *)
    printf '使い方: worktree-localenv.sh <setup|verify|healthcheck|aim|mode> [作業ツリー]\n' >&2
    exit 1
    ;;
esac
