from __future__ import annotations

import ipaddress
import math
import platform
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass

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
    """Return the local IPv4 used for the default route without sending application data."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ""
    finally:
        sock.close()


def scan_interfaces() -> list[NetworkInterface]:
    found: list[NetworkInterface] = [NetworkInterface("Default Route", get_local_ipv4(), None)]
    system = platform.system().lower()
    try:
        if system == "darwin" and shutil.which("ifconfig"):
            out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
            current = ""
            for line in out.splitlines():
                m = re.match(r"^([A-Za-z0-9_.:-]+):", line)
                if m:
                    current = m.group(1)
                m = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+0x([0-9a-fA-F]+)", line)
                if m and current and not m.group(1).startswith("127."):
                    found.append(NetworkInterface(current, m.group(1), hex_mask_to_prefix(m.group(2))))
        elif system == "windows" and shutil.which("ipconfig"):
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
        else:
            cmd = ["ip", "-4", "addr"] if shutil.which("ip") else (["ifconfig"] if shutil.which("ifconfig") else None)
            if cmd:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
                current = ""
                for line in out.splitlines():
                    m = re.match(r"^\d+:\s+([^:]+):", line) or re.match(r"^([A-Za-z0-9_.:-]+):", line)
                    if m:
                        current = m.group(1)
                    m = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
                    if m and current and not m.group(1).startswith("127."):
                        found.append(NetworkInterface(current, m.group(1), int(m.group(2))))
    except Exception:
        pass

    unique: list[NetworkInterface] = []
    seen: set[tuple[str, str]] = set()
    for item in found:
        key = (item.name, item.ip)
        if key not in seen and item.ip:
            seen.add(key)
            unique.append(item)
    return unique or [NetworkInterface("Default Route", get_local_ipv4(), None)]


def ping_once(ip: str, timeout_ms: int) -> float | None:
    system = platform.system().lower()
    timeout_ms = max(100, int(timeout_ms))
    if system == "windows":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
    else:
        cmd = ["ping", "-c", "1", ip]
    timeout = max(2.0, timeout_ms / 1000.0 + 1.5)
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
    if host.lower() in {"server", "name", "address", "router", "gateway", "unknown"}:
        return ""
    return host


def resolve_hostname(ip: str) -> str:
    try:
        host = normalize_hostname(socket.gethostbyaddr(ip)[0])
        if host:
            return host
    except Exception:
        pass
    for cmd in (["host", ip], ["nslookup", ip], ["dig", "+short", "-x", ip]):
        if not shutil.which(cmd[0]):
            continue
        try:
            output = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
            m = re.search(r"pointer\s+([^\s]+)", output, re.IGNORECASE)
            if m:
                host = normalize_hostname(m.group(1))
                if host:
                    return host
        except Exception:
            pass
    return ""


def discovery_targets(start_ip: str, end_ip: str, subnet_mask: str) -> list[str]:
    start = ipaddress.ip_address(start_ip.strip())
    end = ipaddress.ip_address(end_ip.strip())
    if start.version != 4 or end.version != 4:
        raise ValueError("Only IPv4 discovery is supported")
    if int(end) < int(start):
        start, end = end, start
    network = ipaddress.ip_network(f"{start}/{subnet_mask.strip()}", strict=False)
    values = [str(ipaddress.ip_address(value)) for value in range(int(start), int(end) + 1) if ipaddress.ip_address(value) in network]
    if len(values) > DISCOVERY_MAX_TARGETS:
        raise ValueError(f"Discovery is limited to {DISCOVERY_MAX_TARGETS} addresses")
    return values


def read_arp_entries(targets: set[str] | None = None) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not shutil.which("arp"):
        return entries
    for cmd in (["arp", "-an"], ["arp", "-a"]):
        try:
            output = subprocess.run(cmd, capture_output=True, text=True, timeout=4).stdout
            for line in output.splitlines():
                ip_match = re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", line)
                mac_match = re.search(r"\b(?:[0-9a-fA-F]{1,2}:){5}[0-9a-fA-F]{1,2}\b", line)
                if not ip_match:
                    continue
                ip = ip_match.group(0)
                if targets is not None and ip not in targets:
                    continue
                if "incomplete" in line.lower() or "failed" in line.lower():
                    continue
                entries[ip] = mac_match.group(0).upper() if mac_match else ""
            if entries:
                break
        except Exception:
            pass
    return entries


def nmap_discover(targets: list[str]) -> dict[str, str]:
    if not targets or not shutil.which("nmap"):
        return {}
    found: dict[str, str] = {}
    for offset in range(0, len(targets), 64):
        chunk = targets[offset: offset + 64]
        try:
            result = subprocess.run(
                ["nmap", "-sn", "-R", "--max-retries", "1", "--host-timeout", "2s", "-oG", "-", *chunk],
                capture_output=True,
                text=True,
                timeout=max(15, int(len(chunk) * 0.7)),
            )
            for line in result.stdout.splitlines():
                match = re.search(r"^Host:\s+(\d+\.\d+\.\d+\.\d+)\s+\(([^)]*)\)\s+Status:\s+Up", line)
                if match:
                    found[match.group(1)] = normalize_hostname(match.group(2))
        except Exception:
            pass
    return found
