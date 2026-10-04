# Release Packages and Debian Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GitHub Release source/deploy tarballs and a Debian deployment package while preserving the existing GHCR image deployment path.

**Architecture:** Keep the current tag-triggered release workflow and add focused packaging scripts under `scripts/release/`. The workflow builds images first, then creates local release assets in `dist/release/`, then uploads those assets to the GitHub Release.

**Tech Stack:** GitHub Actions, POSIX shell/Bash, `tar`, `dpkg-deb`, Docker Compose deployment files, Markdown documentation.

**Spec:** `docs/superpowers/specs/2026-10-04-release-packages-deb-design.md`

## Global Constraints

- Preserve the existing GHCR images as the canonical production runtime.
- Generate artifacts from the checked-out tag and the same `VERSION` used by image tags.
- Do not package `.env`, TLS private keys, certificates, database data, virtualenvs, `node_modules`, frontend `dist`, or test artifacts.
- The `.deb` installs a Docker Compose deployment skeleton under `/opt/quantbot`; it is not a native application package.
- Do not start or stop local Docker containers during implementation.
- Do not force-push or publish to any repository other than `https://github.com/lsgoodlionel/Quantitative-Trading-Platform.git`.

## Review Focus

- Existing server `.env` must not be overwritten by `.deb` upgrades; Task 2 adds a package-contents test that verifies only `.env.example` is shipped.
- TLS private material must not enter release assets; Task 1 adds a tarball-contents check for `certs`, `.key`, and `.pem` exclusions.
- Release assets must use the tag-derived version, not a hard-coded value; Task 3 verifies workflow `VERSION` propagation into packaging scripts.
- The `.deb` must be inspectable without installing it; Task 2 verifies `dpkg-deb --info` and `dpkg-deb --contents`.
- Documentation must not imply `.deb` replaces Docker; Task 4 documents it as a deployment skeleton for GHCR images.

---

## File Structure

- Create `scripts/release/build_artifacts.sh`: creates backend, frontend, and deploy tarballs in `dist/release/`.
- Create `scripts/release/build_deb.sh`: creates `quantbot-${VERSION}.deb` from the deploy skeleton.
- Modify `.github/workflows/release.yml`: runs packaging after image manifests and attaches generated files to tag Releases.
- Modify `README.md`: update visible version wording and mention Release assets.
- Modify `infra/README.md`: document direct compose pull, deploy tarball, and `.deb` deployment paths.
- Modify `HANDOFF.md`: record release packaging behavior and operational boundaries.
- Create `docs/superpowers/plans/2026-10-04-release-packages-deb.md`: this implementation plan.

## Task 1: Release Tarball Script

**Files:**
- Create: `scripts/release/build_artifacts.sh`
- Test by command: `bash -n scripts/release/build_artifacts.sh`
- Test by command: `scripts/release/build_artifacts.sh 4.0.0-test`

**Interfaces:**
- Consumes: repository root as current working directory; version as `$1`.
- Produces: `dist/release/quantbot-backend-${VERSION}.tar.gz`, `dist/release/quantbot-frontend-${VERSION}.tar.gz`, `dist/release/quantbot-deploy-${VERSION}.tar.gz`.

- [ ] **Step 1: Create `scripts/release/build_artifacts.sh`**

Implement a Bash script with this interface:

```bash
scripts/release/build_artifacts.sh VERSION
```

It must fail when `VERSION` is empty, create a clean `dist/release/` directory, and produce the three tarballs listed above.

- [ ] **Step 2: Exclude generated and sensitive files**

Backend tarball excludes `.venv`, `.pytest_cache`, `.ruff_cache`, `.coverage`, `__pycache__`, and `*.pyc`.

Frontend tarball excludes `node_modules`, `dist`, `playwright-report`, `test-results`, `.DS_Store`, and Vite/Vitest caches.

Deploy tarball includes `infra/docker-compose.prod.yml`, `infra/nginx/`, `.env.example`, and a generated `RELEASE-INSTALL.md`; it excludes `infra/nginx/certs`, `*.key`, `*.pem`, and `.env`.

- [ ] **Step 3: Run script syntax check**

Run: `bash -n scripts/release/build_artifacts.sh`

Expected: exits `0`.

- [ ] **Step 4: Run local tarball generation**

Run: `scripts/release/build_artifacts.sh 4.0.0-test`

Expected: exits `0` and writes exactly the three tarballs to `dist/release/`.

- [ ] **Step 5: Inspect tarball contents**

Run:

```bash
tar -tzf dist/release/quantbot-deploy-4.0.0-test.tar.gz
tar -tzf dist/release/quantbot-frontend-4.0.0-test.tar.gz | rg '(^|/)node_modules/|(^|/)dist/|playwright-report|test-results' && exit 1 || true
for file in dist/release/*.tar.gz; do tar -tzf "$file"; done | rg -P '(^|/)\\.env($|\\.(?!example$))|(^|/)certs/|\\.key$|\\.pem$' && exit 1 || true
```

Expected: deploy tarball lists install files; exclusion checks exit `0`.

- [ ] **Step 6: Commit Task 1**

```bash
git add scripts/release/build_artifacts.sh
git commit -m "ci: add release tarball packaging script"
```

## Task 2: Debian Package Script

**Files:**
- Create: `scripts/release/build_deb.sh`
- Test by command: `bash -n scripts/release/build_deb.sh`
- Test by command: `scripts/release/build_deb.sh 4.0.0-test`

**Interfaces:**
- Consumes: repository root as current working directory; version as `$1`; deployment files from the repository.
- Produces: `dist/release/quantbot-${VERSION}.deb`.

- [ ] **Step 1: Create `scripts/release/build_deb.sh`**

Implement a Bash script with this interface:

```bash
scripts/release/build_deb.sh VERSION
```

It must fail when `VERSION` is empty, build a temporary package root, and generate `dist/release/quantbot-${VERSION}.deb` with `dpkg-deb --build`.

- [ ] **Step 2: Define Debian metadata**

Package metadata:

- `Package: quantbot`
- `Version: ${VERSION}`
- `Architecture: all`
- `Maintainer: QuantBot Team`
- `Description: QuantBot Docker Compose deployment skeleton`

- [ ] **Step 3: Install package files under `/opt/quantbot`**

Package contents must include deployment files from Task 1's deploy skeleton: `infra/docker-compose.prod.yml`, `infra/nginx/`, `.env.example`, and `RELEASE-INSTALL.md`.

Do not include `/opt/quantbot/.env`.

- [ ] **Step 4: Add non-mutating post-install note**

Add `DEBIAN/postinst` that prints the next commands and does not run Docker, edit secrets, copy `.env`, or start services.

- [ ] **Step 5: Run script syntax check**

Run: `bash -n scripts/release/build_deb.sh`

Expected: exits `0`.

- [ ] **Step 6: Build local deb**

Run: `scripts/release/build_deb.sh 4.0.0-test`

Expected: exits `0` and writes `dist/release/quantbot-4.0.0-test.deb`.

- [ ] **Step 7: Inspect deb metadata and contents**

Run:

```bash
dpkg-deb --info dist/release/quantbot-4.0.0-test.deb
dpkg-deb --contents dist/release/quantbot-4.0.0-test.deb
dpkg-deb --contents dist/release/quantbot-4.0.0-test.deb | rg '/opt/quantbot/\\.env$' && exit 1 || true
```

Expected: metadata matches Step 2; contents include `/opt/quantbot/.env.example` and exclude `/opt/quantbot/.env`.

- [ ] **Step 8: Commit Task 2**

```bash
git add scripts/release/build_deb.sh
git commit -m "ci: add Debian deployment package script"
```

## Task 3: GitHub Release Workflow Wiring

**Files:**
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: scripts from Tasks 1 and 2.
- Produces: Release assets attached by `gh release create`.

- [ ] **Step 1: Add packaging job**

Add a `packages` job that needs `manifest`, checks out the repository, resolves `VERSION` the same way as the `manifest` job, runs:

```bash
scripts/release/build_artifacts.sh "$VERSION"
scripts/release/build_deb.sh "$VERSION"
```

Then uploads `dist/release/*` as a workflow artifact.

- [ ] **Step 2: Wire release job to packages**

Change the `release` job to need both `manifest` and `packages`, download the release assets, and call `gh release create` with `dist/release/*` appended after `--verify-tag`.

- [ ] **Step 3: Update Release notes**

Add an "Assets" section that lists:

- backend tarball;
- frontend tarball;
- deploy tarball;
- Debian package.

Keep existing image deployment commands and GHCR image names.

- [ ] **Step 4: Run workflow text checks**

Run:

```bash
rg -n "packages:|build_artifacts|build_deb|gh release create|dist/release" .github/workflows/release.yml
```

Expected: all new workflow integration points are present.

- [ ] **Step 5: Commit Task 3**

```bash
git add .github/workflows/release.yml
git commit -m "ci: attach release packages to GitHub Releases"
```

## Task 4: Documentation Updates

**Files:**
- Modify: `README.md`
- Modify: `infra/README.md`
- Modify: `HANDOFF.md`

**Interfaces:**
- Consumes: artifact names and behavior from Tasks 1-3.
- Produces: user-facing deployment documentation.

- [ ] **Step 1: Update `README.md` version and release overview**

Change the top visible version from `v2.0` to `v4.0` and add a short note that tag Releases publish GHCR images plus downloadable tarballs and `.deb`.

- [ ] **Step 2: Update `infra/README.md`**

Add sections for:

- direct `QB_VERSION` compose deployment;
- deploy tarball installation;
- `.deb` bootstrap installation;
- package safety boundaries.

State clearly that `.deb` installs a Docker Compose skeleton and still pulls GHCR images.

- [ ] **Step 3: Update `HANDOFF.md`**

Add a concise note under known deployment/operations guidance that Release assets are generated by tag workflow and do not include secrets or runtime data.

- [ ] **Step 4: Verify docs mention exact artifact names**

Run:

```bash
rg -n "quantbot-backend-|quantbot-frontend-|quantbot-deploy-|quantbot-.*\\.deb|QB_VERSION|GHCR" README.md infra/README.md HANDOFF.md
```

Expected: each document includes the relevant release/deployment information.

- [ ] **Step 5: Commit Task 4**

```bash
git add README.md infra/README.md HANDOFF.md
git commit -m "docs: document Release package deployment paths"
```

## Task 5: Final Verification and Synchronization

**Files:**
- Read/verify: `.github/workflows/release.yml`
- Read/verify: `scripts/release/`
- Read/verify: `README.md`, `infra/README.md`, `HANDOFF.md`
- Update: Obsidian project page after push

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified branch, GitHub push, Obsidian trace entry.

- [ ] **Step 1: Run full local packaging verification**

Run:

```bash
rm -rf dist/release
scripts/release/build_artifacts.sh 4.0.0-test
scripts/release/build_deb.sh 4.0.0-test
ls -la dist/release
dpkg-deb --info dist/release/quantbot-4.0.0-test.deb
dpkg-deb --contents dist/release/quantbot-4.0.0-test.deb
```

Expected: four assets exist and `.deb` is inspectable.

- [ ] **Step 2: Run non-runtime checks**

Run:

```bash
bash -n scripts/release/build_artifacts.sh
bash -n scripts/release/build_deb.sh
npm run type-check
```

Expected: all pass.

- [ ] **Step 3: Confirm no generated release artifacts are tracked**

Run:

```bash
git status --short
git ls-files dist/release
```

Expected: `dist/release` files are untracked or absent, and `git ls-files` prints nothing for generated assets.

- [ ] **Step 4: Make final cleanup commit if needed**

If any verification-driven doc/script fixes remain, commit only those files with:

```bash
git add <changed-files>
git commit -m "ci: finalize Release package publishing"
```

- [ ] **Step 5: Push branch**

Run:

```bash
git push origin feat/v4-engine-core-and-v3-wave-a
```

Expected: push succeeds. If authentication, branch protection, merge/rebase requirement, or unrelated dirty files block the push, stop and report.

- [ ] **Step 6: Update Obsidian project page**

Update the QuantBot Obsidian project progress page with date `2026-10-04`, branch, commits, verification commands, completed release packaging behavior, remaining release caveats, and next step.

- [ ] **Step 7: Final status report**

Report:

- commits pushed;
- files changed;
- verification results;
- Release behavior now available on next tag;
- any blocked or skipped checks.
