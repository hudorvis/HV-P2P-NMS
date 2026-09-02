from __future__ import annotations

import ipaddress
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .constants import DISCOVERY_MAX_TARGETS


@dataclass(frozen=True)
class NetworkInterface:
    name: str
    ip: str
    prefix: int | None

    @property
    def label(self) -> str:
        if not self.ip:
            return self.name
        try:
            prefix = self.prefix if self.prefix is not None else 24
            network = ipaddress.ip_network(f"{self.ip}/{prefix}", strict=False)
            return f"{self.name} - {self.ip} ({network})"
        except Exception:
            return f"{self.name} - {self.ip}"


@dataclass(frozen=True)
class ArpEntry:
    mac: str = ""
    hostname: str = ""


def mask_to_prefix(mask: str) -> int | None:
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except Exception:
        return None


def hex_mask_to_prefix(hex_mask: str) -> int | None:
    try:
        value = int(hex_mask, 16)
        mask = ".".join(str((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))
        return mask_to_prefix(mask)
    except Exception:
        return None


def get_local_ipv4() -> str:
    """Return the local IPv4 selected by the OS for the default route.

    UDP connect() chooses a route but does not transmit application payload data.
    The hostname fallback keeps isolated/offline control networks usable.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = str(sock.getsockname()[0])
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    finally:
        sock.close()

    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET)
        for info in infos:
            ip = str(info[4][0])
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return ""


def _scan_macos_interfaces() -> list[NetworkInterface]:
    found: list[NetworkInterface] = []
    if not shutil.which("ifconfig"):
        return found
    out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
    current = ""
    for line in out.splitlines():
        m = re.match(r"^([A-Za-z0-9_.:-]+):", line)
        if m:
            current = m.group(1)
        m = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+0x([0-9a-fA-F]+)", line)
        if m and current and not m.group(1).startswith("127."):
            found.append(NetworkInterface(current, m.group(1), hex_mask_to_prefix(m.group(2))))
    return found


def _scan_windows_interfaces() -> list[NetworkInterface]:
    found: list[NetworkInterface] = []
    if not shutil.which("ipconfig"):
        return found
    out = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=5).stdout
    current = ""
    ip = ""
    mask = ""
    for line in out.splitlines() + [""]:
        m_name = re.match(r"^[A-Za-z].*adapter (.+):$", line.strip())
        if m_name or not line.strip():
            if current and ip and not ip.startswith("127."):
                found.append(NetworkInterface(current, ip, mask_to_prefix(mask)))
            if m_name:
                current = m_name.group(1)
            ip = mask = ""
        m_ip = re.search(r"IPv4 Address[.\s]*:\s*(\d+\.\d+\.\d+\.\d+)", line)
        if m_ip:
            ip = m_ip.group(1)
        m_mask = re.search(r"Subnet Mask[.\s]*:\s*(\d+\.\d+\.\d+\.\d+)", line)
        if m_mask:
            mask = m_mask.group(1)
    return found


def _scan_unix_interfaces() -> list[NetworkInterface]:
    found: list[NetworkInterface] = []
    cmd = ["ip", "-4", "addr"] if shutil.which("ip") else (["ifconfig"] if shutil.which("ifconfig") else None)
    if not cmd:
        return found
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    current = ""
    for line in out.splitlines():
        m = re.match(r"^\d+:\s+([^:]+):", line) or re.match(r"^([A-Za-z0-9_.:-]+):", line)
        if m:
            current = m.group(1)
        m = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if m and current and not m.group(1).startswith("127."):
            found.append(NetworkInterface(current, m.group(1), int(m.group(2))))
            continue
        # BSD/macOS style ifconfig output without CIDR, useful on non-mac BSDs.
        m = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+).*\bnetmask\s+0x([0-9a-fA-F]+)", line)
        if m and current and not m.group(1).startswith("127."):
            found.append(NetworkInterface(current, m.group(1), hex_mask_to_prefix(m.group(2))))
    return found


def scan_interfaces() -> list[NetworkInterface]:
    """Return usable IPv4 interfaces, with Default Route first.

    The default-route entry inherits the prefix from the matching physical
    interface whenever possible. This prevents silently treating /16, /20,
    /23, etc. control networks as /24 networks.
    """
    system = platform.system().lower()
    found: list[NetworkInterface] = []
    try:
        if system == "darwin":
            found = _scan_macos_interfaces()
        elif system == "windows":
            found = _scan_windows_interfaces()
        else:
            found = _scan_unix_interfaces()
    except Exception:
        found = []

    unique: list[NetworkInterface] = []
    seen: set[tuple[str, str]] = set()
    for item in found:
        if not item.ip:
            continue
        key = (item.name, item.ip)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    default_ip = get_local_ipv4()
    default_prefix = next((item.prefix for item in unique if item.ip == default_ip and item.prefix is not None), None)
    default = NetworkInterface("Default Route", default_ip, default_prefix)

    result: list[NetworkInterface] = []
    if default.ip:
        result.append(default)
    result.extend(unique)
    return result or [NetworkInterface("Default Route", default_ip, default_prefix)]


def interface_discovery_range(interface: NetworkInterface) -> tuple[str, str, str]:
    """Return first/last usable IPv4 and netmask without materialising hosts()."""
    if not interface.ip:
        raise ValueError("Selected interface has no IPv4 address")
    prefix = 24 if interface.prefix is None else int(interface.prefix)
    network = ipaddress.ip_network(f"{interface.ip}/{prefix}", strict=False)
    if network.version != 4:
        raise ValueError("Only IPv4 discovery is supported")
    if network.prefixlen <= 30:
        start_int = int(network.network_address) + 1
        end_int = int(network.broadcast_address) - 1
    else:
        # ipaddress treats both /31 endpoints and the /32 endpoint as hosts.
        start_int = int(network.network_address)
        end_int = int(network.broadcast_address)
    return str(ipaddress.ip_address(start_int)), str(ipaddress.ip_address(end_int)), str(network.netmask)


def ping_once(ip: str, timeout_ms: int) -> float | None:
    system = platform.system().lower()
    timeout_ms = max(100, int(timeout_ms))
    if system == "windows":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
    else:
        # Keep the command portable across macOS and Linux. subprocess timeout
        # is the authoritative upper bound, so platform-specific -W units do
        # not create inconsistent behaviour.
        cmd = ["ping", "-c", "1", ip]
    timeout = max(1.0, timeout_ms / 1000.0 + 0.75)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return None
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        for pattern in (
            r"time[=<]\s*([0-9]*\.?[0-9]+)\s*ms",
            r"Average = ([0-9]*\.?[0-9]+)ms",
        ):
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                return float(match.group(1))
    except Exception:
        pass
    return None


def normalize_hostname(host: str) -> str:
    host = (host or "").strip().strip(".")
    if not host or host == "?" or " " in host:
        return ""
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", host) or re.fullmatch(r"\d{1,3}", host):
        return ""
    if host.lower().rstrip(":") in {
        "server", "name", "address", "addresses", "router", "gateway", "unknown", "host",
    }:
        return ""
    # Keep normal DNS/mDNS/NetBIOS characters only. This rejects command labels
    # and malformed resolver output while allowing underscores used by devices.
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", host):
        return ""
    return host


def _command_output(cmd: list[str], timeout: float = 2.0) -> str:
    if not cmd or not shutil.which(cmd[0]):
        return ""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="ignore") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="ignore") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return (stdout + "\n" + stderr).strip()
    except Exception:
        return ""


def hostname_from_ping_output(ip: str, output: str) -> str:
    patterns = (
        r"Pinging\s+([^\s\[]+)\s*\[\s*" + re.escape(ip) + r"\s*\]",
        r"PING\s+([^\s\(]+)\s*\(\s*" + re.escape(ip) + r"\s*\)",
    )
    for pattern in patterns:
        m = re.search(pattern, output or "", re.IGNORECASE)
        if m:
            host = normalize_hostname(m.group(1))
            if host and host != ip:
                return host
    return ""


def _parse_resolver_output(ip: str, output: str) -> str:
    if not output:
        return ""
    patterns = (
        r"(?:domain name pointer|pointer)\s+([^\s]+)",
        r"\bname\s*=\s*([^\s]+)",
        r"^Name:\s*([^\s]+)$",
        r"^name:\s*([^\s]+)$",
    )
    for pattern in patterns:
        m = re.search(pattern, output, re.IGNORECASE | re.MULTILINE)
        if m:
            host = normalize_hostname(m.group(1))
            if host and host != ip and ".in-addr.arpa" not in host.lower():
                return host

    # dig +short -x can return only the PTR target on its own line.
    for raw in output.splitlines():
        line = raw.strip().rstrip(".")
        if not line or line == ip or line.startswith(";"):
            continue
        lower = line.lower()
        if any(lower.startswith(prefix) for prefix in ("server:", "address:", "addresses:")):
            continue
        if ".in-addr.arpa" in lower:
            # dns-sd output can contain both the query and PTR answer on one
            # line. Take the final token if it resembles a host name.
            tokens = line.split()
            if tokens:
                candidate = normalize_hostname(tokens[-1])
                if candidate and ".in-addr.arpa" not in candidate.lower() and candidate != ip:
                    return candidate
            continue
        if " " not in line:
            candidate = normalize_hostname(line)
            if candidate and candidate != ip:
                return candidate
    return ""


def resolve_hostname(ip: str, hint: str = "") -> str:
    """Best-effort hostname lookup suitable for a local production LAN.

    This function is intentionally called on a background name-resolution
    pool by Discovery, so no resolver can block the UI or delay first results.
    """
    hint = normalize_hostname(hint)
    if hint and hint != ip:
        return hint

    system = platform.system().lower()
    if system == "darwin":
        # dscacheutil consults the macOS resolver stack/cache without the
        # unbounded blocking behaviour socket.gethostbyaddr() can exhibit on
        # isolated production LANs.
        output = _command_output(["dscacheutil", "-q", "host", "-a", "ip_address", ip], timeout=1.5)
        host = _parse_resolver_output(ip, output)
        if host:
            return host
    else:
        try:
            host = normalize_hostname(socket.gethostbyaddr(ip)[0])
            if host and host != ip:
                return host
        except Exception:
            pass

    # Windows ping -a is useful for NetBIOS/DNS hints. A second ping on macOS
    # normally just repeats the numeric IP, so avoid that unnecessary delay.
    if system == "windows":
        host = hostname_from_ping_output(ip, _command_output(["ping", "-a", "-n", "1", ip], timeout=1.5))
        if host:
            return host

    for cmd in (["host", ip], ["dig", "+time=1", "+tries=1", "+short", "-x", ip], ["nslookup", ip]):
        host = _parse_resolver_output(ip, _command_output(cmd, timeout=2.0))
        if host:
            return host

    # Bonjour/mDNS reverse lookup. dns-sd is built into macOS and normally
    # remains running, so the short subprocess timeout intentionally captures
    # any answer that arrives quickly and then terminates it.
    if system == "darwin" and shutil.which("dns-sd"):
        reverse = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
        host = _parse_resolver_output(ip, _command_output(["dns-sd", "-Q", reverse, "PTR"], timeout=1.5))
        if host:
            return host

    # Optional NetBIOS/SMB helpers used on some camera/control networks.
    for cmd in (["nmblookup", "-A", ip], ["nbtscan", "-q", ip], ["smbutil", "lookup", ip]):
        output = _command_output(cmd, timeout=2.0)
        if not output:
            continue
        m = re.search(r"^\s*([A-Za-z0-9._-]{1,63})\s+<00>", output, re.MULTILINE)
        if not m:
            m = re.search(r"\bname\s*=\s*([A-Za-z0-9._-]+)", output, re.IGNORECASE)
        if m:
            host = normalize_hostname(m.group(1))
            if host and host != ip:
                return host
    return ""


def discovery_targets(start_ip: str, end_ip: str, subnet_mask: str) -> list[str]:
    start = ipaddress.ip_address(start_ip.strip())
    end = ipaddress.ip_address(end_ip.strip())
    if start.version != 4 or end.version != 4:
        raise ValueError("Only IPv4 discovery is supported")
    if int(end) < int(start):
        start, end = end, start
    network = ipaddress.ip_network(f"{start}/{subnet_mask.strip()}", strict=False)

    lo = max(int(start), int(network.network_address))
    hi = min(int(end), int(network.broadcast_address))
    if hi < lo:
        return []
    count = hi - lo + 1
    if count > DISCOVERY_MAX_TARGETS:
        raise ValueError(f"Discovery is limited to {DISCOVERY_MAX_TARGETS} addresses")

    # Enforce the size limit before allocating the list. This is critical for
    # accidental /8 or similarly large ranges.
    return [str(ipaddress.ip_address(value)) for value in range(lo, hi + 1)]


def read_arp_entries(targets: set[str] | None = None) -> dict[str, ArpEntry]:
    """Read the local ARP cache and preserve any human-readable host names."""
    entries: dict[str, ArpEntry] = {}
    if not shutil.which("arp"):
        return entries

    # Human-readable `arp -a` is intentionally first. `arp -an` suppresses
    # names on macOS and was the reason Discovery often showed blank hostnames.
    for cmd in (["arp", "-a"], ["arp", "-an"]):
        try:
            output = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
        except Exception:
            continue
        for line in output.splitlines():
            lower = line.lower()
            if "incomplete" in lower or "failed" in lower:
                continue
            ip_match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line)
            if not ip_match:
                continue
            ip = ip_match.group(0)
            if targets is not None and ip not in targets:
                continue
            mac_match = re.search(r"\b(?:[0-9a-fA-F]{1,2}[:-]){5}[0-9a-fA-F]{1,2}\b", line)
            mac = mac_match.group(0).replace("-", ":").upper() if mac_match else ""

            hostname = ""
            # Common macOS/Linux format: hostname.local (192.168.1.10) at ...
            m_name = re.match(r"^\s*([^\s(]+)\s+\(" + re.escape(ip) + r"\)", line)
            if m_name:
                candidate = normalize_hostname(m_name.group(1))
                if candidate not in {"", "?", ip}:
                    hostname = candidate

            old = entries.get(ip, ArpEntry())
            entries[ip] = ArpEntry(mac=mac or old.mac, hostname=hostname or old.hostname)
        # `arp -a` and `arp -an` expose the same neighbour cache; -an is only
        # a compatibility fallback when the human-readable command gives no
        # usable output. Avoid running both on every streaming ARP poll.
        if output.strip():
            break
    return entries


def nmap_discover(targets: list[str], cancelled: Callable[[], bool] | None = None) -> dict[str, str]:
    """Optional fast host-presence fallback with bounded/cancellable runtime.

    One nmap process receives the target list so nmap can parallelise the sweep
    itself. Reverse DNS is enabled because nmap can resolve many PTR records
    efficiently in one asynchronous process; it never blocks the fast ping/ARP
    stream that populates the UI. Output is written to a temporary file so a
    large scan cannot deadlock on a full stdout pipe while the parent polls for
    cancellation.
    """
    if not targets or not shutil.which("nmap"):
        return {}

    found: dict[str, str] = {}
    proc: subprocess.Popen | None = None
    output_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="hv_nms_nmap_", suffix=".gnmap", delete=False) as handle:
            output_path = handle.name
        cmd = [
            "nmap", "-sn", "-R", "-T4", "--max-retries", "1",
            "--host-timeout", "1500ms", "--min-parallelism", "32",
            "-oG", output_path, *targets,
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + min(30.0, max(8.0, len(targets) * 0.02))
        while proc.poll() is None:
            if cancelled and cancelled():
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
                return {}
            if time.monotonic() >= deadline:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
                break
            time.sleep(0.1)

        if output_path:
            try:
                output = Path(output_path).read_text(encoding="utf-8", errors="replace")
            except Exception:
                output = ""
            for line in output.splitlines():
                match = re.search(r"^Host:\s+(\d+\.\d+\.\d+\.\d+)\s+\(([^)]*)\)\s+Status:\s+Up", line)
                if match:
                    found[match.group(1)] = normalize_hostname(match.group(2))
    except Exception:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
    finally:
        if output_path:
            try:
                Path(output_path).unlink(missing_ok=True)
            except Exception:
                pass
    return found

