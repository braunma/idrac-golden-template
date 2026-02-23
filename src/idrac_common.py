"""Shared utilities for iDRAC Redfish operations.

Based on Dell iDRAC-Redfish-Scripting patterns:
https://github.com/dell/iDRAC-Redfish-Scripting/
"""

import ipaddress
import logging
import sys
import time

import requests
import urllib3

logger = logging.getLogger("idrac")

# Redfish base paths
MANAGERS_URI = "/redfish/v1/Managers/iDRAC.Embedded.1"
TASK_SERVICE_URI = "/redfish/v1/TaskService/Tasks"

# OEM action suffixes by iDRAC generation
OEM_ACTIONS = {
    "v10": "OemManager",
    "legacy": "EID_674_Manager",
}


def setup_logging(verbose: bool = False) -> None:
    """Configure structured logging for pipeline readability."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)-7s] %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)
    # Silence noisy libraries unless debugging
    if not verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("requests").setLevel(logging.WARNING)


def suppress_insecure_warnings() -> None:
    """Suppress SSL warnings when using self-signed iDRAC certificates."""
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# Suppress once at import time — self-signed certs are the norm for iDRAC.
suppress_insecure_warnings()


def expand_targets(targets: list[str]) -> list[str]:
    """Expand a list of IPs and IP ranges into individual IP addresses.

    Supports:
      - Single IPs:  "192.168.1.10"
      - Dash ranges: "192.168.1.10-192.168.1.20"
    """
    expanded = []
    for entry in targets:
        entry = entry.strip()
        if "-" in entry and not entry.startswith("-"):
            start_str, end_str = entry.split("-", 1)
            start = ipaddress.IPv4Address(start_str.strip())
            end = ipaddress.IPv4Address(end_str.strip())
            if start > end:
                raise ValueError(f"Invalid IP range: {entry} (start > end)")
            current = start
            while current <= end:
                expanded.append(str(current))
                current += 1
        else:
            # Validate it is a proper IP
            ipaddress.IPv4Address(entry)
            expanded.append(entry)
    return expanded


class IdracSession:
    """Manages authenticated Redfish sessions to a single iDRAC."""

    def __init__(self, ip: str, username: str, password: str, verify_ssl: bool = False,
                 timeout: int = 30, retries: int = 3):
        self.ip = ip
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.retries = retries
        self.base_url = f"https://{ip}"
        self.idrac_version: int | None = None

    def _request(self, method: str, uri: str, **kwargs) -> requests.Response:
        """Execute an HTTP request with retry logic."""
        url = f"{self.base_url}{uri}"
        kwargs.setdefault("verify", self.verify_ssl)
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("auth", (self.username, self.password))

        last_exc = None
        for attempt in range(1, self.retries + 1):
            try:
                logger.debug("  %s %s (attempt %d/%d)", method.upper(), url, attempt, self.retries)
                resp = requests.request(method, url, **kwargs)
                return resp
            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                if attempt < self.retries:
                    wait = 2 ** attempt
                    logger.warning("  Connection to %s failed (attempt %d/%d), retrying in %ds ...",
                                   self.ip, attempt, self.retries, wait)
                    time.sleep(wait)
        raise ConnectionError(f"Failed to connect to {self.ip} after {self.retries} attempts: {last_exc}")

    def get(self, uri: str, **kwargs) -> requests.Response:
        return self._request("GET", uri, **kwargs)

    def post(self, uri: str, **kwargs) -> requests.Response:
        return self._request("POST", uri, **kwargs)

    def initialize(self) -> int:
        """Verify iDRAC Redfish support and detect generation in a single request.

        Returns:
            Detected iDRAC generation (8, 9, or 10).
        """
        if self.idrac_version is not None:
            return self.idrac_version

        logger.info("  Connecting to iDRAC %s ...", self.ip)
        resp = self.get(MANAGERS_URI)
        if resp.status_code == 401:
            raise PermissionError(f"Authentication failed for {self.ip} - check credentials")
        if resp.status_code != 200:
            raise RuntimeError(
                f"iDRAC {self.ip} returned HTTP {resp.status_code}: {resp.text[:300]}"
            )
        logger.info("  iDRAC %s is reachable (HTTP 200).", self.ip)

        model = resp.json().get("Model", "")
        logger.debug("  iDRAC model string: %s", model)

        if "12" in model or "13" in model:
            self.idrac_version = 8
        elif "14" in model or "15" in model or "16" in model:
            self.idrac_version = 9
        else:
            self.idrac_version = 10

        logger.info("  Detected iDRAC generation: %d (model: %s)", self.idrac_version, model)
        return self.idrac_version

    def oem_action_uri(self, action: str) -> str:
        """Build the correct OEM action URI based on iDRAC generation."""
        if self.idrac_version is None:
            self.initialize()
        prefix = OEM_ACTIONS["v10"] if self.idrac_version >= 10 else OEM_ACTIONS["legacy"]
        return f"{MANAGERS_URI}/Actions/Oem/{prefix}.{action}"

    def poll_job(self, job_id: str, poll_interval: int = 15, job_timeout: int = 1800) -> dict:
        """Poll a Redfish task until completion or timeout.

        Returns the final task response dict.
        """
        uri = f"{TASK_SERVICE_URI}/{job_id}"
        logger.info("  Polling job %s (interval=%ds, timeout=%ds) ...", job_id, poll_interval, job_timeout)
        start = time.time()

        while True:
            elapsed = time.time() - start
            if elapsed > job_timeout:
                raise TimeoutError(
                    f"Job {job_id} on {self.ip} did not complete within {job_timeout}s"
                )

            resp = self.get(uri)
            data = resp.json()
            task_state = data.get("TaskState", "Unknown")
            message = data.get("Messages", [{}])[0].get("Message", "")

            logger.info("  [%s] Job %s — state: %s | %s (%.0fs elapsed)",
                        self.ip, job_id, task_state, message, elapsed)

            if task_state in ("Completed", "CompletedWithErrors", "Failed", "Exception"):
                return data

            time.sleep(poll_interval)
