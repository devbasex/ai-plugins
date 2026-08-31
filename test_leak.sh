source plugins/ndf/scripts/lib/worktree-common.sh
export PATH="/bin:/usr/bin"
cp plugins/ndf/scripts/lib/worktree-common.sh tmp2.sh
sed -i 's/prev="$w"/prev="$w"; echo "TOK: $w cwd: $cwd pipe: $pipe_cwd job: $job_cwd list: $list_cwd" >\&2/' tmp2.sh
source tmp2.sh
wt_extract_write_target "( echo x ; cd .worktrees/x ) ; sed -i 's/a/b/' README.md" "/base" 2> trace2.txt > out4.txt
cat trace2.txt
