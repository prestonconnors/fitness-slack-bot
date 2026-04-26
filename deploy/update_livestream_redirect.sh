#!/usr/bin/env bash
# Update the /livestream redirect target in an nginx site config and reload nginx.
#
# Usage:   update_livestream_redirect.sh <new-url>
# Install: sudo install -m 0755 -o root -g root \
#            deploy/update_livestream_redirect.sh \
#            /usr/local/sbin/update_livestream_redirect.sh
#
# Then add a sudoers drop-in (run `sudo visudo -f /etc/sudoers.d/fitness-slack-bot`):
#   preston ALL=(root) NOPASSWD: /usr/local/sbin/update_livestream_redirect.sh
#
# This is the ONLY thing the Python script is allowed to run as root.

set -euo pipefail

# --- Edit these to match your setup ---------------------------------
SITE_FILE="/etc/nginx/sites-available/prestonconnors.com"
# Regex (POSIX ERE) matching the rewrite line. Capture group 1 = prefix
# up to and including the whitespace before the URL; capture group 2 =
# whitespace + " redirect;" suffix.
PATTERN='^([[:space:]]*rewrite[[:space:]]+\^/livestream\$[[:space:]]+)[^[:space:]]+([[:space:]]+redirect;[[:space:]]*)$'
# --------------------------------------------------------------------

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <new-url>" >&2
  exit 64
fi

NEW_URL="$1"

# Validate URL: https only, and reject whitespace / shell metacharacters / quotes.
case "$NEW_URL" in
  https://*) : ;;
  *) echo "refusing non-https URL: $NEW_URL" >&2; exit 65 ;;
esac
case "$NEW_URL" in
  *[[:space:]]* | *\<* | *\>* | *\"* | *\'* | *\`* | *\\* | *\$* | *\|* | *\;* | *\&* )
    echo "refusing URL with disallowed characters: $NEW_URL" >&2
    exit 65
    ;;
esac

if [[ ! -f "$SITE_FILE" ]]; then
  echo "site file not found: $SITE_FILE" >&2
  exit 66
fi

TMP="$(mktemp)"
BACKUP="${SITE_FILE}.bak"
trap 'rm -f "$TMP"' EXIT

# Use awk so we can safely embed the new URL without sed-escaping headaches.
awk -v new="$NEW_URL" -v pat="$PATTERN" '
  match($0, pat, m) { print m[1] new m[2]; replaced++; next }
  { print }
  END { if (!replaced) { print "no /livestream rewrite line matched" > "/dev/stderr"; exit 67 } }
' "$SITE_FILE" > "$TMP"

# Bail if nothing changed (avoid a needless reload).
if cmp -s "$TMP" "$SITE_FILE"; then
  echo "nginx: /livestream already points at $NEW_URL — no change."
  exit 0
fi

cp -a "$SITE_FILE" "$BACKUP"
install -m 0644 -o root -g root "$TMP" "$SITE_FILE"

if ! nginx -t 2>&1; then
  echo "nginx -t failed; restoring backup." >&2
  install -m 0644 -o root -g root "$BACKUP" "$SITE_FILE"
  exit 1
fi

systemctl reload nginx
echo "nginx: /livestream now redirects to $NEW_URL"
