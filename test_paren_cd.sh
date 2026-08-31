source plugins/ndf/scripts/lib/worktree-common.sh
cmd='( cd dir; echo hi > file )'
wt_extract_write_target "$cmd" "/base"
