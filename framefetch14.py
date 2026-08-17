#!/usr/bin/env python3
"""
framefetch.py — v14 Framework physical hardware dashboard for the Framework 13 ASCII dashboard.

Target:
- Framework Laptop 13, AMD Ryzen AI 300
- Linux / CachyOS / KDE Plasma / Wayland
- static output, Fastfetch-like

What is already automatic:
- CPU model / usage / frequency / temperature
- GPU model / usage / frequency when exposed by amdgpu
- RAM total / used
- SODIMM information when dmidecode is readable
- internal NVMe model / filesystem usage
- laptop battery + peripheral batteries exposed by UPower
- Wi-Fi SSID + instantaneous RX/TX
- connected Bluetooth device names
- internal display resolution / refresh when KScreen exposes it
- fan RPM when hwmon exposes it

What is intentionally NOT guessed:
- exact physical mapping of attached USB devices to Framework PORT 1..4
- exact Framework Expansion Card model for passive cards
- USB-PD watts when the hardware/driver does not expose them

Those are represented by PORT_OVERRIDES for now. Later the port backend can be
replaced without changing the ASCII renderer.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
import random


# =============================================================================
# CONFIG
# =============================================================================

# Edit these while the physical USB-port detector is not implemented.
# Each line is rendered inside the matching port box.
#
# Example:
# 1: ["USB-C Card", "Charger", "PD charging 65W"]
# 4: ["USB-C Card", "RADIATOR", "██████░░ 62%", "620 GiB / 1 TiB"]
#
# Leave an empty list for an empty port.
PORT_OVERRIDES: dict[int, list[str]] = {
    1: [],
    2: [],
    3: [],
    4: [],
}

PHYSICAL_PORT_TYPE = {
    1: "USB4 · 40 Gb/s",
    2: "USB 3.2 Gen2",
    3: "USB4 · 40 Gb/s",
    4: "USB 3.2 Gen2",
}

# Widths inside each box, excluding │ borders.
PORT_WIDTH = 15
RAM_BAR_WIDTH = 19
SSD_BAR_WIDTH = 14
BATTERY_BAR_WIDTH = 20
PERIPHERAL_BATTERY_BAR_WIDTH = 13

# Framework physical slot order for the current AI 300 board.
# DMI enumeration observed on this machine is opposite to the physical drawing.
FRAMEWORK_SODIMM_DMI_ORDER = (1, 0)

# Prefer fan*_max from hwmon. If the kernel does not expose it, use the
# Framework 13 fan maximum as a configurable fallback.
FAN_MAX_RPM_FALLBACK = 6800

LOGO_SHIFT_RIGHT = 5


# =============================================================================
# ANSI COLORS
# =============================================================================

RESET = "\033[0m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"
CYAN = "\033[34m" #sarebbe blue
MAGENTA = "\033[35m"
DIM = "\033[2m"
GOLD = "\033[38;5;220m"
BOLD = "\033[1m"

LOGO = random.choice([ CYAN , YELLOW , GOLD , RED , GREEN ])
LEFT_BOARD_GAP = 1

USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def ansi(text: str, code: str) -> str:
    return f"{code}{text}{RESET}" if USE_COLOR else text


def strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


# =============================================================================
# GENERIC HELPERS
# =============================================================================

def run(cmd: list[str], timeout: float = 2.0) -> str:
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        )
        return p.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
        return ""


def read_text(path: str | Path) -> str:
    try:
        return Path(path).read_text(errors="replace").strip()
    except (OSError, PermissionError):
        return ""


def read_int(path: str | Path) -> Optional[int]:
    try:
        return int(read_text(path))
    except ValueError:
        return None


def fit(text: str, width: int, align: str = "left") -> str:
    """
    Fit visible text into width, preserving ANSI codes if present.
    For now truncation is only applied safely to uncoloured strings.
    """
    visible = strip_ansi(text)
    if len(visible) > width:
        text = visible[: max(0, width - 1)] + "…"
        visible = strip_ansi(text)

    pad = max(0, width - len(visible))
    if align == "right":
        return " " * pad + text
    if align == "center":
        left = pad // 2
        return " " * left + text + " " * (pad - left)
    return text + " " * pad


def percent_bar(percent: float, width: int) -> str:
    p = max(0.0, min(100.0, percent))
    filled = round(width * p / 100)
    return "█" * filled + "░" * (width - filled)


def pct_color(percent: float) -> str:
    """
    Fastfetch-like traffic-light colouring.
    Exact thresholds can be changed later in one place.
    """
    if percent >= 80:
        return RED
    if percent >= 50:
        return YELLOW
    return GREEN


def battery_color(percent: float) -> str:
    if percent <= 20:
        return RED
    if percent <= 50:
        return YELLOW
    return GREEN


def temp_color(temp: float) -> str:
    # User-selected thresholds.
    if temp > 70:
        return RED
    if temp >= 51:
        return YELLOW
    return GREEN


def gib(n: int) -> str:
    return f"{n / (1024**3):.1f} GiB"


def tib_or_gib(n: int) -> str:
    if n >= 1024**4 * 0.8:
        return f"{n / (1024**4):.2f} TiB"
    return f"{n / (1024**3):.0f} GiB"


def compact_rate(bytes_per_sec: float) -> str:
    bits = bytes_per_sec * 8
    if bits >= 1_000_000_000:
        return f"{bits / 1_000_000_000:.1f}G"
    if bits >= 1_000_000:
        return f"{bits / 1_000_000:.1f}M"
    if bits >= 1_000:
        return f"{bits / 1_000:.0f}K"
    return f"{bits:.0f}"


def shift_ascii_line(line: str, shift: int, width: int) -> str:
    """Move ASCII art right while keeping the fixed-width box."""
    moved = (" " * max(0, shift)) + line.rstrip()
    return fit(moved, width)


# =============================================================================
# CPU / GPU
# =============================================================================

def cpu_model() -> str:
    for line in read_text("/proc/cpuinfo").splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip().replace("AMD ", "")
    return "Unknown CPU"


def cpu_usage(sample: float = 0.12) -> float:
    def snap() -> tuple[int, int]:
        line = read_text("/proc/stat").splitlines()[0]
        vals = [int(x) for x in line.split()[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return idle, sum(vals)

    try:
        i1, t1 = snap()
        time.sleep(sample)
        i2, t2 = snap()
        dt = t2 - t1
        return 0.0 if dt <= 0 else (1.0 - (i2 - i1) / dt) * 100.0
    except Exception:
        return 0.0


def cpu_freq_ghz() -> Optional[float]:
    values = []
    for p in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq"):
        v = read_int(p)
        if v:
            values.append(v / 1_000_000)
    if values:
        return sum(values) / len(values)

    mhz = []
    for line in read_text("/proc/cpuinfo").splitlines():
        if line.startswith("cpu MHz"):
            try:
                mhz.append(float(line.split(":", 1)[1]))
            except ValueError:
                pass
    return (sum(mhz) / len(mhz) / 1000) if mhz else None


def cpu_temp() -> Optional[float]:
    preferred = []
    fallback = []

    for hw in Path("/sys/class/hwmon").glob("hwmon*"):
        name = read_text(hw / "name").lower()
        for p in hw.glob("temp*_input"):
            raw = read_int(p)
            if raw is None:
                continue
            value = raw / 1000.0
            label = read_text(p.with_name(p.name.replace("_input", "_label"))).lower()
            item = (label, value)
            if any(x in name for x in ("k10temp", "zenpower")) or "tctl" in label:
                preferred.append(item)
            else:
                fallback.append(item)

    candidates = preferred or fallback
    if not candidates:
        return None

    # Prefer Tctl/Tdie/package-like sensor.
    for label, value in candidates:
        if any(x in label for x in ("tctl", "tdie", "package")):
            return value
    return candidates[0][1]


def gpu_model() -> str:
    out = run(["lspci"])
    for line in out.splitlines():
        if re.search(r"(VGA compatible controller|Display controller)", line, re.I):
            model = line.split(":", 2)[-1].strip()
            model = re.sub(r"\s*\(rev [^)]+\)$", "", model)
            model = model.replace("Advanced Micro Devices, Inc. [AMD/ATI] ", "")
            return model
    return "Radeon GPU"


def amdgpu_card() -> Optional[Path]:
    for card in Path("/sys/class/drm").glob("card[0-9]"):
        vendor = read_text(card / "device/vendor")
        driver = ""
        try:
            driver = (card / "device/driver").resolve().name
        except OSError:
            pass
        if vendor == "0x1002" or driver == "amdgpu":
            return card
    return None


def gpu_usage() -> Optional[float]:
    card = amdgpu_card()
    if not card:
        return None
    v = read_int(card / "device/gpu_busy_percent")
    return float(v) if v is not None else None


def gpu_freq_ghz() -> Optional[float]:
    card = amdgpu_card()
    if not card:
        return None

    # Modern amdgpu may expose current sclk through pp_dpm_sclk.
    text = read_text(card / "device/pp_dpm_sclk")
    for line in text.splitlines():
        if "*" in line:
            m = re.search(r"(\d+)\s*Mhz", line, re.I)
            if m:
                return int(m.group(1)) / 1000

    # Alternate hwmon exposure.
    for p in (card / "device/hwmon").glob("hwmon*/freq1_input"):
        v = read_int(p)
        if v:
            return v / 1_000_000_000
    return None


# =============================================================================
# RAM
# =============================================================================

def ram_usage() -> tuple[int, int, float]:
    vals = {}
    for line in read_text("/proc/meminfo").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        try:
            vals[k] = int(v.strip().split()[0]) * 1024
        except (ValueError, IndexError):
            pass

    total = vals.get("MemTotal", 0)
    available = vals.get("MemAvailable", 0)
    used = max(0, total - available)
    pct = used / total * 100 if total else 0
    return total, used, pct


@dataclass
class Sodimm:
    locator: str = "SODIMM"
    size: str = "UNKNOWN"
    kind: str = "DDR5"
    speed: str = ""
    maker: str = ""
    part: str = ""


def _parse_dmidecode_memory(out: str) -> list[Sodimm]:
    result: list[Sodimm] = []
    for block in re.split(r"\n\s*\n", out):
        if "Memory Device" not in block:
            continue

        def value(key: str) -> str:
            m = re.search(rf"^\s*{re.escape(key)}:\s*(.+)$", block, re.M)
            return m.group(1).strip() if m else ""

        size = value("Size")
        if not size or size == "No Module Installed":
            result.append(Sodimm(locator=value("Locator") or "SODIMM", size="EMPTY", kind="DDR5"))
            continue

        speed = (value("Configured Memory Speed") or value("Speed")).replace(" MT/s", "")
        result.append(Sodimm(
            locator=value("Locator") or "SODIMM",
            size=size,
            kind=value("Type") or "DDR5",
            speed=speed,
            maker=value("Manufacturer"),
            part=value("Part Number"),
        ))
    return result[:2]


def _parse_lshw_memory(out: str) -> list[Sodimm]:
    """Non-root fallback for systems where dmidecode is protected."""
    result: list[Sodimm] = []
    blocks = re.split(r"(?=\n\s*\*-bank(?::\d+)?\s*$)", "\n" + out, flags=re.M)
    for block in blocks:
        if "*-bank" not in block:
            continue

        def field(name: str) -> str:
            m = re.search(rf"^\s*{re.escape(name)}:\s*(.+)$", block, re.M | re.I)
            return m.group(1).strip() if m else ""

        slot = field("slot") or "SODIMM"
        size = field("size")
        desc = field("description")
        product = field("product")
        vendor = field("vendor")
        clock = field("clock")

        if not size:
            result.append(Sodimm(locator=slot, size="EMPTY", kind="DDR5"))
            continue

        size = re.sub(r"(?i)gib$", " GB", size)
        size = re.sub(r"(?i)mib$", " MB", size)
        speed = ""
        m = re.search(r"(\d{4,5})\s*MHz", desc, re.I)
        if m:
            speed = m.group(1)
        elif clock:
            m = re.search(r"([\d.]+)\s*(GHz|MHz)", clock, re.I)
            if m:
                v = float(m.group(1))
                speed = str(round(v * 1000 if m.group(2).lower() == "ghz" else v))

        result.append(Sodimm(
            locator=slot,
            size=size,
            kind="DDR5" if "DDR5" in desc.upper() else "DDR5",
            speed=speed,
            maker=vendor,
            part=product,
        ))
    return result[:2]


def _real_user_home() -> Path:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            import pwd
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except Exception:
            pass
    return Path.home()


def _hardware_cache_path() -> Path:
    return _real_user_home() / ".cache" / "framefetch" / "hardware.json"


def _load_cached_sodimms() -> list[Sodimm]:
    p = _hardware_cache_path()
    try:
        data = json.loads(p.read_text())
        return [
            Sodimm(
                locator=x.get("locator", "SODIMM"),
                size=x.get("size", "UNKNOWN"),
                kind=x.get("kind", "DDR5"),
                speed=x.get("speed", ""),
                maker=x.get("maker", ""),
                part=x.get("part", ""),
            )
            for x in data.get("sodimms", [])
        ][:2]
    except Exception:
        return []


def cache_hardware() -> tuple[bool, str]:
    out = run(["dmidecode", "-t", "memory"], timeout=4.0)
    if not out:
        return False, "dmidecode non leggibile. Esegui con sudo."

    parsed = _parse_dmidecode_memory(out)
    if not parsed:
        return False, "Nessuno slot RAM leggibile da SMBIOS."

    p = _hardware_cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "sodimms": [
                {
                    "locator": s.locator,
                    "size": s.size,
                    "kind": s.kind,
                    "speed": s.speed,
                    "maker": s.maker,
                    "part": s.part,
                }
                for s in parsed
            ]
        }, indent=2))
        sudo_uid = os.environ.get("SUDO_UID")
        sudo_gid = os.environ.get("SUDO_GID")
        if sudo_uid and sudo_gid:
            os.chown(p.parent, int(sudo_uid), int(sudo_gid))
            os.chown(p, int(sudo_uid), int(sudo_gid))
        return True, str(p)
    except OSError as e:
        return False, f"Impossibile scrivere cache: {e}"


def sodimms() -> list[Sodimm]:
    """
    Physical RAM layout is effectively static during a normal boot/session.
    Prefer the explicit hardware cache created with --cache-hardware.

    This avoids spawning dmidecode/sudo/lshw on every framefetch invocation,
    which was the single largest measured startup cost.
    """
    cached = _load_cached_sodimms()
    if cached:
        return cached

    out = run(["dmidecode", "-t", "memory"], timeout=2.0)
    if not out:
        out = run(["sudo", "-n", "dmidecode", "-t", "memory"], timeout=2.0)
    if out:
        parsed = _parse_dmidecode_memory(out)
        if parsed:
            return parsed

    if shutil.which("lshw"):
        parsed = _parse_lshw_memory(run(["lshw", "-C", "memory"], timeout=3.0))
        if parsed:
            return parsed

    return []

# =============================================================================
# STORAGE
# =============================================================================

@dataclass
class Disk:
    model: str
    total: int
    used: int
    free: int
    mountpoint: str
    transport: str = ""
    name: str = ""
    label: str = ""
    physical_port: Optional[int] = None
    usb_signature: str = ""

    @property
    def pct(self) -> float:
        return self.used / self.total * 100 if self.total else 0.0

    @property
    def display_name(self) -> str:
        return (self.label or self.model or self.name or "Storage").strip()


def lsblk_json() -> dict:
    out = run([
        "lsblk", "-J", "-b",
        "-o", "NAME,MODEL,SIZE,TYPE,TRAN,MOUNTPOINTS,PKNAME,LABEL"
    ])
    try:
        return json.loads(out) if out else {}
    except json.JSONDecodeError:
        return {}


def root_disk(lsblk_data: Optional[dict] = None) -> Optional[Disk]:
    root = shutil.disk_usage("/")
    data = lsblk_data if lsblk_data is not None else lsblk_json()
    root_source = run(["findmnt", "-n", "-o", "SOURCE", "/"])
    root_name = Path(root_source).name
    model = ""
    transport = ""

    def walk(items):
        nonlocal model, transport
        for item in items:
            if item.get("name") == root_name:
                parent = item.get("pkname")
                if parent:
                    # Search parent on second pass.
                    pass
            if root_source.endswith(str(item.get("name", ""))) and item.get("model"):
                model = (item.get("model") or "").strip()
                transport = item.get("tran") or ""
            walk(item.get("children") or [])

    walk(data.get("blockdevices") or [])

    # Better: locate top-level block device containing root.
    def contains_name(item, target):
        if item.get("name") == target:
            return True
        return any(contains_name(c, target) for c in item.get("children") or [])

    for top in data.get("blockdevices") or []:
        if contains_name(top, root_name):
            model = (top.get("model") or model or top.get("name") or "NVMe SSD").strip()
            transport = top.get("tran") or transport
            break

    return Disk(
        model=model or "NVMe SSD",
        total=root.total,
        used=root.used,
        free=root.free,
        mountpoint="/",
        transport=transport,
        name=root_name,
    )


def _block_device_sysfs_path(block_name: str) -> Optional[Path]:
    p = Path(f"/sys/class/block/{block_name}/device")
    try:
        return p.resolve()
    except OSError:
        return None


def _find_usb_ancestor(path: Path) -> Optional[Path]:
    """
    Find the USB device node in a block device's parent chain.

    UAS/SCSI paths can look like:
      .../usb7/7-1/7-1:1.0/host7/target7:0:0/7:0:0:0

    so the final block-device path itself isn't named like a USB device.
    """
    candidates = [path, *path.parents]

    # Prefer a real USB device directory N-M(.M...), not an interface N-M:X.Y.
    for p in candidates:
        name = p.name
        if re.fullmatch(r"\d+-\d+(?:\.\d+)*", name):
            if (p / "idVendor").exists() or "/usb" in str(p):
                return p

    # If pathlib parents walked outside /sys devices strangely, parse path and
    # reconstruct the best USB device segment.
    s = str(path)
    matches = re.findall(r"/(\d+-\d+(?:\.\d+)*)(?=/|:)", s)
    if matches:
        devname = matches[-1]
        candidate = Path("/sys/bus/usb/devices") / devname
        if candidate.exists():
            try:
                return candidate.resolve()
            except OSError:
                return candidate

    return None


def _top_block_records(data: dict) -> list[dict]:
    return list(data.get("blockdevices") or [])


def _mounted_child_records(top: dict) -> list[tuple[dict, str]]:
    result: list[tuple[dict, str]] = []

    def visit(item: dict) -> None:
        for mp in item.get("mountpoints") or []:
            if mp and os.path.isdir(mp):
                result.append((item, mp))
        for child in item.get("children") or []:
            visit(child)

    visit(top)
    return result


def _top_device_is_usb(top_name: str) -> tuple[bool, Optional[Path], str, Optional[int]]:
    raw = _block_device_sysfs_path(top_name)
    if raw is None:
        return False, None, "", None

    usb = _find_usb_ancestor(raw)
    if usb is None:
        return False, None, "", None

    sig = _usb_topology_signature(usb) or _usb_topology_signature(raw) or ""

    physical = None
    if sig:
        mapped = _load_port_map().get("usb_signatures", {}).get(sig)
        if mapped is not None:
            try:
                p = int(mapped)
                if p in (1, 2, 3, 4):
                    physical = p
            except (TypeError, ValueError):
                pass

    if physical is None:
        physical = _mapped_framework_port_for_usb(usb)
    if physical is None:
        physical = _mapped_framework_port_for_usb(raw)

    return True, usb, sig, physical


def external_mounted_disks(lsblk_data: Optional[dict] = None) -> list[Disk]:
    """
    Detect mounted external USB storage from the REAL sysfs ancestry.

    Does not require lsblk TRAN=usb.
    """
    data = lsblk_data if lsblk_data is not None else lsblk_json()
    result: list[Disk] = []

    for top in _top_block_records(data):
        top_name = top.get("name") or ""
        if not top_name:
            continue

        is_usb, usbdev, signature, physical_port = _top_device_is_usb(top_name)

        tran = (top.get("tran") or "").lower()
        is_mmc = tran == "mmc"

        if not is_usb and not is_mmc:
            continue

        model = (top.get("model") or "").strip()
        if not model and usbdev is not None:
            model = read_text(usbdev / "product")
        if not model:
            model = top_name or "External storage"

        mounted = _mounted_child_records(top)

        # A mounted filesystem is required to calculate meaningful free space.
        for item, mp in mounted:
            try:
                st = shutil.disk_usage(mp)
            except OSError:
                continue

            label = (
                (item.get("label") or "").strip()
                or (top.get("label") or "").strip()
            )

            result.append(Disk(
                model=model.strip(),
                total=st.total,
                used=st.used,
                free=st.free,
                mountpoint=mp,
                transport="usb" if is_usb else tran,
                name=item.get("name") or top_name,
                label=label,
                physical_port=physical_port,
                usb_signature=signature,
            ))

    unique: dict[str, Disk] = {}
    for d in result:
        unique[d.mountpoint] = d

    return list(unique.values())


# =============================================================================
# BATTERIES
# =============================================================================

@dataclass
class Battery:
    name: str
    pct: Optional[float]
    status: str = ""
    design_wh: Optional[float] = None
    full_wh: Optional[float] = None
    power_w: Optional[float] = None
    peripheral: bool = False


def _external_power_online() -> bool:
    root = Path("/sys/class/power_supply")
    if not root.exists():
        return False
    for ps in root.iterdir():
        if ps.name.startswith("BAT"):
            continue
        if read_int(ps / "online") == 1:
            return True
    return False


def _battery_wh(b: Path, energy_name: str, charge_name: str, voltage_names: tuple[str, ...]) -> Optional[float]:
    energy = read_int(b / energy_name)
    if energy is not None:
        return energy / 1_000_000
    charge = read_int(b / charge_name)
    voltage = None
    for vn in voltage_names:
        voltage = read_int(b / vn)
        if voltage is not None:
            break
    if charge is not None and voltage is not None:
        return charge * voltage / 1_000_000_000_000
    return None


def laptop_battery() -> Optional[Battery]:
    bats = list(Path("/sys/class/power_supply").glob("BAT*"))
    if not bats:
        return None
    b = bats[0]

    pct = read_int(b / "capacity")
    raw_status = read_text(b / "status")
    external = _external_power_online()

    # Linux often says "Not charging" at 100% even while external power is connected.
    # For framefetch the useful state is whether the machine is externally powered.
    if external and raw_status.lower() != "discharging":
        status = "Charging"
    else:
        status = raw_status or ("Charging" if external else "Discharging")

    power = read_int(b / "power_now")
    if power is not None:
        power = power / 1_000_000
    else:
        current = read_int(b / "current_now")
        voltage = read_int(b / "voltage_now")
        power = current * voltage / 1_000_000_000_000 if current is not None and voltage is not None else None

    return Battery(
        name="Battery",
        pct=float(pct) if pct is not None else None,
        status=status,
        design_wh=_battery_wh(b, "energy_full_design", "charge_full_design", ("voltage_min_design", "voltage_now")),
        full_wh=_battery_wh(b, "energy_full", "charge_full", ("voltage_min_design", "voltage_now")),
        power_w=power,
    )

def _upower_field(info: str, name: str) -> str:
    m = re.search(rf"^\s*{re.escape(name)}:\s*(.+)$", info, re.M | re.I)
    return m.group(1).strip() if m else ""


def _bluetooth_connected_records() -> list[tuple[str, str]]:
    out = run(["bluetoothctl", "devices", "Connected"])
    result = []
    for line in out.splitlines():
        m = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)", line)
        if m:
            result.append((m.group(1), m.group(2).strip()))
    return result


def _bluetooth_battery_percentage(mac: str) -> Optional[float]:
    info = run(["bluetoothctl", "info", mac], timeout=2.0)
    for pattern in (
        r"Battery Percentage:\s*0x[0-9a-fA-F]+\s*\((\d+)\)",
        r"Battery Percentage:\s*(\d+)\s*%",
        r"Battery Percentage:\s*(\d+)\b",
    ):
        m = re.search(pattern, info, re.I)
        if m:
            return max(0.0, min(100.0, float(m.group(1))))

    if shutil.which("busctl"):
        obj = "/org/bluez/hci0/dev_" + mac.replace(":", "_")
        out = run([
            "busctl", "get-property",
            "org.bluez", obj,
            "org.bluez.Battery1", "Percentage"
        ])
        m = re.search(r"\b(?:y|u)\s+(\d+)\b", out)
        if m:
            return max(0.0, min(100.0, float(m.group(1))))
    return None


def peripheral_batteries(bt_records: Optional[list[tuple[str, str]]] = None) -> list[Battery]:
    result = []
    seen = set()

    if shutil.which("upower"):
        for path in run(["upower", "-e"]).splitlines():
            low = path.lower()
            if "displaydevice" in low or "/line_power_" in low or "/line-power-" in low:
                continue
            if re.search(r"/battery_bat\d+$", low):
                continue

            info = run(["upower", "-i", path])
            perc = _upower_field(info, "percentage")
            m = re.search(r"([\d.]+)%", perc)
            if not m:
                continue

            typ = _upower_field(info, "type").lower()
            power_supply = _upower_field(info, "power supply").lower()
            if typ == "battery" and power_supply == "yes":
                continue

            name = (
                _upower_field(info, "model")
                or _upower_field(info, "native-path")
                or Path(path).name
            )
            key = re.sub(r"\W+", "", name.lower())
            if not key:
                continue
            result.append(Battery(name=name, pct=float(m.group(1)), peripheral=True))
            seen.add(key)

    if bt_records is None:
        bt_records = _bluetooth_connected_records()

    for mac, name in bt_records:
        key = re.sub(r"\W+", "", name.lower())
        if key in seen:
            continue
        pct = _bluetooth_battery_percentage(mac)
        if pct is not None:
            result.append(Battery(name=name, pct=pct, peripheral=True))
            seen.add(key)

    return result


# =============================================================================
# NETWORK
# =============================================================================

@dataclass
class Network:
    iface: str = ""
    ssid: str = ""
    kind: str = ""
    rx: float = 0.0
    tx: float = 0.0


def active_network(nmcli_out: Optional[str] = None) -> Network:
    iface = ""
    kind = ""
    ssid = ""

    out = nmcli_out if nmcli_out is not None else run(
        ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"]
    )
    for line in out.splitlines():
        parts = line.split(":", 3)
        if len(parts) != 4:
            continue
        dev, typ, state, conn = parts
        if state == "connected" and typ in ("wifi", "ethernet"):
            # Prefer Wi-Fi here because Ethernet is also rendered in a port later.
            if not iface or typ == "wifi":
                iface, kind, ssid = dev, typ, conn

    if not iface:
        route = run(["ip", "route", "show", "default"])
        m = re.search(r"\bdev\s+(\S+)", route)
        if m:
            iface = m.group(1)
            kind = "wifi" if Path(f"/sys/class/net/{iface}/wireless").exists() else "ethernet"

    if not iface:
        return Network()

    rx_path = Path(f"/sys/class/net/{iface}/statistics/rx_bytes")
    tx_path = Path(f"/sys/class/net/{iface}/statistics/tx_bytes")
    r1 = read_int(rx_path) or 0
    t1 = read_int(tx_path) or 0
    start = time.monotonic()
    time.sleep(0.18)
    r2 = read_int(rx_path) or r1
    t2 = read_int(tx_path) or t1
    dt = max(0.001, time.monotonic() - start)

    return Network(
        iface=iface,
        ssid=ssid,
        kind=kind,
        rx=(r2 - r1) / dt,
        tx=(t2 - t1) / dt,
    )


# =============================================================================
# BLUETOOTH
# =============================================================================

def bluetooth_devices(bt_records: Optional[list[tuple[str, str]]] = None) -> list[str]:
    if bt_records is None:
        bt_records = _bluetooth_connected_records()
    return [name for _, name in bt_records]


# =============================================================================
# DISPLAY
# =============================================================================

@dataclass
class Display:
    name: str = "Built-in"
    width: int = 0
    height: int = 0
    hz: float = 0.0
    builtin: bool = False


def _drm_connector_name(conn: Path) -> str:
    m = re.match(r"card\d+-(.+)", conn.name)
    return m.group(1) if m else conn.name


def _drm_displays() -> list[Display]:
    found = []
    for conn in Path("/sys/class/drm").glob("card*-*"):
        if not conn.is_dir() or read_text(conn / "status").lower() != "connected":
            continue
        name = _drm_connector_name(conn)
        modes = [x.strip() for x in read_text(conn / "modes").splitlines() if x.strip()]
        if not modes:
            continue
        m = re.match(r"(\d+)x(\d+)", modes[0])
        if not m:
            continue
        w, h = map(int, m.groups())
        found.append(Display(
            name=name, width=w, height=h, hz=0.0,
            builtin=name.startswith(("eDP", "LVDS"))
        ))
    return found


def _kscreen_blocks(out: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?m)^\s*Output:\s*\d+\s+(\S+).*?$", out))
    result = []
    for i, m in enumerate(matches):
        begin = m.start()
        finish = matches[i + 1].start() if i + 1 < len(matches) else len(out)
        result.append((m.group(1), out[begin:finish]))
    return result


def _kscreen_active_mode(block: str) -> tuple[int, int, float] | None:
    """
    Parse KScreen's current mode. The current mode is marked by '*':
        2:1920x1080@60.00*
    """
    clean = re.sub(r"\x1b\[[0-9;]*m", "", block)

    # Strongest form: mode token followed by * anywhere in the block.
    matches = re.findall(
        r"(\d{3,5})x(\d{3,5})@([\d.]+)\s*\*",
        clean,
    )
    if matches:
        w, h, hz = matches[0]
        return int(w), int(h), float(hz)

    # Some versions have formatting characters between frequency and '*'.
    m = re.search(
        r"(\d{3,5})x(\d{3,5})@([\d.]+)[^\d\n]{0,12}\*",
        clean,
    )
    if m:
        return int(m.group(1)), int(m.group(2)), float(m.group(3))

    m = re.search(r"res:\s*QSize\((\d+),\s*(\d+)\)", clean)
    if m:
        return int(m.group(1)), int(m.group(2)), 0.0

    m = re.search(r"(\d{3,5})x(\d{3,5})@([\d.]+)", clean)
    if m:
        return int(m.group(1)), int(m.group(2)), float(m.group(3))

    return None


def displays() -> list[Display]:
    found = []
    out = run(["kscreen-doctor", "-o"], timeout=3.0)
    out = re.sub(r"\x1b\[[0-9;]*m", "", out)

    if out:
        for conn, block in _kscreen_blocks(out):
            if "connected" not in block.lower():
                continue
            mode = _kscreen_active_mode(block)
            if not mode:
                continue
            w, h, hz = mode
            found.append(Display(
                name=conn,
                width=w,
                height=h,
                hz=hz,
                builtin=conn.startswith(("eDP", "LVDS")),
            ))

        for m in re.finditer(
            r'KScreen::Output\(\d+,\s*"([^"]+)".*?connected.*?res:\s*QSize\((\d+),\s*(\d+)\)',
            out, re.S
        ):
            conn, w, h = m.group(1), int(m.group(2)), int(m.group(3))
            if not any(d.name == conn for d in found):
                found.append(Display(
                    name=conn, width=w, height=h, hz=0.0,
                    builtin=conn.startswith(("eDP", "LVDS"))
                ))

    by_name = {d.name for d in found}
    for d in _drm_displays():
        if d.name not in by_name:
            found.append(d)

    if not found:
        out = run(["xrandr", "--current"])
        for line in out.splitlines():
            if " connected" not in line:
                continue
            conn = line.split()[0]
            m = re.search(r"(\d+)x(\d+)\+\d+\+\d+", line)
            if m:
                w, h = map(int, m.groups())
                found.append(Display(
                    name=conn, width=w, height=h, hz=0.0,
                    builtin=conn.startswith(("eDP", "LVDS"))
                ))
    return found

# =============================================================================
# FAN
# =============================================================================

def fan_reading() -> tuple[Optional[int], Optional[float], Optional[int]]:
    """Return (rpm, percentage_of_max, max_rpm)."""
    candidates: list[tuple[int, Optional[int]]] = []
    for p in Path("/sys/class/hwmon").glob("hwmon*/fan*_input"):
        rpm = read_int(p)
        if rpm is None or rpm < 0:
            continue
        max_rpm = read_int(p.with_name(p.name.replace("_input", "_max")))
        candidates.append((rpm, max_rpm))
    if not candidates:
        return None, None, None

    rpm, max_rpm = max(candidates, key=lambda x: x[0])
    if not max_rpm or max_rpm <= 0:
        max_rpm = FAN_MAX_RPM_FALLBACK
    pct = max(0.0, min(100.0, rpm / max_rpm * 100.0))
    return rpm, pct, max_rpm


def fan_rpm() -> Optional[int]:
    return fan_reading()[0]


def fan_percent_from_rpm(rpm: Optional[int]) -> Optional[float]:
    if rpm is None:
        return None
    measured, pct, _ = fan_reading()
    if measured == rpm:
        return pct
    return max(0.0, min(100.0, rpm / FAN_MAX_RPM_FALLBACK * 100.0))

# =============================================================================
# PORT CONTENT / PRIORITY RENDERER
# =============================================================================

@dataclass
class PortItem:
    priority: int
    normal: str
    minimal: str


def storage_port_item(disk: Disk) -> PortItem:
    name = disk.model or disk.name or "Storage"
    normal = f"{name} · {tib_or_gib(disk.free)} free"
    short_name = name if len(name) <= 8 else name[:7] + "…"
    minimal = f"{short_name} {tib_or_gib(disk.free).replace(' ', '')}"
    return PortItem(10, normal, minimal)


def ethernet_port_item(rx: float, tx: float) -> PortItem:
    return PortItem(
        9,
        f"ETHERNET ↓ {compact_rate(rx)} ↑ {compact_rate(tx)}bit/s", #viola
        f"ETH ↓{compact_rate(rx)} ↑{compact_rate(tx)}",
    )


def display_port_item(d: Display) -> PortItem:
    hz = round(d.hz) if d.hz else 0
    normal = f"DisplayPort {d.name} {d.width}x{d.height} {hz}Hz"
    minimal = f"DP {d.width}x{d.height} {hz}Hz" #giallo
    return PortItem(9, normal, minimal)


def power_port_item(watts: Optional[float]) -> PortItem:
    if watts is None:
        return PortItem(8, "PD charging", "PD")
    return PortItem(8, f"PD charging {watts:.0f}W", f"PD {watts:.0f}W") #giallo


def render_port_lines(
    physical_type: str,
    override_lines: list[str],
    max_lines: int = 6,
) -> list[str]:
    """
    First version:
    - physical type has lowest priority
    - override lines are treated as already user-curated, high-priority text
    - keep shape fixed
    - drop physical type first when necessary
    - truncate individual lines only as a final width constraint

    Later this function can accept structured PortItems and apply
    NORMAL -> MINIMAL -> PRIORITY DROP exactly.
    """
    content = [physical_type] + list(override_lines)

    # Remove physical port type first if the box would overflow.
    if len(content) > max_lines:
        content = content[1:]

    # Last resort only: retain as many user/device lines as fit.
    if len(content) > max_lines:
        hidden = len(content) - (max_lines - 1)
        content = content[: max_lines - 1] + [f"+{hidden} devices"]

    rendered = []
    for x in content:
        plain = strip_ansi(x)
        line = fit(plain, PORT_WIDTH)

        if plain.startswith("DP "):
            line = line.replace("DP", ansi("DP", MAGENTA), 1)

        elif plain.startswith("ETHERNET "):
            line = line.replace("ETHERNET", ansi("ETHERNET", MAGENTA), 1)

        elif plain.startswith("ETH "):
            line = line.replace("ETH", ansi("ETH", MAGENTA), 1)

        elif plain.startswith("PD charging"):
            line = line.replace(
                "PD charging",
                ansi("PD charging", GOLD),
                1
            )

        elif plain.startswith("PD out"):
            line = line.replace(
                "PD out",
                ansi("PD out", GOLD),
                1
            )

        elif plain.startswith("PD "):
            line = line.replace(
                "PD",
                ansi("PD", GOLD),
                1
            )


        # Detect storage mini-bar lines generated by _storage_bar_line().
        m = re.match(r"^(█+░*|░+)\s+(\d+)%", strip_ansi(line))
        if m:
            try:
                pct = float(m.group(2))
                line = ansi(line, pct_color(pct))
            except ValueError:
                pass

        rendered.append(line)

    return rendered + [" " * PORT_WIDTH] * (max_lines - len(content))


# =============================================================================
# FRAMEWORK PORT AUTO-DETECTION (best effort)
# =============================================================================

def _natural_num(path: Path) -> int:
    m = re.search(r"(\d+)$", path.name)
    return int(m.group(1)) if m else 999


def _typec_ports() -> list[Path]:
    root = Path("/sys/class/typec")
    if not root.exists():
        return []
    return sorted([p for p in root.glob("port[0-9]*") if re.fullmatch(r"port\d+", p.name)], key=_natural_num)


def _selected_role(text: str) -> str:
    m = re.search(r"\[([^]]+)\]", text)
    if m:
        return m.group(1).strip().lower()
    return text.strip().split()[0].lower() if text.strip() else ""


def _typec_has_partner(port: Path) -> bool:
    return Path(f"/sys/class/typec/{port.name}-partner").exists()


def _typec_dp_active(port: Path) -> bool:
    root = Path("/sys/class/typec")
    for alt in root.glob(f"{port.name}-partner.*"):
        svid = read_text(alt / "svid").lower()
        active = read_text(alt / "active").lower()
        if "ff01" in svid and active in ("1", "yes", "true", "active", ""):
            return True
        if (alt / "displayport").exists():
            return True
    return False


def _online_external_power_watts() -> Optional[float]:
    candidates: list[float] = []
    root = Path("/sys/class/power_supply")
    if not root.exists():
        return None
    for ps in root.iterdir():
        if ps.name.startswith("BAT") or read_int(ps / "online") != 1:
            continue
        power = read_int(ps / "power_now")
        if power is not None and power > 0:
            candidates.append(power / 1_000_000)
            continue
        voltage = read_int(ps / "voltage_now") or read_int(ps / "voltage_max")
        current = read_int(ps / "current_now") or read_int(ps / "current_max")
        if voltage and current:
            candidates.append(voltage * current / 1_000_000_000_000)
    return max(candidates) if candidates else None


def _iface_sample(iface: str, delay: float = 0.10) -> tuple[float, float]:
    base = Path(f"/sys/class/net/{iface}/statistics")
    r1 = read_int(base / "rx_bytes") or 0
    t1 = read_int(base / "tx_bytes") or 0
    t0 = time.monotonic()
    time.sleep(delay)
    r2 = read_int(base / "rx_bytes") or r1
    t2 = read_int(base / "tx_bytes") or t1
    dt = max(0.001, time.monotonic() - t0)
    return (r2-r1)/dt, (t2-t1)/dt



# =============================================================================
# PHYSICAL PORT CALIBRATION
# =============================================================================

def _port_map_path() -> Path:
    return _real_user_home() / ".config" / "framefetch" / "ports.json"


def _load_port_map() -> dict:
    """
    Persistent mappings learned once from the actual laptop.

    Format:
      {
        "usb_signatures": {"7:1": 2, ...},
        "typec_indices": {"0": 3, ...}
      }
    """
    p = _port_map_path()
    try:
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            return {}
        data.setdefault("usb_signatures", {})
        data.setdefault("typec_indices", {})
        data.setdefault("display_connectors", {})
        data.setdefault("display_edids", {})
        data.setdefault("display_partner_fingerprints", [])
        return data
    except Exception:
        return {
            "usb_signatures": {},
            "typec_indices": {},
            "display_connectors": {},
            "display_edids": {},
            "display_partner_fingerprints": [],
        }


def _save_port_map(data: dict) -> None:
    p = _port_map_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True))


def _usb_topology_signature(device_path: Path) -> Optional[str]:
    """
    Produce a stable top-level USB root-port signature.

    Example:
      /.../usb7/7-1/7-1:4.2  -> "7:1"

    This remains stable when the same physical USB connector is used and is much
    more useful than the transient USB device address.
    """
    s = str(device_path)

    # Prefer a USB device segment "BUS-PORT[.HUBPORT...]".
    matches = re.findall(r"/(\d+)-(\d+)(?:\.\d+)*(?::\d+\.\d+)?(?:/|$)", s)
    if matches:
        bus, port = matches[-1]
        return f"{int(bus)}:{int(port)}"

    # Fall back to /usbN plus the first child N-P.
    m_bus = re.search(r"/usb(\d+)(?:/|$)", s)
    m_dev = re.search(r"/(\d+)-(\d+)(?:[.:/]|$)", s)
    if m_bus and m_dev:
        return f"{int(m_bus.group(1))}:{int(m_dev.group(2))}"

    return None


def _usb_root_port_connector(device_path: Path) -> Optional[int]:
    """
    Use the kernel's USB hub-port -> Type-C connector symlink.

    ABI:
      .../<hub_interface>/port<X>/connector -> /sys/class/typec/portN

    Actual sysfs names often look like:
      usb7/7-0:1.0/usb7-port1/connector

    The older implementation searched one directory pattern too narrowly.
    """
    sig = _usb_topology_signature(device_path)
    if not sig:
        return None

    bus_s, top_port_s = sig.split(":", 1)
    bus = int(bus_s)
    top_port = int(top_port_s)

    root = Path(f"/sys/bus/usb/devices/usb{bus}")
    if not root.exists():
        return None

    # Search the root hub subtree. Match only the physical top-level port.
    try:
        connectors = list(root.rglob("connector"))
    except OSError:
        connectors = []

    for link in connectors:
        if not link.is_symlink():
            continue

        parent_name = link.parent.name.lower()
        # Examples: usb7-port1, port1
        m = re.search(r"(?:^|-)port(\d+)$", parent_name)
        if not m or int(m.group(1)) != top_port:
            continue

        try:
            idx = _typec_port_number_from_target(link.resolve())
            if idx is not None:
                return idx
        except OSError:
            continue

    return None


def _mapped_framework_port_for_usb(device_path: Path) -> Optional[int]:
    """
    Resolution order:
      1. kernel direct Type-C relation
      2. root-hub connector symlink
      3. persistent calibrated USB topology signature
    """
    idx = _usb_device_typec_port_direct(device_path)
    if idx is None:
        idx = _usb_root_port_connector(device_path)

    if idx is not None:
        physical = _physical_port_from_typec_index(idx)
        if physical is not None:
            return physical

    sig = _usb_topology_signature(device_path)
    if sig:
        mapped = _load_port_map().get("usb_signatures", {}).get(sig)
        if mapped is not None:
            try:
                p = int(mapped)
                if p in (1, 2, 3, 4):
                    return p
            except (TypeError, ValueError):
                pass
    return None


def _typec_snapshot() -> set[int]:
    active = set()
    for p in _typec_ports():
        if _typec_has_partner(p):
            active.add(_natural_num(p))
    return active


def _connected_usb_candidates() -> list[tuple[str, Path, str]]:
    """
    Return useful externally connected USB devices as:
      (product, device_path, signature)

    Ignore root hubs and internal devices where possible.
    """
    result = []
    for dev in Path("/sys/bus/usb/devices").glob("*-*"):
        if ":" in dev.name:
            continue
        if not (dev / "idVendor").exists():
            continue

        product = read_text(dev / "product")
        manufacturer = read_text(dev / "manufacturer")
        sig = _usb_topology_signature(dev.resolve())
        if not sig:
            continue

        # Keep devices that look externally useful; ignore empty descriptors.
        label = product or manufacturer
        if not label:
            continue

        result.append((label.strip(), dev.resolve(), sig))
    return result



def _typec_state_snapshot() -> dict[int, dict[str, str | bool]]:
    """Snapshot the observable state of every Type-C connector."""
    snap: dict[int, dict[str, str | bool]] = {}
    for p in _typec_ports():
        idx = _natural_num(p)
        snap[idx] = {
            "partner": _typec_has_partner(p),
            "power": _selected_role(read_text(p / "power_role")) or "",
            "data": _selected_role(read_text(p / "data_role")) or "",
        }
    return snap


def _changed_typec_ports(
    before: dict[int, dict[str, str | bool]],
    after: dict[int, dict[str, str | bool]],
) -> list[int]:
    """Return connector indices whose observable state changed."""
    changed = []
    for idx in sorted(set(before) | set(after)):
        if before.get(idx) != after.get(idx):
            changed.append(idx)
    return changed


def calibrate_typec_interactive(port_number: int) -> tuple[bool, str]:
    """
    Learn the physical Framework slot -> Linux Type-C connector relation by
    watching a plug event.

    Usage:
        framefetch --calibrate-typec 3

    Procedure:
      - start with the target Framework slot EMPTY
      - press Enter
      - plug ANY USB-C partner into that slot
      - press Enter again

    A charger, monitor, phone, dock or USB-C data device can all work, because
    the calibration watches the Type-C partner/role state, not the USB payload.
    """
    if port_number not in (1, 2, 3, 4):
        return False, "La porta deve essere 1, 2, 3 o 4."

    print(
        f"\nCalibrazione Type-C fisica PORT {port_number}\n"
        "1) Lascia VUOTA quella specifica porta.\n"
        "2) Le altre porte possono rimanere come sono.\n"
    )
    input("Premi Invio quando la PORT è vuota... ")
    before = _typec_state_snapshot()

    print(
        "\nOra collega qualcosa USB-C proprio a quella PORT.\n"
        "Va bene monitor, caricatore, telefono, hub o altro dispositivo Type-C."
    )
    input("Premi Invio DOPO averlo collegato... ")
    # Give UCSI/typec a small moment to settle.
    time.sleep(0.7)
    after = _typec_state_snapshot()

    changed = _changed_typec_ports(before, after)

    # Strongest signal: partner False -> True.
    inserted = [
        idx for idx in changed
        if not bool(before.get(idx, {}).get("partner"))
        and bool(after.get(idx, {}).get("partner"))
    ]

    candidates = inserted or changed

    if len(candidates) != 1:
        detail = ", ".join(f"port{x}" for x in candidates) or "nessuna"
        return False, (
            "Non riesco a identificare un solo connettore Type-C cambiato. "
            f"Candidati: {detail}. Riprova evitando di collegare/scollegare altro "
            "durante la calibrazione."
        )

    idx = candidates[0]
    data = _load_port_map()

    # Remove stale reverse mappings: a Linux Type-C connector and a Framework
    # physical slot must each map one-to-one.
    tcmap = data.setdefault("typec_indices", {})
    for key, value in list(tcmap.items()):
        try:
            if int(value) == port_number or int(key) == idx:
                del tcmap[key]
        except (TypeError, ValueError):
            pass

    tcmap[str(idx)] = port_number
    _save_port_map(data)

    return True, f"Linux Type-C port{idx} = Framework PORT {port_number}"


def _framework_port_for_typec_index(idx: int) -> Optional[int]:
    """Use ONLY calibrated Type-C mapping when available."""
    data = _load_port_map()
    mapped = data.get("typec_indices", {}).get(str(idx))
    if mapped is None:
        return None
    try:
        p = int(mapped)
        return p if p in (1, 2, 3, 4) else None
    except (TypeError, ValueError):
        return None



def _external_usb_device_records() -> list[tuple[str, Path, str]]:
    result = []
    for dev in Path("/sys/bus/usb/devices").glob("*"):
        name = dev.name
        if not re.fullmatch(r"\d+-\d+(?:\.\d+)*", name):
            continue
        if not (dev / "idVendor").exists():
            continue
        try:
            real = dev.resolve()
        except OSError:
            real = dev
        product = read_text(dev / "product").strip()
        maker = read_text(dev / "manufacturer").strip()
        label = product or maker or name
        sig = _usb_topology_signature(real)
        if sig:
            result.append((label, real, sig))
    return result


def _usb_storage_device_records() -> list[tuple[str, Path, str]]:
    result = []
    data = lsblk_json()
    for top in data.get("blockdevices") or []:
        name = top.get("name") or ""
        if not name:
            continue
        raw = _block_device_sysfs_path(name)
        if raw is None:
            continue
        usb = _find_usb_ancestor(raw)
        if usb is None:
            continue
        sig = _usb_topology_signature(usb) or _usb_topology_signature(raw)
        if not sig:
            continue
        label = (
            (top.get("label") or "").strip()
            or (top.get("model") or "").strip()
            or read_text(usb / "product").strip()
            or name
        )
        result.append((label, usb, sig))
    return result


def _active_usb_network_records() -> list[tuple[str, Path, str]]:
    result = []
    out = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
    for line in out.splitlines():
        parts = line.split(":", 3)
        if len(parts) != 4:
            continue
        iface, typ, state, conn = parts
        if typ != "ethernet" or state != "connected":
            continue
        try:
            devpath = Path(f"/sys/class/net/{iface}/device").resolve()
        except OSError:
            continue
        if "/usb" not in str(devpath):
            continue
        usb = _find_usb_ancestor(devpath) or devpath
        sig = _usb_topology_signature(usb) or _usb_topology_signature(devpath)
        if not sig:
            continue
        label = _usb_product_for_iface(iface) or conn or iface
        result.append((label, usb, sig))
    return result


def _generic_calibration_candidates() -> list[tuple[str, Path, str]]:
    merged: dict[str, tuple[str, Path, str]] = {}
    for record in _external_usb_device_records():
        merged[record[2]] = record
    for record in _usb_storage_device_records():
        merged[record[2]] = record
    for record in _active_usb_network_records():
        merged[record[2]] = record
    return list(merged.values())


def _usb_signature_snapshot() -> dict[str, tuple[str, Path]]:
    """
    Snapshot currently visible external USB signatures.

    The key is the topological signature (e.g. 8:1).
    We merge generic USB devices, storage and active USB networking so the
    calibration works for SSDs, flash drives, phones, tethering, hubs, etc.
    """
    snap: dict[str, tuple[str, Path]] = {}

    for label, path, sig in _generic_calibration_candidates():
        snap[sig] = (label, path)

    return snap


def calibrate_usb_generic(port_number: int) -> tuple[bool, str]:
    """
    Interactive USB calibration by before/after difference.

    This intentionally does NOT care how many other USB devices are attached to
    the laptop.

    Workflow:
      1. disconnect the USB DATA device from the requested Framework port
      2. press Enter
      3. connect the device to that Framework port
      4. press Enter

    The newly appearing USB topology signature is learned for that physical
    Framework port.

    Appropriate probes:
      - SSD / HDD / flash drive
      - iPhone / iPad / Android USB data or tethering
      - USB hub/dock that enumerates over USB
      - DAC/audio interface
      - camera/webcam
      - keyboard/mouse/controller

    NOT appropriate:
      - pure DisplayPort Alt Mode cable
      - USB-C charger with no USB data function

    Those should use --calibrate-typec instead.
    """
    if port_number not in (1, 2, 3, 4):
        return False, "La porta deve essere 1, 2, 3 o 4."

    print(
        f"\nCalibrazione USB fisica PORT {port_number}\n"
        "--------------------------------------------------\n"
        "Scollega dalla PORT il dispositivo USB che vuoi usare come sonda.\n"
        "NON serve scollegare i dispositivi dalle altre porte.\n"
        "Monitor DP e caricatore possono rimanere collegati.\n"
    )
    input("Premi Invio quando la sonda USB è SCOLLEGATA... ")

    before = _usb_signature_snapshot()

    print(
        f"\nOra collega la sonda USB alla Framework PORT {port_number}.\n"
        "Attendi che venga riconosciuta/montata."
    )
    input("Premi Invio DOPO che il dispositivo è comparso nel sistema... ")

    # Let udev/block/network settle.
    time.sleep(1.0)
    after = _usb_signature_snapshot()

    before_sigs = set(before)
    after_sigs = set(after)

    appeared = sorted(after_sigs - before_sigs)
    disappeared = sorted(before_sigs - after_sigs)

    if len(appeared) == 0:
        return False, (
            "Non è comparsa nessuna nuova firma USB.\n"
            "Se stai calibrando un monitor DisplayPort o un caricatore, è normale: "
            f"usa `--calibrate-typec {port_number}`.\n"
            "Se invece è un SSD/telefono/chiavetta, verifica che sia realmente "
            "enumerato dal sistema e riprova."
        )

    if len(appeared) > 1:
        details = []
        for sig in appeared:
            label, path = after[sig]
            details.append(f"{sig}: {label!r} ({path})")

        return False, (
            "Sono comparse più firme USB contemporaneamente, quindi non posso "
            "sceglierne una senza rischiare di sbagliare:\n  "
            + "\n  ".join(details)
            + "\n\nSe hai collegato un HUB/dock, questo può essere normale. "
              "In quel caso servirà una calibrazione dell'intero gruppo/hub."
        )

    sig = appeared[0]
    label, _path = after[sig]

    data = _load_port_map()
    known = data.setdefault("usb_signatures", {})

    previous = known.get(sig)
    known[sig] = port_number
    _save_port_map(data)

    if previous is not None:
        try:
            previous_int = int(previous)
        except (TypeError, ValueError):
            previous_int = None

        if previous_int is not None and previous_int != port_number:
            return True, (
                f"USB {sig} ({label}) riassegnato "
                f"PORT {previous_int} -> PORT {port_number}"
            )

    extra = ""
    if disappeared:
        extra = f" | firme scomparse durante il test: {', '.join(disappeared)}"

    return True, f"USB {sig} ({label}) = Framework PORT {port_number}{extra}"


def _group_usb_signatures_by_port() -> dict[int, list[str]]:
    grouped = {1: [], 2: [], 3: [], 4: []}
    for sig, port in _load_port_map().get("usb_signatures", {}).items():
        try:
            p = int(port)
        except (TypeError, ValueError):
            continue
        if p in grouped:
            grouped[p].append(sig)
    for p in grouped:
        grouped[p].sort()
    return grouped


def calibrate_port(port_number: int) -> tuple[bool, str]:
    """
    One-step calibration.

    The user should connect a USB data device (phone/iPad/USB stick) ONLY to the
    requested Framework slot, then run:
        framefetch --calibrate-port N

    We prefer an active USB network interface (iPhone/iPad tethering), because it
    identifies the exact USB function currently being used. If unavailable, use
    the most recently active-looking external USB candidate.
    """
    if port_number not in (1, 2, 3, 4):
        return False, "La porta deve essere 1, 2, 3 o 4."

    candidates: list[tuple[str, Path]] = []

    # Best signal: active USB network/tether interface.
    out = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
    for line in out.splitlines():
        parts = line.split(":", 3)
        if len(parts) != 4:
            continue
        iface, typ, state, _conn = parts
        if typ != "ethernet" or state != "connected":
            continue
        try:
            p = Path(f"/sys/class/net/{iface}/device").resolve()
        except OSError:
            continue
        if "/usb" in str(p):
            candidates.append((_usb_product_for_iface(iface) or iface, p))

    # Generic fallback.
    if not candidates:
        generic = _connected_usb_candidates()
        if len(generic) == 1:
            label, p, _sig = generic[0]
            candidates.append((label, p))

    if not candidates:
        return False, (
            "Nessun dispositivo USB adatto rilevato. "
            "Collega iPhone/iPad con tethering o una periferica USB dati alla porta "
            f"{port_number} e riprova."
        )

    # If several active USB-network devices exist, calibration would be ambiguous.
    if len(candidates) > 1:
        labels = ", ".join(x[0] for x in candidates)
        return False, f"Calibrazione ambigua: vedo più dispositivi USB attivi: {labels}"

    label, devpath = candidates[0]
    sig = _usb_topology_signature(devpath)
    if not sig:
        return False, f"Non riesco a ricavare la topologia USB di {label}."

    data = _load_port_map()
    data.setdefault("usb_signatures", {})[sig] = port_number

    # Also learn the Type-C index if the kernel can pair this USB root port with
    # a Type-C connector.
    idx = _usb_device_typec_port_direct(devpath)
    if idx is None:
        idx = _usb_root_port_connector(devpath)
    if idx is not None:
        data.setdefault("typec_indices", {})[str(idx)] = port_number

    _save_port_map(data)

    extra = f", Type-C port{idx}" if idx is not None else ""
    return True, f"PORT {port_number} = USB {sig} ({label}{extra})"


def show_port_map() -> str:
    data = _load_port_map()
    grouped = _group_usb_signatures_by_port()

    lines = [
        f"Port map: {_port_map_path()}",
        "",
        "USB signatures by physical port:",
    ]

    for p in (1, 2, 3, 4):
        sigs = grouped[p]
        lines.append(f"  PORT {p}: {', '.join(sigs) if sigs else '(none learned)'}")

    lines.extend([
        "",
        f"Raw USB signatures: {data.get('usb_signatures', {})}",
        f"Type-C indices: {data.get('typec_indices', {})}",
        "",
        "Legacy display mappings (ignored by v7 placement):",
        f"  connectors: {data.get('display_connectors', {})}",
        f"  EDIDs: {data.get('display_edids', {})}",
    ])

    return "\n".join(lines)



def _partner_path_for_typec(port: Path) -> Optional[Path]:
    direct = Path("/sys/class/typec") / f"{port.name}-partner"
    if direct.exists():
        return direct
    nested = port / f"{port.name}-partner"
    if nested.exists():
        return nested
    return None


def _read_small_sysfs_tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    interesting_names = {
        "supports_usb_power_delivery", "usb_power_delivery_revision",
        "number_of_alternate_modes", "accessory_mode", "type", "usb_mode",
        "id_header", "cert_stat", "product", "product_type_vdo1",
        "product_type_vdo2", "product_type_vdo3", "svid", "vdo", "description",
    }
    values = {}
    try:
        paths = list(root.rglob("*"))
    except OSError:
        paths = []
    for p in paths:
        if not p.is_file() or p.name not in interesting_names:
            continue
        value = read_text(p)
        if not value:
            continue
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            rel = p.name
        rel = re.sub(r"port\d+", "portX", rel)
        values[rel] = value.strip()
    return values


def _typec_partner_fingerprint(port: Path) -> str:
    partner = _partner_path_for_typec(port)
    if partner is None:
        return ""
    values = _read_small_sysfs_tree(partner)
    if not values:
        return ""
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"))
    import hashlib
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _typec_partner_metadata(port: Path) -> dict[str, str]:
    partner = _partner_path_for_typec(port)
    return _read_small_sysfs_tree(partner) if partner else {}


def _normalize_svid(value: str) -> str:
    v = value.strip().lower()
    try:
        if v.startswith("0x"):
            return f"0x{int(v, 16):04x}"
        return f"0x{int(v, 0):04x}"
    except ValueError:
        return v


def _typec_has_dp_altmode(port: Path) -> bool:
    roots = [port]
    partner = _partner_path_for_typec(port)
    if partner is not None:
        roots.append(partner)
    for root in roots:
        try:
            svid_files = list(root.rglob("svid"))
        except OSError:
            svid_files = []
        for p in svid_files:
            if _normalize_svid(read_text(p)) == "0xff01":
                return True
    return False


def _typec_dp_active_robust(port: Path) -> bool:
    roots = [port]
    partner = _partner_path_for_typec(port)
    if partner is not None:
        roots.append(partner)
    for root in roots:
        try:
            svid_files = list(root.rglob("svid"))
        except OSError:
            svid_files = []
        for svid_file in svid_files:
            if _normalize_svid(read_text(svid_file)) != "0xff01":
                continue
            try:
                active_files = list(svid_file.parent.rglob("active"))
            except OSError:
                active_files = []
            for active in active_files:
                if read_text(active).strip().lower() in ("1", "yes", "true", "active"):
                    return True
    try:
        return _typec_dp_active(port)
    except Exception:
        return False


def _saved_display_partner_fingerprints() -> set[str]:
    vals = _load_port_map().get("display_partner_fingerprints", [])
    return {str(x) for x in vals if x}


def calibrate_dp_partner(port_number: int) -> tuple[bool, str]:
    if port_number not in (1, 2, 3, 4):
        return False, "La porta deve essere 1, 2, 3 o 4."
    target_tc = None
    target_idx = None
    for tc in _typec_ports():
        idx = _natural_num(tc)
        if _framework_port_for_typec_index(idx) == port_number:
            target_tc, target_idx = tc, idx
            break
    if target_tc is None:
        return False, f"PORT {port_number} non calibrata Type-C. Esegui --calibrate-typec {port_number}."
    if not _typec_has_partner(target_tc):
        return False, f"Nessun partner Type-C collegato alla PORT {port_number}."
    external = [d for d in displays() if not d.builtin]
    if not external:
        return False, "DRM/KScreen non vede alcun display esterno."
    fp = _typec_partner_fingerprint(target_tc)
    if not fp:
        return False, "Il firmware non espone abbastanza metadata del partner; usa --diagnostics e inviami TYPE-C PARTNER DETAILS."
    data = _load_port_map()
    fps = set(data.setdefault("display_partner_fingerprints", []))
    fps.add(fp)
    data["display_partner_fingerprints"] = sorted(fps)
    _save_port_map(data)
    return True, f"Partner display appreso: PORT {port_number}, typec port{target_idx}, fingerprint={fp}, dp-altmode={_typec_has_dp_altmode(target_tc)}"


def clear_dp_partner_calibration() -> tuple[bool, str]:
    data = _load_port_map()
    data["display_partner_fingerprints"] = []
    _save_port_map(data)
    return True, "Fingerprint partner DisplayPort rimossi."

def _typec_port_number_from_target(target: Path | str) -> Optional[int]:
    s = str(target)
    m = re.search(r"/port(\d+)(?:-partner(?:\.\d+)?)?(?:/|$)", s)
    if not m:
        m = re.search(r"\bport(\d+)(?:-partner(?:\.\d+)?)?\b", s)
    return int(m.group(1)) if m else None


def _usb_device_typec_port_direct(device_path: Path) -> Optional[int]:
    """Direct kernel USB-device -> Type-C partner relation, when firmware exposes it."""
    candidates = [device_path, *device_path.parents]
    for p in candidates:
        link = p / "typec"
        if link.is_symlink():
            try:
                n = _typec_port_number_from_target(link.resolve())
                if n is not None:
                    return n
            except OSError:
                pass
    return None


def _usb_device_typec_port(device_path: Path) -> Optional[int]:
    """Compatibility wrapper used by diagnostics."""
    direct = _usb_device_typec_port_direct(device_path)
    if direct is not None:
        return direct
    return _usb_root_port_connector(device_path)


def _physical_port_from_typec_index(typec_index: int) -> Optional[int]:
    """
    Physical Type-C mapping.

    Once ANY Type-C calibration exists, never fall back to index+1: doing so is
    exactly what caused DP to appear on PORT 1 despite being physically elsewhere.
    """
    data = _load_port_map()
    tcmap = data.get("typec_indices", {})

    mapped = tcmap.get(str(typec_index))
    if mapped is not None:
        try:
            p = int(mapped)
            if p in (1, 2, 3, 4):
                return p
        except (TypeError, ValueError):
            pass

    if tcmap:
        return None

    # Legacy fallback only before physical Type-C calibration exists.
    fallback = typec_index + 1
    return fallback if fallback in (1, 2, 3, 4) else None


def _usb_product_for_iface(iface: str) -> str:
    try:
        p = Path(f"/sys/class/net/{iface}/device").resolve()
    except OSError:
        return ""
    for parent in [p, *p.parents]:
        product = read_text(parent / "product")
        if product:
            return product
    return ""


def _usb_networks(nmcli_out: Optional[str] = None) -> list[tuple[str, str, float, float, Optional[int], str]]:
    """
    Return:
      (iface, product, rx, tx, physical_port, usb_signature)
    """
    result = []
    out = nmcli_out if nmcli_out is not None else run(
        ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"]
    )
    for line in out.splitlines():
        parts = line.split(":", 3)
        if len(parts) != 4:
            continue
        iface, typ, state, conn = parts
        if state != "connected" or typ != "ethernet":
            continue

        try:
            devpath = Path(f"/sys/class/net/{iface}/device").resolve()
        except OSError:
            continue
        if "/usb" not in str(devpath):
            continue

        rx, tx = _iface_sample(iface)
        product = _usb_product_for_iface(iface) or conn or "USB Ethernet"
        physical = _mapped_framework_port_for_usb(devpath)
        sig = _usb_topology_signature(devpath) or "?"
        result.append((iface, product, rx, tx, physical, sig))
    return result


def _port_card_fallback() -> str:
    # Passive USB-C Expansion Cards do not enumerate. Project rule: if no other
    # Framework card can be identified, show USB-C Card rather than EMPTY.
    return "USB-C Card"



def _storage_bar_line(disk: Disk, width: int = 8) -> str:
    bar = percent_bar(disk.pct, width)
    return f"{bar} {disk.pct:.0f}%"


def _storage_free_text(disk: Disk) -> str:
    # User preference: immediate free space, not theoretical bus information.
    free_gib = disk.free / (1024 ** 3)
    if free_gib >= 1024:
        return f"{free_gib / 1024:.1f} TiB free"
    if free_gib >= 100:
        return f"{free_gib:.0f} GiB free"
    return f"{free_gib:.1f} GiB free"


def _short_storage_name(name: str, width: int = PORT_WIDTH) -> str:
    name = name.strip() or "Storage"
    if len(name) <= width:
        return name
    return name[: max(1, width - 1)] + "…"



def _typec_power_output_text(port: Path) -> Optional[str]:
    if not _typec_has_partner(port):
        return None
    if _selected_role(read_text(port / "power_role")) != "source":
        return None
    mode = read_text(port / "power_operation_mode").strip().lower()
    if mode == "usb_power_delivery":
        return "PD out"
    if mode == "3.0a":
        return "PWR out 3A"
    if mode == "1.5a":
        return "PWR out 1.5A"
    return "PWR out"

def autodetect_ports(
    displays_found: list[Display],
    external_disks_found: Optional[list[Disk]] = None,
    nmcli_out: Optional[str] = None,
) -> dict[int, list[str]]:
    result = {i: [_port_card_fallback()] for i in range(1, 5)}
    occupied_by_known_service: set[int] = set()
    if external_disks_found is None:
        external_disks_found = []

    for disk in external_disks_found:
        physical = disk.physical_port
        if physical not in (1, 2, 3, 4):
            continue
        result[physical].append(_short_storage_name(disk.display_name))
        result[physical].append(_storage_bar_line(disk))
        result[physical].append(_storage_free_text(disk))
        occupied_by_known_service.add(physical)

    for iface, product, rx, tx, physical, _sig in _usb_networks(nmcli_out):
        if physical is None:
            continue
        clean_product = product.strip()
        if clean_product and clean_product.lower() not in ("usb ethernet", "wired connection 1", "wired connection"):
            result[physical].append(clean_product)
        result[physical].append(f"{ansi('ETH',MAGENTA)} ↓{compact_rate(rx)} ↑{compact_rate(tx)}")
        occupied_by_known_service.add(physical)

    active_typec = {}
    for tc in _typec_ports():
        idx = _natural_num(tc)
        physical = _framework_port_for_typec_index(idx)
        if physical is not None and _typec_has_partner(tc):
            active_typec[physical] = tc

    if _external_power_online():
        watts = _online_external_power_watts()
        sinks = [p for p,tc in active_typec.items() if _selected_role(read_text(tc / "power_role")) == "sink"]
        if len(sinks) == 1:
            physical = sinks[0]
            result[physical].append("Charger")
            result[physical].append(f"{ansi('PD charging',GOLD)} {watts:.0f}W" if watts else ansi('PD charging',GOLD))
            occupied_by_known_service.add(physical)

    external = [d for d in displays_found if not d.builtin]
    unresolved = list(external)

    if unresolved:
        # A) Most stable signal on this machine:
        # current DRM connector -> calibrated physical Framework port.
        #
        # The same monitor changes DP connector when physically moved, so this
        # follows the cable instead of pinning the monitor/EDID to one slot.
        connector_map = _load_port_map().get("display_connectors", {})
        still_unresolved = []

        for d in unresolved:
            physical = None
            mapped = connector_map.get(d.name)
            if mapped is not None:
                try:
                    p = int(mapped)
                    if p in (1, 2, 3, 4):
                        physical = p
                except (TypeError, ValueError):
                    pass

            if physical is None:
                still_unresolved.append(d)
                continue

            hz = f" {round(d.hz)}Hz" if d.hz else ""
            result[physical].append(f"{ansi('DP',GOLD)} {d.width}x{d.height}{hz}")
            occupied_by_known_service.add(physical)

        unresolved = still_unresolved

        # B) Learned Type-C partner fingerprint fallback.
        if unresolved:
            saved_fps = _saved_display_partner_fingerprints()
            fp_candidates = []

            if saved_fps:
                for physical, tc in active_typec.items():
                    if physical in occupied_by_known_service:
                        continue
                    fp = _typec_partner_fingerprint(tc)
                    if fp and fp in saved_fps:
                        fp_candidates.append(physical)

            if len(fp_candidates) == 1:
                d = unresolved.pop(0)
                physical = fp_candidates[0]
                hz = f" {round(d.hz)}Hz" if d.hz else ""
                result[physical].append(f"{ansi('DP',GOLD)} {d.width}x{d.height}{hz}")
                occupied_by_known_service.add(physical)

        # C) Active DP alt-mode.
        if unresolved:
            candidates = [
                p for p, tc in active_typec.items()
                if p not in occupied_by_known_service and _typec_dp_active_robust(tc)
            ]
            if len(candidates) == len(unresolved) and candidates:
                for d, physical in zip(unresolved, candidates):
                    hz = f" {round(d.hz)}Hz" if d.hz else ""
                    result[physical].append(f"{ansi('DP',GOLD)} {d.width}x{d.height}{hz}")
                    occupied_by_known_service.add(physical)
                unresolved = []

        # D) DP SVID/capability.
        if unresolved:
            candidates = [
                p for p, tc in active_typec.items()
                if p not in occupied_by_known_service and _typec_has_dp_altmode(tc)
            ]
            if len(candidates) == len(unresolved) and candidates:
                for d, physical in zip(unresolved, candidates):
                    hz = f" {round(d.hz)}Hz" if d.hz else ""
                    result[physical].append(f"{ansi('DP',GOLD)} {d.width}x{d.height}{hz}")
                    occupied_by_known_service.add(physical)
                unresolved = []

        # E) Last conservative inference.
        if len(unresolved) == 1:
            candidates = [
                p for p in active_typec
                if p not in occupied_by_known_service
            ]
            if len(candidates) == 1:
                d = unresolved[0]
                physical = candidates[0]
                hz = f" {round(d.hz)}Hz" if d.hz else ""
                result[physical].append(f"{ansi('DP',GOLD)} {d.width}x{d.height}{hz}")
                occupied_by_known_service.add(physical)

    for physical,tc in active_typec.items():
        out_text=_typec_power_output_text(tc)
        if out_text:
            result[physical].append(out_text)

    return result


# =============================================================================
# SNAPSHOT
# =============================================================================

@dataclass
class Snapshot:
    cpu_model: str
    cpu_usage: float
    cpu_freq: Optional[float]
    cpu_temp: Optional[float]

    gpu_model: str
    gpu_usage: Optional[float]
    gpu_freq: Optional[float]

    ram_total: int
    ram_used: int
    ram_pct: float
    sodimms: list[Sodimm]

    root_disk: Optional[Disk]
    external_disks: list[Disk]

    battery: Optional[Battery]
    peripheral_batteries: list[Battery]

    network: Network
    bt_devices: list[str]
    displays: list[Display]

    fan_rpm: Optional[int]
    fan_percent: Optional[float]
    fan_max_rpm: Optional[int]
    ports: dict[int, list[str]]


def collect() -> Snapshot:
    """
    Collect independent hardware data concurrently.

    Important:
    - render order/output is unchanged
    - CPU/network sampling sleeps overlap instead of stacking
    - one lsblk snapshot is shared by root + external disks
    - one nmcli snapshot is shared by network + USB Ethernet placement
    - one bluetoothctl Connected query is shared by BT names + battery lookup
    """

    total, used, rpct = ram_usage()

    # Cheap/static snapshots first.
    lsblk_data = lsblk_json()
    nmcli_out = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
    bt_records = _bluetooth_connected_records()

    # Independent collectors. Threading helps here because most work is I/O,
    # subprocess waiting, sysfs reading, or intentional sampling sleeps.
    with ThreadPoolExecutor(max_workers=12) as ex:
        f_displays = ex.submit(displays)
        f_external = ex.submit(external_mounted_disks, lsblk_data)
        f_fan = ex.submit(fan_reading)

        f_cpu_model = ex.submit(cpu_model)
        f_cpu_usage = ex.submit(cpu_usage)
        f_cpu_freq = ex.submit(cpu_freq_ghz)
        f_cpu_temp = ex.submit(cpu_temp)

        f_gpu_model = ex.submit(gpu_model)
        f_gpu_usage = ex.submit(gpu_usage)
        f_gpu_freq = ex.submit(gpu_freq_ghz)

        f_sodimms = ex.submit(sodimms)
        f_root = ex.submit(root_disk, lsblk_data)

        f_battery = ex.submit(laptop_battery)
        f_peripheral = ex.submit(peripheral_batteries, bt_records)
        f_network = ex.submit(active_network, nmcli_out)

        ds = f_displays.result()
        external = f_external.result()
        frpm, fpct, fmax = f_fan.result()

        # Port detection depends on displays/external disks, but it can still
        # overlap with the remaining independent collectors.
        f_ports = ex.submit(autodetect_ports, ds, external, nmcli_out)

        return Snapshot(
            cpu_model=f_cpu_model.result(),
            cpu_usage=f_cpu_usage.result(),
            cpu_freq=f_cpu_freq.result(),
            cpu_temp=f_cpu_temp.result(),

            gpu_model=f_gpu_model.result(),
            gpu_usage=f_gpu_usage.result(),
            gpu_freq=f_gpu_freq.result(),

            ram_total=total,
            ram_used=used,
            ram_pct=rpct,
            sodimms=f_sodimms.result(),

            root_disk=f_root.result(),
            external_disks=external,

            battery=f_battery.result(),
            peripheral_batteries=f_peripheral.result(),

            network=f_network.result(),
            bt_devices=bluetooth_devices(bt_records),
            displays=ds,

            fan_rpm=frpm,
            fan_percent=fpct,
            fan_max_rpm=fmax,
            ports=f_ports.result(),
        )


# =============================================================================
# ASCII RENDERER
# =============================================================================

FW_LOGO = [
    "       .%%+ .  =%%..   ",
    "     %%%%%%%%%%%%%%%%.  ",
    "     %%%%%%... %%%%%%  ",
    "    .%%%          %%%.. ",
    "  %%%%+.           =%%%@. ",
    " %%%%%              %%%%%. ",
    " @%%%%             .%%%%@. ",
    "  %%%%=           -%%%%. ",
    "     %%@.        .@%%. ",
    "     %%%%%*....+%%%%%  ",
    "     %%%%%%%%%%%%%%%%  ",
    "      .-%%%    #%@-    ",
]


# FW_LOGO = [
#            .%%+ .  =%%..
#          %%%%%%%%%%%%%%%%.
#          %%%%%%... %%%%%%
#         .%%%          %%%..
#       %%%%+.           =%%%@.
#      %%%%%              %%%%%.
#      @%%%%             .%%%%@.
#       %%%%=            -%%%%
#          %%@.        .@%%.
#          %%%%%*....+%%%%%
#          %%%%%%%%%%%%%%%%
#           .-%%%    #%@-


def colored_percent(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return ansi(f"{value:.0f}%", pct_color(value))


def colored_temp_value(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return ansi(f"{value:.0f} °C", temp_color(value))


def color_sodimm_value(line: str, kind: str) -> str:
    if kind == "size" and line and line != "EMPTY":
        return ansi(line, CYAN)

    if kind == "speed":
        m = re.match(r"^(.*?)(\d{3,5})(.*)$", line)
        if m:
            return f"{m.group(1)}{ansi(m.group(2), CYAN)}{m.group(3)}"

    return line


def nvme_free_text(disk: Optional[Disk]) -> str:
    if disk is None:
        return ""
    free_gib = disk.free / (1024 ** 3)
    if free_gib >= 1024:
        return f"{free_gib / 1024:.1f} TiB free"
    if free_gib >= 100:
        return f"{free_gib:.0f} GiB free"
    return f"{free_gib:.1f} GiB free"


def nvme_footer_line(disk: Optional[Disk], width: int = 39) -> str:
    left = "  PCIe 4.0 x4"
    right = nvme_free_text(disk)
    if not right:
        return fit(left, width)
    spaces = max(1, width - len(left) - len(right))
    return fit(left + (" " * spaces) + right, width)


def sodimm_lines(slot: Sodimm | None, idx: int) -> list[str]:
    if slot is None:
        return [
            f"SODIMM {idx}",
            "",
            "UNKNOWN",
            "",
            "DDR5",
            "",
            "",
            "",
        ]

    if slot.size == "EMPTY":
        return [
            f"SODIMM {idx}",
            "",
            "EMPTY",
            "",
            slot.kind or "DDR5",
            "",
            "",
            "",
        ]

    maker = slot.maker.strip()
    part = slot.part.strip()
    speed = f"{slot.kind}-{slot.speed}" if slot.speed else slot.kind

    return [
        f"SODIMM {idx}",
        "",
        slot.size,
        "",
        speed,
        "",
        maker,
        part,
    ]


def built_in_display_line(s: Snapshot) -> tuple[str, str]:
    d = next((x for x in s.displays if x.builtin), None)
    if not d:
        return "Display unavailable", '13"'
    hz = f",{round(d.hz)} Hz" if d.hz else ""
    return f"{d.width}x{d.height}{hz}", '[Built-in],13"'


def render(s: Snapshot, logo_color: Optional[str] = None) -> str:
    if logo_color is None:
        logo_color = LOGO

    # ---------- colors / dynamic strings ----------
    rpct = s.ram_pct
    ram_bar_plain = percent_bar(rpct, RAM_BAR_WIDTH)
    ram_bar = ansi(ram_bar_plain, pct_color(rpct))
    ram_pct = ansi(f"{rpct:.0f}%", pct_color(rpct))

    temp = s.cpu_temp
    temp_txt = f"TEMP {colored_temp_value(temp)}"

    fan_txt = f"FAN: {s.fan_percent:.0f}%" if s.fan_percent is not None else "FAN: N/A"

    cpu_freq = f"{s.cpu_freq:.1f}GHz" if s.cpu_freq is not None else ""
    gpu_freq = f"{s.gpu_freq:.1f}GHz" if s.gpu_freq is not None else ""
    cpu_pct = colored_percent(s.cpu_usage)
    gpu_pct = colored_percent(s.gpu_usage)

    dline1, dline2 = built_in_display_line(s)

    # ---------- RAM modules ----------
    raw_slots = list(s.sodimms)
    while len(raw_slots) < 2:
        raw_slots.append(None)

    # Framework physical drawing order. The machine's SMBIOS enumeration is
    # reversed relative to the physical slot numbering verified by the user.
    slots = []
    for dmi_index in FRAMEWORK_SODIMM_DMI_ORDER:
        slots.append(raw_slots[dmi_index] if dmi_index < len(raw_slots) else None)

    sd0 = sodimm_lines(slots[0], 0)
    sd1 = sodimm_lines(slots[1], 1)

    sd0[2] = color_sodimm_value(sd0[2], "size")
    sd1[2] = color_sodimm_value(sd1[2], "size")
    sd0[4] = color_sodimm_value(sd0[4], "speed")
    sd1[4] = color_sodimm_value(sd1[4], "speed")

    # ---------- SSD ----------
    disk = s.root_disk
    if disk:
        dpct = disk.pct
        dbar = ansi(percent_bar(dpct, SSD_BAR_WIDTH), pct_color(dpct))
        dpct_txt = ansi(f"{dpct:.0f}%", pct_color(dpct))
        disk_title = f"NVMe SSD     {disk.model}"
        disk_usage = f"{tib_or_gib(disk.used)} / {tib_or_gib(disk.total)}"
    else:
        dbar = "░" * SSD_BAR_WIDTH
        dpct_txt = "N/A"
        disk_title = "NVMe SSD     unknown"
        disk_usage = "N/A"

    # Fixed 39-column inner width for the NVMe box.
    disk_usage_visible = f"{strip_ansi(dbar)}  {strip_ansi(dpct_txt):<4}  {disk_usage}"
    disk_footer = nvme_footer_line(disk, 39)

    # ---------- Battery ----------
    b = s.battery
    if b and b.pct is not None:
        bcap = f"{b.design_wh:.0f} Wh" if b.design_wh else "Battery"
        bpct = b.pct
        bbar = ansi(percent_bar(bpct, BATTERY_BAR_WIDTH), battery_color(bpct))
        bpct_txt = ansi(f"{bpct:.0f}%", battery_color(bpct))
        bstatus = (b.status or "").upper()
        power = f" · {b.power_w:.1f}W" if b.power_w is not None and b.power_w > 0.05 else ""
        battery_header = f"BATTERY · {bcap}"
        battery_state = f"{bpct_txt} · {bstatus}{power}"
    else:
        bbar = "░" * BATTERY_BAR_WIDTH
        battery_header = "BATTERY"
        battery_state = "N/A"

    # Peripheral battery lines.
    pb = []
    for x in s.peripheral_batteries[:4]:
        if x.pct is None:
            continue
        pbar = ansi(percent_bar(x.pct, PERIPHERAL_BATTERY_BAR_WIDTH), battery_color(x.pct))
        ppct = ansi(f"{x.pct:.0f}%", battery_color(x.pct))
        pb.append(f"{fit(x.name, 16)} {pbar} {ppct}")
    while len(pb) < 2:
        pb.append("")

    # ---------- Network / BT ----------
    net_name_plain = s.network.ssid or s.network.iface or "Disconnected"
    net_name = ansi(net_name_plain, CYAN)
    rx = compact_rate(s.network.rx)
    tx = compact_rate(s.network.tx)
    net_rate = f"↓ {rx} ↑ {tx}bit/s" if s.network.iface else ""

    bt = list(s.bt_devices[:4])
    while len(bt) < 2:
        bt.append("")

    # ---------- Ports ----------
    p = {}
    for n in range(1, 5):
        detected = list(s.ports.get(n, []))
        detected.extend(PORT_OVERRIDES.get(n, []))
        p[n] = render_port_lines(PHYSICAL_PORT_TYPE[n], detected, max_lines=6)

    # ---------- System labels ----------
    cpu_name = s.cpu_model
    # Keep the characteristic model name visible in the small box.
    cpu_name = cpu_name.replace("AMD Ryzen ", "Ryzen ")
    gpu_name = s.gpu_model.replace("AMD/ATI ", "")
    gpu_name = re.sub(r"\s+\[[^\]]+\]", "", gpu_name)

    # The original template is fixed-width.  We render line-by-line instead of
    # using str.format so ANSI sequences do not alter spacing calculations.

    lines: list[str] = []

    # Helper for a port row.
    def portline(n: int, i: int) -> str:
        return p[n][i]

    # The original template is fixed-width. We render line-by-line instead of
    # using str.format so ANSI sequences do not alter spacing calculations.

    lines: list[str] = []

    # Helper for a port row.
    def portline(n: int, i: int) -> str:
        return p[n][i]

    # -------------------------------------------------------------------------
    # LEFT SIDE LAYOUT
    # -------------------------------------------------------------------------
    # A complete Framework port box is 17 visible characters wide:
    #
    # ┌────PORT─1─────┐
    #
    # LEFT_BOARD_GAP controls only the empty space between that column
    # and the motherboard.
    LEFT_PORT_WIDTH = 17

    left_gap = " " * LEFT_BOARD_GAP

    # Used on rows where there is no PORT box but we still need to keep
    # the motherboard aligned with the rows where a PORT box exists.
    left_empty = " " * LEFT_PORT_WIDTH + left_gap

    # -------------------------------------------------------------------------
    # TOP / MOTHERBOARD
    # -------------------------------------------------------------------------

    lines.append(
        "┌────PORT─1─────┐"
        f"{left_gap}"
        "┌────────────────────────────────────────────────────────────────────────────────────────────────────┐ "
        "┌───PORT─3──────┐"
    )

    lines.append(
        f"│{portline(1,0)}│"
        f"{left_gap}│"
        f"         ┌───────────────────────────────────┐   "
        f"┌────────────────┐  "
        f"RAM  {ram_bar}  {ram_pct:>4}  "
        f"│ │{portline(3,0)}│"
    )

    lines.append(
        f"│{portline(1,1)}│"
        f"{left_gap}│"
        f"         │ {fit(temp_txt, 19)} {fit(fan_txt, 14, 'right')}│   "
        f"│{fit(dline1,16)}│    "
        f"┌──────────┐  ┌──────────┐   "
        f"│ │{portline(3,1)}│"
    )

    lines.append(
        f"│{portline(1,2)}│"
        f"{left_gap}│"
        f"         │{ansi(shift_ascii_line(FW_LOGO[0], LOGO_SHIFT_RIGHT, 35), logo_color)}│   "
        f"│{fit(dline2,16)}│    "
        f"│ {fit(sd0[0],8)} │  │ {fit(sd1[0],8)} │   "
        f"│ │{portline(3,2)}│"
    )

    lines.append(
        f"│{portline(1,3)}│"
        f"{left_gap}│"
        f"         │{ansi(shift_ascii_line(FW_LOGO[1], LOGO_SHIFT_RIGHT, 35), logo_color)}│   "
        f"└────────────────┘    "
        f"│{fit(sd0[1],10)}│  │{fit(sd1[1],10)}│   "
        f"│ │{portline(3,3)}│"
    )

    lines.append(
        f"│{portline(1,4)}│"
        f"{left_gap}│"
        f"         │{ansi(shift_ascii_line(FW_LOGO[2], LOGO_SHIFT_RIGHT, 35), logo_color)}│                         "
        f"│ {fit(sd0[2],8)} │  │ {fit(sd1[2],8)} │   "
        f"│ │{portline(3,4)}│"
    )

    lines.append(
        f"│{portline(1,5)}│"
        f"{left_gap}│"
        f"         │{ansi(shift_ascii_line(FW_LOGO[3], LOGO_SHIFT_RIGHT, 35), logo_color)}│   "
        f"┌─────────────────┐   "
        f"│{fit(sd0[3],10)}│  │{fit(sd1[3],10)}│   "
        f"│ │{portline(3,5)}│"
    )

    lines.append(
        "└───────────────┘"
        f"{left_gap}│"
        f"         │{ansi(shift_ascii_line(FW_LOGO[4], LOGO_SHIFT_RIGHT, 35), logo_color)}│   "
        f"│ {fit(cpu_name,15)} │   "
        f"│{fit(sd0[4],10)}│  │{fit(sd1[4],10)}│   "
        "│ └───────────────┘"
    )

    lines.append(
        f"{left_empty}│"
        f"         │{ansi(shift_ascii_line(FW_LOGO[5], LOGO_SHIFT_RIGHT, 35), logo_color)}│   "
        f"│ {fit('',15)} │   "
        f"│{fit(sd0[5],10)}│  │{fit(sd1[5],10)}│   │"
    )

    # -------------------------------------------------------------------------
    # PORT 2 / PORT 4
    # -------------------------------------------------------------------------

    lines.append(
        "┌────PORT─2─────┐"
        f"{left_gap}│"
        f"         │{ansi(shift_ascii_line(FW_LOGO[6], LOGO_SHIFT_RIGHT, 35), logo_color)}│   "
        f"│ {fit(f'CPU {cpu_pct} {cpu_freq}',15)} │   "
        f"│{fit(sd0[6],10)}│  │{fit(sd1[6],10)}│   │ "
        "┌────PORT─4─────┐"
    )

    lines.append(
        f"│{portline(2,0)}│"
        f"{left_gap}│"
        f"         │{ansi(shift_ascii_line(FW_LOGO[7], LOGO_SHIFT_RIGHT, 35), logo_color)}│   "
        f"│{fit('',17)}│   "
        f"│{fit(sd0[7],10)}│  │{fit(sd1[7],10)}│   │ "
        f"│{portline(4,0)}│"
    )

    lines.append(
        f"│{portline(2,1)}│"
        f"{left_gap}│"
        f"         │{ansi(shift_ascii_line(FW_LOGO[8], LOGO_SHIFT_RIGHT, 35), logo_color)}│   "
        f"│ {fit(gpu_name,15)} │   "
        f"│{fit('',10)}│  │{fit('',10)}│   │ "
        f"│{portline(4,1)}│"
    )

    lines.append(
        f"│{portline(2,2)}│"
        f"{left_gap}│"
        f"         │{ansi(shift_ascii_line(FW_LOGO[9], LOGO_SHIFT_RIGHT, 35), logo_color)}│   "
        f"│ {fit(f'GPU {gpu_pct} {gpu_freq}',15)} │   "
        f"│{fit('',10)}│  │{fit('',10)}│   │ "
        f"│{portline(4,2)}│"
    )

    lines.append(
        f"│{portline(2,3)}│"
        f"{left_gap}│"
        f"         │{ansi(shift_ascii_line(FW_LOGO[10], LOGO_SHIFT_RIGHT, 35), logo_color)}│   "
        f"└─────────────────┘   "
        f"│{fit('',10)}│  │{fit('',10)}│   │ "
        f"│{portline(4,3)}│"
    )

    lines.append(
        f"│{portline(2,4)}│"
        f"{left_gap}│"
        f"         │{ansi(shift_ascii_line(FW_LOGO[11], LOGO_SHIFT_RIGHT, 35), logo_color)}│                         "
        f"└──────────┘  └──────────┘   │ "
        f"│{portline(4,4)}│"
    )

    lines.append(
        f"│{portline(2,5)}│"
        f"{left_gap}│"
        "         └───────────────────────────────────┘"
        "                                                      │ "
        f"│{portline(4,5)}│"
    )

    lines.append(
        "└───────────────┘"
        f"{left_gap}│"
        "                                                                                                    │ "
        "└───────────────┘"
    )

    # -------------------------------------------------------------------------
    # LOWER MOTHERBOARD / STORAGE / BATTERY / NETWORK
    # -------------------------------------------------------------------------

    lines.append(
        f"{left_empty}│             "
        "┌───────────────────────────────────────┐"
        "                                              │"
    )

    lines.append(
        f"{left_empty}│             "
        f"│ {fit(disk_title,38)}│"
        "                                              │"
    )

    lines.append(
        f"{left_empty}│             "
        f"│{dbar}  {fit(dpct_txt,4)}  {fit(disk_usage,17)}│  "
        "┌──────────────────────────────────────┐    │"
        "┌───────────────────┐"
    )

    lines.append(
        f"{left_empty}│             "
        f"│{disk_footer}│  "
        f"│{fit(battery_header,38)}│    │"
        f"│{fit('Wi-Fi / Bluetooth',19)}│"
    )

    lines.append(
        f"{left_empty}│             "
        "└───────────────────────────────────────┘  "
        f"│{fit(bbar,38)}│    │"
        f"│{fit(net_name,19)}│"
    )

    lines.append(
        f"{left_empty}│                                                        "
        f"│{fit(battery_state,38)}│    │"
        f"│{fit(net_rate,19)}│"
    )

    lines.append(
        f"{left_empty}│                                                        "
        f"│{fit('BATTERIES',38)}│    │"
        f"│{fit(ansi('BT:', CYAN) + (' ' + bt[0] if bt[0] else ''),19)}│"
    )

    lines.append(
        f"{left_empty}└────────────────────────────────────────────────────────"
        f"│{fit(pb[0],38)}│────┘│"
        f"{fit('    ' + bt[1] if bt[1] else '',19)}│"
    )

    # These final two lines begin underneath the motherboard rather than
    # underneath the left-side port column, so preserve their existing
    # motherboard alignment based on the configurable left offset.
    lower_indent = " " * (LEFT_PORT_WIDTH + LEFT_BOARD_GAP + 57)

    lines.append(
        f"{lower_indent}"
        f"│{fit(pb[1],38)}│     "
        "└───────────────────┘"
    )

    lines.append(
        f"{lower_indent}"
        "└──────────────────────────────────────┘"
    )

    return "\n".join(lines)


# =============================================================================
# LIVE UPDATE (CACHED SNAPSHOT + FULL REDRAW)
# =============================================================================

WATCH_LOGO_COLORS = [CYAN, YELLOW, GOLD, RED, GREEN]


def refresh_dynamic_snapshot(base: Snapshot) -> Snapshot:
    """
    Refresh the values intended to be live in --watch mode.

    Cached from the initial collect():
    - hardware models
    - SODIMM information
    - displays
    - other effectively static snapshot fields

    Refreshed every watch cycle:
    - RAM usage
    - CPU usage + frequency + temperature
    - GPU usage + frequency
    - fan RPM / percentage
    - active network name/interface + RX/TX
    - internal SSD filesystem usage
    - mounted external storage list + usage
    - laptop battery percentage/state/power
    - peripheral battery list + percentages
    - connected Bluetooth device list
    - Framework port contents, using the fresh external-storage/network state
    """
    total, used, rpct = ram_usage()

    # Shared snapshots: each command is run once per watch cycle and reused by
    # all collectors that need it.
    lsblk_data = lsblk_json()
    nmcli_out = run([
        "nmcli", "-t", "-f",
        "DEVICE,TYPE,STATE,CONNECTION",
        "device"
    ])
    bt_records = _bluetooth_connected_records()

    with ThreadPoolExecutor(max_workers=12) as ex:
        f_cpu_usage = ex.submit(cpu_usage)
        f_cpu_freq = ex.submit(cpu_freq_ghz)
        f_cpu_temp = ex.submit(cpu_temp)
        f_gpu_usage = ex.submit(gpu_usage)
        f_gpu_freq = ex.submit(gpu_freq_ghz)
        f_fan = ex.submit(fan_reading)

        f_network = ex.submit(active_network, nmcli_out)
        f_root = ex.submit(root_disk, lsblk_data)
        f_external = ex.submit(external_mounted_disks, lsblk_data)
        f_battery = ex.submit(laptop_battery)
        f_peripheral = ex.submit(peripheral_batteries, bt_records)

        frpm, fpct, fmax = f_fan.result()
        external = f_external.result()

        # Port rendering depends on the current external-storage list, so submit
        # it after storage discovery while the remaining collectors are still
        # allowed to finish in parallel.
        f_ports = ex.submit(
            autodetect_ports,
            base.displays,
            external,
            nmcli_out,
        )

        return replace(
            base,
            cpu_usage=f_cpu_usage.result(),
            cpu_freq=f_cpu_freq.result(),
            cpu_temp=f_cpu_temp.result(),
            gpu_usage=f_gpu_usage.result(),
            gpu_freq=f_gpu_freq.result(),

            fan_rpm=frpm,
            fan_percent=fpct,
            fan_max_rpm=fmax,

            ram_total=total,
            ram_used=used,
            ram_pct=rpct,

            root_disk=f_root.result(),
            external_disks=external,
            battery=f_battery.result(),
            peripheral_batteries=f_peripheral.result(),
            bt_devices=bluetooth_devices(bt_records),

            network=f_network.result(),
            ports=f_ports.result(),
        )


def watch_cached(initial: Snapshot, interval: float = 3.0) -> None:
    """
    Full-screen live mode.

    The expensive/static hardware collection is done once before entering this
    loop. Each frame creates a lightweight updated Snapshot, clears the terminal
    and redraws the complete dashboard from the same origin.

    This deliberately avoids cursor-coordinate patching and alternate-screen
    mode, which proved unreliable in Konsole for this dashboard.
    """
    interval = max(0.20, float(interval))
    current = initial
    logo_index = -1
    first_frame = True

    # Hide cursor while the dashboard is running.
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()

    try:
        while True:
            cycle_start = time.monotonic()

            # The first frame can reuse collect() immediately. Every following
            # frame refreshes only the dynamic subset.
            if first_frame:
                first_frame = False
            else:
                current = refresh_dynamic_snapshot(initial)

            # Deterministic rotation: CYAN -> YELLOW -> GOLD -> RED -> GREEN.
            logo_index = (logo_index + 1) % len(WATCH_LOGO_COLORS)
            frame_logo_color = WATCH_LOGO_COLORS[logo_index]

            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write(render(current, logo_color=frame_logo_color))
            sys.stdout.flush()

            elapsed = time.monotonic() - cycle_start
            time.sleep(max(0.0, interval - elapsed))

    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.write("\n")
        sys.stdout.flush()


# =============================================================================
# DIAGNOSTICS
# =============================================================================

def diagnostics(s: Snapshot) -> str:
    lines = [
        "framefetch v14 diagnostics", "-------------------------",
        f"CPU temp: {s.cpu_temp}", f"GPU usage: {s.gpu_usage}", f"GPU freq: {s.gpu_freq}",
        f"Fan RPM: {s.fan_rpm}", f"Fan max RPM: {s.fan_max_rpm}", f"Fan percent: {s.fan_percent}",
        f"SODIMMs: {[(x.locator, x.size, x.kind, x.speed, x.maker, x.part) for x in s.sodimms]}",
        f"RAM usable total: {gib(s.ram_total)}", f"Peripheral batteries: {[(b.name, b.pct) for b in s.peripheral_batteries]}",
        f"Bluetooth: {s.bt_devices}", f"Displays: {[(d.name, d.width, d.height, d.hz, d.builtin) for d in s.displays]}",
        "", show_port_map(), "", f"Saved display partner fingerprints: {sorted(_saved_display_partner_fingerprints())}", "", "CURRENT TYPE-C STATE:",
    ]
    for tc in _typec_ports():
        idx=_natural_num(tc); physical=_framework_port_for_typec_index(idx)
        lines.append(f"  {tc.name}: PORT={physical} partner={_typec_has_partner(tc)} power={_selected_role(read_text(tc / 'power_role')) or '?'} mode={read_text(tc / 'power_operation_mode') or '?'} data={_selected_role(read_text(tc / 'data_role')) or '?'} dp-active={_typec_dp_active_robust(tc)} dp-capable={_typec_has_dp_altmode(tc)} fingerprint={_typec_partner_fingerprint(tc) or '?'}")
    lines += ["", "TYPE-C PARTNER DETAILS:"]
    for tc in _typec_ports():
        idx=_natural_num(tc); physical=_framework_port_for_typec_index(idx)
        if not _typec_has_partner(tc):
            continue
        lines.append(f"  {tc.name} / PORT {physical}:")
        meta=_typec_partner_metadata(tc)
        if meta:
            for k,v in sorted(meta.items()): lines.append(f"    {k} = {v}")
        else: lines.append("    (no partner identity/altmode metadata exposed)")
    lines += ["", "EXTERNAL STORAGE DETECTED:"]
    if not s.external_disks: lines.append("  none")
    for d in s.external_disks:
        lines.append(f"  {d.display_name!r}: dev={d.name} mount={d.mountpoint} USB={d.usb_signature or '?'} PORT={d.physical_port} used={d.pct:.1f}% free={gib(d.free)}")
    lines += ["", "DISPLAY PLACEMENT:"]
    for d in [x for x in s.displays if not x.builtin]:
        lines.append(f"  DRM/KScreen sees {d.name}: {d.width}x{d.height}" + (f"@{d.hz:.2f}Hz" if d.hz else ""))
    lines += ["", f"RENDERED PORTS: {s.ports}"]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Framework 13 ASCII hardware dashboard")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--watch", nargs="?", const=3.0, type=float, metavar="SECONDS", help="live cached mode: refresh RAM/CPU/GPU/temp/fan/network/storage/batteries/Bluetooth/ports; default interval 3.0s")
    parser.add_argument("--cache-hardware", action="store_true", help="cache physical RAM data; run once with sudo")
    parser.add_argument("--calibrate-port", type=int, metavar="N", help="learn the USB topology signature for Framework PORT N")
    parser.add_argument("--show-port-map", action="store_true", help="show persistent physical port calibration")
    parser.add_argument("--calibrate-display", type=int, metavar="N", help="deprecated: use --calibrate-typec N")
    parser.add_argument("--calibrate-typec", type=int, metavar="N", help="interactively map Linux Type-C connector to physical Framework PORT N")
    parser.add_argument("--calibrate-usb", type=int, metavar="N", help="interactively learn a USB topology signature for physical Framework PORT N")
    parser.add_argument("--calibrate-dp", type=int, metavar="N", help="learn the current display Type-C partner fingerprint on physical Framework PORT N")
    parser.add_argument("--clear-dp-calibration", action="store_true", help="forget learned display Type-C partner fingerprints")
    parser.add_argument("--clear-display-calibration", action="store_true", help="remove saved display mappings")
    args = parser.parse_args()

    global USE_COLOR
    if args.no_color:
        USE_COLOR = False

    if args.cache_hardware:
        ok, msg = cache_hardware()
        print(("OK: " if ok else "ERROR: ") + msg)
        return 0 if ok else 1

    if args.calibrate_port is not None:
        ok, msg = calibrate_port(args.calibrate_port)
        print(("OK: " if ok else "ERROR: ") + msg)
        return 0 if ok else 1

    if args.show_port_map:
        print(show_port_map())
        return 0

    if args.calibrate_display is not None:
        print(
            "La calibrazione display della v5 è deprecata perché lega il monitor "
            "alla porta invece del connettore fisico.\n"
            f"Usa: --calibrate-typec {args.calibrate_display}"
        )
        return 2

    if args.calibrate_typec is not None:
        ok, msg = calibrate_typec_interactive(args.calibrate_typec)
        print(("OK: " if ok else "ERROR: ") + msg)
        return 0 if ok else 1

    if args.calibrate_usb is not None:
        ok, msg = calibrate_usb_generic(args.calibrate_usb)
        print(("OK: " if ok else "ERROR: ") + msg)
        return 0 if ok else 1

    if args.calibrate_dp is not None:
        ok, msg = calibrate_dp_partner(args.calibrate_dp)
        print(("OK: " if ok else "ERROR: ") + msg)
        return 0 if ok else 1

    if args.clear_dp_calibration:
        ok, msg = clear_dp_partner_calibration()
        print(("OK: " if ok else "ERROR: ") + msg)
        return 0 if ok else 1

    if args.clear_display_calibration:
        ok, msg = clear_display_calibration()
        print(("OK: " if ok else "ERROR: ") + msg)
        return 0 if ok else 1

    s = collect()
    if args.diagnostics:
        print(diagnostics(s))
    elif args.watch is not None:
        watch_cached(s, args.watch)
    else:
        print(render(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
