"""`tether` command line.

tether run "задача"     one turn, then exit
tether chat             interactive session in the frontends from `host.frontends`
tether chat --ui tui    ... or in the one named here
tether plugins          list discovered plugins and frontends
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from tether.api.plugin import Command
from tether.frontends.headless import Headless
from tether.host import frontends
from tether.host.approvals import DEFAULT_POLICY, Approvals
from tether.host.ask_user import ask_user_type
from tether.host.config import TetherConfig, build_agent, load_config, project_dir, user_dir
from tether.host.loader import PluginSet, discover
from tether.host.session import Session, TurnError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tether")
    parser.add_argument("-c", "--config", type=Path, action="append", help="config file (repeatable)")
    parser.add_argument("-m", "--model", help="override the model, e.g. anthropic:claude-opus-5-5")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="run one prompt and exit")
    run_p.add_argument("prompt", nargs="+")
    chat_p = sub.add_parser("chat", help="interactive session")
    chat_p.add_argument("--ui", action="append", help="frontend to use, e.g. tui (repeatable)")
    sub.add_parser("plugins", help="list discovered plugins")
    args = parser.parse_args(argv)

    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")  # the frontends own the terminal

    level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")

    config = load_config(args.config)
    plugins = discover([user_dir() / "plugins", project_dir() / "plugins"])

    if args.cmd == "plugins":
        return _list_plugins(config, plugins)

    session = make_session(config, plugins, model=args.model)
    try:
        if args.cmd == "run":
            Headless().attach(session)
            asyncio.run(session.handle(" ".join(args.prompt)))
        else:
            names = args.ui or config.host.frontends
            ui = [frontends.create(name, config.host.options(name)) for name in names]
            asyncio.run(frontends.run_frontends(session, ui))
    except KeyboardInterrupt:
        return 130
    except TurnError:
        return 1  # already shown by the frontend
    except Exception as e:
        print(f"tether: {e}", file=sys.stderr)
        return 1
    return 0


def make_session(config: TetherConfig, plugins: PluginSet, *, model: Any = None) -> Session:
    session = Session(
        lambda: build_agent(
            config,
            plugins,
            model=model,
            host_types=[ask_user_type(session)],
            host_capabilities=[Approvals(session, config.host.approvals or dict(DEFAULT_POLICY))],
        ),
        commands=plugins.commands(config.capability_names),
    )

    async def help_command(s: Session, _: str) -> None:
        lines = [f"{c.name}  {c.help}".rstrip() for c in sorted(s.commands.values(), key=lambda c: c.name)]
        await s.notify("\n".join(lines))

    session.commands.setdefault("/help", Command("/help", help_command, "list commands"))
    return session


def _list_plugins(config: TetherConfig, plugins: PluginSet) -> int:
    enabled = set(config.capability_names)
    for name, loaded in sorted(plugins.plugins.items()):
        mark = "✓" if name in enabled else " "
        version: Any = loaded.plugin.version if loaded.plugin else "-"
        print(f"{mark} {name:24} {version:10} {loaded.source}")
    for source, error in plugins.errors:
        print(f"✗ {source}: {error}", file=sys.stderr)
    if not plugins.plugins:
        print("no plugins found", file=sys.stderr)
    print(f"frontends: {', '.join(sorted(frontends.available()))}")
    return 1 if plugins.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
