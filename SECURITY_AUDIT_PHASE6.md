# Phase 6 Security Audit

Date: 2026-10-07. Baseline: f57dcd81ecf44956a3a97f22a20a4fea28867cff.

## Executive Summary

Authentication, OAuth, repository ownership, resource limits, AI provider controls,
rate limiting and HTTP hardening exist. Their presence does not establish that all
guarantees in CODEX_CONTEXT.md hold. Concrete defects were found in rate-limit
identity and scope, OAuth linking/cleanup, JWT requirements, error handling and
cookie-authentication CSRF protection. The shared server GitHub token is also an
unresolved authorization boundary for a public multi-user deployment.

This report was created before production code changes. Audit sections describe
the baseline; the remediation/validation section records the final fixed state.
No live Render, Supabase, Vercel or Google configuration was inspected.

## Repository State

- Remote: origin, yashvatsdev/codelens on GitHub; fresh fetch completed.
- Execution workspace main: 1f669cd04eac8531663cd16eba49df6d9d9c0aac, 30 commits behind.
- That workspace has an existing app/core/config.py edit and untracked context file.
- IDE checkout C:/Users/yashv/codelens: main at f57dcd8.
- Audit worktree: .audit-worktree on security/final-security-audit at f57dcd8,
  matching origin/main, initially clean. All audit changes are confined there.
- CODEX_CONTEXT.md was read completely from the parent workspace. Its assertions
  about synchronized/clean local main, complete enumeration protection, universal
  OAuth cleanup and complete Phase 5 coverage are not evidence of those properties.
- Existing migrations were applied to a disposable local test database; no new
  migrations were created. The application database was not used by tests.

## Authentication

HS256 is explicitly selected when verifying sessions; Argon2 hashes passwords.
Auth responses use UserResponse, excluding password_hash and Google/provider tokens.
OAuth-only accounts are rejected by password login. Missing cookies, bad signatures
and expired tokens are rejected. However exp is not required, subject parsing is
not strictly positive/canonical, and token creation contains no random transaction
identifier. Login short-circuits hashing for unknown/OAuth-only users. Signup
reveals existing email addresses and has no backend password-length constraint.
Logout removes the browser cookie; stateless JWTs copied beforehand remain valid
until expiration. No session database/revocation redesign is proposed.

## Authorization / Tenant Isolation

Repository routes check current_user and repository.user_id before data/AI/GitHub
operations. Nested finding routes also compare finding.repository_id. Repository
listing, dashboard snapshots and PR review history are user-scoped. Ask checks
ownership in code_retriever before retrieving SourceFile rows. There is no separate
public finding router and no repository update route. No database-row IDOR was
identified by inspection; a two-user HTTP matrix is required to validate it.

CodeLens ownership is not proof of GitHub permission: any authenticated account
can connect a URL, and GitHub requests use one application-wide token (F02).

### Endpoint inventory

All application routes are listed below; FastAPI also supplies public /docs,
/docs/oauth2-redirect, /redoc and /openapi.json. The body middleware applies globally.
DB = SQLAlchemy; GH = GitHub HTTP; AI = cloud/local provider HTTP. Repository paths
use r = repository_id, f = finding_id, p = pull_request_number.

| Method / path | Auth/authorization | Main access / response | Baseline limit |
| --- | --- | --- | --- |
| POST /auth/signup | public | DB insert, Argon2, user/session | 5/min |
| POST /auth/login | public | DB lookup, Argon2, user/session | 10/min |
| POST /auth/logout | public | cookie removal | default |
| GET /auth/me | session user | DB user, public user fields | default |
| GET /auth/google/login | public | Google discovery/redirect, transaction cookies | 10/min |
| GET /auth/google/callback | state/nonce transaction | Google token/JWKS, DB link/create, redirect/session | default |
| GET /health | public | lightweight status | 120/min |
| GET /db/health | public | DB SELECT 1/status | 30/min |
| GET, POST /repositories/github/metadata | session | URL input; GH metadata | 30/min |
| POST /repositories/github | session | URL/ref input; GH metadata, DB insert | 10/min |
| POST /repositories | session | explicit metadata or URL; DB insert | default |
| GET /repositories | session, user filter | DB metadata list | default |
| GET /repositories/{r} | repository owner | DB metadata | default |
| DELETE /repositories/{r} | repository owner | DB cascade deletion | default |
| GET /repositories/{r}/files | repository owner | DB file metadata, no contents | default |
| POST /repositories/{r}/ingest | repository owner | GH tree/contents, DB source replacement | default |
| POST /repositories/{r}/analyze | repository owner | DB source/findings/snapshot | default |
| POST /repositories/{r}/scan | repository owner | background GH + DB analysis | 5/hour |
| GET /repositories/{r}/scan-status | repository owner | process-local progress | default |
| GET /repositories/{r}/findings | repository owner | DB finding data | default |
| POST /repositories/{r}/findings/{f}/explain | repository owner, nested finding | DB source, AI explanation | 20/hour |
| POST /repositories/{r}/findings/{f}/fix | repository owner, nested finding | DB source, AI suggested code/diff | 10/hour |
| POST /repositories/{r}/findings/{f}/test | repository owner, nested finding | DB source, AI test text | 10/hour |
| POST /repositories/{r}/findings/{f}/apply-fix | repository owner, nested finding | optional ref/message; AI + GH branch/commit | default |
| POST /repositories/{r}/findings/{f}/create-pr | repository owner, nested finding | branch/title/body; GH PR mutation | default |
| POST /repositories/{r}/pull-requests/{p}/review | repository owner | GH changed source/static findings | default |
| POST /repositories/{r}/pull-requests/{p}/ai-review | repository owner | GH + AI + DB PR history | 5/hour |
| POST /repositories/{r}/pull-requests/{p}/findings/fix | repository owner, file/line membership | GH + AI fix text | default |
| POST /repositories/{r}/pull-requests/{p}/comment | repository owner | summary input; GH + AI + GH comment | default |
| POST /repositories/{r}/ask | repository owner | question input; DB context + AI answer/citations | 20/hour |
| GET /pr-reviews | session, review.user_id filter | DB history, caller-controlled limit | default |
| GET /pr-reviews/{review_id} | session, review.user_id filter | DB review/detail | default |
| GET /dashboard/findings-trend | session, repository owner filter | DB snapshots | default |

No application endpoint extracts archives, writes repository files to disk or
executes the returned code. Responses containing source snippets/findings are
sensitive even when they contain no authentication credentials.

## Google OAuth

Random state and nonce, browser cookie equality, Google discovery, signed ID-token
decode, issuer/audience/expiration/nonce comparisons and verified email checks exist.
Redirect destinations come from server FRONTEND_URL, not callback inputs. Token
exchange occurs on the backend. The temporary cookies are plain random strings,
not signed cookies as a module comment claims. Browser max-age alone does not
provide server-enforced transaction expiry/replay tracking. Google authorization
codes are single-use, limiting replay; no live provider replay was attempted.
The baseline can overwrite a previously linked different google_sub, treats any
truthy email_verified as verified, and leaves transaction cookies on failure.
JWKS is cached for the process lifetime without refresh after key rotation.

## Rate Limiting

The limits package, MemoryStorage, FixedWindowRateLimiter, per-route limits,
neutral 429 and Retry-After exist. X-Forwarded-For is ignored by this middleware.
Unverified JWT sub is trusted for identity (F01). Counters are keyed by raw path,
so resource IDs, trailing slashes and alternate routes multiply budgets (F03).
The default is a per-path 60/min limit, not a total client-wide global budget.
Apply-fix, PR-fix and PR-comment are expensive but only receive the default limit.
Counters are process-local; replicas multiply effective limits. Actual Render
proxy client-IP behavior is not verified and uvicorn proxy handling is separate
from the application ignoring forwarded headers.

## Resource Exhaustion

10,000 tree entries, 200 ingestion files, 100,000 bytes/file, 20,000,000 aggregate
source bytes, 1,000,000 request bytes, 120,000 AI prompt characters and 4,096 output
tokens are configured. Ask retrieves at most five excerpts of 200 lines each,
1,000 lines total, and questions are at most 2,000 characters. PR review paginates
to 300 files; oversize source is checked after download. GitHub JSON responses
are read in full before tree-count checks. Ingestion does not independently reject
an actual oversized individual file when tree metadata underestimates it.
Scan progress is process-local. Checking active state then setting queued state
uses separate locks, allowing a concurrent-start race; direct ingest/analyze
routes also bypass the background scan guard. Repository lists, trend history,
caller-supplied PR history limit, snapshots and in-memory scan entries can grow.

## AI Security

Centralized provider controls enforce prompt size, output limits, cloud timeout
and one cloud-to-Ollama fallback. No tool execution, arbitrary DB query or backend
secret is supplied to the model. Repository content is untrusted prompt data;
it can affect suggestions but cannot itself execute server commands. Generated
tests are returned as text. GitHub changes require explicit endpoints and never
merge automatically. Generated changes still require human review.
Provider error logs interpolate raw exceptions, and sanitize_ai_error preserves
unrecognized raw text. Ask catches AIQuotaExceededError and changes 429 to 503.
Several AI services require a Gemini key even when Ollama-only is configured.

## GitHub Security

Token use is server-side Authorization headers; response schemas do not serialize
it. The same token serves all CodeLens users; no GitHub user connection/permission
binding exists. Manual repository creation accepts owner/name/full_name/URL
without validating them when all are supplied. Branch and ref parameters are
interpolated in URLs without consistently encoding them. The branch client
returns upstream response bodies and reasons through GitHubAPIError.

## SSRF

Normal GitHub URL parsing constructs https://api.github.com requests rather than
fetching the supplied URL. Direct localhost, private-IP, file and FTP repository
URLs are rejected by that parser. OAuth discovery starts from a fixed Google URL;
Ollama URL is server configuration, not a user request field. PR contents_url is
used without host/scheme validation and urllib follows redirects. A compromised
or unexpected upstream response could therefore request internal targets. An
attacker-controlled redirect from the real GitHub API was not demonstrated.
Do not label this as a verified internet-exploitable internal-network SSRF.

## Path Traversal

Repository paths are stored as DB strings; analyzers process strings. No local
repository checkout/extraction/write path exists. Remote GitHub identifier and
path/ref validation needs tightening (F07), but arbitrary local filesystem writes
were not identified. Suggested test_file names are not executed or written.

## Command Execution

No dangerous command execution path identified in the application. Python AST
parsing and JavaScript pattern matching do not run repository code. Occurrences
of eval in the JS analyzer are detection rules, not calls. CI/Docker shell commands
are static, with no untrusted GitHub issue/PR text interpolation.

## SQL Injection / Database Security

Application SQLAlchemy queries use expression parameters. The raw health SQL is
constant SELECT 1. Test sequence SQL is constant. No dynamic user-derived SQL or
dynamic table names were identified. Repository user_id is nullable for legacy
rows; owner checks deny these rows to ordinary authenticated users. Repository
deletion cascades source/findings/snapshots; PR FK has database cascade. Unique
email/google_sub and per-user github_id constraints exist. OAuth/signup concurrent
inserts can raise IntegrityError; safe rollback/redirect needs coverage.
Tests mutate rows and synchronize sequences, so they must use a disposable DB.

## Error Handling

/db/health exposes str(e). Provider/branch errors can expose internal diagnostics
and upstream bodies; AI logs also include exception strings. Unhandled errors
are generic framework 500 responses with debug disabled, but explicit raw-error
responses bypass that protection. Validation responses can include submitted
password input; error schemas must avoid returning it.

## CORS / CSRF / Cookies

CORS trusts the configured FRONTEND_URL and uses credentials. Actual production
value is unverified. Wildcard/invalid origin configuration is not rejected at
startup. Session cookies are HttpOnly, path=/, host-only; Secure and SameSite are
configurable and default to development values. Lax prevents most cross-site
authenticated POSTs, but default Vercel/Render hostnames require cross-site cookie
configuration for credentialed fetch. SameSite=None enables simple cross-site
logout POSTs; CORS does not prevent side effects. No server Origin validation
exists in the baseline. Browser third-party-cookie restrictions remain a deployment
compatibility issue independent of backend correctness.

## Security Headers

Security middleware supplies nosniff, DENY, referrer/permissions policies, HSTS
for an HTTPS scope and no-store on auth. 413 bypasses parts of this header path.
CORS is registered inside rate/body middleware, so their direct 429/413 responses
do not receive CORS headers. Swagger remains enabled and no backend CSP is added.
Streaming overflow can be converted by FastAPI to a body-parse 400 instead of 413.

## Secret Leakage

Safe detection scanned 388 reachable history blobs for private keys, GitHub token
formats, Google API-key formats and AWS access-key IDs: no matches. No .env file
was found in reachable history. This is bounded pattern detection, not proof that
arbitrary opaque credentials have never been committed. Tracked config/Compose/CI
and seed files contain identifiable development/test credentials; values are not
reproduced here. No verified production credential exposure was established.
.dockerignore excludes .env variants, git metadata and tests; Docker COPY is
explicit. Root .gitignore does not cover every .env.* name. Backend configuration
has a public default signing secret; deployment must not accept it silently.

## Dependency Security

Python requirements pin many packages but leave authentication/provider packages
open-ended; no complete Python lock exists. Both psycopg and psycopg2 drivers are
declared. npm lockfile exists; Next.js is 16.3.4, React 19.2.8. Maintainer advisories
were checked, not inferred from package age. Next.js September release recommends
16.3.8, but listed production issues require features not configured here (remote
image patterns, Pages Router/catch-all/cache components or metadata image routes).
The next/og RCE advisory also requires ImageResponse usage, not found here. No
CodeLens exploit of those advisories was established. Dependency scanning and
resolved-version results are recorded under validation; no broad upgrade.

Sources: [Next.js September release](https://nextjs.org/blog/september-2026-security-release),
[next/og advisory](https://github.com/vercel/next.js/security/advisories/GHSA-vcvr-r3jv-pc5j),
[joserfc claim validation advisory](https://github.com/authlib/joserfc/security/advisories/GHSA-r74j-q665-7rpj).

## Docker Security

Python 3.13 slim image, explicit copies, uvicorn 0.0.0.0:8000; no privileged mode
or Docker socket mounts. Image has no USER directive and runs as root. Compose
is development-oriented: database port exposure, development credentials, insecure
cookies and fallback signing key. Do not deploy this Compose configuration as a
production security configuration. No production image was built in this audit.

## CI/CD Security

Backend push/PR workflow uses Python 3.13 and PostgreSQL 16, applies migrations,
then pytest. It does not use production repository secrets or interpolate PR text
into shell scripts. Action versions are tags, permissions are not explicitly
restricted, and dependency/security scanning is absent. pull_request_target is
not used. Real repository token permissions and branch protection are unverified.

## Frontend Security

Credentialed fetch, in-memory user context and backend OAuth navigation exist.
No localStorage/sessionStorage authentication token or dangerouslySetInnerHTML
was found. React displays code/AI output as text. NEXT_PUBLIC_API_URL is the public
backend address; no backend secret environment variable is referenced by browser
code. Repository href is server-stored data, including arbitrary manual-create
URL in the baseline. No verified cross-user XSS was established. Logout clears
UI state even if the API logout fails; that does not revoke a surviving cookie.

## Phase 1–5 Regression Check

| Protection | Baseline result |
| --- | --- |
| Password + JWT authentication | Present; claim/password validation gaps |
| Google OAuth | Present; linking/cleanup/claim-type gaps |
| Repository/finding ownership | Present; HTTP matrix to be added |
| Rate limiting | Present; identity/path bypasses |
| Resource limits | Present; concurrency/response-size limitations |
| AI quotas/provider limits | Present; Ask quota HTTP mapping regression |
| Request-body limit | Present; streaming behavior needs regression test |
| Security headers | Present; direct middleware responses incomplete |
| FRONTEND_URL CORS | Present; live value unknown, middleware ordering defect |
| Signup enumeration protection | Not present despite context claim |

## Findings

Every entry refers to the inspected baseline, not an invented live incident.

| ID / severity | Location | Vulnerability and impact | Recommended fix | Exploit verification |
| --- | --- | --- | --- | --- |
| F01 High | core/rate_limit.py _get_client_key | Unverified sub lets forged cookies rotate login/signup budgets or consume another user's counters | Verify signature/claims; use IP for public auth flows | Verified locally: forged cookie selected a user budget; regression now passes |
| F02 High | GitHub services; repository connection/apply-fix/create-pr/comment | Shared server token authority is available to arbitrary CodeLens accounts that connect a repository; CodeLens ownership does not prove GitHub permission | Explicit per-user GitHub authorization or restricted operator-approved access policy before public signup | Authority flow confirmed; no live private data read or GH write attempted |
| F03 Medium | core/rate_limit.py dispatch/ROUTE_LIMITS | Raw path namespaces and alternate routes multiply expensive-operation budgets; no client-wide global bucket | Canonical route buckets, auth IP bucket, default global bucket and missing expensive-route rules | Verified locally: rotating resource IDs/trailing slashes avoided the operation budget; aggregate/alternate/header tests now pass |
| F04 Medium | main.py db_health; services/branch_fixer.py; AI service logging | Raw DB/upstream/provider exceptions can disclose internals or credentials if embedded by upstream | Generic responses and no raw provider exception logs | Verified with synthetic diagnostics: DB/GH responses and AI logging; neutral errors and secret-safe Settings representations now tested |
| F05 Medium | routes/auth.py _resolve_user/_fail_redirect; core/oauth.py | Existing linked Google identity can be replaced; failure cookies persist; truthy nonboolean verification accepted | Reject conflicting linked sub, strict verified flag/claims, always clear failure cookies | Verified with mocked resolution/callback: linked sub replacement, truthy flag and retained failure cookies; regressions now pass |
| F06 Medium | api/deps.py; core/security.py; schemas/user.py | Missing exp accepted, subjects loosely parsed, deterministic sessions within a second, empty/huge passwords accepted | Require claims, canonical positive ID, random token ID, bounded signup/login fields | Verified locally: signed sessions without exp/noncanonical IDs accepted, duplicate token issuance, corrupt hash failure and password bounds; regressions now pass |
| F07 Medium | routes/repositories.py create_repository; services/github.py | Manual route skips URL/identifier validation; path/query-bearing names reach remote API URLs and arbitrary URL reaches browser | Canonical URL/metadata consistency and identifier validation | Verified locally: explicit metadata bypassed parser and dot identifiers accepted; invalid cases now rejected before DB/network |
| F08 Medium (deployment-dependent) | core/middleware.py; auth/logout | SameSite=None cookie sent on simple cross-site logout POST; CORS is not a CSRF gate | Validate Origin for unsafe browser requests, reject cross-site requests missing Origin | Verified locally: cross-site-header logout mutated cookies; now 403 before mutation; live cookie behavior not exercised |
| F09 Medium | core/config.py auth_secret_key | Known default signing key silently enables token forgery when operator omits configuration | Require explicit signing key; validate production cookie/origin configuration | Verified default acceptance by inspection/config tests; omission/unsafe origins/cookies now rejected; live omission not verified |
| F10 Medium | api/routes/ask.py exception handling | Quota/context exhaustion becomes 503 instead of neutral 429 | Propagate AIQuotaExceededError to existing handler | Verified mocked quota returned 503; regression now receives existing 429 quota schema |
| F11 Medium | routes/auth.py signup; schemas/user.py | Duplicate signup discloses email existence; API accepts weaker password than advertised frontend | Deliberate signup UX decision; enforce existing advertised length on backend | Distinct signup 409 confirmed; password bounds fixed/tested, enumeration deliberately remains pending UX decision |
| F12 Medium | scan route/progress; unbounded list/history reads | Concurrent scan-start check/set race, direct ingest/analyze overlap, unbounded retained histories/response reads | Atomic scan claim and durable/shared limits/pagination where necessary | Separate locking confirmed by inspection, concurrency stress/active direct-route tests now pass; negative/unbounded PR limit reproduced and bounded; upstream/history growth remains |
| F13 Low | core/middleware.py; main.py middleware order | 429/413 lack CORS; 413 omits some security headers; chunk overflow may return 400 | Reorder CORS; consistent security headers; pre-parse streaming limit | Verified local HTTP/ASGI: missing early CORS/headers and swallowed streaming overflow; regressions now pass |
| F14 Low | Dockerfile, .gitignore, CI | Root container, incomplete env ignores, unpinned auth dependencies/actions and implicit CI permissions | Least privilege, env ignore coverage, planned dependency/action locking | Configuration verified; no remote compromise demonstrated |
| F15 Low | core/oauth.py JWKS cache; urllib HTTP clients | Lifetime JWKS cache can fail after rotation; unexpected contents URLs/redirects lack explicit policy | Expiring/refreshed JWKS and constrained outbound URLs/redirect policy | Code confirmed; malicious real GitHub redirect not verified |
| F16 Informational | core/security.py logout architecture; deployment | Stateless logout does not revoke copies; process-local rate/scan state; browser cross-site-cookie compatibility | Document constraints and validate deployment topology | Architecture verified, live topology unknown |

## Final Risk Assessment

High after remediation because F02 remains unresolved. A public multi-user
deployment must resolve that authority boundary rather than assuming a CodeLens
repository row establishes GitHub permission. The audit, scoped fixes and local
validation are complete; Phase 6 cannot be signed off for production release.
Live production configuration cannot be certified from repository files.

Baseline application findings: 0 Critical, 2 High, 10 Medium, 3 Low and 1
Informational. Dependency-advisory severities below are separate from these
application findings and do not establish CodeLens exploitability.

## Remediation and Validation

- Baseline full backend run: 439 passed, 24 warnings, 13.76 seconds, Python 3.14
  and local PostgreSQL 18. This is an actual run, not the historical claim.
- CI targets Python 3.13/PostgreSQL 16; the local result does not replace CI.
- New focused regressions: 103 passed, 3 warnings, 1.84 seconds.
- Final complete backend suite: 542 passed, 25 warnings, 11.51 seconds.
- Frontend: `npx tsc --noEmit` passed (no typecheck script exists).
- Frontend: `npm run build` passed; all five listed routes prerendered.
  The first build failed fetching Google fonts in the restricted network; the
  retry with network access passed. No frontend source/lockfile was changed.
- `pip-audit -r requirements.txt --format json`: resolved requirements contain
  no known advisories at audit time. Resolution of unpinned requirements is not
  a lockfile or proof that every deployed environment uses those versions.
- `npm audit --json --package-lock-only`: exit 1, nine affected package entries,
  one Critical and eight High. Next 16.3.4 is flagged for next/og ImageResponse;
  that API is not used here. sharp 0.35.4 is flagged for librsvg SVG decoding;
  no user-upload/SVG-processing feature was found. Developer/build dependencies
  include brace-expansion 1.1.18, braces/micromatch/fast-glob/ESLint dependency
  chains and source-map-js 1.2.1. No advisory exploit was run or demonstrated.
  Review targeted patch updates separately; no automatic audit fix/downgrade.
- `git diff --check`: passed. Changed-file credential-format detection: no hits.
  No .env, migrations, frontend source or dependency manifests changed.
- Test environment: local Windows PostgreSQL 18/Python 3.14, not Compose's
  PostgreSQL 16/Python 3.13. Docker was unavailable on PATH. The requested local
  codelens_test database was absent on the available local instance and was
  created/applied with existing migrations using existing local credentials.
  codelens_test is retained. Only the additional audit-created temporary database
  codelens_phase6_audit_20261007 was dropped; it held disposable test data and is
  not recoverable without rebuilding fixtures. Application data was untouched.
- Warnings include dependency deprecations and existing OAuth AsyncMock
  unawaited-coroutine warnings. They are recorded, not claimed resolved.
- Test-output incident: an early pytest assertion expanded Settings and displayed
  the local database URL. Values are not repeated or persisted in this report.
  Secret settings now use repr=False and configuration error input is hidden;
  later runs used restricted tracebacks. Rotate the exposed local DB credential.
- No production code, frontend, migrations, secrets or deployment settings changed
  before this initial report was written.

### Final remediation status

| Finding | Status after this audit |
| --- | --- |
| F01 | Fixed: verified session identity; public auth IP identity |
| F02 | OPEN High release blocker: GitHub-token authority requires a deliberate access policy or per-user authorization; no architecture redesign performed |
| F03 | Fixed: canonical operation buckets, client-wide 60/min default (health separate), shared connect/metadata alternatives, costly-operation limits |
| F04 | Fixed targeted leaks: health/GH upstream diagnostics, AI provider/service logs and unknown error strings, validation inputs, Settings repr/config error inputs; no universal infrastructure-log certification |
| F05 | Fixed conflicting Google linkage, strict boolean verification/sub types, failure cookie cleanup and neutral IntegrityError rollback/redirect; existing OIDC architecture retained |
| F06 | Fixed required exp/sub, bounded canonical positive DB ID, fresh random jti, corrupt-hash handling and signup/login password bounds |
| F07 | Partial: manual URL/metadata and dot identifiers fixed; consistent ref/path encoding remains a hardening task, no filesystem-write exploit found |
| F08 | Fixed unsafe-method browser Origin check and missing-Origin cross-site Fetch Metadata rejection; non-browser clients without Origin remain compatible |
| F09 | Fixed implicit signing key; explicit 32-character-minimum key required, origin/cookie validation, Compose app/test require AUTH_SECRET_KEY; live values unknown |
| F10 | Fixed Ask quota propagation, preserving existing global neutral 429 response |
| F11 | Partial: backend signup 8–1024, login 1–1024 characters; existing duplicate-signup 409 remains to preserve the current API/UX pending a product decision |
| F12 | Partial: atomic scan claim across scan/ingest/analyze, actual individual-source-size recheck and PR history 1–100 bounds; process-local stores, other history/list growth and upstream full-body reads remain |
| F13 | Fixed tested 429/413 CORS/header paths and bounded pre-parse streaming checks; preflight/framework-generated catastrophic 500 header coverage is not certified |
| F14 | Mostly OPEN Low: root image, incomplete env ignore coverage, dependency/action locks and CI permissions; Compose signing-key fallback removed |
| F15 | Partial: PR content URLs restricted to HTTPS api.github.com/repos; urllib redirect policy and lifetime JWKS cache remain |
| F16 | Documented architecture/deployment constraints; logout revocation and multi-replica/shared state not redesigned |

### Regression coverage and compatibility

New tests cover forged/valid/public-auth identities; required/malformed session
claims; fresh sessions; malformed Argon2 hashes; password bounds; OAuth account
collision/failure cookies/truthy verification; neutral DB/GH/AI errors and logs;
CSRF trusted/untrusted origins; early CORS/security headers; ASGI streaming
overflow; manual URL/metadata and remote traversal; unexpected PR content URLs;
scan concurrency/entrypoint overlap; bounded PR history; configuration secrecy;
global/alternate/forwarded-header/expensive-operation budgets. The actual-DB HTTP
matrix exercises 19 cross-user operations, 19 unauthenticated operations and five
mixed repository/finding operations. Foreign source/finding markers never appear
in their responses. No real GitHub mutation or Google login was attempted.

Existing tests that expected raw provider/scan error strings were updated to
expect neutral messages; concurrency mocks now target the atomic claim operation.
Existing response structures, routes, JWT/cookie/OAuth architecture and ownership
schema remain. Deliberate security behavior changes: weak signup passwords,
untrusted browser origins, inconsistent manual metadata, overlapping processing,
invalid PR history limits and unsafe startup settings are rejected. Authenticated
CLI clients without browser Origin headers still work. A stricter aggregate rate
budget may require frontend polling/backoff tuning under sustained use; health
probes keep independent budgets. Operators must explicitly supply AUTH_SECRET_KEY,
use an exact FRONTEND_URL origin, secure cookies for non-local deployments and
secure cookies for SameSite=None. No live or .env values were changed by the audit.

Sharp advisory source: [maintainer librsvg advisory](https://github.com/lovell/sharp/security/advisories/GHSA-wq5f-xc86-pv6w).

### Git handoff and exact files changed

All changes below are uncommitted in `.audit-worktree`, branch
`security/final-security-audit`, still based on f57dcd8 matching origin/main.
The parent checkout remains on stale main with its pre-existing config edit and
context file preserved. Do not stage the nested worktree from the parent main;
review with `git -C .audit-worktree diff` and `git -C .audit-worktree status`.
No commit, push, PR, migration creation, merge or deployment was performed.
Review and then commit the tested scoped fixes on the audit branch; request PR
creation explicitly. Resolve F02 before any public multi-user production release.

```text
SECURITY_AUDIT_PHASE6.md
apps/api/app/api/deps.py
apps/api/app/api/routes/ask.py
apps/api/app/api/routes/auth.py
apps/api/app/api/routes/pr_reviews.py
apps/api/app/api/routes/repositories.py
apps/api/app/core/ai_errors.py
apps/api/app/core/config.py
apps/api/app/core/middleware.py
apps/api/app/core/rate_limit.py
apps/api/app/core/security.py
apps/api/app/main.py
apps/api/app/schemas/user.py
apps/api/app/services/ai_pr_fixer.py
apps/api/app/services/ai_pr_reviewer.py
apps/api/app/services/ai_provider.py
apps/api/app/services/branch_fixer.py
apps/api/app/services/explainer.py
apps/api/app/services/fixer.py
apps/api/app/services/github.py
apps/api/app/services/ingestion.py
apps/api/app/services/pr_commenter.py
apps/api/app/services/pr_reviewer.py
apps/api/app/services/scan_progress.py
apps/api/app/services/test_generator.py
apps/api/tests/test_ai_error_handling.py
apps/api/tests/test_ai_pr_fixer.py
apps/api/tests/test_ai_pr_reviewer.py
apps/api/tests/test_ask.py
apps/api/tests/test_phase6_security.py
apps/api/tests/test_rate_limiting.py
apps/api/tests/test_resource_limits.py
apps/api/tests/test_scan_progress.py
docker-compose.yml
```
