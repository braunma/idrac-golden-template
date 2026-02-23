#!/usr/bin/env python3
"""iDRAC Golden Template — Export and import Server Configuration Profiles via Redfish.

Usage:
    python main.py export              Export SCP from the source iDRAC
    python main.py import <file>       Import an SCP file to all target iDRACs
    python main.py apply               Export from source, then import to all targets
    python main.py validate            Validate config and connectivity only

Environment variables (override config.yaml):
    IDRAC_USERNAME      iDRAC username
    IDRAC_PASSWORD      iDRAC password
    IDRAC_SOURCE_IP     Source iDRAC IP (overrides config source.ip)
    IDRAC_TARGET_IPS    Comma-separated target IPs (overrides config targets)
    IDRAC_CONFIG_FILE   Path to config file (default: config.yaml)
"""

import argparse
import os
import sys

import yaml

from src.export_scp import export_scp
from src.idrac_common import IdracSession, expand_targets, setup_logging
from src.import_scp import import_scp_to_targets

DEFAULT_CONFIG = "config.yaml"


def load_config(config_path: str) -> dict:
    """Load and return the YAML configuration, with env-var overrides."""
    if not os.path.isfile(config_path):
        print(f"ERROR: Config file not found: {config_path}", file=sys.stderr)
        print("  Copy config.yaml.example to config.yaml and adjust values.", file=sys.stderr)
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    # Environment variable overrides (for CI/CD pipelines)
    if os.environ.get("IDRAC_SOURCE_IP"):
        config.setdefault("source", {})["ip"] = os.environ["IDRAC_SOURCE_IP"]
    if os.environ.get("IDRAC_TARGET_IPS"):
        config["targets"] = [ip.strip() for ip in os.environ["IDRAC_TARGET_IPS"].split(",")]

    return config


def get_credentials() -> tuple[str, str]:
    """Retrieve iDRAC credentials from environment variables."""
    username = os.environ.get("IDRAC_USERNAME", "")
    password = os.environ.get("IDRAC_PASSWORD", "")
    if not username or not password:
        print("ERROR: IDRAC_USERNAME and IDRAC_PASSWORD environment variables are required.", file=sys.stderr)
        print("  Set them locally or via GitLab CI/CD variables.", file=sys.stderr)
        sys.exit(1)
    return username, password


def print_summary(results: dict[str, bool]) -> None:
    """Print a human-readable results table."""
    print()
    print("=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    succeeded = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    for ip, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  {ip:<20s} {status}")
    print("-" * 60)
    print(f"  Total: {len(results)}  |  Succeeded: {succeeded}  |  Failed: {failed}")
    print("=" * 60)


def cmd_export(config: dict) -> str:
    """Run the export workflow. Returns the path to the exported file."""
    username, password = get_credentials()
    conn = config.get("connection", {})
    export_cfg = config.get("export", {})
    source_ip = config.get("source", {}).get("ip")

    if not source_ip:
        print("ERROR: No source IP configured.", file=sys.stderr)
        sys.exit(1)

    session = IdracSession(
        ip=source_ip,
        username=username,
        password=password,
        verify_ssl=conn.get("verify_ssl", False),
        timeout=conn.get("timeout", 30),
        retries=conn.get("retries", 3),
    )

    return export_scp(
        session=session,
        target=export_cfg.get("target", "ALL"),
        export_format=export_cfg.get("format", "XML"),
        include=export_cfg.get("include", "Default"),
        output_dir="templates",
        poll_interval=conn.get("poll_interval", 15),
        job_timeout=conn.get("job_timeout", 1800),
    )


def cmd_import(config: dict, scp_filepath: str) -> bool:
    """Run the import workflow. Returns True if all targets succeeded."""
    username, password = get_credentials()
    raw_targets = config.get("targets", [])

    if not raw_targets:
        print("ERROR: No target IPs configured.", file=sys.stderr)
        sys.exit(1)

    targets = expand_targets(raw_targets)
    print(f"Resolved {len(targets)} target iDRAC(s): {', '.join(targets)}")

    results = import_scp_to_targets(
        targets=targets,
        username=username,
        password=password,
        scp_filepath=scp_filepath,
        config=config,
    )

    print_summary(results)
    return all(results.values())


def cmd_validate(config: dict) -> None:
    """Validate configuration and connectivity to all iDRACs."""
    username, password = get_credentials()
    conn = config.get("connection", {})

    all_ips = []
    source_ip = config.get("source", {}).get("ip")
    if source_ip:
        all_ips.append(("source", source_ip))

    raw_targets = config.get("targets", [])
    if raw_targets:
        for ip in expand_targets(raw_targets):
            all_ips.append(("target", ip))

    if not all_ips:
        print("ERROR: No iDRACs configured to validate.", file=sys.stderr)
        sys.exit(1)

    print(f"Validating connectivity to {len(all_ips)} iDRAC(s) ...")
    failures = 0
    for role, ip in all_ips:
        try:
            session = IdracSession(
                ip=ip, username=username, password=password,
                verify_ssl=conn.get("verify_ssl", False),
                timeout=conn.get("timeout", 30),
                retries=conn.get("retries", 3),
            )
            session.check_supported()
            gen = session.detect_generation()
            print(f"  [{role:6s}] {ip:<20s} OK  (iDRAC gen {gen})")
        except Exception as exc:
            print(f"  [{role:6s}] {ip:<20s} FAIL ({exc})")
            failures += 1

    if failures:
        print(f"\n{failures} iDRAC(s) unreachable.")
        sys.exit(1)
    print("\nAll iDRACs reachable.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="iDRAC Golden Template — Export/Import Server Configuration Profiles via Redfish",
    )
    parser.add_argument("-c", "--config", default=os.environ.get("IDRAC_CONFIG_FILE", DEFAULT_CONFIG),
                        help="Path to config YAML (default: config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("export", help="Export SCP from the source iDRAC")

    import_parser = sub.add_parser("import", help="Import an SCP file to all target iDRACs")
    import_parser.add_argument("file", help="Path to the SCP XML/JSON file")

    sub.add_parser("apply", help="Export from source, then import to all targets")
    sub.add_parser("validate", help="Validate config and connectivity only")

    args = parser.parse_args()
    setup_logging(verbose=args.verbose)
    config = load_config(args.config)

    if args.command == "export":
        filepath = cmd_export(config)
        print(f"\nExported golden template: {filepath}")

    elif args.command == "import":
        if not os.path.isfile(args.file):
            print(f"ERROR: File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        ok = cmd_import(config, args.file)
        sys.exit(0 if ok else 1)

    elif args.command == "apply":
        filepath = cmd_export(config)
        print(f"\nExported golden template: {filepath}")
        print()
        ok = cmd_import(config, filepath)
        sys.exit(0 if ok else 1)

    elif args.command == "validate":
        cmd_validate(config)


if __name__ == "__main__":
    main()
