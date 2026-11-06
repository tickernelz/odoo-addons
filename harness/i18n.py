"""Translation checks: id.po from the template and the term list, and a scan for Indonesian outside i18n/.

Usage: python3 harness/i18n.py build    rewrite i18n/id.po from the .pot and harness/id_terms.json
       python3 harness/i18n.py check    fail when id.po is stale, incomplete, or Indonesian leaks into sources
"""
import json
import os
import re
import sys

import polib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(ROOT, "kaitech_mcp_server")
POT = os.path.join(MODULE, "i18n", "kaitech_mcp_server.pot")
PO = os.path.join(MODULE, "i18n", "id.po")
TERMS = os.path.join(ROOT, "harness", "id_terms.json")
INDONESIAN = ("yang", "tidak", "belum", "kunci", "hapus", "simpan", "batal", "pengaturan", "dengan", "untuk",
              "sudah", "bisa", "harus", "panggilan", "dicabut", "kedaluwarsa")
PLACEHOLDER = re.compile(r"%(?:\([a-z_]+\))?[sdr]")


def build():
    terms = json.load(open(TERMS, encoding="utf-8"))
    pot = polib.pofile(POT)
    po = polib.POFile(wrapwidth=0)
    po.metadata = dict(pot.metadata, **{"Project-Id-Version": "kaitech_mcp_server 10.0",
                                         "PO-Revision-Date": "2026-11-06 00:00+0000",
                                         "Language": "id", "Plural-Forms": "nplurals=1; plural=0;",
                                         "Content-Type": "text/plain; charset=UTF-8"})
    missing = []
    for entry in pot:
        if entry.msgid not in terms:
            missing.append(entry.msgid)
            continue
        po.append(polib.POEntry(msgid=entry.msgid, msgstr=terms[entry.msgid], occurrences=entry.occurrences,
                                comment=entry.comment, flags=entry.flags))
    po.save(PO)
    if missing:
        sys.exit("No Indonesian for:\n" + "\n".join(missing))


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


if __name__ == "__main__":
    {"build": build, "check": check}[sys.argv[1]]()
