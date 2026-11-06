#!/bin/sh
set -eu
commit="${PINNED_ODOO_COMMIT:-28c3f51c4878fbcd79b2e819948465fcf2160ebc}"
apt-get update -qq
apt-get install -y -qq --no-install-recommends libldap2-dev libsasl2-dev libpq-dev libxml2-dev libxslt1-dev \
    libjpeg-dev zlib1g-dev libfreetype6-dev >/dev/null
mkdir -p /tmp/src
curl -sSL "https://codeload.github.com/odoo/odoo/tar.gz/$commit" | tar xz -C /tmp/src --strip-components=1
pip install -q --no-cache-dir -r /tmp/src/requirements.txt
python --version
python -c "import odoo" 2>/dev/null || true
