# Export storage and retrieval

## Preferred Render-to-Supabase route

The `python -m app export` command has built-in private Supabase Storage upload.

Required Render environment variables:

```text
SUPABASE_STORAGE_UPLOAD=true
SUPABASE_URL=https://PROJECT_REF.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<legacy service_role key>
SUPABASE_STORAGE_BUCKET=cross-asset-research-exports
UNTOUCHED_ARCHIVE_PASSWORD=<strong separate password>
```

When export succeeds, the uploader:

1. checks whether the named bucket exists;
2. creates it as private when absent;
3. refuses to use it if it is public;
4. uploads the three timestamp-named ZIP files under `archives/`;
5. retries transient network/server failures;
6. reports the bucket, object path and byte size in the Render log.

Retrieve the discovery archive through Supabase Dashboard → **Storage** → `cross-asset-research-exports` → `archives` → select the discovery ZIP → **Download**. Do not preview or download the untouched archive during discovery.

The uploader uses Supabase’s standard object endpoint. Standard upload supports files up to 5 GB, but Supabase recommends resumable uploads above 6 MB. A failed standard upload restarts from byte zero. For unusually large archives or unstable networking, use the local route below.

## Local fallback

1. Install Python 3.12.
2. Download the private GitHub repository as ZIP and unzip it.
3. Open a terminal inside the repository folder.
4. Copy `.env.example` to `.env`.
5. Paste the same secrets and frozen research dates into `.env`. Never upload `.env` to GitHub.
6. Activate the environment variables using your operating system or terminal tooling.
7. Run:

```bash
pip install -r requirements.lock
python -m app export
```

8. Retrieve the files from the local `exports` folder.
9. Verify each archive hash against the adjacent `.sha256` output/log and each package’s internal `SHA256SUMS.txt`.

## Licensing boundary

Keep the Storage bucket private. The packages may contain exchange-licensed raw market data intended for your own analysis. Do not publish, share or expose them through a public bucket unless every source licence explicitly permits redistribution.
