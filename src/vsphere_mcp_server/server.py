"""vSphere MCP Server - Main server implementation - Docker/Environment version."""

import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from .vsphere_client import VSphereClient
from .pyvmomi_client import PyVmomiClient
from .inventory import (FilterCondition, aggregate, fetch_inventory,
                        find_object, flatten_snapshot_tree, match_snapshot,
                        run_inventory_query, sort_rows)
from pyVmomi import vim, vmodl

# Initialize MCP server
mcp = FastMCP("vSphere MCP Server")


def _handle_error(e: Exception, operation: str) -> str:
    """Handle errors consistently across all tools."""
    error_msg = str(e)

    if "Authentication failed" in error_msg:
        return (
            f"Authentication failed for {operation}. "
            "Check your environment variables (VCENTER_HOST, VCENTER_USER, VCENTER_PASSWORD)."
        )
    if "Connection" in error_msg or "timeout" in error_msg.lower():
        return f"Connection failed for {operation}. Check network connectivity and hostname."
    return f"Error in {operation}: {error_msg}"


def _wait_for_pyvmomi_task(task, timeout=300, interval=2):
    """Poll a pyVmomi task until it leaves queued/running.

    Returns (task_info, timed_out).
    """
    elapsed = 0
    info = task.info
    while info.state in (vim.TaskInfo.State.queued, vim.TaskInfo.State.running):
        if elapsed >= timeout:
            return info, True
        time.sleep(interval)
        elapsed += interval
        info = task.info
    return info, False


# VM Management Tools
@mcp.tool()
def list_vms(
    hostname: Optional[str] = None,
    filters: Optional[List[FilterCondition]] = None,
    group_by: Optional[str] = None,
    fields: Optional[List[str]] = None,
    limit: int = 50,
    sort_by: Optional[str] = None,
    descending: bool = True,
) -> str:
    """List, filter, count, or rank virtual machines.

    Without filters/group_by/fields/sort_by this is a basic listing. With
    any of them, an in-process engine fetches properties for ALL VMs in one
    call, filters/aggregates server-side, and returns only the result — use
    this instead of listing everything and filtering yourself.

    Queryable fields: name, vm_id, power_state, connection_state, guest_os,
    guest_os_running, hardware_version, cpu_count, memory_mb, template,
    tools_status, tools_version, ip_address, guest_hostname, uuid,
    annotation, host, cluster, storage_committed_gb, storage_provisioned_gb,
    disk_count, snapshot_count, boot_time, cpu_usage_mhz, memory_usage_gb,
    uptime_days.
    Filter ops: eq, ne, contains, not_contains, gt, gte, lt, lte, in
    (conditions are ANDed; string matching is case-insensitive).
    Note: power_state values are pyVmomi-style: poweredOn / poweredOff /
    suspended.

    Examples:
      Which VMs run hardware version vmx-13?
        filters=[{"field": "hardware_version", "op": "eq", "value": "vmx-13"}]
      Show the OS distribution:
        group_by="guest_os"
      Top 10 VMs by used storage:
        sort_by="storage_committed_gb", limit=10,
        fields=["name", "storage_committed_gb"]
      Powered-on VMs that have snapshots:
        filters=[{"field": "power_state", "op": "eq", "value": "poweredOn"},
                 {"field": "snapshot_count", "op": "gt", "value": 0}]

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        filters: list of {field, op, value} conditions, ANDed
        group_by: return only a count distribution of this field
        fields: fields shown per VM (default: name, power_state, guest_os)
        limit: max rows in listing or distribution output (default 50; 0 = unlimited)
        sort_by: order rows by this field before applying limit
        descending: sort direction (default True = biggest/latest first)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    if filters or group_by or fields or sort_by:
        pv_client = PyVmomiClient(host=hostname)
        try:
            return run_inventory_query(pv_client, "vm", filters=filters,
                                       group_by=group_by, fields=fields,
                                       limit=limit, sort_by=sort_by,
                                       descending=descending)
        except Exception as e:
            return f"Error querying VMs via pyVmomi: {e}"
        finally:
            pv_client.close()

    client = VSphereClient(hostname)
    try:
        response = client.get("vcenter/vm")
        vms = response.get("value", [])

        if not vms:
            return "No virtual machines found"

        shown = vms if limit == 0 else vms[:limit]
        result = f"Found {len(vms)} virtual machines"
        if len(shown) < len(vms):
            result += f" (showing {len(shown)} — refine with filters or raise limit)"
        result += ":\n\n"
        for vm in shown:
            result += f"• {vm.get('name', 'Unknown')} (ID: {vm.get('vm')})\n"
            result += f"  Power State: {vm.get('power_state', 'Unknown')}\n"
            result += f"  CPU Count: {vm.get('cpu_count', 'Unknown')}\n"
            result += f"  Memory: {vm.get('memory_size_MiB', 'Unknown')} MiB\n\n"

        return result.strip()

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "listing VMs")
    finally:
        client.close()


@mcp.tool()
def get_vm_details(vm_id: str, hostname: Optional[str] = None) -> str:
    """Get detailed information about a specific virtual machine.

    Args:
        vm_id: Virtual machine ID or name
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        # If vm_id doesn't start with 'vm-', assume it's a name and look up the ID
        if not vm_id.startswith("vm-"):
            vms_response = client.get("vcenter/vm")
            vms = vms_response.get("value", [])

            # Find VM by name (case-insensitive)
            vm_id_found = None
            for vm in vms:
                if vm.get("name", "").lower() == vm_id.lower():
                    vm_id_found = vm.get("vm")
                    break

            if not vm_id_found:
                return f"Virtual machine '{vm_id}' not found by name"

            vm_id = vm_id_found

        response = client.get(f"vcenter/vm/{vm_id}")
        vm = response.get("value", {})

        if not vm:
            return f"Virtual machine {vm_id} not found"

        result = f"VM Details: {vm.get('name', 'Unknown')}\n"
        result += f"ID: {vm_id}\n"
        result += f"Power State: {vm.get('power_state', 'Unknown')}\n"
        result += f"CPU Count: {vm.get('cpu', {}).get('count', 'Unknown')}\n"
        result += f"Memory: {vm.get('memory', {}).get('size_MiB', 'Unknown')} MiB\n"
        result += f"Guest OS: {vm.get('guest_OS', 'Unknown')}\n"
        result += (
            f"Hardware Version: {vm.get('hardware', {}).get('version', 'Unknown')}\n"
        )

        # Network info
        nics = vm.get("nics", [])
        if nics:
            result += "\nNetwork Interfaces:\n"
            for i, nic in enumerate(nics):
                network_name = "Unknown"
                if isinstance(nic, dict):
                    backing = nic.get("backing", {})
                    if isinstance(backing, dict):
                        network_name = backing.get("network_name", "Unknown")
                result += f"  NIC {i}: {network_name}\n"

        return result

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"getting VM {vm_id} details")
    finally:
        client.close()


@mcp.tool()
def power_on_vm(vm_id: str, hostname: Optional[str] = None) -> str:
    """Power on a virtual machine.

    Args:
        vm_id: Virtual machine ID
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        client.post(f"vcenter/vm/{vm_id}/power/start")
        return "Power on initiated for VM " + vm_id

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"powering on VM {vm_id}")
    finally:
        client.close()


@mcp.tool()
def power_off_vm(vm_id: str, hostname: Optional[str] = None) -> str:
    """Power off a virtual machine.

    Args:
        vm_id: Virtual machine ID
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        client.post(f"vcenter/vm/{vm_id}/power/stop")
        return f"Power off initiated for VM {vm_id}"

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"powering off VM {vm_id}")
    finally:
        client.close()


# Infrastructure Tools
@mcp.tool()
def list_hosts(
    hostname: Optional[str] = None,
    filters: Optional[List[FilterCondition]] = None,
    group_by: Optional[str] = None,
    fields: Optional[List[str]] = None,
    limit: int = 50,
    sort_by: Optional[str] = None,
    descending: bool = True,
) -> str:
    """List, filter, count, or rank ESXi hosts.

    With filters/group_by/fields/sort_by an in-process engine fetches
    properties for ALL hosts in one call and returns only the
    filtered/aggregated result.

    Queryable fields: name, host_id, connection_state, power_state,
    maintenance_mode, esxi_version, build, vendor, model, cpu_model,
    cpu_cores, cpu_threads, memory_gb, cluster, vm_count, cpu_usage_mhz,
    memory_usage_gb, uptime_days.
    Filter ops: eq, ne, contains, not_contains, gt, gte, lt, lte, in
    (conditions are ANDed; string matching is case-insensitive).

    Examples:
      ESXi version distribution: group_by="esxi_version"
      Hosts in maintenance mode:
        filters=[{"field": "maintenance_mode", "op": "eq", "value": true}]
      Busiest hosts first: sort_by="cpu_usage_mhz"

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        filters: list of {field, op, value} conditions, ANDed
        group_by: return only a count distribution of this field
        fields: fields shown per host (default: name, connection_state, esxi_version)
        limit: max rows in listing or distribution output (default 50; 0 = unlimited)
        sort_by: order rows by this field before applying limit
        descending: sort direction (default True = biggest first)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    if filters or group_by or fields or sort_by:
        pv_client = PyVmomiClient(host=hostname)
        try:
            return run_inventory_query(pv_client, "host", filters=filters,
                                       group_by=group_by, fields=fields,
                                       limit=limit, sort_by=sort_by,
                                       descending=descending)
        except Exception as e:
            return f"Error querying hosts via pyVmomi: {e}"
        finally:
            pv_client.close()

    client = VSphereClient(hostname)
    try:
        response = client.get("vcenter/host")
        hosts = response.get("value", [])

        if not hosts:
            return "No ESXi hosts found"

        shown = hosts if limit == 0 else hosts[:limit]
        result = f"Found {len(hosts)} ESXi hosts"
        if len(shown) < len(hosts):
            result += f" (showing {len(shown)} — refine with filters or raise limit)"
        result += ":\n\n"
        for host in shown:
            result += f"• {host.get('name', 'Unknown')} (ID: {host.get('host')})\n"
            result += f"  Connection State: {host.get('connection_state', 'Unknown')}\n"
            result += f"  Power State: {host.get('power_state', 'Unknown')}\n\n"

        return result.strip()

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "listing hosts")
    finally:
        client.close()


@mcp.tool()
def get_host_details(hostname: Optional[str] = None, host_id: Optional[str] = None) -> str:
    """Get detailed information about an ESXi host.

    Served by the inventory engine via bulk PropertyCollector fetches
    (never per-host calls): the vSphere Automation REST API has no
    per-host detail endpoint. Includes hardware, versions, cluster
    membership, and live quickStats.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        host_id: ESXi host ID (host-###) or name
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return 'Error: No hostname provided and VCENTER_HOST not set in environment'
    if not host_id:
        return "Error: host_id (host-### or host name) is required"

    fields = ["name", "host_id", "connection_state", "power_state",
              "maintenance_mode", "esxi_version", "build", "vendor",
              "model", "cpu_model", "cpu_cores", "cpu_threads",
              "memory_gb", "cluster", "vm_count", "cpu_usage_mhz",
              "memory_usage_gb", "uptime_days"]
    pv_client = PyVmomiClient(host=hostname)
    try:
        rows = fetch_inventory(pv_client, "host", fields)
    except Exception as e:
        return f"Error getting host {host_id} details: {e}"
    finally:
        pv_client.close()

    if host_id.startswith("host-"):
        matches = [r for r in rows if r.get("host_id") == host_id]
    else:
        matches = [r for r in rows
                   if (r.get("name") or "").lower() == host_id.lower()]
    if not matches:
        return (f"ESXi host '{host_id}' not found among {len(rows)} known "
                f"hosts. Use list_hosts to look up host names and IDs.")
    if len(matches) > 1:
        ids = ", ".join(str(r.get("host_id")) for r in matches)
        return f"Host name '{host_id}' is ambiguous ({ids}); use the ID."

    row = matches[0]
    lines = [f"Host Details: {row.get('name')}", ""]
    for field in fields[1:]:
        lines.append(f"  {field}: {row.get(field)}")
    return "\n".join(lines)


@mcp.tool()
def list_datacenters(hostname: Optional[str] = None) -> str:
    """List all datacenters.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if hostname is None:
        hostname = os.environ.get("VCENTER_HOST")
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        response = client.get("vcenter/datacenter")
        datacenters = response.get("value", [])

        if not datacenters:
            return "No datacenters found"

        result = f"Found {len(datacenters)} datacenters:\n\n"
        for dc in datacenters:
            result += f"• {dc.get('name', 'Unknown')} (ID: {dc.get('datacenter')})\n"

        return result.strip()

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "listing datacenters")
    finally:
        client.close()


@mcp.tool()
def get_datacenter_details(hostname: Optional[str] = None, datacenter_id: Optional[str] = None) -> str:
    """Get detailed information about a datacenter.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        datacenter_id: Datacenter ID
    """
    if hostname is None:
        hostname = os.environ.get("VCENTER_HOST")
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        response = client.get(f"vcenter/datacenter/{datacenter_id}")
        dc = response.get("value", {})

        if not dc:
            return f"Datacenter {datacenter_id} not found"

        result = f"Datacenter Details: {dc.get('name', 'Unknown')}\n"
        result += f"ID: {datacenter_id}\n"

        return result

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"getting datacenter {datacenter_id} details")
    finally:
        client.close()


@mcp.tool()
def list_datastores(
    hostname: Optional[str] = None,
    filters: Optional[List[FilterCondition]] = None,
    group_by: Optional[str] = None,
    fields: Optional[List[str]] = None,
    limit: int = 50,
    sort_by: Optional[str] = None,
    descending: bool = True,
) -> str:
    """List, filter, count, or rank datastores.

    With filters/group_by/fields/sort_by an in-process engine fetches
    properties for ALL datastores in one call and returns only the
    filtered/aggregated result.

    Queryable fields: name, datastore_id, type, capacity_gb, free_gb,
    free_pct, accessible, maintenance_mode, vm_count, host_count.
    Filter ops: eq, ne, contains, not_contains, gt, gte, lt, lte, in
    (conditions are ANDed; string matching is case-insensitive).

    Examples:
      Datastores below 10% free:
        filters=[{"field": "free_pct", "op": "lt", "value": 10}]
      Count by type: group_by="type"
      Fullest first: sort_by="free_pct", descending=false

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        filters: list of {field, op, value} conditions, ANDed
        group_by: return only a count distribution of this field
        fields: fields shown per datastore (default: name, type, capacity_gb, free_gb, free_pct)
        limit: max rows in listing or distribution output (default 50; 0 = unlimited)
        sort_by: order rows by this field before applying limit
        descending: sort direction (default True = biggest first)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    if filters or group_by or fields or sort_by:
        pv_client = PyVmomiClient(host=hostname)
        try:
            return run_inventory_query(pv_client, "datastore", filters=filters,
                                       group_by=group_by, fields=fields,
                                       limit=limit, sort_by=sort_by,
                                       descending=descending)
        except Exception as e:
            return f"Error querying datastores via pyVmomi: {e}"
        finally:
            pv_client.close()

    client = VSphereClient(hostname)
    try:
        response = client.get("vcenter/datastore")
        datastores = response.get("value", [])

        if not datastores:
            return "No datastores found"

        shown = datastores if limit == 0 else datastores[:limit]
        result = f"Found {len(datastores)} datastores"
        if len(shown) < len(datastores):
            result += f" (showing {len(shown)} — refine with filters or raise limit)"
        result += ":\n\n"
        for ds in shown:
            capacity = ds.get("capacity", 0)
            free_space = ds.get("free_space", 0)
            used_space = capacity - free_space
            used_pct = (used_space / capacity * 100) if capacity > 0 else 0

            result += f"• {ds.get('name', 'Unknown')} (ID: {ds.get('datastore')})\n"
            result += f"  Type: {ds.get('type', 'Unknown')}\n"
            result += f"  Capacity: {capacity / (1024**3):.1f} GB\n"
            result += f"  Used: {used_space / (1024**3):.1f} GB ({used_pct:.1f}%)\n"
            result += f"  Free: {free_space / (1024**3):.1f} GB\n\n"

        return result.strip()

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "listing datastores")
    finally:
        client.close()


@mcp.tool()
def get_datastore_details(hostname: Optional[str] = None, datastore_id: Optional[str] = None) -> str:
    """Get detailed information about a datastore.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        datastore_id: Datastore ID
    """
    if hostname is None:
        hostname = os.environ.get("VCENTER_HOST")
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        response = client.get(f"vcenter/datastore/{datastore_id}")
        ds = response.get("value", {})

        if not ds:
            return f"Datastore {datastore_id} not found"

        capacity = ds.get("capacity", 0) or 0
        free_space = ds.get("free_space", 0) or 0

        # Ensure values are positive
        if capacity <= 0 or free_space < 0:
            result = f"Datastore Details: {ds.get('name', 'Unknown')}\n"
            result += f"ID: {datastore_id}\n"
            result += f"Type: {ds.get('type', 'Unknown')}\n"
            result += "Capacity information not available or invalid\n"
            return result

        used_space = capacity - free_space
        used_pct = (used_space / capacity * 100) if capacity > 0 else 0

        result = f"Datastore Details: {ds.get('name', 'Unknown')}\n"
        result += f"ID: {datastore_id}\n"
        result += f"Type: {ds.get('type', 'Unknown')}\n"
        result += f"Capacity: {capacity / (1024**3):.1f} GB\n"
        result += f"Used: {used_space / (1024**3):.1f} GB ({used_pct:.1f}%)\n"
        result += f"Free: {free_space / (1024**3):.1f} GB\n"

        return result

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"getting datastore {datastore_id} details")
    finally:
        client.close()


# Organization Tools
@mcp.tool()
def list_folders(hostname: Optional[str] = None, folder_type: str = "VIRTUAL_MACHINE",
                 name_contains: Optional[str] = None, limit: int = 50) -> str:
    """List folders by type.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        folder_type: Folder type (VIRTUAL_MACHINE, HOST, DATACENTER, DATASTORE, NETWORK)
        name_contains: only folders whose name contains this (case-insensitive)
        limit: max folders shown (default 50; 0 = unlimited)
    """
    if hostname is None:
        hostname = os.environ.get("VCENTER_HOST")
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        response = client.get(f"vcenter/folder?filter.type={folder_type}")
        folders = response.get("value", [])
        if name_contains:
            folders = [f for f in folders
                       if name_contains.lower() in f.get("name", "").lower()]

        if not folders:
            return f"No {folder_type} folders found"

        shown = folders if limit == 0 else folders[:limit]
        result = f"Found {len(folders)} {folder_type} folders"
        if len(shown) < len(folders):
            result += (f" (showing {len(shown)} — filter with name_contains "
                       f"or raise limit)")
        result += ":\n\n"
        for folder in shown:
            result += (
                f"• {folder.get('name', 'Unknown')} (ID: {folder.get('folder')})\n"
            )

        return result.strip()

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"listing {folder_type} folders")
    finally:
        client.close()


@mcp.tool()
def get_folder_details(hostname: Optional[str] = None, folder_id: Optional[str] = None) -> str:
    """Get detailed information about a folder.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        folder_id: Folder ID
    """
    if hostname is None:
        hostname = os.environ.get("VCENTER_HOST")
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        response = client.get(f"vcenter/folder/{folder_id}")
        folder = response.get("value", {})

        if not folder:
            return f"Folder {folder_id} not found or inaccessible"

        result = f"Folder Details: {folder.get('name', 'Unknown')}\n"
        result += f"ID: {folder_id}\n"
        result += f"Type: {folder.get('type', 'Unknown')}\n"

        return result

    except (ConnectionError, ValueError, KeyError) as e:
        error_msg = str(e)
        if "404" in error_msg:
            return f"Folder {folder_id} not found or access denied (may be a system folder)"
        return _handle_error(e, f"getting folder {folder_id} details")
    finally:
        client.close()


# Network Tools
@mcp.tool()
def list_networks(
    hostname: Optional[str] = None,
    filters: Optional[List[FilterCondition]] = None,
    group_by: Optional[str] = None,
    fields: Optional[List[str]] = None,
    limit: int = 50,
    sort_by: Optional[str] = None,
    descending: bool = True,
) -> str:
    """List, filter, count, or rank networks (standard and distributed portgroups).

    With filters/group_by/fields/sort_by an in-process engine fetches
    properties for ALL networks in one call and returns only the
    filtered/aggregated result.

    Queryable fields: name, network_id, type, vlan_id, vm_count.
    (vlan_id is only available for distributed portgroups with a plain VLAN
    ID; standard portgroups and trunk/PVLAN configs report None.)
    Filter ops: eq, ne, contains, not_contains, gt, gte, lt, lte, in
    (conditions are ANDed; string matching is case-insensitive).

    Examples:
      Distributed portgroups on VLAN 120:
        filters=[{"field": "vlan_id", "op": "eq", "value": 120}]
      Count by network type: group_by="type"
      Most-used networks first: sort_by="vm_count"

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        filters: list of {field, op, value} conditions, ANDed
        group_by: return only a count distribution of this field
        fields: fields shown per network (default: name, type, vlan_id)
        limit: max rows in listing or distribution output (default 50; 0 = unlimited)
        sort_by: order rows by this field before applying limit
        descending: sort direction (default True = biggest first)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    if filters or group_by or fields or sort_by:
        pv_client = PyVmomiClient(host=hostname)
        try:
            return run_inventory_query(pv_client, "network", filters=filters,
                                       group_by=group_by, fields=fields,
                                       limit=limit, sort_by=sort_by,
                                       descending=descending)
        except Exception as e:
            return f"Error querying networks via pyVmomi: {e}"
        finally:
            pv_client.close()

    client = VSphereClient(hostname)
    try:
        response = client.get("vcenter/network")
        networks = response.get("value", [])

        if not networks:
            return "No networks found"

        shown = networks if limit == 0 else networks[:limit]
        result = f"Found {len(networks)} networks"
        if len(shown) < len(networks):
            result += f" (showing {len(shown)} — refine with filters or raise limit)"
        result += ":\n\n"
        for network in shown:
            name = network.get("name", "Unknown")
            result += f"• {name} (ID: {network.get('network')})\n"
            result += f"  Type: {network.get('type', 'Unknown')}\n"

            # Extract VLAN info from name
            vlan_match = re.search(r"v(\d+)-|VLAN(\d+)", name)
            if vlan_match:
                vlan_id = vlan_match.group(1) or vlan_match.group(2)
                result += f"  VLAN ID: {vlan_id}\n"

            result += "\n"

        return result.strip()

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "listing networks")
    finally:
        client.close()


@mcp.tool()
def get_network_details(hostname: Optional[str] = None, network_id: Optional[str] = None) -> str:
    """Get detailed information about a network.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        network_id: Network ID
    """
    if hostname is None:
        hostname = os.environ.get("VCENTER_HOST")
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        response = client.get(f"vcenter/network/{network_id}")
        network = response.get("value", {})

        if not network:
            return f"Network {network_id} not found or inaccessible"

        name = network.get("name", "Unknown")
        result = f"Network Details: {name}\n"
        result += f"ID: {network_id}\n"
        result += f"Type: {network.get('type', 'Unknown')}\n"

        # Extract VLAN info from name
        vlan_match = re.search(r"v(\d+)-|VLAN(\d+)", name)
        if vlan_match:
            vlan_id = vlan_match.group(1) or vlan_match.group(2)
            result += f"VLAN ID: {vlan_id}\n"

        return result

    except (ConnectionError, ValueError, KeyError) as e:
        error_msg = str(e)
        if "404" in error_msg:
            return (
                f"Network {network_id} not found or is a distributed portgroup "
                "(not accessible via this API)"
            )
        return _handle_error(e, f"getting network {network_id} details")
    finally:
        client.close()


@mcp.tool()
def get_vlan_info(hostname: Optional[str] = None, vlan_query: str = "") -> str:
    """Get information about a VLAN by name or VLAN ID.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        vlan_query: VLAN name (e.g., v1306-MEL03-Secure-Management) or VLAN ID (e.g., 1306)
    """
    if hostname is None:
        hostname = os.environ.get("VCENTER_HOST")
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        response = client.get("vcenter/network")
        networks = response.get("value", [])

        if not networks:
            return "No networks found"

        matches = []

        # Search by name (partial match, case-insensitive)
        for network in networks:
            name = network.get("name", "")
            if vlan_query.lower() in name.lower():
                matches.append(network)

        # If no name matches and query is numeric, search by VLAN ID
        if not matches and vlan_query.isdigit():
            vlan_id = vlan_query
            for network in networks:
                name = network.get("name", "")
                vlan_match = re.search(r"v(\d+)-|VLAN(\d+)", name)
                if vlan_match:
                    extracted_vlan = vlan_match.group(1) or vlan_match.group(2)
                    if extracted_vlan == vlan_id:
                        matches.append(network)

        if not matches:
            return f"No VLAN found matching '{vlan_query}'"

        result = f"VLAN Search Results for '{vlan_query}':\n\n"

        for network in matches:
            name = network.get("name", "Unknown")
            result += f"• {name}\n"
            result += f"  Network ID: {network.get('network', 'Unknown')}\n"
            result += f"  Type: {network.get('type', 'Unknown')}\n"

            # Extract VLAN ID from name
            vlan_match = re.search(r"v(\d+)-|VLAN(\d+)", name)
            if vlan_match:
                vlan_id = vlan_match.group(1) or vlan_match.group(2)
                result += f"  VLAN ID: {vlan_id}\n"

            result += "\n"

        result += f"Found {len(matches)} matching network(s)"
        return result

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"searching for VLAN '{vlan_query}'")
    finally:
        client.close()


@mcp.tool()
def list_vlans(hostname: Optional[str] = None) -> str:
    """Extract and list VLAN information from network names.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if hostname is None:
        hostname = os.environ.get("VCENTER_HOST")
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        response = client.get("vcenter/network")
        networks = response.get("value", [])

        if not networks:
            return "No networks found"

        vlans: Dict[str, List[str]] = {}
        for network in networks:
            name = network.get("name", "Unknown")
            vlan_match = re.search(r"v(\d+)-|VLAN(\d+)", name)
            if vlan_match:
                vlan_id = vlan_match.group(1) or vlan_match.group(2)
                if vlan_id not in vlans:
                    vlans[vlan_id] = []
                vlans[vlan_id].append(name)

        if not vlans:
            return "No VLAN information found in network names"

        result = f"Found {len(vlans)} VLANs:\n\n"
        for vlan_id in sorted(vlans.keys(), key=int):
            result += f"VLAN {vlan_id}:\n"
            for network_name in vlans[vlan_id]:
                result += f"  • {network_name}\n"
            result += "\n"

        return result.strip()

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "extracting VLAN information")
    finally:
        client.close()


@mcp.tool()
def get_vm_disk_usage(hostname: Optional[str] = None, limit: int = 15) -> str:
    """Top VMs by space actually used on datastores (one bulk query).

    Shows the biggest consumers first: disk count, committed (used) and
    provisioned (maximum if thin disks fill up) storage per VM. For other
    views — different sort, filters, per-VM detail — use list_vms with
    filters/sort_by/fields, or get_vm_storage_info for a specific VM's
    datastore placement.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        limit: number of VMs shown (default 15; 0 = all)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    pv_client = PyVmomiClient(host=hostname)
    try:
        listing = run_inventory_query(
            pv_client, "vm",
            fields=["name", "disk_count", "storage_committed_gb",
                    "storage_provisioned_gb"],
            sort_by="storage_committed_gb", limit=limit)
        return (f"Top VMs by used storage (committed GB on datastores):\n\n"
                f"{listing}")
    except Exception as e:
        return f"Error getting VM disk usage: {e}"
    finally:
        pv_client.close()


@mcp.tool()
def get_vm_storage_info(hostname: Optional[str] = None, vm_id: Optional[str] = None,
                        limit: int = 15) -> str:
    """Storage info: per-VM disk detail, or top storage consumers overall.

    With vm_id (ID or name): that VM's disks and datastores.
    Without vm_id: the biggest storage consumers by provisioned space,
    via one bulk query. For other views use list_vms with
    filters/sort_by/fields.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        vm_id: Virtual machine ID or name (optional)
        limit: number of VMs shown in the overview (default 15; 0 = all)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    if vm_id is None:
        pv_client = PyVmomiClient(host=hostname)
        try:
            listing = run_inventory_query(
                pv_client, "vm",
                fields=["name", "storage_provisioned_gb",
                        "storage_committed_gb", "disk_count"],
                sort_by="storage_provisioned_gb", limit=limit)
            return (f"Top VMs by provisioned storage (committed = used on "
                    f"datastores; provisioned = maximum if thin disks "
                    f"fill up):\n\n{listing}")
        except Exception as e:
            return f"Error getting VM storage overview: {e}"
        finally:
            pv_client.close()

    client = VSphereClient(hostname)
    try:
        # If vm_id doesn't start with 'vm-', assume it's a name and look up the ID
        if not vm_id.startswith("vm-"):
            vms_response = client.get("vcenter/vm")
            vms = vms_response.get("value", [])
            vm_id_found = None
            for vm in vms:
                if vm.get("name", "").lower() == vm_id.lower():
                    vm_id_found = vm.get("vm")
                    break
            if not vm_id_found:
                return f"Virtual machine '{vm_id}' not found by name"
            vm_id = vm_id_found

        vm_details = client.get(f"vcenter/vm/{vm_id}")
        vm_data = vm_details.get("value", {})

        result = f"Storage Information for {vm_data.get('name', vm_id)} (ID: {vm_id}):\n\n"
        disks = vm_data.get("disks", [])
        if disks:
            for i, disk in enumerate(disks):
                # The REST detail wraps each disk as {key, value: {...}}.
                info = disk.get("value", disk) if isinstance(disk, dict) else {}
                capacity = info.get("capacity", 0)
                label = info.get("label") or f"Disk {i}"
                if capacity > 0:
                    result += f"  {label}: {capacity / (1024**3):.1f} GB allocated\n"
                else:
                    result += f"  {label}: Size not available\n"
        else:
            result += "  No disk information available\n"

        datastores = vm_data.get("datastores", [])
        if datastores:
            result += "  Datastores:\n"
            for ds in datastores:
                result += f"    - {ds}\n"

        return result.strip()

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "getting VM storage information")
    finally:
        client.close()


@mcp.tool()
def get_datastore_usage(hostname: Optional[str] = None) -> str:
    """Get datastore usage information to identify potential storage issues.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # Get all datastores
        response = client.get("vcenter/datastore")
        datastores = response.get("value", [])
        
        if not datastores:
            return "No datastores found"
        
        result = f"Datastore Usage Report:\n\n"
        high_usage_ds = []
        
        for ds in datastores:
            ds_id = ds.get('datastore')
            ds_name = ds.get('name', 'Unknown')
            capacity = ds.get("capacity", 0)
            free_space = ds.get("free_space", 0)
            
            if capacity > 0 and free_space >= 0:
                used_space = capacity - free_space
                used_pct = (used_space / capacity * 100) if capacity > 0 else 0
                
                result += f"• {ds_name}\n"
                result += f"  Capacity: {capacity / (1024**3):.1f} GB\n"
                result += f"  Used: {used_space / (1024**3):.1f} GB ({used_pct:.1f}%)\n"
                result += f"  Free: {free_space / (1024**3):.1f} GB\n"
                
                if used_pct > 90:
                    high_usage_ds.append(f"{ds_name} ({used_pct:.1f}%)")
                
                result += "\n"
        
        if high_usage_ds:
            result += f"⚠️  Datastores with >90% usage:\n"
            for ds in high_usage_ds:
                result += f"  - {ds}\n"
            result += "\n"
        
        result += "Note: This shows datastore usage, not individual VM disk usage."
        
        return result.strip()
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "getting datastore usage")
    finally:
        client.close()


@mcp.tool()
def get_vm_performance_info(hostname: Optional[str] = None, limit: int = 15) -> str:
    """Top powered-on VMs by live CPU usage, with memory usage (bulk queries, never per-VM).

    cpu_usage_mhz and memory_usage_gb are real-time usage from vCenter
    quickStats; cpu_count/memory_mb are the allocation. For other views
    (sort by memory, filter to a cluster, more rows) use list_vms with
    filters/sort_by/fields.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        limit: number of VMs shown (default 15; 0 = all)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    pv_client = PyVmomiClient(host=hostname)
    try:
        listing = run_inventory_query(
            pv_client, "vm",
            filters=[FilterCondition(field="power_state", op="eq",
                                     value="poweredOn")],
            fields=["name", "cpu_usage_mhz", "cpu_count",
                    "memory_usage_gb", "memory_mb", "host"],
            sort_by="cpu_usage_mhz", limit=limit)
        return f"Top powered-on VMs by CPU usage:\n\n{listing}"
    except Exception as e:
        return f"Error getting VM performance info: {e}"
    finally:
        pv_client.close()


@mcp.tool()
def get_host_performance_info(hostname: Optional[str] = None, limit: int = 25) -> str:
    """ESXi hosts ranked by live CPU usage, with capacity and load (one bulk query).

    cpu_usage_mhz, memory_usage_gb, and uptime_days are real-time from
    vCenter quickStats; cpu_cores/cpu_threads/memory_gb are the hardware
    capacity. For other views use list_hosts with filters/sort_by/fields.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        limit: number of hosts shown (default 25; 0 = all)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    pv_client = PyVmomiClient(host=hostname)
    try:
        listing = run_inventory_query(
            pv_client, "host",
            fields=["name", "cpu_usage_mhz", "cpu_cores", "memory_usage_gb",
                    "memory_gb", "vm_count", "uptime_days"],
            sort_by="cpu_usage_mhz", limit=limit)
        return f"Hosts by CPU usage:\n\n{listing}"
    except Exception as e:
        return f"Error getting host performance info: {e}"
    finally:
        pv_client.close()


@mcp.tool()
def get_vms_with_high_resource_usage(hostname: Optional[str] = None,
                                     cpu_threshold: int = 8,
                                     memory_gb_threshold: int = 16,
                                     limit: int = 15) -> str:
    """Powered-on VMs with large CPU/memory allocations (two bulk queries).

    Lists VMs at or above the thresholds, biggest first, with live usage
    (cpu_usage_mhz / memory_usage_gb) next to the allocation. For custom
    queries use list_vms with filters/sort_by.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        cpu_threshold: minimum vCPU count to report (default 8)
        memory_gb_threshold: minimum memory in GB to report (default 16)
        limit: max VMs per section (default 15; 0 = all)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    powered_on = FilterCondition(field="power_state", op="eq",
                                 value="poweredOn")
    pv_client = PyVmomiClient(host=hostname)
    try:
        high_cpu = run_inventory_query(
            pv_client, "vm",
            filters=[powered_on,
                     FilterCondition(field="cpu_count", op="gte",
                                     value=cpu_threshold)],
            fields=["name", "cpu_count", "cpu_usage_mhz", "host"],
            sort_by="cpu_count", limit=limit)
        high_memory = run_inventory_query(
            pv_client, "vm",
            filters=[powered_on,
                     FilterCondition(field="memory_mb", op="gte",
                                     value=memory_gb_threshold * 1024)],
            fields=["name", "memory_mb", "memory_usage_gb", "host"],
            sort_by="memory_mb", limit=limit)
        return (f"Powered-on VMs with {cpu_threshold}+ vCPUs:\n\n{high_cpu}"
                f"\n\nPowered-on VMs with {memory_gb_threshold}+ GB memory:"
                f"\n\n{high_memory}"
                "\n\nNote: cpu_count/memory_mb are allocations; "
                "cpu_usage_mhz/memory_usage_gb show actual current usage.")
    except Exception as e:
        return f"Error getting VMs with high resource usage: {e}"
    finally:
        pv_client.close()


# Snapshot Management Tools
@mcp.tool()
def list_vm_snapshots(
    vm_id: Optional[str] = None,
    hostname: Optional[str] = None,
    filters: Optional[List[FilterCondition]] = None,
    group_by: Optional[str] = None,
    fields: Optional[List[str]] = None,
    limit: int = 50,
    sort_by: Optional[str] = None,
    descending: bool = True,
) -> str:
    """List snapshots for one VM, or query snapshots across ALL VMs.

    Uses pyVmomi: the vSphere Automation REST API has no snapshot
    endpoints.

    With vm_id: the snapshot tree for that VM — children indented under
    their parent, the currently active snapshot marked.

    Without vm_id: an in-process engine fetches every snapshot of every
    VM in one call, filters/sorts/aggregates server-side, and returns
    only the result — use this for fleet-wide questions instead of
    calling this tool once per VM.

    Queryable fields (fleet mode): name, snapshot_id, vm_name, vm_id,
    created (UTC, YYYY-MM-DD HH:MM:SS), age_days, state, description,
    quiesced, depth, is_current.
    Filter ops: eq, ne, contains, not_contains, gt, gte, lt, lte, in
    (conditions are ANDed; string matching is case-insensitive).

    Examples (fleet mode):
      The 10 oldest snapshots anywhere:
        sort_by="age_days", limit=10
      Snapshots older than 30 days:
        filters=[{"field": "age_days", "op": "gt", "value": 30}]
      Snapshot count per VM:
        group_by="vm_name"

    Args:
        vm_id: Virtual machine ID (vm-###) or name; omit to query across all VMs
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        filters: list of {field, op, value} conditions, ANDed (fleet mode)
        group_by: return only a count distribution of this field (fleet mode)
        fields: fields shown per snapshot (default: name, vm_name, age_days, created) (fleet mode)
        limit: max rows in listing or distribution output (default 50; 0 = unlimited) (fleet mode)
        sort_by: order rows by this field before applying limit (fleet mode)
        descending: sort direction (default True = biggest/oldest first)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    pv_client = PyVmomiClient(host=hostname)
    try:
        if vm_id is None:
            return run_inventory_query(pv_client, "snapshot",
                                       filters=filters, group_by=group_by,
                                       fields=fields, limit=limit,
                                       sort_by=sort_by, descending=descending)
        vm, error = find_object(pv_client, "vm", vm_id, "vm-")
        if vm is None:
            return error or f"VM '{vm_id}' not found"
        snapshot_info = vm.snapshot
        rows = flatten_snapshot_tree(
            snapshot_info.rootSnapshotList if snapshot_info else None)
        if not rows:
            return f"No snapshots found for VM {vm.name} ({vm._moId})"
        current_id = (getattr(snapshot_info.currentSnapshot, "_moId", None)
                      if snapshot_info else None)
        lines = [f"Snapshots for VM {vm.name} ({vm._moId}) — "
                 f"{len(rows)} total:", ""]
        for row in rows:
            indent = "  " * row["depth"]
            marker = "  ← current" if row["snapshot_id"] == current_id else ""
            lines.append(
                f"{indent}• {row['name']} (ID: {row['snapshot_id']}){marker}")
            lines.append(f"{indent}  Created: {row['created']}, "
                         f"state: {row['state']}, quiesced: {row['quiesced']}")
            if row["description"]:
                lines.append(f"{indent}  Description: {row['description']}")
        return "\n".join(lines)
    except Exception as e:
        target = f"VM {vm_id}" if vm_id else "all VMs"
        return f"Error listing snapshots for {target}: {e}"
    finally:
        pv_client.close()


@mcp.tool()
def create_vm_snapshot(vm_id: str, snapshot_name: str, description: str = "",
                       memory: bool = False, quiesce: bool = False,
                       hostname: Optional[str] = None) -> str:
    """Create a snapshot for a specific VM.

    Uses pyVmomi: the vSphere Automation REST API has no snapshot
    endpoints. Waits for the snapshot task to finish.

    Args:
        vm_id: Virtual machine ID (vm-###) or name
        snapshot_name: Name for the snapshot
        description: Description for the snapshot (optional)
        memory: Also capture the VM's memory state (powered-on VMs only)
        quiesce: Quiesce the guest filesystem via VMware Tools (needs
            running tools; ignored for powered-off VMs)
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    pv_client = PyVmomiClient(host=hostname)
    try:
        vm, error = find_object(pv_client, "vm", vm_id, "vm-")
        if vm is None:
            return error or f"VM '{vm_id}' not found"
        task = vm.CreateSnapshot_Task(
            name=snapshot_name, description=description,
            memory=memory, quiesce=quiesce)
        info, timed_out = _wait_for_pyvmomi_task(task)
        if timed_out:
            return (f"Snapshot task for VM {vm.name} still running after "
                    f"300s (state={info.state}); check vCenter tasks.")
        if info.state == vim.TaskInfo.State.success:
            snap_id = getattr(info.result, "_moId", "unknown")
            return (f"✅ Snapshot '{snapshot_name}' created for VM "
                    f"{vm.name} ({vm._moId}). Snapshot ID: {snap_id}")
        fault = info.error
        return (f"❌ Snapshot creation failed for VM {vm.name}: "
                f"{fault.msg if fault else 'Unknown error'}")
    except Exception as e:
        return f"Error creating snapshot for VM {vm_id}: {e}"
    finally:
        pv_client.close()


# Template Management Tools
@mcp.tool()
def list_templates(hostname: Optional[str] = None) -> str:
    """List all VM templates.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # Get all VMs and filter for templates
        response = client.get("vcenter/vm")
        vms = response.get("value", [])
        
        templates = []
        for vm in vms:
            # Check if VM is a template (this might need adjustment based on your vSphere setup)
            vm_name = vm.get('name', '')
            if 'template' in vm_name.lower() or vm.get('template', False):
                templates.append(vm)
        
        if not templates:
            return "No templates found"
        
        result = f"Found {len(templates)} templates:\n\n"
        for template in templates:
            result += f"• {template.get('name', 'Unknown')} (ID: {template.get('vm')})\n"
            result += f"  Power State: {template.get('power_state', 'Unknown')}\n"
            result += f"  Guest OS: {template.get('guest_OS', 'Unknown')}\n\n"
        
        return result.strip()
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "listing templates")
    finally:
        client.close()


# Advanced Monitoring Tools
@mcp.tool()
def get_vm_events(vm_id: str, hostname: Optional[str] = None) -> str:
    """Get recent events for a specific VM.

    Args:
        vm_id: Virtual machine ID or name
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # If vm_id doesn't start with 'vm-', assume it's a name and look up the ID
        if not vm_id.startswith("vm-"):
            vms_response = client.get("vcenter/vm")
            vms = vms_response.get("value", [])
            
            vm_id_found = None
            for vm in vms:
                if vm.get("name", "").lower() == vm_id.lower():
                    vm_id_found = vm.get("vm")
                    break
            
            if not vm_id_found:
                return f"Virtual machine '{vm_id}' not found by name"
            vm_id = vm_id_found
        
        # Get VM events (this endpoint might vary based on vSphere version)
        try:
            response = client.get(f"vcenter/vm/{vm_id}/events")
            events = response.get("value", [])
            
            if not events:
                return f"No recent events found for VM {vm_id}"
            
            result = f"Recent Events for VM {vm_id}:\n\n"
            for event in events[:10]:  # Show last 10 events
                result += f"• {event.get('event_type', 'Unknown')}\n"
                result += f"  Time: {event.get('time', 'Unknown')}\n"
                result += f"  Description: {event.get('description', 'No description')}\n\n"
            
            return result.strip()
            
        except Exception:
            return f"Events endpoint not available for VM {vm_id}. This feature requires specific vSphere API permissions."
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"getting events for VM {vm_id}")
    finally:
        client.close()


@mcp.tool()
def get_alarms(hostname: Optional[str] = None) -> str:
    """Get active alarms in the vSphere environment.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # Get alarms (this endpoint might vary based on vSphere version)
        try:
            response = client.get("vcenter/alarm")
            alarms = response.get("value", [])
            
            if not alarms:
                return "No active alarms found"
            
            result = f"Active Alarms ({len(alarms)}):\n\n"
            for alarm in alarms:
                result += f"• {alarm.get('name', 'Unknown')}\n"
                result += f"  Status: {alarm.get('status', 'Unknown')}\n"
                result += f"  Severity: {alarm.get('severity', 'Unknown')}\n"
                result += f"  Description: {alarm.get('description', 'No description')}\n\n"
            
            return result.strip()
            
        except Exception:
            return "Alarms endpoint not available. This feature requires specific vSphere API permissions."
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "getting alarms")
    finally:
        client.close()


# Network Management Tools
@mcp.tool()
def get_port_groups(hostname: Optional[str] = None, limit: int = 50) -> str:
    """Get port groups in the vSphere environment.

    For filtering, counting, or VLAN lookups prefer list_networks with
    filters/group_by.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        limit: max port groups shown (default 50; 0 = unlimited)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    client = VSphereClient(hostname)
    try:
        # Get port groups (this might be available through network endpoints)
        response = client.get("vcenter/network")
        networks = response.get("value", [])

        if not networks:
            return "No networks found"

        shown = networks if limit == 0 else networks[:limit]
        result = f"Network Port Groups ({len(networks)})"
        if len(shown) < len(networks):
            result += f" (showing {len(shown)} — use list_networks filters or raise limit)"
        result += ":\n\n"
        for network in shown:
            result += f"• {network.get('name', 'Unknown')}\n"
            result += f"  Type: {network.get('type', 'Unknown')}\n"
            result += f"  ID: {network.get('network', 'Unknown')}\n\n"

        return result.strip()

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "getting port groups")
    finally:
        client.close()


# Reporting and Analytics Tools
@mcp.tool()
def generate_vm_report(hostname: Optional[str] = None) -> str:
    """Compact environment report: totals, distributions, top consumers.

    Fixed-size summary built from a few bulk queries — never a full VM
    dump. Drill into any section with list_vms / list_datastores using
    filters, group_by, or sort_by.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    pv_client = PyVmomiClient(host=hostname)
    try:
        vm_rows = fetch_inventory(
            pv_client, "vm",
            ["name", "power_state", "template", "guest_os", "cpu_count",
             "memory_mb", "storage_committed_gb", "snapshot_count"])
        host_rows = fetch_inventory(pv_client, "host", ["name"])
        ds_rows = fetch_inventory(
            pv_client, "datastore",
            ["name", "capacity_gb", "free_gb", "free_pct"])
    except Exception as e:
        return f"Error generating VM report: {e}"
    finally:
        pv_client.close()

    templates = [r for r in vm_rows if r.get("template")]
    vms = [r for r in vm_rows if not r.get("template")]
    powered_on = [r for r in vms if str(r.get("power_state")) == "poweredOn"]

    result = "=== vSphere VM Report ===\n"
    result += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    result += f"Total VMs: {len(vms)} (+ {len(templates)} templates)\n"
    result += f"Total Hosts: {len(host_rows)}\n"
    result += f"Total Datastores: {len(ds_rows)}\n\n"

    result += "=== VM Summary ===\n"
    result += f"Powered On: {len(powered_on)}\n"
    result += f"Powered Off: {len(vms) - len(powered_on)}\n"
    result += (f"vCPUs allocated (powered-on): "
               f"{sum(r.get('cpu_count') or 0 for r in powered_on)}\n")
    result += (f"Memory allocated (powered-on): "
               f"{sum(r.get('memory_mb') or 0 for r in powered_on) / 1024:.1f} GB\n")
    result += (f"VMs with snapshots: "
               f"{sum(1 for r in vms if (r.get('snapshot_count') or 0) > 0)}\n\n")

    result += "=== Top Guest OS (count) ===\n"
    for value, count in aggregate(vms, "guest_os")[:5]:
        result += f"• {value}: {count}\n"

    result += "\n=== Top 5 VMs by used storage ===\n"
    for row in sort_rows(vms, "storage_committed_gb")[:5]:
        result += f"• {row.get('name')}: {row.get('storage_committed_gb')} GB\n"

    result += "\n=== Datastore Summary (lowest free space first) ===\n"
    ds_sorted = sort_rows(ds_rows, "free_pct", descending=False)
    for row in ds_sorted[:5]:
        used_pct = (100 - row["free_pct"]) if row.get("free_pct") is not None else None
        used = f"{used_pct:.1f}% used" if used_pct is not None else "usage unknown"
        result += (f"• {row.get('name')}: {used} "
                   f"({row.get('free_gb')} of {row.get('capacity_gb')} GB free)\n")
    if len(ds_sorted) > 5:
        result += (f"• … and {len(ds_sorted) - 5} more datastores "
                   f"(see list_datastores)\n")

    return result.strip()


@mcp.tool()
def get_resource_utilization_summary(hostname: Optional[str] = None) -> str:
    """Get a summary of resource utilization across the vSphere environment.

    Fixed-size summary from three bulk queries: vCPU/memory allocation of
    powered-on VMs vs. host capacity and live host usage, plus datastore
    totals.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    pv_client = PyVmomiClient(host=hostname)
    try:
        vm_rows = fetch_inventory(
            pv_client, "vm", ["power_state", "template", "cpu_count",
                              "memory_mb"])
        host_rows = fetch_inventory(
            pv_client, "host", ["cpu_cores", "cpu_threads", "memory_gb",
                                "cpu_usage_mhz", "memory_usage_gb"])
        ds_rows = fetch_inventory(pv_client, "datastore",
                                  ["capacity_gb", "free_gb"])
    except Exception as e:
        return f"Error getting resource utilization summary: {e}"
    finally:
        pv_client.close()

    powered_on = [r for r in vm_rows
                  if not r.get("template")
                  and str(r.get("power_state")) == "poweredOn"]
    total_vcpus = sum(r.get("cpu_count") or 0 for r in powered_on)
    total_vm_memory_gb = sum(r.get("memory_mb") or 0 for r in powered_on) / 1024
    total_cores = sum(r.get("cpu_cores") or 0 for r in host_rows)
    total_threads = sum(r.get("cpu_threads") or 0 for r in host_rows)
    total_host_memory_gb = sum(r.get("memory_gb") or 0 for r in host_rows)
    host_cpu_usage_ghz = sum(r.get("cpu_usage_mhz") or 0 for r in host_rows) / 1000
    host_memory_usage_gb = sum(r.get("memory_usage_gb") or 0 for r in host_rows)
    total_capacity_gb = sum(r.get("capacity_gb") or 0 for r in ds_rows)
    total_free_gb = sum(r.get("free_gb") or 0 for r in ds_rows)
    total_used_gb = total_capacity_gb - total_free_gb

    result = "=== Resource Utilization Summary ===\n\n"
    result += "CPU:\n"
    result += f"  vCPUs allocated (powered-on VMs): {total_vcpus}\n"
    result += f"  Physical cores: {total_cores} ({total_threads} threads)\n"
    if total_cores:
        result += f"  vCPU to core ratio: {total_vcpus / total_cores:.2f}:1\n"
    result += f"  Current host CPU usage: {host_cpu_usage_ghz:.1f} GHz\n\n"

    result += "Memory:\n"
    result += f"  VM memory allocated (powered-on): {total_vm_memory_gb:.1f} GB\n"
    result += f"  Host memory capacity: {total_host_memory_gb:.1f} GB\n"
    if total_host_memory_gb:
        result += (f"  Allocation vs capacity: "
                   f"{total_vm_memory_gb / total_host_memory_gb * 100:.1f}%\n")
    result += f"  Current host memory usage: {host_memory_usage_gb:.1f} GB\n\n"

    result += "Storage:\n"
    result += f"  Total capacity: {total_capacity_gb:.1f} GB\n"
    if total_capacity_gb:
        result += (f"  Total used: {total_used_gb:.1f} GB "
                   f"({total_used_gb / total_capacity_gb * 100:.1f}%)\n")
    result += f"  Total free: {total_free_gb:.1f} GB"

    return result.strip()


# Automation Tools
@mcp.tool()
def bulk_power_operations(operation: str, vm_list: str, hostname: Optional[str] = None) -> str:
    """Perform bulk power operations on multiple VMs.

    Args:
        operation: Power operation ('on', 'off', 'restart')
        vm_list: Comma-separated list of VM names or IDs
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    if operation not in ['on', 'off', 'restart']:
        return "Error: Operation must be 'on', 'off', or 'restart'"
    
    client = VSphereClient(hostname)
    try:
        # Get all VMs to resolve names to IDs
        response = client.get("vcenter/vm")
        vms = response.get("value", [])
        
        vm_names = [name.strip() for name in vm_list.split(',')]
        results = []
        
        for vm_name in vm_names:
            vm_id = None
            for vm in vms:
                if vm.get("name", "").lower() == vm_name.lower():
                    vm_id = vm.get("vm")
                    break
            
            if not vm_id:
                results.append(f"❌ {vm_name}: VM not found")
                continue
            
            try:
                if operation == 'on':
                    client.post(f"vcenter/vm/{vm_id}/power/start")
                    results.append(f"✅ {vm_name}: Power on initiated")
                elif operation == 'off':
                    client.post(f"vcenter/vm/{vm_id}/power/stop")
                    results.append(f"✅ {vm_name}: Power off initiated")
                elif operation == 'restart':
                    client.post(f"vcenter/vm/{vm_id}/power/reset")
                    results.append(f"✅ {vm_name}: Restart initiated")
            except Exception as e:
                results.append(f"❌ {vm_name}: Error - {str(e)}")
        
        result = f"Bulk {operation.upper()} Operation Results:\n\n"
        for res in results:
            result += f"{res}\n"
        
        return result.strip()
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"bulk {operation} operation")
    finally:
        client.close()


# Destructive Operations with Confirmation
@mcp.tool()
def delete_vm_snapshot(vm_id: str, snapshot_id: str, confirm: bool = False, hostname: Optional[str] = None) -> str:
    """Delete a snapshot for a specific VM. REQUIRES CONFIRMATION.

    Uses pyVmomi: the vSphere Automation REST API has no snapshot
    endpoints. Child snapshots are kept (only this snapshot is removed).

    Args:
        vm_id: Virtual machine ID (vm-###) or name
        snapshot_id: Snapshot ID (snapshot-###) or snapshot name
        confirm: Must be True to proceed with deletion
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not confirm:
        return f"⚠️  DESTRUCTIVE OPERATION: Delete snapshot {snapshot_id} for VM {vm_id}\n\nThis operation cannot be undone!\n\nTo proceed, call this function again with confirm=True"

    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    pv_client = PyVmomiClient(host=hostname)
    try:
        vm, error = find_object(pv_client, "vm", vm_id, "vm-")
        if vm is None:
            return error or f"VM '{vm_id}' not found"
        snapshot_info = vm.snapshot
        rows = flatten_snapshot_tree(
            snapshot_info.rootSnapshotList if snapshot_info else None)
        if not rows:
            return f"No snapshots found for VM {vm.name} ({vm._moId})"
        row, error = match_snapshot(rows, snapshot_id)
        if row is None:
            return error or f"Snapshot '{snapshot_id}' not found"
        task = row["snapshot_obj"].RemoveSnapshot_Task(removeChildren=False)
        info, timed_out = _wait_for_pyvmomi_task(task)
        if timed_out:
            return (f"Snapshot removal task for VM {vm.name} still running "
                    f"after 300s (state={info.state}); check vCenter tasks.")
        if info.state == vim.TaskInfo.State.success:
            return (f"✅ Snapshot '{row['name']}' ({row['snapshot_id']}) "
                    f"deleted for VM {vm.name} ({vm._moId})")
        fault = info.error
        return (f"❌ Snapshot deletion failed for VM {vm.name}: "
                f"{fault.msg if fault else 'Unknown error'}")
    except Exception as e:
        return f"Error deleting snapshot {snapshot_id} for VM {vm_id}: {e}"
    finally:
        pv_client.close()


@mcp.tool()
def delete_vm(vm_id: str, confirm: bool = False, hostname: Optional[str] = None) -> str:
    """Delete a virtual machine. REQUIRES CONFIRMATION.

    Args:
        vm_id: Virtual machine ID or name
        confirm: Must be True to proceed with deletion
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not confirm:
        return f"⚠️  DESTRUCTIVE OPERATION: Delete VM {vm_id}\n\nThis operation will permanently delete the virtual machine and all its data!\nThis operation cannot be undone!\n\nTo proceed, call this function again with confirm=True"
    
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # If vm_id doesn't start with 'vm-', assume it's a name and look up the ID
        if not vm_id.startswith("vm-"):
            vms_response = client.get("vcenter/vm")
            vms = vms_response.get("value", [])
            
            vm_id_found = None
            for vm in vms:
                if vm.get("name", "").lower() == vm_id.lower():
                    vm_id_found = vm.get("vm")
                    break
            
            if not vm_id_found:
                return f"Virtual machine '{vm_id}' not found by name"
            vm_id = vm_id_found
        
        # Delete VM
        client.delete(f"vcenter/vm/{vm_id}")
        return f"✅ VM {vm_id} deleted successfully"
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"deleting VM {vm_id}")
    finally:
        client.close()


@mcp.tool()
def modify_vm_resources(vm_id: str, cpu_count: Optional[int] = None, memory_gb: Optional[int] = None, confirm: bool = False, hostname: Optional[str] = None) -> str:
    """Modify VM resources (CPU and/or Memory). REQUIRES CONFIRMATION.

    Args:
        vm_id: Virtual machine ID or name
        cpu_count: New CPU count (optional)
        memory_gb: New memory in GB (optional)
        confirm: Must be True to proceed with modification
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not confirm:
        changes = []
        if cpu_count is not None:
            changes.append(f"CPU: {cpu_count} vCPUs")
        if memory_gb is not None:
            changes.append(f"Memory: {memory_gb} GB")
        
        return f"⚠️  DESTRUCTIVE OPERATION: Modify VM {vm_id}\n\nProposed changes:\n" + "\n".join(f"  - {change}" for change in changes) + "\n\nThis operation will modify the VM configuration!\n\nTo proceed, call this function again with confirm=True"
    
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    if cpu_count is None and memory_gb is None:
        return "Error: At least one resource (CPU or Memory) must be specified"
    
    client = VSphereClient(hostname)
    try:
        # If vm_id doesn't start with 'vm-', assume it's a name and look up the ID
        if not vm_id.startswith("vm-"):
            vms_response = client.get("vcenter/vm")
            vms = vms_response.get("value", [])
            
            vm_id_found = None
            for vm in vms:
                if vm.get("name", "").lower() == vm_id.lower():
                    vm_id_found = vm.get("vm")
                    break
            
            if not vm_id_found:
                return f"Virtual machine '{vm_id}' not found by name"
            vm_id = vm_id_found
        
        # Prepare modification data
        modification_data = {}
        
        if cpu_count is not None:
            modification_data["cpu"] = {"count": cpu_count}
        
        if memory_gb is not None:
            modification_data["memory"] = {"size_MiB": memory_gb * 1024}
        
        # Apply modifications
        client.patch(f"vcenter/vm/{vm_id}", modification_data)
        
        changes = []
        if cpu_count is not None:
            changes.append(f"CPU: {cpu_count} vCPUs")
        if memory_gb is not None:
            changes.append(f"Memory: {memory_gb} GB")
        
        return f"✅ VM {vm_id} modified successfully:\n" + "\n".join(f"  - {change}" for change in changes)
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"modifying VM {vm_id}")
    finally:
        client.close()


@mcp.tool()
def bulk_delete_vms(vm_list: str, confirm: bool = False, hostname: Optional[str] = None) -> str:
    """Delete multiple VMs. REQUIRES CONFIRMATION.

    Args:
        vm_list: Comma-separated list of VM names or IDs
        confirm: Must be True to proceed with deletion
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not confirm:
        vm_names = [name.strip() for name in vm_list.split(',')]
        return f"⚠️  DESTRUCTIVE OPERATION: Delete Multiple VMs\n\nVMs to be deleted:\n" + "\n".join(f"  - {name}" for name in vm_names) + "\n\nThis operation will permanently delete all specified VMs and all their data!\nThis operation cannot be undone!\n\nTo proceed, call this function again with confirm=True"
    
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # Get all VMs to resolve names to IDs
        response = client.get("vcenter/vm")
        vms = response.get("value", [])
        
        vm_names = [name.strip() for name in vm_list.split(',')]
        results = []
        
        for vm_name in vm_names:
            vm_id = None
            for vm in vms:
                if vm.get("name", "").lower() == vm_name.lower():
                    vm_id = vm.get("vm")
                    break
            
            if not vm_id:
                results.append(f"❌ {vm_name}: VM not found")
                continue
            
            try:
                client.delete(f"vcenter/vm/{vm_id}")
                results.append(f"✅ {vm_name}: Deleted successfully")
            except Exception as e:
                results.append(f"❌ {vm_name}: Error - {str(e)}")
        
        result = f"Bulk Delete Operation Results:\n\n"
        for res in results:
            result += f"{res}\n"
        
        return result.strip()
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "bulk delete operation")
    finally:
        client.close()


@mcp.tool()
def force_power_off_vm(vm_id: str, confirm: bool = False, hostname: Optional[str] = None) -> str:
    """Force power off a virtual machine (equivalent to pulling the power cord). REQUIRES CONFIRMATION.

    Args:
        vm_id: Virtual machine ID or name
        confirm: Must be True to proceed with force power off
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not confirm:
        return f"⚠️  DESTRUCTIVE OPERATION: Force Power Off VM {vm_id}\n\nThis operation will immediately power off the VM without graceful shutdown!\nThis is equivalent to pulling the power cord and may cause data loss!\n\nTo proceed, call this function again with confirm=True"
    
    if hostname is None:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # If vm_id doesn't start with 'vm-', assume it's a name and look up the ID
        if not vm_id.startswith("vm-"):
            vms_response = client.get("vcenter/vm")
            vms = vms_response.get("value", [])
            
            vm_id_found = None
            for vm in vms:
                if vm.get("name", "").lower() == vm_id.lower():
                    vm_id_found = vm.get("vm")
                    break
            
            if not vm_id_found:
                return f"Virtual machine '{vm_id}' not found by name"
            vm_id = vm_id_found
        
        # Force power off
        client.post(f"vcenter/vm/{vm_id}/power/stop")
        return f"⚠️  VM {vm_id} force powered off (equivalent to pulling power cord)"
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"force powering off VM {vm_id}")
    finally:
        client.close()


# ── pyVmomi-based Advanced Tools ──────────────────────────────────


@mcp.tool()
def list_datacenters_pyvmomi(hostname: Optional[str] = None) -> str:
    """List all datacenters using pyVmomi (direct SOAP connection).

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment)
    """
    client = PyVmomiClient(host=hostname)
    try:
        dcs = client.content.rootFolder.childEntity
        dc_list = [dc for dc in dcs if hasattr(dc, "hostFolder")]
        if not dc_list:
            return "No datacenters found."
        result = f"Found {len(dc_list)} datacenter(s):\n\n"
        for dc in dc_list:
            result += f"  • {dc.name}\n"
        return result.strip()
    except Exception as e:
        return f"Error listing datacenters via pyVmomi: {e}"
    finally:
        client.close()


@mcp.tool()
def list_clusters_pyvmomi(
    datacenter: Optional[str] = None,
    hostname: Optional[str] = None,
    filters: Optional[List[FilterCondition]] = None,
    group_by: Optional[str] = None,
    fields: Optional[List[str]] = None,
    limit: int = 50,
    sort_by: Optional[str] = None,
    descending: bool = True,
) -> str:
    """List, filter, count, or rank clusters using pyVmomi.

    With filters/group_by/fields/sort_by an in-process engine fetches
    properties for ALL clusters in one call and returns only the
    filtered/aggregated result; datacenter is then optional (omit to query
    every datacenter).

    Queryable fields: name, cluster_id, host_count, effective_host_count,
    total_cpu_ghz, total_memory_gb, drs_enabled, ha_enabled, overall_status.
    Filter ops: eq, ne, contains, not_contains, gt, gte, lt, lte, in
    (conditions are ANDed; string matching is case-insensitive).

    Examples:
      Clusters without DRS:
        filters=[{"field": "drs_enabled", "op": "eq", "value": false}]
      Host count per cluster: fields=["name", "host_count"]
      Largest clusters first: sort_by="total_memory_gb"

    Args:
        datacenter: Datacenter name (required for the basic listing;
            optional when using filters/group_by/fields/sort_by)
        hostname: vSphere hostname (optional)
        filters: list of {field, op, value} conditions, ANDed
        group_by: return only a count distribution of this field
        fields: fields shown per cluster (default: name, host_count, drs_enabled, ha_enabled)
        limit: max rows in listing or distribution output (default 50; 0 = unlimited)
        sort_by: order rows by this field before applying limit
        descending: sort direction (default True = biggest first)
    """
    client = PyVmomiClient(host=hostname)
    try:
        container = None
        if datacenter:
            dc = client.find_datacenter(datacenter)
            if dc is None:
                return f"Datacenter '{datacenter}' not found."
            container = dc.hostFolder

        if filters or group_by or fields or sort_by:
            return run_inventory_query(client, "cluster", filters=filters,
                                       group_by=group_by, fields=fields,
                                       limit=limit, container=container,
                                       sort_by=sort_by, descending=descending)

        if not datacenter:
            return "Error: datacenter name is required."

        view = client.get_container_view(vim.ClusterComputeResource, container=container)
        try:
            clusters = list(view.view)
        finally:
            view.Destroy()

        if not clusters:
            return f"No clusters found in datacenter '{datacenter}'."

        shown = clusters if limit == 0 else clusters[:limit]
        result = f"Clusters in '{datacenter}' ({len(clusters)})"
        if len(shown) < len(clusters):
            result += f" (showing {len(shown)} — refine with filters or raise limit)"
        result += ":\n\n"
        for cluster in shown:
            result += f"  • {cluster.name}\n"
        return result.strip()
    except Exception as e:
        return f"Error listing clusters via pyVmomi: {e}"
    finally:
        client.close()


@mcp.tool()
def find_template_pyvmomi(name: str, datacenter: Optional[str] = None, hostname: Optional[str] = None) -> str:
    """Find a VM template by name using pyVmomi PropertyCollector.

    Args:
        name: Template name (full or partial, case-insensitive)
        datacenter: Datacenter name to scope search (optional)
        hostname: vSphere hostname (optional)
    """
    client = PyVmomiClient(host=hostname)
    try:
        dc = client.find_datacenter(datacenter) if datacenter else None
        root = dc.hostFolder if dc else client.content.rootFolder

        view = client.get_container_view(vim.VirtualMachine, container=root)
        try:
            collector = client.content.propertyCollector
            traversal_spec = vmodl.query.PropertyCollector.TraversalSpec(
                name="traverse", type=vim.view.ContainerView, path="view", skip=False
            )
            obj_spec = vmodl.query.PropertyCollector.ObjectSpec(
                obj=view, skip=True, selectSet=[traversal_spec]
            )
            prop_spec = vmodl.query.PropertyCollector.PropertySpec(
                type=vim.VirtualMachine,
                pathSet=["name", "config.template", "runtime.powerState"],
                all=False,
            )
            filter_spec = vmodl.query.PropertyCollector.FilterSpec(
                objectSet=[obj_spec], propSet=[prop_spec]
            )
            results = collector.RetrieveProperties([filter_spec])

            matches = []
            for obj_content in results:
                props = {p.name: p.val for p in obj_content.propSet}
                vm_name = props.get("name", "")
                is_template = props.get("config.template")
                if is_template and name.lower() in vm_name.lower():
                    matches.append((vm_name, is_template))

            if not matches:
                return f"No template matching '{name}' found."

            result = f"Templates matching '{name}':\n\n"
            for vm_name, is_tpl in matches:
                result += f"  • {vm_name}"
                if datacenter:
                    result += f" (DC: {datacenter})"
                result += "\n"
            return result.strip()
        finally:
            view.Destroy()
    except Exception as e:
        return f"Error finding template: {e}"
    finally:
        client.close()


@mcp.tool()
def clone_vm(
    template_name: str,
    vm_name: str,
    cluster_name: str,
    datastore_name: str = "",
    cpu_count: int = 0,
    memory_gb: int = 0,
    target_datacenter: str = "",
    network_name: str = "",
    confirm: bool = False,
    hostname: Optional[str] = None,
) -> str:
    """Clone a VM from a template using pyVmomi.

    Supports cross-datacenter cloning with proper network binding.
    Relies on the latest pyVmomi (9.x) for full API support.

    Args:
        template_name: Source template VM name
        vm_name: Name for the new cloned VM
        cluster_name: Target cluster name
        datastore_name: Target datastore name (optional; if omitted, uses template's datastore)
        cpu_count: CPU count for new VM (0 = use template default)
        memory_gb: Memory in GB for new VM (0 = use template default)
        target_datacenter: Datacenter name containing target cluster
                           (required for cross-DC cloning)
        network_name: Network/portgroup name to attach (optional)
        confirm: Must be True to proceed
        hostname: vSphere hostname (optional)
    """
    if not confirm:
        return (
            f"⚠️  CLONE OPERATION: Clone VM from template\n\n"
            f"  Template: {template_name}\n"
            f"  New VM: {vm_name}\n"
            f"  Target Cluster: {cluster_name}\n"
            f"  Target Datacenter: {target_datacenter or '(auto-detect)'}\n"
            f"  Datastore: {datastore_name or '(template default)'}\n"
            f"  CPU: {cpu_count or '(template default)'}\n"
            f"  Memory: {f'{memory_gb} GB' if memory_gb else '(template default)'}\n"
            f"  Network: {network_name or '(template default)'}\n\n"
            f"To proceed, call again with confirm=True."
        )

    client = PyVmomiClient(host=hostname)
    try:
        si = client.si
        content = client.content

        # ── Step 1: Locate template ──
        template_vm = client.find_vm(template_name)
        if template_vm is None:
            return f"Template '{template_name}' not found."

        # Verify it is a template
        if not template_vm.config.template:
            return f"Error: '{template_name}' is a VM, not a template."

        # Remember which DC the template is in
        source_dc = None
        obj = template_vm.parent
        while obj:
            if hasattr(obj, "hostFolder"):
                source_dc = obj
                break
            obj = obj.parent

        # ── Step 2: Locate target datacenter & cluster ──
        if target_datacenter:
            tg_dc = client.find_datacenter(target_datacenter)
            if tg_dc is None:
                return f"Target datacenter '{target_datacenter}' not found."
        else:
            tg_dc = source_dc

        tg_cluster = client.find_cluster(cluster_name, tg_dc)
        if tg_cluster is None:
            return f"Cluster '{cluster_name}' not found in datacenter '{tg_dc.name}'."

        # ── Step 3: Datastore ──
        if datastore_name:
            tg_datastore = client.find_datastore(datastore_name, tg_dc)
            if tg_datastore is None:
                return f"Datastore '{datastore_name}' not found in datacenter '{tg_dc.name}'."
        else:
            # Use the first datastore of the template (or cluster default)
            if template_vm.datastore:
                tg_datastore = template_vm.datastore[0]
            else:
                return "No datastore found on template and none specified."

        # ── Step 4: Resource pool ──
        resource_pool = tg_cluster.resourcePool

        # ── Step 5: Target folder ──
        # Strategy: find any existing VM in the target DC and use its parent folder
        target_folder = client.get_any_vm_folder(tg_dc)

        # ── Step 6: Build RelocateSpec ──
        relocate_spec = vim.vm.RelocateSpec()
        relocate_spec.pool = resource_pool
        relocate_spec.datastore = tg_datastore

        # Network binding
        if network_name:
            tg_network = client.find_network(network_name, tg_dc)
            if tg_network is None:
                return f"Network '{network_name}' not found in datacenter '{tg_dc.name}'."
            # Map all template NICs to the target network
            device_changes = []
            for device in template_vm.config.hardware.device:
                if isinstance(device, vim.vm.device.VirtualEthernetCard):
                    backing = vim.vm.device.VirtualEthernetCard.NetworkBackingInfo()
                    backing.deviceName = network_name
                    backing.network = tg_network
                    nic_spec = vim.vm.device.VirtualDeviceSpec()
                    nic_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.edit
                    nic_spec.device = device
                    nic_spec.device.backing = backing
                    device_changes.append(nic_spec)
            relocate_spec.deviceChange = device_changes if device_changes else None

        # ── Step 7: Build CloneSpec ──
        clone_spec = vim.vm.CloneSpec()
        clone_spec.location = relocate_spec
        clone_spec.powerOn = False
        clone_spec.template = False

        # Customization (CPU/Memory)
        if cpu_count or memory_gb:
            config_spec = vim.vm.ConfigSpec()
            if cpu_count:
                config_spec.numCPUs = cpu_count
            if memory_gb:
                config_spec.memoryMB = memory_gb * 1024
            clone_spec.config = config_spec

        # ── Step 8: Execute clone ──
        task = template_vm.CloneVM_Task(
            folder=target_folder,
            name=vm_name,
            spec=clone_spec,
        )

        import time
        from pyVmomi import vim as vim_mod

        # Wait for task (poll with timeout)
        timeout = 600
        interval = 3
        elapsed = 0
        task_info = task.info
        while task_info.state in (vim.TaskInfo.State.queued, vim.TaskInfo.State.running):
            if elapsed >= timeout:
                return f"Clone task timed out after {timeout}s. Task: {task_info.name} (state={task_info.state})"
            time.sleep(interval)
            elapsed += interval
            task_info = task.info

        if task_info.state == vim.TaskInfo.State.success:
            return (
                f"✅ VM clone completed successfully!\n\n"
                f"  Template: {template_name}\n"
                f"  New VM: {vm_name}\n"
                f"  Cluster: {cluster_name}\n"
                f"  Datacenter: {tg_dc.name}\n"
                f"  Datastore: {tg_datastore.name}\n"
                f"  CPU: {cpu_count or '(template default)'}\n"
                f"  Memory: {f'{memory_gb} GB' if memory_gb else '(template default)'}\n"
                f"  Network: {network_name or '(template default)'}\n"
            )
        else:
            error = task_info.error
            return (
                f"❌ Clone task failed: state={task_info.state}\n"
                f"  Error: {error.msg if error else 'Unknown error'}\n"
                f"  Task: {task_info.name}"
            )

    except Exception as e:
        return f"Error during clone: {e}"
    finally:
        client.close()


@mcp.tool()
def get_vm_networks_pyvmomi(vm_name: str, hostname: Optional[str] = None) -> str:
    """Get network adapter details of a VM using pyVmomi.

    Args:
        vm_name: VM name
        hostname: vSphere hostname (optional)
    """
    client = PyVmomiClient(host=hostname)
    try:
        vm = client.find_vm(vm_name)
        if vm is None:
            return f"VM '{vm_name}' not found."

        result = f"Network adapters for '{vm_name}':\n\n"
        nics_found = False
        for device in vm.config.hardware.device:
            if isinstance(device, vim.vm.device.VirtualEthernetCard):
                nics_found = True
                backing = device.backing
                network_name = "Unknown"
                if hasattr(backing, "deviceName"):
                    network_name = backing.deviceName
                elif hasattr(backing, "network") and backing.network:
                    network_name = backing.network.name
                result += f"  • {device.deviceInfo.label}\n"
                result += f"    MAC: {device.macAddress}\n"
                result += f"    Network: {network_name}\n"
                result += f"    Connected: {device.connectable.connected}\n\n"

        if not nics_found:
            result += "  No network adapters found."
        return result.strip()
    except Exception as e:
        return f"Error getting network info: {e}"
    finally:
        client.close()


def main() -> None:
    """Main entry point for the MCP server."""
    import os
    from dotenv import load_dotenv
    
    # Load environment variables
    load_dotenv()
    
    # Configure FastMCP settings for streamable HTTP transport
    mcp.settings.host = os.getenv("SERVER_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("SERVER_PORT", "8000"))
    mcp.settings.stateless_http = False
    mcp.settings.json_response = True  # Return JSON instead of SSE for simpler parsing
    
    # Run with streamable HTTP transport
    mcp.run(transport="streamable-http")


# Export the Starlette/FastAPI app for testing and external use
app = mcp.streamable_http_app()


if __name__ == "__main__":
    main()
