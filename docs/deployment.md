# Putting RFP AI live: Render (backend) and Vercel (frontend)

The browser loads the React app from **Vercel** and talks directly to the FastAPI backend on **Render**.

```
Browser  ->  Vercel   (static React app)
   |
   +------>  Render   (FastAPI backend, SQLite, calls Groq / Gemini / OpenRouter)
```

## What you get on the free plan

| | Free plan |
|---|---|
| Sleeping | The backend sleeps after about 15 minutes without traffic. The first visit afterwards takes up to a minute while it wakes up (the app says so). |
| Data | Render's disk is wiped on every restart and deploy. Uploaded documents, RFPs, drafts and approvals are lost, so reload the sample data when you start. |
| Memory | 512 MB. The embedding model needs about 410 MB on its own, so it is switched off: search is keyword-only. In our tests this found every source the 58 retrieval checks need, and missed one paraphrased query in twelve that hybrid search finds. |
| Quota | Everyone using the site shares your free model quota. |
| Access | The site is open to anyone with the link. See "Turning on the access key". |

The Vercel part is free.

## 1. Deploy the backend on Render

1. Sign in at https://render.com and connect your GitHub account to Render.
2. **New +**, then **Blueprint**, then choose the `rfp-azentio` repository. Render finds `render.yaml`.
3. It asks for three values. Paste your key lists, comma-separated, into:
   `GEMINI_API_KEYS`, `GROQ_API_KEYS`, `OPENROUTER_API_KEYS`. Type them here and nowhere else: never into a chat or a file.
4. **Deploy Blueprint**. The first build takes a few minutes.
5. Copy the service address, such as `https://rfp-ai-api.onrender.com`. Open `<address>/api/health`. You should see
   `{"status":"ok","keys_configured":{"gemini":3,"groq":3,"openrouter":3},...}`.

## 2. Deploy the frontend on Vercel

1. Sign in at https://vercel.com and connect your GitHub account.
2. **Add New...**, then **Project**, then import `rfp-azentio`.
3. Set **Root Directory** to `frontend`. The framework is detected as Vite.
4. Under **Environment Variables** add `VITE_API_URL` = the Render address from step 1.5, with **no trailing slash**.
5. **Deploy**, then open the address Vercel gives you.

## 3. First use

1. **Knowledge Base**, then **Load sample knowledge base**. Search mode reads `keyword` on the free plan.
2. Click **Detect conflicts**. Answers only carry conflict flags once this has run, and it uses a little model quota.
3. **Active RFPs**, then upload `sample_data/rfps/ASIB_RFP-2026-017_Core_Islamic_Banking.xlsx`.
4. **Start drafting**. It takes a few minutes and uses your model quota.

`e2e/deploy_check.py` does these steps for you and reports what is wrong if anything is. See "Checking a deployment".

## If something does not work

| What you see | Likely cause |
|---|---|
| "The server did not answer" | The backend is still waking up or still building. Wait a minute and press Try again. |
| Pages load but every action says "Failed to fetch" | `VITE_API_URL` is missing or wrong in Vercel (it is read at build time: change it, then **Redeploy**), or the origin is not allowed (see `CORS_ORIGIN_REGEX`). |
| Downloads are called `response` | The backend was not redeployed after the CORS change. Redeploy it. |
| Everything you uploaded has gone | The free plan wiped its disk on a restart or deploy. See "Keeping your data". |
| Drafting stops with "quota exhausted" | The free model quota is spent. It resumes by itself when it recovers; the banner says when. |
| The backend keeps restarting | It ran out of its 512 MB. Make sure `EMBEDDINGS_ENABLED` is `false`, or move to a bigger instance. |

## Turning on the access key

The app has no user accounts. On an open site anyone with the link can start drafting on your model keys and change or
delete data. To require one shared key:

1. In Render, open the service, **Environment**, and add `APP_ACCESS_KEY` with a long random value.
2. Save. Render redeploys, and the site now asks for the key once per browser tab.
3. Give the key to the people who need it. To change it, change the variable.

Remove the variable to open the site again.

## Keeping your data (a paid Render instance)

The blueprint can be changed to keep everything across restarts and to use the embedding model:

1. In `render.yaml`, change `plan: free` to a paid plan with at least 2 GB of memory, and add a disk:
   ```yaml
       disk:
         name: rfp-ai-data
         mountPath: /var/data
         sizeGB: 1
   ```
2. Replace the `EMBEDDINGS_ENABLED` value with `"true"`, and make the build fetch the model:
   `cd backend && pip install -r requirements.txt && python ../scripts/fetch_embedding_model.py`
3. Add these variables so the database, uploads, exports and model live on the disk:
   `DB_PATH=/var/data/rfp_ai.db`, `UPLOAD_DIR=/var/data/uploads`, `KB_UPLOAD_DIR=/var/data/kb_uploads`,
   `EXPORT_DIR=/var/data/exports`, `MODELS_DIR=/var/data/models`.

Run a single instance only: the database is SQLite.

## Rotating keys

Change the three key variables on Render and redeploy. Delete the old keys at each provider. If a key was ever pasted
into a chat, treat it as exposed.

## Checking a deployment

```bash
cd backend && .venv/bin/python ../e2e/deploy_check.py --site https://your-app.vercel.app --api https://your-api.onrender.com
```

Add `--key <access key>` if you turned it on, and `--draft` to also detect conflicts, draft the sample RFP and export it
(this uses model quota and a few minutes). It reports whether the page reaches the API, whether the knowledge base
loads, whether the RFP imports, whether drafting and the conflict flags reach the page, and whether an export downloads
with its proper name.

We ran exactly this against the Render configuration on Python 3.12 with the page and API at different addresses: 7 of 7
checks passed, and the backend used 217 MB after drafting all 30 requirements.

## Limits to know

- One backend instance only, because the database is SQLite and drafting runs inside the API process.
- A drafting run that is interrupted by a restart can be resumed with **Start drafting**: it only drafts what is missing.
- Deploys do not happen automatically on the free blueprint (`autoDeploy: false`), because each one wipes the data. Press
  **Manual Deploy** on Render after you push changes.
