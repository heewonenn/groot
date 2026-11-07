# CQA — Conversation Quality Analytics

A minimal Windows AI/ML workspace with FastAPI, React, isolated dependencies,
Codex skills, native agent roles, and API/browser smoke tests. The existing
repository root is used directly rather than nesting another `hackathon-ai` directory.

Product requirements: [Conversation Quality Analytics technical specification (Russian)](docs/TECHNICAL_SPECIFICATION.md).

The shared API now follows `CQA-team.zip`: port **8765**, `message_id` references,
and common chat **p1-ru-001**. The ZIP data router and participant 1 checklist
router run in the same FastAPI app. See [ZIP integration](docs/ZIP_INTEGRATION.md).

Participant 4 adds the [dialogue and recording screen](http://127.0.0.1:5173/conversations),
verified playback positions and persistent evaluation jobs to this app.
See [the participant 4 handoff](docs/PARTICIPANT_4_HANDOFF.md) for integration and verification.

Official submissions for team **WE ARE GROOT** are generated in `results/dev/` and
`results/prod/`. See [the submission commands and pre-push checks](results/README.md).

Participant 3 adds the [quality workspace](http://127.0.0.1:5173/analysis),
evidence navigation, persistent appeals and original/final score history.
Start with the [Windows launch and participant 3 guide](docs/PARTICIPANT_3_GUIDE.md).
The optional synthetic demo is enabled explicitly; it is not a model evaluation.
Participant 2 is now integrated with this workspace: explicit evaluation launches,
saved checklist versions, progress/retry, source quotes and model run metadata.
See [model integration and remaining configuration](docs/PARTICIPANT_3_MODEL_INTEGRATION.md).

Team handoff: [four-participant implementation plan](docs/TEAM_IMPLEMENTATION_PLAN.md) and [shared data contracts and fixtures](docs/contracts/README.md). DEV examples under `data/examples/team-zip-v1` are local and ignored by Git; public examples are synthetic and are not model-quality labels.

Participant 1 is implemented: checklist editor, immutable versions, SQLite persistence
and the shared score calculator. See [the participant 1 handoff (Russian)](docs/PARTICIPANT_1_HANDOFF.md)
for API examples, ownership and integration instructions for the other three participants.
For a plain-language explanation, manual checks and Codex skill examples, read
[the participant 1 user guide (Russian)](docs/PARTICIPANT_1_GUIDE.md).

## Как работаем вчетвером

Общий репозиторий команды — [heewonenn/groot](https://github.com/heewonenn/groot).
Ветка `main` содержит согласованную сборку. У каждого участника своя локальная
копия этого репозитория и отдельная ветка для своей задачи. Название папки на
компьютере может отличаться: важно, чтобы `git remote -v` указывал на этот репозиторий.

После публикации первой сборки участники 2–4 начинают с её копии:

```powershell
git clone https://github.com/heewonenn/groot.git
cd groot
git switch -c codex/participant-2
pwsh -NoProfile -File scripts/setup.ps1
```

В команде выше участник подставляет свой номер. Участнику 1, у которого уже есть
эта рабочая папка, повторно клонировать её не нужно.

| Участник | Рабочая ветка | Область |
| --- | --- | --- |
| 1 | `codex/participant-1` | Чек-листы, их версии и общий расчёт |
| 2 | `codex/participant-2` | Модель, проверка ответов и доказательств |
| 3 | `codex/participant-3` | Карточка диалога и апелляции |
| 4 | `codex/participant-4` | Данные, аудио, пакетная обработка и интеграция |

Работать можно параллельно по [границам файлов и задач](docs/TEAM_IMPLEMENTATION_PLAN.md).
Перед изменением общих полей API или правил расчёта участники согласуют изменение.
Готовые изменения отправляются в свою ветку и оформляются pull request в `main`.
Участник 4 проверяет совместную работу и объединяет изменения после проверки.
При работе через Codex отправка в GitHub требует разрешения владельца согласно `AGENTS.md`.

Если часть кода уже написана в другой папке, сохраните ту папку и перенесите
нужные изменения в свою ветку общей копии. Не заменяйте всю сборку очередным ZIP.
После объединения чужих изменений обновляйте свою рабочую ветку из `main`,
предварительно сохранив собственную работу коммитом.

Стартовые данные и API следуют `CQA-team.zip`: общий чат `p1-ru-001`, звонок
`p4-ru-001`, привязка `group_id + conversation_id + message_id`, сервер 8765.
Это одна локальная программа у каждого участника; одинаковый порт не означает
общий сервер в интернете. Импорт DEV каждый настраивает по
[инструкции подключения](docs/ZIP_INTEGRATION.md). Данные DEV, SQLite, ключи,
`.venv` и `node_modules` в Git не передаются; зависимости устанавливает setup.

## Start developing

Open this folder in VS Code. In two terminals at the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.model_app:create_configured_app --factory --reload --host 127.0.0.1 --port 8765
```

```powershell
cd frontend
npm run dev
```

Open http://127.0.0.1:5173. The page should say **API connected**.
Open http://127.0.0.1:5173/checklists for the checklist editor and manual arithmetic check.
API docs: http://127.0.0.1:8765/docs. Stop each server with Ctrl+C.
Alternatively run the `Backend: dev` and `Frontend: dev` VS Code tasks.
The factory includes all four modules. A provider is disabled until explicitly
configured; configuration alone does not create a model request. The original
`backend.app.main:app` remains available without the model audit routes.
No paid API, external database service, dataset or model is required. The checklist
module creates a local SQLite database at `data/cqa.sqlite3` on its first API request;
this file is ignored by Git. Set `CQA_DB_PATH` in the backend process environment to
use another private path. The starter health page does not require the database.

## Reproduce and verify

Requires Windows, Python 3.14, and Node 24 LTS (or a compatible newer LTS).
The Python lock was generated and tested on Windows x64/Python 3.14; it contains
Windows-specific dependencies and is not a cross-platform lock.

```powershell
pwsh -NoProfile -File scripts/setup.ps1
pwsh -NoProfile -File scripts/verify.ps1
cd frontend
npm run mcp:check
```

`setup.ps1` uses the Python lock, `npm ci`, and both Playwright browser revisions.
`verify.ps1` runs dependency consistency, Ruff, Pytest, ESLint, strict TypeScript,
Vite build and Chromium tests against actual backend/frontend servers.
The tests own ports 8765 and 5173; stop development servers before running them.
Browser tests use a disposable temporary database and a synthetic conversation.
The MCP check performs a public documentation search and opens an isolated blank browser.
It does not log into external accounts or modify browser profiles.

Select `.venv\Scripts\python.exe` with **Python: Select Interpreter** if VS Code
keeps an old selection. In notebooks, select the same environment as the kernel.
Launch Jupyter with `.\.venv\Scripts\python.exe -m jupyter lab --notebook-dir notebooks`.

## Codex

`AGENTS.md` supplies repository instructions; `.agents/skills` holds six discoverable
skills; `.codex/agents` holds five native roles. For example:

> Use $ml-engineer to inspect this dataset and propose a leakage-safe baseline.

> Have backend_engineer implement the agreed API and frontend_engineer implement
> the client in separate files; then ask qa_engineer to verify the integration.

On this computer, the default is GPT-6 Astra / Ultra. Named user profiles:

```powershell
codex -p hackathon-quality
codex -p hackathon-routine
```

The routine profile selects GPT-6.1 Sol / Low. Profiles live in
`%USERPROFILE%\.codex\hackathon-quality.config.toml` and
`%USERPROFILE%\.codex\hackathon-routine.config.toml`, the format supported by the
installed CLI. These profiles are local machine settings, not part of the repository.
For a direct session override:

```powershell
codex -m gpt-6-astra -c model_reasoning_effort=ultra
codex -m gpt-6.1-sol -c model_reasoning_effort=low
```

In VS Code, open the Codex sidebar and use the model/reasoning controls beneath
the message composer. Select GPT-6 Astra and Ultra for complex work, or GPT-6.1 Sol
and Low for routine work. An existing chat can retain its own selection; the setup
does not retroactively change a running conversation.

Official Docs, Figma and Playwright MCP are registered in your user Codex config.
`.vscode/mcp.json` additionally makes them available to VS Code's MCP client;
that client can require its own server trust/authentication steps. The Playwright
user registration points to this checkout; re-register if you move the folder.
Do not transfer credential files to teammates. See the readiness report for tested scope.

## Project and team conventions

- `backend/app`: API and shared calculator; `backend/tests`: unit, API and smoke tests.
- `frontend/src`: client; `frontend/tests`: browser tests.
- `notebooks`: exploration; `data` and `models`: ignored local artifacts.
- `docs`: audit evidence and decisions; `scripts`: setup and verification.
- Use `main` and focused `codex/<topic>` branches. Create the first commit before worktrees.
- Team changes are reviewed locally before synchronization. Publishing or pushing
  requires the user's authorization. Participant 3 changes are prepared for a
  pull request from `codex/participant-3` into `main` at the owner's request.
- Use `.env.example` as a template. Set `APP_NAME` in the process environment if needed;
  the backend does not automatically load `.env`. `CQA_SOURCE_URL` configures the
  organizer import; a private `data/team-api/source_url.txt` is the fallback.
  Never place secrets in frontend variables.
- Production requires its own API routing; Vite's development proxy is not a deployment.

See [the readiness report](docs/READINESS.md) for versions, results and remaining checks.
