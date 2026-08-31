. plugins/ndf/scripts/lib/worktree-common.sh
spaced="FOO=bar cd .worktrees/x __WT_SEP__ echo hi __WT_REDIR__ README.md"
_wt_tokenize "$spaced"
