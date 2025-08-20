import math
from pathlib import Path
import importlib.util
import numpy as np
import struct
import pytest


def _load_te():
    spec = importlib.util.spec_from_file_location("te", "test_eph.py")
    te = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(te)
    return te


def _find_nav_file() -> Path:
    agnss = Path("agnss_build")
    cands = sorted([*agnss.glob("*.rnx*"), *agnss.glob("*.nav*")])
    if not cands:
        pytest.skip("No RINEX nav file found in agnss_build/; skipping integration test")
    return cands[0]


def _angle_diff(a: float, b: float) -> float:
    d = (a - b + math.pi) % (2 * math.pi) - math.pi
    return abs(d)


def test_georinex_vs_decoded_one_prn():
    te = _load_te()
    nav = _find_nav_file()

    ds = te._load_nav_gps(nav)
    svs = [sv for sv in ds.sv.values if str(sv).startswith("G")]
    assert svs, "No GPS SVs in RINEX"
    target = "G32" if "G32" in set(map(str, svs)) else str(svs[0])

    svids, frames = te.eph_from_nav(nav)
    svid_to_frame = {svid: fr for svid, fr in zip(svids, frames)}
    svid = int(target[1:])
    assert svid in svid_to_frame, f"No frame built for {target}"
    decoded = te.decode_gpseph(svid_to_frame[svid])

    rows = ds.sel(sv=target)
    arr = rows["sqrtA"].values
    valid_idx = np.where(~np.isnan(arr))[0]
    assert valid_idx.size > 0, f"No valid ephemeris rows for {target}"
    r = rows.isel(time=int(valid_idx[-1]))

    def g_any(names, default: float = 0.0) -> float:
        if isinstance(names, str):
            names = [names]
        for name in names:
            if name in r.variables:
                return float(r[name].values)
        return default

    src = dict(
        sqrtA=g_any(["sqrtA"]),
        e=g_any(["e", "Eccentricity"]),
        i0=g_any(["i0", "Io"]),
        OMEGA0=g_any(["OMEGA0", "Omega0"]),
        omega=g_any(["omega"]),
        M0=g_any(["M0"]),
        DeltaN=g_any(["DeltaN"]),
        OMEGADOT=g_any(["OMEGADOT", "OmegaDot"]),
        IDOT=g_any(["IDOT"]),
        cuc=g_any(["cuc", "Cuc"]),
        cus=g_any(["cus", "Cus"]),
        crc=g_any(["crc", "Crc"]),
        crs=g_any(["crs", "Crs"]),
        cic=g_any(["cic", "Cic"]),
        cis=g_any(["cis", "Cis"]),
        toe=g_any(["toe", "Toe"]),
        af0=g_any(["af0", "SVclockBias"]),
        af1=g_any(["af1", "SVclockDrift"]),
        af2=g_any(["af2", "SVclockDriftRate"]),
    )

    t_posix = np.datetime64(r["time"].values, "s").astype(int)
    gps_epoch = te.dt.datetime(1980, 1, 6, tzinfo=te.dt.timezone.utc).timestamp()
    sec_since_gps = float(t_posix - gps_epoch)
    src["toc"] = sec_since_gps % (7 * 86400)

    if "week" in r.variables:
        src["week"] = int(r["week"].values)
    elif "GPSWeek" in r.variables:
        src["week"] = int(r["GPSWeek"].values)
    else:
        src["week"] = int(sec_since_gps // (7 * 86400))

    if "tgd" in r.variables:
        src["tgd"] = g_any("tgd")
    elif "TGD" in r.variables:
        src["tgd"] = g_any("TGD")
    else:
        src["tgd"] = 0.0

    pi = math.pi
    lsb = dict(
        sqrtA=2.0 ** -19,
        e=2.0 ** -33,
        omega=pi * (2.0 ** -31),
        M0=pi * (2.0 ** -31),
        i0=pi * (2.0 ** -31),
        OMEGA0=pi * (2.0 ** -31),
        OMEGADOT=pi * (2.0 ** -43),
        DeltaN=pi * (2.0 ** -43),
        IDOT=pi * (2.0 ** -43),
        cuc=pi * (2.0 ** -29),
        cus=pi * (2.0 ** -29),
        cic=pi * (2.0 ** -29),
        cis=pi * (2.0 ** -29),
        crc=2.0 ** -5,
        crs=2.0 ** -5,
        toe=16.0,
        toc=16.0,
        af0=2.0 ** -31,
        af1=2.0 ** -43,
        af2=2.0 ** -55,
        tgd=2.0 ** -31,
    )

    for k, tol in lsb.items():
        src_v = src[k]
        dec_v = decoded[k]
        if k in ("omega", "M0", "i0", "OMEGA0"):
            diff = _angle_diff(dec_v, src_v)
        elif k in ("toe", "toc"):
            d = abs(dec_v - src_v) % (7 * 86400)
            diff = min(d, 7 * 86400 - d)
        else:
            diff = abs(dec_v - src_v)
        assert diff <= tol + 1e-12, f"{k}: |dec-src|={diff} > LSB={tol}"


def _ecef_from_eph(p: dict) -> np.ndarray:
    mu = 3.986005e14
    Oe = 7.2921151467e-5

    sqrtA = p["sqrtA"]; A = sqrtA * sqrtA
    e = p["e"]
    M0 = p["M0"]
    DeltaN = p["DeltaN"]
    omega = p["omega"]
    cuc, cus = p["cuc"], p["cus"]
    crc, crs = p["crc"], p["crs"]
    cic, cis = p["cic"], p["cis"]
    i0, IDOT = p["i0"], p["IDOT"]
    OMEGA0, OMEGADOT = p["OMEGA0"], p["OMEGADOT"]
    toe = p["toe"]

    n0 = math.sqrt(mu / (A ** 3))
    n = n0 + DeltaN
    tk = 0.0
    M = M0 + n * tk

    E = M
    for _ in range(20):
        f = E - e * math.sin(E) - M
        fp = 1 - e * math.cos(E)
        dE = -f / fp
        E += dE
        if abs(dE) < 1e-14:
            break

    v = math.atan2(math.sqrt(1 - e * e) * math.sin(E), math.cos(E) - e)
    phi = v + omega

    du = cuc * math.cos(2 * phi) + cus * math.sin(2 * phi)
    dr = crc * math.cos(2 * phi) + crs * math.sin(2 * phi)
    di = cic * math.cos(2 * phi) + cis * math.sin(2 * phi)

    u = phi + du
    r = A * (1 - e * math.cos(E)) + dr
    i = i0 + IDOT * tk + di

    x_orb = r * math.cos(u)
    y_orb = r * math.sin(u)

    OMEGA = OMEGA0 + (OMEGADOT - Oe) * tk - Oe * toe
    x = x_orb * math.cos(OMEGA) - y_orb * math.cos(i) * math.sin(OMEGA)
    y = x_orb * math.sin(OMEGA) + y_orb * math.cos(i) * math.cos(OMEGA)
    z = y_orb * math.sin(i)
    return np.array([x, y, z])


def test_geometry_sanity_at_toe():
    te = _load_te()
    nav = _find_nav_file()
    ds = te._load_nav_gps(nav)

    svs = [sv for sv in ds.sv.values if str(sv).startswith("G")]
    target = "G32" if "G32" in set(map(str, svs)) else str(svs[0])

    svids, frames = te.eph_from_nav(nav)
    svid_to_frame = {svid: fr for svid, fr in zip(svids, frames)}
    svid = int(target[1:])
    dec = te.decode_gpseph(svid_to_frame[svid])

    rows = ds.sel(sv=target)
    arr = rows["sqrtA"].values
    valid_idx = np.where(~np.isnan(arr))[0]
    r = rows.isel(time=int(valid_idx[-1]))

    def g_any(names, default: float = 0.0) -> float:
        if isinstance(names, str):
            names = [names]
        for name in names:
            if name in r.variables:
                return float(r[name].values)
        return default

    src = dict(
        sqrtA=g_any(["sqrtA"]),
        e=g_any(["e", "Eccentricity"]),
        i0=g_any(["i0", "Io"]),
        OMEGA0=g_any(["OMEGA0", "Omega0"]),
        omega=g_any(["omega"]),
        M0=g_any(["M0"]),
        DeltaN=g_any(["DeltaN"]),
        OMEGADOT=g_any(["OMEGADOT", "OmegaDot"]),
        IDOT=g_any(["IDOT"]),
        cuc=g_any(["cuc", "Cuc"]),
        cus=g_any(["cus", "Cus"]),
        crc=g_any(["crc", "Crc"]),
        crs=g_any(["crs", "Crs"]),
        cic=g_any(["cic", "Cic"]),
        cis=g_any(["cis", "Cis"]),
        toe=g_any(["toe", "Toe"]),
    )

    pos_src = _ecef_from_eph(src)
    pos_dec = _ecef_from_eph(dec)
    err = float(np.linalg.norm(pos_dec - pos_src))
    assert err < 10.0, f"ECEF delta at toe too large: {err:.3f} m"


def load_module():
    spec = importlib.util.spec_from_file_location('te', 'test_eph.py')
    te = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(te)
    return te


def _hex_bytes(s: str) -> bytes:
    return bytes(int(b, 16) for b in s.split())


def test_aid_ini_matches_sample():
    te = load_module()

    expected_hex = (
        "BA CE 38 00 0B 01 52 B8 1E 85 EB D1 3F 40 D7 A3 70 3D 0A 47 5D 40 "
        "00 00 00 00 00 00 00 00 00 00 00 00 7C 2E 11 41 00 00 00 00 00 00 "
        "00 00 00 00 00 3F 00 00 00 00 E5 07 00 00 62 08 00 23 19 B4 48 E7"
    )
    expected = _hex_bytes(expected_hex)

    te.gps_week_tow = lambda now=None: (2146, 281503.0)

    frame = te.make_aid_ini(
        lat=31.82,
        lon=117.11,
        alt_m=0.0,
        tacc_s=0.5,
        flags=0x23,
        reserved_u4=2021,
    )

    assert frame == expected


def _parse_gpseph_payload_to_fields(payload: bytes):
    off = 0
    def u4():
        nonlocal off
        v = struct.unpack_from('<I', payload, off)[0]; off += 4; return v
    def i4():
        nonlocal off
        v = struct.unpack_from('<i', payload, off)[0]; off += 4; return v
    def i2():
        nonlocal off
        v = struct.unpack_from('<h', payload, off)[0]; off += 2; return v
    def u2():
        nonlocal off
        v = struct.unpack_from('<H', payload, off)[0]; off += 2; return v
    def i1():
        nonlocal off
        v = struct.unpack_from('<b', payload, off)[0]; off += 1; return v
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
    iodc = u2(); ura = u1(); health = u1(); svid = u1(); valid = u1(); reserved1_u2 = u2()

    def sc_u(v, p): return v * (2.0 ** p)
    def sc_i(v, p): return v * (2.0 ** p)
    def semi(v): return v * 3.141592653589793

    fields = dict(
        reserved0=reserved0,
        svid=svid,
        sqrtA=sc_u(sqrtA_raw, -19),
        e=sc_u(e_raw, -33),
        omega=semi(sc_i(omega_raw, -31)),
        M0=semi(sc_i(M0_raw, -31)),
        i0=semi(sc_i(i0_raw, -31)),
        OMEGA0=semi(sc_i(OMEGA0_raw, -31)),
        OMEGADOT=semi(sc_i(OMEGADOT_raw, -43)),
        DeltaN=semi(sc_i(DeltaN_raw, -43)),
        IDOT=semi(sc_i(IDOT_raw, -43)),
        cuc=semi(sc_i(cuc_raw, -29)),
        cus=semi(sc_i(cus_raw, -29)),
        crc=sc_i(crc_raw, -5),
        crs=sc_i(crs_raw, -5),
        cic=semi(sc_i(cic_raw, -29)),
        cis=semi(sc_i(cis_raw, -29)),
        toe=sc_u(toe_raw, +4),
        week=week,
        toc=sc_u(toc_raw, +4),
        af0=sc_i(af0_raw, -31),
        af1=sc_i(af1_raw, -43),
        af2=sc_i(af2_raw, -55),
        tgd=sc_i(tgd_raw, -31),
        iodc=iodc,
        ura=ura,
        health=health,
        valid=valid,
        reserved1_u2=reserved1_u2,
    )
    return fields


def test_gpseph_matches_samples():
    te = load_module()

    samples = [
        (
            "BA CE 48 00 08 07 CD CD 9A 10 E5 7D 0D A1 A0 03 59 05 58 30 63 21 98 4B 91 03 DA 64 0F 28 EC 77 "
            "2E B1 4C A8 FF FF EC 2C 81 05 12 05 85 14 DB 19 9C 05 0C 00 40 00 FA 32 63 00 FA 32 00 00 8E 96 "
            "18 00 B7 FF 00 0A 27 00 00 00 01 03 A3 41 E2 9B 3D 28"
        ),
        (
            "BA CE 48 00 08 07 F3 E1 31 8C 1E FC 0C A1 8D A9 6D 0A 3B F5 4C C1 28 F5 A4 0D 0A 5C 32 27 C1 AD D6 AD AB A3 FF FF E0 31 8F 04 DA 05 7F 15 "
            "26 17 08 07 57 FF B1 FF FA 32 63 00 FA 32 00 00 39 ED EC FF DF FF 00 DA 2A 00 00 00 02 03 A3 41 2E C4 6B 1F"
        ),
    ]

    for sample_hex in samples:
        frame = _hex_bytes(sample_hex)
        assert frame[:2] == b"\xBA\xCE"
        length, cls_, mid = struct.unpack('<HBB', frame[2:6])
        assert (length, cls_, mid) == (72, 0x08, 0x07)
        payload = frame[6:6+length]
        fields = _parse_gpseph_payload_to_fields(payload)

        rebuilt = te.make_msg_gpseph(**fields)
        assert rebuilt == frame

