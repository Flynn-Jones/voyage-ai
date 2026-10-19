# Pre-commit security report: shared infrastructure (`ai-services/`, `shared/`)

- Scope: `ai-services/` and `shared/` (verbose `files:` regex, one folder per line, in `.pre-commit-config.yaml`)
- Tools: ruff 0.17.1 (`ruff.toml`: E4/E7/E9/F + S), detect-secrets 1.5.0, pip-audit 2.10.1
- Scan: `scripts/test/precommit-report.sh shared`. Raw output is in the `pre-commit-<UTC>.txt` file next to this report.
- Result: **PASS** on all three hooks, after the fixes and justified acceptances below.

| Hook | Findings before | Fixed | Accepted | After |
|---|---|---|---|---|
| ruff (S + defaults) | 13 (S104 ×5, S310 ×2, S110 ×1, E402 ×3, plus S201 found by review, not by Ruff) | 2 | 11 | Passed |
| detect-secrets | 0 | – | – | Passed (empty baseline) |
| pip-audit | 4 advisories (chromadb), 0 elsewhere | 0 (no fix released) | 4 | Passed |

`S101` (`assert`) is allowed in tests only (`ruff.toml` `per-file-ignores`), because pytest uses asserts as its
mechanism. That accounts for about 300 test asserts.

## Fixed

| Location | Rule | Change |
|---|---|---|
| `ai-services/rag-server/rag_pipeline.py` `reset_collection` | S110 try-except-pass | Logs the exception (`logger.info`) instead of discarding it. Behaviour is unchanged on a first build. |
| `shared/database/src/app.py` | S201 Flask `debug=True` (found by review; Ruff could not resolve `app` from `create_app()`) | The Werkzeug debugger now runs only with `FLASK_DEBUG=1`. A reachable debugger allows remote code execution. |

## Accepted (justified inline with `# noqa: <rule> - <reason>`)

| Location | Rule | Justification |
|---|---|---|
| `ai-services/mcp-server/server.py` (`MCP_HOST` default) | S104 | The host service is reached by containers via `host.docker.internal`, which a loopback-only bind breaks. `MCP_HOST=127.0.0.1` restricts it for host-only use. It is never deployed to the cloud. |
| `ai-services/rag-server/rag_http_server.py` | S104 | Same as above. It runs on a developer host behind its firewall and is not deployed to the cloud. |
| `shared/backend`, `shared/database`, `shared/frontend` `src/app.py` | S104 | Flask runs inside its container. Docker port mapping needs a bind on all interfaces, and the container network is the boundary. |
| `ai-services/rag-server/tests/test_activity_scope.py`, `test_rag_pipeline.py` | S310 | `urlopen` uses a fixed `http://127.0.0.1:<port>` URL to the in-process test server, with no user-controlled scheme. |
| `ai-services/agentic_loop/app_main.py` | E402 ×3 | Not a security rule. The imports must follow the `sys.path` bootstrap because the CLI runs as a script from the repo root, and moving them would break the CLI and its tests. |

### pip-audit: chromadb 1.5.9 (`ai-services/rag-server/requirements.txt`)

| Installed | Latest on PyPI | Checked | Re-check by |
|---|---|---|---|
| chromadb **1.5.9** (unpinned, resolves to latest) | **1.5.9**, released 2026-05-05 | 2026-10-20 | **2026-11-20**, or as soon as a release > 1.5.9 ships |

**No fixed version exists.** The four advisories are ignored in `.pre-commit-config.yaml` with this justification:

| Advisory | CVE | Affects | Why it does not apply here |
|---|---|---|---|
| PYSEC-2026-311 | CVE-2026-45829 | Chroma **server** `POST /api/v2/.../collections` with `trust_remote_code` (pre-auth RCE) | We use embedded `chromadb.PersistentClient` in-process. No Chroma HTTP server is started or exposed. |
| PYSEC-2026-3814 | CVE-2026-45833 | Chroma **server** collection update with `trust_remote_code` | Same. There is no server API and no remote embedding functions; we use our own hash embedder. |
| PYSEC-2026-3815 | CVE-2026-45831 | `SimpleRBACAuthorizationProvider` (server auth) | Not configured. It is a server-side auth provider. |
| PYSEC-2026-3813 | CVE-2026-45830 | Cross-tenant authorization in the server | Single local tenant with no server. |

**Action (by 2026-11-20):** run `pip index versions chromadb`. If a release newer than 1.5.9 fixes these, upgrade, delete the
four `--ignore-vuln` lines and re-run `scripts/test/precommit-report.sh shared`. If not, move the re-check date forward
in both the config and this report. Do not run `chroma run` or expose
`ai-services/rag-server/chroma/` over the network.

## Outside this scope (for owners to pick up)

- `scripts/smoke_shared_integration.py`: S603 ×4 and S607 ×2 (subprocess calls with a partial path). Onboard
  `scripts/` to the scope and justify or fix them.
- Student services: `debug=True` in 9 Flask entrypoints (accommodation backend ×2, database and frontend; activity
  backend, database-service and frontend; budget-be and budget-db). The accommodation and activity **frontends** are
  internet-facing in the AWS deployment. Each owner should gate it with `FLASK_DEBUG` as `shared/database` now does.
