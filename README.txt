Cross-Asset Research v1.4.2 bootstrap compatibility fix

Replace the repository-root file app/control.py with the file in this patch.
This removes the dashboard's dependency on Database.schema_ready() and performs
the same read-only schema check directly against PostgreSQL.
