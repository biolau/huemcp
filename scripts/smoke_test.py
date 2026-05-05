"""Connectivity smoke test: hit the bridge, summarise lights/rooms/scenes."""
from __future__ import annotations

import sys

from huemcp.client import HueClient


def main() -> int:
    client = HueClient()
    print(f"Bridge: {client.base_url}  verify={client._verify}")

    info = client.bridge_info()
    print(f"Bridge id: {info.get('id')}  name: {info.get('bridge_id')}")

    lights = client.list_resource("light")
    rooms = client.list_resource("room")
    zones = client.list_resource("zone")
    scenes = client.list_resource("scene")
    print(f"Lights: {len(lights)}  Rooms: {len(rooms)}  Zones: {len(zones)}  Scenes: {len(scenes)}")

    print("\n--- Rooms ---")
    for r in rooms:
        name = r.get("metadata", {}).get("name")
        print(f"  {r['id']}  {name!r}  ({len(r.get('children', []))} devices)")

    print("\n--- Lights (first 5) ---")
    for l in lights[:5]:
        name = l.get("metadata", {}).get("name")
        on = l.get("on", {}).get("on")
        bri = l.get("dimming", {}).get("brightness")
        print(f"  {l['id']}  on={on}  bri={bri}  {name!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
