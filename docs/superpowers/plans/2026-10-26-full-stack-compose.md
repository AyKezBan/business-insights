# Full Stack Compose Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Один проверяемый локальный запуск существующего стека без изменения чужих данных.

**Architecture:** Root Compose starts PostgreSQL → read-only CSV import → Flyway/backend; ML readiness gates backend, frontend nginx proxies Java and material files. Separate test overlay injects a labelled scripted ML client into real ML application.

**Tech Stack:** Docker Compose v2.24+, PostgreSQL 17, Java 21/Maven, Python 3.12, nginx.

**Spec:** docs/superpowers/specs/2026-10-26-full-stack-compose-design.md

## Global Constraints
- Только отдельный worktree/ветка; no reset, public deployment or merge to shared branches.
- CSV/PDF read-only; no implicit replace-current or volume deletion.
- No live LLM spending; scripted smoke always labelled.
- Existing materials orchestration belongs to another chat and stays unchanged.

## Review Focus
- Missing/changed CSV: import fails, no replacement; repeat identical input unchanged.
- Proxy methods and Origin: same-origin mutations; no bearer in browser bundle.
- Service restart: DNS refresh and stored results survive; ML question context limitation documented.
- Missing corpus/key: visible limitation, no silently substituted demo responses.
- Smoke cleanup: only uniquely named disposable project and its volumes.

### Task 1: Containers and lifecycle
Files: compose.yaml, .env.example, stack, backend-api/Dockerfile, data-import/Dockerfile, infra/frontend.Dockerfile, infra/nginx/*, infra/compose.materials.yaml.
Consumes: importer CLI DATABASE_URL/--data-dir; backend DB_JDBC_URL/ML_BASE_URL; ML /health.
Produces: http://127.0.0.1:8088 with static UI, /api/v1 and /v1/materials proxy.
- [x] Verify initial root compose config fails because file absent.
- [x] Add builds, dependency health checks, read-only binds, project volumes, explicit materials jobs and root wrapper.
- [x] Validate compose config and build all images.

### Task 2: Concrete UI bindings
Files: frontend/web/backend-bindings.js, related integration config/UI corrections, frontend/tests/test_backend_bindings.cjs.
Consumes: Java ApiModels/InsightMapper DTOs.
Produces: seven bindings bootstrap/feed/insight/clarify/preferences/savePreference/opened.
- [x] Write and run failing behavior assertions against actual DTO shapes.
- [x] Implement adapter without synthetic content or automatic paid calls.
- [x] Run existing frontend suites and new adapter assertions.

### Task 3: Disposable integration verification and handoff
Files: infra/smoke/*, infra/compose.smoke.yaml, infra/README.md, README.md, infra/VERIFICATION.md.
Consumes: real service images and seven frontend bindings.
Produces: repeatable synthetic smoke command and documented normal operation.
- [x] Generate temporary synthetic CSV and explicit ScriptedClient app outside production runtime.
- [x] Build and run separate project; assert session/profile/analysis/feed/insight/Q&A/state/feedback/trace.
- [x] Repeat import/up, restart and compare persisted record; reject changed dataset without replace.
- [x] Review full diff independently, fix material issues, record exact results/limitations, commit isolated branch.

## Execution outcome

Completed and independently reviewed; exact evidence and limitations in infra/VERIFICATION.md. Existing authorization requested implementation, so no additional planning permission gate was introduced. Frontend adapter and smoke fixture implementation delegated in parallel; infrastructure integrated in this worktree. No changes to ML materials orchestration.
