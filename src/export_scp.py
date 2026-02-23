"""Export iDRAC Server Configuration Profile (SCP) to a local file.

Based on Dell ExportSystemConfigurationLocalREDFISH.py:
https://github.com/dell/iDRAC-Redfish-Scripting/
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

from src.idrac_common import IdracSession

logger = logging.getLogger("idrac")


def export_scp(session: IdracSession, target: str = "ALL", export_format: str = "XML",
               include: str = "Default", output_dir: str = "templates",
               output_filepath: str = "", poll_interval: int = 15,
               job_timeout: int = 1800) -> str:
    """Export the Server Configuration Profile from a single iDRAC.

    Args:
        session: Authenticated IdracSession.
        target: Components to export (ALL, BIOS, IDRAC, NIC, RAID, etc.).
        export_format: XML or JSON.
        include: Export options (Default, IncludeReadOnly, IncludePasswordHashValues).
        output_dir: Directory to write the exported file into (ignored if output_filepath is set).
        output_filepath: If set, write the SCP to this exact path instead of auto-generating.
        poll_interval: Seconds between job polls.
        job_timeout: Max seconds to wait for export job.

    Returns:
        Path to the exported SCP file.
    """
    session.initialize()

    uri = session.oem_action_uri("ExportSystemConfiguration")
    payload = {
        "ExportFormat": export_format,
        "ShareParameters": {
            "Target": target,
        },
    }

    # IncludeInExport is only supported on iDRAC9+ and when not default
    if include != "Default" and session.idrac_version >= 9:
        payload["IncludeInExport"] = include

    logger.info("--- EXPORT SCP FROM %s ---", session.ip)
    logger.info("  Target components : %s", target)
    logger.info("  Format            : %s", export_format)
    logger.info("  Include           : %s", include)
    logger.info("  POST %s", uri)

    resp = session.post(uri, json=payload)

    if resp.status_code not in (200, 202):
        raise RuntimeError(
            f"Export request failed on {session.ip}: HTTP {resp.status_code}\n{resp.text[:500]}"
        )

    # Extract job ID from response headers
    job_uri = resp.headers.get("Location", "")
    if not job_uri:
        raise RuntimeError(f"No job URI returned from export request on {session.ip}")

    job_id = job_uri.split("/")[-1]
    logger.info("  Export job created: %s", job_id)

    # Poll until completion
    task_data = session.poll_job(job_id, poll_interval=poll_interval, job_timeout=job_timeout)

    task_state = task_data.get("TaskState", "Unknown")
    if task_state != "Completed":
        messages = task_data.get("Messages", [])
        msg_text = "; ".join(m.get("Message", "") for m in messages)
        raise RuntimeError(f"Export job {job_id} finished with state '{task_state}': {msg_text}")

    # Extract configuration data from task response
    scp_content = _extract_scp_content(task_data, export_format)
    if not scp_content:
        raise RuntimeError(f"Export job {job_id} completed but returned no configuration data")

    # Write to file
    if output_filepath:
        filepath = output_filepath
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    else:
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_ip = session.ip.replace(".", "_")
        ext = export_format.lower()
        filename = f"scp_{safe_ip}_{timestamp}.{ext}"
        filepath = os.path.join(output_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(scp_content)

    size_kb = os.path.getsize(filepath) / 1024
    logger.info("  Exported SCP written to: %s (%.1f KB)", filepath, size_kb)
    logger.info("--- EXPORT COMPLETE ---")
    return filepath


def _extract_scp_content(task_data: dict, export_format: str) -> str:
    """Extract SCP payload from the Redfish task response.

    Dell iDRAC returns the configuration data inside the task's
    HttpHeaders or as part of the Oem response body depending on
    firmware version.
    """
    # Try Messages -> Message field (common on newer firmware)
    messages = task_data.get("Messages", [])
    for msg in messages:
        oem = msg.get("Oem", {})
        # Dell nests it under Dell -> ServerConfigurationProfile
        dell = oem.get("Dell", {})
        if "ServerConfigurationProfile" in dell:
            if export_format.upper() == "JSON":
                return json.dumps(dell["ServerConfigurationProfile"], indent=2)
            # XML is returned as-is
            return dell["ServerConfigurationProfile"]

    # Fallback: full message text might contain the XML/JSON
    for msg in messages:
        content = msg.get("Message", "")
        if export_format.upper() == "XML" and content.strip().startswith("<"):
            return content
        if export_format.upper() == "JSON" and content.strip().startswith("{"):
            return content

    # Last resort: look at the raw task data for SystemConfiguration
    raw = json.dumps(task_data)
    if export_format.upper() == "XML":
        match = re.search(r"(<SystemConfiguration.*</SystemConfiguration>)", raw, re.DOTALL)
        if match:
            return match.group(1)

    return ""
