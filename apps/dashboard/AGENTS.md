<!-- BEGIN:nextjs-agent-rules -->
# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.
<!-- END:nextjs-agent-rules -->

# Canonical Dashboard Instructions

This dashboard is a component of the single canonical source root. It keeps an
independent Node dependency boundary: run npm commands from `apps/dashboard`
and use this directory's `package-lock.json`. Do not create a root npm
workspace, merge lockfiles, hand-edit the lockfile, or import source from an old
repository checkout.

## Required workflow

- Preserve the Next.js 16 local-documentation block above exactly. Before
  changing Next.js APIs, conventions, routing, caching, or configuration, read
  the relevant installed guide under `node_modules/next/dist/docs/`.
- Install with `npm ci`; validate with `npm test`,
  `./node_modules/.bin/tsc --noEmit`, `npm run lint`, and `npm run build`.
- Browser-facing code must access external data through server-only routes and
  established Control API/Job API contracts. Never expose protected paths,
  credentials, tokens, database access, or private runtime data to the client.
- Do not add a production mutation route. No route may enable live trading,
  invoke a broker, place/cancel/modify an order, run a migration, or reconfigure
  a service.
- Keep tests paper-only. Integration tests may start only their isolated,
  self-cleaning local test server; no persistent server or production probe.
- Production runtime/data locations are external configuration. Do not resolve
  operational data relative to this source checkout or an old repository.

Keep `node_modules`, `.next`, coverage, logs, runtime data, and credentials
untracked. Ask first before dependency, root, runtime, production, service,
remote, or deployment changes.
