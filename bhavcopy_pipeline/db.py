"""Database connection helper and the run-log table."""
from __future__ import annotations

import logging

import psycopg2

from . import config

logger = logging.getLogger(__name__)


def make_conn():
    """Open a resilient connection to Supabase Postgres (pooler)."""
    return psycopg2.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        dbname=config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        sslmode="require",
        connect_timeout=30,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
        options="-c statement_timeout=0",
    )


RUN_LOG_DDL = """
CREATE TABLE IF NOT EXISTS public.bhavcopy_runs (
    id              bigserial PRIMARY KEY,
    trade_date      date        NOT NULL,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text        NOT NULL DEFAULT 'running',  -- running | success | no_data | error
    nse_price_rows  integer     DEFAULT 0,
    bse_price_rows  integer     DEFAULT 0,
    indicator_rows  integer     DEFAULT 0,
    error           text
);
CREATE INDEX IF NOT EXISTS idx_bhavcopy_runs_date ON public.bhavcopy_runs (trade_date);
"""


def ensure_run_log(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(RUN_LOG_DDL)
    conn.commit()


def start_run(conn, trade_date) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.bhavcopy_runs (trade_date) VALUES (%s) RETURNING id",
            (trade_date,),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn, run_id: int, status: str, nse_rows: int = 0,
               bse_rows: int = 0, indicator_rows: int = 0, error: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE public.bhavcopy_runs
                  SET finished_at = now(), status = %s,
                      nse_price_rows = %s, bse_price_rows = %s,
                      indicator_rows = %s, error = %s
                WHERE id = %s""",
            (status, nse_rows, bse_rows, indicator_rows, (error or None), run_id),
        )
    conn.commit()
