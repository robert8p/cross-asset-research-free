# Render health-check hotfix

Version 1.2.1 separates service liveness from database readiness.

- `/health` always returns HTTP 200 when the web process is alive, allowing Render to complete deployment.
- `/ready` performs the detailed Supabase readiness check and returns HTTP 503 when setup needs correction.
- The authenticated dashboard continues to display the exact bootstrap/database error in plain language.

This corrects the deployment loop where a recoverable Supabase configuration issue caused Render's liveness check itself to fail.
