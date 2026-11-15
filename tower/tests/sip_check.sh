#!/bin/sh
# Renders the real config (with fake Callcentric creds), starts Asterisk, and checks the phone side.
set -e
cd "$(dirname "$0")/.."
pkill -f "asterisk -f" || true
sleep 1
rm -f /etc/asterisk/extensions_test.conf
CC_USER=17775551234 CC_PASS=secret PHONE_PASS=testpass EXTERNAL_HOST=203.0.113.10 \
  TEMPLATE_DIR="$PWD/asterisk" AST_ETC=/etc/asterisk python3 -c "import entrypoint; entrypoint.render()"
asterisk -f > /tmp/sipcheck.log 2>&1 &
sleep 6
asterisk -rx "pjsip show registrations" | grep -q "callcentric/sip:sip.callcentric.net" || { echo "Callcentric registration not loaded"; exit 1; }
asterisk -rx "pjsip show identifies" | grep -q "204.11.192.0/22" || { echo "Callcentric identify not loaded"; exit 1; }
asterisk -rx "dialplan show cg-answered" | grep -q "System" || { echo "cg-answered missing"; exit 1; }
python3 tests/sip_register_check.py testpass
pkill -f "asterisk -f" || true
