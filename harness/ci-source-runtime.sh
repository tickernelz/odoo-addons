#!/bin/sh
set -eu
commit="$1"
printf 'deb http://archive.debian.org/debian buster main\ndeb http://archive.debian.org/debian-security buster/updates main\n' > /etc/apt/sources.list
apt-get -o Acquire::Check-Valid-Until=false update -qq
apt-get install -y -qq --no-install-recommends libldap2-dev libsasl2-dev libpq-dev libxml2-dev libxslt1-dev \
    libjpeg-dev zlib1g-dev libfreetype6-dev >/dev/null
mkdir -p /tmp/src
curl -sSL "https://codeload.github.com/odoo/odoo/tar.gz/$commit" | tar xz -C /tmp/src --strip-components=1
pip install -q --no-cache-dir --no-binary psycopg2 -r /tmp/src/requirements.txt
python --version
