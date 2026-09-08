import unittest
from unittest.mock import MagicMock, patch

from app.api.routes.repositories import (
    analyze_repository_endpoint,
    connect_github_repository,
    get_repository_findings,
    get_repository_files,
    ingest_repository_endpoint,
)
from app.db.database import SessionLocal
from app.models.finding import Finding
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.schemas.repository import GitHubRepositoryCreate
from app.services.github import GitHubFileContent, GitHubTreeEntry


class TestEndToEndRepositoryWorkflow(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        # Delete findings, source_files, and repository for test org
        self.db.query(Finding).filter(
            Finding.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "e2e-workflow-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(SourceFile).filter(
            SourceFile.repository_id.in_(
                self.db.query(Repository.id).filter(
                    Repository.owner == "e2e-workflow-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "e2e-workflow-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    @patch("app.services.ingestion.fetch_file_content")
    @patch("app.services.ingestion.fetch_repo_tree")
    def test_full_repository_lifecycle_workflow(self, mock_tree, mock_content):
        """End-to-end integration test:
        1. Connect GitHub Repository (creates Repository record)
        2. Ingest Repository (fetches tree & files, stores SourceFile records in PostgreSQL)
        3. Retrieve SourceFiles via GET /repositories/{id}/files
        4. Analyze Repository (reads SourceFiles, runs static analyzer, stores Finding records)
        5. Retrieve Findings via GET /repositories/{id}/findings
        6. Re-run Analysis (verifies duplicate prevention / idempotency)
        """
        # Step 1: Connect Repository
        payload = GitHubRepositoryCreate(
            url="https://github.com/e2e-workflow-org/sample-service",
            default_branch="main",
        )
        repo = connect_github_repository(payload, self.db)
        self.assertIsNotNone(repo.id)
        self.assertEqual(repo.full_name, "e2e-workflow-org/sample-service")

        # Step 2: GitHub Ingestion
        mock_tree.return_value = [
            GitHubTreeEntry(path="app/main.py", type="blob", sha="sha_main", size=120),
            GitHubTreeEntry(path="app/utils.py", type="blob", sha="sha_utils", size=80),
            GitHubTreeEntry(path="README.md", type="blob", sha="sha_readme", size=50),
        ]
        mock_content.side_effect = [
            GitHubFileContent(
                path="app/main.py",
                sha="sha_main",
                content="import sys\n# TODO: fix authentication\ntry:\n    pass\nexcept:\n    pass\n",
                size=120,
            ),
            GitHubFileContent(
                path="app/utils.py",
                sha="sha_utils",
                content="import math\n\ndef helper():\n    pass\n",
                size=80,
            ),
            GitHubFileContent(
                path="README.md",
                sha="sha_readme",
                content="# Sample Service Documentation",
                size=50,
            ),
        ]

        ingest_summary = ingest_repository_endpoint(repo.id, db=self.db)
        self.assertEqual(ingest_summary.files_fetched, 3)
        self.assertEqual(ingest_summary.files_stored, 3)

        # Verify SourceFile records stored in PostgreSQL
        stored_files = self.db.query(SourceFile).filter(SourceFile.repository_id == repo.id).all()
        self.assertEqual(len(stored_files), 3)

        # Step 3: GET /repositories/{id}/files API
        files_response = get_repository_files(repo.id, db=self.db)
        self.assertEqual(len(files_response), 3)
        file_paths = sorted([f.path for f in files_response])
        self.assertEqual(file_paths, ["README.md", "app/main.py", "app/utils.py"])

        # Step 4: Run Static Analysis (POST /repositories/{id}/analyze)
        analysis_summary = analyze_repository_endpoint(repo.id, db=self.db)
        self.assertEqual(analysis_summary.repository_id, repo.id)
        self.assertEqual(analysis_summary.files_analyzed, 2)  # app/main.py and app/utils.py (.py files)
        self.assertGreater(analysis_summary.total_findings, 0)

        # Verify Finding records stored in PostgreSQL
        stored_findings = self.db.query(Finding).filter(Finding.repository_id == repo.id).all()
        self.assertEqual(len(stored_findings), analysis_summary.total_findings)

        # Step 5: GET /repositories/{id}/findings API
        findings_response = get_repository_findings(repo.id, db=self.db)
        self.assertEqual(len(findings_response), len(stored_findings))
        rule_ids = {f.rule_id for f in findings_response}
        self.assertIn("PY-UNUSED-IMPORT", rule_ids)
        self.assertIn("PY-TODO-FIXME", rule_ids)
        self.assertIn("PY-BARE-EXCEPT", rule_ids)

        # Step 6: Re-run Analysis to verify duplicate prevention
        reanalysis_summary = analyze_repository_endpoint(repo.id, db=self.db)
        self.assertEqual(reanalysis_summary.total_findings, len(stored_findings))
        post_reanalysis_findings_count = (
            self.db.query(Finding).filter(Finding.repository_id == repo.id).count()
        )
        self.assertEqual(post_reanalysis_findings_count, len(stored_findings))


if __name__ == "__main__":
    unittest.main()
