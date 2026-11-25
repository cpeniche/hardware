#!/usr/bin/env python3
"""Import a Digi-Key product/cart CSV (parts you own) into InvenTree, KiCad-ready.

Usage:
    python3 import_digikey_csv.py FILE.csv            # dry run: show what would be created
    python3 import_digikey_csv.py FILE.csv --commit   # actually write to InvenTree

For every row it creates (or reuses an existing part, matched by part name or by MPN;
existing parts, e.g. ones made from your KiCad symbols, get only the Digi-Key data and stock):
  part in the right category, parameters (Kicad_Symbol/Footprint/Reference/Value,
  Capacitance/Inductance, Voltage Rating, Dielectric, Tolerance, Package),
  manufacturer + manufacturer part, Digi-Key supplier part with price break,
  and a stock item with the CSV quantity at LOCATION (only if that supplier part
  has no stock yet, so re-running never doubles stock).

Reads INVENTREE_API_HOST / INVENTREE_API_TOKEN from ~/.config/inventree/env.
Rows it cannot classify are reported and skipped; add them to OVERRIDES.
"""
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

LOCATION = "Home Lab"          # stock location created if missing
SUPPLIER = "DigiKey"
CURRENCY = "USD"
PLUGIN = "kicad-library-plugin"

# Specific parts: MPN -> fields. Symbols/footprints match the breakOutBoard_V2 schematic.
OVERRIDES = {
    # Key must be the exact "Manufacturer Part Number" from the CSV (incl. packaging suffix).
    # A category that does not exist yet is created and published to KiCad automatically.
    "MPQ6524GRE-AEC1-Z": dict(
        category="Electronics/ICs/Motor Drivers", ref="U", value="MPQ6524GRE-AEC1-Z",
        symbol="_Misc_Symbols:MPQ6524GRE-AEC1-Z",
        footprint="Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.5x2.5mm",
        package="QFN-24 4x4mm",
        description="Quad Half-Bridge Motor Driver with Serial Input Control",
        link="https://www.monolithicpower.com/en/documentview/productdocument/index/version/2/document_type/Datasheet/lang/en/sku/MPQ6524GRE-AEC1"),
    "MP6612DGF-P": dict(
        category="Electronics/ICs/Motor Drivers", ref="U", value="MP6612DGF-P",
        symbol="_Misc_Symbols:MP6612DGF-P",
        footprint="User_Footprint:TSSOP-20EP_MP6612D_MNP",
        package="TSSOP-20EP",
        description="40V 5A H-Bridge DC Motor Driver, TSSOP-20EP",
        link="https://www.monolithicpower.com/en/mp6612.html"),
    "436331045822": dict(
        category="Electronics/Switches and Buttons", ref="SW", value="436331045822",
        symbol="_Misc_Symbols:436331045822",
        footprint="User_Footprint:4363X10458XX",
        package="SMD",
        description="Tactile switch SPST-NO 0.05A 12V, side actuated, SMD right angle",
        link="https://www.we-online.com/en/components/products/datasheet/436331045822.pdf"),
}

# Footprints per (kind, package). HandSolder variants, as used in the schematics.
FOOTPRINTS = {
    ("C", "0201"): "Capacitor_SMD:C_0201_0603Metric_Pad0.64x0.40mm_HandSolder",
    ("C", "0402"): "Capacitor_SMD:C_0402_1005Metric_Pad0.74x0.62mm_HandSolder",
    ("C", "0603"): "Capacitor_SMD:C_0603_1608Metric_Pad1.08x0.95mm_HandSolder",
    ("C", "0805"): "Capacitor_SMD:C_0805_2012Metric_Pad1.18x1.45mm_HandSolder",
    ("L", "0201"): "Inductor_SMD:L_0201_0603Metric_Pad0.64x0.40mm_HandSolder",
    ("L", "0402"): "Inductor_SMD:L_0402_1005Metric_Pad0.77x0.64mm_HandSolder",
    ("L", "0603"): "Inductor_SMD:L_0603_1608Metric_Pad1.05x0.95mm_HandSolder",
    ("L", "0805"): "Inductor_SMD:L_0805_2012Metric_Pad1.15x1.40mm_HandSolder",
    ("R", "0201"): "Resistor_SMD:R_0201_0603Metric_Pad0.64x0.40mm_HandSolder",
    ("R", "0402"): "Resistor_SMD:R_0402_1005Metric_Pad0.72x0.64mm_HandSolder",
    ("R", "0603"): "Resistor_SMD:R_0603_1608Metric_Pad0.98x0.95mm_HandSolder",
    ("R", "0805"): "Resistor_SMD:R_0805_2012Metric_Pad1.20x1.40mm_HandSolder",
}
# Metric size codes in MPNs -> imperial package
MURATA_SIZE = {"03": "0201", "15": "0402", "18": "0603", "21": "0805"}
TDK_SIZE = {"0603": "0201", "1005": "0402", "1608": "0603", "2012": "0805"}
# Murata tolerance letter
TOL = {"B": "±0.1pF", "C": "±0.25pF", "D": "±0.5pF", "F": "±1%", "G": "±2%",
       "H": "±3%", "J": "±5%", "K": "±10%", "M": "±20%"}
TOL_L = {"B": "±0.1nH", "C": "±0.2nH", "S": "±0.3nH", "H": "±3%", "J": "±5%", "K": "±10%", "M": "±20%"}

# ------------------------------------------------------------------ helpers
SI = {"P": 1e-12, "N": 1e-9, "U": 1e-6, "µ": 1e-6, "M": 1e-3, "": 1}


def eng(value, unit):
    """1e-11, 'F' -> '10nF' ; 4.7e-9,'H' -> '4.7nH'"""
    for pfx, mult in (("", 1), ("m", 1e-3), ("u", 1e-6), ("n", 1e-9), ("p", 1e-12)):
        if value >= mult * 0.9999:
            n = value / mult
            s = f"{n:.3f}".rstrip("0").rstrip(".")
            return f"{s}{pfx}{unit}"
    return f"{value}{unit}"


def parse_qty(text, unit):
    """'0.047UF' -> 4.7e-8 ; '2.2NH' -> 2.2e-9"""
    m = re.search(rf"(\d+(?:\.\d+)?)\s*([PNUµM]?){unit}\b", text.upper())
    return float(m.group(1)) * SI[m.group(2)] if m else None


def classify(row):
    mpn = row["Manufacturer Part Number"].strip()
    desc = row["Description"].strip().upper()
    if mpn in OVERRIDES:
        return dict(OVERRIDES[mpn])

    pkg = None
    m = re.search(r"\b(0201|0402|0603|0805|1206)\b", desc)
    if m:
        pkg = m.group(1)

    if desc.startswith("CAP CER"):
        c = parse_qty(desc, "F")
        v = re.search(r"\b(\d+(?:\.\d+)?)V\b", desc)
        diel = re.search(r"\b(C0G|NP0|X5R|X7R|X7S|X6S|X8R|Y5V)\b", desc)
        tol = None
        mm = re.match(r"^G[RC][MT](\d\d)\d(\w\w)(\w\w)(\w{3})([A-Z])", mpn)
        if mm:
            pkg = pkg or MURATA_SIZE.get(mm.group(1))
            tol = TOL.get(mm.group(5))
        val = eng(c, "F")
        parts = ["Cap", val, v and f"{v.group(1)}V", diel and diel.group(1), pkg, tol]
        return dict(category="Electronics/Capacitors", ref="C", symbol="Device:C",
                    footprint=FOOTPRINTS.get(("C", pkg)), value=val, package=pkg,
                    capacitance=val, voltage=v and f"{v.group(1)} V",
                    dielectric=diel and diel.group(1).replace("NP0", "C0G"), tolerance=tol,
                    description=" ".join(p for p in parts if p))

    if desc.startswith("FIXED IND"):
        h = parse_qty(desc, "H")
        cur = re.search(r"\b(\d+(?:\.\d+)?)\s*(MA|A)\b", desc)
        dcr = re.search(r"\b(\d+(?:\.\d+)?)\s*(MOHM|OHM)\b", desc)
        tol = None
        mm = re.match(r"^LQ[PGWM](\d\d)\w\w\w{3}([A-Z])", mpn)
        if mm:
            pkg = pkg or MURATA_SIZE.get(mm.group(1))
            tol = TOL_L.get(mm.group(2))
        mm = re.match(r"^MLZ(\d{4})", mpn)
        if mm:
            pkg = pkg or TDK_SIZE.get(mm.group(1))
        val = eng(h, "H")
        extra = []
        if cur:
            extra.append(cur.group(1) + ("mA" if cur.group(2) == "MA" else "A"))
        if dcr:
            extra.append("DCR " + dcr.group(1) + ("mΩ" if dcr.group(2) == "MOHM" else "Ω"))
        parts = ["Inductor", val, tol, pkg] + extra
        return dict(category="Electronics/Inductors", ref="L", symbol="Device:L",
                    footprint=FOOTPRINTS.get(("L", pkg)), value=val, package=pkg,
                    inductance=val, tolerance=tol,
                    description=" ".join(p for p in parts if p))

    if desc.startswith("RES "):
        # e.g. "RES 10K OHM 1% 1/16W 0402", "RES 0 OHM JUMPER 1/16W 0402"
        m = re.search(r"\bRES\s+(\d+(?:\.\d+)?)\s*([KMG]?)\s*OHM", desc)
        if not m:
            return None
        num = m.group(1).rstrip("0").rstrip(".") if "." in m.group(1) else m.group(1)
        pfx = m.group(2).lower().replace("g", "G").replace("m", "M")
        tol = re.search(r"±?(\d+(?:\.\d+)?)%", desc)
        pwr = re.search(r"\b(\d+/\d+|\d+(?:\.\d+)?)W\b", desc)
        jumper = "JUMPER" in desc
        val = f"{num}{pfx}"                      # KiCad style: 0, 100, 4.7k, 1M
        tol_s = None if jumper else (tol and f"±{tol.group(1)}%")
        parts = ["Res", f"{val}Ω", "jumper" if jumper else tol_s,
                 pwr and f"{pwr.group(1)}W", pkg]
        return dict(category="Electronics/Resistors", ref="R", symbol="Device:R",
                    footprint=FOOTPRINTS.get(("R", pkg)), value=val, package=pkg,
                    resistance=f"{num}{pfx}ohm", tolerance=tol_s,
                    description=" ".join(p for p in parts if p))
    return None


# ------------------------------------------------------------------ API
cfg = dict(l.strip().split("=", 1) for l in open(os.path.expanduser("~/.config/inventree/env"))
           if "=" in l and not l.startswith("#"))
HOST = cfg["INVENTREE_API_HOST"].rstrip("/")
TOKEN = cfg["INVENTREE_API_TOKEN"]


def api(method, path, data=None, **query):
    url = HOST + path + ("?" + urllib.parse.urlencode(query) if query else "")
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Authorization": f"Token {TOKEN}",
                                          "Content-Type": "application/json",
                                          "Accept": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                if body and "json" not in (r.headers.get("content-type") or ""):
                    # plugin URLs briefly fall through to the web UI while the server
                    # reloads plugins; nothing was processed, so retry is safe
                    raise ConnectionError(f"got {r.headers.get('content-type')} from {r.geturl()}")
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:600]}")
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            # dropped tunnel connection: retry GETs freely; retry writes only if the
            # connection failed before the request was sent (no response at all)
            if attempt == 3:
                raise SystemExit(f"{method} {path} -> network error: {e}")
            time.sleep(2 * (attempt + 1))


def rows_of(x):
    return x["results"] if isinstance(x, dict) and "results" in x else x


class Writer:
    def __init__(self, commit):
        self.commit = commit
        self.fake = 0

    def create(self, path, data, label):
        print(f"    + {label}")
        if not self.commit:
            self.fake -= 1
            return {"pk": self.fake}
        return api("POST", path, data)


def find_one(path, pred, **query):
    for o in rows_of(api("GET", path, limit=1000, **query)):
        if pred(o):
            return o
    return None


def ensure_datasheet(W, part_pk, url):
    """KiCad's Datasheet field comes from a part attachment whose comment is 'datasheet'
    (not from the part's Link field). Add a link attachment if the part has none."""
    if not url or not url.startswith("http"):
        return
    if part_pk > 0 and any(a.get("comment", "").strip().lower() == "datasheet"
                           for a in rows_of(api("GET", "/api/attachment/",
                                                model_type="part", model_id=part_pk))):
        return
    W.create("/api/attachment/", {"model_type": "part", "model_id": part_pk,
                                  "link": url[:2000], "comment": "datasheet"}, f"datasheet {url}")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    commit = "--commit" in sys.argv
    W = Writer(commit)
    print(("COMMIT" if commit else "DRY RUN (nothing written; add --commit)") + f" -> {HOST}\n")

    with open(sys.argv[1], encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r.get("Manufacturer Part Number", "").strip()]

    cats = {c["pathstring"]: c["pk"] for c in rows_of(api("GET", "/api/part/category/", limit=1000))}
    tpls = {t["name"]: t["pk"] for t in rows_of(api("GET", "/api/parameter/template/", limit=1000))}
    companies = {c["name"]: c for c in rows_of(api("GET", "/api/company/", limit=1000))}
    published = {c["category"]["id"] for c in rows_of(api("GET", f"/plugin/{PLUGIN}/api/category/"))}

    def company(name, supplier=False, manufacturer=False):
        if name in companies:
            return companies[name]["pk"]
        c = W.create("/api/company/", {"name": name, "is_supplier": supplier,
                                       "is_manufacturer": manufacturer, "currency": CURRENCY},
                     f"company {name}")
        companies[name] = c
        return c["pk"]

    def ensure_category(path, ref):
        """Return category pk; create it (and parents) if missing, and publish it to KiCad
        if it is not published yet (also covers categories made by hand in the web UI)."""
        if path not in cats:
            parent, _, name = path.rpartition("/")
            parent_pk = ensure_category(parent, "") if parent else None
            cats[path] = W.create("/api/part/category/", {"name": name, "parent": parent_pk},
                                  f"category {path}")["pk"]
        pk = cats[path]
        if pk not in published and path != "Electronics":
            W.create(f"/plugin/{PLUGIN}/api/category/",
                     {"category": pk, "default_reference": ref},
                     f"publish {path} to KiCad")
            published.add(pk)
        return pk

    mpns = {r["Manufacturer Part Number"].strip() for r in rows}
    for key in OVERRIDES:
        if key not in mpns:
            print(f"  note: OVERRIDES entry '{key}' matches no row in this CSV "
                  f"(key must equal the exact Manufacturer Part Number)")

    loc = find_one("/api/stock/location/", lambda o: o["name"] == LOCATION, search=LOCATION)
    loc_pk = loc["pk"] if loc else None
    supplier_pk = company(SUPPLIER, supplier=True)

    def find_existing_part(mpn):
        """An existing part with this MPN: by part name (e.g. imported from a KiCad symbol)
        or by a manufacturer part number already attached to some part."""
        u = mpn.upper()
        part = find_one("/api/part/", lambda o: o["name"].upper() == u, search=mpn)
        if part:
            return part
        mp = find_one("/api/company/part/manufacturer/", lambda o: o["MPN"].upper() == u,
                      search=mpn)
        return api("GET", f"/api/part/{mp['part']}/") if mp else None

    skipped = []
    for r in rows:
        mpn = r["Manufacturer Part Number"].strip()
        info = classify(r)
        print(f"{mpn}")
        if info and not info.get("footprint"):
            info = None
        part = find_existing_part(mpn)
        if not info and not part:
            print(f"    ! cannot classify, skipped: add OVERRIDES entry with key '{mpn}'")
            skipped.append(mpn)
            continue
        if info:
            print(f"    {info['category']} | {info['description']}")
            print(f"    symbol {info['symbol']} | footprint {info['footprint']}")

        if part:
            print(f"    = part exists: {part['name']} (pk {part['pk']})"
                  + ("" if info else ", adding Digi-Key data and stock only"))
        else:
            cat_pk = ensure_category(info["category"], info.get("ref", ""))
            part = W.create("/api/part/", {
                "name": mpn, "description": info["description"][:250], "category": cat_pk,
                "component": True, "purchaseable": True, "active": True,
                "link": info.get("link", ""), "keywords": info.get("package") or "",
            }, "part")
        ppk = part["pk"]

        if info:
            ensure_datasheet(W, ppk, info.get("datasheet") or info.get("link", ""))
            params = {
                "Kicad_Symbol": info["symbol"], "Kicad_Footprint": info["footprint"],
                "Kicad_Reference": info["ref"], "Kicad_Value": info["value"],
                "Capacitance": info.get("capacitance"), "Inductance": info.get("inductance"),
                "Resistance": info.get("resistance"),
                "Voltage Rating": info.get("voltage"), "Dielectric": info.get("dielectric"),
                "Tolerance": info.get("tolerance"), "Package": info.get("package"),
                "Frequency": info.get("frequency"),
            }
            have = set()
            if ppk > 0:
                have = {p["template"] for p in rows_of(api("GET", "/api/parameter/",
                                                           model_type="part.part", model_id=ppk))}
            for name, val in params.items():
                if val and tpls[name] not in have:
                    W.create("/api/parameter/", {"template": tpls[name], "model_type": "part.part",
                                                 "model_id": ppk, "data": val},
                             f"param {name} = {val}")

        man_name = r["Manufacturer"].strip()
        mp = None
        if ppk > 0:
            mp = find_one("/api/company/part/manufacturer/",
                          lambda o: o["MPN"].upper() == mpn.upper(), part=ppk)
        if not mp:
            man_pk = company(man_name, manufacturer=True)
            mp = W.create("/api/company/part/manufacturer/",
                          {"part": ppk, "manufacturer": man_pk, "MPN": mpn}, f"MPN {man_name} {mpn}")

        sku = r["DigiKey Part #"].strip()
        sp = None
        if ppk > 0:
            sp = find_one("/api/company/part/", lambda o: o["SKU"] == sku, part=ppk)
        if not sp:
            sp = W.create("/api/company/part/", {
                "part": ppk, "supplier": supplier_pk, "SKU": sku, "manufacturer_part": mp["pk"],
                "link": f"https://www.digikey.com/en/products/result?keywords={urllib.parse.quote(sku)}",
                "description": r["Description"].strip(), "primary": True,
            }, f"Digi-Key {sku}")
            price = r["Unit Price"].replace("$", "").replace(",", "").strip()
            if price:
                W.create("/api/company/price-break/", {"part": sp["pk"], "quantity": 1,
                                                       "price": price, "price_currency": CURRENCY},
                         f"price break 1 @ ${price}")

        qty = r["Quantity"].replace(",", "").strip()
        existing_stock = []
        if sp["pk"] > 0:
            existing_stock = rows_of(api("GET", "/api/stock/", supplier_part=sp["pk"]))
        if existing_stock:
            print(f"    = stock already recorded ({sum(float(s['quantity']) for s in existing_stock):g})")
        elif qty and float(qty) > 0:
            if loc_pk is None:
                loc_pk = W.create("/api/stock/location/", {"name": LOCATION},
                                  f"stock location {LOCATION}")["pk"]
            price = r["Unit Price"].replace("$", "").replace(",", "").strip()
            W.create("/api/stock/", {
                "part": ppk, "quantity": qty, "location": loc_pk, "supplier_part": sp["pk"],
                "purchase_price": price or None, "purchase_price_currency": CURRENCY,
            }, f"stock {qty} @ {LOCATION}")

    print(f"\n{len(rows) - len(skipped)} parts processed, {len(skipped)} skipped {skipped or ''}")
    if not commit:
        print("Dry run only. Re-run with --commit to write.")


if __name__ == "__main__":
    main()
