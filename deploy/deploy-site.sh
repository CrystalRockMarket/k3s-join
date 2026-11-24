# Publish https://k3s.kituwa.com to kituwa-cloud-1.
#
# Run from the project root on trinity, after tests/run-tests.sh passes.
# The host only serves: it gets static files, the three installers and an nginx
# vhost. Nothing here executes on the host at request time.

set -euo pipefail

HOST="${KITUWA_HOST:-kituwa-cloud-1}"
ROOT="/var/www/k3s.kituwa.com"
DOMAIN="k3s.kituwa.com"
CONF="k3s-kituwa.conf"

say()  { printf '\n==> %s\n' "$*"; }
ok()   { printf '  ok   %s\n' "$*"; }
die()  { printf 'error %s\n' "$*" >&2; exit 1; }

[ -f site/index.html ] || die "run this from the project root"
[ -f install.sh ] || die "install.sh is missing"

say "tests"
bash tests/run-tests.sh >/dev/null || die "tests failed; not publishing"

say "staging files"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/root"
cp -a site/. "$STAGE/root/"
cp install.sh install-macos.sh install.ps1 "$STAGE/root/"
cp install.sh "$STAGE/root/install-1.4.0.sh"
cp install-macos.sh "$STAGE/root/install-macos-1.4.0.sh"
mkdir -p "$STAGE/root/docs"
cp docs/*.md "$STAGE/root/docs/" 2>/dev/null || true
chmod 0644 "$STAGE"/root/install*.sh "$STAGE"/root/install.ps1

say "uploading to $HOST:$ROOT"
ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" "sudo -n mkdir -p $ROOT && sudo -n chown \$(id -u):\$(id -g) $ROOT"
tar -C "$STAGE/root" -czf - . | ssh -o BatchMode=yes "$HOST" "tar -C $ROOT -xzf -"
ok "files copied"

say "nginx vhost"
scp -q -o BatchMode=yes deploy/"$CONF" "$HOST:/tmp/$CONF"
if ! ssh -o BatchMode=yes "$HOST" "sudo -n test -d /etc/letsencrypt/live/$DOMAIN"; then
  # The real vhost points at a certificate that does not exist yet, so nginx
  # would refuse to load it. Enable an HTTP-only placeholder, get the cert,
  # then install the real vhost.
  say "no certificate yet: enabling an HTTP-only placeholder first"
  cat > "$STAGE/acme.conf" <<CONF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 200 "issuing certificate\n"; }
}
CONF
  scp -q -o BatchMode=yes "$STAGE/acme.conf" "$HOST:/tmp/$CONF"
  ssh -o BatchMode=yes "$HOST" "sudo -n install -m 0644 /tmp/$CONF /etc/nginx/sites-available/$CONF && \
    sudo -n ln -sf /etc/nginx/sites-available/$CONF /etc/nginx/sites-enabled/$CONF && \
    sudo -n nginx -t && sudo -n systemctl reload nginx"
  say "TLS certificate"
  ssh -o BatchMode=yes "$HOST" "sudo -n certbot certonly --webroot -w /var/www/html -d $DOMAIN \
    --non-interactive --agree-tos --keep-until-expiring"
  ok "certificate issued"
  scp -q -o BatchMode=yes deploy/"$CONF" "$HOST:/tmp/$CONF"
fi
ssh -o BatchMode=yes "$HOST" "sudo -n install -m 0644 /tmp/$CONF /etc/nginx/sites-available/$CONF && \
  sudo -n ln -sf /etc/nginx/sites-available/$CONF /etc/nginx/sites-enabled/$CONF && \
  sudo -n nginx -t"
ok "vhost installed and nginx config valid"

say "reload"
ssh -o BatchMode=yes "$HOST" "sudo -n systemctl reload nginx"
ok "nginx reloaded"

say "verify"
sleep 2
for path in / /styles.css /app.js /install.sh /install-macos.sh /install.ps1 /join/ /join/join.js /points/ /points/points.js /cluster/ /cluster/cluster.js /verify/ /watchdog/ /robots.txt; do
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "https://$DOMAIN$path" || true)"
  printf '  %-18s %s\n' "$path" "$code"
  [ "$code" = "200" ] || die "$path returned $code"
done
curl -fsS --max-time 15 "https://$DOMAIN/install.sh" | head -1
ok "live"
