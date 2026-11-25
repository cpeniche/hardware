#!/usr/bin/env python3
"""Create InvenTree parts from your own KiCad symbol libraries.

Usage:
    python3 import_kicad_symbols.py            # dry run: writes symbols_import_review.csv
    python3 import_kicad_symbols.py --commit   # actually create the parts

For each symbol in the INCLUDE_LIBS libraries of SYMBOL_DIR (power symbols excluded):
  part named after the symbol, category guessed from reference/description,
  parameters Kicad_Symbol (lib:name), Kicad_Footprint, Kicad_Reference, Kicad_Value,
  datasheet as the part link, manufacturer part if the symbol has a manufacturer
  property, Mouser supplier part if it has a Mouser part number. No stock is created:
  add stock in InvenTree (or with import_digikey_csv.py) when you actually own the part.

Skipped: symbols already used by a part (Kicad_Symbol parameter), parts whose name
already exists. Footprint problems are listed in the review CSV, the part is still
created so you can fix the footprint in the symbol or in InvenTree.

Category overrides: add "lib:symbol": "Electronics/..." to CATEGORY_OVERRIDES.
Re-running is safe.
"""
import csv
import glob
import os
import re
import sys

from import_digikey_csv import PLUGIN, Writer, api, ensure_datasheet, find_one, rows_of

SYMBOL_DIR = "/home/carlo/NfsShare/Projects/hardware/libraries/Kicad_Library/symbols"
# Only these libraries are imported (add a library name here to include it).
INCLUDE_LIBS = {"_Connectors", "_ISSI_Memory", "_MCU_ESP32", "_MCU_NORDIC", "_MCU_NXP_LPC",
                "_Misc_Symbols", "Cards", "con-hirose-df40"}
REVIEW_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "symbols_import_review.csv")
CATEGORY_OVERRIDES = {
    # "_Misc_Symbols:SOME_SYMBOL": "Electronics/Misc",
}

# ------------------------------------------------------------------ parsing


def top_level_symbols(text):
    """Yield (name, block) for each top-level (symbol ...) in a .kicad_sym file."""
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':                      # skip strings (may contain parens)
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "(":
            depth += 1
            if depth == 2 and text.startswith("(symbol ", i):
                start, d, j = i, 0, i
                while j < n:
                    ch = text[j]
                    if ch == '"':
                        j += 1
                        while j < n and text[j] != '"':
                            j += 2 if text[j] == "\\" else 1
                    elif ch == "(":
                        d += 1
                    elif ch == ")":
                        d -= 1
                        if d == 0:
                            break
                    j += 1
                block = text[start:j + 1]
                name = re.match(r'\(symbol "((?:[^"\\]|\\.)*)"', block).group(1)
                yield name, block
                i = j
                depth -= 1
        elif c == ")":
            depth -= 1
        i += 1


def props(block):
    # only the symbol's own properties (before its first sub-unit)
    head = block.split("\n\t\t(symbol ", 1)[0]
    return {k: v.replace('\\"', '"') for k, v in
            re.findall(r'\(property "((?:[^"\\]|\\.)*)" "((?:[^"\\]|\\.)*)"', head)}


def known_footprint_libs():
    libs = set()
    for table in ("~/.config/kicad/10.0/fp-lib-table", "/usr/share/kicad/template/fp-lib-table"):
        p = os.path.expanduser(table)
        if os.path.exists(p):
            libs |= set(re.findall(r'\(name "([^"]+)"\)', open(p).read()))
    return libs


# ------------------------------------------------------------------ classification


def category_for(lib, name, ref, desc):
    t = f"{name} {desc}".lower()
    r = ref.upper()
    has = lambda *w: any(re.search(rf"\b{x}", t) for x in w)
    if f"{lib}:{name}" in CATEGORY_OVERRIDES:
        return CATEGORY_OVERRIDES[f"{lib}:{name}"]
    if has("antenna"):
        return "Electronics/RF and Antennas"
    if has(r"switch\b", "tactile") or "keys" in t or r in ("SW", "S"):
        return "Electronics/Switches and Buttons"
    if has("crystal", "oscillat", "tcxo", r"xo\b") or r in ("Y", "X"):
        return "Electronics/Crystals and Oscillators"
    if has("connector", "header", "jack", "socket", "receptacle", "terminal block", "usb",
           "ffc", "card", "tag connect", "m\\.2") or r in ("J", "P", "CN", "M"):
        return "Electronics/Connectors"
    if has("ferrite"):
        return "Electronics/Ferrite Beads"
    if r == "L" or has("inductor"):
        return "Electronics/Inductors"
    if has("tvs", "esd"):
        return "Electronics/Protection"
    if r == "C" or has("capacitor"):
        return "Electronics/Capacitors"
    if r == "R" or has("resistor"):
        return "Electronics/Resistors"
    if has("led"):
        return "Electronics/LEDs"
    if r == "D" or has("diode", "rectifier", "schottky"):
        return "Electronics/Diodes"
    if r == "Q" or has("mosfet", "transistor"):
        return "Electronics/Transistors"
    if has("battery"):
        return "Electronics/Misc"
    # ICs
    if has("half bridge", "half-bridge", "h-bridge", "motor"):
        return "Electronics/ICs/Motor Drivers"
    if lib.startswith("_MCU") or has("microcontroller", "mcu", "soc", "cortex") \
            or re.match(r"(STM32|LPC|MIMXRT|ESP32|NRF5)", name, re.I):
        return "Electronics/ICs/Microcontrollers"
    if lib == "_ISSI_Memory" or has("memory", "sdram", "flash", "nor"):
        return "Electronics/ICs/Memory"
    if lib == "_Power" or has("regulator", "converter", "step-down", "step-up", "switcher",
                              "ldo", "pol module", "dc dc"):
        return "Electronics/ICs/Power"
    if has("transceiver", "translator", "shift register", "buffer", "linbus", "level"):
        return "Electronics/ICs/Interface"
    if r in ("U", "IC", "PS"):
        return "Electronics/ICs"
    return "Electronics/Misc"


def first(p, *keys):
    for k in keys:
        v = p.get(k, "").strip()
        if v and v != "~":
            return v
    return ""


# ------------------------------------------------------------------ main


def main():
    commit = "--commit" in sys.argv
    W = Writer(commit)
    print(("COMMIT" if commit else "DRY RUN (nothing written; add --commit)") + "\n")

    fp_libs = known_footprint_libs()
    cats = {c["pathstring"]: c["pk"] for c in rows_of(api("GET", "/api/part/category/", limit=1000))}
    published = {c["category"]["id"] for c in rows_of(api("GET", f"/plugin/{PLUGIN}/api/category/"))}
    tpls = {t["name"]: t["pk"] for t in rows_of(api("GET", "/api/parameter/template/", limit=1000))}
    companies = {c["name"]: c["pk"] for c in rows_of(api("GET", "/api/company/", limit=1000))}
    used_symbols = {p["data"] for p in rows_of(api("GET", "/api/parameter/", limit=5000,
                                                   template=tpls["Kicad_Symbol"]))}
    part_names = {p["name"] for p in rows_of(api("GET", "/api/part/", limit=5000))}

    def ensure_category(path, ref):
        if path not in cats:
            parent, _, name = path.rpartition("/")
            parent_pk = ensure_category(parent, "") if parent else None
            cats[path] = W.create("/api/part/category/", {"name": name, "parent": parent_pk},
                                  f"category {path}")["pk"]
        pk = cats[path]
        if pk not in published and path != "Electronics":
            W.create(f"/plugin/{PLUGIN}/api/category/", {"category": pk, "default_reference": ref},
                     f"publish {path} to KiCad")
            published.add(pk)
        return pk

    def company(name, **flags):
        if name not in companies:
            companies[name] = W.create("/api/company/", {"name": name, "currency": "USD", **flags},
                                       f"company {name}")["pk"]
        return companies[name]

    review = []
    counts = {"create": 0, "skip": 0}
    for path in sorted(glob.glob(os.path.join(SYMBOL_DIR, "*.kicad_sym"))):
        lib = os.path.splitext(os.path.basename(path))[0]
        if lib not in INCLUDE_LIBS:
            continue
        text = open(path, encoding="utf-8").read()
        for name, block in top_level_symbols(text):
            if re.search(r"^\t\t\(power\)", block, re.M):
                continue
            p = props(block)
            lib_id = f"{lib}:{name}"
            ref = first(p, "Reference") or "U"
            fp = first(p, "Footprint")
            desc = first(p, "Description", "DESCRIPTION", "Description_1") or name
            value = first(p, "Value") or name
            ds = first(p, "Datasheet", "DATASHEET_URL")
            mfr = first(p, "MANUFACTURER", "Manufacturer_Name", "MF", "Manufacturer")
            mpn = first(p, "Manufacturer_Part_Number", "MPN", "MP") or name
            mouser = first(p, "Mouser Part Number")
            cat = category_for(lib, name, ref, desc)

            issues = []
            if not fp:
                issues.append("no footprint")
            elif ":" not in fp:
                issues.append("footprint has no library name")
            elif fp.split(":", 1)[0] not in fp_libs:
                issues.append(f"footprint library '{fp.split(':', 1)[0]}' not in global table")

            if lib_id in used_symbols:
                action = "skip: symbol already used by a part"
            elif name in part_names:
                action = "skip: part with this name exists"
            else:
                action = "create"
            counts["create" if action == "create" else "skip"] += 1
            review.append({"action": action, "symbol": lib_id, "category": cat, "reference": ref,
                           "value": value, "footprint": fp, "issues": "; ".join(issues),
                           "manufacturer": mfr, "mpn": mpn if mfr else "", "mouser": mouser,
                           "description": desc})
            if action != "create":
                continue

            print(f"{lib_id}  ->  {cat}  [{ref}]" + (f"  !! {'; '.join(issues)}" if issues else ""))
            if not commit:
                continue
            part = W.create("/api/part/", {
                "name": name[:100], "description": desc[:250], "category": ensure_category(cat, ref),
                "component": True, "purchaseable": True, "active": True,
                "link": ds if ds.startswith("http") else "",
                "keywords": first(p, "ki_keywords")[:250],
            }, "part")
            part_names.add(name)
            ensure_datasheet(W, part["pk"], ds)
            for tname, val in (("Kicad_Symbol", lib_id), ("Kicad_Footprint", fp),
                               ("Kicad_Reference", ref), ("Kicad_Value", value)):
                if val:
                    W.create("/api/parameter/", {"template": tpls[tname], "model_type": "part.part",
                                                 "model_id": part["pk"], "data": val[:500]},
                             f"param {tname} = {val}")
            if mfr:
                mp = W.create("/api/company/part/manufacturer/",
                              {"part": part["pk"], "manufacturer": company(mfr, is_manufacturer=True),
                               "MPN": mpn}, f"MPN {mfr} {mpn}")
                if mouser:
                    W.create("/api/company/part/", {
                        "part": part["pk"], "supplier": company("Mouser", is_supplier=True),
                        "SKU": mouser, "manufacturer_part": mp["pk"],
                        "link": f"https://www.mouser.com/c/?q={mouser}"}, f"Mouser {mouser}")

    with open(REVIEW_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(review[0].keys()))
        w.writeheader()
        w.writerows(sorted(review, key=lambda r: (r["action"] != "create", r["category"], r["symbol"])))
    print(f"\n{counts['create']} to create, {counts['skip']} skipped. Review: {REVIEW_FILE}")
    if not commit:
        print("Dry run only. Re-run with --commit to write.")


if __name__ == "__main__":
    main()
