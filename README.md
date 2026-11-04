# vSphere MCP Server

A comprehensive Model Context Protocol (MCP) server for VMware vSphere management, providing AI agents with full access to virtual infrastructure operations through a secure, Dockerized environment.

## Features

### Core VM Management
- **List VMs** - Get all virtual machines with power states
- **VM Details** - Detailed information about specific VMs
- **Power Operations** - Start, stop, restart VMs
- **Resource Monitoring** - CPU, RAM, and network utilization
- **Storage Information** - Disk usage and datastore details

### Advanced Operations
- **Snapshot Management** - Create, list, delete and **revert** VM snapshots
- **Template Management** - List and manage VM templates
- **Bulk Operations** - Power operations on multiple VMs
- **Resource Modification** - Change CPU and memory allocation
- **Network Management** - Port groups and network configuration
- **VM Cloning** - Clone templates into new VMs via pyVmomi

### Monitoring & Reporting
- **Performance Monitoring** - Real-time resource utilization
- **Event Logging** - VM events and system logs
- **Alarm Management** - Active alarms and alerts
- **Comprehensive Reports** - Environment-wide analytics
- **Resource Utilization** - CPU, memory, and storage summaries

### Safety Features
- **Confirmation System** - All destructive operations require explicit confirmation
- **Optional Bearer Token Auth** - Restrict access to `/mcp` with `MCP_AUTH_TOKEN`
- **Error Handling** - Comprehensive error messages and troubleshooting
- **Secure Authentication** - Environment-based credential management
- **Audit Trail** - Clear logging of all operations

## Installation

### Prerequisites
- Docker and Docker Compose
- VMware vCenter Server 7.x / 8.x
- Valid vCenter credentials

### Quick Start

1. **Clone repo**

2. **Configure Environment**
```bash
cp env.example .env
# Edit .env with your vCenter credentials
```

3. **Deploy with Docker**
```bash
docker compose up -d
```

## Configuration

### Environment Variables

Create a `.env` file with the following variables:

```env
# vCenter Connection
VCENTER_HOST=vcenter.domain.local
VCENTER_USER=username@domain.local
VCENTER_PASSWORD=your_password_here
INSECURE=True

# Server Configuration
SERVER_HOST=0.0.0.0
SERVER_PORT=8000

# MCP Server Authentication (optional, leave empty to disable auth)
MCP_AUTH_TOKEN=
```

> `.env` is excluded from both git (`.gitignore`) and the image build (`.dockerignore`).
> Credentials are injected at runtime via `env_file`, never baked into the image layer.

### API Transport

Two different vSphere APIs are used depending on the operation:

| API | Used for | Client |
|-----|----------|--------|
| vSphere REST (`/api`) | VM listing, power, inventory, performance | `vsphere_client.py` |
| vSphere SOAP (pyVmomi) | Snapshots, cloning, templates, network lookup | `soap_client.py`, `pyvmomi_client.py` |

Snapshot operations use SOAP because **vSphere 8 removed the REST snapshot endpoints**.

### AnythingLLM Integration

Add the following configuration to your `anythingllm_mcp_servers.json`. When
`MCP_AUTH_TOKEN` is set, the same value must be supplied as `auth_token`:

```json
{
  "mcpServers": {
    "vsphere-mcp-server": {
      "name": "vSphere MCP Server",
      "type": "streamable",
      "url": "http://vsphere-mcp-server:8000/mcp",
      "auth_token": null,
      "enabled": true
    }
  }
}
```

## Available Tools

42 tools are exposed. `hostname` is optional everywhere and falls back to
`VCENTER_HOST`.

### VM Management
| Tool | Description | Parameters |
|------|-------------|------------|
| `list_vms` | List all virtual machines | `hostname` |
| `get_vm_details` | Get detailed VM information | `vm_id`, `hostname` |
| `power_on_vm` | Power on a virtual machine | `vm_id`, `hostname` |
| `power_off_vm` | Power off a virtual machine | `vm_id`, `hostname` |

### Infrastructure Management
| Tool | Description | Parameters |
|------|-------------|------------|
| `list_hosts` | List all ESXi hosts | `hostname` |
| `get_host_details` | Get ESXi host details | `host_id`, `hostname` |
| `list_datacenters` | List all datacenters | `hostname` |
| `get_datacenter_details` | Get datacenter details | `datacenter_id`, `hostname` |
| `list_datastores` | List all datastores | `hostname` |
| `get_datastore_details` | Get datastore details | `datastore_id`, `hostname` |
| `list_folders` | List inventory folders | `folder_type`, `hostname` |
| `get_folder_details` | Get folder details | `folder_id`, `hostname` |
| `list_networks` | List all networks | `hostname` |
| `get_network_details` | Get network details | `network_id`, `hostname` |
| `list_vlans` | Extract VLANs from network names | `hostname` |
| `get_vlan_info` | Look up a VLAN by name or ID | `vlan_query`, `hostname` |
| `get_port_groups` | Get network port groups | `hostname` |

### Monitoring & Performance
| Tool | Description | Parameters |
|------|-------------|------------|
| `get_vm_performance_info` | Get VM performance metrics | `hostname` |
| `get_host_performance_info` | Get host performance metrics | `hostname` |
| `get_vm_disk_usage` | Get VM disk utilization | `hostname` |
| `get_vm_storage_info` | Get VM storage layout | `hostname` |
| `get_datastore_usage` | Get datastore utilization | `hostname` |
| `get_vms_with_high_resource_usage` | Find VMs with high resource allocation | `hostname` |
| `get_vm_events` | Get VM events | `vm_id`, `hostname` |
| `get_alarms` | Get active alarms | `hostname` |

### Snapshot Management
Snapshot tools use the SOAP API (vSphere 8 removed the REST endpoints).

| Tool | Description | Parameters |
|------|-------------|------------|
| `list_vm_snapshots` | List VM snapshots | `vm_id`, `hostname` |
| `create_vm_snapshot` | Create VM snapshot | `vm_id`, `snapshot_name`, `description`, `hostname` |
| `delete_vm_snapshot` | Delete VM snapshot | `vm_id`, `snapshot_name`, `confirm`, `hostname` |
| `revert_vm_snapshot` | Revert VM to a snapshot | `vm_id`, `snapshot_name`, `confirm`, `hostname` |

> Snapshots are addressed **by name** (SOAP lookup), not by snapshot ID.

### Template Management
| Tool | Description | Parameters |
|------|-------------|------------|
| `list_templates` | List VM templates (REST) | `hostname` |
| `find_template_pyvmomi` | Find a template via pyVmomi | `name`, `datacenter`, `hostname` |

### pyVmomi Operations (clone & networks)
| Tool | Description | Parameters |
|------|-------------|------------|
| `list_datacenters_pyvmomi` | List datacenters via pyVmomi | `hostname` |
| `list_clusters_pyvmomi` | List clusters | `datacenter`, `hostname` |
| `clone_vm` | Clone a template into a new VM | `vm_name`, `template_name`, `cluster_name`, `datastore_name`, `network_name`, `target_datacenter`, `cpu_count`, `memory_gb`, `confirm`, `hostname` |
| `get_vm_networks_pyvmomi` | Get a VM's networks | `vm_name`, `hostname` |

### Reporting & Analytics
| Tool | Description | Parameters |
|------|-------------|------------|
| `generate_vm_report` | Generate comprehensive VM report | `hostname` |
| `get_resource_utilization_summary` | Get resource utilization summary | `hostname` |

### Automation
| Tool | Description | Parameters |
|------|-------------|------------|
| `bulk_power_operations` | Bulk power operations | `operation`, `vm_list`, `hostname` |
| `bulk_delete_vms` | Bulk delete VMs | `vm_list`, `confirm`, `hostname` |

### Destructive Operations (Require Confirmation)
| Tool | Description | Parameters |
|------|-------------|------------|
| `delete_vm` | Delete virtual machine | `vm_id`, `confirm`, `hostname` |
| `modify_vm_resources` | Modify VM resources | `vm_id`, `cpu_count`, `memory_gb`, `confirm`, `hostname` |
| `force_power_off_vm` | Force power off VM | `vm_id`, `confirm`, `hostname` |
| `delete_vm_snapshot` | Delete a snapshot | `vm_id`, `snapshot_name`, `confirm`, `hostname` |
| `revert_vm_snapshot` | Revert to a snapshot | `vm_id`, `snapshot_name`, `confirm`, `hostname` |
| `clone_vm` | Clone a template | see above |

**Destructive operations require explicit confirmation by setting `confirm=True`**

## Security Features

### Confirmation System
All destructive operations require explicit confirmation:

```python
# First call - shows warning
delete_vm(vm_id="MyServer", confirm=False)
# Returns: DESTRUCTIVE OPERATION: Delete VM MyServer...

# Second call - executes operation
delete_vm(vm_id="MyServer", confirm=True)
# Returns: VM MyServer deleted successfully
```

### Bearer Token Authentication
Set `MCP_AUTH_TOKEN` to require `Authorization: Bearer <token>` on every
request to `/mcp`. Requests with a missing or wrong token get `401`. Leave the
variable empty to disable authentication.

```bash
curl -X POST http://127.0.0.1:8000/mcp \
  -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}'
```

### Environment-Based Authentication
- Credentials stored in environment variables
- No hardcoded passwords
- Secure Docker deployment
- SSL/TLS support with configurable verification

## Usage Examples

### Basic VM Operations
```bash
# List all VMs
"Show me all virtual machines"

# Get VM details
"Get details for VM WebServer01"

# Power operations
"Power on the VM DatabaseServer"
```

### Monitoring and Reporting
```bash
# Performance monitoring
"Show me CPU and memory usage for all VMs"
"Which VMs have high resource usage?"

# Storage monitoring
"Show me datastore usage"
"Which VMs have disk usage over 90%?"

# Comprehensive reporting
"Generate a complete report of the vSphere environment"
```

### Snapshot Management
```bash
# Create snapshots
"Create a snapshot of VM WebServer01 before updates"

# List snapshots
"Show me all snapshots for VM DatabaseServer"

# Revert to a snapshot (requires confirmation)
"Revert VM WebServer01 to snapshot 'before-upgrade'"
# -> shows warning, no action
"Revert VM WebServer01 to snapshot 'before-upgrade' with confirmation"
# -> VM is powered off first (if running), then reverted; stays powered off
```

`revert_vm_snapshot` behaviour:
1. Without `confirm=True` it only returns a warning and touches nothing.
2. A running or suspended VM is powered off first; if powering off fails the
   revert is aborted.
3. The revert uses `suppressPowerOn=True`, so the VM is left **powered off**
   afterwards and must be powered on manually.

### Destructive Operations (with confirmation)
```bash
# VM deletion (requires confirmation)
"Delete VM TestServer"  # Shows warning
"Delete VM TestServer with confirmation"  # Executes deletion

# Resource modification (requires confirmation)
"Modify VM WebServer01 to have 8 CPUs and 16GB RAM with confirmation"

# Bulk operations (requires confirmation)
"Delete VMs: OldServer1, OldServer2, OldServer3 with confirmation"
```

## Development

### Build and run locally
```bash
docker build -t vsphere-mcp-server .
docker compose up -d
```

### Dependency pinning
`mcp==1.14.1` requires `pydantic._internal._typing_extra.eval_type_backport`,
which was removed in pydantic 2.14. Keep `pydantic` pinned to `2.13.x` in
`requirements.txt`, otherwise the server fails at import time.

### Container checks
```bash
# Import sanity check (also used by CI)
docker run --rm vsphere-mcp-server:ci python -c "from vsphere_mcp_server import server"
```
