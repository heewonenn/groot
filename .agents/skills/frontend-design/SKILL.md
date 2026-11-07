---
name: frontend-design
description: Implement or refine React and TypeScript interfaces, responsive dashboards, and accessible states, including provided Figma designs.
---

# Frontend design

Inspect frontend/ and existing components, styles, routes, and API contracts first. Preserve the established visual language. For a Figma reference, retrieve its design context through the available official integration; load its design-to-code skill if installed. If access is unavailable, identify the missing evidence instead of claiming visual fidelity.

Implement the smallest usable React flow in TypeScript. Reuse Tailwind tokens, shared components, Lucide icons, and existing libraries. Use Axios with typed responses and explicit timeout/error handling. Only introduce Recharts when actual data needs a chart; label axes, units, and empty states, and do not invent metrics.

Cover loading, success, empty, validation, and failure states. Use semantic controls, associated labels, keyboard navigation, visible focus, sufficient contrast, and status announcements. Keep credentials and private data out of Vite client variables and browser logs. Explain significant departures from the supplied design.

Verify the configured lint and TypeScript/build commands, then exercise the affected flow in Playwright. Inspect narrow and desktop viewports, keyboard operation, errors, and overflow. Compare against the reference when supplied. Report executed checks and remaining visual or interaction gaps; screenshots alone do not prove functionality.
