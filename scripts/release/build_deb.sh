#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  echo "usage: $0 VERSION" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
OUT_DIR="$ROOT/dist/release"
WORK_DIR="$ROOT/dist/deb-work"
PKG_ROOT="$WORK_DIR/quantbot-${VERSION}"
INSTALL_ROOT="$PKG_ROOT/opt/quantbot"

rm -rf "$WORK_DIR"
mkdir -p "$OUT_DIR" "$INSTALL_ROOT/infra" "$PKG_ROOT/DEBIAN"

cp "$ROOT/.env.example" "$INSTALL_ROOT/.env.example"
cp "$ROOT/infra/docker-compose.prod.yml" "$INSTALL_ROOT/infra/docker-compose.prod.yml"
cp -R "$ROOT/infra/nginx" "$INSTALL_ROOT/infra/nginx"
rm -rf "$INSTALL_ROOT/infra/nginx/certs"
find "$INSTALL_ROOT" \( -name '*.key' -o -name '*.pem' -o -name '.env' \) -delete

cat > "$INSTALL_ROOT/RELEASE-INSTALL.md" <<EOF
# QuantBot ${VERSION} Debian Deployment Skeleton

This package installs the Docker Compose deployment skeleton under
\`/opt/quantbot\`. It does not include secrets, TLS private keys,
certificates, database data, or runtime volumes.

## Deploy

\`\`\`bash
cd /opt/quantbot
export QB_VERSION=${VERSION}
cp .env.example .env   # only when .env does not already exist
# edit .env and place TLS certificates before starting production services
docker compose -f infra/docker-compose.prod.yml pull
docker compose -f infra/docker-compose.prod.yml up -d
\`\`\`
EOF

cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: quantbot
Version: ${VERSION}
Section: web
Priority: optional
Architecture: all
Maintainer: QuantBot Team
Description: QuantBot Docker Compose deployment skeleton
 Installs production Docker Compose and Nginx deployment templates for
 QuantBot. Runtime services continue to use versioned GHCR container images.
EOF

cat > "$PKG_ROOT/DEBIAN/postinst" <<EOF
#!/usr/bin/env sh
set -e

cat <<'NOTE'
QuantBot deployment skeleton installed to /opt/quantbot.

Next steps:
  cd /opt/quantbot
  export QB_VERSION=${VERSION}
  cp .env.example .env   # only when .env does not already exist
  # edit .env and place TLS certificates before starting production services
  docker compose -f infra/docker-compose.prod.yml pull
  docker compose -f infra/docker-compose.prod.yml up -d

This package does not start Docker services or modify secrets.
NOTE
EOF
chmod 0755 "$PKG_ROOT/DEBIAN/postinst"

find "$PKG_ROOT" -type d -exec chmod 0755 {} +
find "$INSTALL_ROOT" -type f -exec chmod 0644 {} +

DEB_PATH="$OUT_DIR/quantbot-${VERSION}.deb"
if command -v dpkg-deb >/dev/null 2>&1; then
  dpkg-deb --build "$PKG_ROOT" "$DEB_PATH"
else
  echo "dpkg-deb not found; building .deb with portable ar fallback" >&2
  FALLBACK_DIR="$WORK_DIR/deb-archive"
  mkdir -p "$FALLBACK_DIR/control" "$FALLBACK_DIR/data"
  printf '2.0\n' > "$FALLBACK_DIR/debian-binary"
  cp "$PKG_ROOT/DEBIAN/control" "$FALLBACK_DIR/control/control"
  cp "$PKG_ROOT/DEBIAN/postinst" "$FALLBACK_DIR/control/postinst"
  (cd "$FALLBACK_DIR/control" && tar -czf "$FALLBACK_DIR/control.tar.gz" .)
  (cd "$PKG_ROOT" && tar --exclude='./DEBIAN' -czf "$FALLBACK_DIR/data.tar.gz" .)
  rm -f "$DEB_PATH"
  printf '!<arch>\n' > "$DEB_PATH"
  append_ar_member() {
    local member_name="$1"
    local member_path="$2"
    local member_size
    member_size="$(wc -c < "$member_path" | tr -d ' ')"
    # ar header: name(16), mtime(12), uid(6), gid(6), mode(8), size(10), magic(2)
    printf '%-16s%-12s%-6s%-6s%-8s%-10s`\n' \
      "$member_name" 0 0 0 100644 "$member_size" >> "$DEB_PATH"
    cat "$member_path" >> "$DEB_PATH"
    if (( member_size % 2 == 1 )); then
      printf '\n' >> "$DEB_PATH"
    fi
  }
  append_ar_member "debian-binary" "$FALLBACK_DIR/debian-binary"
  append_ar_member "control.tar.gz" "$FALLBACK_DIR/control.tar.gz"
  append_ar_member "data.tar.gz" "$FALLBACK_DIR/data.tar.gz"
fi
rm -rf "$WORK_DIR"
ls -1 "$DEB_PATH"
