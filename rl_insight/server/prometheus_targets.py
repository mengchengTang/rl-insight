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

"""Prometheus file_sd target storage for the RL-Insight server."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from omegaconf import DictConfig, OmegaConf

from .catalog import DEFAULT_STATE_ROOT

_JOB_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_LABEL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class PrometheusTarget:
    """One scrape target plus optional labels for Prometheus file_sd."""

    target: str
    labels: Mapping[str, Any] = field(default_factory=dict)


class PrometheusTargetStore:
    """Maintain Prometheus file_sd JSON target files under one directory."""

    def __init__(self, target_dir: str | Path):
        self.target_dir = Path(target_dir).expanduser().resolve()
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, conf: DictConfig) -> "PrometheusTargetStore":
        raw = OmegaConf.select(conf, "prometheus.target_file_dir")
        target_dir = (
            Path(str(raw)).expanduser().resolve()
            if raw
            else (DEFAULT_STATE_ROOT / "runtime" / "prometheus" / "targets").resolve()
        )
        return cls(target_dir)

    def ensure_file(self, job_name: str) -> Path:
        """Create an empty file_sd target file for ``job_name`` when missing."""
        path = self.target_file_path(job_name)
        with self._lock:
            self.target_dir.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                self._write_configs(path, [])
        return path

    def register(
        self, job_name: str, targets: Sequence[PrometheusTarget]
    ) -> dict[str, Any]:
        """Upsert scrape targets into the file_sd file for ``job_name``."""
        if not targets:
            raise ValueError("at least one target is required")

        path = self.target_file_path(job_name)
        incoming = {
            self._normalize_target(item.target): self._normalize_labels(item.labels)
            for item in targets
        }

        with self._lock:
            self.target_dir.mkdir(parents=True, exist_ok=True)
            existing = self._read_target_map(path)
            existing.update(incoming)
            self._write_configs(path, self._to_file_sd_configs(existing))

        return {
            "job_name": job_name,
            "target_count": len(existing),
            "target_file": str(path),
        }

    def target_file_path(self, job_name: str) -> Path:
        value = str(job_name).strip()
        if not value:
            raise ValueError("job_name is required")
        if not _JOB_NAME_RE.match(value):
            raise ValueError(f"invalid Prometheus job_name: {job_name!r}")
        return (self.target_dir / f"{value}.json").resolve()

    def _read_target_map(self, path: Path) -> dict[str, dict[str, str]]:
        target_map: dict[str, dict[str, str]] = {}
        for config in self._read_configs(path):
            if not isinstance(config, dict):
                continue
            raw_targets = config.get("targets") or []
            if not isinstance(raw_targets, list):
                continue
            labels = self._normalize_labels(config.get("labels") or {})
            for target in raw_targets:
                target_map[self._normalize_target(target)] = labels
        return target_map

    @staticmethod
    def _normalize_target(target: Any) -> str:
        value = str(target).strip()
        if not value:
            raise ValueError("target is required")
        if "://" in value or "/" in value:
            raise ValueError(f"target must be host:port, got {target!r}")
        host, separator, port = value.rpartition(":")
        if not separator or not host or not port.isdigit():
            raise ValueError(f"target must be host:port, got {target!r}")
        port_number = int(port)
        if port_number <= 0 or port_number > 65535:
            raise ValueError(f"target port out of range: {target!r}")
        return f"{host}:{port_number}"

    @staticmethod
    def _normalize_labels(labels: Mapping[str, Any] | None) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in dict(labels or {}).items():
            name = str(key).strip()
            if not name:
                raise ValueError("label name cannot be empty")
            if not _LABEL_NAME_RE.match(name):
                raise ValueError(f"invalid Prometheus label name: {key!r}")
            normalized[name] = str(value)
        return normalized

    @staticmethod
    def _to_file_sd_configs(
        targets: Mapping[str, Mapping[str, str]]
    ) -> list[dict[str, Any]]:
        configs: list[dict[str, Any]] = []
        for target, labels in sorted(targets.items()):
            config: dict[str, Any] = {"targets": [target]}
            if labels:
                config["labels"] = dict(sorted(labels.items()))
            configs.append(config)
        return configs

    @staticmethod
    def _read_configs(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid Prometheus target file: {path}") from exc
        if not isinstance(data, list):
            raise ValueError(f"Prometheus target file must contain a JSON list: {path}")
        return data

    @staticmethod
    def _write_configs(path: Path, configs: Sequence[Mapping[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(list(configs), indent=2, sort_keys=True)
        tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp_path.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
