"""Tests for scripts/ci_restore_karpenter.py — karpenter project resolution.

This resolution is load-bearing for parallel e2e runs: a wrong project id means a run
restores/tears down the wrong cluster.
"""

import subprocess
import unittest
from unittest.mock import Mock, patch

import ci_restore_karpenter as crk


def _jd_list(*project_ids: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="\n".join(project_ids) + "\n")


class TestListKarpenterProjects(unittest.TestCase):
    @patch("ci_restore_karpenter.run_jd")
    def test_filters_by_prefix(self, mock_run_jd: Mock) -> None:
        mock_run_jd.return_value = _jd_list(
            "tf-aws-eks-karpenter-aaa",
            "tf-aws-iam-ci-bbb",
            "tf-aws-eks-karpenter-ccc",
        )
        self.assertEqual(
            crk.list_karpenter_projects(),
            ["tf-aws-eks-karpenter-aaa", "tf-aws-eks-karpenter-ccc"],
        )


class TestResolveProjectId(unittest.TestCase):
    def test_explicit_deployment_id_builds_project_id_without_listing(self) -> None:
        # An explicit id short-circuits — no store lookup needed.
        with patch("ci_restore_karpenter.run_jd") as mock_run_jd:
            self.assertEqual(crk.resolve_project_id("abc123"), "tf-aws-eks-karpenter-abc123")
            mock_run_jd.assert_not_called()

    @patch("ci_restore_karpenter.run_jd")
    def test_no_id_returns_sole_project(self, mock_run_jd: Mock) -> None:
        mock_run_jd.return_value = _jd_list("tf-aws-eks-karpenter-only")
        self.assertEqual(crk.resolve_project_id(None), "tf-aws-eks-karpenter-only")

    @patch("ci_restore_karpenter.run_jd")
    def test_no_id_no_project_exits(self, mock_run_jd: Mock) -> None:
        mock_run_jd.return_value = _jd_list()
        with self.assertRaises(SystemExit):
            crk.resolve_project_id(None)

    @patch("ci_restore_karpenter.run_jd")
    def test_no_id_no_project_allow_missing_returns_none(self, mock_run_jd: Mock) -> None:
        mock_run_jd.return_value = _jd_list()
        self.assertIsNone(crk.resolve_project_id(None, allow_missing=True))

    @patch("ci_restore_karpenter.run_jd")
    def test_no_id_multiple_projects_exits(self, mock_run_jd: Mock) -> None:
        # Ambiguous without an explicit id — must not silently pick one.
        mock_run_jd.return_value = _jd_list("tf-aws-eks-karpenter-aaa", "tf-aws-eks-karpenter-bbb")
        with self.assertRaises(SystemExit):
            crk.resolve_project_id(None)
