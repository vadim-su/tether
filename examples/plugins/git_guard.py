"""Example plugin: a git tool, a guard hook, and a slash command.

Copy to ~/.tether/plugins/ or ./.tether/plugins/ and enable in tether.yaml:

    capabilities:
      - git-guard: {forbid: ["push --force"]}
"""

import asyncio

from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.exceptions import SkipToolExecution

from tether import plugin

p = plugin("git-guard", version="0.1.0", description="git status tool and a guard against risky git commands")


@p.config
class Settings(BaseModel):
    forbid: list[str] = ["push --force", "reset --hard"]


p.instructions("Before changing files in a git repository, check `git_status`.")


@p.tool
async def git_status(ctx: RunContext, path: str = ".") -> str:
    """Show `git status --short` for a repository."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        "status",
        "--short",
        cwd=path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return out.decode() or "clean"


@p.on.before_tool_execute
async def forbid_risky_git(ctx, *, call, tool_def, args):
    text = str(args)
    for pattern in p.settings.forbid:
        if pattern in text:
            raise SkipToolExecution(f"blocked by git-guard: {pattern!r} is not allowed")
    return args


@p.command("/gst", help="ask the agent for git status")
async def gst(session, args: str) -> None:
    await session.send("Покажи git status и кратко опиши изменения.")
