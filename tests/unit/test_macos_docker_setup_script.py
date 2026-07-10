"""Tests for the Docker-based macOS setup script."""

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup-macos-docker.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def test_docker_setup_script_has_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", SETUP_SCRIPT], check=True)


def test_docker_setup_is_idempotent_and_starts_stack(tmp_path: Path) -> None:
    project = tmp_path / "project"
    scripts = project / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir(parents=True)
    fake_bin.mkdir()

    copied_setup = scripts / SETUP_SCRIPT.name
    _write_executable(copied_setup, SETUP_SCRIPT.read_text())
    (project / "docker-compose.yml").write_text("services: {}\n")
    _write_executable(fake_bin / "uname", "#!/usr/bin/env bash\necho Darwin\n")
    _write_executable(
        fake_bin / "openssl",
        "#!/usr/bin/env bash\necho generated-docker-auth-token\n",
    )
    docker_log = tmp_path / "docker.log"
    _write_executable(
        fake_bin / "docker",
        f"""#!/usr/bin/env bash
echo "$*" >> "{docker_log}"
if [[ "$1" == "info" ]]; then
    exit 0
fi
if [[ "$1 $2" == "compose version" ]]; then
    exit 0
fi
if [[ "$1" == "compose" ]]; then
    exit 0
fi
exit 1
""",
    )
    _write_executable(fake_bin / "curl", "#!/usr/bin/env bash\nexit 0\n")

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["VOYAGE_API_KEY"] = "first-provider-key"
    subprocess.run([copied_setup], check=True, cwd=project, env=env)

    first_env = (project / ".env.docker").read_text()
    assert "REFINDERY_AUTH_TOKEN=generated-docker-auth-token" in first_env
    assert "REFINDERY_VECTOR_STORE=qdrant" in first_env
    assert "REFINDERY_QDRANT__URL=http://qdrant:6333" in first_env
    assert "REFINDERY_RERANKER__PROVIDER=voyage" in first_env
    assert "REFINDERY_RERANKER__MODEL=rerank-2.5" in first_env
    assert "REFINDERY_ENTITY__EXTRACTOR_CHAIN='[\"spacy\"]'" in first_env
    assert "VOYAGE_API_KEY=first-provider-key" in first_env

    env["VOYAGE_API_KEY"] = "replacement-provider-key"
    subprocess.run([copied_setup], check=True, cwd=project, env=env)

    second_env = (project / ".env.docker").read_text()
    assert "REFINDERY_AUTH_TOKEN=generated-docker-auth-token" in second_env
    assert "VOYAGE_API_KEY=replacement-provider-key" in second_env
    for key in (
        "REFINDERY_AUTH_TOKEN",
        "REFINDERY_VECTOR_STORE",
        "REFINDERY_QDRANT__URL",
        "REFINDERY_RERANKER__PROVIDER",
        "REFINDERY_RERANKER__MODEL",
        "REFINDERY_ENTITY__EXTRACTOR_CHAIN",
        "VOYAGE_API_KEY",
    ):
        assert second_env.count(f"{key}=") == 1

    docker_calls = docker_log.read_text()
    assert f"compose --env-file {project}/.env.docker config --quiet" in docker_calls
    start_call = f"compose --env-file {project}/.env.docker up --detach --build"
    assert start_call in docker_calls
    assert docker_calls.count("info") == 2
