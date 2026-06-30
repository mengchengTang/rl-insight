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

"""HTTP API and remote helpers for the RL-Insight server."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import requests

if TYPE_CHECKING:
    from fastapi import FastAPI, Request
    from omegaconf import DictConfig

    from .prometheus_targets import PrometheusTarget

logger = logging.getLogger(__name__)

_API_PREFIX = "/api/v1"


def server_url() -> str:
    """Return the configured RL-Insight server URL without a trailing slash."""
    from ..utils.constants import MonitorEnv

    return str(os.environ.get(MonitorEnv.SERVER_URL, "")).strip().rstrip("/")


def get_server_services() -> dict[str, Any]:
    """Fetch service endpoints from the RL-Insight server."""
    from ..utils.constants import MonitorEnv

    base_url = server_url()
    if not base_url:
        logger.error(
            "RL-Insight server URL is required; set %s",
            MonitorEnv.SERVER_URL,
        )
        return {}

    url = f"{base_url}{_API_PREFIX}/services"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Failed to fetch RL-Insight server services at %s: %s", url, exc)
        return {}
    return data if isinstance(data, dict) else {}


def create_app(conf: DictConfig) -> FastAPI:
    """Create the RL-Insight server application."""
    from fastapi import Body, FastAPI, HTTPException, Request, status

    from ..utils.constants import PrometheusScrape
    from .prometheus_targets import PrometheusTargetStore

    app = FastAPI(title="RL-Insight server", version="0.1.0")
    store = PrometheusTargetStore.from_config(conf)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(f"{_API_PREFIX}/services")
    def services(request: Request) -> dict[str, Any]:
        host = _public_host(conf, request)
        return {
            "status": "ok",
            "otlp_traces_endpoint": _otlp_traces_endpoint(conf, host),
            "prometheus_url": _prometheus_url(conf, host),
            "grafana_url": _grafana_url(conf, host),
        }

    @app.post(f"{_API_PREFIX}/prometheus/targets")
    def register_prometheus_targets(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        job_name = str(
            payload.get("job_name") or PrometheusScrape.TRAINER_METRICS_JOB
        )
        try:
            targets = _target_specs_from_payload(payload)
            result = store.register(job_name, targets)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return {"status": "ok", **result}

    return app


def main(argv: Sequence[str] | None = None) -> int:
    import uvicorn

    from ..utils.monitor_config_loader import load_server_config_file

    parser = argparse.ArgumentParser(prog="python -m rl_insight.server.http_api")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Resolved server config YAML used by the RL-Insight server.",
    )
    args = parser.parse_args(argv)

    conf = load_server_config_file(args.config)
    host = _select_str(conf, "server.host") or "127.0.0.1"
    port = int(_select(conf, "server.port", default=8080))
    uvicorn.run(create_app(conf), host=host, port=port)
    return 0


def _target_specs_from_payload(payload: Mapping[str, Any]) -> list[PrometheusTarget]:
    from .prometheus_targets import PrometheusTarget

    raw_targets = payload.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("targets must be a non-empty list")

    default_labels = _labels_from_mapping(payload.get("labels") or {})
    specs: list[PrometheusTarget] = []
    for item in raw_targets:
        if isinstance(item, str):
            specs.append(PrometheusTarget(target=item, labels=default_labels))
            continue
        if isinstance(item, dict):
            target = item.get("target")
            labels = {
                **default_labels,
                **_labels_from_mapping(item.get("labels") or {}),
            }
            specs.append(PrometheusTarget(target=str(target), labels=labels))
            continue
        raise ValueError("each target must be either a string or an object")
    return specs


def _labels_from_mapping(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if not isinstance(value, dict):
        raise ValueError("labels must be an object")
    return dict(value)


def _public_host(conf: DictConfig, request: Request) -> str:
    configured = _select_str(conf, "server.public_host")
    if configured:
        return configured
    request_host = request.url.hostname or ""
    if request_host:
        return request_host
    return _select_str(conf, "server.host") or "127.0.0.1"


def _otlp_traces_endpoint(conf: DictConfig, host: str) -> str:
    if not bool(_select(conf, "tempo.enable", default=True)):
        return ""
    port = int(_select(conf, "otel.otel_port"))
    return f"http://{host}:{port}/v1/traces"


def _prometheus_url(conf: DictConfig, host: str) -> str:
    if not bool(_select(conf, "prometheus.enable", default=True)):
        return ""
    port = int(_select(conf, "prometheus.prometheus_port"))
    return f"http://{host}:{port}"


def _grafana_url(conf: DictConfig, host: str) -> str:
    if not bool(_select(conf, "grafana.enable", default=True)):
        return ""
    port = int(_select(conf, "grafana.port"))
    return f"http://{host}:{port}"


def _select_str(conf: DictConfig, key: str) -> str:
    value = _select(conf, key)
    return str(value).strip() if value is not None else ""


def _select(conf: DictConfig, key: str, default: Any = None) -> Any:
    from omegaconf import OmegaConf

    return OmegaConf.select(conf, key, default=default)


if __name__ == "__main__":
    raise SystemExit(main())