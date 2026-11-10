"""Fail CI on unfinished developer work or incomplete test execution."""

from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
import unittest
from pathlib import Path

_MARKERS = re.compile(r"\b(?:TODO|FIXME)\b", re.IGNORECASE)
_SUPPRESSIONS = frozenset({"skip", "skipIf", "skipUnless", "expectedFailure", "skipif", "xfail", "only"})
_SOURCE_DIRECTORIES = ("daily_trending", "tests", "scripts", ".github")
_CONFIG_SUFFIXES = frozenset({".yml", ".yaml", ".toml", ".ini", ".cfg"})


def _python_issues(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    issues = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and _MARKERS.search(token.string):
            issues.append((token.start[0], "unfinished work in comment"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            docstring = ast.get_docstring(node, clean=False)
            if docstring and _MARKERS.search(docstring):
                issues.append((node.body[0].lineno, "unfinished work in docstring"))
        if isinstance(node, ast.Attribute) and node.attr in _SUPPRESSIONS:
            issues.append((node.lineno, "test suppression or focus marker"))
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in _SUPPRESSIONS and node.module in ("unittest", "pytest"):
                    issues.append((node.lineno, "test suppression import"))
    return issues


def _config_issues(source: str) -> list[tuple[int, str]]:
    issues = []
    for number, line in enumerate(source.splitlines(), 1):
        quote = None
        escaped = False
        for offset, char in enumerate(line):
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote:
                quote = None
            elif char in ("'", '"') and quote is None:
                quote = char
            elif char == "#" and quote is None:
                if _MARKERS.search(line[offset:]):
                    issues.append((number, "unfinished work in config comment"))
                break
    return issues


def check_tree(root: Path) -> list[str]:
    """Inspect maintained Python/config files; return located violations without exemptions.

    Documentation and fixture data are not source comments. Malformed Python is a
    violation rather than a reason to silently omit a file from checking.
    """
    paths = [path for name in _SOURCE_DIRECTORIES for path in (root / name).rglob("*") if path.is_file()]
    paths.extend(path for path in root.iterdir() if path.is_file())
    violations = []
    for path in sorted(paths):
        if path.suffix != ".py" and path.suffix not in _CONFIG_SUFFIXES:
            continue
        try:
            source = path.read_text(encoding="utf-8-sig")
            issues = _python_issues(source) if path.suffix == ".py" else _config_issues(source)
        except (SyntaxError, tokenize.TokenError, UnicodeError, OSError) as error:
            violations.append(f"{path.relative_to(root)}: cannot parse: {error}")
            continue
        violations.extend(f"{path.relative_to(root)}:{line}: {message}" for line, message in issues)
    return violations


def main(argv: list[str] | None = None) -> int:
    """Check a repository, optionally run required unittest discovery, and fail closed.

    Runtime checks also reject dynamically skipped or expected-failure tests that
    cannot be identified statically. No tests is always a failure.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args(argv)
    violations = check_tree(args.root)
    if violations:
        print("\n".join(violations))
        return 1
    if not args.run_tests:
        return 0
    suite = unittest.TestLoader().discover(str(args.root / "tests"))
    if suite.countTestCases() == 0:
        print("No tests discovered")
        return 1
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped or result.expectedFailures or result.unexpectedSuccesses:
        print("Required suite contains suppressed tests")
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
