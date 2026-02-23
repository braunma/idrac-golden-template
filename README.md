# iDRAC Golden Template

Export a Dell iDRAC Server Configuration Profile (SCP) from a reference machine and apply it to a fleet of targets via Redfish. Designed to run in GitLab CI/CD pipelines with credentials stored as masked CI/CD variables.

Based on [Dell iDRAC-Redfish-Scripting](https://github.com/dell/iDRAC-Redfish-Scripting/).

## Project Structure

```
.
├── main.py                  # CLI entrypoint (export / import / apply / validate)
├── src/
│   ├── idrac_common.py      # Shared Redfish session, retry logic, job polling
│   ├── export_scp.py        # Export SCP from a single iDRAC
│   └── import_scp.py        # Import SCP to one or more iDRACs
├── templates/               # Exported SCP files and group templates
├── config.yaml.example      # Configuration template — copy to config.yaml
├── .gitlab-ci.yml           # GitLab CI/CD pipeline
└── requirements.txt         # Python dependencies
```

## Quick Start

```bash
# 1. Clone and configure
cp config.yaml.example config.yaml
# Edit config.yaml with your server groups (or single source/targets)

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set credentials
export IDRAC_USERNAME="root"
export IDRAC_PASSWORD="your-password"

# 4. Validate connectivity
python main.py validate

# 5. Export golden template from source(s)
python main.py export

# 6. Edit the template files in your repo, then import to targets
python main.py import

# 7. Or do both in one step
python main.py apply
```

## Server Groups

Groups let you manage different sets of servers with different golden templates. Each group defines:

- **source_ip** — the reference machine to export the SCP from
- **template** — a stable file path where the exported template is stored (and can be manually edited)
- **targets** — the machines the template is applied to

### Example Workflow

1. **Export** from the reference server:
   ```bash
   python main.py export --group compute-nodes
   # → Exports SCP from 192.168.1.1 to templates/compute-nodes.xml
   ```

2. **Edit** the template in your repo (remove host-specific values, tweak BIOS settings, etc.), then commit:
   ```bash
   git add templates/compute-nodes.xml
   git commit -m "Tune compute node BIOS settings"
   ```

3. **Import** the edited template to all targets in the group:
   ```bash
   python main.py import --group compute-nodes
   # → Applies templates/compute-nodes.xml to 192.168.1.1 through 192.168.1.9
   ```

### config.yaml (groups format)

```yaml
groups:
  compute-nodes:
    source_ip: "192.168.1.1"
    template: "templates/compute-nodes.xml"
    targets:
      - "192.168.1.1-192.168.1.9"

  storage-nodes:
    source_ip: "192.168.1.17"
    template: "templates/storage-nodes.xml"
    targets:
      - "192.168.1.20-192.168.1.25"

export:
  target: "ALL"
  format: "XML"
  include: "Default"

import:
  shutdown_type: "Graceful"
  host_power_state: "On"

connection:
  verify_ssl: false
  timeout: 30
  retries: 3
  poll_interval: 15
  job_timeout: 1800
```

### Operating on groups

```bash
# All groups at once
python main.py export
python main.py import
python main.py apply
python main.py validate

# A single group
python main.py export --group compute-nodes
python main.py import --group storage-nodes
python main.py validate --group compute-nodes
```

### Legacy format (still supported)

The original single-source/targets format still works. It is treated as one implicit group called "default":

```yaml
source:
  ip: "192.168.1.100"

targets:
  - "192.168.1.101"
  - "192.168.1.102"
```

```bash
python main.py export
python main.py import templates/scp_192_168_1_100_20240101_120000.xml
python main.py apply
```

## Configuration

### config.yaml

Copy `config.yaml.example` and edit to match your environment. Key sections:

| Section      | Purpose                                              |
|-------------|------------------------------------------------------|
| `groups`    | Server groups with source, template path, and targets |
| `export`    | SCP export options (target, format, include)          |
| `import`    | Shutdown type and host power state                    |
| `connection`| SSL, timeout, retry, and polling settings             |

Target IPs support ranges: `"192.168.1.110-192.168.1.120"`

### Environment Variable Overrides

| Variable           | Required | Description                                    |
|-------------------|----------|------------------------------------------------|
| `IDRAC_USERNAME`  | Yes      | iDRAC username                                 |
| `IDRAC_PASSWORD`  | Yes      | iDRAC password                                 |
| `IDRAC_SOURCE_IP` | No       | Overrides `source.ip` (legacy format only)     |
| `IDRAC_TARGET_IPS`| No       | Comma-separated, overrides `targets` (legacy)  |
| `IDRAC_CONFIG_FILE`| No      | Path to config file (default: config.yaml)     |

## GitLab CI/CD Setup

### 1. Add CI/CD Variables

Go to **Settings > CI/CD > Variables** and add:

| Variable         | Type     | Flags                |
|-----------------|----------|----------------------|
| `IDRAC_USERNAME`| Variable | Masked               |
| `IDRAC_PASSWORD`| Variable | Masked, Protected    |
| `IDRAC_GROUP`   | Variable | _(optional, target one group)_ |

### 2. Pipeline Stages

| Stage      | Job                    | Trigger  | Description                            |
|-----------|------------------------|----------|----------------------------------------|
| validate  | `lint`                 | Auto     | Python syntax check                    |
| validate  | `validate_connectivity`| Manual   | Test Redfish connectivity to all iDRACs |
| export    | `export_template`      | Manual   | Export SCP from source iDRAC(s)        |
| import    | `import_template`      | Manual   | Import template(s) to targets          |
| import    | `apply_template`       | Manual   | Export + import in one step            |

Set `IDRAC_GROUP` to target a specific group, or leave it empty to process all groups.

### 3. Runner Requirements

Your GitLab runner needs:
- Network access to all iDRAC management interfaces (typically on a dedicated management VLAN)
- Python 3.11+ (the pipeline uses `python:3.11-slim` Docker image)

## CLI Reference

```
python main.py [-c CONFIG] [-v] [-g GROUP] {export,import,apply,validate}

Commands:
  export     Export SCP from source iDRAC(s) to template files
  import     Import template files to target iDRACs
  apply      Export from source, then import to all targets
  validate   Check config and test connectivity to all iDRACs

Options:
  -c, --config   Path to config YAML (default: config.yaml)
  -v, --verbose  Enable debug-level logging
  -g, --group    Target a specific server group (default: all groups)
```
