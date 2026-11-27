import sys

from ndf.adapters import claude
from ndf.cli import main
from ndf.domains.records import usage
from ndf.domains.relay import plan_claude

# 下の層の call_claude は中継の部品を読めないので、全層より上のエントリポイントで確かめを差し込む（#3604 #3864 の U12）
claude.set_relay_checks(plan_claude)
# 起動した子の使用量の行も、下の層は使用量ログの部品を読めないので同じ所で差し込む（#1959 の決定 6）
claude.set_launch_recorder(usage)
sys.exit(main())
