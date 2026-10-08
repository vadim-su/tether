"""Tool approvals: a policy from `host.approvals`, enforced with Pydantic AI's deferred tools.

```yaml
host:
  approvals:
    shell: ask          # allow | ask | deny
    "write_*": ask      # glob patterns work too
    "*": allow          # everything else
```

Calls the policy marks `ask` raise `ApprovalRequired`; so do tools that raise it
themselves. The capability then resolves them inline, asking the session's
frontends, and the run continues without pausing.
"""

import fnmatch
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDenied
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.approval_required import ApprovalRequiredToolset

if TYPE_CHECKING:
    from tether.host.session import Session

type Decision = Literal["allow", "ask", "deny"]

DEFAULT_POLICY: dict[str, Decision] = {
    "shell": "ask",
    "write_file": "ask",
    "edit_file": "ask",
    "*": "allow",
}
"""Used when `host.approvals` is not set: the harness `Coder` tools that change things ask first."""


def decide(policy: dict[str, Decision], tool_name: str) -> Decision:
    """Exact name first, then glob patterns in order, then `*`; allow if nothing matches."""
    if tool_name in policy:
        return policy[tool_name]
    for pattern, decision in policy.items():
        if pattern != "*" and fnmatch.fnmatchcase(tool_name, pattern):
            return decision
    return policy.get("*", "allow")


@dataclass
class Approvals(AbstractCapability[Any]):
    session: Session
    policy: dict[str, Decision] = field(default_factory=lambda: dict(DEFAULT_POLICY))

    @classmethod
    def get_serialization_name(cls) -> str | None:
        return None  # added by the host, not from tether.yaml

    def get_wrapper_toolset(self, toolset: AbstractToolset[Any]) -> AbstractToolset[Any]:
        return ApprovalRequiredToolset(
            toolset, lambda ctx, tool_def, args: self._needs_approval(tool_def.name)
        )

    def _needs_approval(self, tool_name: str) -> bool:
        decision = decide(self.policy, tool_name)
        return decision == "deny" or (decision == "ask" and tool_name not in self.session.always_approved)

    async def handle_deferred_tool_calls(
        self, ctx: RunContext[Any], *, requests: DeferredToolRequests
    ) -> DeferredToolResults | None:
        if not requests.approvals:
            return None
        approvals: dict[str, bool | ToolDenied] = {}
        for call in requests.approvals:  # one at a time: a person reads them in order
            if decide(self.policy, call.tool_name) == "deny":
                approvals[call.tool_call_id] = ToolDenied(
                    f"`{call.tool_name}` is forbidden by the approval policy"
                )
                continue
            answer = await self.session.approve(call.tool_name, call.args_as_dict(), call.tool_call_id)
            approvals[call.tool_call_id] = (
                True if answer.approved else ToolDenied(answer.message or "The user denied this tool call.")
            )
        return requests.build_results(approvals=approvals)
