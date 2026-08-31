source plugins/ndf/scripts/lib/worktree-common.sh
wt_extract_write_target "coproc { cd .worktrees/x; }; echo hi > README.md" "/base"
