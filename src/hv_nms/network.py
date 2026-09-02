from __future__ import annotations

import ipaddress
import platform
import re
import shutil
import socket
import struct
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


@dataclass(frozen=True)
class NetworkIdentity:
    """Friendly identity learned independently of ICMP/ARP presence."""

    hostname: str = ""
    device_name: str = ""
    source: str = ""


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


def _ping_command(ip: str, timeout_ms: int) -> tuple[list[str], float]:
    system = platform.system().lower()
    timeout_ms = max(100, int(timeout_ms))
    if system == "windows":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
    else:
        # Keep the command portable across macOS and Linux. subprocess timeout
        # is the authoritative upper bound, so platform-specific -W units do
        # not create inconsistent behaviour. Deliberately do not use -n: the
        # normal ping banner can expose the system-resolved hostname at no
        # additional network cost during Discovery.
        cmd = ["ping", "-c", "1", ip]
    timeout = max(1.0, timeout_ms / 1000.0 + 0.75)
    return cmd, timeout


def _run_ping(ip: str, timeout_ms: int) -> tuple[float | None, str]:
    cmd, timeout = _ping_command(ip, timeout_ms)
    executable = _find_executable(cmd[0])
    if not executable:
        return None, ""
    cmd = [executable, *cmd[1:]]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        if result.returncode != 0:
            return None, output
        for pattern in (
            r"time[=<]\s*([0-9]*\.?[0-9]+)\s*ms",
            r"Average = ([0-9]*\.?[0-9]+)ms",
        ):
            match = re.search(pattern, output, re.IGNORECASE)
            if match:
                return float(match.group(1)), output
    except Exception:
        pass
    return None, ""


def ping_once(ip: str, timeout_ms: int) -> float | None:
    latency, _output = _run_ping(ip, timeout_ms)
    return latency


def ping_probe(ip: str, timeout_ms: int) -> tuple[float | None, str]:
    """Ping once and also return any hostname shown by the OS ping resolver."""
    latency, output = _run_ping(ip, timeout_ms)
    return latency, hostname_from_ping_output(ip, output)


def normalize_hostname(host: str) -> str:
    host = (host or "").strip().strip(".")
    if not host or host == "?" or " " in host:
        return ""
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", host) or re.fullmatch(r"\d{1,3}", host):
        return ""
    # Resolver status/error tokens are not hostnames. In particular, macOS
    # dns-sd can emit a reverse-query line ending in NXDOMAIN. Earlier builds
    # treated that final token as a valid PTR target, which caused both the
    # Discovery Device and Host Name columns to become literally "NXDOMAIN".
    if host.lower().rstrip(":") in {
        "server", "name", "address", "addresses", "router", "gateway", "unknown", "host",
        "nxdomain", "servfail", "refused", "notfound", "not_found", "nonexistent",
        "timeout", "timedout", "noerror", "formerr", "notimp", "yxdomain", "yxrrset",
        "nxrrset", "notauth", "notzone",
    }:
        return ""
    # Keep normal DNS/mDNS/NetBIOS characters only. This rejects command labels
    # and malformed resolver output while allowing underscores used by devices.
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", host):
        return ""
    return host


def _find_executable(name: str) -> str:
    """Find CLI helpers even when a Finder-launched app has a minimal PATH.

    macOS GUI applications commonly do not inherit Homebrew/MacPorts paths.
    Discovery therefore checks the standard Apple paths plus the normal Intel
    and Apple-Silicon package-manager locations explicitly.
    """
    found = shutil.which(name)
    if found:
        return found
    for root in (
        "/opt/homebrew/bin", "/opt/homebrew/sbin",
        "/usr/local/bin", "/usr/local/sbin",
        "/opt/local/bin", "/opt/local/sbin",
        "/usr/bin", "/usr/sbin", "/bin", "/sbin",
    ):
        candidate = Path(root) / name
        try:
            if candidate.is_file() and candidate.stat().st_mode & 0o111:
                return str(candidate)
        except Exception:
            pass
    return ""


def normalize_device_name(value: str) -> str:
    """Sanitize a friendly service/device label without forcing DNS syntax."""
    value = re.sub(r"[\x00-\x1f\x7f]", "", str(value or "")).strip().strip(".")
    if not value or value == "?" or len(value) > 96:
        return ""
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", value):
        return ""
    if value.lower().rstrip(":") in {
        "nxdomain", "servfail", "refused", "notfound", "unknown", "noerror", "timeout"
    }:
        return ""
    return value


def _command_output(cmd: list[str], timeout: float = 2.0) -> str:
    if not cmd:
        return ""
    executable = _find_executable(cmd[0])
    if not executable:
        return ""
    cmd = [executable, *cmd[1:]]
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
        # Do not harvest DNS status text as a hostname. This catches output
        # from dns-sd, host and nslookup such as "... NXDOMAIN",
        # "Host ... not found" and "No Such Record".
        if any(token in lower for token in (
            "nxdomain", "servfail", "refused", "not found", "no such record",
            "can't find", "cannot find", "timed out", "timeout", "no answer",
        )):
            continue
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
    if system == "darwin" and _find_executable("dns-sd"):
        reverse = ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
        host = _parse_resolver_output(ip, _command_output(["dns-sd", "-Q", reverse, "PTR"], timeout=1.5))
        if host:
            return host

    # Direct NetBIOS node-status query works on macOS without optional helper
    # packages and is particularly useful for Windows/Samba control devices.
    host = netbios_node_name(ip, timeout=0.35)
    if host and host != ip:
        return host

    # Optional NetBIOS/SMB helpers remain as a final compatibility fallback.
    for cmd in (["nmblookup", "-A", ip], ["nbtscan", "-q", ip], ["smbutil", "lookup", ip]):
        output = _command_output(cmd, timeout=1.2)
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
    arp_path = _find_executable("arp")
    if not arp_path:
        return entries

    # Human-readable `arp -a` is intentionally first. `arp -an` suppresses
    # names on macOS and was the reason Discovery often showed blank hostnames.
    for cmd in ([arp_path, "-a"], [arp_path, "-an"]):
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
    nmap_path = _find_executable("nmap")
    if not targets or not nmap_path:
        return {}

    found: dict[str, str] = {}
    proc: subprocess.Popen | None = None
    output_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="hv_nms_nmap_", suffix=".gnmap", delete=False) as handle:
            output_path = handle.name
        cmd = [
            nmap_path, "-sn", "-R", "-T4", "--max-retries", "1",
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


# ---------- active local-name discovery ----------

_MDNS_ADDR = ("224.0.0.251", 5353)
_DNS_A = 1
_DNS_PTR = 12
_DNS_SRV = 33
_DNS_CLASS_IN = 1


@dataclass(frozen=True)
class _DnsRecord:
    name: str
    rtype: int
    value: object


def _dns_encode_name(name: str) -> bytes:
    labels = str(name or "").strip(".").split(".") if name else []
    out = bytearray()
    for label in labels:
        raw = label.encode("utf-8", errors="ignore")
        if not raw or len(raw) > 63:
            raise ValueError(f"Invalid DNS label: {label!r}")
        out.append(len(raw))
        out.extend(raw)
    out.append(0)
    return bytes(out)


def _dns_read_name(packet: bytes, offset: int, *, _depth: int = 0) -> tuple[str, int]:
    if _depth > 20:
        raise ValueError("DNS compression pointer loop")
    labels: list[str] = []
    cursor = offset
    next_offset = offset
    jumped = False
    while True:
        if cursor >= len(packet):
            raise ValueError("DNS name exceeds packet")
        length = packet[cursor]
        if length == 0:
            cursor += 1
            if not jumped:
                next_offset = cursor
            break
        if length & 0xC0 == 0xC0:
            if cursor + 1 >= len(packet):
                raise ValueError("Truncated DNS compression pointer")
            pointer = ((length & 0x3F) << 8) | packet[cursor + 1]
            suffix, _ = _dns_read_name(packet, pointer, _depth=_depth + 1)
            if suffix:
                labels.extend(suffix.rstrip(".").split("."))
            cursor += 2
            if not jumped:
                next_offset = cursor
            jumped = True
            break
        if length & 0xC0:
            raise ValueError("Unsupported DNS label encoding")
        cursor += 1
        end = cursor + length
        if end > len(packet):
            raise ValueError("Truncated DNS label")
        labels.append(packet[cursor:end].decode("utf-8", errors="replace"))
        cursor = end
        if not jumped:
            next_offset = cursor
    name = ".".join(labels)
    return (name + "." if name else ""), next_offset


def _dns_parse_records(packet: bytes) -> list[_DnsRecord]:
    if len(packet) < 12:
        return []
    try:
        _ident, _flags, qd, an, ns, ar = struct.unpack("!HHHHHH", packet[:12])
        offset = 12
        for _ in range(qd):
            _name, offset = _dns_read_name(packet, offset)
            if offset + 4 > len(packet):
                return []
            offset += 4

        records: list[_DnsRecord] = []
        for _ in range(an + ns + ar):
            name, offset = _dns_read_name(packet, offset)
            if offset + 10 > len(packet):
                break
            rtype, _rclass, _ttl, rdlength = struct.unpack("!HHIH", packet[offset:offset + 10])
            offset += 10
            rdata_offset = offset
            end = offset + rdlength
            if end > len(packet):
                break
            value: object | None = None
            if rtype == _DNS_A and rdlength == 4:
                value = socket.inet_ntoa(packet[rdata_offset:end])
            elif rtype == _DNS_PTR:
                try:
                    value, _ = _dns_read_name(packet, rdata_offset)
                except Exception:
                    value = None
            elif rtype == _DNS_SRV and rdlength >= 6:
                priority, weight, port = struct.unpack("!HHH", packet[rdata_offset:rdata_offset + 6])
                try:
                    target, _ = _dns_read_name(packet, rdata_offset + 6)
                    value = (priority, weight, port, target)
                except Exception:
                    value = None
            if value is not None:
                records.append(_DnsRecord(name=name, rtype=rtype, value=value))
            offset = end
        return records
    except Exception:
        return []


def _dns_query_packet(questions: list[tuple[str, int]]) -> bytes:
    # mDNS transaction ID is zero. The QU bit in QCLASS asks responders to
    # return directly to our ephemeral source port, avoiding the need to bind
    # 5353 and coexist with macOS mDNSResponder.
    header = struct.pack("!HHHHHH", 0, 0, len(questions), 0, 0, 0)
    body = bytearray()
    for qname, qtype in questions:
        body.extend(_dns_encode_name(qname))
        body.extend(struct.pack("!HH", int(qtype), 0x8000 | _DNS_CLASS_IN))
    return header + bytes(body)


def _mdns_query(
    questions: list[tuple[str, int]],
    *,
    local_ip: str = "",
    timeout: float = 0.7,
) -> list[_DnsRecord]:
    if not questions:
        return []
    try:
        packet = _dns_query_packet(questions)
    except Exception:
        return []

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    records: list[_DnsRecord] = []
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # TTL 1 keeps the discovery traffic on the local link.
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        if local_ip:
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
                sock.bind((local_ip, 0))
            except Exception:
                sock.bind(("", 0))
        else:
            sock.bind(("", 0))
        sock.settimeout(0.08)
        sock.sendto(packet, _MDNS_ADDR)
        deadline = time.monotonic() + max(0.1, float(timeout))
        seen_packets: set[bytes] = set()
        while time.monotonic() < deadline:
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception:
                break
            if not data or data in seen_packets:
                continue
            seen_packets.add(data)
            records.extend(_dns_parse_records(data))
    except Exception:
        return records
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return records


def _chunks(values: list[str], size: int) -> list[list[str]]:
    size = max(1, int(size))
    return [values[i:i + size] for i in range(0, len(values), size)]


def _reverse_name_to_ipv4(name: str) -> str:
    labels = str(name or "").strip(".").lower().split(".")
    if len(labels) != 6 or labels[-2:] != ["in-addr", "arpa"]:
        return ""
    octets = labels[:4]
    if not all(x.isdigit() and 0 <= int(x) <= 255 for x in octets):
        return ""
    return ".".join(reversed(octets))


def _service_instance_label(instance_fqdn: str, service_type: str) -> str:
    instance = str(instance_fqdn or "").strip(".")
    suffix = str(service_type or "").strip(".")
    if suffix and instance.lower().endswith("." + suffix.lower()):
        instance = instance[: -(len(suffix) + 1)]
    return normalize_device_name(instance)


def mdns_discover_identities(
    targets: set[str] | list[str],
    local_ip: str = "",
    *,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, NetworkIdentity]:
    """Actively discover friendly names advertised over mDNS / DNS-SD.

    Reverse-DNS alone is often empty on isolated production LANs because there
    is no PTR zone. Bonjour devices, however, commonly advertise a service
    instance and an SRV hostname. This census asks for both reverse mDNS PTRs
    and DNS-SD service records, then maps the advertised names back to IPv4.

    The routine runs on the backend auxiliary pool and never blocks the ping/
    ARP stream that populates Discovery rows.
    """
    target_set = {str(ip) for ip in targets if ip}
    if not target_set:
        return {}
    if not local_ip:
        local_ip = get_local_ipv4()

    identities: dict[str, NetworkIdentity] = {}

    def cancelled_now() -> bool:
        return bool(cancelled and cancelled())

    # 1) Batch reverse-mDNS queries for every target. These are cheap and can
    # return host.local names even when the LAN has no conventional DNS PTR.
    reverse_questions = [
        (".".join(reversed(ip.split("."))) + ".in-addr.arpa.", _DNS_PTR)
        for ip in sorted(target_set)
    ]
    for batch in _chunks([q[0] for q in reverse_questions], 18):
        if cancelled_now():
            return identities
        records = _mdns_query([(name, _DNS_PTR) for name in batch], local_ip=local_ip, timeout=0.32)
        for rec in records:
            if rec.rtype != _DNS_PTR or not isinstance(rec.value, str):
                continue
            ip = _reverse_name_to_ipv4(rec.name)
            host = normalize_hostname(rec.value)
            if ip in target_set and host:
                identities[ip] = NetworkIdentity(hostname=host, device_name=host.split(".")[0], source="mdns")

    if cancelled_now():
        return identities

    # 2) Enumerate advertised service types. Devices frequently advertise a
    # useful service instance even when they do not publish a reverse PTR.
    type_records = _mdns_query(
        [("_services._dns-sd._udp.local.", _DNS_PTR)],
        local_ip=local_ip,
        timeout=0.55,
    )
    service_types = sorted({
        str(rec.value).rstrip(".")
        for rec in type_records
        if rec.rtype == _DNS_PTR and isinstance(rec.value, str)
        and str(rec.value).lower().endswith(".local.")
    })[:64]
    if not service_types:
        return identities

    # 3) Browse all advertised types in compact batches and collect instance
    # PTRs. A single host can advertise many instances/types.
    instance_to_type: dict[str, str] = {}
    for batch in _chunks(service_types, 14):
        if cancelled_now():
            return identities
        records = _mdns_query([(stype + ".", _DNS_PTR) for stype in batch], local_ip=local_ip, timeout=0.45)
        batch_lookup = {stype.rstrip(".").lower(): stype for stype in batch}
        for rec in records:
            if rec.rtype != _DNS_PTR or not isinstance(rec.value, str):
                continue
            owner = rec.name.rstrip(".").lower()
            stype = batch_lookup.get(owner)
            if stype:
                instance_to_type[str(rec.value).rstrip(".")] = stype

    if not instance_to_type or cancelled_now():
        return identities

    # 4) Resolve service instances to SRV target hostnames. Answers commonly
    # include A records in the Additional section; if not, query those hosts.
    instance_to_host: dict[str, str] = {}
    host_to_ips: dict[str, set[str]] = {}
    instances = sorted(instance_to_type)
    for batch in _chunks(instances, 12):
        if cancelled_now():
            return identities
        records = _mdns_query([(instance + ".", _DNS_SRV) for instance in batch], local_ip=local_ip, timeout=0.48)
        for rec in records:
            owner = rec.name.rstrip(".")
            if rec.rtype == _DNS_SRV and isinstance(rec.value, tuple) and len(rec.value) == 4:
                host = normalize_hostname(str(rec.value[3]))
                if host:
                    instance_to_host[owner.lower()] = host
            elif rec.rtype == _DNS_A and isinstance(rec.value, str):
                host = normalize_hostname(rec.name)
                if host:
                    host_to_ips.setdefault(host.lower(), set()).add(rec.value)

    hosts = sorted({host for host in instance_to_host.values() if host})
    unresolved_hosts = [host for host in hosts if host.lower() not in host_to_ips]
    for batch in _chunks(unresolved_hosts, 18):
        if cancelled_now():
            return identities
        records = _mdns_query([(host + ".", _DNS_A) for host in batch], local_ip=local_ip, timeout=0.38)
        for rec in records:
            if rec.rtype == _DNS_A and isinstance(rec.value, str):
                host = normalize_hostname(rec.name)
                if host:
                    host_to_ips.setdefault(host.lower(), set()).add(rec.value)

    # Prefer a service instance name over a bare hostname because it is often
    # the user-facing label printed on a camera/controller. Preserve the first
    # useful identity for stability rather than changing names as more service
    # types answer later in the same census.
    for instance in instances:
        host = instance_to_host.get(instance.lower(), "")
        if not host:
            continue
        stype = instance_to_type.get(instance, "")
        friendly = _service_instance_label(instance, stype)
        for ip in sorted(host_to_ips.get(host.lower(), ())):
            if ip not in target_set:
                continue
            prior = identities.get(ip)
            if prior and prior.device_name and prior.device_name.lower() != prior.hostname.split(".")[0].lower():
                continue
            identities[ip] = NetworkIdentity(
                hostname=host,
                device_name=friendly or host.split(".")[0],
                source="bonjour",
            )
    return identities


def netbios_node_name(ip: str, timeout: float = 0.35) -> str:
    """Return a NetBIOS workstation/server name using an NBSTAT node query.

    This avoids relying on optional nmblookup/nbtscan binaries, which are not
    installed by default on macOS. It is only used as a final fallback for a
    host already discovered by ping/ARP/nmap.
    """
    try:
        raw_name = ("*" + (" " * 14)).encode("ascii") + b"\x00"
        encoded = bytearray()
        for byte in raw_name:
            encoded.extend((ord("A") + ((byte >> 4) & 0x0F), ord("A") + (byte & 0x0F)))
        qname = bytes([32]) + bytes(encoded) + b"\x00"
        ident = int(time.time_ns() & 0xFFFF)
        packet = struct.pack("!HHHHHH", ident, 0, 1, 0, 0, 0) + qname + struct.pack("!HH", 0x0021, 0x0001)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(max(0.1, float(timeout)))
            sock.sendto(packet, (ip, 137))
            data, _ = sock.recvfrom(4096)
        finally:
            sock.close()
        if len(data) < 12:
            return ""
        rid, flags, qd, an, _ns, _ar = struct.unpack("!HHHHHH", data[:12])
        if rid != ident or not (flags & 0x8000) or an < 1:
            return ""
        offset = 12
        for _ in range(qd):
            _qname, offset = _dns_read_name(data, offset)
            offset += 4
        for _ in range(an):
            _owner, offset = _dns_read_name(data, offset)
            if offset + 10 > len(data):
                return ""
            rtype, _rclass, _ttl, rdlength = struct.unpack("!HHIH", data[offset:offset + 10])
            offset += 10
            end = offset + rdlength
            if end > len(data):
                return ""
            if rtype == 0x0021 and rdlength >= 1:
                count = data[offset]
                cursor = offset + 1
                candidates: list[tuple[int, str]] = []
                for _idx in range(count):
                    if cursor + 18 > end:
                        break
                    name = data[cursor:cursor + 15].decode("ascii", errors="ignore").rstrip(" \x00")
                    suffix = data[cursor + 15]
                    name_flags = struct.unpack("!H", data[cursor + 16:cursor + 18])[0]
                    cursor += 18
                    is_group = bool(name_flags & 0x8000)
                    candidate = normalize_hostname(name)
                    if candidate and not is_group:
                        # <00> workstation/server name is the most useful label;
                        # retain other unique names only as fallback.
                        candidates.append((0 if suffix == 0x00 else 1, candidate))
                if candidates:
                    candidates.sort(key=lambda x: (x[0], len(x[1]), x[1].lower()))
                    return candidates[0][1]
            offset = end
    except Exception:
        pass
    return ""
