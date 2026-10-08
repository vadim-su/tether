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
  - LocalWorkspace: {working_dir: .}
  - Coder: {}
```

## Терминальный интерфейс

TUI на Textual живёт отдельным плагином [plugins/tui](plugins/tui/README.md):

```bash
uv tool install --python 3.14 . --with ./plugins/tui
tether chat --ui tui          # или host: {frontends: [tui]} в tether.yaml
```

При разработке (`uv sync`) он уже установлен: `uv run tether -m test chat --ui tui`.

## Подтверждения и вопросы

Перед `shell`, `write_file` и `edit_file` tether спрашивает разрешения у фронтенда. Политику задаёт `host.approvals` (имя инструмента или glob → `allow` | `ask` | `deny`):

```yaml
host:
  approvals:
    shell: ask
    "write_*": ask
    "*": allow
```

`AskUser` из pydantic-ai-harness включается как обычная capability (`- AskUser: {}`), а отвечает на его вопросы фронтенд. В `tether run` без терминала такие вызовы отклоняются.

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

## Свой фронтенд

Фронтенд — любой объект с `async def run(session)`: подписывается на `session.bus`, отправляет ввод в `session.handle(...)`, отвечает на запросы через `session.answer(...)`. Регистрируется entry point'ом:

```toml
[project.entry-points."tether.frontends"]
web = "tether_plugin_web:WebFrontend"
```

События описаны в `tether.api.events`. Если указать несколько фронтендов (`host.frontends: [tui, web]`), они работают с одной сессией одновременно, на запрос отвечает тот, кто успел первым.

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

Готово: загрузчик плагинов, конфиг, хост сессий, CLI, механизм фронтендов, подтверждения инструментов и `AskUser` через фронтенд, отмена хода, headless и TUI. Дальше по [плану](docs/architecture.md#7-план-mvp): hot reload, web, UI-вклады плагинов (статус, панели).
