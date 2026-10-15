import re

from helpers import AWS_DIR, AZURE_DIR, REPO_ROOT

LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def test_readme_links_resolve():
    for readme in (REPO_ROOT / "README.md", AWS_DIR / "README.md", AZURE_DIR / "README.md"):
        text = readme.read_text(encoding="utf-8")
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://")):
                continue
            assert (readme.parent / target).exists(), f"{readme.relative_to(REPO_ROOT)} → {target}"


def test_aws_readme_documents_the_essential_commands():
    text = (AWS_DIR / "README.md").read_text(encoding="utf-8")
    for command in ("aws/infra/deploy.sh", "aws/infra/teardown.sh", "start-pipeline-execution",
                    "export_telemetry.py", "RESET_TM"):
        assert command in text, command


def test_aws_readme_says_which_branch_to_deploy_before_merge():
    # The pipeline builds GITHUB_BRANCH (default main); before aws/ is merged, main has no buildspecs.
    text = (AWS_DIR / "README.md").read_text(encoding="utf-8")
    assert "merged into `main`" in text
    assert "GITHUB_BRANCH=" in text


def test_aws_readme_explains_retry_and_custom_log_group():
    text = (AWS_DIR / "README.md").read_text(encoding="utf-8")
    assert "**Retry**" in text and "same run ID" in text
    assert "--log-group" in text
