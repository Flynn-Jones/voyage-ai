# Pre-commit security scans (Release 2)

Every commit runs three hooks over the folders in scope:

| Hook | Checks | Config |
|---|---|---|
| **ruff** | Python lint plus the **S** (flake8-bandit) security rules, e.g. hardcoded passwords, `shell=True`, `eval`, unsafe YAML/pickle, binds to all interfaces, `requests` without a timeout | `ruff.toml` |
| **detect-secrets** | API keys, tokens, passwords and high-entropy strings in any staged file | `.secrets.baseline` |
| **pip-audit** | Known CVEs in every `requirements*.txt` you change (resolves the full dependency tree) | local hook, `scripts/precommit/pip_audit_requirements.py` |

## Set up (once per clone)

```bash
pip install pre-commit
pre-commit install            # from now on, git commit runs the hooks
pre-commit run --all-files    # scan everything in scope now
```

The first run downloads the hook environments, which takes a few minutes. pip-audit needs network access.

## Add your feature to the scope (do this in your feature PR)

Hooks only see paths matching the top-level `files:` regex in `.pre-commit-config.yaml`. It is a verbose
regex (`(?x)`, whitespace ignored) with **one folder per line**:

```yaml
files: |
  (?x)^(
    ai-services/|
    shared/
  )
```

1. Add **one line** for your folder, ending in `/|`, anywhere above `shared/` (the last entry has no `|`).
   For example, after Budget's PR:
   ```yaml
   files: |
     (?x)^(
       ai-services/|
       budget-service/|
       shared/
     )
   ```
   Folders: `student-1/`, `accommodation-service/`, `activity-service/`, `budget-service/`, `itinerary-service/`.
   Check it with `pre-commit run --all-files`: your files should now appear in the hook output.
2. Run `pre-commit run --all-files` and fix what it reports.
   - **Fix** where you can, e.g. add `timeout=` to `requests` calls, remove `debug=True`, drop `shell=True`.
   - **Accept** only with a reason, on the same line: `# noqa: S104 - binds 0.0.0.0 inside its container`.
     Never use a bare `# noqa`.
   - **pip-audit:** upgrade or pin the package. If no fixed release exists and the vulnerable code path is
     not used, add `--ignore-vuln=<ID>` to the hook's `args`, with a comment saying why.
   - **detect-secrets:** remove the secret and rotate it. If it is a false positive (test fixture, example
     value), run `detect-secrets scan --baseline .secrets.baseline`, then `detect-secrets audit .secrets.baseline`
     and mark it as not a secret. Never add a real secret to the baseline.
3. Produce your report:
   ```bash
   scripts/test/precommit-report.sh student-N   # saves docs/release-2/student-N/pre-commit-<UTC>.txt
   ```
4. Write `docs/release-2/student-N/pre-commit-security-report.md` using the template below. The agentic loop's
   `testing-review` mode reads it.

Because each folder is on its own line, students who onboard at the same time add different lines and normally
don't conflict. If git still reports a conflict there, which can happen when both add a line in the same place,
keep both lines.

## Report template (`docs/release-2/student-N/pre-commit-security-report.md`)

```markdown
# Pre-commit security report: <Feature> (student-N)

- Scan: `scripts/test/precommit-report.sh student-N` → `pre-commit-<UTC>.txt`
- Commit: <sha>   Result: PASS | FAIL

| Hook | Result | Findings | Fixed | Accepted |
|---|---|---|---|---|
| ruff (S) | Passed | n | n | n |
| detect-secrets | Passed | n | n | n |
| pip-audit | Passed | n | n | n |

## Fixed
- <file:line> <rule>: <what changed>

## Accepted (with justification)
- <file:line> <rule>: <why it is safe; where the inline justification is>
```

See `docs/release-2/shared/pre-commit-security-report.md` for a completed example.

## Skipping a hook

`SKIP=pip-audit git commit ...` skips one hook, for example when you're offline. CI and the report still run every
hook, so skipping only delays the finding.
