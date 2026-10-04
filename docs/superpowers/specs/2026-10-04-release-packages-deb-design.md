# GitHub Release Packages and Debian Package Design

## Context

QuantBot already publishes production container images through GitHub Actions:

- Tags matching `v*` trigger `.github/workflows/release.yml`.
- The workflow builds backend and frontend production images for `linux/amd64` and `linux/arm64`.
- Images are pushed to GHCR as:
  - `ghcr.io/lsgoodlionel/quantbot-backend:${VERSION}`
  - `ghcr.io/lsgoodlionel/quantbot-frontend:${VERSION}`
- `infra/docker-compose.prod.yml` deploys by pulling those versioned images through `QB_VERSION`.

The requested change is to keep that image-based release path and add downloadable GitHub Release assets: frontend package, backend package, deploy bundle, and a Debian package.

## Goals

1. Generate release artifacts from GitHub Releases, not from the deployment host.
2. Preserve the existing GHCR image workflow as the canonical production runtime.
3. Attach deterministic, versioned artifacts to every tag release.
4. Provide a `.deb` package that installs the deployment skeleton without embedding secrets or runtime data.
5. Document both supported deployment paths: direct Docker Compose pull and Release asset based installation.

## Non-Goals

- Do not replace Docker Compose with a native systemd service.
- Do not package PostgreSQL, Redis, certificates, database data, or user secrets.
- Do not change application runtime behavior, API contracts, database schemas, trading logic, or frontend routes.
- Do not start or stop local Docker containers as part of this work.
- Do not force-push or publish to any repository other than the registered QuantBot origin.

## Release Artifacts

For tag `vX.Y.Z`, GitHub Release should include:

| Artifact | Contents | Intended Use |
|---|---|---|
| `quantbot-backend-X.Y.Z.tar.gz` | backend source needed to build/run backend image context, excluding caches and virtualenvs | audit, offline inspection, emergency rebuild |
| `quantbot-frontend-X.Y.Z.tar.gz` | frontend source needed to build/run frontend image context, excluding `node_modules`, `dist`, and test artifacts | audit, offline inspection, emergency rebuild |
| `quantbot-deploy-X.Y.Z.tar.gz` | production deployment files: `infra/docker-compose.prod.yml`, `infra/nginx/`, `.env.example`, release install notes | server deployment skeleton |
| `quantbot-X.Y.Z.deb` | Debian package that installs the deployment skeleton under `/opt/quantbot` | apt/dpkg based deployment bootstrap |

The runtime path remains container based: the deploy bundle and `.deb` both reference GHCR images through `QB_VERSION`.

## Debian Package Behavior

The `.deb` package should:

- Install files under `/opt/quantbot`.
- Include production compose files, nginx config templates, `.env.example`, and a concise deployment README.
- Avoid shipping `.env`, TLS private keys, certificates, database data, or generated frontend/backend dependency directories.
- Leave existing `/opt/quantbot/.env` untouched during upgrades.
- Print a post-install note explaining:
  - set `QB_VERSION=X.Y.Z`;
  - copy/edit `.env.example` to `.env` if missing;
  - place TLS certificates outside the package-managed files;
  - run `docker compose -f /opt/quantbot/infra/docker-compose.prod.yml pull`;
  - run `docker compose -f /opt/quantbot/infra/docker-compose.prod.yml up -d`.

The package is a deployment bootstrap, not a full native application package.

## Workflow Design

Extend `.github/workflows/release.yml` with a packaging job after `manifest` succeeds and before `release` uploads assets.

Recommended structure:

- Add `scripts/release/build_artifacts.sh`.
- Add `scripts/release/build_deb.sh`.
- Have the GitHub Actions job call those scripts with `VERSION`.
- Upload generated files from `dist/release/`.
- Pass the assets to `gh release create`.

This keeps complex shell logic out of the workflow YAML and makes local dry-runs possible.

## Documentation Updates

Update existing documents only:

- `README.md`: align top-level version wording with v4.0 and mention Release assets.
- `infra/README.md`: add the Release asset and `.deb` deployment paths next to the existing `QB_VERSION` compose path.
- `HANDOFF.md`: record the release packaging behavior and operational boundaries.

Do not create a second top-level deployment guide unless existing docs become too large.

## Verification

Local verification should cover:

1. Packaging scripts run locally with a sample version and generate all expected files.
2. Generated tarballs contain expected files and exclude dependency/cache/build output.
3. Generated `.deb` can be inspected with `dpkg-deb --info` and `dpkg-deb --contents`.
4. Existing non-runtime checks still pass for touched areas:
   - release script shell syntax check;
   - frontend type-check if frontend metadata changes;
   - no backend regression test required unless backend runtime files change.

CI verification should cover:

- Tag release builds images first.
- Packaging uses the same `VERSION` as image tags.
- GitHub Release includes image notes and artifact files.

## Risks and Mitigations

- Risk: Release assets drift from container image contents.
  - Mitigation: Build assets from the same checked-out tag and use the tag-derived `VERSION`.
- Risk: `.deb` overwrites server configuration.
  - Mitigation: ship `.env.example`, never `.env`; keep runtime secrets outside package-managed files.
- Risk: deployment operators confuse `.deb` with a native package.
  - Mitigation: document clearly that it installs a Docker Compose deployment skeleton.
- Risk: workflow YAML becomes hard to maintain.
  - Mitigation: keep packaging behavior in scripts and call scripts from Actions.

## Acceptance Criteria

- A tag release creates GHCR images and GitHub Release assets in one workflow.
- Release notes show image deployment commands and list the downloadable package assets.
- Local packaging dry-run succeeds without Docker.
- `.deb` metadata and contents are inspectable.
- Documentation explains both deployment paths and safety boundaries.
