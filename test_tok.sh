. plugins/ndf/scripts/lib/worktree-common.sh
wt_extract_write_target "( case foo in bar ) echo hi; cd .worktrees/x ;; esac ); echo hi > README.md" "/base"
