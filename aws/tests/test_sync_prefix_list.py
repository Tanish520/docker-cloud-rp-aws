import pytest

import sync_prefix_list as spl

IP_RANGES = {
    "prefixes": [
        {"ip_prefix": "13.0.0.0/28", "region": "ap-south-1", "service": "CODEBUILD"},
        {"ip_prefix": "13.0.0.0/28", "region": "ap-south-1", "service": "CODEBUILD"},
        {"ip_prefix": "15.0.0.0/28", "region": "ap-south-1", "service": "CODEBUILD"},
        {"ip_prefix": "99.0.0.0/28", "region": "us-east-1", "service": "CODEBUILD"},
        {"ip_prefix": "13.1.0.0/16", "region": "ap-south-1", "service": "EC2"},
    ]
}


class FakeEc2:
    def __init__(self, entries, max_entries=20, version=3):
        self.entries = list(entries)
        self.max_entries = max_entries
        self.version = version
        self.modifications = []

    def describe_managed_prefix_lists(self, PrefixListIds):
        return {"PrefixLists": [{"PrefixListId": PrefixListIds[0], "MaxEntries": self.max_entries,
                                 "Version": self.version, "State": "modify-complete"}]}

    def get_managed_prefix_list_entries(self, PrefixListId, **kwargs):
        return {"Entries": [{"Cidr": cidr} for cidr in self.entries]}

    def modify_managed_prefix_list(self, **kwargs):
        self.modifications.append(kwargs)
        return {"PrefixList": {"State": "modify-in-progress"}}


def test_codebuild_cidrs_filters_by_service_and_region():
    assert spl.codebuild_cidrs(IP_RANGES, "ap-south-1") == ["13.0.0.0/28", "15.0.0.0/28"]


def test_normalize_cidr():
    assert spl.normalize_cidr("203.0.113.5") == "203.0.113.5/32"
    assert spl.normalize_cidr("203.0.113.0/24") == "203.0.113.0/24"
    with pytest.raises(ValueError):
        spl.normalize_cidr("not-an-ip")


def test_plan_changes():
    assert spl.plan_changes(["a", "b"], ["b", "c"]) == (["c"], ["a"])


def test_sync_adds_and_removes_with_current_version():
    ec2 = FakeEc2(["1.1.1.0/24", "9.9.9.0/24"])
    assert spl.sync(ec2, "pl-1", ["1.1.1.0/24", "2.2.2.0/24"], poll_seconds=0) == (["2.2.2.0/24"], ["9.9.9.0/24"])
    [request] = ec2.modifications
    assert request["PrefixListId"] == "pl-1"
    assert request["CurrentVersion"] == 3
    assert [e["Cidr"] for e in request["AddEntries"]] == ["2.2.2.0/24"]
    assert request["RemoveEntries"] == [{"Cidr": "9.9.9.0/24"}]


def test_sync_does_nothing_when_already_in_sync():
    ec2 = FakeEc2(["1.1.1.0/24"])
    assert spl.sync(ec2, "pl-1", ["1.1.1.0/24"], poll_seconds=0) == ([], [])
    assert ec2.modifications == []


def test_sync_refuses_more_cidrs_than_the_list_holds():
    ec2 = FakeEc2([], max_entries=1)
    with pytest.raises(ValueError):
        spl.sync(ec2, "pl-1", ["1.1.1.0/24", "2.2.2.0/24"], poll_seconds=0)
