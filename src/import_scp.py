"""Import iDRAC Server Configuration Profile (SCP) to one or more targets.

Based on Dell ImportSystemConfigurationLocalFilenameREDFISH.py:
https://github.com/dell/iDRAC-Redfish-Scripting/
"""

import logging
import re
import sys

from src.idrac_common import IdracSession

logger = logging.getLogger("idrac")


def import_scp(session: IdracSession, scp_filepath: str, target: str = "ALL",
               shutdown_type: str = "Graceful", host_power_state: str = "On",
               poll_interval: int = 15, job_timeout: int = 1800) -> bool:
    """Import a Server Configuration Profile to a single iDRAC.

    Args:
        session: Authenticated IdracSession.
        scp_filepath: Path to the SCP XML/JSON file to import.
        target: Components to import (ALL, BIOS, IDRAC, NIC, RAID, etc.).
        shutdown_type: Graceful, Forced, or NoReboot.
        host_power_state: On or Off after import.
        poll_interval: Seconds between job polls.
        job_timeout: Max seconds to wait for import job.

    Returns:
        True if import completed successfully, False otherwise.
    """
    session.check_supported()
    session.detect_generation()

    logger.info("--- IMPORT SCP TO %s ---", session.ip)
    logger.info("  Source file     : %s", scp_filepath)
    logger.info("  Target          : %s", target)
    logger.info("  Shutdown type   : %s", shutdown_type)
    logger.info("  Host power state: %s", host_power_state)

    # Read and prepare the SCP file content
    scp_content = _read_scp_file(scp_filepath)
    if not scp_content:
        logger.error("  SCP file is empty or unreadable: %s", scp_filepath)
        return False

    uri = session.oem_action_uri("ImportSystemConfiguration")

    payload = {
        "ImportBuffer": scp_content,
        "ShutdownType": shutdown_type,
        "HostPowerState": host_power_state,
        "ShareParameters": {
            "Target": target,
        },
    }

    logger.info("  POST %s", uri)
    resp = session.post(uri, json=payload)

    if resp.status_code not in (200, 202):
        logger.error("  Import request failed on %s: HTTP %d", session.ip, resp.status_code)
        logger.error("  Response: %s", resp.text[:500])
        return False

    # Extract job ID
    job_uri = resp.headers.get("Location", "")
    if not job_uri:
        logger.error("  No job URI returned from import request on %s", session.ip)
        return False

    job_id = job_uri.split("/")[-1]
    logger.info("  Import job created: %s", job_id)

    # Poll until completion
    task_data = session.poll_job(job_id, poll_interval=poll_interval, job_timeout=job_timeout)

    task_state = task_data.get("TaskState", "Unknown")
    messages = task_data.get("Messages", [])
    msg_text = "; ".join(m.get("Message", "") for m in messages if m.get("Message"))

    if task_state == "Completed":
        logger.info("  Import to %s SUCCEEDED: %s", session.ip, msg_text)
        logger.info("--- IMPORT COMPLETE ---")
        return True

    logger.error("  Import to %s FAILED (state: %s): %s", session.ip, task_state, msg_text)
    logger.info("--- IMPORT FAILED ---")
    return False


def import_scp_to_targets(targets: list[str], username: str, password: str,
                          scp_filepath: str, config: dict) -> dict[str, bool]:
    """Import SCP to multiple target iDRACs sequentially.

    Args:
        targets: List of target IP addresses.
        username: iDRAC username.
        password: iDRAC password.
        scp_filepath: Path to the golden template file.
        config: Full parsed config dict for connection/import settings.

    Returns:
        Dict mapping each IP to True (success) or False (failure).
    """
    conn = config.get("connection", {})
    imp = config.get("import", {})
    export_cfg = config.get("export", {})

    results: dict[str, bool] = {}
    total = len(targets)

    logger.info("=" * 60)
    logger.info("IMPORTING GOLDEN TEMPLATE TO %d TARGET(S)", total)
    logger.info("=" * 60)

    for idx, ip in enumerate(targets, 1):
        logger.info("")
        logger.info("[%d/%d] Processing target: %s", idx, total, ip)
        logger.info("-" * 40)

        try:
            session = IdracSession(
                ip=ip,
                username=username,
                password=password,
                verify_ssl=conn.get("verify_ssl", False),
                timeout=conn.get("timeout", 30),
                retries=conn.get("retries", 3),
            )
            ok = import_scp(
                session=session,
                scp_filepath=scp_filepath,
                target=export_cfg.get("target", "ALL"),
                shutdown_type=imp.get("shutdown_type", "Graceful"),
                host_power_state=imp.get("host_power_state", "On"),
                poll_interval=conn.get("poll_interval", 15),
                job_timeout=conn.get("job_timeout", 1800),
            )
            results[ip] = ok
        except Exception:
            logger.exception("  Unhandled error importing to %s", ip)
            results[ip] = False

    return results


def _read_scp_file(filepath: str) -> str:
    """Read an SCP file and collapse it into a single-line string for the ImportBuffer.

    Dell's Redfish import expects the XML/JSON as a single-line string
    in the ImportBuffer field.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Collapse whitespace between XML tags (mirrors Dell reference script)
    content = re.sub(r">\s+<", "><", content)
    content = re.sub(r"\n", "", content)

    return content.strip()
