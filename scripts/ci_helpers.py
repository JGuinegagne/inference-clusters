"""Shared helpers for this repo's CI scripts.

A small, self-contained subset for driving the `jd` CLI from CI — the CLI is the
published `jupyter-deploy` package installed in this workspace (no external checkout).
"""

from __future__ import annotations

import subprocess
import sys

# Backstop timeout for any single `jd` invocation, so a stuck CLI fails with a
# diagnostic rather than silently consuming the whole job budget.
JD_TIMEOUT_SECONDS = 600


def run_jd(
    jd_args: list[str],
    cwd: str | None = None,
    capture: bool = False,
    timeout: int = JD_TIMEOUT_SECONDS,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a `jd` CLI command with CI-safe defaults.

    Closes stdin so a no-TTY interactive prompt fails fast instead of hanging,
    enforces a timeout backstop, and surfaces stderr on failure or timeout.

    With check=False a non-zero exit is returned to the caller instead of
    terminating the process, so optional steps can fail without aborting.
    """
    cmd = ["uv", "run", "jd", *jd_args]
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=capture,
            text=True,
            check=check,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        print(f"Error: `jd {' '.join(jd_args)}` timed out after {timeout}s", file=sys.stderr)
        if e.stderr:
            print(e.stderr if isinstance(e.stderr, str) else e.stderr.decode(), file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"Error: `jd {' '.join(jd_args)}` failed with exit code {e.returncode}", file=sys.stderr)
        if e.stderr:
            print(e.stderr, file=sys.stderr)
        sys.exit(1)


def is_project_deployed(project_dir: str) -> bool:
    """Return True if a project has live infrastructure (non-empty terraform state).

    Runs `jd show --outputs --list`; a project with empty state (e.g. destroyed but
    still in the store) returns False.
    """
    result = subprocess.run(
        ["uv", "run", "jd", "show", "--outputs", "--list", "--text", "-p", project_dir],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=JD_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def run_jd_config(config_args: list[str], project_dir: str, check: bool = True) -> bool:
    """Run `jd config` with the given arguments in a project directory.

    Returns True on success. With check=False a non-zero exit returns False instead
    of aborting, so optional config steps can be skipped on failure.
    """
    result = run_jd(["config", *config_args], cwd=project_dir, check=check)
    return result.returncode == 0
