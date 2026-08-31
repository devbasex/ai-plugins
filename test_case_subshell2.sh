. plugins/ndf/scripts/lib/worktree-common.sh
set -x
wt_extract_write_target "( case foo in bar ) cd .worktrees/x ;; esac ); echo hi > README.md" "/base"
