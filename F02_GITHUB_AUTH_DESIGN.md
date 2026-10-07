# F02 — GitHub App User Authorization Design

Date: 2026-10-07. Status: approved architecture; implementation NOT started.
Source checkout: `.audit-worktree`, `security/final-security-audit`, baseline
`f57dcd81ecf44956a3a97f22a20a4fea28867cff` plus the uncommitted Phase 6 fixes.

This document distinguishes verified current behavior from proposed behavior.
Every new table, field, route, service and environment-variable name below is a
design proposal, not something already implemented. No production configuration,
GitHub App registration or live token permissions have been verified. No existing
Phase 6 files are to be changed by this documentation task.

## 1. Current shared-token architecture

Verified source: `apps/api/app/core/config.py`, `api/routes/repositories.py`,
`services/github.py`, `services/branch_fixer.py`, `services/pr_commenter.py`,
`services/pr_reviewer.py`, `services/ingestion.py`, `models/user.py`,
`models/repository.py`, and `apps/web/lib/api.ts`.

`GITHUB_TOKEN` populates `settings.github_token`. `github.py` attaches that
server-wide credential to metadata, tree and source-content GET requests.
`branch_fixer.py` and `pr_commenter.py` accept an optional service-level token but
fall back to `settings.github_token`; the HTTP routes supply no caller-specific
credential. `pr_reviewer.py` currently makes unauthenticated GitHub reads.

The three GitHub mutation endpoints are:

| Existing CodeLens POST route | Remote side effects |
| --- | --- |
| `/repositories/{repository_id}/findings/{finding_id}/apply-fix` | Create a Git ref/branch, then commit file contents |
| `/repositories/{repository_id}/findings/{finding_id}/create-pr` | Create a pull request from a supplied existing branch |
| `/repositories/{repository_id}/pull-requests/{pull_request_number}/comment` | Post an issue-style PR timeline comment |

There is no GitHub merge endpoint. Repository deletion deletes CodeLens data,
not the GitHub repository. A user can create/connect a local repository record
with their CodeLens `user_id` without proving GitHub permission. Checks against
that row prevent CodeLens IDOR but do not authorize use of the shared token.
The actual shared token's permissions determine whether GitHub accepts a call.

Current users have password/Google identity fields, not GitHub connections.
`Repository.github_id` is a string populated with `full_name`, not a reliable
immutable numeric GitHub repository ID. Uniqueness is per CodeLens user.
The background scan currently receives only `repository_id` and opens a fresh
`SessionLocal`. Source files/findings/PR reviews/snapshots are stored locally.

## 2. Target GitHub App architecture

Keep password/Google login, CodeLens JWT cookies and existing `Repository.user_id`
ownership. GitHub connection is an additional authorization, not a new login or
email-based account merge. Initial scope is individual CodeLens accounts; team
tenancy is not implemented in the current repository and is not assumed here.

The authorization conjunction for every protected repository action is:

`CodeLens row ownership AND active caller connection AND verified repository/
installation binding AND current GitHub user access AND required operation
permission AND CodeLens operation policy`.

All user-triggered content reads, scans and mutations use that user's GitHub App
user access token. Version 1 has NO installation-token fallback for these paths.
App JWTs may authenticate installation-metadata reconciliation only; they are
not user credentials. Future autonomous installation-token automation requires
a separate explicit tenant/grant design and approval.

GitHub user tokens constrain operations to both the user's and App's authority;
installation and user authorization are distinct prerequisites. Installation
alone is not permission for every CodeLens user to operate on its repositories.
[GitHub authorization semantics](https://docs.github.com/en/apps/using-github-apps/authorizing-github-apps).

Proposed components: GitHub connection routes, single authorization resolver,
credential/refresh manager, constrained HTTP client, installation/webhook
reconciliation, and sanitized audit events. Services receive an authorized
context bound to actor, connection generation, installation, numeric repository
ID and operation—not an arbitrary token or caller-selected installation ID.
Token material stays out of repr, serialization, logs and error context.

## 3. User authorization flow

1. An already authenticated user requests GitHub connection through a CSRF-checked
   POST. Reconnection/account replacement requires recent CodeLens authentication.
2. Backend creates a random, single-use, server-expiring transaction (proposed
   ten-minute TTL), bound to CodeLens user, current session jti hash, browser
   transaction cookie and fixed callback/return destination. Store only the
   state's hash; store the PKCE verifier encrypted. Do not reuse Google state.
3. Navigate to GitHub's authorization endpoint with the App client ID, state,
   exact registered callback and S256 PKCE challenge. Request no classic OAuth
   `repo` scope. User authorization is a GitHub App flow.
4. GET callback requires the same logged-in CodeLens user/session and browser
   binding; atomically consume the transaction before exchanging the code.
   Reject missing, expired, replayed or mismatched state, including account swaps.
5. Exchange server-side using the verifier and App client authentication; validate
   token type/expiry response, then obtain the stable GitHub numeric user ID
   through authenticated `/user`. Never link by email or a submitted username.
6. Enforce connection uniqueness, encrypt credentials, discover accessible
   installations/repositories and return a fixed frontend success/status URL.
   Never put token material in redirects, JSON responses or frontend state.

Clear transaction cookies on every terminal path. Callback responses are
no-store; redact callback query parameters in application/proxy/access logs.
Scrub token-exchange HTTP tracing and APM capture. Interrupted flow requires a
new transaction; it must not attach credentials to a later logged-in user.
GitHub supports S256 PKCE for this flow.
[User-token generation](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app).

## 4. GitHub App installation flow

After connecting, list installations visible to the user using their token, then
repositories within the chosen installation. If none is suitable, provide a
server-constructed installation URL for the registered App. Prefer selected
repositories rather than all repositories. Organization installation requests
may need owner approval; show pending status, not a successful connection.

Enable a fixed setup callback with a separate browser-bound transaction. Treat
callback installation IDs and setup actions as hints, never proof of authority.
Re-fetch installation/repository membership with the caller token and match the
configured App ID before binding. A webhook announcing installation also cannot
link arbitrary CodeLens users to it. Authorization and installation may happen
in either order; enable repository actions only after both checks succeed.
[Installation behavior](https://docs.github.com/en/apps/using-github-apps/installing-a-github-app-from-a-third-party).

## 5. User access token lifecycle

Require expiring GitHub App user tokens. Store server-derived access/refresh
expiry timestamps and refresh before access expiry with a small clock-skew
margin. Do not rely on parsing token strings. The current documented defaults
are eight hours for access and six months for refresh; use returned lifetime
fields rather than hard-coded validity assumptions. Refresh replaces both tokens
and invalidates the previous pair. Expired refresh requires reauthorization.
[Refresh lifecycle](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens).

Connection states: active, reauthorization-required, disconnected and revoked.
Installation suspension/removal is tracked separately. A valid access token
does not imply permission to every repository/action. On upstream rejection,
classify credentials versus repository permission versus rate-limit/network
errors; do not refresh every 403 or mark a whole account revoked for one 404.

## 6. Token encryption/storage strategy

Proposed production policy: authenticated envelope encryption with AES-256-GCM
and a managed KMS wrapping key outside PostgreSQL. Use independent random nonces;
associated data binds connection ID, CodeLens user ID, GitHub user ID, field type
and encryption version. Persist ciphertext, authentication data/nonce, wrapped
data key and key/version identifiers—never plaintext credentials.

Only the backend credential manager may unwrap/decrypt, immediately before use.
No tokens in ORM repr, public schemas, exception causes exposed to callers, task
arguments, URLs, frontend bundles or telemetry. Use provider-supported secret
storage for App client credentials, signing private key and webhook secret.
Do not reuse AUTH_SECRET_KEY as an encryption key. Database encryption at rest
alone does not protect a database export. Do not claim protection from a fully
compromised authorized backend process or guaranteed Python memory zeroization.

Key rotation rewraps data keys or reencrypts through a bounded audited job; reads
can support a controlled previous-key window. KMS failure denies credentialed
actions. Protect backups and restore procedures; restored records must undergo
revocation/installation reconciliation before being used. The KMS provider and
runtime identity mechanism must be selected before implementation/deployment;
no particular live infrastructure is assumed.

## 7. Refresh-token handling

Use database-coordinated refresh across replicas, not process-local locks.
Serialize one refresh per connection with an expiring lease and generation/CAS
check; do not hold a database transaction across an unbounded HTTP request.
Only the winning refresh commits the newly encrypted pair atomically. Other
requests wait briefly and reload; they never reuse the consumed refresh token.

Disconnect/revoke increments authorization generation. A stale refresh response
cannot reactivate or overwrite that generation. Bound requests by timeout and
retry budget. If GitHub consumed the refresh token but a network/DB failure lost
the replacement pair, fail closed and require reauthorization rather than trying
alternate identities. Never retry an unknown-outcome mutation just because a
credential refresh succeeded. Test crash, lease expiry and cross-worker races.

## 8. Revocation/disconnect handling

Process signed `github_app_authorization` revocation events by stable GitHub
user ID. Also process installation deletion/suspension and repository selection
changes. Verify HMAC over raw bounded bytes, configured App identity/event shape,
and deduplicate delivery IDs. Reconcile out-of-order events against GitHub;
never reactivate access solely from an old webhook. User revocation produces
this authorization webhook; invalid credentials also fail at GitHub.
[Token/revocation behavior](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-with-a-github-app-on-behalf-of-a-user),
[webhook reference](https://docs.github.com/en/webhooks/webhook-events-and-payloads#github_app_authorization).

Disconnect first atomically disables the local connection, invalidates grants/
caches/leases and stops jobs, then attempts GitHub-side authorization revocation.
Use the documented App authorization revocation endpoint with server-side App
client authentication, not an installation token. If remote revocation fails,
retain only encrypted revocation material in a restricted expiring retry record,
never available to the ordinary credential resolver; notify the user to revoke
through GitHub settings. Purge ordinary credential ciphertext immediately.
[Authorization deletion](https://docs.github.com/en/rest/apps/oauth-applications#delete-an-app-authorization).

## 9. Repository-to-user authorization model

Preserve CodeLens ownership and per-user duplicate repository records. Add
immutable numeric GitHub repository ID, installation ID and the owner's
connection binding through verified association rows. Full name/URL are display
and routing metadata; refresh them on rename/transfer only after verifying the
numeric ID. Never automatically substitute a different repository at an old URL.

One active GitHub account per CodeLens account; a GitHub numeric account ID is
unique across active CodeLens connections in version 1. A collision requires an
explicit account-recovery/support flow, never auto-merge by email. Changing the
GitHub identity invalidates existing bindings and requires reconnection.

A grant records a verified access observation, not permanent GitHub authority.
For each remote operation, resolve live repository access with the user token;
write operations always perform a fresh check. GitHub remains the final action
permission authority (including comment permissions, not inferred just from
repository push rights). Check connection generation again before side effects.

For locally cached private content, revalidate user access before returning it or
submitting it to AI. Version 1 permits no positive authorization cache across
requests for these sensitive paths; deployment must measure GitHub quota/latency
and coalesce concurrent checks only within one request/job. A future cache TTL
requires an explicit, documented revocation-delay policy. Webhooks accelerate
invalidation but do not guarantee instantaneous knowledge of every membership
change. In-flight responses/mutations already authorized cannot be recalled.

## 10. Private repository read authorization

Connection discovery and metadata must use the caller credential, not global
credentials. Verify membership in `/user/installations/{installation_id}/repositories`
with bounded pagination, then verify the exact repository with the user token.
Require active installation selection and readable contents before ingesting.
Unauthorized discovery must not disclose private repository existence.

Use the same context for tree, file and PR reads; remove the current
unauthenticated PR-client inconsistency. Protect cached source, findings, AI
explanations/fixes/tests/Ask context, PR review detail/history and historical
dashboard data derived from private repositories. Filter lists by authorized
repositories; failures never return sensitive cached items. CodeLens repository
deletion and connection-management status may remain accessible to their local
owner without GitHub access so users can disconnect/delete their data.

Even a repository made public later is not automatically reclassified from an
untrusted request. Optional anonymous public browsing would be a separate,
read-only explicit path; it cannot enable shared-token private reads or writes.

## 11. Apply-fix authorization

Retain repository/finding ownership, nested-ID checks, source lookup, AI quotas
and unchanged-content rejection. Resolve caller connection and live repository
write capability BEFORE source retrieval/AI generation, then recheck immediately
before GitHub side effects. Use the same caller identity throughout.

Resolve current default-branch commit and file SHA; reject stale source/fix
preconditions rather than overwriting changed code. Allow only new validated
CodeLens fix branches, never updating the default branch or workflow files.
Use bounded generated content, path/ref validation and an idempotent operation
record. Record created branch/commit numeric repository identity and connection
generation. Partial branch-created/commit-failed outcomes are visible and audited;
do not delete remote branches automatically or repeat unknown-outcome writes.
Explicit user invocation remains required; no AI instruction grants authority.

## 12. Create-PR authorization

Retain the existing route and response fields. Check local owner/nested finding,
active caller connection, exact repository installation membership and current
operation permission. Require a recorded successful CodeLens fix operation for
this owner/repository/finding/head branch, not any supplied existing branch.
Verify current head commit and base/default branch with the caller token.

Use the caller token to create the PR; bound/sanitize title/body, deny unexpected
cross-repository heads in version 1, and reconcile duplicates/unknown outcomes
before retrying. Record PR number/result under the originating operation.
Never merge a PR or bypass branch protections. Stale/missing legacy branch
provenance requires a new verified fix flow, not automatic trust.

## 13. PR-comment authorization

Check local repository ownership, verified binding, live user access and
App permission to comment. Fetch the exact PR with the same context before
generating or posting; this avoids commenting on an unrelated issue number.
Comment authorization is operation-specific: lack of push access alone does
not prove the user cannot comment. GitHub's endpoint remains authoritative.

Post only on explicit caller request. Bound Markdown and prevent accidental
secret/source oversharing; enforce deduplication/idempotency for retries.
Existing static-analysis fallback may still produce a comment, but only inside
the same authorized context; fallback cannot bypass permission or quota errors.

## 14. Background scan authorization

Extend the existing `run_background_scan(repository_id)` contract to an immutable
job identity: initiating CodeLens user, repository/binding IDs, connection
generation and requested read-only operation. Persist these IDs/status in a job
record; put no credential material in task arguments. Existing BackgroundTasks
can initially dispatch it, but durable execution is a separate operational
upgrade, not an assumed current capability.

Re-resolve ownership, connection, installation and user access at execution,
before each remote batch and before committing/displaying results. Refresh through
the same coordinated manager. Disconnect, removal or changed generation cancels
remaining work; staged source is not promoted after access loss. Failed checks
release scan state and report a neutral actionable status. No installation-token
or other user's token fallback. Jobs initiated before logout may finish while
their explicit GitHub connection remains active; disconnect cancels them.

## 15. Organization/SAML considerations

An organization owner installing the App does not authorize every member's
CodeLens account. Each member must authorize the App; installation selection,
membership, role changes, organization restrictions and suspension are honored.
New App permissions may need installation-owner approval; an old installation
must not be assumed to have the new permissions.

SSO-protected organizations may need an active SAML session during authorization
or installation. If organization resources are missing, guide the user through
SSO and appropriate reauthorization rather than escalating to a service token.
Do not implement PAT-style SSO handling as if it were GitHub App authorization.
Enterprise-managed users and actual organization policies need sandbox validation
before support is claimed. Initial host scope is GitHub.com; GitHub Enterprise
Server/custom API hosts require a separately validated deployment design.
[SAML and GitHub Apps](https://docs.github.com/en/enterprise-cloud%40latest/apps/using-github-apps/saml-and-github-apps).

## 16. Required GitHub App permissions

| Permission | Proposed level | Reason |
| --- | --- | --- |
| Repository Metadata | Read | Identify repositories/installation association |
| Repository Contents | Read/write | Read tree/source; create fix refs and commit files |
| Repository Pull requests | Read/write | Read PR diff/source metadata; create PRs; post PR timeline comments |
| Repository Issues | Not requested initially | PR comment endpoint accepts Pull requests write as an alternative |
| Repository Workflows | Not requested | Workflow-file changes explicitly denied |
| Administration, organization Members, user Emails | Not requested initially | Not needed for the current feature set; no email identity linking |

Use least privilege and selected repositories. Subscription proposal:
`github_app_authorization`, `installation`, `installation_repositories` and
repository rename/transfer/delete events supported by the selected permissions;
verify event availability when registering. No automatic PR-review automation
is introduced solely by subscribing to events.
Endpoint requirements: [Git refs](https://docs.github.com/en/rest/git/refs#create-a-reference),
[file commits](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents),
[PR creation](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request),
[PR timeline comments](https://docs.github.com/en/rest/issues/comments#create-an-issue-comment).
Permission changes require reviewing existing installation approvals.
[App permissions](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app).

## 17. Required backend models/tables

All names below are proposed. Use BIGINT for GitHub numeric IDs, independent of
CodeLens integer primary keys; UTC timestamps, FK constraints and explicit indexes.

| Proposed table | Essential fields/invariants |
| --- | --- |
| `github_connections` | CodeLens user FK, GitHub user ID, display login, status, authorization generation, encrypted credential pair/envelope metadata, expiry, refresh lease/version; partial unique constraints prevent multiple active connections per CodeLens user or reuse of one GitHub identity across active CodeLens accounts; disconnected historical rows may remain credential-free |
| `github_oauth_transactions` | Hashed state, user/session/browser binding, encrypted PKCE verifier, purpose, expiry and consumed timestamp; atomic one-use |
| `github_installations` | Unique installation ID, App ID, stable account ID/type, status, granted permissions, repository selection and reconciliation timestamps; contains no user credential |
| `github_repository_bindings` | Unique local repository FK, owner connection FK, installation FK, numeric GitHub repository ID, status and last verified metadata; check owner consistency in resolver/transactions |
| `github_operations` | Actor, repository/binding, connection generation, operation/idempotency key, expected head SHA, fix branch/commit/PR/comment results and sanitized status; branch provenance and audit |
| `github_scan_jobs` | Actor/binding/generation, bounded parameters, lifecycle/cancellation status; no token fields |
| `github_webhook_deliveries` | Unique delivery ID, event type, validated subject IDs, processing/reconciliation status and bounded retention; do not retain raw sensitive payloads by default |
| `github_revocation_tasks` | Restricted encrypted remote-revocation material only when necessary, expiry/retry status; unavailable to data-operation resolver |

Use existing `Repository`, source/finding/review/snapshot relationships unchanged.
Local bindings preserve per-user duplicate GitHub repo records. Owner/connection
FK consistency and account collisions must be verified transactionally, not just
by frontend filtering. Use bounded retention for transactions/delivery/job logs.

## 18. Required migrations

Design only—no migration files are created here. Existing migration head is
`0207cb567c11`; confirm actual head again before a future migration is authored.

An additive migration series would create connection/transaction/installation/
binding tables, then operation/job/delivery/revocation tables and indexes.
Do not change `users.google_sub`, password nullability, repository ownership or
existing per-user uniqueness. Legacy repository rows start unverified; do not
backfill GitHub numeric IDs or access grants by trusting `github_id`/full_name.
Only authenticated GitHub verification may populate bindings. Preserve existing
source/history during schema rollout, but gate access until verified (section 24).

Test fresh/reused databases, constraints, transaction races, upgrade and safe
downgrade/rollback. A downgrade must not restore shared-token runtime behavior;
encrypted credentials must be purged safely rather than left orphaned. Obtain
separate implementation/migration authorization before writing any migration.

## 19. Required routes

Proposed additional routes (names subject to implementation review):

| Method / path | Purpose/security |
| --- | --- |
| `GET /github/connection` | Caller connection status/display identity only; no credentials |
| `POST /github/authorize` | Authenticated, recent-auth/Origin-checked transaction start; return fixed authorization navigation URL |
| `GET /github/callback` | Browser-bound one-use state + logged-in user verification; backend exchange, fixed redirect |
| `GET /github/installations` | Caller-visible verified installation list, bounded pagination |
| `GET /github/installations/{installation_id}/repositories` | Caller-visible App-selected repositories; never trust the requested installation ID alone |
| `POST /github/installations/start` | Create a bound installation-navigation transaction |
| `GET /github/installations/callback` | Treat setup parameters as untrusted; verify through GitHub |
| `DELETE /github/connection` | CSRF-checked disable/cancel/purge and remote-revocation attempt |
| `POST /github/webhooks` | No CodeLens session required; strict webhook signature/body/delivery validation |

Existing connection/metadata, scan/ingest, PR reads/AI and three mutation routes
keep their paths and successful response shapes but require the resolver.
Protect cached-data paths in `ask.py`, `code_retriever.py`, `pr_reviews.py` and
`dashboard.py` as well. Apply Phase 6 limits/headers to new routes; define separate
bounded webhook abuse controls without relying on session-based limiting.
Use neutral codes such as `GITHUB_CONNECTION_REQUIRED`,
`GITHUB_REAUTHORIZATION_REQUIRED`, `GITHUB_INSTALLATION_REQUIRED`,
`GITHUB_ACCESS_DENIED`, and `GITHUB_UNAVAILABLE`; retain 401 for missing CodeLens
authentication, avoid private-resource enumeration, and preserve quota semantics.

## 20. Required frontend changes

Extend `apps/web/lib/api.ts` with status, authorization, installation discovery
and disconnect methods/types. Update `apps/web/app/dashboard/page.tsx` with a
GitHub connection panel, verified repository chooser, pending installation/
SSO/reconnection states and operation-capability controls. A proposed dedicated
GitHub settings page can keep that large existing dashboard from growing further.

Use backend-generated navigation URLs; callback returns fixed status, not
credentials. No GitHub tokens in React context, localStorage, sessionStorage,
NEXT_PUBLIC variables or URL fragments. Preserve `lib/auth-context.tsx` and
password/Google login semantics. Disabled buttons improve UX but are not policy
enforcement. Handle neutral new error codes without infinite retries. Explicit
disconnect confirmation explains cached-data locking and scan cancellation.

## 21. Required service-layer changes

| Existing file/area | Proposed change |
| --- | --- |
| `app/core/config.py` | App/KMS secret-reference configuration and startup validation; remove runtime shared token |
| `app/main.py`, `app/core/rate_limit.py` | Register new routes and specific bounded limits; preserve Phase 6 middleware |
| `app/api/routes/repositories.py` | Resolve actor/repository operation context; capture background actor; provenance checks |
| `app/services/github.py` | Explicit authorized context for metadata/tree/source; bounded pagination and constrained HTTP requests |
| `app/services/ingestion.py` | Carry the same context for every fetch and guarded result promotion |
| `app/services/pr_reviewer.py` | Caller-authenticated PR metadata/files/content reads, retaining host validation |
| `app/services/branch_fixer.py` | Remove global fallback; enforce authorized context, branch provenance/preconditions and idempotency |
| `app/services/pr_commenter.py` | Remove global fallback; authorization before analysis/comment; same-context fallback |
| `app/services/scan_progress.py` | Integrate guarded job cancellation/status; retain atomic Phase 6 claim behavior |
| `app/api/routes/ask.py`, `services/code_retriever.py`, `api/routes/pr_reviews.py`, `api/routes/dashboard.py` | Gate/filter cached private content before reads/AI dispatch |
| `app/schemas/repository.py`, relevant schemas/model registration | Public connection/capability statuses, never credentials |

Proposed new modules: GitHub App flow client, credential/encryption/refresh
manager, repository authorization resolver, installation/webhook service and
sanitized operation recorder. Centralize GitHub HTTP calls with fixed allowed
hosts, safe redirect policy, bounded body sizes, encoding and timeouts.
App identity/JWT operations are segregated from user-content operations.
AI providers/analyzers need no credential awareness; preserve existing quotas.
No current Phase 6 fix depends on implementing F02.

## 22. Required tests

Tests must use synthetic credentials/mocked GitHub and a disposable database;
never log token responses or use production repositories. Required regressions:

- OAuth state/session/browser binding, TTL, denial/cleanup, callback replay,
  PKCE, account collision and malicious redirect/install-ID substitution.
- Encryption integrity, wrong associated data/key/version, repr/serialization/
  validation/log secrecy, KMS outage and rotation/backup restoration.
- Expiring token refresh, single-use rotation, simultaneous replicas, timeout
  after remote consumption, stale lease and disconnect-versus-refresh races.
- Two CodeLens users; token belongs to correct actor; private reads and writes
  denied when only the other user or installation can access the repository.
- All three mutation routes; operation-specific rights, stale SHA, unrelated
  branch, workflow paths, branch protection, duplicate/unknown-outcome handling.
- Private source/findings/Ask/explanations/fixes/tests/review history/trend do
  not leak after disconnect or permission loss; metadata/list enumeration checks.
- Scan actor/generation persistence, permission removal mid-job, cancellation
  and no result promotion or fallback after access loss.
- Signed webhook validation, bad signatures, replay/deduplication, wrong App,
  out-of-order removal/suspension/reinstatement and bounded body/retention.
- Organization selected repositories, changed permissions, SSO required,
  revoked membership and transfer/rename with immutable IDs.
- Every missing/expired/revoked user-credential path fails closed even when
  legacy GITHUB_TOKEN or an installation credential is configured.
- Migration constraints/backfill safety, old repository response compatibility,
  frontend connection/error/disconnect UX and all Phase 1–6 regressions.

Future release validation: complete backend pytest on CI's supported runtime,
TypeScript validation, frontend build, dependency/secret/diff checks and a
separately approved sandbox GitHub App end-to-end test. Documentation creation
does not rerun or certify the previously recorded Phase 6 test result.

## 23. Removing existing GITHUB_TOKEN fallback

Replace every `settings.github_token` read and `token or settings.github_token`
path with required authorized user context. Replace unauthenticated PR reads;
reject missing context before network access. Low-level clients must not accept
caller-controlled tokens from request JSON or silently substitute credentials.

Remove shared-token guards in apply-fix/create-PR and global setting/config
references in API Compose/CI/development documentation when future cutover is
authorized. Treat a still-present runtime GITHUB_TOKEN as a deprecated unused
variable with a value-free warning; never read it for SaaS operations. Removal
of a deployed legacy credential is a separately approved operator action, not
something this design task does. GitHub Actions' platform token is unrelated;
do not break CI by indiscriminately removing platform-provided token usage.

## 24. Backward compatibility/migration strategy

1. Implement/test additive schema and App flows in a future approved branch.
2. Register/test an App and secret/KMS setup in an approved sandbox; no live
   deployment or secret change is authorized by this document.
3. Disable legacy privileged GitHub operations at cutover BEFORE enabling public
   SaaS onboarding; there must be no period of dual unsafe fallback behavior.
4. Existing rows remain owned by their existing CodeLens users but are marked
   unverified. Reconnect through GitHub, then bind only after numeric-ID and
   permission verification. Rows whose legacy target cannot be verified remain
   locked; no blanket conversion of local ownership into GitHub authority.
5. Keep source/history physically retained during an explicitly approved
   migration/retention window, hidden from retrieval/AI until authorization is
   verified; allow owner deletion and neutral reconnection/status information.
   Restrict metadata that itself reveals formerly private repository identity.
6. Preserve successful route response shapes and frontend auth. New neutral
   connection-required failures are intentional compatibility changes. Old
   branches have no trusted fix provenance; do not automatically authorize PRs
   for them. Deploy backend/frontend compatibility together or gate safely.
7. Rollback fails closed/temporarily disables GitHub integration; NEVER restore
   shared-token delegation. Operator rotates/removes the legacy credential only
   after confirming all runtime consumers were intentionally migrated.

No existing data is automatically deleted or migrated by this document. Any
retention/purge/backfill job needs explicit approved scope and recovery review.

## 25. Security failure cases

| Failure | Required outcome |
| --- | --- |
| Caller owns local row but lacks GitHub access | Deny before private read/AI/write; no service-token substitution |
| Wrong connection/installation/repository or renamed URL now identifies another repo | Deny numeric-ID mismatch; require verified binding |
| Expired/revoked token, refresh unavailable | Bounded refresh or reauthorization; no stale cache success |
| KMS/DB/GitHub outage | Neutral temporary failure, no private cached fallback; bounded retries |
| Organization suspension, repository deselection, member removal | Invalidate known grants/jobs, live checks deny further use |
| Callback tampering/replay/session swap | Reject and clear transaction; no account merge or credential overwrite |
| Invalid/replayed webhook | Reject/deduplicate; no authorization changes from unverified payload |
| Refresh overlaps disconnect | Generation fence prevents reactivation |
| Write accepted remotely but response lost | Reconcile operation; no automatic duplicate mutation |
| Access changes during a job/mutation | Stop at next check; report partial outcome; never claim retroactive rollback |
| User supplies workflow/default-branch/traversal/unknown head | Deny explicit branch/path/provenance policy |
| Prompt injection requests different repository/identity/action | AI never selects credentials or expands authorization |

Keep rate/resource/AI/body/CSRF protections independent of GitHub grants. Redact
secrets in provider responses/errors/telemetry and sanitize external diagnostics.
The App does not itself fix all other remaining Phase 6 deployment/resource risks.

## 26. Logout, disconnect and revocation behavior

CodeLens logout clears the CodeLens session only; it is NOT GitHub disconnect or
GitHub App uninstall. Explicit authorized queued scans may complete after logout,
but no new action is permitted without CodeLens authentication. Current stateless
CodeLens JWT revocation limitations remain; this design does not rebuild sessions.

GitHub disconnect disables local use immediately, cancels jobs, locks cached
private-derived views and initiates remote authorization revocation. It does
not uninstall an organization-wide App or affect other users' independent
connections. Reconnection revalidates identities/repositories; it does not revive
stale queued operations. GitHub-side user revocation disables that connection;
App uninstall/suspension or repository removal affects relevant bindings across
users. Locally known events take effect immediately; remote discovery delays
and in-flight work cannot be represented as instantaneous revocation guarantees.

Account deletion must first disconnect/revoke safely, then apply the approved
CodeLens data-retention/deletion policy. Removing the local repository row does
not delete remote branches/PRs/comments. Do not uninstall an App just because a
single CodeLens user logs out, disconnects or deletes their account.

## 27. Deployment environment variables — names only

Existing names relevant to this design:

```text
DATABASE_URL
AUTH_SECRET_KEY
AUTH_COOKIE_NAME
AUTH_COOKIE_SECURE
AUTH_COOKIE_SAMESITE
AUTH_TOKEN_EXPIRE_MINUTES
FRONTEND_URL
NEXT_PUBLIC_API_URL
GITHUB_TOKEN
```

Proposed GitHub App names (not currently implemented):

```text
GITHUB_APP_ID
GITHUB_APP_SLUG
GITHUB_APP_CLIENT_ID
GITHUB_APP_CLIENT_SECRET
GITHUB_APP_PRIVATE_KEY_SECRET_REF
GITHUB_APP_WEBHOOK_SECRET
GITHUB_APP_REDIRECT_URI
GITHUB_APP_SETUP_REDIRECT_URI
GITHUB_API_VERSION
GITHUB_TOKEN_KMS_KEY_ID
GITHUB_TOKEN_ENCRYPTION_VERSION
```

GITHUB_TOKEN is listed for removal, not target SaaS authorization. Only
NEXT_PUBLIC_API_URL is intended for client exposure. App secret/private-key
material and encryption credentials must remain server-side. Cloud-specific KMS
identity variable names depend on the provider selected; do not invent a current
provider configuration. Prefer workload identity to stored cloud access keys.
No values, token examples, connection strings or credential material are included.

## Design review / implementation gate

This is documentation only. F02 remains unimplemented and the Phase 6 release
blocker remains open. Implementation requires a separate user instruction.
Before coding, confirm KMS/runtime identity, App registration/permission settings,
retention window, sandbox organization coverage and authorization-check quota
budget. These are deployment/product decisions, not verified live facts.
No migrations, credentials, production code, Phase 6 fixes, commits, pushes,
merges or deployments are created/changed by this document.
