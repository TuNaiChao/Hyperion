"""workspace 服务:每个 bug 一个专用工作目录(R2 末最简形态)。

完整设计见 docs/设计/workspace-design.md。R2 末只建最小 workspace(code/+triggers/+delegate/+patch/+report/+AGENTS.md);
完整七段 + LocalSandbox 留 R3。
"""
from rootrecall.services.workspace.manager import create_workspace

__all__ = ["create_workspace"]
