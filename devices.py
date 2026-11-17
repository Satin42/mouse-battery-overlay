"""Battery providers. Each returns {'percent', 'charging', 'name'} or None.

razer      - Razer wireless mice over HID feature reports (verified on a DeathAdder V4 Pro)
logitech   - Logitech HID++ 2.0 via Unified Battery (0x1004) / Battery Status (0x1000)
             (receiver or direct; written from the public protocol, NOT yet tested on hardware)
bluetooth  - any Bluetooth mouse whose battery Windows exposes (DEVPKEY_Device_BatteryLevel)
             (NOT yet tested on a Bluetooth mouse)
"""
import ctypes
import time
from ctypes import wintypes

import hid

# ================================================================= Razer
RAZER_VID = 0x1532
_RZ_TIDS = (0x1F, 0xFF, 0x3F)  # transaction ids used by different Razer generations


def _rz_pkt(tid, cls, cid, size):
    b = bytearray(90)
    b[1], b[5], b[6], b[7] = tid, size, cls, cid
    c = 0
    for x in b[2:88]:
        c ^= x
    b[88] = c
    return bytes([0]) + bytes(b)


log_fn = None  # set by core; used for diagnostics only


def _dbg(msg):
    if log_fn:
        try:
            log_fn(msg)
        except Exception:
            pass


def _rz_ask(h, tid, cid):
    """Send a class-0x07 query and return the reply to THIS command, or None.
    Wireless replies are relayed by the dongle and arrive late; reading too early returns
    the previous command's reply (that produced bogus 0% / 100%). So the echoed command
    class/id must match, and status must be success; status 0/1 means 'not ready, keep polling'."""
    h.send_feature_report(_rz_pkt(tid, 0x07, cid, 2))
    deadline = time.time() + 0.6
    time.sleep(0.03)
    while time.time() < deadline:
        r = h.get_feature_report(0, 91)
        if len(r) > 10 and r[7] == 0x07 and r[8] == cid:
            if r[1] == 2:
                return r
            if r[1] in (3, 4, 5):  # failed / timed out (mouse asleep) / not supported
                _dbg(f"razer cmd {cid:#x} tid {tid:#x} status {r[1]}")
                return None
        time.sleep(0.03)
    return None


def _rz_try(path, tid):
    h = hid.device()
    try:
        h.open_path(path)
        raws = []
        for _ in range(3):
            r = _rz_ask(h, tid, 0x80)
            if r is not None:
                raws.append(r[10])
            time.sleep(0.05)
        if not raws:
            return None
        good = sorted(x for x in raws if x > 0)  # a raw level of 0 is a glitch, not an empty battery
        _dbg(f"razer tid {tid:#x} raw samples {raws}")
        if not good:
            return None
        raw = good[len(good) // 2]  # median
        r2 = _rz_ask(h, tid, 0x84)
        charging = bool(r2 is not None and r2[10] == 1)
        return round(raw / 255 * 100), charging
    except Exception as e:
        _dbg(f"razer read error: {e!r}")
        return None
    finally:
        try:
            h.close()
        except Exception:
            pass


_rz_cache = None


def razer(prefer=None):
    global _rz_cache
    devs = [d for d in hid.enumerate(RAZER_VID, 0) if d["interface_number"] in (0, 1, 2)]
    # only products that expose a mouse collection (skips keyboards/headsets)
    mouse_pids = {d["product_id"] for d in devs if d["usage_page"] == 1 and d["usage"] == 2}
    devs = [d for d in devs if d["product_id"] in mouse_pids]
    devs.sort(key=lambda d: (d["usage_page"] != 1 or d["usage"] != 2, d["interface_number"]))
    by_path = {d["path"]: d for d in devs}
    order = []
    if _rz_cache and _rz_cache[0] in by_path:
        order.append(_rz_cache)
    for d in devs:
        for tid in _RZ_TIDS:
            order.append((d["path"], tid))
    seen = set()
    for path, tid in order:
        if (path, tid) in seen:
            continue
        seen.add((path, tid))
        res = _rz_try(path, tid)
        if res:
            _rz_cache = (path, tid)
            return {"percent": res[0], "charging": res[1],
                    "name": by_path[path].get("product_string") or "Razer mouse"}
    _rz_cache = None
    return None


# ================================================================= Logitech HID++ 2.0
LOGI_VID = 0x046D
_SW_ID = 0x07


def _lg_request(h, idx, feat_idx, func, params=b""):
    msg = bytearray(20)
    msg[0], msg[1], msg[2], msg[3] = 0x11, idx, feat_idx, (func << 4) | _SW_ID
    msg[4:4 + len(params)] = params
    h.write(bytes(msg))
    deadline = time.time() + 0.25
    while time.time() < deadline:
        r = h.read(20, 60)
        if not r:
            continue
        if r[0] in (0x10, 0x11) and r[1] == idx:
            if r[2] == 0xFF:  # HID++ error report: device absent / unsupported
                return None
            if r[2] == feat_idx and (r[3] & 0x0F) == _SW_ID:
                return bytes(r[4:])
    return None


def _lg_feature(h, idx, feature_id):
    r = _lg_request(h, idx, 0x00, 0, bytes([feature_id >> 8, feature_id & 0xFF]))
    return r[0] if r and r[0] else None


_CHARGING = {1, 2, 4}


def _lg_device(h, idx):
    # skip keyboards, headsets etc. when the device reports its type
    ft = _lg_feature(h, idx, 0x0005)
    if ft is not None:
        r = _lg_request(h, idx, ft, 2)  # getDeviceType
        if r is not None and r[0] not in (3, 4, 5):  # mouse, trackpad, trackball
            return None
    f = _lg_feature(h, idx, 0x1004)  # Unified Battery
    if f is not None:
        r = _lg_request(h, idx, f, 1)  # getStatus -> [percent, level, chargingStatus, ...]
        if r:
            return r[0], r[2] in _CHARGING
    f = _lg_feature(h, idx, 0x1000)  # Battery level status
    if f is not None:
        r = _lg_request(h, idx, f, 0)  # -> [percent, nextLevel, status]
        if r:
            return r[0], r[2] in _CHARGING
    return None


def logitech(prefer=None):
    cands = [d for d in hid.enumerate(LOGI_VID, 0) if d["usage_page"] == 0xFF00 and d["usage"] == 0x0002]
    for d in cands:
        h = hid.device()
        try:
            h.open_path(d["path"])
            for idx in (0xFF, 1, 2, 3, 4, 5, 6):  # 0xFF = wired/Bluetooth, 1-6 = receiver slots
                res = _lg_device(h, idx)
                if res:
                    return {"percent": max(0, min(100, res[0])), "charging": res[1],
                            "name": d.get("product_string") or "Logitech mouse"}
        except Exception:
            continue
        finally:
            try:
                h.close()
            except Exception:
                pass
    return None


# ================================================================= Windows / Bluetooth
class GUID(ctypes.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s):
        import uuid
        u = uuid.UUID(s)
        return cls(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.ULONG)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", GUID), ("DevInst", wintypes.DWORD),
                ("Reserved", ctypes.c_void_p)]


GUID_MOUSE = "{4D36E96F-E325-11CE-BFC1-08002BE10318}"
_BATTERY = DEVPROPKEY(GUID.parse("{104EA319-6EE2-4701-BD47-8DDBF425BBE5}"), 2)
_FRIENDLY = DEVPROPKEY(GUID.parse("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 14)

_setupapi = ctypes.windll.setupapi
_cfg = ctypes.windll.cfgmgr32
_setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
_setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
_setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
_setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
_cfg.CM_Get_DevNode_PropertyW.argtypes = [wintypes.DWORD, ctypes.POINTER(DEVPROPKEY), ctypes.POINTER(wintypes.ULONG),
                                          ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.ULONG]
_cfg.CM_Get_Parent.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.ULONG]


def _prop(node, key):
    ptype, size = wintypes.ULONG(), wintypes.ULONG(0)
    _cfg.CM_Get_DevNode_PropertyW(node, ctypes.byref(key), ctypes.byref(ptype), None, ctypes.byref(size), 0)
    if size.value == 0:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if _cfg.CM_Get_DevNode_PropertyW(node, ctypes.byref(key), ctypes.byref(ptype), buf, ctypes.byref(size), 0) != 0:
        return None
    return ptype.value, buf.raw[:size.value]


def bluetooth(prefer=None, class_guid=GUID_MOUSE):
    """Walk each mouse device node and its parents (the BLE node carries the battery property)."""
    guid = GUID.parse(class_guid)
    hdev = _setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, 0x2)  # DIGCF_PRESENT
    if not hdev or hdev == ctypes.c_void_p(-1).value:
        return None
    try:
        i = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not _setupapi.SetupDiEnumDeviceInfo(hdev, i, ctypes.byref(info)):
                return None
            i += 1
            node, name = info.DevInst, None
            for _ in range(5):
                if name is None:
                    fn = _prop(node, _FRIENDLY)
                    if fn and fn[0] == 0x12:  # DEVPROP_TYPE_STRING
                        name = fn[1].decode("utf-16-le", "ignore").rstrip("\0") or None
                b = _prop(node, _BATTERY)
                if b and b[0] == 0x3 and b[1]:  # DEVPROP_TYPE_BYTE
                    return {"percent": b[1][0], "charging": False, "name": name or "Bluetooth mouse"}
                parent = wintypes.DWORD()
                if _cfg.CM_Get_Parent(ctypes.byref(parent), node, 0) != 0:
                    break
                node = parent.value
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(hdev)


PROVIDERS = (("razer", razer), ("logitech", logitech), ("bluetooth", bluetooth))
