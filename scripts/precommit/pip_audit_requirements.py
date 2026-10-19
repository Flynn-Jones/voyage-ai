"""pre-commit hook: run pip-audit over each requirements file it is given.

    python scripts/precommit/pip_audit_requirements.py [--ignore-vuln ID ...] FILE...

Each file is audited from its own directory so `-r ../requirements.txt`
includes resolve. Exits non-zero if any file has an un-ignored vulnerability
or cannot be resolved. Every --ignore-vuln must be justified in
.pre-commit-config.yaml and in the owner's pre-commit-security-report.md.
"""
import argparse
import subprocess
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ignore-vuln", action="append", default=[], metavar="ID")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)

    failed = []
    for name in args.files:
        path = Path(name).resolve()
        command = [sys.executable, "-m", "pip_audit", "-r", path.name, "--progress-spinner", "off"]
        for vuln in args.ignore_vuln:
            command += ["--ignore-vuln", vuln]
        print(f"==> pip-audit {name}", flush=True)
        # Arguments are a fixed list (no shell); file paths come from pre-commit.
        result = subprocess.run(command, cwd=path.parent, check=False)  # noqa: S603 - no shell, fixed executable
        if result.returncode != 0:
            failed.append(name)

    if failed:
        print(f"pip-audit failed for: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
