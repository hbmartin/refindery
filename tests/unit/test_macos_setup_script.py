"""Tests for the Homebrew-based macOS setup script."""

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup-macos.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def test_setup_script_has_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", SETUP_SCRIPT], check=True)


def test_setup_script_is_idempotent_with_homebrew_tools(tmp_path: Path) -> None:
    project = tmp_path / "project"
    scripts = project / "scripts"
    fake_bin = tmp_path / "bin"
    python_prefix = tmp_path / "python"
    uv_prefix = tmp_path / "uv"
    for directory in (scripts, fake_bin, python_prefix / "bin", uv_prefix / "bin"):
        directory.mkdir(parents=True, exist_ok=True)

    copied_setup = scripts / SETUP_SCRIPT.name
    _write_executable(copied_setup, SETUP_SCRIPT.read_text())
    _write_executable(fake_bin / "uname", "#!/usr/bin/env bash\necho Darwin\n")
    _write_executable(
        fake_bin / "brew",
        f"""#!/usr/bin/env bash
if [[ "$1" == "install" ]]; then
    exit 0
fi
if [[ "$1" == "--prefix" && "$2" == "python@3.13" ]]; then
    echo "{python_prefix}"
    exit 0
fi
if [[ "$1" == "--prefix" && "$2" == "uv" ]]; then
    echo "{uv_prefix}"
    exit 0
fi
exit 1
""",
    )
    _write_executable(
        python_prefix / "bin" / "python3.13",
        "#!/usr/bin/env bash\necho generated-auth-token\n",
    )
    uv_log = tmp_path / "uv.log"
    _write_executable(
        uv_prefix / "bin" / "uv",
        f'#!/usr/bin/env bash\necho "$*" >> "{uv_log}"\n',
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["VOYAGE_API_KEY"] = "first-provider-key"
    subprocess.run([copied_setup], check=True, cwd=project, env=env)

    first_env = (project / ".env").read_text()
    assert "REFINDERY_AUTH_TOKEN=generated-auth-token" in first_env
    assert "REFINDERY_VECTOR_STORE=lancedb" in first_env
    assert "REFINDERY_RERANKER__PROVIDER=voyage" in first_env
    assert "REFINDERY_RERANKER__MODEL=rerank-2.5" in first_env
    assert "VOYAGE_API_KEY=first-provider-key" in first_env

    env["VOYAGE_API_KEY"] = "replacement-provider-key"
    subprocess.run([copied_setup], check=True, cwd=project, env=env)

    second_env = (project / ".env").read_text()
    assert "REFINDERY_AUTH_TOKEN=generated-auth-token" in second_env
    assert "VOYAGE_API_KEY=replacement-provider-key" in second_env
    for key in (
        "REFINDERY_AUTH_TOKEN",
        "REFINDERY_VECTOR_STORE",
        "REFINDERY_RERANKER__PROVIDER",
        "REFINDERY_RERANKER__MODEL",
        "VOYAGE_API_KEY",
    ):
        assert second_env.count(f"{key}=") == 1

    uv_calls = uv_log.read_text()
    assert (
        f"sync --python {python_prefix}/bin/python3.13 --locked --extra ner" in uv_calls
    )
    assert f"run --env-file {project}/.env python -" in uv_calls
    assert "docker" not in uv_calls.lower()
