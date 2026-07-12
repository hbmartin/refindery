"""Regression tests for the Prometheus Docker Compose credentials file."""

import os
import stat
import subprocess
from pathlib import Path

import yaml
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).parents[2]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
PROMETHEUS_CONFIG_FILE = PROJECT_ROOT / "monitoring" / "prometheus.yml"
TOKEN_PATH = Path("/tmp/refindery-prometheus-token")  # noqa: S108


class _PrometheusService(BaseModel):
    entrypoint: list[str]


class _ComposeServices(BaseModel):
    prometheus: _PrometheusService


class _ComposeConfig(BaseModel):
    services: _ComposeServices


class _Authorization(BaseModel):
    credentials_file: Path


class _ScrapeConfig(BaseModel):
    authorization: _Authorization


class _PrometheusConfig(BaseModel):
    scrape_configs: list[_ScrapeConfig]


def test_prometheus_token_file_is_private_and_paths_match(tmp_path: Path) -> None:
    compose = _ComposeConfig.model_validate(yaml.safe_load(COMPOSE_FILE.read_text()))
    prometheus = _PrometheusConfig.model_validate(
        yaml.safe_load(PROMETHEUS_CONFIG_FILE.read_text()),
    )
    entrypoint = compose.services.prometheus.entrypoint
    assert entrypoint[:2] == ["/bin/sh", "-c"]
    assert len(entrypoint) == 3

    command = entrypoint[2]
    credentials_file = prometheus.scrape_configs[0].authorization.credentials_file
    assert credentials_file == TOKEN_PATH
    assert str(TOKEN_PATH) in command

    token_file = tmp_path / TOKEN_PATH.name
    write_command, separator, _ = command.partition("&& exec ")
    assert separator
    write_command = write_command.replace("$${", "${").replace(
        str(TOKEN_PATH),
        str(token_file),
    )
    token = f"prometheus-{tmp_path.name}"
    subprocess.run(
        ["/bin/sh", "-c", write_command],
        check=True,
        env={**os.environ, "REFINDERY_AUTH_TOKEN": token},
    )

    assert token_file.read_text() == token
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
