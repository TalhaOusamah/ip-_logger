How to deploy this Flask app to Vercel

1) Add these files (already included):
- `api/index.py` — Vercel WSGI adapter entrypoint
- `requirements.txt` — Python dependencies
- `vercel.json` — Vercel routing/build config

2) Install Vercel CLI and log in:
```powershell
npm i -g vercel
vercel login
```

3) From the project root run:
```powershell
vercel --prod
```

Follow prompts to link or create a project.

4) Environment variables
Set the following env vars in the Vercel dashboard (or via `vercel env add`):
- `SECRET_KEY` (recommended)
- `ADMIN_PASSWORD` (if you want a custom admin password)
- `NOMINATIM_USER_AGENT` (optional)
- `PORT` (not required; Vercel provides a runtime port)

Notes and caveats
- The app writes `requests.jsonl` and `geocode_cache.json` to local disk. Serverless functions have ephemeral filesystems — logs will not persist across function instances. Use an external datastore (S3, database) for persistence in production.
- Vercel serverless functions have execution time limits. The app's reverse geocoding makes external requests and includes a rate limiter — consider moving geocoding to a background job or using a paid geocoding service.
