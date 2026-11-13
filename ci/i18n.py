"""Translation checks: id.po against the template, the template against a fresh export, no Indonesian outside i18n/.

Usage: python3 ci/i18n.py check | compare <fresh folder or .pot>
"""
import os
import re
import sys

import polib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = ("kaitech_mcp_server", "pos_bank_card_program", "pos_checkout_extras", "pos_hr_manager_validation", "kai_pos_promotion", "pos_xendit_qris")
INDONESIAN = ("yang", "tidak", "belum", "kunci", "hapus", "simpan", "batal", "pengaturan", "dengan", "untuk",
              "sudah", "bisa", "harus", "panggilan", "dicabut", "kedaluwarsa")
PLACEHOLDER = re.compile(r"%(?:\([a-z_]+\))?[sdr]")


def catalogue_paths(module):
    folder = os.path.join(ROOT, module, "i18n")
    return os.path.join(folder, module + ".pot"), os.path.join(folder, "id.po")


def check_module(module):
    problems = []
    pot_path, po_path = catalogue_paths(module)
    pot = polib.pofile(pot_path)
    po = polib.pofile(po_path)
    translated = dict((entry.msgid, entry.msgstr) for entry in po if entry.msgstr and "fuzzy" not in entry.flags)
    for entry in pot:
        if entry.msgid not in translated:
            problems.append("%s untranslated: %r" % (module, entry.msgid))
        elif sorted(PLACEHOLDER.findall(entry.msgid)) != sorted(PLACEHOLDER.findall(translated[entry.msgid])):
            problems.append("%s placeholders differ: %r" % (module, entry.msgid))
    stale = set(translated) - set(entry.msgid for entry in pot)
    problems.extend("%s not in template: %r" % (module, msgid) for msgid in sorted(stale))
    return problems, len(pot)


def check():
    problems = []
    total = 0
    for module in MODULES:
        module_problems, count = check_module(module)
        problems.extend(module_problems)
        total += count
    pattern = re.compile(r"\b(%s)\b" % "|".join(INDONESIAN), re.IGNORECASE)
    for module in MODULES:
        folder_root = os.path.join(ROOT, module)
        for folder, _dirs, files in os.walk(folder_root):
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
    print("i18n ok: %s entries translated, 0 untranslated, 0 fuzzy" % total)


def compare():
    target = sys.argv[2]
    if not os.path.exists(target):
        sys.exit("fresh export not found: %s" % target)
    problems = []
    total = 0
    if os.path.isdir(target):
        fresh = dict((module, os.path.join(target, module + ".pot")) for module in MODULES)
    else:
        fresh = {"kaitech_mcp_server": target}
    for module, fresh_path in fresh.items():
        pot_path, _po_path = catalogue_paths(module)
        fresh_ids = set(entry.msgid for entry in polib.pofile(fresh_path))
        stored = set(entry.msgid for entry in polib.pofile(pot_path))
        problems += ["%s missing from the template: %r" % (module, msgid) for msgid in sorted(fresh_ids - stored)]
        problems += ["%s no longer in the module: %r" % (module, msgid) for msgid in sorted(stored - fresh_ids)]
        total += len(fresh_ids)
    if problems:
        sys.exit("\n".join(problems) + "\nRe-export with ci/export-pot.sh and update the catalogues.")
    print("template ok: %s msgids match a fresh export" % total)


if __name__ == "__main__":
    {"check": check, "compare": compare}[sys.argv[1]]()
