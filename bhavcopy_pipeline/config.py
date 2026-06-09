"""Central configuration, read from environment variables.

The pipeline connects to Supabase Postgres via the transaction pooler. Set these
in your shell (.env for local) or as Render/Railway env vars:

    DB_HOST       e.g. aws-1-ap-south-1.pooler.supabase.com
    DB_PORT       e.g. 6543
    DB_NAME       e.g. postgres
    DB_USER       e.g. postgres.<project-ref>
    DB_PASSWORD   <secret>
"""
from __future__ import annotations

import os

# ─── Database ─────────────────────────────────────────────────────────────────
DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = int(os.environ.get("DB_PORT", "6543"))
DB_NAME = os.environ.get("DB_NAME", "postgres")
DB_USER = os.environ.get("DB_USER", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

# ─── Download sources (UDiFF format, identical schema for both exchanges) ──────
NSE_URL_TMPL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{date}_F_0000.csv.zip"
)
BSE_URL_TMPL = (
    "https://www.bseindia.com/download/BhavCopy/Equity/"
    "BhavCopy_BSE_CM_0_0_0_{date}_F_0000.CSV"
)

# Where downloaded files are staged. Ephemeral on Render/Railway — that's fine,
# we re-download each run and the DB is the source of truth.
DATA_DIR = os.environ.get("BHAVCOPY_DATA_DIR", "/tmp/bhavcopy")

HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "30"))

# ─── Series handling ──────────────────────────────────────────────────────────
# NSE security series we keep (matches the existing price_history history).
NSE_SERIES = {"EQ", "BE", "BZ", "SM", "ST"}

# All BSE equity rows are stored under this single series tag, matching the
# existing BSE rows already in price_history.
BSE_SERIES_TAG = "BSE"


def require_db_password() -> str:
    if not DB_PASSWORD:
        raise SystemExit("DB_PASSWORD env var is required")
    return DB_PASSWORD
