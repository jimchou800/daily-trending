"""Exercise the CI coverage command with independent report totals."""

import contextlib
import io
import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_coverage.py"


class CoverageGateTests(unittest.TestCase):
    def test_line_and_branch_must_independently_meet_eighty_percent(self):
        for lines, branches, expected in ((80, 80, 0), (79, 100, 1), (100, 79, 1)):
            with self.subTest(lines=lines, branches=branches), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "coverage.json").write_text(
                    json.dumps(
                        {
                            "totals": {
                                "covered_lines": lines,
                                "num_statements": 100,
                                "covered_branches": branches,
                                "num_branches": 100,
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                previous = Path.cwd()
                try:
                    os.chdir(root)
                    with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as result:
                        runpy.run_path(str(SCRIPT), run_name="__main__")
                    self.assertEqual(result.exception.code, expected)
                finally:
                    os.chdir(previous)
