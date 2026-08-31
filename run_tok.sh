. plugins/ndf/scripts/lib/worktree-common.sh
spaced="( case foo in bar ) cd .worktrees/x ;; esac ) __WT_SEP__ echo hi __WT_REDIR__ README.md"
_wt_tokenize "$spaced"
