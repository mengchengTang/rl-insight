<p align="center">
  <img src="./assets/rl-insight-logo.png" width="180" alt="RL-Insight logo">
</p>

<h1 align="center">RL-Insight Monitor</h1>

<p align="center">
  Lightweight online monitoring for reinforcement learning training.
  Collect metrics, trace state transitions, and inspect distributed rollout / training behavior with Prometheus, Tempo, and Grafana.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#training-api">Training API</a> ·
  <a href="#server-stack">Server Stack</a> ·
  <a href="./docs/server_installation.md">Installation Guide</a>
</p>

## What Is This?

`experimental/` contains RL-Insight's online monitoring stack. It is designed for RL workloads where rollout, inference, reward, and training components run across multiple Ray workers or replicas and need to be observed together.

It provides two pieces:

- `rl-insight server ...` starts and manages a local observability stack.
- `rl_insight` exposes small Python APIs for training-side metrics and traces.

The stack uses mature open-source components instead of a custom storage backend:

- Prometheus stores counters, gauges, and histograms from training code.
- Tempo stores traces and state intervals exported through OTLP.
- Grafana provides dashboards and timeline exploration.

## Highlights

- **RL-native state tracing**: record rollout, logprob, reward, critic, and optimizer phases as timeline intervals.
- **Metric APIs with labels**: emit counters, gauges, and histograms with worker, replica, stage, or experiment labels.
- **Ray-friendly collection**: trainers send events to a detached Ray monitor hub, which exposes `/metrics` and exports traces.
- **Managed local stack**: install, start, stop, and configure Prometheus, Tempo, and Grafana from one CLI.
- **Self-hosted by default**: data is stored locally under `~/.rl-insight/data` unless configured otherwise.

## Quick Start

### 1. Install RL-Insight

Run from the repository root:

```bash
pip install -r requirements.txt
pip install -e .
```

### 2. Install The Server Services

RL-Insight can download supported Linux binaries for Prometheus, Tempo, and Grafana:

```bash
rl-insight server install
```

See [`docs/server_installation.md`](docs/server_installation.md) for Linux distribution, CPU architecture, and version details.

### 3. Start The Stack

Start Prometheus, Tempo, and Grafana in foreground mode:

```bash
rl-insight server start
```

The CLI prints the Prometheus, Tempo, Grafana, and trainer OTLP endpoints after startup. Foreground mode keeps logs attached and stops the stack when you press `Ctrl+C`.

Useful variants:

```bash
rl-insight server start --detach
rl-insight server start --attach-logs
rl-insight server start --config path/to/config.yaml
rl-insight server stop
```

### 4. Instrument Training Code

```python
import os
import ray
import rl_insight as insight

os.environ["RL_INSIGHT_SERVICE_IP"] = "<server-ip>"

ray.init(address="auto", namespace="rl-insight-monitor")
insight.init(project="verl", experiment_name="ppo-smoke-test")

insight.metric_count("train_step_total", amount=1, worker="trainer_0")
insight.metric_value("reward_mean", value=1.23, worker="trainer_0")
insight.metric_distribution("step_latency_ms", value=42.5, worker="trainer_0")

with insight.trace_state("rollout", state_lane_id="actor_0", step=10):
    run_rollout()

@insight.trace_op("update_policy", stage="optimizer")
def update_policy(batch):
    ...
```

## Training API

`init(project=None, experiment_name=None, config=None)`

Initializes the monitor client once per process. `config` may be a Python `dict` or an OmegaConf `DictConfig`. `project` and `experiment_name` are optional experiment identifiers and become global labels / trace attributes.

```python
insight.init(
    project="verl",
    experiment_name="ppo-smoke-test",
    config={
        "namespace": "verl_train",
        "prometheus": {"metrics_report_port": 9092},
    },
)
```

Metric helpers:

- `metric_count(name, amount=1.0, documentation="", **labels)` records a counter increment.
- `metric_value(name, value, documentation="", **labels)` records the latest gauge value.
- `metric_distribution(name, value, documentation="", **labels)` records one histogram sample.

Trace helpers:

- `trace_state(state_name, state_lane_id=None, **labels)` records a runtime state interval. Use a stable `state_lane_id` such as a Ray worker id, replica id, or role name to group intervals in trace UIs.
- `trace_op(name=None, extra_labels=None, **static_labels)` decorates synchronous functions and records one duration span per call.
- `finish()` resets in-process monitor state. It does not stop the detached Ray hub or server stack.

## Server Stack

`rl-insight server install`

Downloads supported service binaries into a user-managed directory. The default install location is `~/.rl-insight/services`.

Common options:

- `--install-dir`: override the service binary directory.
- `--force`: reinstall services even if binaries already exist.
- `--config`: use a custom server YAML.
- `--log-level`: set CLI logging verbosity.

`rl-insight server start`

Starts Prometheus, Tempo, and Grafana. By default, service data is persisted under `~/.rl-insight/data`, and Prometheus / Tempo retention is `30d`.

Common options:

- `--detach`: start services in the background and return immediately.
- `--attach-logs`: run in foreground and stream service logs.
- `--config`: use a custom server YAML.
- `--log-level`: set CLI logging verbosity.

`rl-insight server stop`

Stops services recorded in the state file. This does not delete persisted metrics or traces.

## Configuration

### Training-Side Config

Pass overrides through `insight.init(config=...)`:

```python
insight.init(
    project="verl",
    experiment_name="ppo-smoke-test",
    config={
        "server": {
            "namespace": "rl_insight_monitor",
            "backend": "ray",
            "service_ip": "127.0.0.1",
        },
        "prometheus": {
            "metrics_report_port": 9092,
            "prometheus_port": 9090,
        },
        "otel": {
            "otel_port": 4318,
        },
    },
)
```

Important keys:

- `server.namespace`: metric namespace and trace resource namespace.
- `server.backend`: monitor backend; currently `ray`.
- `server.service_ip`: required RL-Insight server IP used to build the OTLP trace endpoint.
- `prometheus.metrics_report_port`: monitor hub `/metrics` port.
- `prometheus.prometheus_port`: Prometheus HTTP port used for reload.
- `prometheus.config_file`: Prometheus config file to rewrite.
- `otel.otel_port`: OTLP/HTTP trace export port.

`RL_INSIGHT_SERVICE_IP` and `RL_INSIGHT_OTEL_PORT` take precedence over `config["server"]["service_ip"]` and `config["otel"]["otel_port"]`.

Training-side environment variables:

- `RL_INSIGHT_SERVICE_IP` (required): IP address printed by `rl-insight server start`; trainers use it to export traces to Tempo.
- `RL_INSIGHT_OTEL_PORT`: OTLP/HTTP port, default `4318`.
- `RL_INSIGHT_PROMETHEUS_PORT`: Prometheus HTTP port, default `9090`.
- `RL_INSIGHT_PROMETHEUS_CONFIG_FILE`: Prometheus config file to update when the monitor hub registers scrape targets.

### Server YAML

The default server config lives at [`config/services/config.yaml`](config/services/config.yaml). It controls local service paths, ports, retention, and Grafana provisioning.

Frequently changed keys:

- `server.backend`: stack startup backend, currently `local` by default.
- `server.install_dir`: optional service binary directory.
- `server.runtime_dir`: rendered Tempo/Grafana runtime config directory.
- `server.data_dir`: Prometheus, Tempo, and Grafana data directory.
- `server.state_file`: PID state file used by `server stop`.
- `prometheus.prometheus_port`: Prometheus UI and API port.
- `tempo.query_port`: Tempo query port.
- `otel.otel_port`: trainer-facing OTLP HTTP port.
- `grafana.port`: Grafana HTTP port.
- `grafana.provisioning_dir`: datasource / dashboard provisioning directory.
- `grafana.dashboards_dir`: dashboard JSON directory.

## Data And Dashboards

Grafana is provisioned with Prometheus and Tempo datasources. Dashboard JSON files can be placed under the configured dashboards directory and loaded by Grafana provisioning.

Prometheus target updates are handled by the monitor hub when `prometheus.reload.mode` is `ray`. If reload is disabled, manage Prometheus scrape configs manually.

## Status

This monitor is under `experimental/` while the APIs and server workflow evolve. Prefer the documented public entry points:

- `rl-insight server ...` for service management.
- `rl_insight.init(...)`, `metric_*`, and `trace_*` for training instrumentation.
