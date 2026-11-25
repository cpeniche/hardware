#!/usr/bin/env python3
"""Give every part that has a datasheet URL in its Link field a 'datasheet' attachment,
so KiCad's Datasheet field is filled.

Usage:
    python3 fill_datasheets.py            # dry run
    python3 fill_datasheets.py --commit   # write

Parts that already have a 'datasheet' attachment are left alone. Re-running is safe.
Parts without a Link are listed at the end: add their datasheet by hand
(part -> Attachments -> add link or upload PDF, comment "datasheet").
"""
import sys

from import_digikey_csv import Writer, api, ensure_datasheet, rows_of


def main():
    commit = "--commit" in sys.argv
    W = Writer(commit)
    print(("COMMIT" if commit else "DRY RUN (nothing written; add --commit)") + "\n")

    parts = rows_of(api("GET", "/api/part/", limit=5000))
    has_ds = {a["model_id"] for a in rows_of(api("GET", "/api/attachment/", limit=5000,
                                                 model_type="part"))
              if (a.get("comment") or "").strip().lower() == "datasheet"}
    added, missing = 0, []
    for p in sorted(parts, key=lambda p: p["name"]):
        if p["pk"] in has_ds:
            continue
        if (p.get("link") or "").startswith("http"):
            print(p["name"])
            ensure_datasheet(W, p["pk"], p["link"])
            added += 1
        else:
            missing.append(p["name"])
    print(f"\n{added} datasheet attachments {'added' if commit else 'to add'}; "
          f"{len(has_ds)} parts already had one; {len(missing)} parts have no URL:")
    print("  " + ", ".join(missing))
    if not commit:
        print("Dry run only. Re-run with --commit to write.")


if __name__ == "__main__":
    main()
