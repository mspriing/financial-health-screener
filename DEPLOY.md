# Deploying the screener to a free public URL

Goal: get a `https://<something>.streamlit.app` link you can paste into your LinkedIn
**Featured** section. Two stages: (1) push the code to GitHub, (2) connect Streamlit
Community Cloud. ~10 minutes.

This folder already lives in your Recruitment Assistant folder, so open it in **Claude Code**
(or any terminal) and go.

---

## Stage 0 — Sanity check it runs locally (optional but smart)

```bash
cd "financial-health-screener"
pip install -r requirements.txt
streamlit run app.py
```

Open the local URL, click through the three sample companies and try a live ticker
(e.g. `AAPL`, `MSFT`). When it looks right, stop the server (Ctrl-C) and deploy.

---

## Stage 1 — Push to GitHub

### Easiest: let Claude Code do it
In Claude Code, just say:
> "Initialize a git repo here, make the first commit, create a new public GitHub repo
> called `financial-health-screener`, and push."

### Or do it by hand

**If you have the GitHub CLI (`gh`):**
```bash
git init
git add .
git commit -m "Financial Health & Red-Flag Screener"
gh repo create financial-health-screener --public --source=. --remote=origin --push
```

**Without `gh`:** create an empty public repo at <https://github.com/new> (name it
`financial-health-screener`, don't add a README), then:
```bash
git init
git add .
git commit -m "Financial Health & Red-Flag Screener"
git branch -M main
git remote add origin https://github.com/<YOUR_USERNAME>/financial-health-screener.git
git push -u origin main
```

---

## Stage 2 — Deploy on Streamlit Community Cloud (free)

1. Go to <https://share.streamlit.io> and **sign in with GitHub** (authorize it once).
2. Click **Create app** → **Deploy a public app from GitHub**.
3. Fill in:
   - **Repository:** `<YOUR_USERNAME>/financial-health-screener`
   - **Branch:** `main`
   - **Main file path:** `app.py`
4. Click **Deploy**. First build takes ~2–4 minutes while it installs `requirements.txt`.
5. You'll land on your live app at a URL like
   `https://financial-health-screener-<random>.streamlit.app`.
   - You can customize the subdomain in the app's **Settings → General**.

That URL is what goes in **LinkedIn → Featured → Add a link** (see `../LINKEDIN.md`).

---

## Updating it later

Any push to `main` redeploys automatically:
```bash
git add .
git commit -m "tweak: <what you changed>"
git push
```

## Troubleshooting

- **Build fails on a package:** loosen or pin the version in `requirements.txt`, commit, push.
- **A live ticker shows "no statement data":** Yahoo occasionally rate-limits cloud IPs or
  lacks detail for a name. The sample companies always work; for your Featured screenshot,
  demo a large, well-covered ticker (AAPL, MSFT, KO) or use a sample.
- **App "sleeps" after inactivity:** free Community Cloud apps idle out and wake on the next
  visit (a few seconds). Fine for a portfolio link.
