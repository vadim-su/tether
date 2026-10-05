"""The plugin API: the one object a plugin author touches.

A `Plugin` collects tools, hooks, instructions and commands, and turns them into
an ordinary Pydantic AI capability. Anything Pydantic AI can do is therefore
available without wrappers; `Plugin` only removes the boilerplate.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel
from pydantic_ai.capabilities import AbstractCapability, Capability, CombinedCapability, Hooks
from pydantic_ai.tools import Tool

if TYPE_CHECKING:
    from tether.host.session import Session

type CommandHandler = Callable[[Session, str], Awaitable[None] | None]


@dataclass
class Command:
    name: str
    handler: CommandHandler
    help: str = ""


class _HookRecorder:
    """Mirror of `Hooks.on`: records registrations so each build gets a fresh `Hooks`."""

    def __init__(self, plugin: Plugin) -> None:
        self._plugin = plugin

    def __getattr__(self, hook_name: str) -> Callable[..., Any]:
        if hook_name not in _HOOK_NAMES:
            raise AttributeError(f"unknown hook {hook_name!r}; see pydantic_ai.capabilities.Hooks")

        def register(func: Callable[..., Any] | None = None, /, **options: Any) -> Any:
            if func is not None:
                self._plugin._hooks.append((hook_name, func, options))
                return func

            def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
                self._plugin._hooks.append((hook_name, f, options))
                return f

            return decorator

        return register


_HOOK_NAMES = frozenset(n for n in dir(Hooks().on) if not n.startswith("_"))


@dataclass(eq=False)
class Plugin:
    """A tether plugin.

    ```python
    from tether import plugin

    p = plugin("git-guard")

    @p.tool
    async def git_status(ctx, path: str = ".") -> str: ...

    @p.on.before_tool_execute
    async def guard(ctx, *, call, tool_def, args): ...
    ```
    """

    name: str
    version: str = "0.0.0"
    description: str | None = None

    _tools: list[Tool[Any]] = field(default_factory=list, repr=False)
    _hooks: list[tuple[str, Callable[..., Any], dict[str, Any]]] = field(default_factory=list, repr=False)
    _instructions: list[Any] = field(default_factory=list, repr=False)
    _config_model: type[BaseModel] | None = field(default=None, repr=False)
    commands: dict[str, Command] = field(default_factory=dict, repr=False)

    settings: Any = field(default=None, init=False, repr=False)
    """Validated config from the last build; `None` until the plugin is enabled."""

    @property
    def on(self) -> Any:
        """Hook decorators, same names and signatures as `pydantic_ai.capabilities.Hooks.on`."""
        return _HookRecorder(self)

    def tool(self, func: Callable[..., Any] | None = None, /, **options: Any) -> Any:
        """Register a tool. Accepts the same options as `pydantic_ai.Tool`."""
        if func is not None:
            self._tools.append(Tool(func, **options))
            return func

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            self._tools.append(Tool(f, **options))
            return f

        return decorator

    def instructions(self, value: Any) -> Any:
        """Add instructions: a string, or a function `(ctx) -> str` used as a decorator."""
        self._instructions.append(value)
        return value

    def config(self, model: type[BaseModel]) -> type[BaseModel]:
        """Declare the plugin's config schema (a pydantic model) as a class decorator."""
        self._config_model = model
        return model

    def command(self, name: str, *, help: str = "") -> Callable[[CommandHandler], CommandHandler]:
        """Register a slash command, e.g. `@p.command("/gst")`."""
        if not name.startswith("/"):
            name = "/" + name

        def decorator(handler: CommandHandler) -> CommandHandler:
            self.commands[name] = Command(name, handler, help or (handler.__doc__ or "").strip())
            return handler

        return decorator

    def build(self, **config: Any) -> AbstractCapability[Any]:
        """Turn the plugin into a Pydantic AI capability, validating its config."""
        if self._config_model is not None:
            self.settings = self._config_model(**config)
        elif config:
            raise ValueError(f"plugin {self.name!r} takes no config, got {sorted(config)}")

        parts: list[AbstractCapability[Any]] = []
        if self._tools or self._instructions:
            parts.append(
                Capability(
                    instructions=list(self._instructions) or None,
                    tools=list(self._tools),
                    id=f"{self.name}.tools",
                )
            )
        if self._hooks:
            hooks: Hooks[Any] = Hooks(id=f"{self.name}.hooks")
            for hook_name, func, options in self._hooks:
                registrar = getattr(hooks.on, hook_name)
                registrar(**options)(func) if options else registrar(func)
            parts.append(hooks)
        return CombinedCapability(parts, id=self.name, description=self.description)

    def capability_type(self) -> type[AbstractCapability[Any]]:
        """A capability class named after the plugin, for `Agent.from_spec` registries."""
        cls = type(f"TetherPlugin[{self.name}]", (_PluginCapability,), {"plugin": self})
        return dataclass(cls)  # Agent.from_spec requires dataclass capability types


@dataclass
class _PluginCapability(AbstractCapability[Any]):
    """Registry shim: lets a `Plugin` be referenced by name in an agent spec."""

    plugin: ClassVar[Plugin]

    @classmethod
    def get_serialization_name(cls) -> str | None:
        return cls.plugin.name

    @classmethod
    def from_spec(cls, *args: Any, **kwargs: Any) -> AbstractCapability[Any]:
        if args:
            raise ValueError(f"plugin {cls.plugin.name!r} takes keyword config only")
        return cls.plugin.build(**kwargs)


def plugin(name: str, *, version: str = "0.0.0", description: str | None = None) -> Plugin:
    """Create a plugin. The module-level object is discovered automatically."""
    return Plugin(name=name, version=version, description=description)
