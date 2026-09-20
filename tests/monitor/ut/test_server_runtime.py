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

"""Unit tests for rendering local server runtime files."""

from __future__ import annotations

import os
import socket
import sys
from contextlib import ExitStack
from unittest.mock import MagicMock

import pytest
import requests
import yaml
from omegaconf import OmegaConf

from rl_insight.server import runtime as runtime_module
from rl_insight.server.dependencies import ServiceStatus
from rl_insight.server.services import ServerServiceManager
from rl_insight.utils.monitor_config_loader import load_server_config_file

_render_prometheus_config = runtime_module._render_prometheus_config


def _targets_file(tmp_path):
    return tmp_path / "data" / "targets" / "prometheus-targets.yml"


def _prometheus_conf(source, tmp_path):
    return OmegaConf.create(
        {
            "server": {"data_dir": str(tmp_path / "data")},
            "prometheus": {"config_file": str(source)},
        }
    )


def test_render_prometheus_config_should_reference_and_preserve_file_sd_targets(
    tmp_path,
) -> None:
    source = tmp_path / "source-prometheus.yml"
    source.write_text(
        yaml.safe_dump(
            {
                "global": {"scrape_interval": "10s"},
                "scrape_configs": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    targets_file = _targets_file(tmp_path)
    targets_file.parent.mkdir(parents=True)
    existing_targets = [
        {
            "targets": ["node-a:9100"],
            "labels": {"rl_insight_job": "node-exporter"},
        }
    ]
    targets_file.write_text(yaml.safe_dump(existing_targets), encoding="utf-8")
    conf = _prometheus_conf(source, tmp_path)

    rendered_path = _render_prometheus_config(conf, runtime_dir)
    rendered = yaml.safe_load(rendered_path.read_text(encoding="utf-8"))

    assert yaml.safe_load(targets_file.read_text(encoding="utf-8")) == existing_targets
    assert rendered["scrape_configs"] == [
        {
            "job_name": "rl-insight-dynamic",
            "file_sd_configs": [
                {
                    "files": [str(targets_file.resolve())],
                    "refresh_interval": "5s",
                }
            ],
            "relabel_configs": [
                {
                    "source_labels": ["rl_insight_job"],
                    "target_label": "job",
                },
                {"regex": "rl_insight_job", "action": "labeldrop"},
            ],
        }
    ]


def test_render_prometheus_config_should_migrate_existing_static_targets(
    tmp_path,
) -> None:
    source = tmp_path / "source-prometheus.yml"
    source.write_text(
        yaml.safe_dump({"global": {"scrape_interval": "10s"}, "scrape_configs": []}),
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    runtime_config = runtime_dir / "prometheus.yml"
    runtime_config.write_text(
        yaml.safe_dump(
            {
                "scrape_configs": [
                    {
                        "job_name": "node-exporter",
                        "static_configs": [
                            {
                                "targets": ["node-a:9100"],
                                "labels": {"node": "node-a"},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    conf = _prometheus_conf(source, tmp_path)

    _render_prometheus_config(conf, runtime_dir)

    targets_file = _targets_file(tmp_path)
    assert yaml.safe_load(targets_file.read_text(encoding="utf-8")) == [
        {
            "targets": ["node-a:9100"],
            "labels": {
                "rl_insight_job": "node-exporter",
                "node": "node-a",
            },
        }
    ]


def test_render_prometheus_config_should_move_legacy_runtime_targets_to_data(
    tmp_path,
) -> None:
    source = tmp_path / "source-prometheus.yml"
    source.write_text("scrape_configs: []\n", encoding="utf-8")
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    legacy_targets = [
        {
            "targets": ["trainer:9092"],
            "labels": {"rl_insight_job": "trainer_metrics"},
        }
    ]
    (runtime_dir / "prometheus-targets.yml").write_text(
        yaml.safe_dump(legacy_targets), encoding="utf-8"
    )

    _render_prometheus_config(_prometheus_conf(source, tmp_path), runtime_dir)

    assert (
        yaml.safe_load(_targets_file(tmp_path).read_text(encoding="utf-8"))
        == legacy_targets
    )


def test_render_prometheus_config_should_migrate_only_runtime_added_targets(
    tmp_path,
) -> None:
    source = tmp_path / "source-prometheus.yml"
    source_job = {
        "job_name": "node-exporter",
        "metrics_path": "/custom-metrics",
        "static_configs": [{"targets": ["node-a:9100"]}],
    }
    source.write_text(
        yaml.safe_dump({"scrape_configs": [source_job]}, sort_keys=False),
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "prometheus.yml").write_text(
        yaml.safe_dump(
            {
                "scrape_configs": [
                    {
                        **source_job,
                        "static_configs": [{"targets": ["node-a:9100", "node-b:9100"]}],
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    conf = _prometheus_conf(source, tmp_path)

    rendered_path = _render_prometheus_config(conf, runtime_dir)

    rendered = yaml.safe_load(rendered_path.read_text(encoding="utf-8"))
    assert rendered["scrape_configs"][0] == source_job
    profile_job = rendered["scrape_configs"][1]
    assert profile_job["metrics_path"] == "/custom-metrics"
    assert profile_job["file_sd_configs"] == [
        {
            "files": [str(_targets_file(tmp_path).resolve())],
            "refresh_interval": "5s",
        }
    ]
    assert profile_job["relabel_configs"][:3] == [
        {
            "source_labels": ["rl_insight_job"],
            "regex": "node\\-exporter",
            "action": "keep",
        },
        {"source_labels": ["rl_insight_job"], "target_label": "job"},
        {"regex": "rl_insight_job", "action": "labeldrop"},
    ]
    assert yaml.safe_load(_targets_file(tmp_path).read_text(encoding="utf-8")) == [
        {
            "targets": ["node-b:9100"],
            "labels": {"rl_insight_job": "node-exporter"},
        }
    ]


def test_render_prometheus_config_should_preserve_colliding_custom_job(
    tmp_path,
) -> None:
    source = tmp_path / "source-prometheus.yml"
    custom_job = {
        "job_name": "rl-insight-dynamic",
        "static_configs": [{"targets": ["custom:9090"]}],
    }
    source.write_text(
        yaml.safe_dump({"scrape_configs": [custom_job]}, sort_keys=False),
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    conf = _prometheus_conf(source, tmp_path)

    rendered_path = _render_prometheus_config(conf, runtime_dir)

    jobs = yaml.safe_load(rendered_path.read_text(encoding="utf-8"))["scrape_configs"]
    assert jobs[0] == custom_job
    assert [job["job_name"] for job in jobs] == [
        "rl-insight-dynamic",
        "rl-insight-dynamic-1",
        "rl-insight-dynamic-2",
    ]


def test_render_prometheus_config_should_migrate_targets_from_colliding_job(
    tmp_path,
) -> None:
    source = tmp_path / "source-prometheus.yml"
    source_job = {
        "job_name": "rl-insight-dynamic",
        "static_configs": [{"targets": ["node-a:9100"]}],
    }
    source.write_text(
        yaml.safe_dump({"scrape_configs": [source_job]}, sort_keys=False),
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "prometheus.yml").write_text(
        yaml.safe_dump(
            {
                "scrape_configs": [
                    {
                        **source_job,
                        "static_configs": [{"targets": ["node-a:9100", "node-b:9100"]}],
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    conf = _prometheus_conf(source, tmp_path)

    _render_prometheus_config(conf, runtime_dir)

    assert yaml.safe_load(_targets_file(tmp_path).read_text(encoding="utf-8")) == [
        {
            "targets": ["node-b:9100"],
            "labels": {"rl_insight_job": "rl-insight-dynamic"},
        }
    ]


def test_render_prometheus_config_should_not_leave_partial_targets_on_write_failure(
    monkeypatch, tmp_path
) -> None:
    source = tmp_path / "source-prometheus.yml"
    source.write_text("scrape_configs: []\n", encoding="utf-8")
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    targets_file = _targets_file(tmp_path)
    real_replace = os.replace

    def fail_targets_replace(source_path, target_path) -> None:
        if target_path == targets_file.resolve():
            raise OSError("simulated interrupted migration")
        real_replace(source_path, target_path)

    monkeypatch.setattr(runtime_module.os, "replace", fail_targets_replace)
    conf = _prometheus_conf(source, tmp_path)

    with pytest.raises(OSError, match="simulated interrupted migration"):
        _render_prometheus_config(conf, runtime_dir)

    assert not targets_file.exists()


@pytest.mark.parametrize("occupied_kind", ["tcp", "udp"])
def test_auto_ports_avoid_occupied_and_duplicate_ports(tmp_path, occupied_kind):
    conf = load_server_config_file()
    keys = [
        "server.port",
        "prometheus.prometheus_port",
        "tempo.query_port",
        "otel.otel_port",
        "grafana.port",
        "tempo.grpc_port",
        "tempo.memberlist_port",
    ]
    kind = socket.SOCK_STREAM if occupied_kind == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as occupied:
        if os.name == "nt":
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("0.0.0.0", 0))
        if occupied_kind == "tcp":
            occupied.listen()
        port = occupied.getsockname()[1]
        for key in keys:
            OmegaConf.update(conf, key, port)
        runtime = runtime_module.LocalServiceRuntime(conf, tmp_path, None)
        runtime._assign_ports()
        assigned = [int(OmegaConf.select(conf, key)) for key in keys]
        assert len(set(assigned)) == len(keys)
        if occupied_kind == "tcp":
            assert port not in assigned
        else:
            assert conf.tempo.memberlist_port != port
        with ExitStack() as sockets:
            for value in assigned:
                sock = sockets.enter_context(socket.socket())
                sock.bind(("0.0.0.0", value))
        rendered = runtime_module._render_tempo_config(
            conf, tmp_path, tmp_path, "2.6.1"
        )
        tempo = yaml.safe_load(rendered.read_text())
        assert tempo["server"]["grpc_listen_port"] == conf.tempo.grpc_port
        assert tempo["memberlist"]["bind_port"] == conf.tempo.memberlist_port
        assert tempo["server"]["http_listen_port"] == conf.tempo.query_port
        endpoint = tempo["distributor"]["receivers"]["otlp"]["protocols"]["http"][
            "endpoint"
        ]
        assert endpoint.endswith(f":{conf.otel.otel_port}")
        runtime_module._render_grafana_provisioning(conf, tmp_path, tmp_path)
        sources = yaml.safe_load(
            (tmp_path / "provisioning" / "datasources" / "default.yml").read_text()
        )["datasources"]
        assert sources[0]["url"].endswith(f":{conf.prometheus.prometheus_port}")
        assert sources[1]["url"].endswith(f":{conf.tempo.query_port}")


def test_auto_ports_preserve_free_port_and_skip_disabled_services(tmp_path):
    with socket.socket() as sock:
        sock.bind(("0.0.0.0", 0))
        port = sock.getsockname()[1]
    conf = OmegaConf.create(
        {
            "server": {"port": port},
            "prometheus": {"enable": False},
            "tempo": {"enable": False},
            "grafana": {"enable": False},
        }
    )
    runtime_module.LocalServiceRuntime(conf, tmp_path, None)._assign_ports()
    assert conf.server.port == port
    assert "grpc_port" not in conf.tempo


@pytest.mark.parametrize("races", [0, 1, 3])
def test_auto_port_real_server_start_retry_and_stop(tmp_path, monkeypatch, races):
    conf = load_server_config_file()
    for name in ("prometheus", "tempo", "grafana"):
        conf[name].enable = False
    conf.server.runtime_dir = str(tmp_path / "runtime")
    conf.server.state_file = str(tmp_path / "state.json")
    conf.server.data_dir = str(tmp_path / "data")
    manager = ServerServiceManager(conf, install_root=tmp_path / "install")
    original_spawn = runtime_module._spawn_service
    processes, blockers = [], []

    def spawn(name, command, log_file):
        if len(processes) < races:
            sock = socket.socket()
            if os.name == "nt":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("0.0.0.0", conf.server.port))
            sock.listen()
            blockers.append(sock)
        process = original_spawn(name, command, log_file)
        processes.append(process)
        return process

    monkeypatch.setattr(runtime_module, "_spawn_service", spawn)
    with socket.socket() as occupied:
        if os.name == "nt":
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("0.0.0.0", 0))
        occupied.listen()
        conf.server.port = occupied.getsockname()[1]
        original_port = conf.server.port
        try:
            if races == 3:
                with pytest.raises(runtime_module.PortConflictError):
                    manager.start(detach=True, attach_logs=False, auto_port=True)
                assert len(processes) == 3
                assert not manager.runtime.state_file.exists()
                assert all(p.poll() is not None for p in processes)
                return
            stack = manager.start(detach=True, attach_logs=False, auto_port=True)
            assert len(processes) == races + 1
            assert all(p.poll() is not None for p in processes[:-1])
            assert conf.server.port != original_port
            saved = OmegaConf.load(tmp_path / "runtime" / "server.yaml")
            assert saved.server.port == conf.server.port
            with requests.Session() as session:
                session.trust_env = False
                response = session.get(
                    f"http://127.0.0.1:{conf.server.port}/api/v1/services", timeout=3
                )
                assert response.status_code == 200
            assert manager.start(detach=True, attach_logs=False, auto_port=True) is None
            assert manager.stop()[0] == 0
            stack.services[0].process.wait(timeout=5)
            assert not manager.runtime.state_file.exists()
        finally:
            for process in processes:
                runtime_module._terminate_process(process)
            for sock in blockers:
                sock.close()


@pytest.mark.parametrize("old_log", [b"", b"address already in use\n"])
def test_auto_port_does_not_retry_other_startup_errors(tmp_path, monkeypatch, old_log):
    conf = load_server_config_file()
    for name in ("prometheus", "tempo", "grafana"):
        conf[name].enable = False
    conf.server.port = 0
    conf.server.runtime_dir = str(tmp_path / "runtime")
    conf.server.state_file = str(tmp_path / "state.json")
    manager = ServerServiceManager(conf, install_root=tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "rl-insight-server.log").write_bytes(old_log)
    spawn_service = runtime_module._spawn_service
    processes = []

    def spawn(name, command, log_file):
        process = spawn_service(
            name,
            [sys.executable, "-c", "raise RuntimeError('bad configuration')"],
            log_file,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(runtime_module, "_spawn_service", spawn)
    try:
        with pytest.raises(RuntimeError, match="exited during startup") as error:
            manager.start(detach=True, attach_logs=False, auto_port=True)
        assert not isinstance(error.value, runtime_module.PortConflictError)
        assert len(processes) == 1
        assert processes[0].poll() is not None
        assert not manager.runtime.state_file.exists()
    finally:
        for process in processes:
            runtime_module._terminate_process(process)


@pytest.mark.parametrize("auto_port", [False, True])
@pytest.mark.parametrize("failure", ["spawn", "state"])
def test_start_cleans_up_entire_stack_on_failure(
    tmp_path, monkeypatch, auto_port, failure
):
    conf = load_server_config_file()
    conf.server.state_file = str(tmp_path / "state.json")
    conf.server.data_dir = str(tmp_path / "data")
    dependencies = MagicMock()
    dependencies.check.return_value = [
        ServiceStatus(name=name, enabled=True, binary=tmp_path / name)
        for name in ("prometheus", "tempo", "grafana")
    ]
    dependencies.missing.return_value = []
    dependencies.enabled_services.return_value = ["prometheus", "tempo", "grafana"]
    runtime = runtime_module.LocalServiceRuntime(conf, tmp_path, dependencies)
    monkeypatch.setattr(runtime, "prepare_files", MagicMock())
    monkeypatch.setattr(runtime, "_assign_ports", MagicMock())
    monkeypatch.setattr(runtime, "_wait_ready", MagicMock())
    original_spawn = runtime_module._spawn_service
    processes = []
    names = []

    def spawn(name, command, log_file):
        names.append(name)
        if failure == "spawn" and name == "rl-insight-server":
            raise RuntimeError("simulated spawn failure")
        process = original_spawn(
            name, [sys.executable, "-c", "import time; time.sleep(120)"], log_file
        )
        processes.append(process)
        return process

    monkeypatch.setattr(runtime_module, "_spawn_service", spawn)
    if failure == "state":
        monkeypatch.setattr(
            runtime_module,
            "_write_state",
            MagicMock(side_effect=OSError("simulated state failure")),
        )
    try:
        with pytest.raises(
            (RuntimeError, OSError), match=f"simulated {failure} failure"
        ):
            runtime.start(detach=True, attach_logs=False, auto_port=auto_port)
        assert names == ["prometheus", "tempo", "grafana", "rl-insight-server"]
        assert len(processes) == (3 if failure == "spawn" else 4)
        assert all(process.poll() is not None for process in processes)
        assert not runtime.state_file.exists()
    finally:
        for process in processes:
            runtime_module._terminate_process(process)
