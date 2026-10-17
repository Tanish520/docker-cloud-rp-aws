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


def test_database_firewall_allows_only_codebuild_ranges_and_the_ec2_runner():
    rules = resources()["DbSecurityGroup"]["Properties"]["SecurityGroupIngress"]
    assert all(r["FromPort"] == 1433 and r["ToPort"] == 1433 for r in rules)
    sources = [r.get("SourcePrefixListId") or r.get("SourceSecurityGroupId") for r in rules]
    assert sources == [{"Ref": "DbPrefixList"}, {"GetAtt": "RunnerSecurityGroup.GroupId"}]
    assert not any("CidrIp" in r for r in rules)


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
    assert variable["Name"] == "RESET_TM" and variable["DefaultValue"] == "false"
    for stage in stages[1:]:
        assert action_env(stage)["RUN_ID"] == "#{codepipeline.PipelineExecutionId}"
    assert action_env(stages[2])["RESET_TM"] == "#{variables.RESET_TM}"


def test_buckets_clean_up_incomplete_multipart_uploads():
    for name in ("DataBucket", "ArtifactBucket"):
        rules = resources()[name]["Properties"]["LifecycleConfiguration"]["Rules"]
        assert any(r.get("AbortIncompleteMultipartUpload", {}).get("DaysAfterInitiation") == 1 for r in rules), name


def test_codebuild_can_abort_its_own_multipart_uploads():
    statements = resources()["CodeBuildRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    run_files = next(s for s in statements if s["Sid"] == "RunFilesAndModelCache")
    assert "s3:AbortMultipartUpload" in run_files["Action"]


def runner_launch_data():
    return resources()["RunnerLaunchTemplate"]["Properties"]["LaunchTemplateData"]


def test_runner_is_a_two_vcpu_amazon_linux_machine_that_terminates_itself():
    data = runner_launch_data()
    assert template()["Parameters"]["RunnerInstanceType"]["Default"] == "m5.large"
    assert data["InstanceType"] == {"Ref": "RunnerInstanceType"}
    assert data["ImageId"] == "{{resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64}}"
    assert data["InstanceInitiatedShutdownBehavior"] == "terminate"
    assert data["MetadataOptions"]["HttpTokens"] == "required"
    assert data["MetadataOptions"]["HttpPutResponseHopLimit"] == 2  # containers can reach the instance role
    [disk] = data["BlockDeviceMappings"]
    assert disk["Ebs"]["VolumeSize"] == 40 and disk["Ebs"]["VolumeType"] == "gp3"
    assert data["SecurityGroupIds"] == [{"GetAtt": "RunnerSecurityGroup.GroupId"}]


def test_runner_bootstrap_installs_docker_and_has_a_safety_shutdown():
    user_data = runner_launch_data()["UserData"]["Base64"]
    assert "shutdown -h +240" in user_data
    assert "dnf install -y docker" in user_data
    assert "/opt/venv" in user_data and "boto3" in user_data


def test_runner_role_can_use_ssm_the_data_bucket_the_db_secret_and_its_logs():
    role = resources()["RunnerRole"]["Properties"]
    assert role["AssumeRolePolicyDocument"]["Statement"][0]["Principal"]["Service"] == "ec2.amazonaws.com"
    assert any("AmazonSSMManagedInstanceCore" in str(arn) for arn in role["ManagedPolicyArns"])
    statements = role["Policies"][0]["PolicyDocument"]["Statement"]
    actions = {a for s in statements for a in (s["Action"] if isinstance(s["Action"], list) else [s["Action"]])}
    assert {"s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListBucket",
            "secretsmanager:GetSecretValue", "logs:CreateLogStream", "logs:PutLogEvents"} <= actions


def test_runner_log_group_and_outputs():
    assert resources()["RunnerLogGroup"]["Properties"]["RetentionInDays"] == 90
    assert {"RunnerLaunchTemplateId", "RunnerLogGroupName"} <= set(template()["Outputs"])
