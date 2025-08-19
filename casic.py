"""Utilities to build CASIC GPS ephemeris frames.

This module exposes helpers to download RINEX navigation files and
convert them into CASIC binary frames. The binary layout is based on
internal documentation and may require adjustments for specific
receivers.
"""

from __future__ import annotations

import gzip
import os
import struct
from dataclasses import dataclass
import logging
from typing import Any, Iterable, List, Optional
from datetime import datetime, timezone
import re
import warnings

try:  # Optional dependencies
    import georinex as grx  # type: ignore[import-untyped]
except Exception:  # pragma: no cover - handled at runtime
    grx = None

try:  # Optional dependencies
    import requests  # type: ignore[import-untyped]
except Exception:  # pragma: no cover - handled at runtime
    requests = None

try:  # Optional dependency for merge patching
    import xarray as xr  # type: ignore[import-untyped]
except Exception:  # pragma: no cover
    xr = None  # type: ignore[assignment]

CASIC_HDR0 = 0xBA
CASIC_HDR1 = 0xCE
logger = logging.getLogger(__name__)


@dataclass
class GpsEph:
    """Subset of GPS ephemeris parameters used to build frames."""

    prn: int
    week: int
    toe_s: float
    sqrtA: float
    e: float
    i0: float
    omega0: float
    w: float
    M0: float
    deltaN: float
    OmegaDot: float
    IDOT: float
    Cuc: float
    Cus: float
    Crc: float
    Crs: float
    Cic: float
    Cis: float
    af0: float
    af1: float
    af2: float
    ura: float
    svHealth: int


def casic_checksum32(cls: int, msg_id: int, payload: bytes) -> bytes:
    """Return the 32-bit CASIC checksum in little-endian order."""
    plen = len(payload)
    acc = (cls << 24) | (msg_id << 16) | (plen & 0xFFFF)
    if plen % 4 != 0:
        payload += b"\x00" * (4 - (plen % 4))
    for i in range(0, len(payload), 4):
        (word,) = struct.unpack("<I", payload[i:i + 4])
        acc = (acc + word) & 0xFFFFFFFF
    return struct.pack("<I", acc)


def build_casic_frame(cls: int, msg_id: int, payload: bytes) -> bytes:
    """Assemble a CASIC frame including headers and checksum."""
    header = struct.pack("<BBHB", CASIC_HDR0, CASIC_HDR1, len(payload), cls)
    header += struct.pack("<B", msg_id)
    checksum = casic_checksum32(cls, msg_id, payload)
    return header + payload + checksum


def rinex_to_gps_eph(ds: Any) -> Iterable[GpsEph]:
    """Extract GPS ephemerides from a georinex dataset."""
    svs = [sv for sv in getattr(ds, "sv", []) if str(sv).startswith("G")]
    for sv in svs:
        sel = ds.sel(sv=sv).dropna("time", how="all")
        if sel.time.size == 0:
            continue
        idx = int(sel.time.size - 1)
        prn = int(str(sv)[1:])

        def get(name: str, default: float = 0.0) -> float:
            if name in sel.data_vars:
                return float(sel[name].values[idx])
            return default

        yield GpsEph(
            prn=prn,
            week=int(get("week", 0)),
            toe_s=get("toe", 0.0),
            sqrtA=get("sqrtA", 0.0),
            e=get("e", 0.0),
            i0=get("i0", 0.0),
            omega0=get("OMEGA0", 0.0),
            w=get("omega", 0.0),
            M0=get("M0", 0.0),
            deltaN=get("DeltaN", 0.0),
            OmegaDot=get("OMEGADOT", 0.0),
            IDOT=get("IDOT", 0.0),
            Cuc=get("Cuc", 0.0),
            Cus=get("Cus", 0.0),
            Crc=get("Crc", 0.0),
            Crs=get("Crs", 0.0),
            Cic=get("Cic", 0.0),
            Cis=get("Cis", 0.0),
            af0=get("af0", 0.0),
            af1=get("af1", 0.0),
            af2=get("af2", 0.0),
            ura=get("SVaccuracy", 0.0),
            svHealth=int(get("SVhealth", 0.0)),
        )


def build_payload_gps_eph_casic(e: GpsEph) -> bytes:
    """Pack GPS ephemeris into a CASIC payload.

    The layout (72 bytes) should be validated against hardware
    documentation. This implementation mirrors a typical structure and
    may require adjustments.
    """

    parts = [
        struct.pack("<BBH", e.prn & 0xFF, e.svHealth & 0xFF, e.week & 0xFFFF),
        struct.pack("<d", e.toe_s),
        struct.pack("<d", e.sqrtA),
        struct.pack("<d", e.e),
        struct.pack("<d", e.i0),
        struct.pack("<d", e.omega0),
        struct.pack("<d", e.w),
        struct.pack("<d", e.M0),
        struct.pack("<fff", e.deltaN, e.OmegaDot, e.IDOT),
        struct.pack("<ffffff", e.Cuc, e.Cus, e.Crc, e.Crs, e.Cic, e.Cis),
        struct.pack("<dff", e.af0, e.af1, e.af2),
        struct.pack("<B", int(e.ura) & 0xFF),
    ]
    payload = b"".join(parts)
    if len(payload) < 72:
        payload += b"\x00" * (72 - len(payload))
    return payload[:72]


def make_casic_gps_eph_frames(ds: Any) -> Iterable[bytes]:
    """Yield CASIC frames for all GPS ephemerides in *ds*."""
    for eph in rinex_to_gps_eph(ds):
        payload = build_payload_gps_eph_casic(eph)
        yield build_casic_frame(0x08, 0x07, payload)


# ---- Alternative builders (ported from test_eph.py) ----
# The following helpers encode CASIC frames using scaled integer fields
# and pick the most recent (non-NaN) ephemeris per SV, matching the
# device expectations validated in test_eph.py.

INT16_MIN, INT16_MAX = -32768, 32767
INT32_MIN, INT32_MAX = -2147483648, 2147483647

def _clamp_i16(x: int) -> int:
    return INT16_MIN if x < INT16_MIN else (INT16_MAX if x > INT16_MAX else x)

def _clamp_i32(x: int) -> int:
    return INT32_MIN if x < INT32_MIN else (INT32_MAX if x > INT32_MAX else x)

def _wrap_pm_pi(rad: float) -> float:
    y = (rad + 3.141592653589793) % (2.0 * 3.141592653589793)
    if y < 0:
        y += 2.0 * 3.141592653589793
    return y - 3.141592653589793

def _rad_to_semi(rad: float) -> float:
    # semicircles are radians/pi
    return rad / 3.141592653589793

def _radps_to_semips(radps: float) -> float:
    return radps / 3.141592653589793

def _iN_scaled(x: float, pow2_exp: int, nbytes: int) -> bytes:
    raw = int(round(x * (2.0 ** (-pow2_exp))))
    if nbytes == 4:
        return struct.pack("<i", _clamp_i32(raw))
    if nbytes == 2:
        return struct.pack("<h", _clamp_i16(raw))
    if nbytes == 1:
        raw = max(-128, min(127, raw))
        return struct.pack("<b", raw)
    raise ValueError("nbytes must be 1,2,4")

def _uN_scaled(x: float, pow2_exp: int, nbytes: int) -> bytes:
    raw = int(round(x * (2.0 ** (-pow2_exp))))
    if nbytes == 4:
        raw = max(0, min(0xFFFFFFFF, raw))
        return struct.pack("<I", raw)
    if nbytes == 2:
        raw = max(0, min(0xFFFF, raw))
        return struct.pack("<H", raw)
    if nbytes == 1:
        raw = max(0, min(0xFF, raw))
        return struct.pack("<B", raw)
    raise ValueError("nbytes must be 1,2,4")

def make_msg_gpseph_scaled(
    *,
    svid: int,
    sqrtA: float,
    e: float,
    omega: float,
    M0: float,
    i0: float,
    OMEGA0: float,
    OMEGADOT: float,
    DeltaN: float,
    IDOT: float,
    cuc: float,
    cus: float,
    crc: float,
    crs: float,
    cic: float,
    cis: float,
    toe: float,
    week: int,
    toc: float,
    af0: float,
    af1: float,
    af2: float,
    tgd: float = 0.0,
    iodc: int = 0,
    ura: int = 0,
    health: int = 0,
    valid: int = 3,
) -> bytes:
    omega_semi  = _rad_to_semi(_wrap_pm_pi(omega))
    M0_semi     = _rad_to_semi(_wrap_pm_pi(M0))
    i0_semi     = _rad_to_semi(_wrap_pm_pi(i0))
    OMEGA0_semi = _rad_to_semi(_wrap_pm_pi(OMEGA0))
    OMEGADOT_semips = _radps_to_semips(OMEGADOT)
    DeltaN_semips   = _radps_to_semips(DeltaN)
    IDOT_semips     = _radps_to_semips(IDOT)
    cuc_semi = _rad_to_semi(cuc)
    cus_semi = _rad_to_semi(cus)
    cic_semi = _rad_to_semi(cic)
    cis_semi = _rad_to_semi(cis)

    parts = [struct.pack("<I", 0)]  # reserved/vendor-specific
    parts += [
        _uN_scaled(sqrtA, -19, 4),
        _uN_scaled(e,     -33, 4),
        struct.pack("<i", _clamp_i32(int(round(omega_semi  * (2**31))))),
        struct.pack("<i", _clamp_i32(int(round(M0_semi     * (2**31))))),
        struct.pack("<i", _clamp_i32(int(round(i0_semi     * (2**31))))),
        struct.pack("<i", _clamp_i32(int(round(OMEGA0_semi * (2**31))))),
    ]
    parts += [struct.pack("<i", _clamp_i32(int(round(OMEGADOT_semips * (2**43)))))]
    parts += [
        struct.pack("<h", _clamp_i16(int(round(DeltaN_semips * (2**43))))),
        struct.pack("<h", _clamp_i16(int(round(IDOT_semips   * (2**43))))),
    ]
    parts += [
        struct.pack("<h", _clamp_i16(int(round(cuc_semi * (2**29))))),
        struct.pack("<h", _clamp_i16(int(round(cus_semi * (2**29))))),
    ]
    parts += [
        _iN_scaled(crc,  -5, 2),
        _iN_scaled(crs,  -5, 2),
    ]
    parts += [
        struct.pack("<h", _clamp_i16(int(round(cic_semi * (2**29))))),
        struct.pack("<h", _clamp_i16(int(round(cis_semi * (2**29))))),
    ]
    parts += [
        _uN_scaled(toe,  +4, 2),
        struct.pack("<H", week & 0xFFFF),
        _uN_scaled(toc,  +4, 4),
        _iN_scaled(af0, -31, 4),
        _iN_scaled(af1, -43, 2),
        struct.pack("<b", max(-128, min(127, int(round(af2 * (2**55)))))),
        struct.pack("<b", max(-128, min(127, int(round(tgd * (2**31)))))),
        struct.pack("<H", iodc & 0xFFFF),
        struct.pack("<B", ura & 0xFF),
        struct.pack("<B", health & 0xFF),
        struct.pack("<B", svid & 0xFF),
        struct.pack("<B", valid & 0xFF),
        struct.pack("<H", 0),
    ]
    payload = b"".join(parts)
    return build_casic_frame(0x08, 0x07, payload)

def _pick_latest_per_gps_sv(ds: Any) -> list[dict[str, float]]:
    """Return list of ephemeris dicts for the latest valid record per GPS SV.

    This mirrors the logic from test_eph.py: for each SV 'Gxx', choose
    the last non-NaN index based on 'sqrtA' presence, then extract fields
    with compatibility across GeoRinex versions.
    """
    try:
        import numpy as np  # type: ignore[import-untyped]
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("numpy not installed") from exc
    try:
        sv_values = ds.sv.values  # xarray coordinate -> numpy array
    except Exception:
        sv_values = []
    svs = [sv for sv in sv_values if str(sv).startswith("G")]
    if not svs:
        return []
    out: list[dict[str, float]] = []
    for sv in svs:
        rows = ds.sel(sv=sv)
        if "sqrtA" not in rows.variables:
            continue
        values = rows["sqrtA"].values  # xarray -> numpy
        valid_idx = np.where(~np.isnan(values))[0]
        if valid_idx.size == 0:
            continue
        r = rows.isel(time=int(valid_idx[-1]))

        def g_any(names: str | list[str], default: float = 0.0) -> float:
            keys = [names] if isinstance(names, str) else names
            for name in keys:
                if name in r.variables:
                    try:
                        return float(r[name].values)
                    except Exception:
                        pass
            return default

        # Epochs
        # toc from epoch timestamp; convert to GPST seconds-of-week
        try:
            import numpy as _np  # type: ignore[import-untyped]
        except Exception:  # pragma: no cover
            _np = None
        toc = 0.0
        if _np is not None:
            try:
                t_posix = _np.datetime64(r["time"].values, "s").astype(int)
                gps_epoch = datetime(1980, 1, 6, tzinfo=timezone.utc).timestamp()
                sec_since_gps = float(t_posix - gps_epoch)
                if sec_since_gps < 0:
                    sec_since_gps = 0.0
                toc = sec_since_gps % (7 * 86400)
            except Exception:
                toc = 0.0

        # Compute GPS week if not present
        week_val = g_any(["week"], float("nan"))
        if not (week_val == week_val):  # NaN check
            try:
                # seconds since GPS epoch
                t_posix = _np.datetime64(r["time"].values, "s").astype(int)
                gps_epoch = datetime(1980, 1, 6, tzinfo=timezone.utc).timestamp()
                sec_since_gps = float(t_posix - gps_epoch)
                week_val = int(sec_since_gps // (7 * 86400))
            except Exception:
                week_val = 0

        item = {
            "svid": int(str(sv)[1:]),
            "sqrtA": g_any(["sqrtA"]),
            "e": g_any(["e", "Eccentricity"]),
            "i0": g_any(["i0", "Io"]),
            "OMEGA0": g_any(["OMEGA0", "Omega0"]),
            "omega": g_any(["omega"]),
            "M0": g_any(["M0"]),
            "DeltaN": g_any(["DeltaN"]),
            "OMEGADOT": g_any(["OMEGADOT", "OmegaDot"]),
            "IDOT": g_any(["IDOT"]),
            "cuc": g_any(["cuc", "Cuc"]),
            "cus": g_any(["cus", "Cus"]),
            "crc": g_any(["crc", "Crc"]),
            "crs": g_any(["crs", "Crs"]),
            "cic": g_any(["cic", "Cic"]),
            "cis": g_any(["cis", "Cis"]),
            "toe": g_any(["toe", "Toe"]),
            "week": int(week_val) if isinstance(week_val, (int, float)) else 0,
            "toc": toc,
            "af0": g_any(["af0", "SVclockBias"]),
            "af1": g_any(["af1", "SVclockDrift"]),
            "af2": g_any(["af2", "SVclockDriftRate"]),
            "tgd": g_any(["tgd", "TGD"], 0.0),
            "iodc": int(g_any(["IODC"], 0.0)),
            "ura": int(g_any(["URA"], 0.0)),
            "health": int(g_any(["health"], 0.0)),
        }
        out.append(item)
    return out

def build_latest_gps_bin_from_ds(ds: Any) -> bytes:
    """Concatenate CASIC GPSEPH frames for the latest eph per SV in ds."""
    frames: list[bytes] = []
    for it in _pick_latest_per_gps_sv(ds):
        frames.append(
            make_msg_gpseph_scaled(
                svid=it["svid"],
                sqrtA=it["sqrtA"],
                e=it["e"],
                omega=it["omega"],
                M0=it["M0"],
                i0=it["i0"],
                OMEGA0=it["OMEGA0"],
                OMEGADOT=it["OMEGADOT"],
                DeltaN=it["DeltaN"],
                IDOT=it["IDOT"],
                cuc=it["cuc"],
                cus=it["cus"],
                crc=it["crc"],
                crs=it["crs"],
                cic=it["cic"],
                cis=it["cis"],
                toe=it["toe"],
                week=int(it["week"]),
                toc=it["toc"],
                af0=it["af0"],
                af1=it["af1"],
                af2=it["af2"],
                tgd=it.get("tgd", 0.0),
                iodc=int(it.get("iodc", 0)),
                ura=int(it.get("ura", 0)),
                health=int(it.get("health", 0)),
            )
        )
    return b"".join(frames)


def build_casic_bin_latest(
    year: int,
    doy: int,
    hour: Optional[int] = None,
    workdir: Optional[str] = None,
    url_template: Optional[str] = None,
    token: Optional[str] = None,
) -> bytes:
    """Fetch RINEX (daily or hourly) and return a concatenated GPSEPH bin.

    - If ``url_template`` includes ``{hour}``/``{HH}``, uses hourly; if it
      points to a directory, lists and picks the best (requested hour,
      else latest; fallback hour-1 on 404 handled by fetch).
    - Otherwise downloads daily BRDC.
    - Parses the RINEX via georinex and builds CASIC frames using the
      scaled-field builder from test_eph.py logic, selecting the latest
      valid ephemeris per SV.
    """
    workdir = workdir or os.getcwd()
    os.makedirs(workdir, exist_ok=True)

    inferred_hour = False
    if url_template and hour is None and ("{hour" in url_template or "{HH" in url_template):
        hour = datetime.now(timezone.utc).hour
        inferred_hour = True
    logger.info(
        "Building latest CASIC bin",
        extra={"year": year, "doy": doy, "hour": hour, "inferred_hour": inferred_hour},
    )

    def _gz_name(h: Optional[int]) -> str:
        if h is not None:
            return f"hour{doy:03d}{h}.{year % 100:02d}n.gz"
        return f"brdc{doy:03d}0.{year % 100:02d}n.gz"

    gz_name = _gz_name(hour)
    gz_path = os.path.join(workdir, gz_name)

    def _format_url(h: Optional[int]) -> str:
        if not url_template:
            return ""
        try:
            return url_template.format(
                year=year,
                doy=doy,
                yy=year % 100,
                hour=(h if h is not None else ""),
                HH=(f"{h:02d}" if h is not None else ""),
            )
        except Exception:
            return url_template

    url = None
    if url_template:
        url = _format_url(hour)
        if url and not url.endswith('.gz'):
            if not url.endswith('/'):
                url += '/'
            fname, resolved_hour = discover_hourly_filename(url, year, doy, hour, token)
            url = url + fname
            if hour is None:
                hour = resolved_hour
            gz_name = fname
            gz_path = os.path.join(workdir, gz_name)
        try:
            try:
                fetch_rinex_brdc(year, doy, gz_path, url=url, timeout=10, token=token)
            except RuntimeError as exc:
                msg = str(exc)
                if hour is not None and "404" in msg:
                    prev_hour = (hour - 1) % 24
                    alt_name = _gz_name(prev_hour)
                    gz_path = os.path.join(workdir, alt_name)
                    alt_url = _format_url(prev_hour)
                    logger.info(
                        "Retry previous hour (bin)",
                        extra={"prev_hour": prev_hour, "url": alt_url, "gz_path": gz_path},
                    )
                    fetch_rinex_brdc(year, doy, gz_path, url=alt_url or None, timeout=10, token=token)
                    hour = prev_hour
                else:
                    raise
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"RINEX processing failed: {exc}") from exc
    else:
        # No template: iterate BKG candidates for requested day, then fallback to previous day
        last_err: Exception | None = None
        def _try_day(y: int, d: int) -> bytes | None:
            nonlocal last_err
            for url in _bkg_v3_candidates(y, d):
                try:
                    if requests is None:  # pragma: no cover
                        raise RuntimeError("requests not installed")
                    r = requests.get(
                        url,
                        timeout=20,
                        allow_redirects=True,
                        headers={"User-Agent": "agnss-builder/1.0"},
                    )
                    if r.status_code != 200 or not r.content or len(r.content) <= 1024:
                        continue
                    fname = url.rsplit('/', 1)[-1]
                    path_gz = os.path.join(workdir, fname)
                    with open(path_gz, 'wb') as fh:
                        fh.write(r.content)
                    nav_path = open_rinex_file(path_gz)
                    ds = parse_rinex_nav_filtered(nav_path, systems="G")
                    bin_bytes = build_latest_gps_bin_from_ds(ds)
                    if bin_bytes:
                        logger.info(
                            "Built CASIC from BKG candidate",
                            extra={"url": url, "bytes": len(bin_bytes), "year": y, "doy": d},
                        )
                        return bin_bytes
                except Exception as exc:  # pragma: no cover
                    last_err = exc
                    logger.debug("Candidate failed", extra={"url": url, "error": str(exc)})
                    continue
            return None

        # Try requested day first
        out = _try_day(year, doy)
        if out:
            return out
        # Fallback to previous day (same logic as test_eph CLI)
        try:
            import datetime as _dt
            first = _dt.date(year, 1, 1)
            ddate = first + _dt.timedelta(days=doy - 1)
            prev = ddate - _dt.timedelta(days=1)
            prev_year = prev.year
            prev_doy = int(prev.strftime("%j"))
        except Exception:
            prev_year, prev_doy = year, max(1, doy - 1)
        out = _try_day(prev_year, prev_doy)
        if out:
            return out
        if last_err:
            raise RuntimeError(f"RINEX processing failed: {last_err}") from last_err
        raise RuntimeError("RINEX processing failed: no valid candidate produced frames (today or yesterday)")
    try:
        nav_path = open_rinex_file(gz_path)
        ds = parse_rinex_nav_filtered(nav_path, systems="G")
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"RINEX processing failed: {exc}") from exc

    return build_latest_gps_bin_from_ds(ds)


def default_brdc_url(year: int, doy: int) -> str:
    """Return the default RINEX navigation URL for a given day.

    The default points to the NASA CDDIS archive which requires an
    authentication token. See :func:`fetch_rinex_brdc` for details.
    """
    yy = year % 100
    fname = f"brdc{doy:03d}0.{yy:02d}n.gz"
    url = (
        "https://cddis.nasa.gov/archive/gnss/data/daily/"
        f"{year}/{doy:03d}/{yy:02d}n/{fname}"
    )
    logger.debug("Default BRDC URL built", extra={"year": year, "doy": doy, "url": url})
    return url


# ---- BKG/IGS BRDC v3 candidates (same as test_eph.py) ----
BKG_BASE = "https://igs.bkg.bund.de/root_ftp/IGS/BRDC/{yyyy}/{ddd:03d}/"

def _bkg_v3_candidates(year: int, doy: int) -> list[str]:
    stamp = f"{year}{doy:03d}0000"
    names = [
        f"BRDC00IGS_R_{stamp}_01D_MN.rnx.gz",
        f"BRDC00WRD_R_{stamp}_01D_MN.rnx.gz",
        f"BRDM00DLR_S_{stamp}_01D_MN.rnx.gz",
        f"BRD400DLR_S_{stamp}_01D_MN.rnx.gz",
        f"BRDC00WRD_S_{stamp}_01D_MN.rnx.gz",
    ]
    yy = year % 100
    names.append(f"brdc{doy:03d}0.{yy:02d}n.gz")
    base = BKG_BASE.format(yyyy=year, ddd=doy)
    return [base + n for n in names]

def fetch_best_nav_bkg(year: int, doy: int, out_dir: str, timeout: int = 20) -> str:
    if requests is None:  # pragma: no cover
        raise RuntimeError("requests not installed")
    os.makedirs(out_dir, exist_ok=True)
    sess = requests.Session()
    sess.headers.update({"User-Agent": "trackteur-analyse/1.0 (+https://www.trackteur.cc)"})
    last_url: str | None = None
    for url in _bkg_v3_candidates(year, doy):
        last_url = url
        try:
            r = sess.get(url, timeout=timeout, allow_redirects=True)
            if r.status_code == 200 and r.content and len(r.content) > 1024:
                fname = url.rsplit("/", 1)[-1]
                out_path = os.path.join(out_dir, fname)
                with open(out_path, "wb") as fh:
                    fh.write(r.content)
                logger.info("Fetched BKG RINEX", extra={"url": url, "bytes": len(r.content)})
                return out_path
            logger.debug("Skip candidate", extra={"url": url, "status": r.status_code})
        except Exception as exc:  # pragma: no cover
            logger.debug("Candidate error", extra={"url": url, "error": str(exc)})
            continue
    raise RuntimeError(f"No RINEX candidate succeeded (last: {last_url})")


def fetch_rinex_brdc(
    year: int,
    doy: int,
    out_path: str,
    url: Optional[str] = None,
    timeout: int = 10,
    token: Optional[str] = None,
) -> str:
    """Download a RINEX navigation file.

    If ``token`` is provided (or the ``CDDIS_TOKEN`` environment variable
    is set), it is sent as a Bearer token to authenticate against the
    CDDIS archive.
    """
    if requests is None:  # pragma: no cover - dependency check
        raise RuntimeError("requests not installed")
    url = url or default_brdc_url(year, doy)
    headers: dict[str, str] = {"User-Agent": "trackteur-analyse/1.0 (+https://www.trackteur.cc)"}
    token_in = token or os.getenv("CDDIS_TOKEN")
    if token_in:
        tok = str(token_in).strip()
        # Accept either raw token or full "Bearer ..." value
        if tok.lower().startswith("bearer "):
            headers["Authorization"] = tok
        else:
            headers["Authorization"] = f"Bearer {tok}"
    logger.info(
        "Fetching RINEX",
        extra={
            "url": url,
            "timeout": timeout,
            "auth": bool(token),
        },
    )
    try:
        resp = requests.get(url, timeout=timeout, headers=headers or None, allow_redirects=True)
        logger.debug(
            "RINEX response",
            extra={
                "status_code": getattr(resp, "status_code", None),
                "length": len(getattr(resp, "content", b"")),
                "final_url": getattr(resp, "url", url),
                "content_type": (resp.headers.get("Content-Type") if hasattr(resp, "headers") else None),
            },
        )
        resp.raise_for_status()
    except Exception as exc:  # pragma: no cover - network failure
        logger.exception("Failed to fetch RINEX", extra={"url": url})
        raise RuntimeError(f"failed to fetch {url}: {exc}") from exc
    data = resp.content
    # Heuristic: if expecting .gz but content does not start with gzip magic
    if url.endswith('.gz') and not data.startswith(b"\x1f\x8b"):
        head = data[:120]
        ct = resp.headers.get("Content-Type", "") if hasattr(resp, "headers") else ""
        final_url = getattr(resp, "url", url)
        logger.error(
            "Unexpected non-gzip content",
            extra={"url": final_url, "content_type": ct, "head": head},
        )
        raise RuntimeError(
            f"unexpected content (not gzip): status={getattr(resp, 'status_code', '?')} "
            f"content_type={ct!r} url={final_url} head={head!r}. "
            "Check authentication token or URL template."
        )
    with open(out_path, "wb") as fh:
        fh.write(data)
    logger.debug("Saved RINEX file", extra={"path": out_path, "bytes": len(resp.content)})
    return out_path


def open_rinex_file(path: str) -> str:
    """Return path to decompressed RINEX file."""
    if path.endswith(".gz"):
        try:
            with gzip.open(path, "rb") as gz:
                raw = gz.read()
        except Exception as exc:
            # Provide a clearer error if file is actually HTML or similar
            try:
                with open(path, "rb") as fh:
                    head = fh.read(200)
            except Exception:
                head = b""
            logger.error(
                "Failed to decompress RINEX",
                extra={"path": path, "error": str(exc), "head": head},
            )
            raise
        nav_path = path[:-3]
        with open(nav_path, "wb") as fh:
            fh.write(raw)
        logger.debug(
            "Decompressed RINEX",
            extra={"gz_path": path, "nav_path": nav_path, "bytes": len(raw)},
        )
        return nav_path
    logger.debug("RINEX not compressed", extra={"path": path})
    return path


def parse_rinex_nav(path: str) -> Any:
    """Parse a RINEX navigation file using georinex.

    Note: To avoid GeoRinex/xarray merge pitfalls and noisy warnings,
    we patch xr.merge defaults and filter to GPS by default.
    """
    return parse_rinex_nav_filtered(path, systems="G")


class _XRMergeOuter:
    """Context manager to set safer defaults for xarray.merge.

    Mirrors the approach from test_eph.py to avoid AlignmentError and
    adopt future defaults proactively.
    """

    def __enter__(self):
        if xr is None:  # pragma: no cover
            self._orig = None
            return None
        self._orig = xr.merge
        def _merge_patched(objs, *args, **kwargs):
            kwargs.setdefault("join", "outer")
            kwargs.setdefault("compat", "no_conflicts")
            return self._orig(objs, *args, **kwargs)
        xr.merge = _merge_patched  # type: ignore[assignment]
        return xr.merge

    def __exit__(self, exc_type, exc, tb):
        if xr is not None and getattr(self, "_orig", None):  # pragma: no cover
            xr.merge = self._orig  # type: ignore[assignment]


def parse_rinex_nav_filtered(path: str, systems: str = "G") -> Any:
    if grx is None:  # pragma: no cover - dependency check
        raise RuntimeError("georinex not installed")
    logger.info(
        "Parsing RINEX with georinex",
        extra={"path": path, "systems": systems},
    )
    with _XRMergeOuter():
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", category=FutureWarning, module=r"xarray.*|georinex.*|numpy.*"
            )
            ds = grx.load(path, use=systems)
    try:
        sv_count = len(getattr(ds, "sv", []))
    except Exception:
        sv_count = None
    logger.debug("Parsed RINEX dataset", extra={"sv_count": sv_count})
    return ds


def _auth_headers(token: Optional[str]) -> dict[str, str]:
    hdrs: dict[str, str] = {"User-Agent": "trackteur-analyse/1.0 (+https://www.trackteur.cc)"}
    if token:
        tok = str(token).strip()
        if tok:
            if tok.lower().startswith("bearer "):
                hdrs["Authorization"] = tok
            else:
                hdrs["Authorization"] = f"Bearer {tok}"
    return hdrs


def discover_hourly_filename(
    base_dir_url: str,
    year: int,
    doy: int,
    hour: Optional[int],
    token: Optional[str],
    timeout: int = 10,
) -> tuple[str, int]:
    """Return the best-matching hourly file name and resolved hour.

    Fetches the directory listing at ``base_dir_url`` and finds files
    matching the pattern for GPS nav: ``hour{doy:03d}{H}.{yy:02d}n.gz``.
    If ``hour`` is provided, prefers that hour; otherwise chooses the
    latest available hour.
    """
    if requests is None:  # pragma: no cover
        raise RuntimeError("requests not installed")
    yy = year % 100
    pattern = re.compile(rf"hour{doy:03d}(\d{{1,2}})\.{yy:02d}n\.gz$")
    headers = _auth_headers(token)
    logger.info("Discovering hourly file", extra={"dir": base_dir_url, "hour": hour})
    resp = requests.get(base_dir_url, headers=headers or None, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    text = resp.text
    # Find candidate filenames via regex (works on simple HTML listing, XML, or text)
    candidates = set(m.group(0) for m in pattern.finditer(text))
    # Some listings include href="filename"; catch that too
    href_pat = re.compile(r'href=[\"\']([^\"\']+)[\"\']')
    for hm in href_pat.finditer(text):
        name = hm.group(1)
        if pattern.search(name):
            # Extract just the filename segment
            fname = name.rsplit('/', 1)[-1]
            candidates.add(fname)
    if not candidates:
        logger.error("No hourly files found in directory", extra={"dir": base_dir_url})
        raise RuntimeError("no hourly files found in directory")
    # Map hour->filename
    hour_map: dict[int, str] = {}
    for fname in candidates:
        m = pattern.search(fname)
        if not m:
            continue
        try:
            h = int(m.group(1))
            if 0 <= h <= 23:
                hour_map[h] = fname
        except Exception:
            continue
    if not hour_map:
        raise RuntimeError("no matching hourly filenames parsed")
    chosen_hour: int
    if hour is not None and hour in hour_map:
        chosen_hour = hour
    elif hour is not None:
        # pick greatest hour <= requested, else max available
        leq = [h for h in hour_map.keys() if h <= hour]
        chosen_hour = (max(leq) if leq else max(hour_map.keys()))
    else:
        chosen_hour = max(hour_map.keys())
    chosen_name = hour_map[chosen_hour]
    logger.info("Discovered hourly file", extra={"hour": chosen_hour, "file": chosen_name})
    return chosen_name, chosen_hour


def build_casic_ephemeris(
    year: int,
    doy: int,
    hour: Optional[int] = None,
    workdir: str | None = None,
    url_template: Optional[str] = None,
    token: Optional[str] = None,
) -> List[str]:
    """Return CASIC GPS ephemeris frames for the specified date.

    Frames are returned as hexadecimal strings for easier transport over
    JSON APIs.
    """

    workdir = workdir or os.getcwd()
    os.makedirs(workdir, exist_ok=True)
    # Choose a local filename; remote name can differ
    # If template expects an hour and none provided, infer current UTC hour
    inferred_hour = False
    if url_template and hour is None and ("{hour" in url_template or "{HH" in url_template):
        hour = datetime.now(timezone.utc).hour
        inferred_hour = True
    logger.info(
        "Building CASIC ephemeris",
        extra={"year": year, "doy": doy, "hour": hour, "inferred_hour": inferred_hour},
    )
    def _gz_name(h: Optional[int]) -> str:
        if h is not None:
            return f"hour{doy:03d}{h}.{year % 100:02d}n.gz"
        return f"brdc{doy:03d}0.{year % 100:02d}n.gz"
    gz_name = _gz_name(hour)
    gz_path = os.path.join(workdir, gz_name)
    # Build optional override URL from template
    url = None
    def _format_url(h: Optional[int]) -> str:
        if not url_template:
            return ""
        try:
            return url_template.format(
                year=year,
                doy=doy,
                yy=year % 100,
                hour=(h if h is not None else ""),
                HH=(f"{h:02d}" if h is not None else ""),
            )
        except Exception:
            return url_template

    if url_template:
        # Support {year}, {doy}, {yy}, and optional {hour}/{HH}
        url = _format_url(hour)
        # If template is a directory (not a file), list and pick a file
        if url and not url.endswith('.gz'):
            if not url.endswith('/'):
                url += '/'
            try:
                fname, resolved_hour = discover_hourly_filename(url, year, doy, hour, token)
                url = url + fname
                if hour is None:
                    hour = resolved_hour
                gz_name = fname
                gz_path = os.path.join(workdir, gz_name)
            except Exception as exc:  # pragma: no cover - network
                logger.exception("Directory discovery failed", extra={"dir": url})
                raise RuntimeError(f"directory discovery failed: {exc}") from exc
    logger.debug(
        "Resolved ephemeris URL",
        extra={"url": url, "gz_path": gz_path, "has_token": bool(token)},
    )
    try:
        try:
            fetch_rinex_brdc(year, doy, gz_path, url=url, timeout=10, token=token)
        except RuntimeError as exc:
            msg = str(exc)
            # If hourly fetch 404s, try previous hour once
            if hour is not None and "404" in msg:
                prev_hour = (hour - 1) % 24
                alt_name = _gz_name(prev_hour)
                gz_path = os.path.join(workdir, alt_name)
                alt_url = _format_url(prev_hour) if url_template else url
                logger.info(
                    "Retrying previous hour",
                    extra={"prev_hour": prev_hour, "url": alt_url, "gz_path": gz_path},
                )
                fetch_rinex_brdc(year, doy, gz_path, url=alt_url or None, timeout=10, token=token)
                hour = prev_hour
            else:
                raise
        nav_path = open_rinex_file(gz_path)
        # Limit to GPS to avoid parsing issues with other constellations
        ds = parse_rinex_nav_filtered(nav_path, systems="G")
    except Exception as exc:  # pragma: no cover - runtime failure
        raise RuntimeError(f"RINEX processing failed: {exc}") from exc
    frames = list(make_casic_gps_eph_frames(ds))
    logger.info("Generated CASIC frames", extra={"count": len(frames)})
    return [fr.hex() for fr in frames]
