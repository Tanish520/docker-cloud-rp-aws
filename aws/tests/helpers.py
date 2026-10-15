"""Paths and small helpers shared by the AWS test suite."""
import importlib.util
from pathlib import Path

AWS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = AWS_DIR.parent
AZURE_DIR = REPO_ROOT / "azure"

BUCKET = "test-bucket"
REGION = "ap-south-1"


def load_module(name, path):
    """Import a file as a fresh module (used for modules that do work at import time)."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_resx(path, values):
    """Write a minimal .resx file whose <data> entries hold the given strings."""
    items = "".join(
        f'<data name="S{i}" xml:space="preserve"><value>{value}</value></data>'
        for i, value in enumerate(values)
    )
    path.write_text(f'<?xml version="1.0" encoding="utf-8"?><root>{items}</root>', encoding="utf-8")


def forbidden_connect(*args, **kwargs):
    raise AssertionError("tests must not open a real database connection")
