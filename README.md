# Link Watch — Internal Link Dashboard — Hi-Tech Apparel

A small web dashboard for the IT team that tracks **how many times each
internal link is used**, **how many people used it**, and whether each link
is **online or down** (with response time).

Built with Python (Flask) + SQLite. Everything runs and stays on your own
server — no internet needed, nothing leaves your network.

---

## How it works (important to understand)

The dashboard counts clicks using **short links**:

```
You share:                 http://SERVERNAME:8090/go/hr-form
                                        |
                                        v   (dashboard counts the click)
User instantly lands on:   http://SERVERNAME:5000/   (your real app)
```

1. You add a link in the dashboard → it creates a short link like
   `http://SERVERNAME:8090/go/hr-form`
2. You share the **short link** with users (email, Teams, intranet page)
3. Every click is counted (clicks + unique people by IP address), then the
   user is instantly forwarded to the real destination
4. Separately, the dashboard **pings every destination every 5 minutes** to
   show Online / Down status and response time

> ⚠️ **The dashboard must be running for clicks to be counted.** If it is
> stopped, the short links stop working too. On the server, set it up to
> start automatically (see "Start automatically" below).
>
> ⚠️ Clicks are only counted when people use the **short** link. If someone
> opens the destination address directly, that visit is not counted.

---

## How to run

```
cd "DashBoard Check link"
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:8090** in your browser.

The first run creates `data.db` (the database) and, if `sample_mode = true`,
4 demo links with fake click history so you can try everything safely.

---

## How to add and check a link

### Add a link
1. Open **Manage links** (top right)
2. Fill in **Name** (e.g. `HR Request Form`) and **Destination URL**
   (your real app, e.g. `http://SERVERNAME:5000/`)
3. *(Optional)* choose a **Short name** — otherwise it is made from the name
4. Click **Add link**, then press **Copy** next to the new short link and
   share it with your users

### Check a link
Open the **Dashboard** page. For every link you see:

| Column | Meaning |
|---|---|
| **Status** | 🟢 Online / 🔴 Down (checked every 5 minutes) |
| **Response** | How fast the link answered (milliseconds) |
| **Today** | Clicks today / how many different people today |
| **Total** | All-time clicks / all-time different people |
| **Last 14 days** | Small chart of clicks per day (hover a bar for the number) |
| **Last checked** | When the last health check ran |

The page refreshes itself every minute. Press **Check now** to force an
immediate health check.

### Pause / Delete
- **Pause** — stops counting and health checks, but the short link **keeps
  forwarding**, so nobody's saved bookmark breaks. **Resume** turns it back on.
- **Delete** — removes the link **and all of its click history**.

---

## How to check and change the port

The dashboard uses **port 8090** by default (set in `config.ini`):

```ini
[server]
host = 0.0.0.0
port = 8090
```

### Is the port free? (before starting)
Open PowerShell on the machine and run:

```powershell
netstat -ano | findstr :8090
```

- **No output** → the port is free, you are good.
- **Lines with `LISTENING`** → something already uses that port. The last
  number on the line is the process ID; to see which program it is:

  ```powershell
  Get-Process -Id <that number>
  ```

  Then either close that program or pick another port (e.g. `8091`) in
  `config.ini` and restart the dashboard.

> Note: port **8080 is already used by another Python app on the
> development PC** — that is why this project uses 8090.

### After changing the port
- Restart the dashboard (`Ctrl+C`, then `python app.py` again)
- **All short links change too** (they contain the port!), so only change
  the port before you start sharing links with users
- Tell the team the new address: `http://SERVERNAME:<new port>`

### Can't reach it from another PC?
If `http://SERVERNAME:8090` doesn't open from a colleague's machine, Windows
Firewall on the server is probably blocking it. Allow the port (run as
Administrator on the server):

```powershell
New-NetFirewallRule -DisplayName "Link Watch Dashboard" -Direction Inbound -Protocol TCP -LocalPort 8090 -Action Allow
```

---

## Deploy to the server

1. Copy this whole folder to the server
2. In `config.ini` set:
   ```ini
   sample_mode = false
   ```
3. On the server:
   ```
   pip install -r requirements.txt
   python app.py
   ```
4. Delete the demo links (Manage links → Delete) and add your real ones
5. Team opens `http://SERVERNAME:8090`

### Start automatically after a reboot (recommended)
1. On the server open **Task Scheduler** → **Create Task…**
2. **General** tab: name it `Link Watch`, tick **Run whether user is logged
   on or not**
3. **Triggers** tab: New → Begin the task: **At startup**
4. **Actions** tab: New →
   - Program/script: `python`
   - Add arguments: `"C:\path\to\DashBoard Check link\app.py"`
   - Start in: `C:\path\to\DashBoard Check link`
5. OK → enter the account password. Done — the dashboard now survives reboots.

---

## Configuration reference (`config.ini`)

| Setting | Meaning | Default |
|---|---|---|
| `[server] host` | `0.0.0.0` = reachable by the whole network | `0.0.0.0` |
| `[server] port` | Port of the dashboard **and** of every short link | `8090` |
| `[health] check_interval_minutes` | How often links are pinged | `5` |
| `[health] timeout_seconds` | How long to wait before calling a link Down | `10` |
| `[health] ssl_verify` | `false` accepts self-signed internal certificates | `false` |
| `[app] sample_mode` | `true` = create demo links/history when database is empty. Set `false` on the server | `true` |

---

## How it's built — behind the scenes

### The trick: the dashboard doesn't "listen" to your links at all

A common first idea is that the dashboard somehow watches the network or
spies on your apps. It doesn't — that would be complicated and unreliable.
Instead, the dashboard **puts itself in front of the door**:

```
user clicks short link
        |
        v
http://SERVER:8090/go/hr-form      <- this address BELONGS to the dashboard,
        |                             so the click arrives at the dashboard first
        |  1. look up "hr-form" in the database
        |  2. save one row: which link, which day, which IP  (+1 click)
        |  3. answer "302 - go here instead: http://SERVER:5000/"
        v
user's browser jumps to the real app (takes a few milliseconds)
```

So the dashboard never touches your Python/React apps and needs no changes
to them. It simply owns the address people click, counts the visit, and
passes the visitor along. This is exactly how bit.ly and TinyURL work.

The code for this is one small function in `app.py` (route `/go/<slug>`):
find the link → `record_click()` → `redirect(url, code=302)`. The redirect
uses **302 (temporary)** on purpose — a 301 (permanent) would let browsers
remember the destination and skip the dashboard next time, and those clicks
would never be counted.

The status check is separate: a timer wakes up every 5 minutes and simply
tries to open every destination URL itself (like a robot visitor), timing
how long the answer takes. Online = answered with a normal code, Down = an
error code or no answer within 10 seconds.

### Libraries used (and why)

| Library | Job in this project | Why this one |
|---|---|---|
| **Flask** | The web framework — serves the dashboard pages, the manage forms, and the `/go/...` redirect route | Small, simple, perfect for internal tools; one file is enough |
| **APScheduler** | The background timer that runs health checks every 5 minutes while the web app keeps serving pages | Runs inside the same process — no separate service or Task Scheduler entry needed for the checks |
| **requests** | Makes the health-check calls to each destination URL and measures response time | The standard way to make HTTP calls in Python. Configured with `trust_env=False` so checks go **directly** to your apps, never through the company proxy (a proxy would answer instead of the real link and fake the result) |
| **sqlite3** *(built into Python)* | The database — one file, `data.db` | Nothing to install or administer; a single file holds everything and is trivial to back up |
| **Jinja2** *(comes with Flask)* | Fills the HTML templates in `templates/` with live numbers | Standard Flask templating |

No JavaScript frameworks, no internet CDNs, no external services — the web
pages are plain HTML + CSS (plus ~15 lines of JS for the Copy button), so
everything works on a server with **no internet access**.

### The database (3 tables in `data.db`)

| Table | One row means | Used for |
|---|---|---|
| `links` | one tracked link (name, short name, destination, enabled) | the list you manage |
| `daily_hits` | "link X was clicked N times by IP Y on day Z" | clicks today/total, **people** counts (unique IPs), the 14-day chart |
| `checks` | one health check result (ok?, HTTP code, milliseconds) | Online/Down status + response time (kept 30 days) |

Storing clicks per **day + IP** (instead of one row per click) keeps the
database tiny forever, while still answering every question the dashboard
asks: total clicks (sum), unique people (count different IPs), and the
daily chart (group by day).

---

## Files

| File | What it is |
|---|---|
| `app.py` | The web app: pages, short-link redirect (`/go/...`), scheduler |
| `collector.py` | Health checks (ping every link, save status + speed) |
| `db.py` | Database (SQLite) — tables and click recording |
| `sample_data.py` | Demo data for local testing only |
| `config.ini` | All settings (port, intervals, sample mode) |
| `data.db` | The database file — **this is your data; back it up** |
| `templates/`, `static/` | The web pages and styling |

**Backup** = copy `data.db` somewhere safe (links, click history, and check
history are all inside this one file).

## Troubleshooting

| Problem | Fix |
|---|---|
| Page doesn't open | Is `python app.py` running? Check the terminal window |
| "Port already in use" error at start | See "How to check and change the port" above |
| Link shows Down but works in browser | The server pings the **destination URL** directly — make sure that URL is reachable *from the server itself*, not only from your PC |
| Clicks not increasing | Users are probably opening the destination directly instead of the short `/go/` link |
| Works on server, not from other PCs | Open the firewall port (command above) |
