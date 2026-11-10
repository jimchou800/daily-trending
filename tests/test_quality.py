"""Regression tests for fail-closed CI integrity checks."""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from daily_trending.quality import main


class QualityTests(unittest.TestCase):
    """Exercise the command against real temporary source trees and test suites."""

    def _run(self, source, *, filename="tests/test_sample.py", run_tests=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
            command = ["--root", directory]
            if run_tests:
                command.append("--run-tests")
            stdout, stderr = io.StringIO(), io.StringIO()
            previous_path = sys.path[:]
            try:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = main(command)
            finally:
                sys.path[:] = previous_path
                sys.modules.pop("test_sample", None)
            return SimpleNamespace(returncode=status, stdout=stdout.getvalue(), stderr=stderr.getvalue())

    def test_developer_comments_and_docstrings_fail(self):
        for source in ("# TODO: finish\n", '"""FIXME: finish."""\n', 'def f():\n    """TODO: finish."""\n'):
            with self.subTest(source=source):
                result = self._run(source)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unfinished work", result.stdout)

    def test_fixture_strings_and_identifiers_are_not_markers(self):
        result = self._run('sample = "TODO: example"\nFIXME_value = 1\n# methodology is complete\n')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_config_comments_are_checked_but_quoted_data_is_not(self):
        result = self._run('name: "TODO" # FIXME: finish\n', filename=".github/workflows/quality.yml")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unfinished work", result.stdout)
        result = self._run('name: "TODO # FIXME"\n', filename=".github/workflows/quality.yml")
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_disabled_and_focused_tests_fail(self):
        for marker in (
            'unittest.skip("reason")',
            'unittest.skipIf(False, "reason")',
            'unittest.skipUnless(True, "reason")',
            "unittest.expectedFailure",
            'pytest.mark.skip(reason="reason")',
            'pytest.mark.skipif(False, reason="reason")',
            "pytest.mark.xfail",
            "pytest.mark.only",
        ):
            with self.subTest(marker=marker):
                result = self._run(f"@{marker}\ndef test_sample():\n    pass\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("test suppression", result.stdout)

    def test_syntax_errors_fail_closed(self):
        result = self._run("def broken(\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot parse", result.stdout)

    def test_empty_discovery_fails(self):
        result = self._run("value = 1\n", run_tests=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No tests discovered", result.stdout)

    def test_passing_and_failing_tests_control_exit_status(self):
        for expression, expected in (("True", 0), ("False", 1)):
            result = self._run(
                "import unittest\nclass Sample(unittest.TestCase):\n"
                f"    def test_result(self):\n        self.assertTrue({expression})\n",
                run_tests=True,
            )
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def test_dynamic_skips_cannot_pass_runtime_check(self):
        result = self._run(
            "import unittest\nclass Sample(unittest.TestCase):\n"
            '    def test_result(self):\n        self.skipTest("reason")\n',
            run_tests=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("suppressed tests", result.stdout)

    def test_imported_suppression_alias_is_rejected(self):
        result = self._run('from unittest import skip as bypass\n@bypass("reason")\ndef test_sample():\n    pass\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test suppression import", result.stdout)

    def test_dynamic_expected_failures_and_unexpected_successes_fail(self):
        for expression in ("True", "False"):
            result = self._run(
                "import unittest\nclass Sample(unittest.TestCase):\n"
                '    @getattr(unittest, "expected" + "Failure")\n'
                f"    def test_result(self):\n        self.assertTrue({expression})\n",
                run_tests=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("suppressed tests", result.stdout)

    def test_root_config_comments_are_checked(self):
        result = self._run("[tool.example]\n# TODO: finish\n", filename="pyproject.toml")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pyproject.toml:2", result.stdout)

    def test_unrelated_text_and_quoted_config_comments_are_preserved(self):
        result = self._run('message = "escaped \\" # TODO" # complete\n', filename="sample.toml")
        self.assertEqual(result.returncode, 0, result.stdout)
