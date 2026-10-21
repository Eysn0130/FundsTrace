# AGENTS.md

## Scope and project map

- FundsTrace is a local application for transaction tracing and case analysis.
- `run.py` starts FastAPI; `app/` contains Python code; `static/` retains the G6 UI; `frontend/` contains the React pilot served at `/workspace/`.
- `desktop/` is the independent Electron host; `desktop_backend.py` is its owned Python sidecar. Keep desktop identity/data/process ownership separate from Analytix. Desktop commands and Windows evidence limits are in `docs/desktop-delivery.md`.
- This directory is the maintained source candidate. The original `fund-tracker` directory is preserved; do not change it or read its business data for development.
- For source/data boundaries and licensing limitations, read `docs/source-boundaries.md`.
- For naming, module layout and later Rust/React work, read `docs/evolution.md` when the task concerns architecture or frontend migration.

## Setup and verification

- The initial verified development target is Python 3.11. Install with `uv venv --python 3.11` and `uv pip sync requirements-dev.lock`.
- Run regressions with `.venv/bin/python scripts/test.py` (Windows: `.venv\Scripts\python scripts\test.py`). The runner always creates temporary data and overrides inherited runtime settings.
- The full regression suite also uses Node (verified 22.22.1) for the synthetic legacy-navigation harness; it never launches a browser or business server.
- Direct pytest also uses synthetic temporary state through `tests/conftest.py`; `testpaths` is restricted to the new `tests/` directory.
- With Node 22.22.1 (the verified baseline), run `cd frontend && npm ci && npm run build && npm test`. The project `.npmrc` is part of the reproducible install; no global npm changes are needed.
- API changes require `.venv/bin/python scripts/export_api_contract.py --check`. Regenerate both `contracts/` outputs from the Python schema when intentionally changing v1; do not hand-edit generated types.
- Desktop checks: `npm --prefix desktop test`; source-only disposable GUI: `npm --prefix desktop run synthetic`. Native backend builds require Python 3.11 plus `requirements-desktop.lock`. Run `scripts/build-windows.ps1` in PowerShell 7 on Windows x64; it checks the actual directory candidate's resources and backend. The local manual Actions workflow reuses it; authoring a workflow does not authorize upload or remote execution. Never package a macOS backend as Windows or treat a configuration file, simulated viewport or empty-state crash smoke as Windows GUI/DPI/import-recovery evidence.
- `scripts/preview_synthetic.py` serves the built UI using disposable synthetic state on loopback. It overrides inherited data/signing settings and removes its runtime on exit.
- `scripts/benchmark.py --check` is correctness smoke only. Follow `docs/performance.md` for timed runs; do not run builds/tests or competing performance workers during sampling.
- Legacy `dbg_*`, `patch_*`, packaging copies and recovery scripts are excluded. Do not execute them as tests or copy their data into fixtures.
- Use synthetic transactions and temporary databases. Do not connect test clients to a running business instance.
- Report only checks actually executed. Unit/API checks, browser behavior, installed packages and release qualification are different evidence.

## Data contracts

- Preserve case isolation, decimal amounts, transaction direction, timestamps and source traceability.
- The v1 API represents IDs and monetary amounts as strings, nullable balances as `null`, and totals separately by currency. Keep this lossless boundary through React; never use `Number` for amounts or IDs.
- Legacy monetary fields now follow the same decimal-string rule. See `docs/money-contract.md`: calculate from raw Decimal, separate currencies, retain cents through graph/report/Excel, and distinguish rounded means/statistical scores/display geometry from exact sums. Update provenance version when changing an import parser.
- Import sessions and progress belong to one case. Preserve binding checks in both versioned and legacy routes; changing case must discard stale requests and displayed state.
- `frontend/src/panel/index.ts` is the shared React entry. Keep browser routing, case selection, HTTP and file/download navigation in the standalone shell/adapters. An embedded host supplies a case-bound, versioned session and explicitly revokes it when its context expires; a UI port is not authorization or a protected-display contract.
- Panel CSS must stay within `.funds-trace`, adapt to its containing slot, and avoid document resets. Check same-case rebinds, revocation, stale callbacks/responses and missing host actions when changing the panel boundary.
- Keep import orchestration in `app/services/imports.py`; HTTP surfaces adapt this service. Preserve durable receipts in the same transaction as flows, accounts and provenance; startup recovery never automatically replays writes.
- New import operational timestamps use explicit UTC; do not reinterpret transaction times. Cleanup acts only on registered expired uploads after rechecking activity and source references; never discover authority by scanning files. See `docs/import-lifecycle.md`.
- Original transactions, graph display deduplication and analytical aggregates have distinct meanings; explain any changed rule.
- `static/graph-view.js` is display policy only. Neighborhood filtering must not mutate the complete tracked graph or exact money. Canvas saves retain all graph data; visual exports declare the current view and label visibility. Revoke old graph facts as soon as a case/canvas binding changes, including A–B–A switches and delayed layout/photo/export callbacks. See `docs/graph-interaction.md` for the synthetic browser benchmark and its timing limits.
- Viewport culling hides drawing groups only. Include real path/label bounds and crossing edges; invalidate geometry after editing. G6 `model.label` is optional for lazy drawing; `fundsLabel` and source metadata retain full text/money for persistence and export. Structural layout anchors are geometry, not business-role decisions. Follow `docs/graph-viewport.md`; `--graph-assets` is synthetic-preview-only and must not be added to production runtime configuration.
- `static/graph-labels.js` selects labels in screen space without changing facts. Preserve explicit all/export modes, manual path ratios and hidden-label snapshot coordinates across geometry changes. See `docs/graph-render.md` for the current performance evidence. The local G6 4.8.19 membership patch is pinned by complete hashes; `scripts/patch_g6_validation.py --check` verifies it. Do not replace or reapply it to a different vendor version without reviewing the validation contract and regressions.
- Treat each import, case-package operation and multi-table deletion as a complete operation; protect it with the database transaction/lock boundary.
- The maintainer's confirmed workspace design allows the query sidebar and editor tools to collapse independently. Hover/focus reveals the seam controls; only clicking changes panel state. Collapsed tools retain direct save/undo/redo/layout/fit controls. Preserve query/selection state and the G6 camera during shell resizing. Retain the real seven-column transaction table, full monetary strings and local horizontal scrolling rather than ledger/card rows. See `docs/graph-workspace-plan.md`.
- Keep existing node, relationship and edge rendering in `static/app.js`, `static/graph-*.js` and the pinned G6 bundle unchanged for the workspace delivery. `workspace-shell.js` owns chrome; `canvas-editor.js` adapts existing commands and `canvas-history.js` owns bounded, binding-scoped history. History contains IDs, geometry and display settings, never transaction facts or photo data. Restoring tracked membership must use case/canvas-bound atomic compare-and-set; a failed or ambiguous mutation must not be reported as a successful undo.
- Desktop bootstrap tokens travel only over the parent stdin pipe and Main-owned exact-origin request headers; never expose them in URLs, renderer state, IPC, logs or exports. Preserve this authentication when changing product activation. Source-only synthetic mode is rejected by frozen/distributed backends; do not ship its seed module or business data.
- Historical alpha packages use FT2 Ed25519 verification and the independent `desktop/issuer/` app; preserve their compatibility records and private signing material. Only SPKI public keys may enter client resources or the non-secret Actions variable. Never add private/shared secrets or default test keys to installers. The distribution policy below suspends mandatory activation; `docs/license-activation.md` records the historical licensed flow.
- Reject archive paths outside the documented package format. Temporary files must be operation-specific and cleaned up.
- A failed import must not leave a partial case, partial batch or orphan photo files. Cover failure and rollback paths as well as successful round trips.
- Changes to schemas and `.fundcase` need compatibility evidence. Keep existing runtime environment names and file formats unless the task authorizes migration.

## Windows distribution policy

- The maintainer's 2026-10-15 decision is that upcoming `FundsTrace v0.1.x` packages temporarily launch without an activation code. Normal Python/API and UI startup are activation-free, including packaged smoke checks; existing alpha binaries keep their historical behavior. Do not substitute synthetic mode or a shared test license for normal startup.
- Each concrete `v0.1.x` tag must have a corresponding GitHub Release with a downloadable Windows x64 NSIS `.exe`. Preserve FundsTrace's appId, data directory and upgrade identity. Default uninstall retains case data; installing a new version must close only FundsTrace-owned services safely.
- Write public Release notes as a short user-facing update: one introductory sentence if useful, then 2–5 concrete feature/improvement/fix bullets. Keep CI, implementation details and hashes in verification records. Follow `CONTRIBUTING.md`'s release-note convention; verify that analytix.top's download center shows the same latest version and notes before reporting domestic synchronization complete.
- Adapt the necessary Analytix installer/update design without modifying Analytix or copying its appId, registry keys, feed, signing identity or runtime cleanup list. Start with EXE installation/upgrade/uninstall, then add explicit in-app update checking. Keep update integrity checks and desktop request authentication independent of the suspended product-activation requirement. Details and verification limits are in `docs/desktop-delivery.md`.

## Working and delivery

- All Agent Harnesses share this file and `CONTRIBUTING.md`; `CLAUDE.md` imports this file instead of duplicating policy. If a harness does not load it automatically, explicitly read it before work. Personal memory, a skill or another agent's handoff does not grant merge, release, credential or data authority.
- Use a short branch from current `origin/main` and an isolated checkout for concurrent tasks. State the owned files and acceptance behavior; do not share an index or runtime data directory across writers. Routine fixes can go straight to a PR without a separate plan or Issue.
- Submit changes as a PR for Eysn0130 to review. Agent work ends at the reviewable PR unless the maintainer explicitly authorizes merging that current revision; do not approve your own PR, enable auto-merge, bypass protection or treat a green check as owner approval. Packaging and release remain separate maintainer decisions, not a requirement for each merge. The shared-account limitation and future independent-review setting are documented in `CONTRIBUTING.md`.
- Make the smallest complete change. Preserve other contributors' work and assign exclusive files for parallel edits.
- Bug fixes should have a regression through the public behavior where practical. Expected values come from independent synthetic examples.
- UI changes need user-flow verification; distribution changes need installed-entry verification. Do not claim either from API tests alone.
- Analytix integration is currently research only. `docs/analytix-integration.md` describes the host boundary; the standalone API is not a protected-display contract or permission to open a host database.
- Never include real accounts, identities, transactions, license records or signing material in fixtures, logs, documents or commits.
- Keep signing secrets outside source. Historical or explicitly enabled license verification must still reject missing/invalid verification material; future default startup follows the distribution policy above. Suspending activation does not authorize deleting keys or existing runtime data.
- Commit, push, remote changes, packaging and publication require current task authorization. The maintainer has authorized the public `Eysn0130/FundsTrace` repository under MIT, manual Windows Actions and unsigned preview releases. Report native build/sidecar checks accurately; Windows GUI acceptance is not a release label for these previews. This does not authorize access to original business data or changes to Analytix.
- PRs describe the problem, resulting behavior, checks and relevant compatibility limits. Stop once the authorized acceptance criteria pass.
