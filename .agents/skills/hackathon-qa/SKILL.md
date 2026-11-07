---
name: hackathon-qa
description: Verify API and browser integration, investigate regressions, and assess the critical demo path before a hackathon handoff.
---

# Hackathon QA

Read the README, test configuration, current changes, and intended demo flow. Identify the user-visible acceptance criteria and prioritize the path that must work in front of judges.

Run the repository's existing Python tests, Ruff, frontend lint, TypeScript/build, and Playwright checks as relevant. Record commands, exit codes, and failures; a skipped or unexecuted test is not a pass. Use deterministic fixtures and temporary files, not private production data.

Exercise the real browser-to-API path, not only mocked routes. Cover successful use, bad input, unavailable backend, loading and empty states, keyboard access, and narrow screens. Check browser console errors and API response contracts. Ensure server startup waits for readiness and test cleanup stops only processes started by the test.

For a failure, reproduce it minimally and separate environment problems from product defects. Fix within the assigned scope or hand off a concrete reproduction with expected/actual behavior and relevant evidence. After a fix, rerun the failing check and affected integration checks.

Deliver a demo readiness result listing observed passes, blocking failures, and unverified dependencies. Include launch commands, required environment variables by name only, and a fallback demo scenario based on already verified functionality. Never publish or send data as part of a test without authorization.
