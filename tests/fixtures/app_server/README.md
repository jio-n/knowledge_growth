# Codex app-server protocol fixture

Source: `codex-cli 0.159.0-alpha.3`, generated on 2026-10-04 with:

```sh
codex app-server generate-json-schema --out /tmp/kg-codex-schema
codex app-server generate-ts --out /tmp/kg-codex-ts
```

`client_requests.schema.json` retains the eleven adapter request variants and their
transitive definitions unchanged from generated `ClientRequest.json`.
Full generated ClientRequest SHA-256: `f0266f32d66be73d202c43a89b705cfd4b1fbdd6f58cc9a3fae754a7180cae65`.
It contains no credentials, account data, or real paper text.

`fake_server.py` is a deterministic subprocess fixture, not an actual Codex server.
It models response envelopes and notifications observed in the generated bindings.
Test-only CONFIG selects failure modes. Authentication reuse stores only an empty
synthetic marker in pytest tmp_path. No external account or network is required.
