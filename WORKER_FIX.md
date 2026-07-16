# v1.3 worker fix

The dashboard no longer runs the 90-day collection inside the web process. Clicking a dashboard button now creates a database job only. A separate Render background worker performs the data work, so the dashboard remains available.

## Existing deployment upgrade

1. Upload/replace the contents of this package in the existing GitHub repository.
2. In Render, open the Blueprint and choose **Sync Blueprint**.
3. Approve creation of `cross-asset-research-worker` on the Starter plan.
4. Wait for both services to show live/running.
5. Open the dashboard. If the earlier job is marked failed, click **Resume safely** once.

The worker receives the existing six credentials from the dashboard service automatically. Do not recreate Supabase or API keys.
