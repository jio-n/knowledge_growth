# Codex app-server protocol fixture

Current source: `codex-cli 0.160.1`, generated and compared on 2026-10-12 with:

```sh
codex app-server generate-json-schema --out /tmp/kg-codex-schema
codex app-server generate-ts --out /tmp/kg-codex-ts
```

`client_requests.schema.json` retains the eleven adapter request variants and their
transitive definitions unchanged from generated `ClientRequest.json`. These variants
and definitions are structurally identical to the original fixture, so its bytes
are unchanged (variant order is not significant).
Full generated ClientRequest SHA-256: `1c7fec8758deb95ffe967060130e8d1ddb7a0a5a437ee9b64086dddbed8f5831`.

Added unchanged generated response schemas:

- `GetAccountResponse.schema.json`, from `v2/GetAccountResponse.json`.
  SHA-256: `c3092dd8e52c2933c5ba5f0bf92377174f65bed627ff4ef5d7c04a189dd34fd1`.
- `LoginAccountResponse.schema.json`, from `v2/LoginAccountResponse.json`.
  SHA-256: `72e6d77f49bc5809c3af50f183e2a0862dd8f7836816d4796ff69bb13eae0e89`.

`generate-ts` confirms `GetAccountParams.refreshToken?: boolean`: true requests
a proactive token refresh. Normal state reads send `{refreshToken:false}`,
including startup, status refresh, login notifications and restart.
`PlanType.ts` includes `prolite`, `self_serve_business_prolite`, and
`enterprise_cbp_automation`. Known plans are normalized; unknown future values
remain authenticated and display `unknown`, rather than being response-schema
rejected by the runtime. Response schemas are test references, not runtime gates.

Current official sources:

- [GitHub app-server README](https://github.com/openai/codex/blob/6c372fbef45f730b6d8aa727b0ec8332365588db/codex-rs/app-server/README.md),
  main observed on 2026-10-12 (additional protocol contracts).
- [Official app-server documentation](https://developers.openai.com/codex/app-server#authentication-endpoints):
  normal account read uses false; browser example returns `https://chatgpt.com/...`.
- [Current login implementation](https://github.com/openai/codex/blob/6c372fbef45f730b6d8aa727b0ec8332365588db/codex-rs/login/src/server.rs):
  `DEFAULT_ISSUER` is `https://auth.openai.com`; browser URL is built from
  issuer + `/oauth/authorize`. Both hosts are exact allowlist entries.

History: the initial Phase 0A fixture was generated with `0.159.0-alpha.3` on
2026-10-04; its full ClientRequest SHA-256 was
`f0266f32d66be73d202c43a89b705cfd4b1fbdd6f58cc9a3fae754a7180cae65`.
That version and the old forced-refresh behavior are historical, not the current
recommended contract. The environment's preinstalled alpha schema/TS were also
generated for comparison; 0.160.1 and current official sources take precedence.

Fixtures contain no credentials, real account data, or real paper text.

`fake_server.py` is a deterministic subprocess fixture, not an actual Codex server.
It models response envelopes and notifications observed in the generated bindings.
Test-only CONFIG selects failure modes. Authentication reuse stores only an empty
synthetic marker in pytest tmp_path. No external account or network is required.
`null_on_force_refresh` models the Windows observation that true returns null
while false detects the saved account. `rpc_delays` models cold-start/handshake
latency, `plan` models forward compatibility, and `auth_url` exercises URL checks.
