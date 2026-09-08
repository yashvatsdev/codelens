"""Python static analysis engine using ast and line-by-line inspection."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.finding import Finding
from app.models.source_file import SourceFile


class PythonStaticAnalyzer(ast.NodeVisitor):
    def __init__(self, file_path: str, max_function_lines: int = 50):
        self.file_path = file_path
        self.max_function_lines = max_function_lines
        self.findings: list[dict] = []
        # Track imported names: name_in_scope -> (lineno, original_name)
        self.imports: dict[str, tuple[int, str]] = {}
        self.used_names: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name_in_scope = alias.asname or alias.name.split(".")[0]
            self.imports[name_in_scope] = (node.lineno, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                continue
            name_in_scope = alias.asname or alias.name
            self.imports[name_in_scope] = (node.lineno, alias.name)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.used_names.add(node.id)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        # Check for __all__ = ["name1", "name2"]
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            self.used_names.add(elt.value)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.findings.append({
                "file_path": self.file_path,
                "line_number": node.lineno,
                "severity": "warning",
                "category": "bug",
                "message": "Bare 'except:' clause used",
                "rule_id": "PY-BARE-EXCEPT",
            })
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function_length(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function_length(node)
        self.generic_visit(node)

    def _check_function_length(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        end_lineno = getattr(node, "end_lineno", None)
        if end_lineno is not None:
            length = end_lineno - node.lineno + 1
            if length > self.max_function_lines:
                self.findings.append({
                    "file_path": self.file_path,
                    "line_number": node.lineno,
                    "severity": "warning",
                    "category": "complexity",
                    "message": f"Function '{node.name}' is too long ({length} lines, max threshold is {self.max_function_lines})",
                    "rule_id": "PY-LONG-FUNCTION",
                })


def check_todo_fixme(content: str, file_path: str) -> list[dict]:
    findings = []
    lines = content.splitlines()
    pattern = re.compile(r"#.*\b(TODO|FIXME)\b", re.IGNORECASE)
    for idx, line in enumerate(lines, start=1):
        match = pattern.search(line)
        if match:
            comment_text = line[match.start():].strip()
            findings.append({
                "file_path": file_path,
                "line_number": idx,
                "severity": "info",
                "category": "maintainability",
                "message": f"TODO/FIXME comment found: {comment_text}",
                "rule_id": "PY-TODO-FIXME",
            })
    return findings


def analyze_python_file(
    content: str, file_path: str, max_function_lines: int = 50
) -> list[dict]:
    """Analyze a single Python source file's string content and return a list of raw finding dicts."""
    findings = []

    # 1. Check TODO/FIXME comments line by line
    findings.extend(check_todo_fixme(content, file_path))

    # 2. Try parsing AST
    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError as err:
        findings.append({
            "file_path": file_path,
            "line_number": err.lineno or 1,
            "severity": "error",
            "category": "syntax",
            "message": f"Syntax error: {err.msg}",
            "rule_id": "PY-SYNTAX-001",
        })
        return findings

    # 3. Walk AST for structural findings
    visitor = PythonStaticAnalyzer(file_path=file_path, max_function_lines=max_function_lines)
    visitor.visit(tree)

    # 4. Check for unused imports
    for imported_name, (lineno, orig_name) in visitor.imports.items():
        if imported_name not in visitor.used_names:
            visitor.findings.append({
                "file_path": file_path,
                "line_number": lineno,
                "severity": "warning",
                "category": "style",
                "message": f"Unused import '{orig_name}'",
                "rule_id": "PY-UNUSED-IMPORT",
            })

    findings.extend(visitor.findings)
    findings.sort(key=lambda f: (f.get("line_number") or 0, f.get("rule_id") or ""))
    return findings


@dataclass
class AnalysisResult:
    """Summary returned after analyzing a repository's Python source files."""
    repository_id: int
    files_analyzed: int
    total_findings: int
    findings: list[Finding] = field(default_factory=list)


def analyze_repository(
    repository_id: int,
    db: Session,
    max_function_lines: int = 50,
) -> AnalysisResult:
    """Analyze all stored Python source files for a repository and persist findings to PostgreSQL."""
    source_files = db.execute(
        select(SourceFile).where(SourceFile.repository_id == repository_id)
    ).scalars().all()

    python_files = [
        f for f in source_files
        if f.path.endswith(".py")
    ]

    # Delete existing findings for this repository (full re-analysis)
    db.execute(
        delete(Finding).where(Finding.repository_id == repository_id)
    )

    all_findings: list[Finding] = []
    for sf in python_files:
        raw_findings = analyze_python_file(
            content=sf.content,
            file_path=sf.path,
            max_function_lines=max_function_lines,
        )
        for rf in raw_findings:
            finding = Finding(
                repository_id=repository_id,
                file_path=rf["file_path"],
                line_number=rf["line_number"],
                severity=rf["severity"],
                category=rf["category"],
                message=rf["message"],
                rule_id=rf["rule_id"],
            )
            db.add(finding)
            all_findings.append(finding)

    db.commit()

    return AnalysisResult(
        repository_id=repository_id,
        files_analyzed=len(python_files),
        total_findings=len(all_findings),
        findings=all_findings,
    )

