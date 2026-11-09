# Validation and rollout notes

## Automated coverage

`backend/tests/test_domain.py` names BR-01…BR-24 directly. Tests cover quantity identities using Decimal, state permissions, next/final node logic, temporary operations, route-free mode, QC, defect allocation, equipment eligibility, reason restrictions, employee identity, audit/outbox decisions, cancellation disposition and optimistic version rejection.

`backend/tests/test_integration.py` initializes the **original** `schema.sql` and `seed_demo.sql`, applies `002_runtime.sql` and exercises FastAPI over the database. Coverage includes a complete seven-station route, idempotency replay, stale writes, rollback of an invalid checkout, multi-card breakdown rollback, all cancellation dispositions, defect reversal, processed-defect blocking, repeated reversal, quantity-change guards, release policy, skip authorization consumption, one-shot employee sessions, equipment history, backfill time checks, RBAC, notification targeting/repeats/escalation, durable pending outbox events, XLSX validation/commit, and route-version preservation.

The integration tests DROP and recreate `public`; run them only on an expendable database through `TEST_DATABASE_URL`. They are skipped when that variable is not supplied.

## Verification in the build environment

On 2026-11-09: **75 tests passed** (52 domain cases and 23 database/API cases); production frontend dependency audit reported zero known vulnerabilities.

- Python business-rule and API/database tests executed using PGlite's PostgreSQL engine and socket adapter because a standalone PostgreSQL service/Docker was unavailable. This verifies SQL/schema/API behavior but is **not** proof of production concurrency or Docker startup.
- TypeScript typecheck and optimized Vite production build executed successfully.
- Browser preview verified the login layout and Vietnamese/Traditional Chinese switch. The preview host isolates the frontend from the Python/PostgreSQL runtime; authenticated screens, camera access, physical scanner behavior and installed-PWA behavior still require browser/device acceptance testing with Docker Compose.
- GitHub Actions is configured to rerun the same backend suite against standalone PostgreSQL 16 and to typecheck/build the frontend and validate the Compose configuration.

## Deliberate boundaries

- This is an initial implementation for demonstration and acceptance testing, not a claim of production certification.
- No real customer data, ERP endpoint or SMTP recipients are configured. Outbox/email data is durable and visible; unconfigured delivery is not reported as sent.
- Offline PWA caches the application shell. Offline write queues (OPT-13), multi-record rollback preview (OPT-05), phone QR/OTP delegation (OPT-06), OEE/shift reporting (OPT-09), configurable child-number policies (OPT-12), and line-scoped permissions (OPT-14) remain backlog items.
- PWA installation and camera use require HTTPS on tablets; localhost is a development exception. Deploy behind an HTTPS reverse proxy before tablet acceptance testing.
- Operator employee numbers are signatures within a trusted device session, matching the specification. Provision `DEVICE_TOKEN` only on authorized devices. A stronger device enrollment/rotation protocol is a future hardening step.
- The initial command service uses a global transaction advisory lock for correctness. Benchmark real workloads before changing to finer-grained ordered aggregate locks.
- Terminal cards are read-only according to document 02; reversal is available only in WAITING, IN_PROGRESS, QUALITY_HOLD and ABNORMAL. A later split, scrap, rework or transfer blocks reversal if restoring the snapshot would invalidate quantity accounting.
- Preserve the original BR document as the source spec; the user-approved quantity extension and release-policy resolution are recorded in `01-architecture.md` and enforced by the runtime migration.

## Acceptance walkthrough

1. Start Compose and open `/admin`. Log in with `admin` and the password from `.env`. Confirm three seeded cards, seven stations and six machines.
2. Open `/operator`, use employee `OP001` and the device token from `.env`. Scan `RC:DEMO-RC-0001`, start with `EQ:DEMO-PRT01`.
3. Checkout with output 95, defect `DEMO-D01` quantity 5, QC 125. Verify parent moves to SMT, child `-D1` is DEFECT_HOLD, quantity balance is unchanged and equipment history ends in US_4.
4. In admin, revoke that work with a reason. Confirm the parent returns to printing with 100, the work record remains soft-deleted and the defect card is cancelled with a linked return movement.
5. Start two cards on printing equipment. Scan the equipment, choose report breakdown, enter DU_1 and a reason. Verify both cards return to WAITING at their current node and the equipment enters MAINTENANCE atomically.
6. Test checkout QC outside 100–150; confirm QUALITY_HOLD, operator cannot continue, and QA can release using the configured reason policy.
7. Cancel a waiting card with each disposition in a fresh demo database. Inspect the ledger, audit and outbox; transfer must produce a linked successor card.
8. Test simultaneous actions from two browser tabs. Only the first matching version should commit; the stale tab must refresh and review.
9. Check both languages, tablet touch sizes, keyboard scanner Enter behavior, physical camera permission and an installed PWA over HTTPS.
