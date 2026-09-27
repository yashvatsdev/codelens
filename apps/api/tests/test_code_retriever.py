import unittest
from sqlalchemy.orm import Session
from app.db.database import SessionLocal
from app.models.user import User
from app.models.repository import Repository
from app.models.source_file import SourceFile
from app.services.code_retriever import (
    retrieve_code_context,
    RepositoryNotFound,
    UnauthorizedRepositoryAccess,
    RetrievedContext
)

class TestCodeRetriever(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        
        self.user1 = self.db.query(User).filter_by(email="retriever1@test.com").first()
        if not self.user1:
            self.user1 = User(email="retriever1@test.com", password_hash="hash")
            self.db.add(self.user1)
            self.db.commit()
            
        self.user2 = self.db.query(User).filter_by(email="retriever2@test.com").first()
        if not self.user2:
            self.user2 = User(email="retriever2@test.com", password_hash="hash")
            self.db.add(self.user2)
            self.db.commit()
            
        self.repo1 = Repository(
            github_id="retriever_repo1",
            name="repo1",
            full_name="user1/repo1",
            owner="user1",
            url="http://github.com/user1/repo1",
            user_id=self.user1.id
        )
        self.repo2 = Repository(
            github_id="retriever_repo2",
            name="repo2",
            full_name="user2/repo2",
            owner="user2",
            url="http://github.com/user2/repo2",
            user_id=self.user2.id
        )
        self.db.add_all([self.repo1, self.repo2])
        self.db.commit()

    def tearDown(self):
        self.db.delete(self.repo1)
        self.db.delete(self.repo2)
        self.db.commit()
        self.db.close()

    def _add_files(self, repo_id, files_data):
        files = []
        for path, content in files_data.items():
            f = SourceFile(
                repository_id=repo_id,
                path=path,
                sha="dummy_sha",
                content=content,
                size=len(content)
            )
            files.append(f)
        self.db.add_all(files)
        self.db.commit()

    def test_relevant_file_retrieval(self):
        self._add_files(self.repo1.id, {
            "main.py": "def hello():\n    print('world')",
            "utils.py": "def calculate_sum(a, b):\n    return a + b"
        })
        results = retrieve_code_context(self.db, self.repo1.id, self.user1.id, "How do I calculate sum?")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].file_path, "utils.py")
        self.assertIn("calculate_sum", results[0].content)

    def test_multiple_relevant_files(self):
        self._add_files(self.repo1.id, {
            "auth.py": "def login(): pass",
            "security.py": "def verify_password(): pass\ndef login_user(): pass",
            "main.py": "print('hello')"
        })
        results = retrieve_code_context(self.db, self.repo1.id, self.user1.id, "How does login work?")
        self.assertEqual(len(results), 2)
        paths = [r.file_path for r in results]
        self.assertIn("auth.py", paths)
        self.assertIn("security.py", paths)

    def test_filename_path_matching(self):
        self._add_files(self.repo1.id, {
            "database_config.py": "DB_URL = 'xyz'",
            "main.py": "import database_config"
        })
        results = retrieve_code_context(self.db, self.repo1.id, self.user1.id, "Where is the database config?")
        self.assertTrue(len(results) > 0)
        # Should prefer database_config.py due to path match + content
        self.assertEqual(results[0].file_path, "database_config.py")

    def test_no_relevant_results(self):
        self._add_files(self.repo1.id, {
            "main.py": "def hello(): pass"
        })
        results = retrieve_code_context(self.db, self.repo1.id, self.user1.id, "How do I connect to postgresql database?")
        self.assertEqual(len(results), 0)

    def test_repository_ownership(self):
        with self.assertRaises(UnauthorizedRepositoryAccess):
            retrieve_code_context(self.db, self.repo2.id, self.user1.id, "question")

    def test_nonexistent_repository(self):
        with self.assertRaises(RepositoryNotFound):
            retrieve_code_context(self.db, 999999, self.user1.id, "question")

    def test_empty_repository(self):
        results = retrieve_code_context(self.db, self.repo1.id, self.user1.id, "question")
        self.assertEqual(results, [])

    def test_files_from_another_repository_never_returned(self):
        self._add_files(self.repo2.id, {
            "secret.py": "SECRET_KEY = 'abc'"
        })
        self._add_files(self.repo1.id, {
            "main.py": "print('hello')"
        })
        # user2 owns repo2. user1 queries repo1 for secret
        results = retrieve_code_context(self.db, self.repo1.id, self.user1.id, "Where is the secret key?")
        self.assertEqual(len(results), 0)

    def test_context_size_limits(self):
        # Create a huge file
        huge_content = "\n".join([f"line {i} with keyword target" for i in range(500)])
        self._add_files(self.repo1.id, {
            "huge.py": huge_content
        })
        results = retrieve_code_context(self.db, self.repo1.id, self.user1.id, "target")
        self.assertEqual(len(results), 1)
        # MAX_LINES_PER_FILE is 200
        self.assertLessEqual(results[0].end_line - results[0].start_line, 200)

if __name__ == "__main__":
    unittest.main()
