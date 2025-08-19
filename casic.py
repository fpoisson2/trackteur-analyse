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
from typing import Any, Iterable, List, Optional

try:  # Optional dependencies
    import georinex as grx  # type: ignore[import-untyped]
except Exception:  # pragma: no cover - handled at runtime
    grx = None

try:  # Optional dependencies
    import requests  # type: ignore[import-untyped]
except Exception:  # pragma: no cover - handled at runtime
    requests = None

CASIC_HDR0 = 0xBA
CASIC_HDR1 = 0xCE


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


def default_brdc_url(year: int, doy: int) -> str:
    """Return the default RINEX navigation URL for a given day.

    The default points to the NASA CDDIS archive which requires an
    authentication token. See :func:`fetch_rinex_brdc` for details.
    """
    yy = year % 100
    fname = f"brdc{doy:03d}0.{yy:02d}n.gz"
    return (
        "https://cddis.nasa.gov/archive/gnss/data/daily/"
        f"{year}/{doy:03d}/{yy:02d}n/{fname}"
    )


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
    headers: dict[str, str] = {}
    token = token or os.getenv("CDDIS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(url, timeout=timeout, headers=headers or None)
        resp.raise_for_status()
    except Exception as exc:  # pragma: no cover - network failure
        raise RuntimeError(f"failed to fetch {url}: {exc}") from exc
    with open(out_path, "wb") as fh:
        fh.write(resp.content)
    return out_path


def open_rinex_file(path: str) -> str:
    """Return path to decompressed RINEX file."""
    if path.endswith(".gz"):
        with gzip.open(path, "rb") as gz:
            raw = gz.read()
        nav_path = path[:-3]
        with open(nav_path, "wb") as fh:
            fh.write(raw)
        return nav_path
    return path


def parse_rinex_nav(path: str) -> Any:
    """Parse a RINEX navigation file using georinex."""
    if grx is None:  # pragma: no cover - dependency check
        raise RuntimeError("georinex not installed")
    return grx.load(path)


def build_casic_ephemeris(
    year: int,
    doy: int,
    workdir: str | None = None,
) -> List[str]:
    """Return CASIC GPS ephemeris frames for the specified date.

    Frames are returned as hexadecimal strings for easier transport over
    JSON APIs.
    """

    workdir = workdir or os.getcwd()
    os.makedirs(workdir, exist_ok=True)
    gz_path = os.path.join(
        workdir, f"brdc{doy:03d}0.{year % 100:02d}n.gz"
    )
    try:
        fetch_rinex_brdc(year, doy, gz_path)
        nav_path = open_rinex_file(gz_path)
        ds = parse_rinex_nav(nav_path)
    except Exception as exc:  # pragma: no cover - runtime failure
        raise RuntimeError(f"RINEX processing failed: {exc}") from exc
    frames = list(make_casic_gps_eph_frames(ds))
    return [fr.hex() for fr in frames]
