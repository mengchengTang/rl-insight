# Copyright (c) 2026 verl-project authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Network helpers for RL-Insight server endpoints."""

from __future__ import annotations

import socket
from typing import Any
from urllib.parse import urlparse


def local_ipv4() -> str:
    """Return the best non-loopback IPv4 address for this machine, or empty if unknown."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            host = sock.getsockname()[0]
            if host and not host.startswith("127."):
                return host
    except OSError:
        pass

    try:
        host = socket.gethostbyname(socket.gethostname())
        if host and not host.startswith("127."):
            return host
    except OSError:
        pass
    return ""


def service_url_from_server_url(
    server_url: str,
    port: Any,
    path: str = "",
) -> str:
    """Build a service URL using the host from the RL-Insight server URL."""
    if not port:
        return ""
    parsed = urlparse(str(server_url))
    host = parsed.hostname or ""
    if not host:
        return ""
    scheme = parsed.scheme or "http"
    normalized_path = path if not path or path.startswith("/") else f"/{path}"
    return f"{scheme}://{host}:{int(port)}{normalized_path}"
