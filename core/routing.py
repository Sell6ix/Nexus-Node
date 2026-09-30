"""Geocode + route ETA via Nominatim / OSRM (no API key)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

_UA = "NexusNode/0.3 (personal-assistant; contact: local-hub)"


def geocode(query: str) -> dict[str, Any] | None:
    q = (query or "").strip()
    if not q:
        return None
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": "1"}
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=12) as resp:
            rows = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    row = rows[0]
    try:
        return {
            "label": row.get("display_name") or q,
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def route_eta(
    *,
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    mode: str = "driving",
) -> dict[str, Any] | None:
    """Return duration/distance via public OSRM. mode: driving|walking."""
    profile = "foot" if mode in {"walking", "walk", "foot"} else "driving"
    path = (
        f"https://router.project-osrm.org/route/v1/{profile}/"
        f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
        "?overview=false&alternatives=false"
    )
    try:
        req = urllib.request.Request(path, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    routes = data.get("routes") or []
    if not routes:
        return None
    r0 = routes[0]
    seconds = float(r0.get("duration") or 0)
    meters = float(r0.get("distance") or 0)
    return {
        "mode": "walking" if profile == "foot" else "driving",
        "duration_seconds": int(seconds),
        "duration_minutes": max(1, int(round(seconds / 60.0))),
        "distance_km": round(meters / 1000.0, 2),
    }


def maps_links(
    *,
    origin: str,
    destination: str,
    mode: str = "driving",
) -> dict[str, str]:
    travel = "walking" if mode in {"walking", "walk", "foot"} else "driving"
    gdir = (
        "https://www.google.com/maps/dir/?api=1&"
        + urllib.parse.urlencode(
            {
                "origin": origin,
                "destination": destination,
                "travelmode": travel,
            }
        )
    )
    # Apple Maps
    apple = "https://maps.apple.com/?" + urllib.parse.urlencode(
        {
            "saddr": origin,
            "daddr": destination,
            "dirflg": "w" if travel == "walking" else "d",
        }
    )
    return {"google_maps": gdir, "apple_maps": apple}


def plan_trip(
    origin: str,
    destination: str,
    *,
    mode: str = "driving",
    origin_coords: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Geocode + ETA + deep links."""
    dest = geocode(destination)
    if not dest:
        return {"ok": False, "error": f"Could not find destination '{destination}'"}
    if origin_coords:
        o_lat, o_lon = origin_coords
        origin_label = origin
    else:
        orig = geocode(origin)
        if not orig:
            return {"ok": False, "error": f"Could not find origin '{origin}'"}
        o_lat, o_lon = float(orig["lat"]), float(orig["lon"])
        origin_label = str(orig["label"])
    eta = route_eta(
        origin_lat=o_lat,
        origin_lon=o_lon,
        dest_lat=float(dest["lat"]),
        dest_lon=float(dest["lon"]),
        mode=mode,
    )
    links = maps_links(origin=origin_label or origin, destination=str(dest["label"]), mode=mode)
    if not eta:
        return {
            "ok": True,
            "origin": origin_label,
            "destination": dest["label"],
            "eta": None,
            "links": links,
            "hint": "ETA unavailable; open the maps link for directions.",
        }
    return {
        "ok": True,
        "origin": origin_label,
        "destination": dest["label"],
        "eta": eta,
        "links": links,
    }
