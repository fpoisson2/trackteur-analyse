#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build EPH CASIC (0x08,0x07) for Quectel L76K from public RINEX BRDC (BKG/IGS).

Points clés:
- RINEX v3 stamp = YYYYDDD0000 (4 zéros)
- GeoRinex limité à GPS (use="G")
- Patch temporaire de xarray.merge -> join='outer', compat='no_conflicts' (évite AlignmentError)
- Angles radians -> demi-cercles + clamp int16/int32
- Date UTC timezone-aware
- AID-INI -> EPH + attente d'ACK (option série)
"""

from __future__ import annotations
import argparse
import datetime as dt
import math
import struct
import time
from pathlib import Path
from typing import List, Tuple
import warnings

import numpy as np
import requests
import georinex as grx  # type: ignore
import xarray as xr

# --- Adoucir les FutureWarning xarray/GeoRinex ---
warnings.filterwarnings("ignore", category=FutureWarning, module=r"georinex\.nav3")
warnings.filterwarnings("ignore", category=FutureWarning, message=r".*default value for (compat|join) will change.*")

BKG_BASE = "https://igs.bkg.bund.de/root_ftp/IGS/BRDC/{yyyy}/{ddd:03d}/"


# ---------- 1) Sélection / téléchargement du RINEX ----------
def yydoy(d: dt.date) -> Tuple[int, int, int]:
    yyyy = d.year
    yy = d.year % 100
    ddd = int(d.strftime("%j"))
    return yyyy, yy, ddd


def v3_candidates(d: dt.date) -> List[str]:
    """
    URLs candidates (RINEX v3 mixed 'MN'), puis fallback v2 en dernier.
    IMPORTANT : stamp quotidien v3 = YYYYDDD0000 (4 zéros).
    """
    yyyy, yy, ddd = yydoy(d)
    stamp = f"{yyyy}{ddd:03d}0000"
    names = [
        f"BRDC00IGS_R_{stamp}_01D_MN.rnx.gz",
        f"BRDC00WRD_R_{stamp}_01D_MN.rnx.gz",
        f"BRDM00DLR_S_{stamp}_01D_MN.rnx.gz",
        f"BRD400DLR_S_{stamp}_01D_MN.rnx.gz",
        f"BRDC00WRD_S_{stamp}_01D_MN.rnx.gz",
        # Fallback v2 :
        f"brdc{ddd:03d}0.{yy:02d}n.gz",
    ]
    base = BKG_BASE.format(yyyy=yyyy, ddd=ddd)
    return [base + n for n in names]


def fetch_best_nav(d: dt.date, out_dir: Path, timeout: int = 30) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update({"User-Agent": "agnss-builder/1.0"})
    last = None
    for url in v3_candidates(d):
        last = url
        try:
            r = s.get(url, timeout=timeout, allow_redirects=True)
            if r.status_code == 200 and r.content and len(r.content) > 1024:
                fp = out_dir / url.rsplit("/", 1)[-1]
                fp.write_bytes(r.content)
                print(f"[OK] {url}")
                return fp
            print(f"[skip] {url} -> {r.status_code}")
        except requests.RequestException as e:
            print(f"[err]  {url} -> {e}")
    raise FileNotFoundError(f"Aucun fichier NAV trouvé pour {d} (dernier essai: {last})")


# ---------- 2) Emballage CASIC ----------
def csip_pack(msg_class: int, msg_id: int, payload: bytes) -> bytes:
    """
    0xBA 0xCE | U2 len | U1 class | U1 id | payload (multiple de 4) | U4 checksum (LE)
    checksum = (id<<24) + (class<<16) + len + Σ(payload en U32 LE) (mod 2^32)
    """
    if len(payload) % 4:
        payload += b"\x00" * (4 - (len(payload) % 4))
    length = len(payload)
    ck = (msg_id << 24) + (msg_class << 16) + length
    for i in range(0, length, 4):
        ck = (ck + int.from_bytes(payload[i:i+4], "little")) & 0xFFFFFFFF
    return b"\xBA\xCE" + struct.pack("<HBB", length, msg_class, msg_id) + payload + struct.pack("<I", ck)


# ---------- 2.b) Helpers d'encodage & conversions ----------
INT16_MIN, INT16_MAX = -32768, 32767
INT32_MIN, INT32_MAX = -2147483648, 2147483647

def clamp_i16(x: int) -> int:
    return INT16_MIN if x < INT16_MIN else (INT16_MAX if x > INT16_MAX else x)

def clamp_i32(x: int) -> int:
    return INT32_MIN if x < INT32_MIN else (INT32_MAX if x > INT32_MAX else x)

def wrap_pm_pi(rad: float) -> float:
    y = (rad + math.pi) % (2.0 * math.pi)
    if y < 0:
        y += 2.0 * math.pi
    return y - math.pi

def rad_to_semi(rad: float) -> float:
    return rad / math.pi

def radps_to_semips(radps: float) -> float:
    return radps / math.pi

def _iN_scaled(x: float, pow2_exp: int, nbytes: int) -> bytes:
    raw = int(round(x * (2.0 ** (-pow2_exp))))
    if nbytes == 4:
        return struct.pack("<i", clamp_i32(raw))
    elif nbytes == 2:
        return struct.pack("<h", clamp_i16(raw))
    elif nbytes == 1:
        raw = max(-128, min(127, raw))
        return struct.pack("<b", raw)
    else:
        raise ValueError("nbytes must be 1,2,4")

def _uN_scaled(x: float, pow2_exp: int, nbytes: int) -> bytes:
    raw = int(round(x * (2.0 ** (-pow2_exp))))
    if nbytes == 4:
        raw = max(0, min(0xFFFFFFFF, raw))
        return struct.pack("<I", raw)
    elif nbytes == 2:
        raw = max(0, min(0xFFFF, raw))
        return struct.pack("<H", raw)
    elif nbytes == 1:
        raw = max(0, min(0xFF, raw))
        return struct.pack("<B", raw)
    else:
        raise ValueError("nbytes must be 1,2,4")


def make_msg_gpseph(
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
    tgd: float,
    iodc: int,
    ura: int = 0,
    health: int = 0,
    valid: int = 3,
    reserved0: int = 0,
    reserved1_u2: int = 0,
) -> bytes:
    """
    MSG-GPSEPH payload (72 octets) — angles & vitesses en demi-cercles (/π).
    """
    # Angles radians -> semicircles, repli [-π, π)
    omega_semi  = rad_to_semi(wrap_pm_pi(omega))
    M0_semi     = rad_to_semi(wrap_pm_pi(M0))
    i0_semi     = rad_to_semi(wrap_pm_pi(i0))
    OMEGA0_semi = rad_to_semi(wrap_pm_pi(OMEGA0))

    # Vitesses rad/s -> semicircles/s
    OMEGADOT_semips = radps_to_semips(OMEGADOT)
    DeltaN_semips   = radps_to_semips(DeltaN)
    IDOT_semips     = radps_to_semips(IDOT)

    # Harmoniques (petites valeurs rad) -> semicircles
    cuc_semi = rad_to_semi(cuc)
    cus_semi = rad_to_semi(cus)
    cic_semi = rad_to_semi(cic)
    cis_semi = rad_to_semi(cis)

    parts = [struct.pack("<I", int(reserved0) & 0xFFFFFFFF)]  # reserved / vendor-specific

    # Non angulaires
    parts += [
        _uN_scaled(sqrtA, -19, 4),
        _uN_scaled(e,     -33, 4),
    ]

    # Angles purs (I4, 2^-31)
    parts += [
        struct.pack("<i", clamp_i32(int(round(omega_semi  * (2**31))))),
        struct.pack("<i", clamp_i32(int(round(M0_semi     * (2**31))))),
        struct.pack("<i", clamp_i32(int(round(i0_semi     * (2**31))))),
        struct.pack("<i", clamp_i32(int(round(OMEGA0_semi * (2**31))))),
    ]

    # Vitesse Ωdot (I4, 2^-43), Δn & Ďi (I2, 2^-43)
    parts += [struct.pack("<i", clamp_i32(int(round(OMEGADOT_semips * (2**43)))))]
    parts += [
        struct.pack("<h", clamp_i16(int(round(DeltaN_semips * (2**43))))),
        struct.pack("<h", clamp_i16(int(round(IDOT_semips   * (2**43))))),
    ]

    # Harmoniques (I2, 2^-29)
    parts += [
        struct.pack("<h", clamp_i16(int(round(cuc_semi * (2**29))))),
        struct.pack("<h", clamp_i16(int(round(cus_semi * (2**29))))),
    ]

    # CRS/CRC en mètres (I2, 2^-5)
    parts += [
        _iN_scaled(crc,  -5, 2),
        _iN_scaled(crs,  -5, 2),
    ]

    # Suite des harmoniques
    parts += [
        struct.pack("<h", clamp_i16(int(round(cic_semi * (2**29))))),
        struct.pack("<h", clamp_i16(int(round(cis_semi * (2**29))))),
    ]

    # Temps & horloge
    parts += [
        _uN_scaled(toe,  +4, 2),           # toe/16 s
        struct.pack("<H", week & 0xFFFF),
        _uN_scaled(toc,  +4, 4),           # toc/16 s
        _iN_scaled(af0, -31, 4),
        _iN_scaled(af1, -43, 2),
        struct.pack("<b", max(-128, min(127, int(round(af2 * (2**55)))))),   # I1 2^-55
        struct.pack("<b", max(-128, min(127, int(round(tgd * (2**31)))))),   # I1 2^-31
        struct.pack("<H", iodc & 0xFFFF),
        struct.pack("<B", ura & 0xFF),
        struct.pack("<B", health & 0xFF),
        struct.pack("<B", svid & 0xFF),
        struct.pack("<B", valid & 0xFF),
        struct.pack("<H", int(reserved1_u2) & 0xFFFF),
    ]

    payload = b"".join(parts)
    return csip_pack(0x08, 0x07, payload)


# ---------- 2.c) Decode helpers ----------
def decode_aid_ini(frame: bytes):
    if not (len(frame) >= 6 and frame[:2] == b"\xBA\xCE"):
        raise ValueError("Not a CSIP frame")
    length, cls_, mid = struct.unpack("<HBB", frame[2:6])
    if cls_ != 0x0B or mid != 0x01:
        raise ValueError("Not AID-INI (0x0B,0x01)")
    payload = frame[6:6+length]
    lat, lon, alt, tow, fb, pacc, tacc, facc, res, wn, ts, flags = struct.unpack(
        "<ddd d f f f f I H B B", payload
    )
    return {
        "lat": lat, "lon": lon, "alt": alt,
        "tow": tow, "freqBias": fb, "pAcc": pacc, "tAcc": tacc, "fAcc": facc,
        "reserved": res, "wn": wn, "timeSource": ts, "flags": flags,
    }


def decode_gpseph(frame: bytes):
    if not (len(frame) >= 6 and frame[:2] == b"\xBA\xCE"):
        raise ValueError("Not a CSIP frame")
    length, cls_, mid = struct.unpack("<HBB", frame[2:6])
    if cls_ != 0x08 or mid != 0x07:
        raise ValueError("Not GPSEPH (0x08,0x07)")
    payload = frame[6:6+length]

    off = 0
    def u4():
        nonlocal off
        v = struct.unpack_from("<I", payload, off)[0]; off += 4; return v
    def i4():
        nonlocal off
        v = struct.unpack_from("<i", payload, off)[0]; off += 4; return v
    def i2():
        nonlocal off
        v = struct.unpack_from("<h", payload, off)[0]; off += 2; return v
    def u2():
        nonlocal off
        v = struct.unpack_from("<H", payload, off)[0]; off += 2; return v
    def i1():
        nonlocal off
        v = struct.unpack_from("<b", payload, off)[0]; off += 1; return v
    def u1():
        nonlocal off
        v = payload[off]; off += 1; return v

    reserved0 = u4()
    sqrtA_raw = u4(); e_raw = u4()
    omega_raw = i4(); M0_raw = i4(); i0_raw = i4(); OMEGA0_raw = i4()
    OMEGADOT_raw = i4(); DeltaN_raw = i2(); IDOT_raw = i2()
    cuc_raw = i2(); cus_raw = i2()
    crc_raw = i2(); crs_raw = i2()
    cic_raw = i2(); cis_raw = i2()
    toe_raw = u2(); week = u2(); toc_raw = u4()
    af0_raw = i4(); af1_raw = i2(); af2_raw = i1(); tgd_raw = i1()
    iodc = u2(); ura = u1(); health = u1(); svid = u1(); valid = u1(); reserved1 = u2()

    def scu(v, p): return v * (2.0 ** p)
    def sci(v, p): return v * (2.0 ** p)
    def semi_to_rad(v): return v * math.pi

    return {
        "reserved0": reserved0,
        "sqrtA": scu(sqrtA_raw, -19),
        "e": scu(e_raw, -33),
        "omega": semi_to_rad(sci(omega_raw, -31)),
        "M0": semi_to_rad(sci(M0_raw, -31)),
        "i0": semi_to_rad(sci(i0_raw, -31)),
        "OMEGA0": semi_to_rad(sci(OMEGA0_raw, -31)),
        "OMEGADOT": semi_to_rad(sci(OMEGADOT_raw, -43)),
        "DeltaN": semi_to_rad(sci(DeltaN_raw, -43)),
        "IDOT": semi_to_rad(sci(IDOT_raw, -43)),
        "cuc": semi_to_rad(sci(cuc_raw, -29)),
        "cus": semi_to_rad(sci(cus_raw, -29)),
        "crc": sci(crc_raw, -5),
        "crs": sci(crs_raw, -5),
        "cic": semi_to_rad(sci(cic_raw, -29)),
        "cis": semi_to_rad(sci(cis_raw, -29)),
        "toe": scu(toe_raw, +4),
        "week": week,
        "toc": scu(toc_raw, +4),
        "af0": sci(af0_raw, -31),
        "af1": sci(af1_raw, -43),
        "af2": sci(af2_raw, -55),
        "tgd": sci(tgd_raw, -31),
        "iodc": iodc, "ura": ura, "health": health, "svid": svid, "valid": valid,
        "reserved1": reserved1,
    }


# ---------- 3) Patch contextuel de xarray.merge pour GeoRinex ----------
class _XRMergeOuter:
    """
    Contexte: remplace temporairement xr.merge par une version qui force
    join='outer' et compat='no_conflicts' si non spécifiés (GeoRinex nav3.py).
    """
    def __enter__(self):
        self._orig = xr.merge
        def _merge_patched(objs, *args, **kwargs):
            kwargs.setdefault("join", "outer")
            kwargs.setdefault("compat", "no_conflicts")
            return self._orig(objs, *args, **kwargs)
        xr.merge = _merge_patched
        return xr.merge

    def __exit__(self, exc_type, exc, tb):
        xr.merge = self._orig


def _load_nav(nav_path: Path, systems: str = "G"):
    """
    Charge le RINEX en limitant GeoRinex aux systèmes demandés (ex: "G", "C", "GC"),
    sous patch xr.merge (join='outer', compat='no_conflicts').
    """
    with _XRMergeOuter():
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning, module=r"xarray.*")
            warnings.filterwarnings("ignore", category=FutureWarning, module=r"georinex.*")
            ds = grx.load(str(nav_path), use=systems)
    return ds


def _load_nav_gps(nav_path: Path):
    return _load_nav(nav_path, systems="G")


def eph_from_nav(nav_path: Path) -> Tuple[List[int], List[bytes]]:
    """
    Extrait, pour chaque SV GPS, l'éphéméride la plus récente et fabrique la trame CASIC correspondante.
    """
    ds = _load_nav_gps(nav_path)

    sv_gps = [sv for sv in ds.sv.values if str(sv).startswith("G")]
    if not sv_gps:
        raise RuntimeError("Aucun SV GPS trouvé dans ce RINEX.")
    ds = ds.sel(sv=sv_gps)

    frames: List[bytes] = []
    svids: List[int] = []

    for sv in sv_gps:
        rows = ds.sel(sv=sv)
        arr = rows["sqrtA"].values
        valid_idx = np.where(~np.isnan(arr))[0]
        if valid_idx.size == 0:
            continue
        r = rows.isel(time=int(valid_idx[-1]))

        def g_any(names, default: float = 0.0) -> float:
            if isinstance(names, str):
                names = [names]
            for name in names:
                if name in r.variables:
                    return float(r[name].values)
            return default

        # Core orbit/clock parameters — try GeoRinex v3 canonical names first, then aliases
        sqrtA = g_any(["sqrtA"])  # present as-is
        e = g_any(["e", "Eccentricity"])  # GeoRinex v3 uses 'Eccentricity'
        i0 = g_any(["i0", "Io"])  # GeoRinex v3 uses 'Io'
        OMEGA0 = g_any(["OMEGA0", "Omega0"])  # GeoRinex v3 uses 'Omega0'
        omega = g_any(["omega"])  # same
        M0 = g_any(["M0"])  # same
        DeltaN = g_any(["DeltaN"])  # same
        ODOT = g_any(["OMEGADOT", "OmegaDot"])  # GeoRinex v3 uses 'OmegaDot'
        IDOT = g_any(["IDOT"])  # same

        # Harmonic corrections — capitalized in GeoRinex
        cuc = g_any(["cuc", "Cuc"])  # rad
        cus = g_any(["cus", "Cus"])  # rad
        crc = g_any(["crc", "Crc"])  # m
        crs = g_any(["crs", "Crs"])  # m
        cic = g_any(["cic", "Cic"])  # rad
        cis = g_any(["cis", "Cis"])  # rad

        # Epochs
        toe = g_any(["toe", "Toe"])  # seconds-of-week
        # toc is not provided as a field by GeoRinex; use the epoch timestamp (RINEX line epoch)
        # Convert the row's time coordinate to GPST seconds-of-week
        t_posix = np.datetime64(r["time"].values, "s").astype(int)
        gps_epoch = dt.datetime(1980, 1, 6, tzinfo=dt.timezone.utc).timestamp()
        sec_since_gps = float(t_posix - gps_epoch)
        if sec_since_gps < 0:
            sec_since_gps = 0.0
        toc = sec_since_gps % (7 * 86400)

        # Clock terms — GeoRinex v3 uses SVclockBias/Drift/DriftRate
        af0 = g_any(["af0", "SVclockBias"])  # s
        af1 = g_any(["af1", "SVclockDrift"])  # s/s
        af2 = g_any(["af2", "SVclockDriftRate"])  # s/s^2

        if "tgd" in r.variables:
            tgd = g_any("tgd")
        elif "TGD" in r.variables:
            tgd = g_any("TGD")
        else:
            tgd = 0.0

        iodc   = int(r["IODC"].values)   if "IODC" in r.variables else 0
        ura    = int(r["URA"].values)    if "URA"  in r.variables else 0
        health = int(r["health"].values) if "health" in r.variables else 0

        if "week" in r.variables:
            week = int(r["week"].values)
        elif "GPSWeek" in r.variables:
            week = int(r["GPSWeek"].values)
        else:
            # Déduire la semaine GPS depuis l'horodatage
            t_posix2 = np.datetime64(r["time"].values, "s").astype(int)
            gps_epoch2 = dt.datetime(1980, 1, 6, tzinfo=dt.timezone.utc).timestamp()
            sec = float(t_posix2 - gps_epoch2)
            week = int(sec // (7 * 86400))

        svid = int(str(sv)[1:])
        frame = make_msg_gpseph(
            svid, sqrtA, e, omega, M0, i0, OMEGA0, ODOT, DeltaN, IDOT,
            cuc, cus, crc, crs, cic, cis, toe, week, toc, af0, af1, af2,
            tgd, iodc, ura, health, valid=3
        )
        frames.append(frame)
        svids.append(svid)

    return sorted(svids), frames


# ---------- 2.d) BeiDou (BDS) message build/parse ----------
def bdt_week_tow(now: dt.datetime | None = None) -> Tuple[int, float]:
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    bdt_epoch = dt.datetime(2006, 1, 1, tzinfo=dt.timezone.utc)
    dt_s = (now - bdt_epoch).total_seconds()
    w = int(dt_s // (7 * 86400))
    tow = dt_s - w * 7 * 86400
    return w, tow


def make_msg_bdseph(
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
    tgd_s: float,
    iodc: int,
    iode: int,
    ura: int = 0,
    health: int = 0,
    valid: int = 3,
    reserved0_u4: int = 0,
    reserved2_u2: int = 0,
    reserved3_u2: int = 0,
) -> bytes:
    """
    MSG-BDSEPH payload (92 octets) selon CASIC 0x08/0x02.
    - Angles (omega, M0, i0, OMEGA0) en demi-cercles (π), I4 2^-31
    - OMEGADOT, DeltaN, IDOT en demi-cercles/s, 2^-43 (I4/I2/I2)
    - Harmoniques cuc/cus/cic/cis en radians, I4 2^-31
    - CRC/CRS en m, I4 2^-6
    - toe/toc en s, U4 avec pas 2^3 (8 s)
    - af0 2^-33 (I4), af1 2^-50 (I4), af2 2^-66 (I2)
    - tgd en 0.1 ns (I2)
    """
    omega_semi  = rad_to_semi(wrap_pm_pi(omega))
    M0_semi     = rad_to_semi(wrap_pm_pi(M0))
    i0_semi     = rad_to_semi(wrap_pm_pi(i0))
    OMEGA0_semi = rad_to_semi(wrap_pm_pi(OMEGA0))

    OMEGADOT_semips = radps_to_semips(OMEGADOT)
    DeltaN_semips   = radps_to_semips(DeltaN)
    IDOT_semips     = radps_to_semips(IDOT)

    parts: List[bytes] = []
    parts.append(struct.pack("<I", int(reserved0_u4) & 0xFFFFFFFF))
    parts += [
        _uN_scaled(sqrtA, -19, 4),
        _uN_scaled(e,     -33, 4),
        struct.pack("<i", clamp_i32(int(round(omega_semi  * (2**31))))),
        struct.pack("<i", clamp_i32(int(round(M0_semi     * (2**31))))),
        struct.pack("<i", clamp_i32(int(round(i0_semi     * (2**31))))),
        struct.pack("<i", clamp_i32(int(round(OMEGA0_semi * (2**31))))),
        struct.pack("<i", clamp_i32(int(round(OMEGADOT_semips * (2**43))))),
        struct.pack("<h", clamp_i16(int(round(DeltaN_semips   * (2**43))))),
        struct.pack("<h", clamp_i16(int(round(IDOT_semips     * (2**43))))),
        _iN_scaled(cuc, -31, 4),
        _iN_scaled(cus, -31, 4),
        _iN_scaled(crc,  -6, 4),
        _iN_scaled(crs,  -6, 4),
        _iN_scaled(cic, -31, 4),
        _iN_scaled(cis, -31, 4),
        _uN_scaled(toe,  +3, 4),
        struct.pack("<H", week & 0xFFFF),
        struct.pack("<H", int(reserved2_u2) & 0xFFFF),
        _uN_scaled(toc,  +3, 4),
        _iN_scaled(af0, -33, 4),
        _iN_scaled(af1, -50, 4),
        _iN_scaled(af2, -66, 2),
    ]
    # tgd: seconds -> 0.1 ns units in I2
    tgd_tenth_ns = int(round(tgd_s * 1e10))
    parts.append(struct.pack("<h", clamp_i16(tgd_tenth_ns)))
    parts += [
        struct.pack("<B", iodc & 0xFF),
        struct.pack("<B", iode & 0xFF),
        struct.pack("<B", ura & 0xFF),
        struct.pack("<B", health & 0xFF),
        struct.pack("<B", svid & 0xFF),
        struct.pack("<B", valid & 0xFF),
        struct.pack("<H", int(reserved3_u2) & 0xFFFF),
    ]
    payload = b"".join(parts)
    return csip_pack(0x08, 0x02, payload)


def decode_bdseph(frame: bytes):
    if not (len(frame) >= 6 and frame[:2] == b"\xBA\xCE"):
        raise ValueError("Not a CSIP frame")
    length, cls_, mid = struct.unpack("<HBB", frame[2:6])
    if cls_ != 0x08 or mid != 0x02:
        raise ValueError("Not BDSEPH (0x08,0x02)")
    payload = frame[6:6+length]

    off = 0
    def u4():
        nonlocal off
        v = struct.unpack_from("<I", payload, off)[0]; off += 4; return v
    def i4():
        nonlocal off
        v = struct.unpack_from("<i", payload, off)[0]; off += 4; return v
    def i2():
        nonlocal off
        v = struct.unpack_from("<h", payload, off)[0]; off += 2; return v
    def u2():
        nonlocal off
        v = struct.unpack_from("<H", payload, off)[0]; off += 2; return v
    def u1():
        nonlocal off
        v = payload[off]; off += 1; return v

    reserved0 = u4()
    sqrtA_raw = u4(); e_raw = u4()
    omega_raw = i4(); M0_raw = i4(); i0_raw = i4(); OMEGA0_raw = i4()
    OMEGADOT_raw = i4(); DeltaN_raw = i2(); IDOT_raw = i2()
    cuc_raw = i4(); cus_raw = i4()
    crc_raw = i4(); crs_raw = i4()
    cic_raw = i4(); cis_raw = i4()
    toe_raw = u4(); week = u2(); reserved2 = u2(); toc_raw = u4()
    af0_raw = i4(); af1_raw = i4(); af2_raw = i2(); tgd_raw = i2()
    iodc = u1(); iode = u1(); ura = u1(); health = u1(); svid = u1(); valid = u1(); reserved3 = u2()

    def scu(v, p): return v * (2.0 ** p)
    def sci(v, p): return v * (2.0 ** p)
    def semi_to_rad(v): return v * math.pi

    return {
        "reserved0": reserved0,
        "sqrtA": scu(sqrtA_raw, -19),
        "e": scu(e_raw, -33),
        "omega": semi_to_rad(sci(omega_raw, -31)),
        "M0": semi_to_rad(sci(M0_raw, -31)),
        "i0": semi_to_rad(sci(i0_raw, -31)),
        "OMEGA0": semi_to_rad(sci(OMEGA0_raw, -31)),
        "OMEGADOT": semi_to_rad(sci(OMEGADOT_raw, -43)),
        "DeltaN": semi_to_rad(sci(DeltaN_raw, -43)),
        "IDOT": semi_to_rad(sci(IDOT_raw, -43)),
        "cuc": sci(cuc_raw, -31),
        "cus": sci(cus_raw, -31),
        "crc": sci(crc_raw, -6),
        "crs": sci(crs_raw, -6),
        "cic": sci(cic_raw, -31),
        "cis": sci(cis_raw, -31),
        "toe": scu(toe_raw, +3),
        "week": week,
        "toc": scu(toc_raw, +3),
        "af0": sci(af0_raw, -33),
        "af1": sci(af1_raw, -50),
        "af2": sci(af2_raw, -66),
        "tgd": (tgd_raw * 1e-10),
        "iodc": iodc, "iode": iode, "ura": ura, "health": health, "svid": svid, "valid": valid,
        "reserved2": reserved2, "reserved3": reserved3,
    }


def eph_from_nav_bds(nav_path: Path) -> Tuple[List[int], List[bytes]]:
    ds = _load_nav(nav_path, systems="C")
    sv_c = [sv for sv in ds.sv.values if str(sv).startswith("C")]
    if not sv_c:
        raise RuntimeError("Aucun SV BDS trouvé dans ce RINEX.")
    ds = ds.sel(sv=sv_c)

    frames: List[bytes] = []
    svids: List[int] = []

    for sv in sv_c:
        rows = ds.sel(sv=sv)
        arr = rows["sqrtA"].values
        valid_idx = np.where(~np.isnan(arr))[0]
        if valid_idx.size == 0:
            continue
        r = rows.isel(time=int(valid_idx[-1]))

        def g_any(names, default: float = 0.0) -> float:
            if isinstance(names, str):
                names = [names]
            for name in names:
                if name in r.variables:
                    return float(r[name].values)
            return default

        sqrtA = g_any(["sqrtA"])  
        e = g_any(["e", "Eccentricity"])  
        i0 = g_any(["i0", "Io"])  
        OMEGA0 = g_any(["OMEGA0", "Omega0"])  
        omega = g_any(["omega"])  
        M0 = g_any(["M0"])  
        DeltaN = g_any(["DeltaN"])  
        ODOT = g_any(["OMEGADOT", "OmegaDot"])  
        IDOT = g_any(["IDOT"])  

        cuc = g_any(["cuc", "Cuc"])  # rad
        cus = g_any(["cus", "Cus"])  # rad
        crc = g_any(["crc", "Crc"])  # m
        crs = g_any(["crs", "Crs"])  # m
        cic = g_any(["cic", "Cic"])  # rad
        cis = g_any(["cis", "Cis"])  # rad

        toe = g_any(["toe", "Toe"])  # seconds-of-week (BDT)

        # toc from epoch timestamp, BDT epoch start 2006-01-01
        t_posix = np.datetime64(r["time"].values, "s").astype(int)
        bdt_epoch = dt.datetime(2006, 1, 1, tzinfo=dt.timezone.utc).timestamp()
        sec_since_bdt = float(t_posix - bdt_epoch)
        if sec_since_bdt < 0:
            sec_since_bdt = 0.0
        toc = sec_since_bdt % (7 * 86400)

        af0 = g_any(["af0", "SVclockBias"])  
        af1 = g_any(["af1", "SVclockDrift"])  
        af2 = g_any(["af2", "SVclockDriftRate"])  

        # TGD: prefer TGD1 for BDS, seconds
        if "TGD1" in r.variables:
            tgd = g_any("TGD1")
        elif "tgd" in r.variables:
            tgd = g_any("tgd")
        elif "TGD" in r.variables:
            tgd = g_any("TGD")
        else:
            tgd = 0.0

        # IOD/URA/health
        def g_int(name_list, default=0):
            for nm in ([name_list] if isinstance(name_list, str) else name_list):
                if nm in r.variables:
                    try:
                        return int(r[nm].values)
                    except Exception:
                        return int(float(r[nm].values))
            return default

        iodc = g_int(["IODC", "AODC"])  
        iode = g_int(["IODE", "AODE"])  
        ura = g_int(["URA"])  
        health = g_int(["health"])  

        # BDT week
        if "week" in r.variables:
            week = int(r["week"].values)
        else:
            week = int(sec_since_bdt // (7 * 86400))

        svid = int(str(sv)[1:])
        frame = make_msg_bdseph(
            svid, sqrtA, e, omega, M0, i0, OMEGA0, ODOT, DeltaN, IDOT,
            cuc, cus, crc, crs, cic, cis, toe, week, toc, af0, af1, af2,
            tgd, iodc, iode, ura, health, valid=3
        )
        frames.append(frame)
        svids.append(svid)

    return sorted(svids), frames


# ---------- 4) AID-INI + injection série (optionnel) ----------
def gps_week_tow(now: dt.datetime | None = None) -> Tuple[int, float]:
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    gps_epoch = dt.datetime(1980, 1, 6, tzinfo=dt.timezone.utc)
    dt_s = (now - gps_epoch).total_seconds()
    w = int(dt_s // (7 * 86400))
    tow = dt_s - w * 7 * 86400
    return w, tow


def make_aid_ini(
    lat: float,
    lon: float,
    alt_m: float,
    *,
    freq_bias_hz: float = 0.0,
    pacc_m: float = 0.0,
    tacc_s: float = 0.0,
    facc_hz: float = 0.0,
    flags: int | None = None,
    reserved_u4: int = 0,
) -> bytes:
    """
    AID-INI (payload 56 octets) :
      lat, lon, alt (R8), tow (R8), freqBias (R4), pAcc (R4), tAcc (R4), fAcc (R4),
      res (U4), WN (U2), timeSource (U1), flags (U1)
    Flags : B0 loc valid | B1 time valid | B4 freq provided | B5 LLA
    """
    wn, tow = gps_week_tow()
    if flags is None:
        # Default: position+time valid, LLA provided; frequency not provided
        flags = (1 << 0) | (1 << 1) | (1 << 5)
    payload = struct.pack(
        "<ddd d f f f f I H B B",
        float(lat), float(lon), float(alt_m),
        float(tow),
        float(freq_bias_hz), float(pacc_m), float(tacc_s), float(facc_hz),
        int(reserved_u4) & 0xFFFFFFFF,  # reserved
        wn & 0xFFFF,
        0,                    # timeSource (0=GPST)
        flags & 0xFF,
    )
    return csip_pack(0x0B, 0x01, payload)


def inject_serial(
    port: str,
    baud: int,
    aid_ini: bytes,
    eph_frames: List[bytes],
    ack_timeout_s: float = 2.0,
    inter_frame_s: float = 0.02,
) -> int:
    """
    Ouvre le port série, envoie AID-INI → attend ACK, puis chaque EPH → attend ACK.
    Renvoie le nombre de paquets EPH envoyés (ACK reçus).
    """
    import serial

    def wait_ack(ser, expect_cls: int, expect_id: int, timeout: float) -> bool:
        deadline = time.time() + timeout
        buf = bytearray()
        while time.time() < deadline:
            buf += ser.read(256)
            i = 0
            while True:
                j = buf.find(b"\xBA\xCE", i)
                if j < 0 or j + 12 > len(buf):
                    break
                length = int.from_bytes(buf[j + 2:j + 4], "little")
                cls_ = buf[j + 4]
                mid = buf[j + 5]
                if length == 4 and cls_ == 0x05 and mid == 0x01:
                    p = buf[j + 6:j + 10]  # payload: ClsID, MsgID, ResU2
                    if p[0] == expect_cls and p[1] == expect_id:
                        return True
                i = j + 1
            time.sleep(0.02)
        return False

    with serial.Serial(port, baudrate=baud, timeout=0.1) as ser:
        # AID-INI
        ser.write(aid_ini)
        ser.flush()
        if not wait_ack(ser, 0x0B, 0x01, ack_timeout_s):
            raise RuntimeError("ACK manquant après AID-INI")

        # EPH + ACK
        sent = 0
        for fr in eph_frames:
            ser.write(fr)
            ser.flush()
            if not wait_ack(ser, 0x08, 0x07, ack_timeout_s):
                raise RuntimeError(f"ACK manquant pour EPH #{sent + 1}")
            sent += 1
            time.sleep(inter_frame_s)
        return sent


# ---------- 5) CLI ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (défaut : hier UTC)")
    ap.add_argument("--out", default="./agnss_build")
    ap.add_argument("--sample", action="store_true", help="Utiliser des trames exemples (évite le réseau)")
    ap.add_argument("--systems", default="G", help="Constellations à traiter (ex: G, C, GC)")
    ap.add_argument("--inject", action="store_true", help="AID-INI + EPH sur port série")
    ap.add_argument("--port", help="Port série (ex: /dev/ttyUSB0 ou COM5)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--lat", type=float, default=0.0)
    ap.add_argument("--lon", type=float, default=0.0)
    ap.add_argument("--alt", type=float, default=0.0)
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.sample:
        # Deux trames MSG-GPSEPH issues de la doc Quectel
        hex_str = (
            "BA CE 48 00 08 07 CD CD 9A 10 E5 7D 0D A1 A0 03 59 05 58 30 63 21 98 4B 91 03 DA 64 0F 28 EC 77 "
            "2E B1 4C A8 FF FF EC 2C 81 05 12 05 85 14 DB 19 9C 05 0C 00 40 00 FA 32 63 00 FA 32 00 00 8E 96 "
            "18 00 B7 FF 00 0A 27 00 00 00 01 03 A3 41 E2 9B 3D 28 "
            "BA CE 48 00 08 07 F3 E1 31 8C 1E FC 0C A1 8D A9 6D 0A 3B F5 4C C1 28 F5 A4 0D 0A 5C 32 27 C1 AD D6 AD AB A3 FF FF E0 31 8F 04 DA 05 7F 15 "
            "26 17 08 07 57 FF B1 FF FA 32 63 00 FA 32 00 00 39 ED EC FF DF FF 00 DA 2A 00 00 00 02 03 A3 41 2E C4 6B 1F"
        )
        data = bytes(int(b, 16) for b in hex_str.split())
        # Split frames
        eph_frames = []
        svids = []
        i = 0
        while i < len(data) - 6:
            if data[i:i+2] != b"\xBA\xCE":
                i += 1
                continue
            length, cls_, mid = struct.unpack('<HBB', data[i+2:i+6])
            fr = data[i:i+6+length+4]
            if cls_ == 0x08 and mid == 0x07:
                eph_frames.append(fr)
                try:
                    svids.append(decode_gpseph(fr)['svid'])
                except Exception:
                    pass
            i += 6 + length + 4
    else:
        # Date: if provided, use it; otherwise try today (UTC) then fallback to yesterday
        now_utc = dt.datetime.now(dt.timezone.utc)
        if args.date:
            d = dt.date.fromisoformat(args.date)
            nav = fetch_best_nav(d, out_dir)
        else:
            d_today = now_utc.date()
            try:
                nav = fetch_best_nav(d_today, out_dir)
            except FileNotFoundError:
                d_yest = d_today - dt.timedelta(days=1)
                print(f"[INFO] Aucune BRDC pour {d_today} encore. Essai {d_yest}…")
                nav = fetch_best_nav(d_yest, out_dir)
        svids, eph_frames = ([], [])
        # GPS
        if 'G' in args.systems:
            svids_g, eph_g = eph_from_nav(nav)
            svids.extend(svids_g); eph_frames.extend(eph_g)
        # BDS
        if 'C' in args.systems:
            try:
                svids_c, eph_c = eph_from_nav_bds(nav)
                svids.extend(svids_c); eph_frames.extend(eph_c)
            except Exception as e:
                print(f"[WARN] BDS: {e}")

    # Écritures par constellation
    if args.sample or ('G' in args.systems):
        out_g = out_dir / "gps_eph.bin"
        with open(out_g, "wb") as f:
            for fr in [fr for fr in eph_frames if fr[4:6] == b"\x08\x07"]:
                f.write(fr)
        if args.sample:
            print(f"[OK] {len([1 for fr in eph_frames if fr[4:6]==b'\x08\x07'])} SV GPS (samples)")
        elif 'G' in args.systems:
            print(f"[OK] Trames GPS → {out_g}")

    if (not args.sample) and ('C' in args.systems):
        out_c = out_dir / "bds_eph.bin"
        with open(out_c, "wb") as f:
            for fr in [fr for fr in eph_frames if fr[4:6] == b"\x08\x02"]:
                f.write(fr)
        print(f"[OK] Trames BDS → {out_c}")

    # Décodage synthétique des EPH générés (triés par ordre d'éphéméride/toe)
    try:
        # Helpers pour afficher l'ancienneté (Δ) des eph (toe/toc)
        def _fmt_delta(seconds: float) -> str:
            s = int(round(seconds))
            sign = "-" if s < 0 else ""
            s = abs(s)
            if s < 60:
                return f"{sign}{s}s"
            m, s = divmod(s, 60)
            if m < 60:
                return f"{sign}{m}m{s:02d}s"
            h, m = divmod(m, 60)
            return f"{sign}{h}h{m:02d}m"

        # Replie un delta sur l'intervalle [-3.5j, +3.5j] pour gérer le rollover hebdo
        def _wrap_week_delta(seconds: float) -> float:
            week = 7 * 86400.0
            return ((seconds + week / 2.0) % week) - week / 2.0

        def _now_gps_seconds() -> float:
            now = dt.datetime.now(dt.timezone.utc)
            gps_epoch = dt.datetime(1980, 1, 6, tzinfo=dt.timezone.utc)
            return (now - gps_epoch).total_seconds()

        def _now_bdt_seconds() -> float:
            now = dt.datetime.now(dt.timezone.utc)
            bdt_epoch = dt.datetime(2006, 1, 1, tzinfo=dt.timezone.utc)
            return (now - bdt_epoch).total_seconds()

        gps_items = []  # (abs_toe, dict d)
        bds_items = []  # (abs_toe, dict d)

        # Pré-calcul des "now" par constellation
        now_gps = _now_gps_seconds()
        now_bdt = _now_bdt_seconds()

        for fr in eph_frames:
            try:
                if fr[4:6] == b"\x08\x07":
                    d = decode_gpseph(fr)
                    abs_toe = d['week'] * 7 * 86400 + float(d['toe'])
                    abs_toc = d['week'] * 7 * 86400 + float(d['toc'])
                    d['age_toe'] = _wrap_week_delta(now_gps - abs_toe)
                    d['age_toc'] = _wrap_week_delta(now_gps - abs_toc)
                    gps_items.append((abs_toe, d))
                elif fr[4:6] == b"\x08\x02":
                    d = decode_bdseph(fr)
                    abs_toe = d['week'] * 7 * 86400 + float(d['toe'])
                    abs_toc = d['week'] * 7 * 86400 + float(d['toc'])
                    d['age_toe'] = _wrap_week_delta(now_bdt - abs_toe)
                    d['age_toc'] = _wrap_week_delta(now_bdt - abs_toc)
                    bds_items.append((abs_toe, d))
            except Exception as e:
                print(f"[WARN] Décodage EPH échoué: {e}")

        # Tri par abs_toe décroissant (plus récent -> plus ancien)
        gps_items.sort(key=lambda x: x[0], reverse=True)
        bds_items.sort(key=lambda x: x[0], reverse=True)

        # Impression ordonnée, renumérotée
        for gi, (_, d) in enumerate(gps_items, start=1):
            print(
                f"[DECODE GPSEPH #{gi}] svid={d['svid']} week={d['week']} "
                f"toe={int(d['toe'])} (Δtoe={_fmt_delta(d['age_toe'])}) "
                f"toc={int(d['toc'])} (Δtoc={_fmt_delta(d['age_toc'])}) "
                f"e={d['e']:.10f} sqrtA={d['sqrtA']:.6f} "
                f"M0={d['M0']:.6f} OMEGA0={d['OMEGA0']:.6f} i0={d['i0']:.6f}"
            )
        for ci, (_, d) in enumerate(bds_items, start=1):
            print(
                f"[DECODE BDSEPH #{ci}] svid={d['svid']} week={d['week']} "
                f"toe={int(d['toe'])} (Δtoe={_fmt_delta(d['age_toe'])}) "
                f"toc={int(d['toc'])} (Δtoc={_fmt_delta(d['age_toc'])}) "
                f"e={d['e']:.10f} sqrtA={d['sqrtA']:.6f} "
                f"M0={d['M0']:.6f} OMEGA0={d['OMEGA0']:.6f} i0={d['i0']:.6f}"
            )
    except Exception as e:
        print(f"[WARN] Décodage EPH échoué: {e}")

    if args.inject:
        if not args.port:
            raise SystemExit("--inject requiert --port")
        aid = make_aid_ini(args.lat, args.lon, args.alt)
        # Décodage synthétique de l'AID-INI avant injection
        try:
            a = decode_aid_ini(aid)
            print(
                f"[DECODE AID-INI] lat={a['lat']:.6f} lon={a['lon']:.6f} alt={a['alt']:.2f} "
                f"tow={int(a['tow'])} wn={a['wn']} flags=0x{a['flags']:02X}"
            )
        except Exception as e:
            print(f"[WARN] Décodage AID-INI échoué: {e}")
        n = inject_serial(args.port, args.baud, aid, eph_frames)
        print(f"[SERIAL] {n} EPH envoyés (ACK reçus).")


if __name__ == "__main__":
    main()
