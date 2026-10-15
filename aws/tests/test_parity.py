"""The AWS pipeline must keep the Azure logic so results stay comparable."""
from astutil import (assigned_value_dump, function_dump, import_dumps, shared_top_level,
                     statement_slice)
from helpers import AWS_DIR, AZURE_DIR

AZ_PHASES = AZURE_DIR / "API_based_HFace_AppInsight"
AZ_DOCKER = AZURE_DIR / "Docker-Local-Inference"
AWS_TRANSLATOR = AWS_DIR / "translator"


def test_resx_to_xlf_is_identical_to_azure():
    assert function_dump(AWS_DIR / "phase1.py", "resx_to_xlf") == function_dump(AZ_PHASES / "phase1.py", "resx_to_xlf")
