# Deploying Ripple

The backend (FastAPI) runs on **Render**, the frontend (React + Vite) on **Vercel**.
Secrets live only in Render's environment settings, never in the repo.

```
Visitor's browser ──> Vercel (frontend, static) ──calls──> Render (backend API) ──> Cominty
```

The visitor id travels in a header (`X-Ripple-Visitor`), with a cookie as a fallback. Browsers
that block cross-site cookies (Safari, private windows) still work.

---

## 0. Push the code to GitHub

1. On github.com: **New repository** → name `ripple` → **Private** → **Create repository**
   (no README, no .gitignore).
2. In `C:\Users\ameni\Projects\ripple`:
   ```
   git remote add origin https://github.com/<your-account>/ripple.git
   git push -u origin main
   ```
3. Check on GitHub that `backend/.env` is **not** there (it is gitignored) and that
   `backend/fixtures/` and `backend/sample_pack/` **are** there (the sample event replays them).

## 1. Make the secrets you'll paste into Render

- **Owner token** (your own pass that skips the visitor limits):
  ```
  backend/.venv/Scripts/python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```
  Keep it in your password manager. Don't put it in the repo or a chat.
- **Cominty values**: copy them from your local `backend/.env`: `COMINTY_API_KEY`,
  `COMINTY_USER_ID`, `COMINTY_SKILL_CONTRACTS`, and the four `COMINTY_AGENT_*` ids.

## 2. Backend on Render

1. render.com → **New +** → **Web Service** → **Connect a repository** → pick `ripple`.
2. Fill in:

   | Field | Value |
   |---|---|
   | Name | `ripple-api` (the URL becomes `https://ripple-api.onrender.com`) |
   | Region | **Frankfurt (EU Central)**, closest to Paris |
   | Branch | `main` |
   | Root Directory | `backend` |
   | Runtime / Language | **Python 3** (the version comes from `backend/.python-version`: 3.13) |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
   | Instance Type | **Starter** (recommended, see "What is lost on restart"), or **Free** |

3. **Environment** → **Add Environment Variable**, one per line:

   | Key | Value |
   |---|---|
   | `COMINTY_API_KEY` | *(from your .env)* |
   | `COMINTY_USER_ID` | *(from your .env)* |
   | `COMINTY_SKILL_CONTRACTS` | *(from your .env)* |
   | `COMINTY_AGENT_IMPACT` | *(from your .env)* |
   | `COMINTY_AGENT_FINDER` | *(from your .env)* |
   | `COMINTY_AGENT_REPLANNER` | *(from your .env)* |
   | `COMINTY_AGENT_COORDINATOR` | *(from your .env)* |
   | `DEMO_MODE` | `false` |
   | `RIPPLE_PUBLIC` | `true` |
   | `RIPPLE_CROSS_SITE_COOKIE` | `true` |
   | `RIPPLE_CORS_ORIGINS` | `https://ripple.vercel.app` *(your Vercel URL from step 3; fill in after)* |
   | `RIPPLE_DAILY_BUDGET_USD` | `2` |
   | `RIPPLE_VISITOR_ANALYSES` | `1` |
   | `RIPPLE_VISITOR_PROBLEMS` | `5` |
   | `RIPPLE_VISITOR_TTL_HOURS` | `24` |
   | `RIPPLE_VISITOR_MAX_FILE_MB` | `5` |
   | `RIPPLE_OWNER_TOKEN` | *(the token from step 1)* |
   | `RIPPLE_OWNER_BUDGET_USD` | `5` |
   | `RIPPLE_DATA_DIR` | `/var/data` *(only with a disk, see below)* |

4. **Advanced** → **Health Check Path**: `/api/health`.
5. *(Starter only)* **Advanced** → **Add Disk**: name `ripple-data`, mount path `/var/data`, size 1 GB.
6. **Create Web Service**. Wait for "Live", then open
   `https://ripple-api.onrender.com/api/health`. It should show `{"ok":true,"demo_mode":false}`.

### What is lost on restart (without a disk)

Render's free instances have no persistent disk and sleep after 15 minutes without traffic.
On every restart or redeploy, these are lost:

- visitor events, their uploads and built files (they would be deleted after 24 h anyway);
- live recordings of visitor runs;
- **the day's spending counter and the visitor counts**. After a restart, the $2 daily cap and the
  per-visitor limits start from zero again.

These are not lost, because they ship in the repo: the sample event (CodeSprint) and its recorded
agent runs (`backend/fixtures/`), the sample pack and the demo files.
With **Starter + a disk** and `RIPPLE_DATA_DIR=/var/data`, nothing is lost.

On the Free plan, the first request after a sleep takes about a minute. Open `/api/health`
a minute before the jury or a demo.

## 3. Frontend on Vercel

1. vercel.com → **Add New…** → **Project** → **Import** the `ripple` repository.
2. Fill in:

   | Field | Value |
   |---|---|
   | Root Directory | `frontend` (click **Edit** and pick the folder) |
   | Framework Preset | **Vite** (detected) |
   | Build Command | `npm run build` |
   | Output Directory | `dist` |
   | Install Command | `npm install` (default) |

3. **Environment Variables**:

   | Key | Value |
   |---|---|
   | `VITE_API_URL` | `https://ripple-api.onrender.com` (no trailing slash) |
   | `VITE_VIDEO_URL` | *(optional)* the demo video link; turns on "Watch the video" |

4. **Deploy**. `frontend/vercel.json` sends every path (`/start`, `/app/...`) to the app, so
   reloading any page works.
5. Copy the production URL (e.g. `https://ripple.vercel.app`). On Render, open **Environment**,
   set `RIPPLE_CORS_ORIGINS` to it and click **Save, rebuild, and deploy**.
   For more than one address (e.g. a custom domain), separate them with commas.

`VITE_*` values are baked in at build time. After changing one, click **Redeploy** on Vercel.

## 4. Your owner pass

Open `https://<your-vercel-url>/start?owner=<your token>` **once** in each browser you use.
The token disappears from the address bar, and that browser skips the visitor limits.
Its runs still count in the day's total, with their own cap (`RIPPLE_OWNER_BUDGET_USD`).
Nothing in the page shows it. To revoke the pass, change `RIPPLE_OWNER_TOKEN` on Render.

## 5. Checklist on the deployed app

Use a normal browser window, then a private one (a new visitor).

- [ ] **Health:** `https://ripple-api.onrender.com/api/health` shows `"ok": true`.
- [ ] **Landing:** the page loads, the hero ripple animates, the dark mode toggle works,
      and **Get started** opens `/start`.
- [ ] **Reload:** reloading `/start` and `/app/event` shows the page, not a 404.
- [ ] **Sample event:** on `/start`, click **Use a sample event**, pick problem 1, then **Start Ripple**.
  - The workspace opens and the agents replay.
  - Choose option B. The outbox fills, and the `.ics` and `.csv` download.
  - Problem 2 skips the Resource Finder. Problem 3 runs only the Coordinator.
  - The Cominty dashboard shows no new spend: the sample event only replays recordings.
- [ ] **Live run** (about $0.03): in a private window, upload the 5 files from
      **Download a sample pack** and paste `problem1_venue_email.txt`.
  - "What Ripple understood" appears in about 40 s.
  - **Looks right** opens the workspace and handles the problem.
  - The options say "N people on site, M seats".
- [ ] **Limits:** in the same private window, start a second file analysis. It is refused politely
      and offers "Use a sample event". The remaining tries are shown under the form.
- [ ] **Privacy:** in the first window's developer tools (Application → Local Storage), copy
      `ripple.event` (e.g. `v-1a2b3c4d5e`). In another private window, open
      `https://ripple-api.onrender.com/api/events/<that id>`: it says "unknown event".
- [ ] **Odd inputs:** an empty form keeps **Start Ripple** disabled; a `.docx` or a 6 MB file
      shows a plain message.
- [ ] **Owner pass:** open `/start?owner=<token>`. The tries line disappears, and a second analysis works.
- [ ] **Phone:** on a real phone (Safari and Chrome), open the landing page and `/start`, and run
      the sample event. Nothing should scroll sideways, and the buttons should be easy to tap.
- [ ] **Spending:** in the Cominty dashboard, today's spend matches the runs you made.

## Not in the repo, ever

`backend/.env`, the Cominty key and ids, and the owner token. `.env.example` lists the variable
names only.
