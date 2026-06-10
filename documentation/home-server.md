# Home Server Runbook — Postgres + Ollama on a Windows PC

This guide turns a Windows home PC into the platform's "always-on" backend: it hosts the
PostgreSQL database and a local LLM (Ollama), and your Mac connects to both over
[Tailscale](https://tailscale.com) — a private, encrypted network between your own devices.

Wherever you see `home-pc.<tailnet>.ts.net`, replace it with **your** PC's MagicDNS name
(you'll find it in Section 2).

---

## 1. What runs where

| Machine | Runs | Why |
|---------|------|-----|
| **Mac** (this repo) | The `findb` CLI, `.env` with API keys, OAuth token files | Where you work day to day. |
| **Windows PC** | PostgreSQL 16 (Docker Desktop) + Ollama (native Windows app) | Always on; stores the data and runs the local LLM. |
| **Tailscale** (both) | Encrypted private network between the two | Lets the Mac reach the PC from anywhere, with no port-forwarding. |

```
   Mac (laptop)                          Windows PC (home server)
 ┌──────────────────┐    Tailscale     ┌───────────────────────────┐
 │ uv run findb ... │  (WireGuard,     │ Docker Desktop            │
 │ .env:            │   encrypted)     │  └─ findb-db  :5432       │
 │  DATABASE_URL ───┼──────────────────┼──► Postgres 16            │
 │  OLLAMA_BASE_URL─┼──────────────────┼──► Ollama     :11434      │
 └──────────────────┘                  └───────────────────────────┘
        home-pc.<tailnet>.ts.net  ◄── MagicDNS name of the PC
```

Your **local dev database** (repo-root `docker-compose.yml`, container `xmarket-db`)
stays exactly as it is. The home server runs a *separate* Postgres with user/db `findb`,
so you can switch between them with a one-line `.env` change (Section 6).

---

## 2. One-time Windows PC setup

Do these once, on the Windows PC:

| Step | What to do |
|------|------------|
| 1. Install Docker Desktop | Download from [docker.com](https://www.docker.com/products/docker-desktop/). During setup, keep the default **WSL 2 backend** enabled (it's the checkbox in the installer). Reboot if asked. |
| 2. Install Tailscale | Download from [tailscale.com/download](https://tailscale.com/download). Log in with the **same account/tailnet** you use on the Mac. |
| 3. Find the PC's MagicDNS name | On the Mac, run `tailscale status` — the PC appears in the list. Or open the [Tailscale admin console](https://login.tailscale.com/admin/machines) and copy the machine's full name, e.g. `home-pc.tail1234.ts.net`. |

Quick check from the Mac (PC must be online):

```bash
tailscale ping home-pc.<tailnet>.ts.net
```

You should see `pong` replies. If not, see Troubleshooting (Section 10).

---

## 3. Start the database

On the Windows PC:

1. Get the `deploy/home-server/` folder onto the PC — either `git clone` this repo there,
   or just copy that one folder (USB, network share, whatever is easy).
2. In that folder, copy `.env.example` to `.env` and replace
   `change-me-strong-password` with a strong password. Save it somewhere safe — the Mac
   needs it in Section 6.
3. Open a terminal (PowerShell) in the folder and start it:

```powershell
cd path\to\deploy\home-server
copy .env.example .env      # then edit .env and set the password
docker compose up -d
```

4. Verify it's running and healthy:

```powershell
docker compose ps
```

You should see `findb-db` with status `Up ... (healthy)`. Data lives in a named Docker
volume (`findb_pgdata`), so it survives restarts and `docker compose down`.

---

## 4. Windows Firewall — allow the Mac in

By default, Windows blocks inbound connections to ports 5432 (Postgres) and 11434
(Ollama). Open them **only for Tailscale addresses** (`100.64.0.0/10` is the special
address range Tailscale uses), so nothing is reachable from your normal LAN or the
internet.

Open **PowerShell as Administrator** and run:

```powershell
New-NetFirewallRule -DisplayName "findb Postgres (Tailscale only)" `
  -Direction Inbound -Protocol TCP -LocalPort 5432 `
  -RemoteAddress 100.64.0.0/10 -Action Allow

New-NetFirewallRule -DisplayName "Ollama (Tailscale only)" `
  -Direction Inbound -Protocol TCP -LocalPort 11434 `
  -RemoteAddress 100.64.0.0/10 -Action Allow
```

> **Note:** Docker Desktop publishes container ports on `0.0.0.0` (all interfaces), so
> the Postgres port is already listening for the Tailscale interface — no special Docker
> networking needed. The firewall rules above are what scope access down to your tailnet.

---

## 5. Ollama on the Windows PC

1. Install the Windows app from [ollama.com/download](https://ollama.com/download).
2. By default Ollama only listens on `localhost` — the Mac couldn't reach it. Fix that
   with a system environment variable:
   - Press **Win**, type "environment variables", open **Edit the system environment
     variables** → **Environment Variables…**
   - Under *System variables*, click **New…**: name `OLLAMA_HOST`, value `0.0.0.0`.
   - Quit Ollama from the system tray and start it again (so it picks up the variable).
3. Pull a model (example — pick whatever fits your GPU/RAM):

```powershell
ollama pull qwen3:14b
```

4. Test from the **Mac** — this proves Tailscale, the firewall rule, and `OLLAMA_HOST`
   are all correct:

```bash
curl http://home-pc.<tailnet>.ts.net:11434/v1/models
```

You should get JSON listing `qwen3:14b`.

---

## 6. Point the Mac at the home server

Edit the repo's `.env` on the Mac (use the password from Section 3):

```bash
# Home server (over Tailscale)
DATABASE_URL=postgresql://findb:<password>@home-pc.<tailnet>.ts.net:5432/findb
OLLAMA_BASE_URL=http://home-pc.<tailnet>.ts.net:11434
```

To use the local LLM for enrichment, set a model with the `ollama:` prefix, e.g.
`SENTIMENT_MODEL=ollama:qwen3:14b` (bare model names default to Anthropic).

Smoke test:

```bash
uv run findb migrate-status
```

If it connects, you're done — on a fresh database it will list every migration as
pending; run `uv run findb migrate` to apply them (or restore your existing data first,
Section 7). Optional direct check:

```bash
psql "postgresql://findb:<password>@home-pc.<tailnet>.ts.net:5432/findb" -c '\conninfo'
```

**Switching back to local dev** is one line — set `DATABASE_URL` back to
`postgresql://xmarket:xmarket@localhost:5432/xmarket` (and remove/comment
`OLLAMA_BASE_URL` if you want Anthropic-only).

---

## 7. One-time data migration from the Mac's local DB

Copy everything from the local `xmarket` database into the home server's `findb`
database. Run on the Mac (local `xmarket-db` container must be running):

```bash
# 1. Dump the local DB to a file (custom format, good for pg_restore)
docker exec xmarket-db pg_dump -U xmarket -d xmarket -Fc > xmarket.dump

# 2. Restore it into the home server (prompts for the findb password)
pg_restore -h home-pc.<tailnet>.ts.net -U findb -d findb --no-owner --no-privileges xmarket.dump

# 3. Confirm the schema is fully migrated
uv run findb migrate-status   # should show none pending
```

`--no-owner --no-privileges` is needed because the dump was made as user `xmarket` but
the new database is owned by `findb`. The `xmarket.dump` file is a local artifact — you
can delete it once the restore succeeds.

---

## 8. Keep it running

| What | How |
|------|-----|
| Don't let the PC sleep | **Settings → System → Power & battery → Screen and sleep** → set "When plugged in, put my device to sleep after" to **Never**. (Screen off is fine; sleep is not.) |
| Docker starts on login | Docker Desktop → **Settings → General → "Start Docker Desktop when you log in"**. |
| Postgres restarts itself | Already handled — the compose file sets `restart: unless-stopped`, so the container comes back whenever Docker does. |
| Tailscale stays up | Tailscale installs as a Windows service, so it reconnects automatically after reboots. |
| Ollama starts on login | The Windows app registers itself to start at login by default; check the system tray after a reboot. |

---

## 9. Security notes

- **Tailscale is the perimeter.** All traffic between Mac and PC travels inside a
  WireGuard-encrypted tunnel; only devices logged into *your* tailnet can even route to
  the PC's `100.x` address.
- **The DB password still matters.** Postgres 16 uses `scram-sha-256` auth by default,
  so connections require the password even inside the tailnet — defense in depth.
- **Nothing is exposed to the public internet.** No router port-forwarding, no public
  IP, and the firewall rules (Section 4) only accept traffic from the Tailscale range
  `100.64.0.0/10` — your home LAN can't reach these ports either.

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `connection refused` on 5432 | Docker/container not running, or firewall rule missing | On the PC: `docker compose ps` (start Docker Desktop if needed); re-check Section 4 rules with `Get-NetFirewallRule -DisplayName "*Tailscale only*"`. |
| `could not translate host name` / unknown host | Tailscale down on either machine, or wrong MagicDNS name | Run `tailscale status` on the Mac — is the PC listed and online? Copy the exact name from there. Check the PC's tray icon says Connected. |
| `curl` to `:11434` hangs or refuses | `OLLAMA_HOST=0.0.0.0` not set, or Ollama not restarted after setting it | Re-do Section 5 step 2; confirm on the PC with `curl http://localhost:11434/v1/models` first, then from the Mac. |
| Queries noticeably slower than local | Expected — every query crosses the internet (Tailscale adds little, but your home upload speed matters) | Normal for a remote DB. Batchy CLI work is fine; if it hurts, switch `DATABASE_URL` back to local dev for that session. |
| `password authentication failed` | Password in the Mac's `DATABASE_URL` doesn't match `deploy/home-server/.env` on the PC | Fix the URL, or change the PC's `.env` and recreate: `docker compose down && docker compose up -d` (volume keeps old password's data — see note below). |

> **Changing the Postgres password later:** the `POSTGRES_PASSWORD` env var only applies
> on *first* initialization of the volume. To change it afterwards, run on the PC:
> `docker exec -it findb-db psql -U findb -d findb -c "ALTER USER findb WITH PASSWORD 'new-password';"`
> then update both `.env` files.
