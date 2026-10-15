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


def test_model_key_and_converter_pins_match_the_image():
    ct2, transformers = pinned("ctranslate2"), pinned("transformers")
    assert load("phase2.yml")["env"]["variables"]["MODEL_KEY"] == f"nllb-200-distilled-600M_int8_ct2-{ct2}"
    script = RUNNER.read_text(encoding="utf-8")
    assert f'"ctranslate2=={ct2}"' in script
    assert f'"transformers=={transformers}"' in script


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
