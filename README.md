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
- **Snapshot Management** - Create, list, and delete VM snapshots
- **Template Management** - List and manage VM templates
- **Bulk Operations** - Power operations on multiple VMs
- **Resource Modification** - Change CPU and memory allocation
- **Network Management** - Port groups and network configuration

### Monitoring & Reporting
- **Performance Monitoring** - Real-time resource utilization
- **Event Logging** - VM events and system logs
- **Alarm Management** - Active alarms and alerts
- **Comprehensive Reports** - Environment-wide analytics
- **Resource Utilization** - CPU, memory, and storage summaries

### Safety Features
- **Confirmation System** - All destructive operations require explicit confirmation
- **Error Handling** - Comprehensive error messages and troubleshooting
- **Secure Authentication** - Environment-based credential management
- **Audit Trail** - Clear logging of all operations

## Installation

### Prerequisites
- Docker and Docker Compose
- VMware vCenter Server with REST API access
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
```

### AnythingLLM Integration

Add the following configuration to your `anythingllm_mcp_servers.json`:

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

### VM Management
| Tool | Description | Parameters |
|------|-------------|------------|
| `list_vms` | List all virtual machines | `hostname` (optional) |
| `get_vm_details` | Get detailed VM information | `vm_id`, `hostname` (optional) |
| `power_on_vm` | Power on a virtual machine | `vm_id`, `hostname` (optional) |
| `power_off_vm` | Power off a virtual machine | `vm_id`, `hostname` (optional) |
| `restart_vm` | Restart a virtual machine | `vm_id`, `hostname` (optional) |

### Infrastructure Management
| Tool | Description | Parameters |
|------|-------------|------------|
| `list_hosts` | List all ESXi hosts | `hostname` (optional) |
| `list_datastores` | List all datastores | `hostname` (optional) |
| `list_networks` | List all networks | `hostname` (optional) |
| `list_datacenters` | List all datacenters | `hostname` (optional) |

### Monitoring & Performance
| Tool | Description | Parameters |
|------|-------------|------------|
| `get_vm_performance_info` | Get VM performance metrics | `hostname` (optional) |
| `get_host_performance_info` | Get host performance metrics | `hostname` (optional) |
| `get_vm_disk_usage` | Get VM disk utilization | `hostname` (optional) |
| `get_datastore_usage` | Get datastore utilization | `hostname` (optional) |
| `get_vms_with_high_resource_usage` | Find VMs with high resource allocation | `hostname` (optional) |

### Snapshot Management
| Tool | Description | Parameters |
|------|-------------|------------|
| `list_vm_snapshots` | List one VM's snapshots, or query all VMs' snapshots | `vm_id` (optional; omit for fleet-wide query), `hostname` (optional) |
| `create_vm_snapshot` | Create VM snapshot | `vm_id`, `snapshot_name`, `description`, `hostname` (optional) |
| `delete_vm_snapshot` | Delete VM snapshot  | `vm_id`, `snapshot_id`, `confirm`, `hostname` (optional) |

### Template Management
| Tool | Description | Parameters |
|------|-------------|------------|
| `list_templates` | List VM templates | `hostname` (optional) |

### Advanced Monitoring
| Tool | Description | Parameters |
|------|-------------|------------|
| `get_vm_events` | Get VM events | `vm_id`, `hostname` (optional) |
| `get_alarms` | Get active alarms | `hostname` (optional) |
| `get_port_groups` | Get network port groups | `hostname` (optional) |

### Reporting & Analytics
| Tool | Description | Parameters |
|------|-------------|------------|
| `generate_vm_report` | Generate comprehensive VM report | `hostname` (optional) |
| `get_resource_utilization_summary` | Get resource utilization summary | `hostname` (optional) |

### Automation
| Tool | Description | Parameters |
|------|-------------|------------|
| `bulk_power_operations` | Bulk power operations | `operation`, `vm_list`, `hostname` (optional) |
| `bulk_delete_vms` | Bulk delete VMs | `vm_list`, `confirm`, `hostname` (optional) |

### Destructive Operations (Require Confirmation)
| Tool | Description | Parameters |
|------|-------------|------------|
| `delete_vm` | Delete virtual machine  | `vm_id`, `confirm`, `hostname` (optional) |
| `modify_vm_resources` | Modify VM resources  | `vm_id`, `cpu_count`, `memory_gb`, `confirm`, `hostname` (optional) |
| `force_power_off_vm` | Force power off VM | `vm_id`, `confirm`, `hostname` (optional) |

**Destructive operations require explicit confirmation by setting `confirm=True`**

## Filtering, counting, and ranking

The list tools (`list_vms`, `list_hosts`, `list_datastores`, `list_networks`,
`list_clusters_pyvmomi`) accept optional `filters`, `group_by`, `fields`,
`sort_by`/`descending`, and `limit` parameters. Filtering, counting, and
sorting run inside the server (one PropertyCollector round-trip for the whole
inventory), so only the result — never the full inventory — reaches the LLM.
`list_vm_snapshots` supports the same parameters in fleet mode: called
without `vm_id`, it queries the snapshots of every VM in one round-trip.

Examples:

- Which VMs run hardware version vmx-13?
  `list_vms(filters=[{"field": "hardware_version", "op": "eq", "value": "vmx-13"}])`
- Show the guest OS distribution:
  `list_vms(group_by="guest_os")` → one line per OS with count and percentage
- Datastores below 10% free:
  `list_datastores(filters=[{"field": "free_pct", "op": "lt", "value": 10}])`
- VMs with hardware version vmx-13 or older:
  `list_vms(filters=[{"field": "hardware_version", "op": "lte", "value": "vmx-13"}])`
- The 10 largest VMs by provisioned storage:
  `list_vms(sort_by="storage_provisioned_gb", fields=["name", "storage_provisioned_gb"], limit=10)`
- The 10 oldest snapshots anywhere in the fleet:
  `list_vm_snapshots(sort_by="age_days", limit=10)`
- Snapshots older than 30 days, per VM:
  `list_vm_snapshots(filters=[{"field": "age_days", "op": "gt", "value": 30}], group_by="vm_name")`

Operators: `eq, ne, contains, not_contains, gt, gte, lt, lte, in` (conditions
are ANDed, string matching is case-insensitive). Range operators compare
numbers, and also prefix-number strings like `vmx-13` by their trailing
number. `sort_by` orders by any queryable field (`descending` defaults to
true — biggest/latest first). Each tool's description lists its queryable
fields. `limit` defaults to 50 rows (`0` = unlimited).

## Security Features

### Confirmation System
All destructive operations require explicit confirmation:

```python
# First call - shows warning
delete_vm(vm_id="MyServer", confirm=False)
# Returns: DESTRUCTIVE OPERATION: Delete VM MyServer...

# Second call - executes operation
delete_vm(vm_id="MyServer", confirm=True)
# Returns:  VM MyServer deleted successfully
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
"Restart all VMs: WebServer01, WebServer02, WebServer03"
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
"Show me resource utilization summary"
```

### Snapshot Management
```bash
# Create snapshots
"Create a snapshot of VM WebServer01 before updates"

# List snapshots
"Show me all snapshots for VM DatabaseServer"

# Delete snapshots (requires confirmation)
"Delete snapshot 'backup-2024-01-15' from VM WebServer01"
```

### Destructive Operations (with confirmation)
```bash
# VM deletion (requires confirmation)
"Delete VM TestServer"  # Shows warning
"Delete VM TestServer with confirmation"  # Executes deletion

# Resource modification (requires confirmation)
"Modify VM WebServer01 to have 8 CPUs and 16GB RAM"
"Modify VM WebServer01 to have 8 CPUs and 16GB RAM with confirmation"

# Bulk operations (requires confirmation)
"Delete VMs: OldServer1, OldServer2, OldServer3"
"Delete VMs: OldServer1, OldServer2, OldServer3 with confirmation"
```

