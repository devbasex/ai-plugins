cp plugins/ndf/scripts/lib/worktree-common.sh tmp.sh
sed -i '/prev="$w"/a \    echo "TOK: $w prev: $prev at_cmd: $at_cmd cwd: $cwd" >&2' tmp.sh
source tmp.sh
wt_extract_write_target "( echo x ; cd .worktrees/x ) ; sed -i 's/a/b/' README.md" "/base" 2> trace.txt > out3.txt
cat trace.txt
echo "---"
cat out3.txt
