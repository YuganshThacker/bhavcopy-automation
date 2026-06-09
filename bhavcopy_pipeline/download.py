"""Download NSE and BSE UDiFF bhavcopy files for a given trading date.

Returns the path to a plain CSV on disk, or None if the file isn't published
for that date (holiday / not yet available).
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

import requests

from . import config

logger = logging.getLogger(__name__)

_NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
_BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bseindia.com/",
}


def _new_nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_NSE_HEADERS)
    # Prime cookies — NSE archive sometimes rejects cold requests.
    try:
        s.get("https://www.nseindia.com", timeout=config.HTTP_TIMEOUT)
    except requests.RequestException as e:  # non-fatal; archive CDN often works anyway
        logger.debug("NSE cookie priming failed (continuing): %s", e)
    return s


def download_nse(d: date, data_dir: Path, session: Optional[requests.Session] = None) -> Optional[Path]:
    """Download NSE bhavcopy zip for date d, extract, return CSV path (or None)."""
    date_str = d.strftime("%Y%m%d")
    url = config.NSE_URL_TMPL.format(date=date_str)
    session = session or _new_nse_session()
    try:
        resp = session.get(url, timeout=config.HTTP_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("NSE download error for %s: %s", d, e)
        return None

    if resp.status_code != 200 or not resp.content:
        logger.info("NSE bhavcopy not available for %s (HTTP %s)", d, resp.status_code)
        return None

    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except zipfile.BadZipFile:
        logger.warning("NSE response for %s was not a valid zip", d)
        return None

    inner = zf.namelist()[0]
    out_path = data_dir / f"nse_{date_str}.csv"
    out_path.write_bytes(zf.read(inner))
    logger.info("NSE bhavcopy %s -> %s (%d bytes)", d, out_path.name, out_path.stat().st_size)
    return out_path


def download_bse(d: date, data_dir: Path, session: Optional[requests.Session] = None) -> Optional[Path]:
    """Download BSE bhavcopy CSV for date d, return CSV path (or None)."""
    date_str = d.strftime("%Y%m%d")
    url = config.BSE_URL_TMPL.format(date=date_str)
    session = session or requests.Session()
    try:
        resp = session.get(url, headers=_BSE_HEADERS, timeout=config.HTTP_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("BSE download error for %s: %s", d, e)
        return None

    # BSE returns 200 with a tiny HTML error page when the file is absent.
    text_head = resp.content[:64].lstrip().lower()
    if resp.status_code != 200 or not resp.content or text_head.startswith(b"<"):
        logger.info("BSE bhavcopy not available for %s (HTTP %s)", d, resp.status_code)
        return None

    out_path = data_dir / f"bse_{date_str}.csv"
    out_path.write_bytes(resp.content)
    logger.info("BSE bhavcopy %s -> %s (%d bytes)", d, out_path.name, out_path.stat().st_size)
    return out_path
