"""MCP server exposing Philips Hue CLIP v2 controls over stdio."""
from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import HueClient, HueError

log = logging.getLogger("huemcp")

mcp = FastMCP("huemcp")
_client: HueClient | None = None


def _hue() -> HueClient:
    global _client
    if _client is None:
        _client = HueClient()
    return _client


def _light_summary(light: dict[str, Any]) -> dict[str, Any]:
    color = light.get("color") or {}
    ct = light.get("color_temperature") or {}
    out: dict[str, Any] = {
        "id": light["id"],
        "name": light.get("metadata", {}).get("name"),
        "archetype": light.get("metadata", {}).get("archetype"),
        "on": light.get("on", {}).get("on"),
        "brightness": light.get("dimming", {}).get("brightness"),
        "owner": light.get("owner"),
    }
    if ct:
        out["color_temp_mirek"] = ct.get("mirek")
        schema = ct.get("mirek_schema") or {}
        out["color_temp_range_mirek"] = [
            schema.get("mirek_minimum"),
            schema.get("mirek_maximum"),
        ]
    if color.get("xy"):
        out["color_xy"] = color["xy"]

    fx_v2 = light.get("effects_v2") or {}
    available = (fx_v2.get("action") or {}).get("effect_values") or []
    current = (fx_v2.get("status") or {}).get("effect")
    if available or current:
        out["effects"] = {"current": current, "available": available}

    timed = light.get("timed_effects") or {}
    timed_available = timed.get("effect_values") or []
    if timed_available:
        out["timed_effects"] = {
            "current": timed.get("status"),
            "available": timed_available,
        }

    gradient = light.get("gradient")
    if gradient:
        out["gradient"] = {
            "points": gradient.get("points", []),
            "mode": gradient.get("mode"),
            "points_capable": gradient.get("points_capable"),
            "pixel_count": gradient.get("pixel_count"),
            "mode_values": gradient.get("mode_values", []),
        }
    return out


def _group_summary(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": group["id"],
        "on": group.get("on", {}).get("on"),
        "brightness": group.get("dimming", {}).get("brightness"),
        "owner": group.get("owner"),
    }


def _scene_summary(scene: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": scene["id"],
        "name": scene.get("metadata", {}).get("name"),
        "group": scene.get("group"),
        "active": scene.get("status", {}).get("active"),
        "last_recall": scene.get("status", {}).get("last_recall"),
        "speed": scene.get("speed"),
        "auto_dynamic": scene.get("auto_dynamic"),
    }


def _room_or_zone_summary(item: dict[str, Any]) -> dict[str, Any]:
    grouped_light = next(
        (s["rid"] for s in item.get("services", []) if s.get("rtype") == "grouped_light"),
        None,
    )
    return {
        "id": item["id"],
        "name": item.get("metadata", {}).get("name"),
        "archetype": item.get("metadata", {}).get("archetype"),
        "grouped_light_id": grouped_light,
        "children": item.get("children", []),
    }


def _try(fn):
    try:
        return fn()
    except HueError as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Discovery tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_lights() -> dict[str, Any]:
    """List every light on the bridge with on/off state, brightness, colour, and owner.

    `owner.rid` points to the parent device — useful when one device exposes
    multiple light services. To find which room a light belongs to, cross-reference
    the device id against `list_rooms()` `children`.
    """
    return _try(lambda: {"lights": [_light_summary(l) for l in _hue().list_resource("light")]})


@mcp.tool()
def list_rooms() -> dict[str, Any]:
    """List rooms with their `grouped_light_id` (use with `set_group`) and member devices."""
    return _try(lambda: {"rooms": [_room_or_zone_summary(r) for r in _hue().list_resource("room")]})


@mcp.tool()
def list_zones() -> dict[str, Any]:
    """List zones with their `grouped_light_id` (use with `set_group`) and member lights.

    Zones differ from rooms: a light belongs to exactly one room but can be in many
    zones (e.g. a "Downstairs" zone spanning Kitchen + Living room).
    """
    return _try(lambda: {"zones": [_room_or_zone_summary(z) for z in _hue().list_resource("zone")]})


@mcp.tool()
def list_scenes(group_id: str | None = None) -> dict[str, Any]:
    """List scenes. If `group_id` is given (a room or zone id), only scenes targeting
    that group are returned. Use the returned `id` with `activate_scene`."""
    def _do() -> dict[str, Any]:
        scenes = _hue().list_resource("scene")
        if group_id:
            scenes = [s for s in scenes if s.get("group", {}).get("rid") == group_id]
        return {"scenes": [_scene_summary(s) for s in scenes]}
    return _try(_do)


@mcp.tool()
def get_resource(rtype: str, rid: str | None = None) -> dict[str, Any]:
    """Generic escape hatch — fetch a Hue resource by type, optionally by id.

    `rtype` is a CLIP v2 resource type, e.g. `light`, `room`, `zone`, `scene`,
    `grouped_light`, `device`, `bridge`, `motion`, `temperature`, `button`,
    `behavior_instance`, `smart_scene`. Returns the raw bridge payload — useful
    when you need fields the higher-level tools don't expose.
    """
    def _do() -> dict[str, Any]:
        if rid:
            item = _hue().get_resource(rtype, rid)
            return {"data": item}
        return {"data": _hue().list_resource(rtype)}
    return _try(_do)


# ---------------------------------------------------------------------------
# Control tools
# ---------------------------------------------------------------------------


def _build_light_payload(
    on: bool | None,
    brightness: float | None,
    color_temp_mirek: int | None,
    color_xy: dict[str, float] | list[float] | None,
    effect: str | None,
    transition_ms: int | None,
    gradient_points: list[list[float]] | None = None,
    gradient_mode: str | None = None,
    timed_effect: str | None = None,
    timed_effect_duration_ms: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if on is not None:
        payload["on"] = {"on": bool(on)}
    if brightness is not None:
        payload["dimming"] = {"brightness": float(brightness)}
    if color_temp_mirek is not None:
        payload["color_temperature"] = {"mirek": int(color_temp_mirek)}
    if color_xy is not None:
        if isinstance(color_xy, (list, tuple)):
            xy = {"x": float(color_xy[0]), "y": float(color_xy[1])}
        else:
            xy = {"x": float(color_xy["x"]), "y": float(color_xy["y"])}
        payload["color"] = {"xy": xy}
    if effect is not None:
        payload["effects_v2"] = {"action": {"effect": effect}}
    if gradient_points is not None or gradient_mode is not None:
        gradient: dict[str, Any] = {}
        if gradient_points is not None:
            gradient["points"] = [
                {"color": {"xy": {"x": float(p[0]), "y": float(p[1])}}}
                for p in gradient_points
            ]
        if gradient_mode is not None:
            gradient["mode"] = gradient_mode
        payload["gradient"] = gradient
    if timed_effect is not None or timed_effect_duration_ms is not None:
        timed: dict[str, Any] = {}
        if timed_effect is not None:
            timed["effect"] = timed_effect
        if timed_effect_duration_ms is not None:
            timed["duration"] = int(timed_effect_duration_ms)
        payload["timed_effects"] = timed
    if transition_ms is not None:
        payload.setdefault("dynamics", {})["duration"] = int(transition_ms)
    return payload


@mcp.tool()
def set_light(
    light_id: str,
    on: bool | None = None,
    brightness: float | None = None,
    color_temp_mirek: int | None = None,
    color_xy: list[float] | None = None,
    effect: str | None = None,
    gradient_points: list[list[float]] | None = None,
    gradient_mode: str | None = None,
    timed_effect: str | None = None,
    timed_effect_duration_ms: int | None = None,
    transition_ms: int | None = None,
) -> dict[str, Any]:
    """Update one light. Pass only the attributes you want to change.

    Use `list_lights()` to discover what each light supports — it returns
    `effects.available`, `timed_effects.available`, and (for gradient strips)
    a `gradient` block with `points_capable` and `mode_values`.

    - `brightness` is 0–100 (percent). Setting brightness on an off light does
      NOT turn it on; pass `on=True` as well.
    - `color_temp_mirek` is the CIE mired value; the per-light valid range is
      reported by `list_lights()` as `color_temp_range_mirek` (commonly 153–500,
      i.e. ~6500K–2000K). Setting this clears any active colour.
    - `color_xy` is a 2-element list `[x, y]` in CIE 1931 colour space, clamped
      to the light's gamut. Setting this clears any active colour temperature.
    - `effect` is one of the values from `effects.available` (e.g. `no_effect`,
      `candle`, `fire`, `prism`, `sparkle`, `opal`, `glisten`). Pass `no_effect`
      to stop a running effect.
    - `gradient_points` is a list of `[x, y]` CIE 1931 points (typically 2–5,
      capped by the light's `gradient.points_capable`). Only meaningful for
      gradient-capable strips like the Hue Play Gradient lightstrip.
    - `gradient_mode` is one of the light's `gradient.mode_values` —
      `interpolated_palette` (default smooth blend), `interpolated_palette_mirrored`,
      `random_pixelated`, or `segmented_palette`.
    - `timed_effect` is `sunrise`, `sunset`, or `no_effect`. Use
      `timed_effect_duration_ms` to set how long the effect runs.
    - `transition_ms` is the fade duration in milliseconds for on/off, dimming,
      and colour changes.
    """
    payload = _build_light_payload(
        on,
        brightness,
        color_temp_mirek,
        color_xy,
        effect,
        transition_ms,
        gradient_points=gradient_points,
        gradient_mode=gradient_mode,
        timed_effect=timed_effect,
        timed_effect_duration_ms=timed_effect_duration_ms,
    )
    if not payload:
        return {"ok": False, "error": "no fields to update"}
    return _try(lambda: {"ok": True, "raw": _hue().update_resource("light", light_id, payload)})


@mcp.tool()
def set_group(
    grouped_light_id: str,
    on: bool | None = None,
    brightness: float | None = None,
    color_temp_mirek: int | None = None,
    color_xy: list[float] | None = None,
    transition_ms: int | None = None,
) -> dict[str, Any]:
    """Control all lights in a room or zone at once via its `grouped_light_id`.

    Get this id from `list_rooms()` or `list_zones()` (`grouped_light_id` field).
    Same parameters as `set_light`, minus per-light effects.
    """
    payload = _build_light_payload(
        on, brightness, color_temp_mirek, color_xy, None, transition_ms
    )
    if not payload:
        return {"ok": False, "error": "no fields to update"}
    return _try(
        lambda: {
            "ok": True,
            "raw": _hue().update_resource("grouped_light", grouped_light_id, payload),
        }
    )


@mcp.tool()
def activate_scene(
    scene_id: str,
    brightness: float | None = None,
    transition_ms: int | None = None,
    action: str = "active",
) -> dict[str, Any]:
    """Recall a scene — applies its stored light states to its target group.

    - `action` is `active` (default), `dynamic_palette` (cycles through the scene's
      palette), or `static` (one-shot apply with no dynamics).
    - `brightness` (0–100) overrides the scene's stored brightness.
    - `transition_ms` overrides the scene's default fade duration.
    """
    payload: dict[str, Any] = {"recall": {"action": action}}
    if brightness is not None:
        payload["recall"]["dimming"] = {"brightness": float(brightness)}
    if transition_ms is not None:
        payload["recall"]["duration"] = int(transition_ms)
    return _try(
        lambda: {"ok": True, "raw": _hue().update_resource("scene", scene_id, payload)}
    )


@mcp.tool()
def identify_light(light_id: str) -> dict[str, Any]:
    """Make a light breathe (briefly pulse) so you can physically locate it."""
    return _try(
        lambda: {
            "ok": True,
            "raw": _hue().update_resource(
                "light", light_id, {"alert": {"action": "breathe"}}
            ),
        }
    )


@mcp.tool()
def bridge_info() -> dict[str, Any]:
    """Return basic bridge identity (name, id, software version, time-zone)."""
    return _try(lambda: {"bridge": _hue().bridge_info()})


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    mcp.run()


if __name__ == "__main__":
    main()
