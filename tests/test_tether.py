import asyncio
import io
import textwrap
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError
from pydantic_ai.exceptions import SkipToolExecution
from pydantic_ai.models.test import TestModel

from tether import plugin
from tether.cli import main, make_session
from tether.frontends.headless import Headless
from tether.host.config import build_agent, load_config, merge
from tether.host.loader import discover
from tether.host.session import Session, TurnFinished

REPO = Path(__file__).resolve().parents[1]


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


def make_plugin():
    p = plugin("echo")
    calls = []

    @p.tool
    def shout(text: str) -> str:
        """Upper-case text."""
        calls.append(text)
        return text.upper()

    return p, calls


def test_plugin_tool_runs_through_agent_spec():
    p, calls = make_plugin()
    from tether.host.config import HostConfig, TetherConfig
    from tether.host.loader import LoadedPlugin, PluginSet

    plugins = PluginSet()
    plugins.add(LoadedPlugin(p.name, p.capability_type(), "test", p))
    config = TetherConfig(agent={"capabilities": [{"echo": {}}]}, host=HostConfig())
    agent = build_agent(config, plugins, model=TestModel(custom_output_text="ok"))

    result = agent.run_sync("hi")
    assert result.output == "ok"
    assert len(calls) == 1


def test_hook_can_skip_tool_execution():
    p, calls = make_plugin()

    @p.on.before_tool_execute
    async def block(ctx, *, call, tool_def, args):
        raise SkipToolExecution("blocked")

    from pydantic_ai import Agent

    agent = Agent(TestModel(), capabilities=[p.build()])
    result = agent.run_sync("hi")
    assert calls == []
    assert "blocked" in result.output


def test_plugin_config_is_validated():
    p = plugin("cfg")

    @p.config
    class Settings(BaseModel):
        level: int = 1

    p.build(level=3)
    assert p.settings.level == 3
    with pytest.raises(ValidationError):
        p.build(level="not a number")
    with pytest.raises(ValueError):
        plugin("noconf").build(x=1)


def test_discover_files_and_bad_plugins(tmp_path):
    plugins_dir = tmp_path / "plugins"
    write(
        plugins_dir / "good.py",
        """
        from tether import plugin
        p = plugin("good", version="1.2")
        @p.command("/hello")
        def hello(session, args): ...
    """,
    )
    write(
        plugins_dir / "caps.py",
        """
        from pydantic_ai.capabilities import AbstractCapability
        class MyCap(AbstractCapability):
            pass
    """,
    )
    write(plugins_dir / "broken.py", "raise RuntimeError('boom')\n")

    found = discover([plugins_dir], use_entry_points=False)
    assert set(found.plugins) == {"good", "MyCap"}
    assert len(found.errors) == 1 and "boom" in str(found.errors[0][1])
    assert set(found.commands(["good"])) == {"/hello"}
    assert found.commands(["MyCap"]) == {}


def test_project_plugin_overrides_user_plugin(tmp_path):
    for where in ("user", "project"):
        write(
            tmp_path / where / "p.py",
            f"""
            from tether import plugin
            p = plugin("same", version="{where}")
        """,
        )
    found = discover([tmp_path / "user", tmp_path / "project"], use_entry_points=False)
    assert found.plugins["same"].plugin.version == "project"


def test_config_merge_and_host_section(tmp_path):
    user = write(
        tmp_path / "user.yaml",
        """
        model: test
        instructions: base
        capabilities:
          - a: {x: 1}
          - b
        host:
          frontends: [headless]
          web: {port: 1}
    """,
    )
    project = write(
        tmp_path / "project.yaml",
        """
        capabilities:
          - a: {x: 2}
        host:
          web: {host: localhost}
    """,
    )
    config = load_config([user, project])
    assert config.agent["capabilities"] == ["b", {"a": {"x": 2}}]
    assert config.agent["model"] == "test"
    assert config.host.frontends == ["headless"]
    assert config.host.web == {"port": 1, "host": "localhost"}
    assert "host" not in config.agent
    assert merge({"k": {"a": 1}}, {"k": {"b": 2}}) == {"k": {"a": 1, "b": 2}}


def test_harness_capability_resolved_by_name():
    from tether.host.config import HostConfig, TetherConfig
    from tether.host.loader import PluginSet

    config = TetherConfig(agent={"capabilities": ["Planning"]}, host=HostConfig())
    agent = build_agent(config, PluginSet(), model=TestModel())
    assert agent.run_sync("plan").output


def test_default_model_is_haiku(monkeypatch):
    from tether.host.config import DEFAULT_MODEL, HostConfig, TetherConfig
    from tether.host.loader import PluginSet

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    agent = build_agent(TetherConfig(agent={}, host=HostConfig()), PluginSet())
    assert DEFAULT_MODEL == "anthropic:claude-haiku-4-5"
    assert agent.model.model_name == "claude-haiku-4-5"


def test_session_keeps_history_and_rebuilds_agent():
    builds = []

    def factory():
        from pydantic_ai import Agent

        builds.append(1)
        return Agent(TestModel(custom_output_text="pong"))

    session = Session(factory)
    events = []
    session.bus.subscribe(events.append)

    async def go():
        await session.send("one")
        n = len(session.history)
        session.invalidate_agent()
        await session.send("two")
        return n

    first_len = asyncio.run(go())
    assert len(builds) == 2
    assert len(session.history) > first_len
    assert sum(isinstance(e, TurnFinished) for e in events) == 2


def test_commands_and_headless_output():
    p, _ = make_plugin()
    seen = []

    @p.command("/note", help="remember")
    async def note(session, args):
        seen.append(args)
        await session.notify(f"noted {args}")

    from tether.host.config import HostConfig, TetherConfig
    from tether.host.loader import LoadedPlugin, PluginSet

    plugins = PluginSet()
    plugins.add(LoadedPlugin(p.name, p.capability_type(), "test", p))
    config = TetherConfig(agent={"capabilities": ["echo"]}, host=HostConfig())
    session = make_session(config, plugins, model=TestModel(custom_output_text="hello"))
    out, err = io.StringIO(), io.StringIO()
    Headless(out, err).attach(session)

    asyncio.run(session.handle("/note milk"))
    asyncio.run(session.handle("/help"))
    asyncio.run(session.handle("say hi"))

    assert seen == ["milk"]
    assert "noted milk" in err.getvalue()
    assert "/note  remember" in err.getvalue() and "/help" in err.getvalue()
    assert "→ shout(" in err.getvalue()
    assert "← shout:" in err.getvalue()
    assert out.getvalue().strip() == "hello"


def test_example_plugin_loads_and_guards():
    found = discover([REPO / "examples" / "plugins"], use_entry_points=False)
    guard = found.plugins["git-guard"].plugin
    assert "/gst" in guard.commands
    guard.build(forbid=["rm -rf"])
    assert guard.settings.forbid == ["rm -rf"]


def test_cli_plugins_command(tmp_path, capsys):
    write(
        tmp_path / ".tether" / "plugins" / "x.py",
        """
        from tether import plugin
        p = plugin("xp", version="9")
    """,
    )
    write(tmp_path / ".tether" / "tether.yaml", "capabilities: [xp]\n")
    assert main(["plugins"]) == 0
    assert "✓ xp" in capsys.readouterr().out


def test_non_dataclass_harness_capability_loads_by_name(tmp_path):
    """Harness `Coder` is not a dataclass; it must still work from tether.yaml."""
    from tether.host.config import HostConfig, TetherConfig
    from tether.host.loader import PluginSet

    (tmp_path / "a.txt").write_text("hello")
    config = TetherConfig(
        agent={"capabilities": [{"LocalWorkspace": {"working_dir": str(tmp_path)}}, {"Coder": {}}]},
        host=HostConfig(),
    )
    agent = build_agent(config, PluginSet(), model=TestModel(call_tools=["read_file"]))
    result = agent.run_sync("read a.txt")
    tool_returns = [p for m in result.all_messages() for p in m.parts if p.part_kind == "tool-return"]
    assert [p.tool_name for p in tool_returns] == ["read_file"]
