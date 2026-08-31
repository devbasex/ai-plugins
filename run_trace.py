import subprocess
import os

with open("trace.sh", "w") as f:
    f.write('''
. plugins/ndf/scripts/lib/worktree-common.sh
cmd="FOO=bar cd .worktrees/x; echo hi > README.md"
base="/base"
cmd=$(_wt_strip_heredocs "$cmd")
spaced=$(printf '%s\n' "$cmd" | sed -e 's/;/ __WT_SEP__ /g' -e 's/>/ __WT_REDIR__ /g')

_wt_read_lines < <(_wt_tokenize "$spaced")
words=("${WT_LINES[@]+"${WT_LINES[@]}"}")
echo "WORDS: ${words[@]}"

prev=""
cwd="$base"
for ((i = 0; i < ${#words[@]}; i++)); do
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
  echo "i=$i, w='$w', prev='$prev', at_cmd=$at_cmd"
  prev="$w"
done
''')

os.system("bash trace.sh")
