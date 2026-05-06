"""
AST-based audit: AMBIGUOUS_SIG (bare, without _RFID/_NONRFID suffix) and NO_CANDIDATE
must not appear as string literals anywhere under apps/filament_iq/ except on the lines
that define their named constants.

This catches regressions where someone hand-types the old bare string instead of using
the promoted constant.  FORCE_ACCEPTED is intentionally excluded from this check because
it is a cross-repo coupled value consumed by exact-equality Jinja matches in three files
across two repos — bare-string uses in Python comparison sites remain acceptable.
"""

import ast
import os
import pathlib

# Strings that must not appear as bare literals anywhere in the package
# (outside their own constant-definition lines).
FORBIDDEN = {"AMBIGUOUS_SIG", "NO_CANDIDATE"}

# The one file where these constants are defined; the Assign lines that
# define the constants are the only legal occurrences.
CONSTANTS_FILE = "ams_rfid_reconcile.py"


def _collect_string_literals(tree: ast.AST, source_lines: list[str]) -> list[tuple[int, str]]:
    """Return (lineno, value) for every ast.Constant with a str value in *tree*."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            hits.append((node.lineno, node.value))
    return hits


def _is_constant_definition_line(lineno: int, value: str, source_lines: list[str]) -> bool:
    """Return True if *lineno* (1-based) in *source_lines* is the Assign that defines
    the constant whose string value is *value* (e.g. ``AMBIGUOUS_SIG_RFID = "AMBIGUOUS_SIG_RFID"``).

    We accept a line whose value appears as both the LHS identifier and the RHS string literal
    — that is the canoncial ``CONSTANT = "CONSTANT"`` pattern.
    """
    if lineno < 1 or lineno > len(source_lines):
        return False
    line = source_lines[lineno - 1].strip()
    # The constant definition pattern: IDENTIFIER = "VALUE" (or 'VALUE')
    # We just check that the identifier part of the LHS equals the value.
    lhs = line.split("=")[0].strip()
    return lhs == value


def _find_package_py_files(package_dir: pathlib.Path) -> list[pathlib.Path]:
    return sorted(package_dir.rglob("*.py"))


def test_no_bare_ambiguous_sig_or_no_candidate_strings():
    """Audit: AMBIGUOUS_SIG (without _RFID/_NONRFID) and NO_CANDIDATE must not appear
    as string literals in apps/filament_iq/ except on their own constant-definition lines."""
    repo_root = pathlib.Path(__file__).parent.parent
    package_dir = repo_root / "apps" / "filament_iq"
    assert package_dir.is_dir(), f"Package dir not found: {package_dir}"

    violations: list[str] = []

    for py_file in _find_package_py_files(package_dir):
        source = py_file.read_text(encoding="utf-8")
        source_lines = source.splitlines()
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError as exc:
            raise AssertionError(f"SyntaxError parsing {py_file}: {exc}") from exc

        for lineno, value in _collect_string_literals(tree, source_lines):
            if value not in FORBIDDEN:
                continue

            # Check for bare "AMBIGUOUS_SIG" (not the suffixed variants)
            if value == "AMBIGUOUS_SIG":
                # This exact string is forbidden everywhere — the suffixed constants
                # AMBIGUOUS_SIG_RFID / AMBIGUOUS_SIG_NONRFID have different values,
                # so they will never match here.
                violations.append(
                    f"{py_file.relative_to(repo_root)}:{lineno}: "
                    f'bare string literal "{value}" — use AMBIGUOUS_SIG_RFID or AMBIGUOUS_SIG_NONRFID'
                )
                continue

            # For NO_CANDIDATE: allow only the constant-definition line in CONSTANTS_FILE
            if value == "NO_CANDIDATE":
                if py_file.name == CONSTANTS_FILE and _is_constant_definition_line(
                    lineno, value, source_lines
                ):
                    continue  # This is the definition line — allowed
                violations.append(
                    f"{py_file.relative_to(repo_root)}:{lineno}: "
                    f'bare string literal "{value}" — use the NO_CANDIDATE constant'
                )

    assert not violations, (
        "Bare forbidden string literals found in apps/filament_iq/:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
