#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  echo "usage: $0 VERSION" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
OUT_DIR="$ROOT/dist/release"
WORK_DIR="$ROOT/dist/release-work"

rm -rf "$OUT_DIR" "$WORK_DIR"
mkdir -p "$OUT_DIR" "$WORK_DIR"

tar_backend() {
  tar -czf "$OUT_DIR/quantbot-backend-${VERSION}.tar.gz" \
    --exclude='.env' \
    --exclude='.env.*' \
    --exclude='certs' \
    --exclude='*.key' \
    --exclude='*.pem' \
    --exclude='.venv' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --exclude='.coverage' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -C "$ROOT" backend
}

tar_frontend() {
  tar -czf "$OUT_DIR/quantbot-frontend-${VERSION}.tar.gz" \
    --exclude='.env' \
    --exclude='.env.*' \
    --exclude='certs' \
    --exclude='*.key' \
    --exclude='*.pem' \
    --exclude='node_modules' \
    --exclude='dist' \
    --exclude='playwright-report' \
    --exclude='test-results' \
    --exclude='.DS_Store' \
    --exclude='.vite' \
    --exclude='.vitest' \
    -C "$ROOT" frontend
}

write_install_notes() {
  cat > "$WORK_DIR/RELEASE-INSTALL.md" <<EOF
# QuantBot ${VERSION} Release Deployment

This bundle installs the Docker Compose deployment skeleton. It does not
include secrets, TLS private keys, certificates, database data, or runtime
volumes.

## Deploy

\`\`\`bash
export QB_VERSION=${VERSION}
cp .env.example .env   # only when .env does not already exist
# edit .env and place TLS certificates before starting production services
docker compose -f infra/docker-compose.prod.yml pull
docker compose -f infra/docker-compose.prod.yml up -d
\`\`\`

Production images are pulled from GHCR:

- ghcr.io/lsgoodlionel/quantbot-backend:${VERSION}
- ghcr.io/lsgoodlionel/quantbot-frontend:${VERSION}
EOF
}

tar_deploy() {
  local deploy_root="$WORK_DIR/quantbot-deploy-${VERSION}"
  mkdir -p "$deploy_root"
  cp "$ROOT/.env.example" "$deploy_root/.env.example"
  mkdir -p "$deploy_root/infra"
  cp "$ROOT/infra/docker-compose.prod.yml" "$deploy_root/infra/docker-compose.prod.yml"
  cp -R "$ROOT/infra/nginx" "$deploy_root/infra/nginx"
  rm -rf "$deploy_root/infra/nginx/certs"
  find "$deploy_root" \( -name '*.key' -o -name '*.pem' -o -name '.env' \) -delete
  cp "$WORK_DIR/RELEASE-INSTALL.md" "$deploy_root/RELEASE-INSTALL.md"

  tar -czf "$OUT_DIR/quantbot-deploy-${VERSION}.tar.gz" \
    -C "$WORK_DIR" "quantbot-deploy-${VERSION}"
}

write_install_notes
tar_backend
tar_frontend
tar_deploy

rm -rf "$WORK_DIR"
ls -1 "$OUT_DIR"
