# Handoff: InvenTree parts inventory setup

Give this file to Claude Code on another machine: "Read HANDOFF_InvenTree_setup.md and continue."

- 2026-11-24 (work): planning, decisions, BOM files.
- 2026-11-24/25 (home): VM built, InvenTree running, public via Tailscale Funnel, backups + update script.
  Stages 1-10 done except the GitHub off-site copy (10b) and the Proxmox backup job (10c).

No passwords, keys or 2FA details are in this file on purpose.

## Goal
Self-hosted InvenTree to track the components I own, with KiCad pulling parts from it
(KiCad HTTP library via the InvenTree KiCad plugin), reachable from work and home.

## How to reach it
| What | Address | Where from |
|---|---|---|
| InvenTree web UI / API (public) | **https://inventree.tail317ee5.ts.net** | anywhere, incl. work (plain HTTPS 443) |
| InvenTree web UI (LAN) | http://inventree.home.net | home LAN only |
| SSH to the VM | `ssh inventree` (user `carlo`, key `~/.ssh/id_ed25519_inventree`, host `inventree.home.net` / 10.10.0.127) | **home LAN only**, keys only |

- Login: user `carlo` (superuser), password + TOTP 2FA (enforced for all users).
  Don't tick "Trust this browser" on shared/work machines.
- From work there is **no SSH** (Funnel publishes only HTTPS; no VPN/Tailscale on the work PC by policy).
  So server maintenance (backups, updates, `.env`) happens from home; web UI work can happen anywhere.
- SSH config entry on the home machine (`~/.ssh/config`):
  `Host inventree / HostName inventree.home.net / User carlo / IdentityFile ~/.ssh/id_ed25519_inventree / IdentitiesOnly yes`

## Decisions (made and done)
- Host at home on **Proxmox**, in a **VM** (not LXC). Cloud VPS rejected (cost).
- **No VPN** from work (company blocks it, we don't get around it). Access is ordinary HTTPS.
- Tunnel: **Tailscale Funnel** (free, no domain). Cloudflare Tunnel was the alternative; not used (no domain).
  Tailscale runs only on the VM. Key expiry disabled for the VM in the Tailscale admin console.
- InvenTree via the **official Docker Compose** setup (Docker CE from Docker's repo). Podman was considered and
  rejected: not officially supported by InvenTree.
- The database is **not** synced through git. One central InvenTree; git only for the nightly export backup
  and for the `User_Footprint` KiCad library.
- HTTP library serves symbols and part data only; footprints/3D models must exist on every machine
  (`User_Footprint` via git clone + same KiCad path variable everywhere).
- Keep KiCad on the same major version everywhere (work PC is still 9.0.8; KiCad 10 PPA is
  `ppa:kicad/kicad-10.0-releases`, upgrade not done yet).
- Still to check: that company policy allows company part data on the home server (before importing work parts).

## The VM
- Proxmox VM `inventree`: Debian 13 (trixie), 2 vCPU, 4 GB RAM, 32 GB disk (LVM, one partition),
  VirtIO SCSI single + IO thread + discard, qemu-guest-agent installed.
- Hardening:
  - Automatic security updates (`unattended-upgrades`, Debian + Debian-Security origins).
  - `ufw`: deny incoming; allow 22/tcp and 80,443/tcp from 10.10.0.0/24 only.
  - SSH: `/etc/ssh/sshd_config.d/10-hardening.conf`: no password/keyboard-interactive login, no root login.
  - `carlo` has passwordless sudo (`/etc/sudoers.d/90-carlo`), needed so Claude can administer it.
    (It was accidentally also created on the home machine OptiPlex-790 and removed again there.)
- Proxmox snapshots taken along the way (names like `clean_os` ... `funnel_working`).

## InvenTree install (`~/inventree` on the VM)
- Version at setup: **InvenTree 1.7.4** (API 570), `INVENTREE_TAG=stable`.
- Containers: `inventree-db` (PostgreSQL 17), `inventree-cache` (Redis), `inventree-server`, `inventree-worker`,
  `inventree-proxy` (Caddy). All data in `~/inventree/inventree-data/`.
- Changes from the official files (backups of the originals: `.env.bak-pre-tailscale`, `Caddyfile.bak-pre-tailscale`):
  - `.env`:
    - `INVENTREE_SITE_URL="https://inventree.tail317ee5.ts.net"`
    - `INVENTREE_ALLOWED_HOSTS=inventree.home.net,127.0.0.1,localhost` (127.0.0.1/localhost are needed by
      Caddy's health check; without them the proxy shows "unhealthy")
    - `INVENTREE_TRUSTED_ORIGINS=http://inventree.home.net`
    - `INVENTREE_HTTPS_PORT=127.0.0.1:8443` (Tailscale owns 443 on the tailnet IP; Caddy's 443 is kept off the network)
    - DB password changed from the default (random, 48 chars). `.env` is mode 600.
  - `Caddyfile`: site address changed to `http:// {` (Tailscale does TLS), plus a global
    `servers { trusted_proxies static 172.16.0.0/12 127.0.0.1/32 }` block so the https X-Forwarded-Proto header
    from Tailscale is passed through.
- Funnel: `sudo tailscale funnel --bg 80` (persists across reboots). Check with `tailscale funnel status`.
- InvenTree security settings (System Settings -> **Authentication**, called "Login" in older docs):
  Enforce MFA on, registration off, SSO off, password-forgot off (no email server configured).
  Server instance name: `KiCad Parts`.
- Gotcha learned: with "Trust this browser" ticked, a browser skips the 2FA code; when Enforce MFA was then
  switched on, that browser looped to the MFA setup page. Fix: delete the site's cookies, log in without "trust".
  If ever locked out, Enforce MFA can be switched off from the VM:
  `docker compose exec -w /home/inventree/src/backend/InvenTree inventree-server python manage.py shell -c "from common.models import InvenTreeSetting as S; S.set_setting('LOGIN_ENFORCE_MFA', False, None)"`

## Backups and updates (Stage 10)
- `~/inventree/backup.sh`, run nightly ~03:00 by systemd `inventree-backup.timer` (Persistent=true):
  1. `invoke backup --clean`: database + media -> `~/inventree/inventree-data/backup/`
     (restore: `docker compose run --rm inventree-server invoke restore`). Verified: valid PostgreSQL dump.
  2. Config bundle (`.env`, Caddyfile, compose file, config.yaml, secret_key.txt, oidc.pem, plugins.txt)
     -> `~/inventree-backups/config/` (mode 700/600, kept 30 days). Contains secrets, never leaves the VM.
  3. If `~/inventree-export` is a git repo: `invoke export-records` JSON (with `mfa.authenticator` and
     `oauth2_provider.application` records removed) + mirrored media -> commit + push. Skipped until 10b is done.
  - Logs: `journalctl -u inventree-backup.service -n 20`
- `~/inventree/update.sh`: backup -> apt full-upgrade -> `docker compose pull` -> down ->
  `invoke update` (migrations) -> up -d -> prune images -> wait for healthy -> says if a reboot is needed.
  Tested once successfully.

## Open items / next steps
1. **10b GitHub off-site copy (from home):** create a **private** repo (e.g. `inventree-backup`, empty),
   add the VM's deploy key with **write access** (public key: `~/.ssh/github_inventree_backup.pub` on the VM),
   then Claude (at home) sets up `~/inventree-export` as a clone of it and tests a push.
2. **10c Proxmox backup job:** Datacenter -> Backup -> Add, VM `inventree`, storage = NFS/other disk
   (not local-lvm), weekly, mode Snapshot, keep last 4. Snapshots alone are not backups.
3. **Test login from the work PC** at https://inventree.tail317ee5.ts.net (if blocked, note the exact message;
   some company filters block `ts.net`).
4. **Stage 11 KiCad (can be done from work):** install the InvenTree KiCad plugin (Admin Center -> Plugins),
   create an API token for KiCad, write the `.kicad_httplib` file pointing at
   `https://inventree.tail317ee5.ts.net`, add it as a symbol library in KiCad on each machine.
5. **Stage 12 (can be done from work):** import the breakOutBoard_V2 parts and BOM into InvenTree
   (after the company-policy check, if any parts are company data).

## Related files in this folder
- `bom/breakOutBoard_V2_BOM_DigiKey_Active.csv`: Digi-Key BOM, active-lifecycle parts, with status/notes
- `bom/breakOutBoard_V2_BOM_Mouser.csv` (+ `.xlsx` original): converted from the Mouser BOM tool export, with fixes
- `bom/breakOutBoard_V2_BOM_JLCPCB.csv`: JLCPCB BOM (LCSC Part # column still empty)
- Open items on the board: verify RF match (C14, C15, L1, L2 changed to LQG15HS multilayer inductors);
  C5 and C9/C10 lifecycle status not confirmed; C11 ("N/C") should be marked DNP in the schematic;
  J2 is the Mini PCIe card edge (not a purchased part); JLCPCB CPL (pick-and-place) file not yet made.
