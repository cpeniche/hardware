#!/usr/bin/env python3
"""Idempotent setup of InvenTree for the KiCad HTTP library plugin.

Reads INVENTREE_API_HOST / INVENTREE_API_TOKEN from ~/.config/inventree/env.
Creates parameter templates, points the kicad-library-plugin settings at them,
creates an electronics category tree and registers each category with the plugin.
Safe to re-run: existing objects are found by name and reused.
"""
import json
import os
import urllib.error
import urllib.request

ENV = os.path.expanduser("~/.config/inventree/env")
cfg = dict(l.strip().split("=", 1) for l in open(ENV) if "=" in l and not l.startswith("#"))
HOST = cfg["INVENTREE_API_HOST"].rstrip("/")
TOKEN = cfg["INVENTREE_API_TOKEN"]
PLUGIN = "kicad-library-plugin"


def api(method, path, data=None):
    req = urllib.request.Request(
        HOST + path,
        method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Token {TOKEN}", "Content-Type": "application/json",
                 "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:500]}")


def results(x):
    return x["results"] if isinstance(x, dict) and "results" in x else x


# ---------------------------------------------------------------- parameter templates
TEMPLATES = {
    # name: (units, description)
    "Kicad_Symbol": ("", "KiCad symbol lib_id, e.g. Device:C"),
    "Kicad_Footprint": ("", "KiCad footprint lib_id, e.g. Capacitor_SMD:C_0402_1005Metric"),
    "Kicad_Reference": ("", "KiCad reference designator prefix, e.g. C"),
    "Kicad_Value": ("", "KiCad Value field (generic parts)"),
    "Capacitance": ("F", "Capacitance"),
    "Resistance": ("ohm", "Resistance"),
    "Inductance": ("H", "Inductance"),
    "Voltage Rating": ("V", "Maximum rated voltage"),
    "Tolerance": ("", "Tolerance, e.g. 1% or 0.1pF"),
    "Dielectric": ("", "Capacitor dielectric, e.g. X7R, C0G"),
    "Package": ("", "Package / case code, e.g. 0402"),
    "Frequency": ("Hz", "Frequency"),
}


def ensure_template(name, units, desc):
    found = [t for t in results(api("GET", f"/api/parameter/template/?search={urllib.request.quote(name)}"))
             if t["name"] == name]
    if found:
        return found[0]["pk"]
    t = api("POST", "/api/parameter/template/",
            {"name": name, "units": units, "description": desc, "model_type": "part.part"})
    print(f"  created template {name} (pk {t['pk']})")
    return t["pk"]


print("Parameter templates")
tpl = {n: ensure_template(n, u, d) for n, (u, d) in TEMPLATES.items()}

# ---------------------------------------------------------------- plugin settings
print("Plugin settings")
SETTINGS = {
    "KICAD_SYMBOL_PARAMETER": tpl["Kicad_Symbol"],
    "KICAD_FOOTPRINT_PARAMETER": tpl["Kicad_Footprint"],
    "KICAD_REFERENCE_PARAMETER": tpl["Kicad_Reference"],
    "KICAD_VALUE_PARAMETER": tpl["Kicad_Value"],
    "KICAD_ENABLE_MANUFACTURER_DATA": True,
}
for key, val in SETTINGS.items():
    api("PATCH", f"/api/plugins/{PLUGIN}/settings/{key}/", {"value": val})
    print(f"  {key} = {val}")

# ---------------------------------------------------------------- categories
# (path, default_symbol, default_reference, value_template or None)
CATEGORIES = [
    ("Electronics", None, None, None),
    ("Electronics/Capacitors", "Device:C", "C", "Capacitance"),
    ("Electronics/Resistors", "Device:R", "R", "Resistance"),
    ("Electronics/Inductors", "Device:L", "L", "Inductance"),
    ("Electronics/Ferrite Beads", "Device:FerriteBead", "FB", None),
    ("Electronics/Diodes", "Device:D", "D", None),
    ("Electronics/LEDs", "Device:LED", "D", None),
    ("Electronics/Transistors", "Device:Q_NMOS", "Q", None),
    ("Electronics/ICs", "", "U", None),
    ("Electronics/ICs/Microcontrollers", "", "U", None),
    ("Electronics/ICs/Power", "", "U", None),
    ("Electronics/ICs/RF", "", "U", None),
    ("Electronics/ICs/Memory", "", "U", None),
    ("Electronics/ICs/Interface", "", "U", None),
    ("Electronics/Crystals and Oscillators", "Device:Crystal", "Y", "Frequency"),
    ("Electronics/Connectors", "", "J", None),
    ("Electronics/Switches and Buttons", "", "SW", None),
    ("Electronics/RF and Antennas", "", "AE", None),
    ("Electronics/Protection", "", "D", None),
    ("Electronics/Misc", "", "", None),
]

print("Categories")
existing = {c["pathstring"]: c["pk"] for c in results(api("GET", "/api/part/category/?limit=1000"))}
kicad_cats = {c["category"]["id"]: c["pk"] for c in results(api("GET", f"/plugin/{PLUGIN}/api/category/"))}

for path, sym, ref, valtpl in CATEGORIES:
    if path not in existing:
        parent, _, name = path.rpartition("/")
        c = api("POST", "/api/part/category/", {
            "name": name,
            "parent": existing.get(parent) if parent else None,
            "structural": sym is None,
        })
        existing[path] = c["pk"]
        print(f"  created category {path} (pk {c['pk']})")
    pk = existing[path]
    if sym is None:  # structural root, not served to KiCad
        continue
    payload = {"category": pk, "default_symbol": sym or "", "default_reference": ref or "",
               "default_value_parameter_template": tpl[valtpl] if valtpl else None}
    if pk in kicad_cats:
        api("PATCH", f"/plugin/{PLUGIN}/api/category/{kicad_cats[pk]}/", payload)
    else:
        api("POST", f"/plugin/{PLUGIN}/api/category/", payload)
        print(f"  registered {path} with KiCad plugin")

print("Done.")
