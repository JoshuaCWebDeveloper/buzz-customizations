# Buzz Customizations

This repository contains independent Buzz customization projects. Each package owns its implementation, tests, documentation, and explicit deployment actions; there is no shared plugin framework.

## Prerequisites and install

Use Node.js with npm and Python 3.9 or newer. Install the pinned workspace dependencies with:

```sh
npm ci
```

Do not use pnpm. Nx runs from the pinned local dependency through `npx nx` or npm scripts.

## Projects and standardized targets

Packages live under `packages/` and are discovered from npm workspaces plus Nx project configuration:

```sh
npx nx show projects
npx nx show project channel-context
```

Every customization should expose:

- `test`: complete project tests.
- `lint`: language-appropriate static validation.
- `deploy`: an explicit, user-invoked deployment action.
- `undeploy`: an explicit removal or rollback action.

The safe aggregate check is `npm run verify`, which runs `test` and `lint` for every package. Deployment targets are excluded from verification, tests, and CI-style validation.

## Package index

- [`packages/channel-context`](packages/channel-context/README.md): deterministic Buzz channel context for Codex and Grok, from `/var/lib/buzz/channel-context/<uuid>/`.
- [`packages/custom-grok-acp`](packages/custom-grok-acp/README.md): drop-in `grok-acp` wrapper with a hook interface for Grok Build prompt and context control.
- [`packages/base-prompt`](packages/base-prompt/README.md): deploys the managed Buzz base prompt.
