#!/bin/bash
source plugins/ndf/scripts/lib/worktree-common.sh

cmd="cmd1 && cd .worktrees/x; echo hi > README.md"
wt_extract_write_target "$cmd" "/base"

