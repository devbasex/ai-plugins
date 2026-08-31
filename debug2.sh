debug_extract() {
  local base="/base"
  local cwd="$base"
  local pipe_cwd="$base"
  local list_cwd="$base"

  local -a words=()
  readarray -t words < out.txt

  local n=${#words[@]} i j w target found=0 prev="" at_cmd=0 dest="" k
  for ((i = 0; i < n; i++)); do
    w=${words[i]}
    at_cmd=0
    if [ "$i" = 0 ]; then
      at_cmd=1
    else
      case "$prev" in
        __WT_SEP__|"&&"|"||"|"|"|"|&"|"&") at_cmd=1 ;;
        if|elif|then|else|while|until|do|"{"|"!"|time) at_cmd=1 ;;
      esac
    fi
    echo "TOKEN: [$w] prev=[$prev] at_cmd=$at_cmd cwd=$cwd"
    prev="$w"
    case "$w" in
      cd)
        if [ "$at_cmd" = 1 ]; then
          cwd="$cwd/.worktrees/x"
          echo "  --> cd done, cwd=$cwd"
        fi
        ;;
      __WT_SEP__)
        pipe_cwd="$cwd"
        echo "  --> __WT_SEP__ done, pipe_cwd=$pipe_cwd"
        ;;
    esac
  done
}
debug_extract
