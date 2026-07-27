"""Tests for scripts/upgrade_template_version.py."""

import tomllib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import upgrade_template_version as u
from upgrade_template_version import Bump, TemplateSpec


class TestComputeBumpedVersion(unittest.TestCase):
    def test_patch_increments_patch(self) -> None:
        self.assertEqual(u.compute_bumped_version("0.1.0", Bump.PATCH), "0.1.1")

    def test_minor_resets_patch(self) -> None:
        self.assertEqual(u.compute_bumped_version("0.1.4", Bump.MINOR), "0.2.0")

    def test_major_resets_minor_and_patch(self) -> None:
        self.assertEqual(u.compute_bumped_version("1.2.3", Bump.MAJOR), "2.0.0")

    def test_prerelease_suffix_is_dropped_before_bump(self) -> None:
        self.assertEqual(u.compute_bumped_version("0.1.0rc1", Bump.PATCH), "0.1.1")

    def test_unparseable_version_exits(self) -> None:
        with self.assertRaises(SystemExit):
            u.compute_bumped_version("not-a-version", Bump.PATCH)


class TestResolveNewVersion(unittest.TestCase):
    def test_bump_keyword_is_computed_from_current(self) -> None:
        self.assertEqual(u.resolve_new_version("0.1.0", "minor"), "0.2.0")

    def test_explicit_version_passes_through(self) -> None:
        self.assertEqual(u.resolve_new_version("0.1.0", "0.1.0rc2"), "0.1.0rc2")

    def test_unknown_bump_word_is_treated_as_explicit_version(self) -> None:
        # An unrecognized keyword is not silently treated as patch; it is passed
        # through verbatim as an explicit version string.
        self.assertEqual(u.resolve_new_version("0.1.0", "pathc"), "pathc")


class TestBumpEnum(unittest.TestCase):
    def test_only_the_three_keywords_are_valid(self) -> None:
        self.assertEqual({b.value for b in Bump}, {"patch", "minor", "major"})

    def test_unrecognized_raises(self) -> None:
        with self.assertRaises(ValueError):
            Bump("nope")


class TestPep440ToSemver(unittest.TestCase):
    def test_rc_becomes_undotted_semver(self) -> None:
        self.assertEqual(u.pep440_to_semver("0.1.0rc1"), "0.1.0-rc1")

    def test_alpha_and_beta(self) -> None:
        self.assertEqual(u.pep440_to_semver("1.2.3a4"), "1.2.3-a4")
        self.assertEqual(u.pep440_to_semver("1.2.3b5"), "1.2.3-b5")

    def test_final_release_is_unchanged(self) -> None:
        self.assertEqual(u.pep440_to_semver("0.1.0"), "0.1.0")


class TestUpgrade(unittest.TestCase):
    """upgrade() rewrites all pinned files for a template (PEP 440 in the four core
    files, undotted SemVer in the synced charts, untouched charts left alone)."""

    def _scaffold(self, root: Path) -> TemplateSpec:
        spec = TemplateSpec(package="demo-tpl", synced_charts=("synced",))
        pkg = root / "libs" / "demo-tpl"
        module = pkg / "demo_tpl"
        template = module / "template"
        (template / "engine").mkdir(parents=True)
        (template / "charts" / "synced").mkdir(parents=True)
        (template / "charts" / "independent").mkdir(parents=True)

        (pkg / "pyproject.toml").write_text('[project]\nname = "demo-tpl"\nversion = "0.1.0rc1"\n')
        (module / "__init__.py").write_text('__version__ = "0.1.0rc1"\n')
        (template / "manifest.yaml").write_text("template:\n  version: 0.1.0rc1\n")
        (template / "engine" / "main.tf").write_text('locals {\n  template_version = "0.1.0rc1"\n}\n')
        (template / "charts" / "synced" / "Chart.yaml").write_text("name: synced\nversion: 0.1.0-rc1\n")
        (template / "charts" / "independent" / "Chart.yaml").write_text("name: independent\nversion: 9.9.9\n")
        return spec

    def test_upgrade_rewrites_core_and_synced_charts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = self._scaffold(root)
            # Point the module's path anchors at the temp tree.
            with patch.object(u, "LIBS", root / "libs"), patch.object(u, "REPO_ROOT", root):
                u.upgrade(spec, "0.2.0rc3")

            pkg = root / "libs" / "demo-tpl"
            template = pkg / "demo_tpl" / "template"

            # Four core files carry the PEP 440 string verbatim.
            with open(pkg / "pyproject.toml", "rb") as f:
                self.assertEqual(tomllib.load(f)["project"]["version"], "0.2.0rc3")
            self.assertIn('__version__ = "0.2.0rc3"', (pkg / "demo_tpl" / "__init__.py").read_text())
            self.assertIn("version: 0.2.0rc3", (template / "manifest.yaml").read_text())
            self.assertIn('template_version = "0.2.0rc3"', (template / "engine" / "main.tf").read_text())

            # Synced chart uses the undotted SemVer spelling.
            self.assertIn("version: 0.2.0-rc3", (template / "charts" / "synced" / "Chart.yaml").read_text())

            # A chart NOT in synced_charts is left untouched.
            self.assertIn("version: 9.9.9", (template / "charts" / "independent" / "Chart.yaml").read_text())
