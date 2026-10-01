# Link Watch — Setup & Developer Guide

> The full guide: how to run it, add links, change the port, deploy to the server, every setting
> in `config.ini`, how the counting works inside, and troubleshooting. For a short overview of
> what the project is, see the **[README](../README.md)**.

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
cd "DashBoard Check link"      # the project folder — one level up from this file
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:8090** in your browser.

The first run creates `data.db` (the database) and, if `sample_mode = true`,
4 demo links with fake click history so you can try everything safely.

> **The demo data does not disappear when you turn the flag off.** Both
> seeders (`db.seed_samples` and `sample_data.seed_demo_hits`) only run when
> their table is *empty*, so they never re-seed — but equally, nothing ever
> removes what an earlier run already inserted. Setting
> `sample_mode = false` stops new seeding and nothing else. To actually get
> rid of demo data, delete the demo links on **Manage links** (deleting a
> link cascades to its clicks and check history), or delete `data.db` and
> start over.

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

## Start buttons — bringing the apps back after a reboot

When the server restarts, every internal app that was running in a terminal
goes down with it. Instead of opening a terminal for each one, you can give a
link a **start command**. It then gets a **Start** button next to its status,
and a **Start all down** button appears at the top of the dashboard.

```
Reboot  ->  you start the dashboard   (one terminal, by hand)
        ->  click "Start all down"    (on the dashboard)
        ->  every other app comes back
```

### Telling the dashboard how to start an app

Add one `[process:<name>]` section per app at the bottom of `config.ini`:

```ini
[process:my-app]
link    = my-app
command = npm run dev
cwd     = D:\Path\To\my-app
```

| Key | Meaning |
|---|---|
| section name | any short name — it also names the log file (`logs\my-app.log`) |
| `link` | the `/go/<slug>` name of the link this app sits behind |
| `command` | exactly what you would type in the terminal |
| `cwd` | the folder you would type it in |

**Where to find the slug:** it is the `/go/...` line shown under each link
name on the dashboard. `http://SERVERNAME:8090/go/my-app` → the slug is
`my-app`. It must match **exactly**, or no button appears.

**Two processes behind one link** (a backend and a frontend): give both
sections the same `link`. One button starts both, in the order they appear
in the file.

```ini
[process:my-dashboard-backend]
link    = my-dashboard
command = py .\serve.py
cwd     = D:\Path\To\my-dashboard

[process:my-dashboard-frontend]
link    = my-dashboard
command = py -m http.server 8080 --directory dist
cwd     = D:\Path\To\my-dashboard\frontend
```

> The dashboard itself is deliberately **not** listed — it cannot start
> itself. It is the one thing you launch by hand after a reboot.

**Pin the port of every Vite app you start this way.** `npm run dev` does not
guarantee a port: Vite takes 5173 if it is free and otherwise walks up to
5174, 5175, … So the port an app lands on depends on what else happened to
start first, and the link you saved in the dashboard silently stops matching
— the app is running fine but its row reads Down. Fix it in the app's own
`vite.config.js`, not here:

```js
export default { server: { port: 5174, strictPort: true } }
```

`strictPort` makes Vite **fail loudly** instead of drifting to another port,
which is what you want: a startup error in `logs\<name>.log` is far easier to
diagnose than a link that is mysteriously Down. Give each app its own port
and set the link's destination to that same port.

### What happens when you click Start

1. The dashboard pings that link **right now** to see whether it is already
   answering. If it is, it refuses and tells you so. This is what stops a
   second copy being launched to fight the first one for the same port.
2. Otherwise every process for that link is started, **detached** — so they
   keep running after you close the dashboard's terminal.
3. Everything each app prints is appended to `logs\<section name>.log`.
4. After a few seconds the link is checked again and the status updates.

An app that starts and then dies on its own is **not** reported as an error.
The health check is what tells you whether it really came up — and the log
file tells you why it did not.

> The status check is always **live**, never the last saved result. Right
> after a reboot the newest stored check is stale and still says "online",
> which would refuse to start exactly the apps this button exists for.

### Checking your configuration

Every time the dashboard starts, it reports what it found:

```
[runner] Start buttons for 3 link(s): my-app, my-dashboard, ...
```

If a slug in `config.ini` matches no link on the dashboard, it says so
instead of silently leaving the button out:

```
[runner] WARNING: config.ini has link = 'my-app', but no link on the
         dashboard uses that slug - no button for it
```

That warning is the usual explanation for a missing Start button.

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

1. Copy this whole folder to the server — but **not** `data.db`, so the
   server starts with an empty database instead of your test data
2. In `config.ini` set:
   ```ini
   sample_mode = false
   ```
3. On the server:
   ```
   pip install -r requirements.txt
   python app.py
   ```
4. Add your real links on **Manage links**. If any demo links are showing,
   delete them here — step 2 stops new demo data being created but does not
   remove demo data that already exists (see **How to run** above)
5. Point each link at an address **users' browsers can reach** — a server
   name or IP, not `127.0.0.1`. The `/go/` redirect sends the visitor's own
   browser to that address, so `localhost` would send every user to their
   own machine
6. Team opens `http://SERVERNAME:8090`

### Updating a server that is already running

Copying the *whole* folder over an existing install is **not** safe — it
would overwrite two files you care about:

| File | Why not to overwrite it |
|---|---|
| `data.db` | All your links, click history and check history. Copying over it loses everything |
| `config.ini` | The server's copy has `sample_mode = false`, the real port, and your `[process:...]` sections |

So when updating, copy only the code:

```
app.py  collector.py  runner.py  db.py  templates\  static\
```

and for `config.ini`, **paste any new settings into the server's existing
file** rather than replacing it. Then stop the dashboard and start it again —
Python does not pick up new code while it is running.

> `requirements.txt` has not changed since the first release, so there is
> normally no need to run `pip install` again.

> **Copy the whole `static\` folder, not single files.** The styling is now
> split across three stylesheets (`tokens.css`, `style.css`,
> `components.css`). If one is missing the pages still load, but with no
> styling at all — plain black text on white. That is the symptom to look for
> if the dashboard suddenly looks unformatted after an update.

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

**No administrator rights?** Step 2's "Run whether user is logged on or not"
and step 3's "At startup" both need elevation. A trigger of **At log on** for
your own account does not. The dashboard then starts by itself as soon as you
RDP into the server — one step later than "At startup", but it needs no
special permissions.

Either way, once the dashboard comes back on its own, the only thing left to
do after a reboot is click **Start all down** to bring the other apps up.

---

## This deployment (Hi-Tech Apparel)

Everything above is generic. This section records how *our* install is set
up. Links and click history live in `data.db`, which is **excluded by
`.gitignore`** — so a fresh clone starts with no links at all and this table
is the record of what to recreate.

**Server:** `192.0.2.10` · dashboard on port **8090** →
`http://192.0.2.10:8090`

| Link (name) | Short link | Port | Started by | Where |
|---|---|---|---|---|
| Forming Box | `/go/forming-box` | 5174 | `npm run dev` | `D:\All Project for PCK\Forming Box File\src` |
| Label Converter Excel | `/go/label-converter` | 5176 | `npm run dev` | `D:\All Project for PCK\Make Excel to Excel\src` |
| Barcode Label Check | `/go/barcode-label-check` | 5177 | `npm run dev` | `D:\All Project for PCK\Compare PDF Barcode and EXCEL\react-app` |
| BOM Query Web | `/go/bom-query-web` | 8000 | `python -m uvicorn main:app --port 8000` | `D:\All Project for PCK\BOM Query Web\src` |
| PPS, ACS, WISDOM | `/go/pps-acs-wisdom-compare` | 8080 | `py .\serve.py` **+** `py -m http.server 8080 --directory dist` | `D:\All Project for PCK\PPS,ACS,WISDOM\DashBoard` (backend) and `D:\All Project for PCK\PPS,ACS,WISDOM\DashBoard\frontend` (frontend) |

The dashboard itself runs from
`D:\All Project for PCK\DashBoard Check link` with `py .\app.py`, and is
deliberately absent from the table — it is the one thing started by hand
after a reboot.

### ⚠ The three Vite ports in that table are not stable

Only **8000** and **8080** are fixed, because those commands name their port.
The three `npm run dev` apps do not: Vite takes 5173 if free and otherwise
walks upward, so **the port each app gets depends on the order they were
started in.** Observed on 2026-09-14, within a single afternoon:

```
first look    5174 Forming Box   5175 Label Converter   (5177 closed)
hours later   5174 Forming Box   5175 Barcode!          5176 Label Converter   5177 Barcode
```

Label Converter had moved 5175 → 5176, and Barcode had taken 5175.

**Why this is worse than it sounds.** A drifted port does not always show up
as a red link. If another app has moved into the port your link points at,
the health check gets a perfectly good `200` and the row stays **green** —
while `/go/label-converter` quietly forwards users to the Barcode app. That
happened here and was only caught by comparing each page's `<title>` against
the link that pointed to it:

```
curl -s http://192.0.2.10:5176/ | findstr /i "<title>"
```

**The fix is to pin the ports**, in each project's own `vite.config.js`:

```js
export default { server: { port: 5176, strictPort: true } }
```

`strictPort` makes Vite fail loudly rather than drift. Until all three are
pinned, treat a green row on a Vite link as "something answered", not "the
right app answered", and re-check the titles after any restart.

---

## Configuration reference (`config.ini`)

| Setting | Meaning | Default |
|---|---|---|
| `[server] host` | `0.0.0.0` = reachable by the whole network | `0.0.0.0` |
| `[server] port` | Port of the dashboard **and** of every short link | `8090` |
| `[health] check_interval_minutes` | How often every link is pinged. One pass is **sequential**, so allow `timeout_seconds` per unreachable link | `30` |
| `[health] timeout_seconds` | How long to wait before calling a link Down | `10` |
| `[health] ssl_verify` | `false` accepts self-signed internal certificates | `false` |
| `[app] sample_mode` | `true` = create demo links/history **when the database is empty**. Set `false` on the server. Turning it off does not delete demo data already in `data.db` | `true` |
| `[process:<name>] link` | The `/go/<slug>` name of the link this app is behind | — |
| `[process:<name>] command` | Exactly what you would type in the terminal to start it | — |
| `[process:<name>] cwd` | The folder to run that command in | — |

A `[process:...]` section missing any of the three keys is ignored, with a
message at startup saying which section was skipped. See **"Start buttons"**
above for the full explanation.

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
| **APScheduler** | The background timer that runs health checks on the `check_interval_minutes` schedule while the web app keeps serving pages | Runs inside the same process — no separate service or Task Scheduler entry needed for the checks |
| **requests** | Makes the health-check calls to each destination URL and measures response time | The standard way to make HTTP calls in Python. Configured with `trust_env=False` so checks go **directly** to your apps, never through the company proxy (a proxy would answer instead of the real link and fake the result) |
| **sqlite3** *(built into Python)* | The database — one file, `data.db` | Nothing to install or administer; a single file holds everything and is trivial to back up |
| **subprocess** *(built into Python)* | Starts the app behind a link when you click **Start** | Each app is launched through `cmd /c` (so both `py ...` and `npm ...` work the same way) and detached from the dashboard's console, so it keeps running after you close that terminal |
| **Jinja2** *(comes with Flask)* | Fills the HTML templates in `templates/` with live numbers | Standard Flask templating |

No JavaScript frameworks, no internet CDNs, no external services — the web
pages are plain HTML + CSS (plus ~15 lines of JS for the Copy button), so
everything works on a server with **no internet access**. That rule covers
the styling too: every font is one that ships with Windows, and the browser
tab icon is written into the page itself rather than loaded as a file, so a
page never waits on something it cannot reach.

### The styling (`static/`)

Three stylesheets, loaded in this order by `templates/base.html`. The order
matters — each one relies on the one before it.

| File | Holds | Change it when |
|---|---|---|
| `tokens.css` | Every colour, spacing step, corner radius and shadow — twice: once for the light theme, once for dark | You want to **recolour** the dashboard |
| `style.css` | The page shell: header bar, headings, cards, form fields, buttons, footer | You are changing a control or the page frame |
| `components.css` | The data parts: stat tiles, the health dial, the link table, status pills, the 14-day chart | You are changing how the numbers are displayed |

**To change a colour, edit `tokens.css`.** Neither `style.css` nor
`components.css` contains a single colour of its own — every rule points at a
token like `var(--accent)`, so one edit there reaches every page. The tokens
are listed twice in that file: the plain `:root` block is the light theme, and
the `@media (prefers-color-scheme: dark)` block below it overrides the same
names for dark. Change a colour in one block and the other theme keeps its own.

The **one exception** is the logo. `templates/base.html` holds its colours
directly, in three places that must be changed together or the mark will not
match itself:

| In `base.html` | What it colours |
|---|---|
| the `<linearGradient id="lw-face">` stops, and the `#312E81` block behind it | the mark in the header bar |
| the `%23...` codes inside the `rel="icon"` line | the same mark as the browser-tab icon (a `#` is written `%23` inside that line) |
| `<meta name="theme-color">` | the browser's own bar colour on mobile |

They sit outside the token system because an icon has to be readable before
any stylesheet has loaded.

Dark mode follows the **Windows** theme setting (Settings → Personalisation →
Colours). There is no toggle in the app and nothing is stored — the browser
reports which mode Windows is in, and the matching tokens apply.

Below 900px wide the link table stops being a table: each row becomes its own
card. That works because every `<td>` in `templates/dashboard.html` and
`templates/manage.html` carries a `data-label="..."` attribute — on a narrow
screen CSS prints that label beside the value, since the real column headings
are hidden. **If you add a column, give its cells a `data-label` too**, or it
will lose its heading on phones.

### How one health-check pass runs

`collector.run_health_checks()` walks the enabled links **one at a time** in a
single loop — there is no concurrency. A link that answers costs a few
milliseconds, but a link that is unreachable costs the full
`timeout_seconds` before `requests` gives up.

So the worst case for a whole pass is roughly:

```
timeout_seconds  x  number of unreachable links
```

With `timeout_seconds = 10` and 4 dead links, one pass takes ~40 seconds.
(Only links whose packets are *dropped* cost the full timeout. A port that
actively refuses the connection fails in milliseconds, so real passes are
often much faster than the worst case.)
That has a consequence worth knowing when testing: **for up to a minute after
a restart, the dashboard is still showing the previous pass's results.** Rows
update as the loop reaches them, not all at once. If you change a link's URL
and restart, wait for a full pass before judging the result — otherwise you
are reading stale rows and will blame the wrong thing.

To force a complete pass immediately instead of waiting, either click
**Check now** on the dashboard, or run it synchronously:

```
python -c "import collector; collector.run_health_checks(10, False)"
```

Each ping uses a `requests` session with `trust_env = False` so the corporate
proxy is bypassed — a proxy would answer on the app's behalf and report a
healthy link that is actually down.

---

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
| `app.py` | The web app: pages, short-link redirect (`/go/...`), scheduler, Start buttons |
| `collector.py` | Health checks (ping every link, save status + speed) |
| `runner.py` | Starts the app behind a link — reads `[process:...]`, launches it detached |
| `db.py` | Database (SQLite) — tables and click recording |
| `sample_data.py` | Demo data for local testing only |
| `config.ini` | All settings (port, intervals, sample mode, start commands) |
| `data.db` | The database file — **this is your data; back it up** |
| `logs/` | What each started app printed — created on the first Start. Not backed up |
| `tests/` | `python -m pytest tests/` — the launcher tests start real processes |
| `templates/` | The web pages (Jinja2): `base.html` frame, `dashboard.html`, `manage.html`, `edit.html`, `missing.html` |
| `static/` | The styling — `tokens.css` (colours, both themes), `style.css` (shell, forms, buttons), `components.css` (tiles, dial, table, charts) |
| `README.md` | GitHub landing page — what the dashboard is, for anyone |
| `docs/DEVELOPER-GUIDE.md` | this file — setup, deployment, settings, internals |

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
| **No Start button on a link** | The slug in `config.ini` must match the link's `/go/...` name exactly. Restart the dashboard and read the `[runner]` lines — a mismatch is named there |
| **No Start buttons at all, and no "Start all down"** | There are no `[process:...]` sections in `config.ini`. On the server, check with `findstr /C:"[process:" config.ini` |
| **Says "Started" but the link stays Down** | The app was launched but did not come up. Open `logs\<name>.log` — the reason is at the bottom |
| **"already online - left it alone"** | Something is already answering on that address. That is the guard doing its job; it will not launch a second copy |
| **Demo links still showing after `sample_mode = false`** | The flag only stops *new* seeding; it never deletes. Remove the demo links on **Manage links** — deleting a link also removes its clicks and check history — or delete `data.db` to start clean |
| **A link is Down but the app is definitely running** | Check the **port** first: `python -c "import socket;s=socket.socket();s.settimeout(2);print(s.connect_ex(('SERVER-IP',PORT))==0)"`. `True` means something is listening and the URL is wrong; `False` means nothing is there. For Vite apps the port is the usual culprit |
| **Status looks wrong right after a restart** | Checks run sequentially and a dead link costs `timeout_seconds` each, so a full pass can take ~40s. Until it finishes you are seeing the *previous* pass. Click **Check now**, or wait a full pass before concluding anything |
| **A Vite app starts but the link is still Down** | If its usual port was taken, Vite quietly moves to the next one, so the saved link no longer matches. `logs\<name>.log` shows the port it actually chose — update the link, then pin the port with `strictPort` (see "Start buttons") so it cannot drift again |
