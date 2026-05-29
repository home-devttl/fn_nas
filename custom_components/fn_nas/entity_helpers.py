"""Helpers for stable entity and device identifiers."""

from __future__ import annotations

import re
from hashlib import sha1

from .const import DEVICE_ID_NAS, DEVICE_ID_ZFS, DOMAIN

UNKNOWN_VALUES = {
    "",
    "-",
    "--",
    "unknown",
    "none",
    "null",
    "n/a",
    "na",
    "not available",
    "not_available",
    "未知",
}


def sanitize_id(value: object) -> str:
    """Return a registry-safe identifier fragment."""
    raw = str(value or "unknown").strip()
    text = raw.lower()
    slug = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    if slug and slug == text:
        return slug

    digest = sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{slug or 'id'}_{digest}"


def is_known_value(value: object) -> bool:
    """Return True when a collected hardware value is usable."""
    if value is None:
        return False
    return str(value).strip().lower() not in UNKNOWN_VALUES


def entry_prefix(coordinator) -> str:
    """Return the unique prefix for this config entry."""
    config_entry = getattr(coordinator, "config_entry", None)
    if config_entry is None and hasattr(coordinator, "main_coordinator"):
        config_entry = getattr(coordinator.main_coordinator, "config_entry", None)
    return config_entry.entry_id


def nas_identifier(coordinator) -> str:
    """Return the main NAS device identifier for this config entry."""
    return f"{entry_prefix(coordinator)}_{DEVICE_ID_NAS}"


def zfs_identifier(coordinator) -> str:
    """Return the ZFS device identifier for this config entry."""
    return f"{entry_prefix(coordinator)}_{DEVICE_ID_ZFS}"


def child_identifier(coordinator, child_id: str) -> str:
    """Return a child device identifier scoped to this config entry."""
    return f"{entry_prefix(coordinator)}_{child_id}"


def nas_via_device(coordinator) -> tuple[str, str]:
    """Return a via_device tuple pointing to this config entry's NAS."""
    return (DOMAIN, nas_identifier(coordinator))


def disk_key(disk: dict) -> str:
    """Use serial as the stable disk key, falling back to the kernel device name."""
    serial = disk.get("serial")
    if is_known_value(serial):
        return sanitize_id(str(serial).strip())
    return sanitize_id(disk.get("device"))


def find_disk(disks: list[dict], key: str, fallback_device: str | None = None) -> dict | None:
    """Find a disk by stable key, with device name fallback for legacy/unknown serials."""
    for disk in disks:
        if disk_key(disk) == key:
            return disk

    if fallback_device:
        for disk in disks:
            if disk.get("device") == fallback_device:
                return disk

    return None
