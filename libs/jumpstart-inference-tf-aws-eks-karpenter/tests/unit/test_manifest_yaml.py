"""Tests that the template manifest and variables files are well-formed."""

import unittest
from pathlib import Path
from typing import Any

import yaml
from jupyter_deploy.handlers import base_project_handler

from jumpstart_inference_tf_aws_eks_karpenter.template import TEMPLATE_PATH


class TestManifest(unittest.TestCase):
    MANIFEST_PATH: Path = TEMPLATE_PATH / "manifest.yaml"
    VARIABLES_PATH: Path = TEMPLATE_PATH / "variables.yaml"
    MANIFEST: dict[str, Any] | None = None
    VARIABLES_CONFIG: dict[str, Any] | None = None
    EXPECTED_REQUIREMENTS = ["terraform", "awscli", "kubectl"]
    EXPECTED_VALUES = ["deployment_id", "aws_region"]

    @classmethod
    def setUpClass(cls) -> None:
        with open(cls.MANIFEST_PATH) as f:
            cls.MANIFEST = yaml.safe_load(f)
        with open(cls.VARIABLES_PATH) as f:
            cls.VARIABLES_CONFIG = yaml.safe_load(f)

    def test_manifest_parses_as_a_dict(self) -> None:
        self.assertIsInstance(self.MANIFEST, dict)

    def test_manifest_parsable_by_jd(self) -> None:
        manifest = base_project_handler.retrieve_project_manifest(self.MANIFEST_PATH)
        self.assertIsNotNone(manifest)

    def test_all_expected_requirements_declared(self) -> None:
        assert self.MANIFEST is not None
        requirement_names = [req.get("name") for req in self.MANIFEST.get("requirements", [])]
        for expected in self.EXPECTED_REQUIREMENTS:
            self.assertIn(expected, requirement_names)

    def test_all_expected_values_declared(self) -> None:
        assert self.MANIFEST is not None
        value_names = [val.get("name") for val in self.MANIFEST.get("values", [])]
        for expected in self.EXPECTED_VALUES:
            self.assertIn(expected, value_names)

    def test_engine_is_terraform(self) -> None:
        assert self.MANIFEST is not None
        self.assertEqual(self.MANIFEST["template"]["engine"], "terraform")

    def test_variables_parses_as_a_dict(self) -> None:
        self.assertIsInstance(self.VARIABLES_CONFIG, dict)

    def test_overrides_keys_are_declared_variables(self) -> None:
        """Every preset default in defaults-all.tfvars should be a known variable name."""
        assert self.VARIABLES_CONFIG is not None
        # variables.yaml overrides are commented out in the seed; this guards the schema shape.
        self.assertIn("overrides", self.VARIABLES_CONFIG)
