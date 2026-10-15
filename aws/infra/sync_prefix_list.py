"""Keep the database firewall prefix list in sync with CodeBuild's published IP ranges."""
import argparse
import ipaddress
import json
import sys
import time
import urllib.request

import boto3

IP_RANGES_URL = "https://ip-ranges.amazonaws.com/ip-ranges.json"


def codebuild_cidrs(ip_ranges, region):
    return sorted({
        prefix["ip_prefix"]
        for prefix in ip_ranges.get("prefixes", [])
        if prefix.get("service") == "CODEBUILD" and prefix.get("region") == region
    })


def normalize_cidr(value):
    return str(ipaddress.ip_network(value, strict=False))


def plan_changes(current, desired):
    current, desired = set(current), set(desired)
    return sorted(desired - current), sorted(current - desired)


def _describe(client, prefix_list_id):
    return client.describe_managed_prefix_lists(PrefixListIds=[prefix_list_id])["PrefixLists"][0]


def _current_cidrs(client, prefix_list_id):
    cidrs, kwargs = [], {"PrefixListId": prefix_list_id}
    while True:
        resp = client.get_managed_prefix_list_entries(**kwargs)
        cidrs.extend(entry["Cidr"] for entry in resp.get("Entries", []))
        token = resp.get("NextToken")
        if not token:
            return cidrs
        kwargs["NextToken"] = token


def sync(client, prefix_list_id, desired, poll_seconds=2, max_polls=60):
    desired = sorted(set(desired))
    info = _describe(client, prefix_list_id)
    if len(desired) > info["MaxEntries"]:
        raise ValueError(f"{len(desired)} CIDRs needed but the prefix list holds at most {info['MaxEntries']}")
    add, remove = plan_changes(_current_cidrs(client, prefix_list_id), desired)
    if not add and not remove:
        return add, remove

    request = {"PrefixListId": prefix_list_id, "CurrentVersion": info["Version"]}
    if add:
        request["AddEntries"] = [{"Cidr": cidr, "Description": "localization pipeline database client"} for cidr in add]
    if remove:
        request["RemoveEntries"] = [{"Cidr": cidr} for cidr in remove]
    client.modify_managed_prefix_list(**request)

    for _ in range(max_polls):
        state = _describe(client, prefix_list_id)["State"]
        if state.endswith("-complete"):
            return add, remove
        if state.endswith("-failed"):
            raise RuntimeError(f"prefix list update failed: {state}")
        time.sleep(poll_seconds)
    raise TimeoutError("prefix list update did not finish in time")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--prefix-list-id", required=True)
    parser.add_argument("--extra-cidr", action="append", default=[],
                        help="Additional CIDR allowed to reach the database (repeatable).")
    args = parser.parse_args(argv)

    with urllib.request.urlopen(IP_RANGES_URL, timeout=30) as resp:
        cidrs = codebuild_cidrs(json.load(resp), args.region)
    if not cidrs:
        print(f"❌ AWS publishes no CODEBUILD IP ranges for {args.region}", file=sys.stderr)
        return 1
    desired = cidrs + [normalize_cidr(cidr) for cidr in args.extra_cidr]

    add, remove = sync(boto3.client("ec2", region_name=args.region), args.prefix_list_id, desired)
    print(f"🔐 Database firewall: {len(desired)} allowed ranges (+{len(add)} / -{len(remove)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
