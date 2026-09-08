import unittest

from app.db.database import SessionLocal, engine
from app.models.repository import Repository
from app.schemas.repository import GitHubRepositoryCreate, RepositoryCreate
from app.services.github import parse_github_url
from app.api.routes.repositories import (
    connect_github_repository,
    create_repository,
    delete_repository,
    get_repositories,
    get_repository,
)
from fastapi import HTTPException


class TestGitHubUrlParser(unittest.TestCase):
    def test_valid_https_url(self):
        parsed = parse_github_url("https://github.com/yashvatsdev/codelens")
        self.assertEqual(parsed.owner, "yashvatsdev")
        self.assertEqual(parsed.name, "codelens")
        self.assertEqual(parsed.full_name, "yashvatsdev/codelens")
        self.assertEqual(parsed.url, "https://github.com/yashvatsdev/codelens")

    def test_valid_https_with_git_suffix(self):
        parsed = parse_github_url("https://github.com/facebook/react.git")
        self.assertEqual(parsed.owner, "facebook")
        self.assertEqual(parsed.name, "react")
        self.assertEqual(parsed.full_name, "facebook/react")
        self.assertEqual(parsed.url, "https://github.com/facebook/react")

    def test_valid_https_with_trailing_slash(self):
        parsed = parse_github_url("https://github.com/torvalds/linux/")
        self.assertEqual(parsed.owner, "torvalds")
        self.assertEqual(parsed.name, "linux")
        self.assertEqual(parsed.full_name, "torvalds/linux")

    def test_valid_http_and_www(self):
        parsed = parse_github_url("http://www.github.com/psf/requests")
        self.assertEqual(parsed.owner, "psf")
        self.assertEqual(parsed.name, "requests")
        self.assertEqual(parsed.url, "https://github.com/psf/requests")

    def test_valid_ssh_format(self):
        parsed = parse_github_url("git@github.com:pallets/flask.git")
        self.assertEqual(parsed.owner, "pallets")
        self.assertEqual(parsed.name, "flask")
        self.assertEqual(parsed.full_name, "pallets/flask")

    def test_valid_schemeless(self):
        parsed = parse_github_url("github.com/encode/uvicorn")
        self.assertEqual(parsed.owner, "encode")
        self.assertEqual(parsed.name, "uvicorn")

    def test_invalid_empty_url(self):
        with self.assertRaises(ValueError) as ctx:
            parse_github_url("")
        self.assertIn("cannot be empty", str(ctx.exception).lower())

    def test_invalid_domain(self):
        with self.assertRaises(ValueError) as ctx:
            parse_github_url("https://gitlab.com/owner/repo")
        self.assertIn("only github repository urls", str(ctx.exception).lower())

    def test_invalid_missing_repo(self):
        with self.assertRaises(ValueError) as ctx:
            parse_github_url("https://github.com/owner")
        self.assertIn("must contain both owner and repository name", str(ctx.exception).lower())

    def test_invalid_deep_path(self):
        with self.assertRaises(ValueError) as ctx:
            parse_github_url("https://github.com/owner/repo/tree/main")
        self.assertIn("must contain both owner and repository name", str(ctx.exception).lower())


class TestRepositoryDatabaseEndpoints(unittest.TestCase):
    def setUp(self):
        self.db = SessionLocal()
        # Clean up any test repositories
        self._cleanup()

    def tearDown(self):
        self._cleanup()
        self.db.close()

    def _cleanup(self):
        test_repos = self.db.query(Repository).filter(
            Repository.owner.in_(["test-org", "test-url-org"])
        ).all()
        for repo in test_repos:
            self.db.delete(repo)
        self.db.commit()

    def test_connect_github_repository_success(self):
        payload = GitHubRepositoryCreate(
            url="https://github.com/test-org/test-project",
            default_branch="develop",
        )
        repo = connect_github_repository(payload, self.db)
        self.assertIsNotNone(repo.id)
        self.assertEqual(repo.name, "test-project")
        self.assertEqual(repo.owner, "test-org")
        self.assertEqual(repo.full_name, "test-org/test-project")
        self.assertEqual(repo.url, "https://github.com/test-org/test-project")
        self.assertEqual(repo.default_branch, "develop")

    def test_connect_github_repository_duplicate_raises_409(self):
        payload = GitHubRepositoryCreate(url="https://github.com/test-org/duplicate-project")
        connect_github_repository(payload, self.db)

        with self.assertRaises(HTTPException) as ctx:
            connect_github_repository(payload, self.db)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_connect_github_repository_invalid_url_raises_400(self):
        payload = GitHubRepositoryCreate(url="https://notgithub.com/test-org/proj")
        with self.assertRaises(HTTPException) as ctx:
            connect_github_repository(payload, self.db)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_create_repository_with_url_auto_parse(self):
        payload = RepositoryCreate(url="https://github.com/test-url-org/auto-parsed.git")
        repo = create_repository(payload, self.db)
        self.assertEqual(repo.name, "auto-parsed")
        self.assertEqual(repo.owner, "test-url-org")
        self.assertEqual(repo.full_name, "test-url-org/auto-parsed")


if __name__ == "__main__":
    unittest.main()

