import subprocess
import os

with open("test_tok4.sh", "w") as f:
    f.write('''
. plugins/ndf/scripts/lib/worktree-common.sh
spaced="( case foo in bar ) echo hi ;; esac )"
_wt_read_lines < <(_wt_tokenize "$spaced")
printf "[%s]\n" "${WT_LINES[@]}"
''')

os.system("bash test_tok4.sh")
