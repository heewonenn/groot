# Hackathon workspace readiness

Historical environment audit from 6 November 2026. The 94/100 score below measures
the initial workstation setup, not product readiness or AI accuracy. The current
participant 1 implementation and remaining MVP work are described in the
[participant 1 guide](PARTICIPANT_1_GUIDE.md). Since this audit, private DEV artifacts
and the SQLite checklist module have been added, and an `origin` remote configured.
Treat the original inventory and test counts below as a dated snapshot.

Audit date: **6 November 2026**, Asia/Qyzylorda. Scope: this Windows workstation and
`C:\Users\1\Documents\ChatGPT\GROOOOOT`. **Local development ready: 94/100**.
No repository was published, no remote push was performed, and no paid external API was configured.

## Installed tools

| Tool | Version | Status and verification |
|---|---|---|
| Windows | NT 10.0.26200.0 | OS API checked; friendly edition unavailable through sandboxed CIM |
| PowerShell | 7.6.9 | Shell version checked |
| Git | 2.55.0.windows.5 | Existing install; repository on `main`, identity configured, credential helper `manager` |
| Python | 3.14.3 | Existing install; isolated `.venv` created and tested |
| Node.js | 24.13.1 | Existing LTS line; Vite and browser tests ran |
| npm | 11.8.0 | Install, lockfile, audit and scripts worked |
| VS Code | 1.144.0 | CLI version and extension listing verified |
| Codex CLI | 0.167.0-alpha.3.2 | Existing bundled install; ChatGPT login and two model runs worked |
| Codex VS Code extension | 26.1021.22027 | `openai.chatgpt` already installed |
| Python extension | 2026.10.0 | Already installed; inventory verified |
| Pylance | 2026.4.1 | Already installed; inventory verified |
| Jupyter extension | 2025.9.1 | Installed; inventory verified |
| Ruff extension | 2026.94.0 | Installed; inventory verified |
| ESLint extension | 3.0.34 | Installed; inventory verified |
| Tailwind IntelliSense | 0.16.0 | Installed; inventory verified |
| GitLens | 19.5.0 | Installed; inventory verified |

Extension presence does not prove activation inside an open VS Code window. Reload and select
the interpreter as described below. Existing unrelated extensions were preserved.

## Isolated dependencies

| Package | Version | Verification |
|---|---|---|
| fastapi | 0.162.0 | Installed in `.venv`; imports/tests or CLI checked |
| pydantic | 2.14.3 | Installed in `.venv`; imports/tests or CLI checked |
| uvicorn | 0.54.0 | Installed in `.venv`; imports/tests or CLI checked |
| pandas | 3.1.1 | Installed in `.venv`; imports/tests or CLI checked |
| numpy | 2.5.4 | Installed in `.venv`; imports/tests or CLI checked |
| scikit-learn | 1.9.1 | Installed in `.venv`; imports/tests or CLI checked |
| joblib | 1.6.0 | Installed in `.venv`; imports/tests or CLI checked |
| jupyter | 1.1.1 | Installed in `.venv`; imports/tests or CLI checked |
| matplotlib | 3.11.2 | Installed in `.venv`; imports/tests or CLI checked |
| plotly | 7.1.0 | Installed in `.venv`; imports/tests or CLI checked |
| pytest | 9.2.0 | Installed in `.venv`; imports/tests or CLI checked |
| httpx | 0.28.1 | Installed in `.venv`; imports/tests or CLI checked |
| ruff | 0.17.4 | Installed in `.venv`; imports/tests or CLI checked |
| react | 19.3.1 | Project-local; build/lint/browser or MCP checks passed |
| typescript | 6.0.3 | Project-local; build/lint/browser or MCP checks passed |
| vite | 8.3.6 | Project-local; build/lint/browser or MCP checks passed |
| tailwindcss | 4.3.5 | Project-local; build/lint/browser or MCP checks passed |
| axios | 1.21.0 | Project-local; build/lint/browser or MCP checks passed |
| lucide-react | 1.70.0 | Project-local; build/lint/browser or MCP checks passed |
| recharts | 3.10.1 | Project-local; build/lint/browser or MCP checks passed |
| eslint | 10.14.0 | Project-local; build/lint/browser or MCP checks passed |
| @playwright/test | 1.65.0 | Project-local; build/lint/browser or MCP checks passed |
| @playwright/mcp | 0.0.84 | Project-local; build/lint/browser or MCP checks passed |

`backend/requirements.lock.txt` pins the complete Windows/Python 3.14 environment;
`frontend/package-lock.json` pins npm dependencies. No global Python/npm package installs were needed.
Jupyter CLI reports its installed core components. Recharts is installed and resolved, but the
minimal UI does not render a chart. Jupyter notebook execution/VS Code kernel UI was not tested.

## MCP servers and GitHub

| Integration | Connected | Authentication | Harmless functional test |
|---|---|---|---|
| OpenAI Docs | Yes; 5 tools | Public, no token required | Live `search_openai_docs` returned content |
| Playwright MCP | Yes; 25 tools | Local stdio, no account | Isolated headless Chromium opened `about:blank`, snapshot returned, browser closed |
| Figma connector in this chat | Yes | Existing account | `whoami` returned authenticated identity |
| Standalone Figma MCP | Yes; 45 tools | OAuth reported by Codex app-server | OAuth login completed and authenticated tool inventory returned; no design-file read attempted |
| GitHub connector | Yes | Existing account | `get_profile` returned the authenticated profile; repository-specific scopes untested |

Official endpoints: `https://developers.openai.com/mcp` and `https://mcp.figma.com/mcp`.
User-level Codex registrations are installed; `.vscode/mcp.json` supplies equivalent VS Code settings.
VS Code's own MCP client may require separate trust/login. The current chat's tool inventory is
not dynamically replaced by editing those files.

Playwright MCP and the Playwright test runner depend on different browser revisions. Both were
installed. The full MCP Chromium executable failed to spawn, so the verified launcher
`frontend/scripts/playwright-mcp.mjs` selects the matching headless-shell revision automatically.
It keeps an isolated browser profile and does not disable global security settings.
The launcher currently targets Windows. Its global registration contains this checkout's path.

Although you chose to defer Figma login, the pending CLI login had already returned
`Successfully logged in`; a subsequent app-server query confirmed OAuth and 45 tools.
There is no remaining standalone Figma login blocker. No credential contents were read or printed.
Figma's connector reports a View seat; actual design-file permissions and feature limits remain
unverified until a file is selected.

## Skills

| Skill | Location | Format validation | Discovery |
|---|---|---|---|
| ml-engineer | `.agents/skills/ml-engineer/SKILL.md` | Passed | Enabled; app-server and live Codex context |
| frontend-design | `.agents/skills/frontend-design/SKILL.md` | Passed | Enabled; app-server and live Codex context |
| backend-engineer | `.agents/skills/backend-engineer/SKILL.md` | Passed | Enabled; app-server and live Codex context |
| hackathon-qa | `.agents/skills/hackathon-qa/SKILL.md` | Passed | Enabled; app-server and live Codex context |
| ml-experiment-reviewer | `.agents/skills/ml-experiment-reviewer/SKILL.md` | Passed | Enabled; app-server and live Codex context |
| hackathon-pitch | `.agents/skills/hackathon-pitch/SKILL.md` | Passed | Enabled; app-server and live Codex context |

All six passed the bundled `quick_validate.py`. Automatic invocation remains enabled.
One delegated agent created the six skills; the main agent integrated and verified them.

## Native agents

Native multi-agent is enabled in this Codex build. Project concurrency is capped at four.

| Role | Configuration | Availability |
|---|---|---|
| ml_engineer | `.codex/agents/ml_engineer.toml` | Discovered in live Codex context |
| backend_engineer | `.codex/agents/backend_engineer.toml` | Discovered in live Codex context |
| frontend_engineer | `.codex/agents/frontend_engineer.toml` | Discovered in live Codex context |
| qa_engineer | `.codex/agents/qa_engineer.toml` | Discovered in live Codex context |
| architecture_reviewer | `.codex/agents/architecture_reviewer.toml` | Discovered in live Codex context |

Each role has file-ownership boundaries and validation requirements. ML and architecture roles
select Astra/Ultra; implementation and QA roles select Sol/High. Agent discovery was verified,
but all five roles were not separately spawned for artificial tasks. No background roles remain
running from this setup. The live Codex probe also quoted an actual rule from `AGENTS.md`, confirming
that repository instructions were loaded.

## Models and reasoning

Authenticated app-server `model/list` returned these visible models (no pagination remaining):

- `gpt-6.1-sol`
- `gpt-6-astra`
- `gpt-6.1-terra`
- `gpt-6-sol`
- `gpt-6-luna`
- `gpt-5.6-sol`
- `gpt-5.6-terra`
- `gpt-5.6-luna`

All listed Sol/Terra models and Astra expose `low`, `medium`, `high`, `xhigh`, `max`, `ultra`.
The two Luna models expose those levels through `max`, without `ultra`.
Hidden internal models were excluded from recommendations.

For complex work, **GPT-6 Astra / Ultra** is configured as the user default and in the
`hackathon-quality` profile. The catalog describes Astra as the model for the most demanding work;
Ultra is the highest exposed reasoning setting. For routine edits, **GPT-6.1 Sol / Low** is in
`hackathon-routine`. The installed CLI uses `~/.codex/<name>.config.toml` for named profiles.
Astra/Ultra and Sol/Low both completed real read-only smoke prompts through the existing ChatGPT login.
The remaining listed models were discovered, not individually inference-tested. No subscription tier,
quota, or unlimited availability is inferred from discovery.

Project config originally overrode the routine profile. This was corrected: the shared project file
now sets agent concurrency, and model defaults/profiles live in user configuration. Existing unrelated
user settings and MCP registrations were preserved. A running chat may retain its prior selection.

## Executed checks

| Check | Result |
|---|---|
| Python dependency consistency (`pip check`) | Passed |
| ML/scientific library imports | Passed |
| Synthetic scikit-learn fit and Joblib save/load | Passed; not a product metric |
| FastAPI health response and unknown route | Passed |
| Python smoke suite | **3 passed** |
| Ruff on backend and setup Python utility | Passed |
| ESLint | Passed |
| Strict TypeScript + production Vite build | Passed |
| Actual Uvicorn and Vite startup | Passed through Playwright-managed servers |
| Chromium integration suite | **2 passed**: connection, keyboard retry, narrow viewport, API failure and recovery |
| Public Docs MCP search | Passed |
| Playwright MCP browser operations | Passed |
| Skills validator and discovery | All 6 passed/enabled |
| Native agent discovery and AGENTS.md loading | Passed live Codex verification |
| npm vulnerability audit | **0 known vulnerabilities** |
| Python vulnerability audit | **0 known vulnerabilities** |
| Secret-pattern scan | No matches in 64 initial unignored project files |
| Sensitive path ignores | `.env`, `.venv`, node_modules, private data and models ignored |
| Git | `main`, configured identity, no remote, no commits or pushes |

The latest Vite starter selected TypeScript 7, incompatible with the installed typescript-eslint
peer range. TypeScript was pinned to the compatible 6.0 line. A transitive minimatch advisory was
resolved using an explicit patched `^11.0.1` override, followed by clean audit and passing lint/build/tests.
Revisit that override when the upstream lint dependencies adopt the patched major themselves.

Nonblocking warning: current Starlette deprecates its HTTPX TestClient adapter in favor of HTTPX2.
The requested HTTPX stack passes today; no warning suppression was added. Codex also emits existing
PowerShell shell-snapshot and plugin-icon warnings; neither blocked model or discovery checks.

## Security and reliability boundaries

No credentials were placed in source or frontend variables. Local servers bind loopback by default.
The workspace is owned by the current Windows user; ownership was checked, not a full OS ACL audit.
No administrator install, global security change, public repository, or external API billing was used.
Package audit results are advisory snapshots, and heuristic secret scanning is not proof that arbitrary
future datasets or files are safe. External MCP output must be treated as untrusted reference content.
No private datasets exist in this starter. Add a database only when the product requires one.

## Remaining user steps

1. Open this folder in VS Code and run **Developer: Reload Window** from Ctrl+Shift+P.
   Run **Python: Select Interpreter**, choose `.venv\Scripts\python.exe`, and select that environment
   in the notebook kernel picker. Confirm Python analysis, Ruff, ESLint and notebook UI activation.
2. In the Codex sidebar, use the model and reasoning controls beneath the composer. Existing chats
   may need Astra/Ultra selected manually. CLI profiles and direct override commands are in README.
3. For a new teammate's Windows checkout, install Python 3.14 and a compatible Node LTS, then run
   `pwsh -NoProfile -File scripts/setup.ps1` and `pwsh -NoProfile -File scripts/verify.ps1`.
   Reproduction on a second machine has not been tested. Each teammate authenticates their own tools.
4. Supply the actual Figma file and GitHub repository when needed so design reads and repository
   scopes can be tested. No remote was guessed, no GitHub CLI token was created, and GitHub CLI is
   not installed because the connected GitHub integration is available.
5. Choose the hackathon problem, dataset and demo path. The starter deliberately has no fabricated
   model, inference endpoint, database or performance claims. Review files and make an initial local
   commit when ready. Remote creation/pushing still requires your approval.

## Score criteria

| Area | Earned | Maximum | Basis |
|---|---:|---:|---|
| Runtimes and isolated dependencies | 20 | 20 | All required tools/dependencies installed and compatible |
| Reproducibility and dependency hygiene | 20 | 20 | Both lockfiles, setup/verification scripts, clean audits |
| App and test execution | 25 | 25 | API, build, lint, ML plumbing and real-browser checks passed |
| Codex configuration | 15 | 15 | Auth, model runs, skills, agents and instructions verified |
| Connected integrations | 10 | 12 | Missing specific Figma-file and GitHub-repository scope tests |
| Editor/team handoff | 4 | 8 | Live extension/kernel activation and second-machine setup unverified |
| **Total** | **94** | **100** | Local development ready; product/demo readiness depends on requirements |

## Evidence and sources

Local evidence: `verification.log`, `codex-audit.json`, `codex-runtime-check.txt`,
`routine-model-check.txt`, `mcp-audit.json`, `npm-audit.json`, `python-audit.json`,
`security-scan.json`, `vscode-extensions.txt`. Logs are intentionally ignored by Git.

The configuration follows [official Codex subagent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[skill discovery documentation](https://learn.chatgpt.com/docs/build-skills), and
[OpenAI Docs MCP setup](https://developers.openai.com/learn/docs-mcp).
Frontend integration follows [Vite](https://vite.dev/guide/) and
[Tailwind's Vite integration](https://tailwindcss.com/docs/installation/using-vite).
Browser tooling uses [Microsoft's official Playwright MCP](https://github.com/microsoft/playwright-mcp).
Node 24 remains on an [LTS release line](https://nodejs.org/en/about/previous-releases).
