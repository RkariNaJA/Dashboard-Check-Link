# Link Watch

**An internal link dashboard: how often each company link is used, by how many people, and whether it is up right now.**

<p>
  <img alt="Python 3.9+" src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white">
  <img alt="Flask 3" src="https://img.shields.io/badge/Flask-3-000000?logo=flask&logoColor=white">
  <img alt="SQLite" src="https://img.shields.io/badge/SQLite-single%20file%20db-003B57?logo=sqlite&logoColor=white">
  <img alt="APScheduler" src="https://img.shields.io/badge/APScheduler-health%20checks-6E4C13">
  <img alt="Jinja2 + plain HTML/CSS" src="https://img.shields.io/badge/Jinja2-plain%20HTML%20%2F%20CSS-B41717?logo=jinja&logoColor=white">
  <img alt="No internet required" src="https://img.shields.io/badge/runs-fully%20on--premises-0A7EA4">
  <img alt="Status: internal deployment" src="https://img.shields.io/badge/status-internal%20deployment-success">
</p>

Built for the IT team at **Hi-Tech Apparel**. It hands out short links for the internal apps,
counts every click, and pings each destination every few minutes so a broken tool is noticed before
someone reports it. Runs on your own server — no internet, no external service, nothing leaves the
network.

---

## The problem it solves

Internal apps get shared as raw addresses like `http://SERVERNAME:5000/`, and after that nobody
knows anything: is this tool actually used? by how many people? has it been down since Tuesday?

Link Watch answers all three by **putting itself in front of the door** — the same trick bit.ly
uses:

```
You share:                 http://SERVERNAME:8090/go/hr-form
                                        |
                                        v   (the click is counted here)
User instantly lands on:   http://SERVERNAME:5000/   (your real app)
```

1. Add a link in the dashboard → you get a short link like `.../go/hr-form`
2. Share the **short** link (email, Teams, intranet page)
3. Every click is recorded — total clicks and how many different people (by IP) — then the visitor
   is forwarded on in a few milliseconds
4. Separately, every destination is pinged on a timer, giving Online / Down and a response time

Your apps need **no changes at all** — the dashboard never touches them, it just owns the address
people click.

---

## What the dashboard shows

| Column | Meaning |
|---|---|
| **Status** | 🟢 Online / 🔴 Down — checked every 5 minutes |
| **Response** | how fast the link answered, in milliseconds |
| **Today** | clicks today / how many different people today |
| **Total** | all-time clicks / all-time different people |
| **Last 14 days** | a small bar chart of clicks per day |
| **Last checked** | when the last health check ran |

The page refreshes itself every minute, and **Check now** forces an immediate health check.

---

## Features

- **Short links that count clicks** — a `302` redirect on purpose, so browsers keep coming back
  through the dashboard instead of remembering the destination and skipping the count.
- **People, not just hits** — clicks are stored per day *and* per IP, so "12 clicks by 4 people"
  is a question the database can answer.
- **Health checks on a timer** — every destination is called directly (never through the company
  proxy, which would answer instead of the real app and fake the result).
- **Pause without breaking bookmarks** — a paused link stops counting and checking but keeps
  forwarding, so nobody's saved link dies.
- **Tiny, backup-friendly storage** — one SQLite file, `data.db`. Storing one row per day + IP
  instead of one row per click keeps it small forever.
- **No internet needed** — plain HTML + CSS pages, no JS framework, no CDN. Works on a server with
  no outside access.

---

## Running it

```bash
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:8090**.

The first run creates `data.db`, and with `sample_mode = true` in `config.ini` it also creates 4
demo links with fake click history so you can try everything safely. Set `sample_mode = false` on
the real server.

> The dashboard has to stay running for clicks to be counted — the short links are served by it.
> On a server, start it automatically (Task Scheduler recipe in the guide).
>
> Clicks are only counted through the **short** link. Opening the destination address directly is
> invisible to the dashboard.

---

## Under the hood

| | |
|---|---|
| **Built with** | Python + [Flask](https://flask.palletsprojects.com/) 3, SQLite (built in), [APScheduler](https://apscheduler.readthedocs.io/) for the 5-minute checks, [requests](https://requests.readthedocs.io/) for the pings, Jinja2 templates |
| **The app** | [`app.py`](app.py) — pages, the `/go/<slug>` counting redirect, the scheduler |
| **Health checks** | [`collector.py`](collector.py) — pings every link, saves status + response time |
| **Storage** | [`db.py`](db.py) — 3 tables: `links`, `daily_hits` (per day + IP), `checks` (kept 30 days) |
| **Settings** | [`config.ini`](config.ini) — port, check interval, timeout, SSL verification, sample mode |

Default port is **8090** (8080 is taken by another app on the dev machine). Changing the port
changes every short link, so pick it before sharing links with people.

---

## Documentation

**[docs/DEVELOPER-GUIDE.md](docs/DEVELOPER-GUIDE.md)** — the full guide: adding and checking
links, changing the port (and checking it is free), opening the firewall, deploying to the server,
starting automatically after a reboot, every `config.ini` setting, how the counting and health
checks work internally, the database design, and troubleshooting.

---

<sub>Internal tool for HI-TECH APPAREL · `data.db` holds all links and click history and is
excluded by `.gitignore` — back it up by copying that one file.</sub>
