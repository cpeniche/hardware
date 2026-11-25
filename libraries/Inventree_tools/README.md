# InvenTree + KiCad setup

Self-hosted InvenTree (Docker on a Proxmox VM) reached through Tailscale Funnel at
`https://inventree.tail317ee5.ts.net`. KiCad reads parts from it through the
**inventree-kicad-plugin** (slug `kicad-library-plugin`) as an HTTP library.

Tokens are never stored in this repo. Each computer gets its own token.

## Connect KiCad on a new computer

KiCad 10 is used here. The symbol libraries in `Kicad_Library` are saved in KiCad 10
format, so KiCad 9 cannot open some of them.

### 1. Get an API token
In the InvenTree web UI: user menu → **Account settings** → **Access tokens** →
create a token, named after the computer (e.g. `kicad-home`). Copy it.

### 2. Create the HTTP library file
Save as `~/.local/share/kicad/InvenTree.kicad_httplib` (keep it **out of the NFS share
and out of git**), then `chmod 600` it:

```json
{
    "meta": { "version": 1.0 },
    "name": "InvenTree",
    "description": "Parts from InvenTree",
    "source": {
        "type": "REST_API",
        "api_version": "v1",
        "root_url": "https://inventree.tail317ee5.ts.net/plugin/kicad-library-plugin",
        "token": "PASTE-TOKEN-HERE",
        "timeout_parts_seconds": 60,
        "timeout_categories_seconds": 600
    }
}
```

The two timeouts are how long KiCad caches part details (60 s) and the part list of each
category (600 s). Restarting KiCad clears both.

### 3. Add it to KiCad
KiCad → **Preferences → Manage Symbol Libraries → Global Libraries** → add a row:
- Nickname: `InvenTree`
- Library Path: the full path to the `.kicad_httplib` file
- Library Format: **HTTP**

### 4. Make the local libraries available
The HTTP library only sends symbol and footprint *names* (e.g. `_Misc_Symbols:MPQ6524GRE-AEC1-Z`,
`User_Footprint:...`). The libraries with those nicknames must exist on every computer:

- **Preferences → Configure Paths**:
  - `KICAD_USER_SYMBOLS_DIR` = `<NFS>/Projects/hardware/libraries/Kicad_Library/symbols`
  - `KICAD_USER_FOOTPRINT_DIR` = `<NFS>/Projects/hardware/libraries/Kicad_Library/footprints`
- **Manage Symbol Libraries**: add every `.kicad_sym` from that symbols folder with its file
  name as nickname (`_Connectors`, `_Misc_Symbols`, `_MCU_NORDIC`, `Cards`, `con-hirose-df40`, ...).
- **Manage Footprint Libraries**: `User_Footprint` → `${KICAD_USER_FOOTPRINT_DIR}/User_Footprint.pretty`,
  plus the standard KiCad libraries.

### 5. Test
Open the schematic editor → **Place Symbol** → the `InvenTree` library shows the categories
(Electronics/Capacitors, ...). Placing a part fills Value, Footprint, Datasheet, MPN, etc.

Placed parts don't update by themselves: use **Tools → Update Symbols from Library**.

## Troubleshooting

**KiCad shows no parts / the library is empty.** The plugin's URLs sometimes drop out of
the server: they return the login web page instead of JSON, even with a valid token. Fix:

```bash
python3 -c "from import_digikey_csv import api; api('POST','/api/plugins/reload/',{'full_reload':True,'force_reload':True,'collect_plugins':True})"
```

then restart KiCad. Check with:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Token $TOKEN" \
  https://inventree.tail317ee5.ts.net/plugin/kicad-library-plugin/v1/categories.json
```

`200` is fine; `302` means the plugin dropped out. The cause is not found yet. Check
`docker compose logs inventree-server` and that `inventree-data/plugins.txt` lists
`inventree-kicad-plugin`.

**Value shows "50 VV"**: plugin setting `KICAD_INCLUDE_UNITS_IN_PARAMETERS` must be False.

**Phone browser redirects to "install the app"**: add to the server's `config.yaml`, then
restart `inventree-server`:

```yaml
frontend_settings:
  mobile_mode: allow-always
```

## Scripts

All scripts read `~/.config/inventree/env` (mode 600, not in git):

```
INVENTREE_API_HOST=https://inventree.tail317ee5.ts.net
INVENTREE_API_TOKEN=...
```

Only needed on a computer that runs the scripts; KiCad doesn't use it.
All scripts dry-run by default; add `--commit` to write. Re-running is safe.

| Script | What it does |
|---|---|
| `setup_kicad_plugin.py` | Parameter templates (`Kicad_Symbol`, `Kicad_Footprint`, `Kicad_Reference`, `Kicad_Value`, ...), plugin settings, category tree, publishes categories to KiCad. |
| `import_digikey_csv.py FILE.csv` | Imports a Digi-Key order/product CSV: part, KiCad parameters, manufacturer part, Digi-Key supplier part with price, stock in `Home Lab`. Classifies `CAP CER`, `FIXED IND`, `RES`; anything else needs an entry in `OVERRIDES`. Existing parts only get Digi-Key data and stock. |
| `import_kicad_symbols.py` | Creates parts from the own symbol libraries listed in `INCLUDE_LIBS` (no stock). Writes `symbols_import_review.csv` with footprint issues. |
| `fill_datasheets.py` | Adds a `datasheet` attachment from the part link. KiCad's Datasheet field comes from an attachment whose comment is `datasheet`. |

## How fields reach KiCad

- Symbol / Footprint / Reference / Value: part parameters `Kicad_Symbol`, `Kicad_Footprint`,
  `Kicad_Reference`, `Kicad_Value`. A category's footprint mapping (plugin category settings)
  overrides `Kicad_Footprint`. The Resistors category maps `Package` → footprint.
- Datasheet: attachment with comment `datasheet`.
- `Manufacturer_N` / `MPN_N` / `Supplier_N_M` / `SPN_N_M`: manufacturer and supplier parts.
- `InvenTree`: the part's id (pk) in InvenTree.
