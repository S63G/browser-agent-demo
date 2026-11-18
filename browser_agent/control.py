"""Client for the fake portal's demo controls (layout switch, fault injection). Not part of the agent."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class PortalControl:
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def reachable(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/admin/api/settings", timeout=self.timeout):
                return True
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            return False

    def get(self) -> dict:
        with urllib.request.urlopen(f"{self.base_url}/admin/api/settings", timeout=self.timeout) as response:
            return json.loads(response.read())

    def set(self, layout: str | None = None, submit_delay_once: float | None = None) -> dict:
        settings = self.get()
        if layout is not None:
            settings["layout"] = layout
        if submit_delay_once is not None:
            settings["submit_delay_once"] = submit_delay_once
        request = urllib.request.Request(f"{self.base_url}/admin/api/settings", data=json.dumps(settings).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read())
