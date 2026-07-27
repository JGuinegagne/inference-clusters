"""Tests that README.md and the template's terraform variables/outputs stay in sync.

The README's Inputs/Outputs tables are the deployer-facing contract, in both directions:
- every `variable`/`output` declared in the template MUST be referenced there (no gaps), and
- every name listed in those tables MUST be a real declared name (no stale/typo'd entries).

A name counts as referenced if it appears verbatim in a backticked token (`name`) OR is
covered by a backticked glob (`prefix_*`, `*_suffix`) — the README groups families of outputs
(e.g. `*_ecr_repository`, `*_namespace`) under one wildcard rather than listing each. A glob in
the tables must match at least one declared name.
"""

import re
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parent.parent.parent
ENGINE = PACKAGE_ROOT / "inference_tf_aws_eks_karpenter" / "template" / "engine"
README = PACKAGE_ROOT / "README.md"


def _tf_names(tf_file: Path, block: str) -> list[str]:
    """Names declared by `block "<name>" {` (block is 'variable' or 'output')."""
    return re.findall(rf'^{block}\s+"([^"]+)"', tf_file.read_text(), re.MULTILINE)


def _section(heading: str) -> str:
    """The README text from `## <heading>` up to the next `## ` heading."""
    text = README.read_text()
    match = re.search(rf"^## {re.escape(heading)}\b.*?(?=^## )", text, re.MULTILINE | re.DOTALL)
    assert match, f"README.md has no '## {heading}' section"
    return match.group(0)


def _backticked_tokens(text: str) -> list[str]:
    """Every `...`-quoted identifier/glob token in `text`."""
    return re.findall(r"`([A-Za-z0-9_*]+)`", text)


def _glob_matches(token: str, name: str) -> bool:
    return "*" in token and re.fullmatch(re.escape(token).replace(r"\*", ".*"), name) is not None


def _is_referenced(name: str, tokens: list[str]) -> bool:
    return any(tok == name or _glob_matches(tok, name) for tok in tokens)


class _ReadmeSyncMixin:
    """Shared bidirectional check for a `## <section>` table against declared tf names."""

    section: str
    block: str

    def _check(self, testcase: unittest.TestCase) -> None:
        declared = _tf_names(ENGINE / f"{self.block}s.tf", self.block)
        testcase.assertTrue(declared, f"no {self.block}s found in {self.block}s.tf")
        tokens = _backticked_tokens(_section(self.section))

        # Forward: every declared name is referenced (literally or via a glob).
        missing = [n for n in declared if not _is_referenced(n, tokens)]
        testcase.assertEqual(missing, [], f"{self.block}s missing from README '{self.section}': {missing}")

        # Reverse: every token in the table is a real name, or a glob matching one.
        stale = [
            tok
            for tok in tokens
            if tok not in declared and not (("*" in tok) and any(_glob_matches(tok, n) for n in declared))
        ]
        testcase.assertEqual(stale, [], f"README '{self.section}' lists undeclared {self.block}s: {stale}")


class TestReadmeVariablesInSync(unittest.TestCase, _ReadmeSyncMixin):
    section = "Inputs"
    block = "variable"

    def test_variables_and_readme_agree(self) -> None:
        self._check(self)


class TestReadmeOutputsInSync(unittest.TestCase, _ReadmeSyncMixin):
    section = "Outputs"
    block = "output"

    def test_outputs_and_readme_agree(self) -> None:
        self._check(self)
