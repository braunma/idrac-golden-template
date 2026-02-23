#!/usr/bin/env python3
"""iDRAC Golden Template — Export and import Server Configuration Profiles via Redfish.

Usage:
    python main.py export                      Export SCP from source iDRAC(s)
    python main.py export --group NAME         Export SCP for a specific group
    python main.py import                      Import templates to all groups
    python main.py import --group NAME         Import template for a specific group
    python main.py import <file>               Import a specific file to targets (legacy)
    python main.py apply                       Export then import for all groups
    python main.py apply --group NAME          Export then import for a specific group
    python main.py validate                    Validate config and connectivity
    python main.py validate --group NAME       Validate a specific group only
    python main.py pipeline                    Run steps defined in config.yaml pipeline.steps
    python main.py pipeline --group NAME       Run configured steps for a specific group

Environment variables (override config.yaml):
    IDRAC_USERNAME      iDRAC username
    IDRAC_PASSWORD      iDRAC password
    IDRAC_SOURCE_IP     Source iDRAC IP (overrides config source.ip, legacy mode only)
    IDRAC_TARGET_IPS    Comma-separated target IPs (overrides config targets, legacy mode only)
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

    # Environment variable overrides (for CI/CD pipelines, legacy format only)
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


def resolve_groups(config: dict) -> dict[str, dict]:
    """Resolve server groups from config.

    Supports both the new 'groups' format and the legacy single source/targets format.
    Returns dict of group_name -> {source_ip, template, targets}.
    """
    if "groups" in config:
        groups = {}
        for name, group in config["groups"].items():
            groups[name] = {
                "source_ip": group.get("source_ip", ""),
                "template": group.get("template", ""),
                "targets": group.get("targets", []),
            }
        return groups

    # Legacy format: single source + targets -> implicit "default" group
    source_ip = config.get("source", {}).get("ip", "")
    targets = config.get("targets", [])
    return {
        "default": {
            "source_ip": source_ip,
            "template": "",
            "targets": targets,
        }
    }


def select_groups(all_groups: dict[str, dict], group_name: str | None) -> dict[str, dict]:
    """Filter groups by name. Returns all groups if group_name is None."""
    if group_name is None:
        return all_groups
    if group_name not in all_groups:
        available = ", ".join(sorted(all_groups.keys()))
        print(f"ERROR: Unknown group '{group_name}'. Available groups: {available}", file=sys.stderr)
        sys.exit(1)
    return {group_name: all_groups[group_name]}


def print_summary(results: dict[str, bool], group_name: str = "") -> None:
    """Print a human-readable results table."""
    print()
    header = f"RESULTS — {group_name}" if group_name else "RESULTS SUMMARY"
    print("=" * 60)
    print(header)
    print("=" * 60)
    succeeded = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    for ip, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  {ip:<20s} {status}")
    print("-" * 60)
    print(f"  Total: {len(results)}  |  Succeeded: {succeeded}  |  Failed: {failed}")
    print("=" * 60)


def _make_session(ip: str, username: str, password: str, conn: dict) -> IdracSession:
    """Create an IdracSession with connection settings from config."""
    return IdracSession(
        ip=ip,
        username=username,
        password=password,
        verify_ssl=conn.get("verify_ssl", False),
        timeout=conn.get("timeout", 30),
        retries=conn.get("retries", 3),
    )


def cmd_export(config: dict, group_name: str | None = None) -> dict[str, str]:
    """Run the export workflow for one or all groups.

    Returns dict of group_name -> exported filepath.
    """
    username, password = get_credentials()
    conn = config.get("connection", {})
    export_cfg = config.get("export", {})
    groups = select_groups(resolve_groups(config), group_name)

    exported = {}
    for name, group in groups.items():
        source_ip = group["source_ip"]
        if not source_ip:
            print(f"ERROR: No source IP configured for group '{name}'.", file=sys.stderr)
            sys.exit(1)

        print(f"\n--- Exporting group '{name}' from {source_ip} ---")
        session = _make_session(source_ip, username, password, conn)
        template_path = group.get("template", "")

        filepath = export_scp(
            session=session,
            target=export_cfg.get("target", "ALL"),
            export_format=export_cfg.get("format", "XML"),
            include=export_cfg.get("include", "Default"),
            output_dir="templates",
            output_filepath=template_path,
            poll_interval=conn.get("poll_interval", 15),
            job_timeout=conn.get("job_timeout", 1800),
        )

        exported[name] = filepath
        print(f"Exported template for group '{name}': {filepath}")

    return exported


def cmd_import(config: dict, scp_filepath: str | None = None,
               group_name: str | None = None) -> bool:
    """Run the import workflow for one or all groups.

    If scp_filepath is provided, imports that file to the group's targets.
    If scp_filepath is None, each group uses its configured template file.

    Returns True if all imports succeeded.
    """
    username, password = get_credentials()
    groups = select_groups(resolve_groups(config), group_name)

    all_ok = True
    for name, group in groups.items():
        raw_targets = group["targets"]
        if not raw_targets:
            print(f"ERROR: No target IPs configured for group '{name}'.", file=sys.stderr)
            sys.exit(1)

        targets = expand_targets(raw_targets)

        # Determine which template file to use
        file_to_import = scp_filepath or group.get("template", "")
        if not file_to_import:
            print(f"ERROR: No template file for group '{name}'.", file=sys.stderr)
            print("  Provide a file argument or set 'template' in the group config.", file=sys.stderr)
            sys.exit(1)

        if not os.path.isfile(file_to_import):
            print(f"ERROR: Template file not found for group '{name}': {file_to_import}", file=sys.stderr)
            sys.exit(1)

        print(f"\n--- Importing group '{name}' ({len(targets)} targets) from {file_to_import} ---")
        print(f"Targets: {', '.join(targets)}")

        results = import_scp_to_targets(
            targets=targets,
            username=username,
            password=password,
            scp_filepath=file_to_import,
            config=config,
        )

        print_summary(results, group_name=name)
        if not all(results.values()):
            all_ok = False

    return all_ok


def cmd_validate(config: dict, group_name: str | None = None) -> None:
    """Validate configuration and connectivity for one or all groups."""
    username, password = get_credentials()
    conn = config.get("connection", {})
    groups = select_groups(resolve_groups(config), group_name)

    all_ips: list[tuple[str, str, str]] = []
    for name, group in groups.items():
        source_ip = group["source_ip"]
        if source_ip:
            all_ips.append((name, "source", source_ip))
        raw_targets = group.get("targets", [])
        if raw_targets:
            for ip in expand_targets(raw_targets):
                all_ips.append((name, "target", ip))

    if not all_ips:
        print("ERROR: No iDRACs configured to validate.", file=sys.stderr)
        sys.exit(1)

    print(f"Validating connectivity to {len(all_ips)} iDRAC(s) ...")
    failures = 0
    for grp, role, ip in all_ips:
        try:
            session = _make_session(ip, username, password, conn)
            gen = session.initialize()
            print(f"  [{grp}] [{role:6s}] {ip:<20s} OK  (iDRAC gen {gen})")
        except Exception as exc:
            print(f"  [{grp}] [{role:6s}] {ip:<20s} FAIL ({exc})")
            failures += 1

    if failures:
        print(f"\n{failures} iDRAC(s) unreachable.")
        sys.exit(1)
    print("\nAll iDRACs reachable.")


def cmd_pipeline(config: dict, group_name: str | None = None) -> None:
    """Execute pipeline steps as configured in config.pipeline.steps.

    Steps are run in the order listed. Available steps:
      validate  — test Redfish connectivity
      export    — export SCP from source iDRAC(s)
      import    — apply committed template(s) to target iDRACs
      apply     — export + import in one shot
    """
    pipeline_cfg = config.get("pipeline", {})
    steps = pipeline_cfg.get("steps", [])

    if not steps:
        print("INFO: pipeline.steps is empty — nothing to do.")
        print("  Add steps to config.yaml, e.g.: steps: [validate, export]")
        return

    valid_steps = {"validate", "export", "import", "apply"}
    for step in steps:
        if step not in valid_steps:
            print(
                f"ERROR: Unknown pipeline step '{step}'. "
                f"Valid steps: {', '.join(sorted(valid_steps))}",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f"Pipeline steps: {' → '.join(steps)}")

    exported_files: dict[str, str] = {}
    for step in steps:
        print(f"\n{'=' * 60}")
        print(f"STEP: {step.upper()}")
        print(f"{'=' * 60}")

        if step == "validate":
            cmd_validate(config, group_name=group_name)

        elif step == "export":
            exported_files = cmd_export(config, group_name=group_name)
            print(f"\nExported {len(exported_files)} template(s).")

        elif step == "import":
            ok = cmd_import(config, group_name=group_name)
            if not ok:
                sys.exit(1)

        elif step == "apply":
            exported_files = cmd_export(config, group_name=group_name)
            all_ok = True
            for name, filepath in exported_files.items():
                ok = cmd_import(config, scp_filepath=filepath, group_name=name)
                if not ok:
                    all_ok = False
            if not all_ok:
                sys.exit(1)

    print(f"\nAll {len(steps)} pipeline step(s) completed.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="iDRAC Golden Template — Export/Import Server Configuration Profiles via Redfish",
    )
    parser.add_argument("-c", "--config", default=os.environ.get("IDRAC_CONFIG_FILE", DEFAULT_CONFIG),
                        help="Path to config YAML (default: config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("-g", "--group", default=None,
                        help="Operate on a specific server group (default: all groups)")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("export", help="Export SCP from source iDRAC(s)")

    import_parser = sub.add_parser("import", help="Import SCP to target iDRACs")
    import_parser.add_argument("file", nargs="?", default=None,
                               help="Path to SCP file (optional when groups define template paths)")

    sub.add_parser("apply", help="Export from source, then import to all targets")
    sub.add_parser("validate", help="Validate config and connectivity")
    sub.add_parser("pipeline", help="Run steps defined in config.yaml (pipeline.steps)")

    args = parser.parse_args()
    setup_logging(verbose=args.verbose)
    config = load_config(args.config)

    if args.command == "export":
        exported = cmd_export(config, group_name=args.group)
        print(f"\nExported {len(exported)} template(s).")

    elif args.command == "import":
        scp_file = getattr(args, "file", None)
        if scp_file and not os.path.isfile(scp_file):
            print(f"ERROR: File not found: {scp_file}", file=sys.stderr)
            sys.exit(1)
        ok = cmd_import(config, scp_filepath=scp_file, group_name=args.group)
        sys.exit(0 if ok else 1)

    elif args.command == "apply":
        exported = cmd_export(config, group_name=args.group)
        print()
        all_ok = True
        for name, filepath in exported.items():
            ok = cmd_import(config, scp_filepath=filepath, group_name=name)
            if not ok:
                all_ok = False
        sys.exit(0 if all_ok else 1)

    elif args.command == "validate":
        cmd_validate(config, group_name=args.group)

    elif args.command == "pipeline":
        cmd_pipeline(config, group_name=args.group)


if __name__ == "__main__":
    main()
