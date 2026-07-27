"""Tests for scripts/ci_restore.py — CI-project discovery logic."""

import subprocess
import unittest
from unittest.mock import Mock, patch

import ci_restore


def _jd_list(*project_ids: str) -> subprocess.CompletedProcess[str]:
    """A fake `jd projects list --text` result listing the given project ids."""
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="\n".join(project_ids) + "\n")


class TestDiscoverProjectId(unittest.TestCase):
    @patch("ci_restore.run_jd")
    def test_returns_the_sole_ci_project(self, mock_run_jd: Mock) -> None:
        mock_run_jd.return_value = _jd_list("tf-aws-iam-ci-abc123")
        self.assertEqual(ci_restore.discover_project_id(), "tf-aws-iam-ci-abc123")

    @patch("ci_restore.run_jd")
    def test_ignores_non_ci_projects(self, mock_run_jd: Mock) -> None:
        # Only the tf-aws-iam-ci-* entry is a CI project; the karpenter one is not.
        mock_run_jd.return_value = _jd_list("tf-aws-eks-karpenter-xyz", "tf-aws-iam-ci-abc123")
        self.assertEqual(ci_restore.discover_project_id(), "tf-aws-iam-ci-abc123")

    @patch("ci_restore.run_jd")
    def test_no_ci_project_exits(self, mock_run_jd: Mock) -> None:
        mock_run_jd.return_value = _jd_list("tf-aws-eks-karpenter-xyz")
        with self.assertRaises(SystemExit):
            ci_restore.discover_project_id()

    @patch("ci_restore.run_jd")
    def test_multiple_ci_projects_exits(self, mock_run_jd: Mock) -> None:
        mock_run_jd.return_value = _jd_list("tf-aws-iam-ci-abc123", "tf-aws-iam-ci-def456")
        with self.assertRaises(SystemExit):
            ci_restore.discover_project_id()
