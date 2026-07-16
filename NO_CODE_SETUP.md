> **v1.3 note:** Long-running collection now runs in a separate Render background worker. See `WORKER_FIX.md`.

# No-code deployment: the six-step version

You will never touch the code or run a command.

## Before starting: collect six values

Create a temporary note on your computer containing these six labels:

```text
SUPABASE_DB_URL=
SUPABASE_SERVICE_ROLE_KEY=
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
FRED_API_KEY=
DASHBOARD_PASSWORD=
```

Do not upload this note to GitHub.

---

## 1. Create the free Alpaca keys

1. Create an Alpaca account and open its Paper Trading dashboard.
2. Generate paper API credentials.
3. Copy the key into `ALPACA_API_KEY`.
4. Copy the secret into `ALPACA_SECRET_KEY`.

A free Paper account is sufficient. The collector deliberately requests the free IEX feed.

---

## 2. Create the free FRED key

1. Sign in to FRED.
2. Open **My Account**.
3. Open **API Keys**.
4. Request a key.
5. Copy it into `FRED_API_KEY`.

---

## 3. Create Supabase and copy two values

1. In Supabase, click **New project**.
2. Name it `cross-asset-research`.
3. Create a database password and save it.
4. Wait until the project is ready.

### Copy the database URL

1. Click **Connect** near the top of the project.
2. Select the **Transaction pooler** connection on port `6543`.
3. Copy the complete URI, including the database password.
4. Paste it after `SUPABASE_DB_URL=` in your temporary note.

Use the complete value beginning `postgresql://`. Do not add quotation marks.

### Copy the server key

1. Open **Project Settings → API Keys**.
2. Copy the backend **secret key**. A legacy `service_role` key also works.
3. Paste it after `SUPABASE_SERVICE_ROLE_KEY=`.

You do not create tables, run SQL or create a Storage bucket. The dashboard does that automatically.

---

## 4. Put the supplied folder into GitHub

1. Extract the supplied ZIP on your computer.
2. Open GitHub and create a new repository named `cross-asset-research-no-code`.
3. Select **Private**.
4. Do not initialise it with a README or licence.
5. On the empty repository page, click **uploading an existing file** or **Add file → Upload files**.
6. Open the extracted project folder.
7. Select everything inside it and drag the contents into GitHub.
8. Click **Commit changes**.

Success means `render.yaml`, `app`, `config`, `sql` and `README.md` appear directly on the repository homepage.

---

## 5. Deploy once in Render

1. Open Render.
2. Click **New + → Blueprint**.
3. Connect GitHub if asked.
4. Select `cross-asset-research-no-code`.
5. Render should detect the root `render.yaml`.
6. Click **Deploy Blueprint**.

Render will ask for six values. Paste the first five from your temporary note.

For `DASHBOARD_PASSWORD`, invent a strong password that you will remember. This password also encrypts the untouched-test archive.

Render creates one paid web service named:

```text
cross-asset-research-dashboard
```

Wait until the service displays **Live**.

If it displays **Deploy failed**, open the logs. The most common cause is an incorrectly copied Supabase database URL.

---

## 6. Open the dashboard and click one button

1. In the Render service, click its public `onrender.com` URL.
2. Your browser asks for credentials.
3. Username: `admin`
4. Password: the `DASHBOARD_PASSWORD` you chose.
5. Click **Run complete setup**.
6. Confirm the prompt.

You may close the page. Reopen it later to see progress.

The dashboard automatically:

- creates the database tables;
- freezes the 90-day and untouched-test dates;
- checks all credentials and required instruments;
- tests Bitcoin and SPY;
- collects the full history;
- resumes safely after temporary failures;
- runs quality checks without querying the untouched period;
- creates all archives;
- creates a private Supabase Storage bucket;
- uploads the archives;
- displays **Download discovery package** when complete.

Download only the discovery package from that button. The dashboard deliberately does not expose a button for the untouched-test archive.

## What to do if it stops

Open the dashboard and read the plain-English status.

- **Connection check failed:** correct the relevant Render environment value and redeploy.
- **Provider temporarily unavailable:** click **Resume safely**.
- **Service restarted:** click **Resume safely**. Checkpoints prevent duplicates.
- **Export upload failed:** click **Recreate export**.

Do not delete the Supabase project or change the frozen dates during the research round.
