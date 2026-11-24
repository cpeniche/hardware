# Handoff: InvenTree parts inventory setup (from work session, 2026-11-24)

Give this file to Claude Code on the home machine: "Read HANDOFF_InvenTree_setup.md and continue."

## Goal
Self-hosted InvenTree to track the components I own, with KiCad pulling parts from it
(KiCad HTTP library via the InvenTree KiCad plugin), reachable from work and home.

## Decisions already made
- Host at home on the **Proxmox** server (cloud VPS ~$10-12/month rejected as too costly for light use).
- Use a **VM**, not LXC (internet-exposed, Docker-based install, easy snapshots).
  - Debian 12 or Ubuntu 24.04 server, 2 vCPU, 4 GB RAM, 32 GB disk, SSH server enabled.
- **No VPN**: the company network blocks VPNs and we will NOT try to get around that.
  Access from work is through ordinary HTTPS (port 443) via a tunnel from home:
  - Preferred: **Cloudflare Tunnel + Cloudflare Access** (needs a domain, ~$10/year).
  - Free alternative: **Tailscale Funnel** (`*.ts.net` address, Tailscale only on the home VM).
  - Still open: which of the two (do I own a domain?).
- Install InvenTree with the **official Docker Compose** setup.
- Do **not** sync the database through git (binary, unmergeable, risk of corruption and lost edits).
  One central InvenTree; git is used only for:
  - nightly InvenTree data export -> **private** GitHub repo (backup only),
  - the custom KiCad footprint library `User_Footprint` (plain text, versioned in git).
- HTTP library only serves symbols and part data; footprints/3D models must exist on every machine
  (`User_Footprint` library via git clone + same KiCad path variable everywhere).
- Keep KiCad on the same major version everywhere (work machine is still 9.0.8; KiCad 10 PPA is
  `ppa:kicad/kicad-10.0-releases`, upgrade not done yet).
- Check that company policy allows storing company part data on the home server.

## Steps for tonight
1. Create the VM in the Proxmox web UI (specs above), install the OS with SSH enabled.
2. On the home machine, Claude generates an SSH key; I add the public key to the VM user's
   `~/.ssh/authorized_keys` (user needs sudo). Tell Claude the VM's IP.
3. Claude then: installs Docker + InvenTree, enables 2FA / disables sign-up, sets up the
   Cloudflare Tunnel (or Tailscale Funnel), automatic security updates, scheduled backups
   (InvenTree backup -> private GitHub repo / other machine) plus Proxmox snapshots,
   an `update.sh` (backup + update in one command), and the KiCad plugin / `.kicad_httplib` file.
4. Import the breakOutBoard_V2 parts and BOM into InvenTree.

## Related files in this folder (made today)
- `breakOutBoard_V2_BOM_DigiKey_Active.csv`: Digi-Key BOM, active-lifecycle parts, with status/notes
- `breakOutBoard_V2_BOM_Mouser.csv`: converted from the Mouser BOM tool `.xlsx` export, with fixes
- `breakOutBoard_V2_BOM_JLCPCB.csv`: JLCPCB BOM (LCSC Part # column still empty)
- Open items on the board: verify RF match (C14, C15, L1, L2 changed to LQG15HS multilayer inductors);
  C5 and C9/C10 lifecycle status not confirmed; C11 ("N/C") should be marked DNP in the schematic;
  J2 is the Mini PCIe card edge (not a purchased part); JLCPCB CPL (pick-and-place) file not yet made.
