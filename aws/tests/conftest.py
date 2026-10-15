import sys
import types

import boto3
import pytest
from moto import mock_aws

from helpers import BUCKET, REGION


@pytest.fixture
def aws_env(monkeypatch):
    """Fake credentials and region so nothing can reach a real AWS account."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
    }.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def s3(aws_env):
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        yield client


@pytest.fixture
def stub_modules(monkeypatch):
    """Install fake modules, e.g. stub_modules(pyodbc={"connect": fn})."""
    def install(**modules):
        for name, attrs in modules.items():
            module = types.ModuleType(name)
            for attr, value in attrs.items():
                setattr(module, attr, value)
            monkeypatch.setitem(sys.modules, name, module)
    return install
