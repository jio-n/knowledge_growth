# AGENTS.md — knowledge_growth

This repository is a local-first research paper reading workspace.

## Current baseline vs. redesign target

- The currently implemented baseline is the July 2026 MVP documented under `docs/handoff/`.
- The active redesign target is **v0.4** under `docs/redesign/v0.4/`.
- Do not assume redesign features are implemented just because they are specified.

## Source of truth for redesign work

Read only the files relevant to the assigned task, in this order:

1. `docs/redesign/v0.4/README.md`
2. `docs/redesign/v0.4/knowledge_growth_mvp_spec_v0_4.md`
3. `docs/redesign/v0.4/knowledge_growth_implementation_tasks_v0_4.md`
4. Task-specific specs:
   - `docs/redesign/v0.4/paper_brief_schema_v0_1.md`
   - `docs/redesign/v0.4/import_bridge_spec_v0_1.md`
   - `docs/decisions/ADR-006_subscription_first_ai_runtime_and_import_bridge.md`

Existing architecture contracts remain relevant where v0.4 does not supersede them:
`docs/architecture/`, `docs/product/`, `docs/decisions/`.

## Non-negotiable product constraints

- **Do not require metered API billing for the MVP.**
- Primary target AI runtime: **Codex app-server + ChatGPT plan**.
- ChatGPT Import Bridge (`.kgpack`) is an MVP feature.
- Metered OpenAI/Anthropic/other API providers may remain an abstract future extension point, but are not an MVP dependency.
- AI-unavailable mode must still allow original PDF reading, notes, highlights, imported knowledge, and exports.
- Keep original-source provenance. Japanese translations, AI outputs, user notes, and source facts must remain distinguishable.
- Never silently guess unsupported Paper Brief fields. Use statuses such as `not_reported` or `uncertain`.
- Preserve SourceAnchor behavior and user-created data across migrations.
- Figures/tables/equations are first-class reading evidence, not secondary decoration.

## Technical constraints

- Keep the existing **FastAPI + SQLite + build-free Vanilla JS** architecture unless the task explicitly requires otherwise.
- Keep LLM/AI calls behind an adapter boundary; UI and persisted research objects must not become Codex-specific.
- Schema changes require a migration and documentation update.
- Prompt changes belong in `prompts/*.md`; do not embed long prompts directly in route code.
- Do not overwrite user-origin `knowledge_items` from automated re-analysis.
- Do not commit runtime `data/`, credentials, API keys, or user/private papers.

## PDF and test-data policy

Cloud development must use:
- generated fixtures, or
- files with explicit reusable/open licenses.

Do **not** add:
- subscription/purchased publisher PDFs,
- private/unpublished papers,
- NDA/customer/company material,
- arbitrary user research PDFs

to the repository or cloud test fixtures.

When a real paper exposes a bug locally, create a minimal synthetic fixture that reproduces the layout/problem, then fix it in cloud development.

## Development workflow

1. Run the baseline tests before material changes:
   `python -m pytest tests/ -q`
2. Implement only the requested phase/task IDs. Do not implement the whole roadmap in one task.
3. Add or update tests for observable acceptance criteria.
4. Run the relevant test suite.
5. Update affected docs together with code.
6. At task completion, report:
   - changed files
   - implemented task IDs
   - test results
   - known limitations
   - next recommended task

## Environment setup

Codex Cloud setup command:

```bash
bash scripts/setup_codex_cloud.sh
```

Windows local one-click launcher:

```text
start_knowledge_growth.bat
```

The first local launch creates `.venv`, installs dependencies, fetches vendored browser libraries, and then starts the app. Subsequent launches reuse the prepared environment.
