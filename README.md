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
├── templates/               # Exported SCP files land here (git-ignored)
├── config.yaml.example      # Configuration template — copy to config.yaml
├── .gitlab-ci.yml           # GitLab CI/CD pipeline
└── requirements.txt         # Python dependencies
```

## Quick Start

```bash
# 1. Clone and configure
cp config.yaml.example config.yaml
# Edit config.yaml with your source IP and target IPs

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set credentials
export IDRAC_USERNAME="root"
export IDRAC_PASSWORD="your-password"

# 4. Validate connectivity
python main.py validate

# 5. Export golden template from source
python main.py export

# 6. Import to all targets
python main.py import templates/scp_192_168_1_100_20240101_120000.xml

# 7. Or do both in one step
python main.py apply
```

## Configuration

### config.yaml

Copy `config.yaml.example` and edit to match your environment. Key sections:

| Section      | Purpose                                       |
|-------------|-----------------------------------------------|
| `source.ip` | iDRAC IP to export the golden template from   |
| `targets`   | List of IPs or IP ranges to apply template to |
| `export`    | SCP export options (target, format, include)   |
| `import`    | Shutdown type and host power state             |
| `connection`| SSL, timeout, retry, and polling settings      |

Target IPs support ranges: `"192.168.1.110-192.168.1.120"`

### Environment Variable Overrides

All credentials and optional IP overrides come from environment variables:

| Variable           | Required | Description                             |
|-------------------|----------|-----------------------------------------|
| `IDRAC_USERNAME`  | Yes      | iDRAC username                          |
| `IDRAC_PASSWORD`  | Yes      | iDRAC password                          |
| `IDRAC_SOURCE_IP` | No       | Overrides `source.ip` in config.yaml    |
| `IDRAC_TARGET_IPS`| No       | Comma-separated, overrides `targets`    |
| `IDRAC_CONFIG_FILE`| No      | Path to config file (default: config.yaml) |

## GitLab CI/CD Setup

### 1. Add CI/CD Variables

Go to **Settings > CI/CD > Variables** and add:

| Variable         | Type     | Flags                |
|-----------------|----------|----------------------|
| `IDRAC_USERNAME`| Variable | Masked               |
| `IDRAC_PASSWORD`| Variable | Masked, Protected    |
| `IDRAC_SOURCE_IP`| Variable | _(optional)_        |
| `IDRAC_TARGET_IPS`| Variable | _(optional)_       |

### 2. Pipeline Stages

| Stage      | Job                    | Trigger  | Description                           |
|-----------|------------------------|----------|---------------------------------------|
| validate  | `lint`                 | Auto     | Python syntax check                   |
| validate  | `validate_connectivity`| Manual   | Test Redfish connectivity to all iDRACs|
| export    | `export_template`      | Manual   | Export SCP from source iDRAC          |
| import    | `import_template`      | Manual   | Import exported template to targets   |
| import    | `apply_template`       | Manual   | Export + import in one step           |

### 3. Runner Requirements

Your GitLab runner needs:
- Network access to all iDRAC management interfaces (typically on a dedicated management VLAN)
- Python 3.11+ (the pipeline uses `python:3.11-slim` Docker image)

## CLI Reference

```
python main.py [-c CONFIG] [-v] {export,import,apply,validate}

Commands:
  export     Export SCP from the source iDRAC to templates/
  import     Import an SCP file to all configured target iDRACs
  apply      Export from source, then import to all targets
  validate   Check config and test connectivity to all iDRACs

Options:
  -c, --config   Path to config YAML (default: config.yaml)
  -v, --verbose   Enable debug-level logging
```
