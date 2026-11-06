"""Translation checks: id.po against the template, the template against a fresh export, no Indonesian outside i18n/.

Usage: python3 ci/i18n.py check | compare <fresh.pot>
"""
import os
import re
import sys

import polib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(ROOT, "kaitech_mcp")
POT = os.path.join(MODULE, "i18n", "kaitech_mcp.pot")
PO = os.path.join(MODULE, "i18n", "id.po")
INDONESIAN = ("yang", "tidak", "belum", "kunci", "hapus", "simpan", "batal", "pengaturan", "dengan", "untuk",
              "sudah", "bisa", "harus", "panggilan", "dicabut", "kedaluwarsa")
PLACEHOLDER = re.compile(r"%(?:\([a-z_]+\))?[sdr]")


def check():
    problems = []
    pot = polib.pofile(POT)
    po = polib.pofile(PO)
    translated = dict((entry.msgid, entry.msgstr) for entry in po if entry.msgstr and "fuzzy" not in entry.flags)
    for entry in pot:
        if entry.msgid not in translated:
            problems.append("untranslated: %r" % entry.msgid)
        elif sorted(PLACEHOLDER.findall(entry.msgid)) != sorted(PLACEHOLDER.findall(translated[entry.msgid])):
            problems.append("placeholders differ: %r" % entry.msgid)
    stale = set(translated) - set(entry.msgid for entry in pot)
    problems.extend("not in template: %r" % msgid for msgid in sorted(stale))
    pattern = re.compile(r"\b(%s)\b" % "|".join(INDONESIAN), re.IGNORECASE)
    for folder, _dirs, files in os.walk(MODULE):
        if os.sep + "i18n" in folder or os.sep + "tests" in folder:
            continue
        for name in files:
            if name.endswith((".py", ".xml", ".csv", ".js")):
                path = os.path.join(folder, name)
                for number, line in enumerate(open(path, encoding="utf-8"), 1):
                    found = pattern.search(line)
                    if found:
                        problems.append("Indonesian word %r in %s:%s" % (found.group(0), os.path.relpath(path, ROOT), number))
    if problems:
        sys.exit("\n".join(problems))
    print("i18n ok: %s entries translated, 0 untranslated, 0 fuzzy" % len(pot))


def compare():
    fresh = set(entry.msgid for entry in polib.pofile(sys.argv[2]))
    stored = set(entry.msgid for entry in polib.pofile(POT))
    problems = ["missing from the template: %r" % msgid for msgid in sorted(fresh - stored)]
    problems += ["no longer in the module: %r" % msgid for msgid in sorted(stored - fresh)]
    if problems:
        sys.exit("\n".join(problems) + "\nRe-export with harness/export-pot.sh and run harness/i18n.py build.")
    print("template ok: %s msgids match a fresh export" % len(fresh))


if __name__ == "__main__":
    {"check": check, "compare": compare}[sys.argv[1]]()
