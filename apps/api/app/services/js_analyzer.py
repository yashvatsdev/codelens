"""JavaScript and TypeScript static analysis engine.

Inspects JavaScript and TypeScript files using pattern and line-by-line inspection
for code smells, security risks, debugging artifacts, and maintenance comments.
"""

from __future__ import annotations

import re

# Supported JavaScript/TypeScript extensions
JS_TS_EXTENSIONS: frozenset[str] = frozenset({
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
})

# Precompiled regex patterns for rules
_TODO_FIXME_PATTERN = re.compile(
    r"(?://|/\*|\*)\s*.*\b(TODO|FIXME)\b",
    re.IGNORECASE,
)
_CONSOLE_LOG_PATTERN = re.compile(
    r"\bconsole\s*\.\s*log\s*\(",
)
_EVAL_PATTERN = re.compile(
    r"(?<![.\w$])eval\s*\(",
)
_DEBUGGER_PATTERN = re.compile(
    r"(?<![.\w$])debugger\s*(?:;|\n|$)",
)


def _strip_strings_for_code_search(line: str) -> str:
    """Mask string literals so code identifiers are not matched inside strings.

    Heuristic that masks '...', "...", and `...` literals and // comments.
    """
    comment_pos = line.find("//")
    code_part = line if comment_pos == -1 else line[:comment_pos]

    # Mask simple quoted strings
    code_part = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'", "''", code_part)
    code_part = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', '""', code_part)
    code_part = re.sub(r'`[^`\\]*(?:\\.[^`\\]*)*`', '``', code_part)
    return code_part


def analyze_javascript_file(content: str, file_path: str) -> list[dict]:
    """Analyze a single JavaScript or TypeScript source file and return a list of raw finding dicts.

    Rules checked:
      - JS-TODO-FIXME: detect TODO or FIXME comments
      - JS-CONSOLE-LOG: detect console.log(...) calls
      - JS-EVAL-USAGE: detect direct eval(...) invocations
      - JS-DEBUGGER: detect debugger statements
    """
    findings: list[dict] = []
    lines = content.splitlines()

    for idx, line in enumerate(lines, start=1):
        # 1. Check TODO/FIXME comments
        todo_match = _TODO_FIXME_PATTERN.search(line)
        if todo_match:
            findings.append({
                "file_path": file_path,
                "line_number": idx,
                "severity": "info",
                "category": "maintainability",
                "message": f"TODO/FIXME comment found: {line.strip()}",
                "rule_id": "JS-TODO-FIXME",
            })

        # Mask strings so we don't trigger rules for text inside string literals
        code_only = _strip_strings_for_code_search(line)

        # 2. Check console.log(...)
        if _CONSOLE_LOG_PATTERN.search(code_only):
            findings.append({
                "file_path": file_path,
                "line_number": idx,
                "severity": "warning",
                "category": "style",
                "message": "Unexpected 'console.log' statement",
                "rule_id": "JS-CONSOLE-LOG",
            })

        # 3. Check eval(...)
        if _EVAL_PATTERN.search(code_only):
            findings.append({
                "file_path": file_path,
                "line_number": idx,
                "severity": "error",
                "category": "security",
                "message": "Dangerous 'eval()' usage detected",
                "rule_id": "JS-EVAL-USAGE",
            })

        # 4. Check debugger statement
        if _DEBUGGER_PATTERN.search(code_only):
            findings.append({
                "file_path": file_path,
                "line_number": idx,
                "severity": "warning",
                "category": "bug",
                "message": "Unexpected 'debugger' statement",
                "rule_id": "JS-DEBUGGER",
            })

    findings.sort(key=lambda f: (f.get("line_number") or 0, f.get("rule_id") or ""))
    return findings
