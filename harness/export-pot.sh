#!/bin/sh
set -eu
db="$1"
shift
odoo "$@" -d "$db" --i18n-export=/tmp/kaitech_mcp_server.po --modules=kaitech_mcp_server --stop-after-init >/tmp/export.log 2>&1 \
    || { cat /tmp/export.log >&2; exit 1; }
sed -e '/^"POT-Creation-Date:/d' -e '/^"PO-Revision-Date:/d' -e '/^"Project-Id-Version:/d' /tmp/kaitech_mcp_server.po
