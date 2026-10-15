import json
import re
import subprocess

import yaml

from helpers import AWS_DIR, REPO_ROOT

BUILDSPECS = AWS_DIR / "buildspecs"
RUNNER = AWS_DIR / "scripts" / "run_phase2.sh"


def load(name):
    return yaml.safe_load((BUILDSPECS / name).read_text(encoding="utf-8"))


def pinned(package):
    requirements = (AWS_DIR / "translator" / "requirements.txt").read_text(encoding="utf-8")
    return re.search(rf"^{package}==([\w.]+)", requirements, re.M).group(1)


def test_buildspecs_use_bash():
    for name in ("phase1.yml", "phase2.yml", "phase3.yml"):
        spec = load(name)
        assert spec["version"] == 0.2
        assert spec["env"]["shell"] == "bash"


def test_files_referenced_by_buildspecs_exist():
    for name in ("phase1.yml", "phase2.yml", "phase3.yml"):
        text = (BUILDSPECS / name).read_text(encoding="utf-8")
        paths = re.findall(r"(aws/[\w/.-]+\.(?:py|sh|json))", text)
        assert paths, name
        for path in paths:
            assert (REPO_ROOT / path).is_file(), f"{name}: {path}"


def test_phase3_artifact_matches_configured_output_folder():
    config = json.loads((AWS_DIR / "config.json").read_text(encoding="utf-8"))
    assert load("phase3.yml")["artifacts"]["files"] == [f"{config['TargetResxPath']}/**/*"]


def test_model_key_and_converter_ctranslate2_match_the_image():
    # ctranslate2 sets the converted model's format, so the converter must use the image's version.
    ct2 = pinned("ctranslate2")
    assert load("phase2.yml")["env"]["variables"]["MODEL_KEY"] == f"nllb-200-distilled-600M_int8_ct2-{ct2}"
    assert f'"ctranslate2=={ct2}"' in RUNNER.read_text(encoding="utf-8")


def test_converter_transformers_supports_the_dtype_argument_used_by_ctranslate2():
    # ctranslate2 4.7's converter calls from_pretrained(..., dtype=...), which needs transformers >= 4.56.
    version = re.search(r'"transformers==([\d.]+)"', RUNNER.read_text(encoding="utf-8")).group(1)
    assert tuple(int(part) for part in version.split(".")[:2]) >= (4, 56)


def test_translation_runs_on_pinned_cores_with_exit_code_kept():
    script = RUNNER.read_text(encoding="utf-8")
    assert 'PHASE2_CPUSET="${PHASE2_CPUSET:-0,1}"' in script
    assert '--cpuset-cpus="$PHASE2_CPUSET"' in script
    assert "set -euo pipefail" in script


def test_shell_scripts_parse():
    scripts = sorted(p for p in AWS_DIR.rglob("*.sh") if ".venv" not in p.parts)
    assert scripts
    for script in scripts:
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_model_converter_uses_the_same_base_image_as_the_translator():
    dockerfile = (AWS_DIR / "translator" / "Dockerfile").read_text(encoding="utf-8")
    image = re.search(r"^FROM (\S+)", dockerfile, re.M).group(1)
    assert f'BASE_IMAGE="{image}"' in RUNNER.read_text(encoding="utf-8")
    assert image.endswith("-bookworm")


def test_converter_takes_cpu_torch_from_the_pytorch_index_and_everything_else_from_pypi():
    # --index-url would make the PyTorch index the only source, and some torch dependencies are not
    # installable from it; pin the exact CPU build and add the PyTorch index as an extra source.
    script = RUNNER.read_text(encoding="utf-8")
    assert '"torch==2.14.1+cpu" --extra-index-url https://download.pytorch.org/whl/cpu' in script
    assert "--index-url https://download.pytorch.org" not in script
