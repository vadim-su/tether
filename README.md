# tether

Модульный агентный харнесс на [Pydantic AI](https://ai.pydantic.dev/) и [pydantic-ai-harness](https://pydantic.dev/docs/ai/harness/). Главный принцип — моддинг: всё, кроме тонкого хоста, это плагины, а плагин — обычная capability Pydantic AI.

Архитектура: [docs/architecture.md](docs/architecture.md).

## Быстрый старт

Нужен Python 3.14 (`uv python install 3.14`) и ключ Anthropic API. По умолчанию используется Claude Haiku 4.5 (`anthropic:claude-haiku-4-5`).

```bash
uv sync
export ANTHROPIC_API_KEY=sk-ant-...
uv run tether chat                       # интерактивно, на Haiku
uv run tether run "Что лежит в этом репозитории?"
uv run tether -m anthropic:claude-sonnet-5-5 chat   # другая модель на один запуск
uv run tether plugins
```

Чтобы агент работал с файлами проекта, добавьте capability в `.tether/tether.yaml`:

```yaml
capabilities:
  - LocalWorkspace: {root: .}
  - Coder: {}
```

Модель можно закрепить там же (`model: anthropic:claude-opus-5-5`). Без ключа проводку можно проверить на тестовой модели: `uv run tether -m test run привет`.

## Плагин за минуту

Положите файл в `~/.tether/plugins/` или `./.tether/plugins/`:

```python
from pydantic_ai.exceptions import SkipToolExecution
from tether import plugin

p = plugin("git-guard")

@p.tool
async def git_status(ctx, path: str = ".") -> str:
    """Показать git status."""
    ...

@p.on.before_tool_execute            # те же хуки, что у pydantic_ai.capabilities.Hooks
async def guard(ctx, *, call, tool_def, args):
    if "push --force" in str(args):
        raise SkipToolExecution("force push запрещён")
    return args

@p.command("/gst")
async def gst(session, args):
    await session.send("покажи git status")
```

и включите его в `tether.yaml`:

```yaml
capabilities:
  - git-guard: {}
```

Полный пример с конфигом: [examples/plugins/git_guard.py](examples/plugins/git_guard.py).

Плагином также может быть любой подкласс `pydantic_ai.capabilities.AbstractCapability` (`@dataclass`), а любая capability из `pydantic_ai_harness` (`Coder`, `Planning`, `Memory`, ...) подключается по имени без установки.

## Где ищутся плагины

1. Пакеты с entry point `[project.entry-points."tether.plugins"]`.
2. `~/.tether/plugins/` (каталог меняется через `TETHER_HOME`).
3. `./.tether/plugins/` — проектные, перекрывают одноимённые.

Конфиги `~/.tether/tether.yaml` и `./.tether/tether.yaml` сливаются; формат — родной AgentSpec Pydantic AI плюс секция `host:`.

## Разработка

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Статус

Этап 1 из [плана](docs/architecture.md#7-план-mvp): загрузчик плагинов, конфиг, хост сессий, headless-фронтенд и CLI. Дальше: подтверждения и `AskUser` через фронтенд, hot reload, TUI, web.
