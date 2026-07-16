# Troubleshooting — No-Code Edition

Use the browser dashboard first. It shows the failed step and technical detail only when expanded.

## Render says Deploy failed

Most likely causes:

- `SUPABASE_DB_URL` was copied incompletely.
- The database password placeholder was not replaced.
- One of the six prompted values was left blank.

Correct the value under the Render service's **Environment** tab and redeploy.

## The dashboard does not accept the password

- Username is always `admin`.
- Password is the exact `DASHBOARD_PASSWORD` entered during deployment.
- Update the Render environment variable and redeploy if it was lost.

## A collection job failed

Click **Resume safely**. The historical collector is checkpointed and duplicate-safe.

## Export failed

Click **Recreate export**. This reruns discovery-only quality checks and archive creation without repeating the historical collection.

## Optional UK or German yields fail

They are optional in the free-data edition and do not block the required dataset. Their limitations remain disclosed in the export.

## Discovery download is not shown

The archive has not uploaded successfully yet. Expand the latest job's technical details. Once the export succeeds, the button appears automatically.
