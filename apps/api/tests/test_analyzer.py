import unittest

from app.db.database import SessionLocal
from app.models.finding import Finding
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.services.analyzer import (
    analyze_python_file,
    analyze_repository,
    analyze_source_file,
)
from app.services.js_analyzer import analyze_javascript_file


class TestPythonStaticAnalyzer(unittest.TestCase):
    def test_syntax_error_detection(self):
        code = "def broken_func(:\n    pass\n"
        findings = analyze_python_file(code, "broken.py")
        self.assertTrue(any(f["rule_id"] == "PY-SYNTAX-001" for f in findings))
        syntax_finding = next(f for f in findings if f["rule_id"] == "PY-SYNTAX-001")
        self.assertEqual(syntax_finding["severity"], "error")
        self.assertEqual(syntax_finding["category"], "syntax")
        self.assertEqual(syntax_finding["line_number"], 1)

    def test_unused_import_detection(self):
        code = (
            "import os\n"
            "import sys\n"
            "from math import sin, cos\n"
            "\n"
            "print(os.name)\n"
            "x = sin(0.5)\n"
        )
        findings = analyze_python_file(code, "test_imports.py")
        unused_rules = [f for f in findings if f["rule_id"] == "PY-UNUSED-IMPORT"]
        unused_imported_names = [f["message"].split("'")[1] for f in unused_rules]

        self.assertIn("sys", unused_imported_names)
        self.assertIn("cos", unused_imported_names)
        self.assertNotIn("os", unused_imported_names)
        self.assertNotIn("sin", unused_imported_names)

    def test_todo_fixme_comment_detection(self):
        code = (
            "# TODO: refactor this function\n"
            "def calculate():\n"
            "    # FIXME: handle division by zero\n"
            "    return 1 / 1\n"
        )
        findings = analyze_python_file(code, "test_comments.py")
        todo_findings = [f for f in findings if f["rule_id"] == "PY-TODO-FIXME"]
        self.assertEqual(len(todo_findings), 2)
        self.assertEqual(todo_findings[0]["line_number"], 1)
        self.assertEqual(todo_findings[1]["line_number"], 3)

    def test_bare_except_detection(self):
        code = (
            "try:\n"
            "    val = int('abc')\n"
            "except:\n"
            "    val = 0\n"
        )
        findings = analyze_python_file(code, "test_except.py")
        bare_excepts = [f for f in findings if f["rule_id"] == "PY-BARE-EXCEPT"]
        self.assertEqual(len(bare_excepts), 1)
        self.assertEqual(bare_excepts[0]["line_number"], 3)
        self.assertEqual(bare_excepts[0]["severity"], "warning")

    def test_long_function_detection(self):
        lines = ["def huge_function():"] + [f"    x_{i} = {i}" for i in range(60)]
        code = "\n".join(lines) + "\n"
        findings = analyze_python_file(code, "test_long.py", max_function_lines=50)
        long_funcs = [f for f in findings if f["rule_id"] == "PY-LONG-FUNCTION"]
        self.assertEqual(len(long_funcs), 1)
        self.assertEqual(long_funcs[0]["line_number"], 1)
        self.assertIn("huge_function", long_funcs[0]["message"])

    def test_clean_file_no_findings(self):
        code = (
            "import math\n"
            "\n"
            "def add(a: int, b: int) -> int:\n"
            "    return a + b + int(math.pi)\n"
        )
        findings = analyze_python_file(code, "clean.py")
        self.assertEqual(len(findings), 0)


class TestJavaScriptStaticAnalyzer(unittest.TestCase):
    def test_console_log_detection(self):
        code = (
            "function greet(name) {\n"
            "  console.log('Hello', name);\n"
            "  return name;\n"
            "}\n"
        )
        findings = analyze_javascript_file(code, "app.js")
        log_findings = [f for f in findings if f["rule_id"] == "JS-CONSOLE-LOG"]
        self.assertEqual(len(log_findings), 1)
        self.assertEqual(log_findings[0]["line_number"], 2)
        self.assertEqual(log_findings[0]["severity"], "warning")
        self.assertEqual(log_findings[0]["category"], "style")
        self.assertIn("console.log", log_findings[0]["message"])

    def test_eval_usage_detection(self):
        code = (
            "function executeCode(userString) {\n"
            "  const result = eval(userString);\n"
            "  return result;\n"
            "}\n"
        )
        findings = analyze_javascript_file(code, "runner.ts")
        eval_findings = [f for f in findings if f["rule_id"] == "JS-EVAL-USAGE"]
        self.assertEqual(len(eval_findings), 1)
        self.assertEqual(eval_findings[0]["line_number"], 2)
        self.assertEqual(eval_findings[0]["severity"], "error")
        self.assertEqual(eval_findings[0]["category"], "security")
        self.assertIn("eval()", eval_findings[0]["message"])

    def test_debugger_detection(self):
        code = (
            "function processData(items) {\n"
            "  debugger;\n"
            "  return items.map(x => x * 2);\n"
            "}\n"
        )
        findings = analyze_javascript_file(code, "process.tsx")
        debugger_findings = [f for f in findings if f["rule_id"] == "JS-DEBUGGER"]
        self.assertEqual(len(debugger_findings), 1)
        self.assertEqual(debugger_findings[0]["line_number"], 2)
        self.assertEqual(debugger_findings[0]["severity"], "warning")
        self.assertEqual(debugger_findings[0]["category"], "bug")
        self.assertIn("debugger", debugger_findings[0]["message"])

    def test_todo_fixme_comment_detection(self):
        code = (
            "// TODO: implement input validation\n"
            "export function parse(input: string) {\n"
            "  /* FIXME: handle edge case with null bytes */\n"
            "  return input.trim();\n"
            "}\n"
        )
        findings = analyze_javascript_file(code, "parser.ts")
        todo_findings = [f for f in findings if f["rule_id"] == "JS-TODO-FIXME"]
        self.assertEqual(len(todo_findings), 2)
        self.assertEqual(todo_findings[0]["line_number"], 1)
        self.assertEqual(todo_findings[0]["severity"], "info")
        self.assertEqual(todo_findings[0]["category"], "maintainability")
        self.assertEqual(todo_findings[1]["line_number"], 3)

    def test_clean_js_file_no_findings(self):
        code = (
            "export function add(a, b) {\n"
            "  const sum = a + b;\n"
            "  return sum;\n"
            "}\n"
        )
        findings = analyze_javascript_file(code, "math.js")
        self.assertEqual(len(findings), 0)

    def test_clean_ts_file_no_findings(self):
        code = (
            "interface User {\n"
            "  id: string;\n"
            "  name: string;\n"
            "}\n"
            "\n"
            "export function getUserGreeting(user: User): string {\n"
            "  return `Hello, ${user.name}!`;\n"
            "}\n"
        )
        findings = analyze_javascript_file(code, "user.ts")
        self.assertEqual(len(findings), 0)

    def test_strings_do_not_trigger_rules(self):
        code = (
            'const logMsg = "console.log(inside string)";\n'
            'const evalMsg = "eval(also inside string)";\n'
            'const dbgMsg = "debugger inside string";\n'
        )
        findings = analyze_javascript_file(code, "strings.js")
        self.assertEqual(len(findings), 0)


class TestAnalyzerDispatcher(unittest.TestCase):
    def test_dispatches_python_file(self):
        code = "# TODO: python todo\nimport unused\n"
        findings = analyze_source_file(code, "module.py")
        rule_ids = {f["rule_id"] for f in findings}
        self.assertIn("PY-TODO-FIXME", rule_ids)
        self.assertIn("PY-UNUSED-IMPORT", rule_ids)

    def test_dispatches_js_ts_extensions(self):
        extensions = [".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"]
        code = "console.log('test');\n"
        for ext in extensions:
            findings = analyze_source_file(code, f"file{ext}")
            self.assertEqual(len(findings), 1, f"Failed for extension {ext}")
            self.assertEqual(findings[0]["rule_id"], "JS-CONSOLE-LOG")

    def test_unsupported_extensions_return_no_findings(self):
        code = "console.log('test');\n# TODO: unsupported\n"
        for path in ["file.txt", "file.json", "file.yaml", "file.html", "file.rs", "Dockerfile"]:
            findings = analyze_source_file(code, path)
            self.assertEqual(len(findings), 0, f"Expected no findings for {path}")


class TestAnalyzerRepositoryPersistence(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        self.db.query(Finding).filter(
            Finding.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "analyzer-test-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(SourceFile).filter(
            SourceFile.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "analyzer-test-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "analyzer-test-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo_with_source_files(self) -> Repository:
        repo = Repository(
            github_id="analyzer-test-org/analyzer-repo",
            name="analyzer-repo",
            full_name="analyzer-test-org/analyzer-repo",
            owner="analyzer-test-org",
            url="https://github.com/analyzer-test-org/analyzer-repo",
            default_branch="main",
        )
        self.db.add(repo)
        self.db.commit()
        self.db.refresh(repo)

        sf1 = SourceFile(
            repository_id=repo.id,
            path="app/main.py",
            sha="sha1",
            content="import sys\n# TODO: fix this\ntry:\n    pass\nexcept:\n    pass\n",
            size=60,
        )
        sf2 = SourceFile(
            repository_id=repo.id,
            path="app/config.json",
            sha="sha2",
            content='{"key": "value"}',
            size=20,
        )
        self.db.add_all([sf1, sf2])
        self.db.commit()
        return repo

    def test_analyze_repository_persists_findings(self):
        repo = self._create_repo_with_source_files()
        res = analyze_repository(repo.id, db=self.db)

        self.assertEqual(res.repository_id, repo.id)
        self.assertEqual(res.files_analyzed, 1)  # only app/main.py (.py)
        self.assertGreater(res.total_findings, 0)

        stored_findings = self.db.query(Finding).filter(Finding.repository_id == repo.id).all()
        self.assertEqual(len(stored_findings), res.total_findings)

        rule_ids = {f.rule_id for f in stored_findings}
        self.assertIn("PY-UNUSED-IMPORT", rule_ids)
        self.assertIn("PY-TODO-FIXME", rule_ids)
        self.assertIn("PY-BARE-EXCEPT", rule_ids)

    def test_reanalyze_replaces_existing_findings(self):
        repo = self._create_repo_with_source_files()
        analyze_repository(repo.id, db=self.db)

        initial_count = self.db.query(Finding).filter(Finding.repository_id == repo.id).count()
        self.assertGreater(initial_count, 0)

        # Run analysis again
        res2 = analyze_repository(repo.id, db=self.db)
        new_count = self.db.query(Finding).filter(Finding.repository_id == repo.id).count()

        self.assertEqual(initial_count, new_count)
        self.assertEqual(res2.total_findings, new_count)

    def test_mixed_python_and_js_ts_repository_analysis(self):
        repo = Repository(
            github_id="analyzer-test-org/mixed-repo",
            name="mixed-repo",
            full_name="analyzer-test-org/mixed-repo",
            owner="analyzer-test-org",
            url="https://github.com/analyzer-test-org/mixed-repo",
            default_branch="main",
        )
        self.db.add(repo)
        self.db.commit()
        self.db.refresh(repo)

        # 1 Python file, 1 JS file, 1 TS file, 1 unsupported file (.json)
        sf_py = SourceFile(
            repository_id=repo.id,
            path="backend/server.py",
            sha="sha_py",
            content="# TODO: python task\nimport sys\n",
            size=40,
        )
        sf_js = SourceFile(
            repository_id=repo.id,
            path="frontend/index.js",
            sha="sha_js",
            content="console.log('booting');\nconst x = eval('2+2');\n",
            size=50,
        )
        sf_ts = SourceFile(
            repository_id=repo.id,
            path="frontend/debug.ts",
            sha="sha_ts",
            content="// FIXME: remove before release\ndebugger;\n",
            size=45,
        )
        sf_json = SourceFile(
            repository_id=repo.id,
            path="package.json",
            sha="sha_json",
            content='{"name": "app"}',
            size=20,
        )
        self.db.add_all([sf_py, sf_js, sf_ts, sf_json])
        self.db.commit()

        res = analyze_repository(repo.id, db=self.db)
        # Analyzable files: backend/server.py, frontend/index.js, frontend/debug.ts = 3 files
        self.assertEqual(res.files_analyzed, 3)
        self.assertEqual(res.repository_id, repo.id)

        stored_findings = self.db.query(Finding).filter(Finding.repository_id == repo.id).all()
        self.assertEqual(len(stored_findings), res.total_findings)

        rule_ids = {f.rule_id for f in stored_findings}
        # Python findings
        self.assertIn("PY-TODO-FIXME", rule_ids)
        self.assertIn("PY-UNUSED-IMPORT", rule_ids)
        # JS findings
        self.assertIn("JS-CONSOLE-LOG", rule_ids)
        self.assertIn("JS-EVAL-USAGE", rule_ids)
        # TS findings
        self.assertIn("JS-TODO-FIXME", rule_ids)
        self.assertIn("JS-DEBUGGER", rule_ids)


class TestAnalyzerEndpoints(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        self.db.query(Finding).filter(
            Finding.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "analyzer-endpoint-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(SourceFile).filter(
            SourceFile.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "analyzer-endpoint-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "analyzer-endpoint-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo_with_source(self) -> Repository:
        repo = Repository(
            github_id="analyzer-endpoint-org/endpoint-repo",
            name="endpoint-repo",
            full_name="analyzer-endpoint-org/endpoint-repo",
            owner="analyzer-endpoint-org",
            url="https://github.com/analyzer-endpoint-org/endpoint-repo",
            default_branch="main",
        )
        self.db.add(repo)
        self.db.commit()
        self.db.refresh(repo)

        sf = SourceFile(
            repository_id=repo.id,
            path="main.py",
            sha="sha1",
            content="# TODO: implement feature\nimport unused_lib\n",
            size=40,
        )
        self.db.add(sf)
        self.db.commit()
        return repo

    def test_post_analyze_endpoint_success(self):
        from app.api.routes.repositories import analyze_repository_endpoint

        repo = self._create_repo_with_source()
        res = analyze_repository_endpoint(repo.id, db=self.db)

        self.assertEqual(res.repository_id, repo.id)
        self.assertEqual(res.files_analyzed, 1)
        self.assertEqual(res.total_findings, 2)
        self.assertEqual(len(res.findings), 2)

    def test_post_analyze_endpoint_not_found(self):
        from fastapi import HTTPException
        from app.api.routes.repositories import analyze_repository_endpoint

        with self.assertRaises(HTTPException) as ctx:
            analyze_repository_endpoint(999999, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_get_findings_endpoint_success(self):
        from app.api.routes.repositories import (
            analyze_repository_endpoint,
            get_repository_findings,
        )

        repo = self._create_repo_with_source()
        analyze_repository_endpoint(repo.id, db=self.db)

        findings = get_repository_findings(repo.id, db=self.db)
        self.assertEqual(len(findings), 2)
        rule_ids = [f.rule_id for f in findings]
        self.assertIn("PY-TODO-FIXME", rule_ids)
        self.assertIn("PY-UNUSED-IMPORT", rule_ids)

    def test_get_findings_endpoint_not_found(self):
        from fastapi import HTTPException
        from app.api.routes.repositories import get_repository_findings

        with self.assertRaises(HTTPException) as ctx:
            get_repository_findings(999999, db=self.db)
        self.assertEqual(ctx.exception.status_code, 404)

    def test_post_analyze_endpoint_no_source_files(self):
        from app.api.routes.repositories import analyze_repository_endpoint

        empty_repo = Repository(
            github_id="analyzer-endpoint-org/empty-repo",
            name="empty-repo",
            full_name="analyzer-endpoint-org/empty-repo",
            owner="analyzer-endpoint-org",
            url="https://github.com/analyzer-endpoint-org/empty-repo",
            default_branch="main",
        )
        self.db.add(empty_repo)
        self.db.commit()
        self.db.refresh(empty_repo)

        res = analyze_repository_endpoint(empty_repo.id, db=self.db)
        self.assertEqual(res.repository_id, empty_repo.id)
        self.assertEqual(res.files_analyzed, 0)
        self.assertEqual(res.total_findings, 0)
        self.assertEqual(len(res.findings), 0)



if __name__ == "__main__":
    unittest.main()

