# Security model

Career Quest handles career-development data that can influence how employees perceive their future in a company. The demo therefore treats authorization, integrity and explainability as product requirements rather than presentation-only features.

## Scope and assets

Protected assets:

- employee identity, role, grade, tenure, skill gaps and participation history;
- HR aggregates and identifiable watchlist;
- integrity of skills, readiness, activities and audit history;
- uploaded jury/company data and session credentials.

Relevant actors:

- anonymous visitor or cross-site page;
- authenticated employee attempting horizontal privilege escalation;
- HR user with broader read access;
- malicious or malformed uploaded dataset;
- concurrent clients changing shared demo state.

Trust boundaries:

1. Browser ↔ HTTP server.
2. Authenticated principal ↔ `employee_id` object.
3. Uploaded file ↔ recommendation engine.
4. Demo identity adapter ↔ future Halyk OIDC/SSO.

## Agent governance boundary

The proposed Halyk pilot is described as three coordinated logical agents: Career Agent, Retention Agent and HR Copilot. In this MVP they are functions over one deterministic, explainable engine, **not** independent service identities and not autonomous LLM decision-makers.

- Career Agent may recommend development steps but cannot grant promotion or change employment status.
- Retention Agent may surface factual stagnation signals but must not label loyalty, predict resignation as fact, or trigger adverse action.
- HR Copilot may prioritize a private review queue but every intervention requires an authorized human decision.
- Retail and SME career graphs, thresholds and turnover hypotheses are synthetic pilot assumptions until approved and calibrated by Halyk HR and business owners.
- Production must preserve the source facts, score breakdown, model/rule version and human disposition for contestability and audit.

## Authorization policy

- Deny by default.
- Anonymous users can access only static login assets, configuration metadata, health and login.
- Employee identity is bound to `E0028` in the server-side session. A client-supplied different ID is rejected and audited.
- Employees can confirm only their own currently eligible recommendation.
- HR can read the directory, profiles and aggregates, but cannot confirm employee activities.
- Upload, reset, security telemetry and audit are HR-only in this demo.
- UI visibility is convenience only; every rule is enforced again at the API boundary.

## Implemented controls

### Authentication and sessions

- `PBKDF2-HMAC-SHA256`, random 16-byte salt, 260,000 iterations.
- Constant-time hash comparison and dummy hashing for unknown usernames.
- Opaque 256-bit server-side session token.
- One live session per demo principal: a new login revokes the previous token; logout revokes the current token.
- Absolute, non-sliding in-memory session lifetime (`CQ_SESSION_TTL`, two hours by default).
- `HttpOnly; SameSite=Strict; Path=/` cookie; `Secure` when HTTPS mode is configured.
- Session-bound random CSRF token kept only in JavaScript memory.
- Cross-tab auth changes are broadcast immediately; focus/visibility also revalidates `/api/me`, and revoked/expired sessions scrub private DOM state.
- Per client+username and aggregate per-client failed-login windows, concurrent hash limits and `429 Retry-After` response.
- Repeated rate-limit audit events are coalesced so an attack cannot trivially evict the rest of the 200-entry demo trail.

### Request boundary

- Exact JSON content type for mutations.
- A foreign `Origin` is rejected whenever that header is present; a valid session-bound CSRF token is required on **every** authenticated POST, including requests without an `Origin` header.
- Endpoint-specific body limits, an 8 MB request ceiling, plus 8-file / 4 MB per-file / 6 MB aggregate content limits.
- Duplicate JSON keys and non-finite numbers are rejected.
- Explicit methods and JSON `404` for unknown API routes.
- Generic server errors without traceback, file paths or exception text.
- Request socket timeout and bounded in-memory audit/rate-limit collections.

### Authorization and privacy

- Server-side RBAC on each data endpoint.
- Object-level ownership check prevents IDOR/BOLA.
- Profile DTO uses allowlisted fields; uploaded salary, phone, password or `auth_role` fields cannot be reflected or grant access.
- Authentication records are stored separately from uploaded business data.
- HR activity completion is rejected to preserve employee-history integrity.

### Data integrity

- IDs, strings, types, counts, skill ranges, dates and foreign keys are validated.
- Unknown/duplicate bundle sections, duplicate JSON keys and duplicate IDs are rejected server-side.
- Upload is validated in a staging engine and committed all-or-nothing.
- Shared engine reads and writes use a re-entrant lock.
- Completion checks role audience, current recommendation and previous completion.
- Critical mutations and denied actions are recorded in the audit trail without credentials or full payloads.

### Browser hardening

All successful and error responses include:

- Content Security Policy;
- `X-Content-Type-Options: nosniff`;
- `X-Frame-Options: DENY` and CSP `frame-ancestors 'none'`;
- `Referrer-Policy: no-referrer`;
- restrictive Permissions Policy;
- COOP and CORP same-origin;
- HSTS when HTTPS is configured.

Only `index.html`, `styles.css` and `app.js` are web-accessible. Source code, tests, README and sample datasets return `404`.

## Security-relevant configuration

| Variable | Purpose |
|---|---|
| `CQ_DEMO_MODE` | `1` enables documented synthetic demo accounts; use `0` outside demo |
| `CQ_EMPLOYEE_PASSWORD` | overrides employee demo password |
| `CQ_HR_PASSWORD` | overrides HR demo password |
| `CQ_SESSION_TTL` | absolute in-memory session lifetime in seconds |
| `CQ_MAX_LOGIN_FAILURES` | failures before temporary rate limit |
| `CQ_MAX_CLIENT_FAILURES` | aggregate failures per client before temporary rate limit |
| `CQ_LOGIN_WINDOW` | failed-attempt window in seconds |
| `CQ_LOGIN_LOCK` | retry interval reported after limit |
| `CQ_COOKIE_SECURE` | `1` adds cookie `Secure`; requires HTTPS |
| `CQ_TRUST_PROXY` | trust forwarding headers only behind a controlled reverse proxy |

The repository intentionally documents two public credentials for the **local synthetic demo**. Quick-login buttons appear only when demo mode is enabled and neither password override is set. Password overrides are all-or-nothing: setting only one role is rejected at startup so the other role cannot silently retain a public default. Those credentials are not secrets and the default server must not be exposed to the internet. With `CQ_DEMO_MODE=0`, both passwords must be provided explicitly, must be different and 12–128 characters long, and the shipped demo values are rejected. Do not commit real passwords or personal data. In production, remove local accounts entirely and derive identity/role claims from Halyk OIDC/SSO with MFA.

## Known demo limitations

This repository is a hackathon MVP, not a production banking service:

- sessions, audit, rate limits and business data live in one process and disappear on restart;
- `ThreadingHTTPServer` is not an internet-facing application server and has no distributed rate limiting;
- demo roles are coarse; production should separate HR reader, dataset administrator and auditor;
- the three agent roles are logical modules in one process, not separately authenticated or sandboxed services;
- HR can still read identifiable profiles, so production requires department/tenant scope and purpose-based access;
- activity completion is self-reported; production should create `pending verification` and accept confirmation from LMS/HRIS;
- CSP currently allows inline styles because progress bars use dynamic style values; scripts remain self-only;
- local HTTP intentionally omits the cookie `Secure` flag so the demo works; public deployment must use HTTPS and `CQ_COOKIE_SECURE=1`;
- audit is visible for the demo but is not immutable or exported to a SIEM;
- application-layer limits do not replace reverse-proxy connection, request and slow-client controls.

## Production deployment checklist

- [ ] Replace demo auth with Halyk OIDC/SSO, MFA and short-lived claims.
- [ ] Split HR reader, data administrator and security auditor roles.
- [ ] Deploy behind TLS reverse proxy/WAF; enable HSTS and secure cookies.
- [ ] Store secrets in Vault/KMS; rotate and monitor them.
- [ ] Move data, sessions and rate limits to durable scoped stores.
- [ ] Export append-only audit events to SIEM with alerting.
- [ ] Add tenant/department scoping and row-level access policies.
- [ ] Perform DPIA, define lawful purpose, retention, deletion and correction workflows.
- [ ] Verify LMS/HRIS events instead of trusting self-reported completion.
- [ ] Add SAST, dependency/container scanning, DAST and an external penetration test.
- [ ] Run bias/fairness evaluation and require human review for any HR action.
- [ ] Obtain formal HR approval for retail/SME role graphs, stagnation signals, thresholds and allowed intervention playbooks.

## Verification

Run:

```bash
python3 -m unittest discover -s tests -v
```

The integration suite starts the real HTTP handler and checks authentication, cookies, logout, RBAC, IDOR, CSRF, Origin, content type, brute-force limiting, security headers, static-file allowlisting, idempotency, field minimization and atomic uploads.

## Reporting a vulnerability

For this hackathon repository, open a private report to the team and include the affected route, role, reproduction steps, expected behaviour and impact. Do not include real employee data, passwords, session cookies or CSRF tokens in public issues.
