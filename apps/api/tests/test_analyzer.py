import unittest

from app.db.database import SessionLocal
from app.models.finding import Finding
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.services.analyzer import (
    analyze_python_file,
    analyze_repository,
)


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

