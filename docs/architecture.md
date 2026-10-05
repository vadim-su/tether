# tether: архитектура (черновик v0.3, на Pydantic AI)

Название: **tether** — пакет `tether`, команда `tether`, плагины `tether-plugin-*`, конфиг и плагины в `~/.tether/` и `./.tether/`.

Основа: Python 3.14, **pydantic-ai** (2.54) и **pydantic-ai-harness** (0.54). Проверено по исходникам установленных пакетов.

Главная мысль: в Pydantic AI уже есть отличный механизм расширения — **capability**. Это объект, который добавляет агенту инструкции, инструменты, настройки модели и хуки на каждый шаг цикла. pydantic-ai-harness — это 30+ готовых capability (файлы, shell, планирование, память, сабагенты, компакция контекста, skills, AskUser, ACP и т.д.). Поэтому **плагин tether = capability плюс, по желанию, вклад в UI**. Свой агентный цикл, провайдеров и систему хуков не пишем.

---

## 1. Что даёт Pydantic AI, а что пишем сами

| Готово в pydantic-ai / harness | Пишем сами (наш слой) |
|---|---|
| Агентный цикл, стриминг событий (`run_stream_events`, `iter`) | **Загрузчик плагинов**: поиск, порядок, hot reload |
| Все провайдеры моделей (`'anthropic:...'`, `'openai:...'`, Ollama и др.) | **Конфиг**: один файл, из которого собирается `Agent` |
| Инструменты из функций со схемами из type hints | **Хост сессий**: история, запуск ходов, отмена |
| Хуки: `before/after/wrap` для run, model request, tool validate/execute, output | **Протокол фронтендов** и шина событий хоста |
| Подтверждения: `ApprovalRequired`, `HandleDeferredToolCalls` | Маршрутизация подтверждений и вопросов в UI |
| `AskUser`, `Coder`, `LocalWorkspace`, `Memory`, `Compaction`, `Subagents`, `Skills` ... | Вклады плагинов в UI: команды, статус, панели |
| AgentSpec: агент из YAML + `custom_capability_types` | TUI-фронтенд, web-фронтенд |
| Адаптеры UI: AG-UI, Vercel AI; `agent.to_web()`, `clai`; ACP (experimental) | |

Наш код получается маленьким: ядро ~1000 строк плюс два фронтенда.

## 2. Слои

```
 frontends (плагины):  TUI (Textual) · Web (FastAPI+WS) · ACP (Zed/Toad) · headless
                │ события хоста ▲ команды
 ───────────────▼───────────────┴────────────────────────────────
 tether host:   PluginLoader · Config · SessionHost · HostBus · UI registry
                │ собирает Agent(capabilities=[...]) и гоняет run_stream_events
 ───────────────▼────────────────────────────────────────────────
 pydantic-ai:   Agent · models · toolsets · capabilities/hooks
 pydantic-ai-harness: Coder, Memory, Planning, Compaction, AskUser, ...
```

## 3. Плагин

### Как пишется

Один `.py` файл в `~/.tether/plugins/` или `./.tether/plugins/`:

```python
# ~/.tether/plugins/git_guard.py
from tether import plugin
from pydantic_ai import RunContext
from pydantic_ai.exceptions import SkipToolExecution

p = plugin("git-guard", version="0.1")

@p.tool                                   # обычный tool pydantic-ai
async def git_status(ctx: RunContext, path: str = ".") -> str:
    """Показать git status репозитория."""
    ...

@p.on.before_tool_execute                 # те же хуки, что у pydantic_ai.capabilities.Hooks
async def no_force_push(ctx, *, call, tool_def, args):
    if call.tool_name == "shell" and "push --force" in str(args):
        raise SkipToolExecution("force push запрещён плагином git-guard")
    return args

@p.command("/gst")                        # вклад в UI, работает и в TUI, и в вебе
async def gst(session, argv):
    await session.send("покажи git status")
```

`plugin(...)` внутри собирает обычную capability (`Capability` с инструментами и инструкциями + `Hooks`), так что всё, что умеет pydantic-ai, доступно без обёрток.

Плагин посложнее — это просто класс `AbstractCapability` (переопределяет `get_toolset`, `get_instructions`, `wrap_model_request`, `wrap_tool_execute`, `on_event` и т.д.) (с декоратором `@dataclass`, этого требует `Agent.from_spec`). Любая готовая capability из pydantic-ai-harness — уже плагин, её достаточно указать в конфиге.

### Как находится

1. Пакеты через entry point `[project.entry-points."tether.plugins"]` (`uv add tether-plugin-foo`).
2. Файлы и папки в `~/.tether/plugins/` и `./.tether/plugins/` (проектные).
3. Всё из `pydantic_ai.capabilities` и `pydantic_ai_harness` доступно по имени без установки плагинов.

Загрузчик передаёт найденные классы в `Agent.from_spec(..., custom_capability_types=...)`, поэтому плагины можно включать и настраивать прямо в YAML-спеке агента.

### Hot reload

При изменении файла плагина модуль перезагружается, а `Agent` пересобирается из конфига **между ходами** (сборка агента дешёвая, история сообщений живёт в хосте, а не в агенте). Текущий ход не прерывается.

## 4. Конфиг

`tether.yaml` = родной AgentSpec pydantic-ai плюс секция хоста:

```yaml
model: anthropic:claude-opus-5-5
instructions: Ты помощник Вадима.
capabilities:
  - LocalWorkspace: {root: .}
  - Coder: {}
  - Compaction: {}
  - git-guard: {}          # наш плагин
host:
  frontends: [tui, web]
  web: {port: 8765}
  approvals:
    shell: ask             # allow | ask | deny
```

Глобальный `~/.tether/tether.yaml` и проектный `./.tether/tether.yaml` сливаются. (Точные имена ключей для capability из harness проверю на этапе 1.)

## 5. Интерфейс: как подключаются фронтенды

**SessionHost** владеет историей, запускает `agent.run_stream_events(...)` и рассылает события всем подключённым фронтендам. Поток состоит из:

- родных событий pydantic-ai (`PartStartEvent`, `PartDeltaEvent`, `FunctionToolCallEvent`, `FunctionToolResultEvent`, `AgentRunResultEvent`) — это dataclass'ы, сериализуются через `TypeAdapter`;
- событий хоста: `ApprovalRequested`, `QuestionAsked` (от `AskUser`), `StatusChanged`, `Toast`, `UIChanged`.

Обратно фронтенд шлёт `UserInput`, `ApprovalAnswer`, `QuestionAnswer`, `RunCommand`, `Cancel`.

Подтверждения и вопросы связываются с UI так: хост подключает `HandleDeferredToolCalls` и `AskUser` с обработчиками, которые публикуют запрос в шину и ждут ответа от любого фронтенда. Первый ответивший побеждает.

Фронтенды (все — плагины):

- **TUI** на Textual, в том же процессе.
- **Web**: FastAPI отдаёт тот же поток в JSON по WebSocket плюс небольшой клиент. Одну сессию можно вести из терминала и браузера одновременно.
- **ACP** почти бесплатно: в pydantic-ai-harness есть `run_acp_stdio`, и tether сразу работает в Zed и Toad. Пока experimental.
- **headless** (`tether run "задача"`) печатает события в stdout.

Почему свой протокол поверх WebSocket, а не только AG-UI/Vercel AI: нам нужны несколько фронтендов на одну сессию и UI-вклады плагинов, а эти протоколы рассчитаны на один чат-клиент. Готовые адаптеры (`AGUIAdapter`, `VercelAIAdapter`) можно подключить отдельными фронтенд-плагинами, если захочется взять готовый React-интерфейс.

**UI-вклады плагинов** декларативны, каждый фронтенд рисует их как умеет:

```python
p.ui.command("/review", handler=review)
p.ui.status_item("tokens", lambda s: f"{s.usage.total_tokens} tok")
p.ui.panel("plan", render=lambda s: Markdown(s.plan_text))
```

Примитивы: `Text`, `Markdown`, `List`, `Table`, `Progress`, `Button`. Незнакомый примитив фронтенд показывает текстом.

## 6. Структура репозитория

```
tether/
  src/tether/
    api/        plugin.py ui.py events.py      # публичный API для авторов плагинов
    host/       loader.py config.py session.py bus.py approvals.py
    frontends/  tui/ web/ acp/ headless/
  examples/plugins/
  tests/
```

Плагины импортируют только `tether.api` и `pydantic_ai`.

## 7. План MVP

1. Загрузчик плагинов, конфиг → `Agent`, SessionHost, headless-фронтенд. Проверить на `Coder` + `LocalWorkspace`.
2. Шина хоста, подтверждения и `AskUser` через фронтенд; hot reload.
3. TUI на Textual.
4. Web: FastAPI + WebSocket + минимальный клиент.
5. ACP-фронтенд и пара примеров плагинов, гайд «плагин за 5 минут».

## 8. Риски

- pydantic-ai-harness в статусе Alpha (0.x), ACP и часть возможностей experimental. Пиним версии и обновляем осознанно.
- Хуки pydantic-ai работают внутри одного прогона агента; всё, что живёт дольше (сессии, UI), остаётся в нашем хосте.

## 9. Открытые вопросы

- Это продолжение идей из Rust-проекта loom или отдельный проект?
- Нужен ли web на другой машине с авторизацией или только локально?
