"""Parse adb command outputs into structured data."""

import re
from typing import Optional

from adb_utils.client import ADBResult, adb


def parse_devices(text: str) -> list[dict]:
    """Parse `adb devices -l` output into a list of device dicts.

    Example line:
    192.168.1.100:5555  device product:raven model:Pixel_6_Pro device:raven transport_id:7
    R5CT1234ABCD          device usb:1-1 product:mars model:Xiaomi_12 device:mars transport_id:3
    """
    devices = []
    for line in text.strip().split("\n"):
        if not line.strip():
            continue
        # Skip the header line
        if line.startswith("List of devices"):
            continue

        # Split into tokens: serial, state, and optional attributes
        parts = line.strip().split()
        if len(parts) < 2:
            continue

        serial = parts[0]
        state = parts[1]

        # Parse key:value attributes from remaining parts
        attrs = {}
        for token in parts[2:]:
            if ":" in token:
                key, val = token.split(":", 1)
                attrs[key] = val

        device = {
            "serial": serial,
            "state": state,
            "model": attrs.get("model", ""),
            "product": attrs.get("product", ""),
            "device": attrs.get("device", ""),
            "transport_id": attrs.get("transport_id", ""),
            "connection_type": "usb" if "usb" in attrs else "network",
        }
        devices.append(device)
    return devices


def parse_packages(text: str) -> list[str]:
    """Parse `pm list packages` output into a package name list.

    Example: "package:com.example.app"
    """
    packages = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("package:"):
            packages.append(line[len("package:"):])
    return packages


def get_device_detail(serial: str) -> dict:
    """Build a full device detail dict for a given serial."""
    info = adb.get_device_info(serial)
    return {
        "serial": serial,
        "state": "device",  # will be updated by caller
        "model": info.get("model", "unknown"),
        "brand": info.get("brand", "unknown"),
        "android_version": info.get("android_version", "unknown"),
        "sdk_version": info.get("sdk_version", "unknown"),
        "abi": info.get("abi", "unknown"),
    }


def get_devices_with_details() -> list[dict]:
    """Return device list with full details (model, brand, etc.)."""
    result = adb.devices()
    if not result.success:
        return []

    devices = parse_devices(result.stdout)
    for d in devices:
        try:
            detail = get_device_detail(d["serial"])
            d.update(detail)
        except Exception:
            pass  # device might be offline or busy
    return devices
