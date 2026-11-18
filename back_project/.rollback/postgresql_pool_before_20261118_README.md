# PostgreSQL pool rollback record

Created before the PostgreSQL connection-pool change on 2026-11-18.

Backup archive:

- `postgresql_pool_before_20261118.zip`
- SHA-256: `230C0C3B4702531EEE01774E42F96FBC80518C8A014E76AE22650D70E0312BEE`

Restore the archive entries to these paths:

- `db.py` -> `back_project/app/utils/db.py`
- `common.py` -> `back_project/app/repositories/common.py`
- `transaction.py` -> `back_project/app/repositories/transaction.py`
- `server.py` -> `back_project/app/server.py`
- `requirements.txt` -> `back_project/requirements.txt`

Also remove the wheel added by this change:

- `C:\Users\USER\Desktop\offline\ADD\backend\python_wheels\psycopg_pool-3.2.8-py3-none-any.whl`

The frontend, `.env`, and `back_project/main.py` were not changed.
