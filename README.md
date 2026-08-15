# Buzz Customizations

This repository contains independent Buzz customization projects. Each project owns its implementation, tests, documentation, and deployment action; there is no shared plugin framework.

## Prerequisites and install

Use Node.js with npm and Python 3.9 or newer. Install the pinned Nx dependency and generate the reproducible lockfile state with:

```sh
npm ci
```

Do not use pnpm for this repository. Nx is run from the pinned local dependency through `npx nx` or npm scripts.

## Nx project and target contract

Nx discovers projects from the npm workspace packages and their `project.json` files. List projects and targets with:

```sh
npx nx show projects
npx nx show project channel-context
```

Every customization should expose these targets:

- `test`: complete project tests.
- `lint`: static validation appropriate to the project's language.
- `deploy`: an explicit, user-invoked deployment action.
- `undeploy`: an explicit removal or rollback action.

The aggregate safe check is:

```sh
npm run verify
```

It runs `test` and `lint` for every project. Deployment targets are intentionally excluded from `verify`, ordinary tests, and CI-style validation.

## channel-context

`channel-context` is a Python Codex `UserPromptSubmit` hook. Its context layout is:

```text
$CODEX_HOME/channel-context/<buzz-channel-uuid>/
```

Directory names are Buzz channel UUIDs. The hook reads regular files only, sorts them alphabetically by filename, and concatenates their contents exactly. Missing or empty directories and malformed/non-Buzz prompts fail open without additional context. Output and input are bounded.

Run its safe targets with:

```sh
npx nx run channel-context:test
npx nx run channel-context:lint
```

Deployment is intentionally separate and must be directed at the intended Codex home. Use the explicit actions only when authorized:

```sh
npx nx run channel-context:deploy -- --codex-home /path/to/staging-codex-home
npx nx run channel-context:undeploy -- --codex-home /path/to/staging-codex-home
```

Installation preserves unrelated hook configuration, creates a stable first-install backup at `hooks.json.buzz-customizations-backup`, and atomically replaces `hooks.json`. Reinstalling does not overwrite that rollback artifact. Restore the backup manually for rollback after verifying the target home. No active agent-1 home is modified by repository validation.
