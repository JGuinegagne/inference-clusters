"""Tests for scripts/ci_helpers.py — the jd-CLI subprocess wrappers."""

import subprocess
import unittest
from unittest.mock import Mock, patch

import ci_helpers


class TestRunJd(unittest.TestCase):
    @patch("ci_helpers.subprocess.run")
    def test_success_returns_completed_process(self, mock_run: Mock) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok")
        mock_run.return_value = completed
        result = ci_helpers.run_jd(["show", "-o", "x"], capture=True)
        self.assertIs(result, completed)
        # prepends `uv run jd` and closes stdin so a no-TTY prompt fails fast.
        args, kwargs = mock_run.call_args
        self.assertEqual(args[0][:3], ["uv", "run", "jd"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    @patch("ci_helpers.subprocess.run")
    def test_timeout_exits(self, mock_run: Mock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="jd", timeout=1)
        with self.assertRaises(SystemExit):
            ci_helpers.run_jd(["up"])

    @patch("ci_helpers.subprocess.run")
    def test_called_process_error_exits(self, mock_run: Mock) -> None:
        mock_run.side_effect = subprocess.CalledProcessError(returncode=2, cmd="jd", stderr="boom")
        with self.assertRaises(SystemExit):
            ci_helpers.run_jd(["config"])

    @patch("ci_helpers.subprocess.run")
    def test_check_false_is_passed_through(self, mock_run: Mock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1)
        ci_helpers.run_jd(["config"], check=False)
        self.assertFalse(mock_run.call_args.kwargs["check"])


class TestIsProjectDeployed(unittest.TestCase):
    @patch("ci_helpers.subprocess.run")
    def test_nonzero_returncode_is_not_deployed(self, mock_run: Mock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="")
        self.assertFalse(ci_helpers.is_project_deployed("proj"))

    @patch("ci_helpers.subprocess.run")
    def test_empty_outputs_is_not_deployed(self, mock_run: Mock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="  \n")
        self.assertFalse(ci_helpers.is_project_deployed("proj"))

    @patch("ci_helpers.subprocess.run")
    def test_nonempty_outputs_is_deployed(self, mock_run: Mock) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="deployment_id\nregion\n")
        self.assertTrue(ci_helpers.is_project_deployed("proj"))


class TestRunJdConfig(unittest.TestCase):
    @patch("ci_helpers.run_jd")
    def test_returns_true_on_success(self, mock_run_jd: Mock) -> None:
        mock_run_jd.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        self.assertTrue(ci_helpers.run_jd_config(["--restore-secrets"], "proj"))

    @patch("ci_helpers.run_jd")
    def test_returns_false_on_failure_when_unchecked(self, mock_run_jd: Mock) -> None:
        mock_run_jd.return_value = subprocess.CompletedProcess(args=[], returncode=1)
        self.assertFalse(ci_helpers.run_jd_config(["--restore-secrets"], "proj", check=False))
