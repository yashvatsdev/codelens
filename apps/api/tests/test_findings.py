import unittest

from app.db.database import SessionLocal
from app.models.finding import Finding
from app.models.repository import Repository
from app.schemas.finding import FindingCreate, FindingResponse


class TestFindingModel(unittest.TestCase):
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
                    Repository.owner == "finding-test-org"
                )
            )
        ).delete(synchronize_session=False)
        self.db.query(Repository).filter(
            Repository.owner == "finding-test-org"
        ).delete(synchronize_session=False)
        self.db.commit()

    def _create_repo(self) -> Repository:
        repo = Repository(
            github_id="finding-test-org/finding-repo",
            name="finding-repo",
            full_name="finding-test-org/finding-repo",
            owner="finding-test-org",
            url="https://github.com/finding-test-org/finding-repo",
            default_branch="main",
        )
        self.db.add(repo)
        self.db.commit()
        self.db.refresh(repo)
        return repo

    def test_create_finding_and_relationship(self):
        repo = self._create_repo()

        finding = Finding(
            repository_id=repo.id,
            file_path="src/main.py",
            line_number=42,
            severity="warning",
            category="security",
            message="Hardcoded IP address detected",
            rule_id="SEC-001",
        )
        self.db.add(finding)
        self.db.commit()
        self.db.refresh(finding)

        self.assertIsNotNone(finding.id)
        self.assertEqual(finding.repository_id, repo.id)
        self.assertEqual(finding.rule_id, "SEC-001")
        self.assertIsNotNone(finding.created_at)

        # Verify relationship from Repository -> Findings
        self.db.refresh(repo)
        self.assertEqual(len(repo.findings), 1)
        self.assertEqual(repo.findings[0].id, finding.id)

        # Verify relationship from Finding -> Repository
        self.assertEqual(finding.repository.id, repo.id)
        self.assertEqual(finding.repository.full_name, "finding-test-org/finding-repo")

    def test_finding_cascade_delete(self):
        repo = self._create_repo()
        finding = Finding(
            repository_id=repo.id,
            file_path="app.ts",
            line_number=10,
            severity="error",
            category="bug",
            message="Null pointer dereference risk",
            rule_id="BUG-002",
        )
        self.db.add(finding)
        self.db.commit()

        # Delete repository and verify cascade deletes finding
        self.db.delete(repo)
        self.db.commit()

        remaining_findings = self.db.query(Finding).filter(Finding.repository_id == repo.id).all()
        self.assertEqual(len(remaining_findings), 0)

    def test_finding_schemas(self):
        create_payload = FindingCreate(
            repository_id=1,
            file_path="config.json",
            line_number=None,
            severity="info",
            category="style",
            message="Unused configuration key",
            rule_id="STYLE-003",
        )
        self.assertEqual(create_payload.repository_id, 1)
        self.assertEqual(create_payload.severity, "info")
        self.assertIsNone(create_payload.line_number)


if __name__ == "__main__":
    unittest.main()

