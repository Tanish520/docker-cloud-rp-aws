import json
import subprocess
import sys
from pathlib import Path

import yaml

from helpers import AWS_DIR, REPO_ROOT

TEMPLATE = AWS_DIR / "infra" / "pipeline.yaml"
CFN_LINT = Path(sys.executable).parent / "cfn-lint"


class CfnLoader(yaml.SafeLoader):
    """Reads CloudFormation short-form tags (!Ref, !Sub, ...) as {"Ref": value} etc."""


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {suffix: value}


CfnLoader.add_multi_constructor("!", _tag)


def template():
    return yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=CfnLoader)


def resources():
    return template()["Resources"]


def env_vars(project_name):
    return {v["Name"]: v for v in resources()[project_name]["Properties"]["Environment"]["EnvironmentVariables"]}


def action_env(stage):
    return {v["name"]: v["value"] for v in json.loads(stage["Actions"][0]["Configuration"]["EnvironmentVariables"])}


def test_template_passes_cfn_lint():
    result = subprocess.run([str(CFN_LINT), str(TEMPLATE)], capture_output=True, text=True)
    assert result.returncode in (0, 4, 8, 12), result.stdout + result.stderr  # no error bit (2), no crash (1)


def test_template_does_not_hardcode_a_region():
    assert "ap-south-1" not in TEMPLATE.read_text(encoding="utf-8")


def test_every_buildspec_exists():
    for name in ("Phase1Project", "Phase2Project", "Phase3Project"):
        buildspec = resources()[name]["Properties"]["Source"]["BuildSpec"]
        assert (REPO_ROOT / buildspec).is_file(), buildspec


def test_phase2_runs_docker_on_medium_with_database_secrets():
    environment = resources()["Phase2Project"]["Properties"]["Environment"]
    assert environment["PrivilegedMode"] is True
    assert environment["ComputeType"] == "BUILD_GENERAL1_MEDIUM"
    env = env_vars("Phase2Project")
    assert env["DB_USER"]["Type"] == "SECRETS_MANAGER"
    assert env["DB_USER"]["Value"]["Sub"].endswith(":username")
    assert env["DB_PASSWORD"]["Type"] == "SECRETS_MANAGER"
    assert env["DB_PASSWORD"]["Value"]["Sub"].endswith(":password")
    assert env["PHASE2_CPUSET"]["Value"] == {"Ref": "Phase2CpuSet"}
    assert template()["Parameters"]["Phase2CpuSet"]["Default"] == "0,1"


def test_phases_1_and_3_are_small_and_unprivileged():
    for name in ("Phase1Project", "Phase3Project"):
        environment = resources()[name]["Properties"]["Environment"]
        assert environment["ComputeType"] == "BUILD_GENERAL1_SMALL"
        assert not environment.get("PrivilegedMode", False)


def test_database_is_disposable_sql_server_express():
    database = resources()["Database"]
    assert database["DeletionPolicy"] == "Delete"
    assert database["UpdateReplacePolicy"] == "Delete"
    props = database["Properties"]
    assert props["Engine"] == "sqlserver-ex"
    assert props["ManageMasterUserPassword"] is True
    assert props["PubliclyAccessible"] is True
    assert "MasterUserPassword" not in props


def test_database_firewall_only_allows_the_prefix_list():
    [rule] = resources()["DbSecurityGroup"]["Properties"]["SecurityGroupIngress"]
    assert rule["FromPort"] == 1433 and rule["ToPort"] == 1433
    assert rule["SourcePrefixListId"] == {"Ref": "DbPrefixList"}
    assert "CidrIp" not in rule


def test_buckets_block_public_access():
    for name in ("DataBucket", "ArtifactBucket"):
        block = resources()[name]["Properties"]["PublicAccessBlockConfiguration"]
        assert all(block.values()), name


def test_pipeline_is_manual_with_four_stages_and_shared_run_id():
    pipeline = resources()["Pipeline"]["Properties"]
    assert pipeline["PipelineType"] == "V2"
    stages = pipeline["Stages"]
    assert [s["Name"] for s in stages] == ["Source", "Phase1", "Phase2", "Phase3"]
    assert stages[0]["Actions"][0]["Configuration"]["DetectChanges"] == "false"
    [variable] = pipeline["Variables"]
    assert variable["Name"] == "RESET_TM" and variable["DefaultValue"] == "true"
    for stage in stages[1:]:
        assert action_env(stage)["RUN_ID"] == "#{codepipeline.PipelineExecutionId}"
    assert action_env(stages[2])["RESET_TM"] == "#{variables.RESET_TM}"
