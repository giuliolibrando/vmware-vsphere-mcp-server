"""vSphere MCP Server - Main server implementation - Docker/Environment version."""

import os
import re
from typing import Dict, List

from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .vsphere_client import VSphereClient
from .pyvmomi_client import PyVmomiClient
from .soap_client import SoapClient
from pyVmomi import vim, vmodl

# Initialize MCP server
mcp = FastMCP("vSphere MCP Server")


class BearerTokenAuthMiddleware(BaseHTTPMiddleware):
    """Middleware that validates Bearer token on all requests."""

    def __init__(self, app, token: str | None = None):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        if self.token:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer ") or auth_header[7:] != self.token:
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)


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


# VM Management Tools
@mcp.tool()
def list_vms(hostname: str | None = None) -> str:
    """List all virtual machines with basic information.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        response = client.get("vcenter/vm")
        vms = response.get("value", [])

        if not vms:
            return "No virtual machines found"

        result = f"Found {len(vms)} virtual machines:\n\n"
        for vm in vms:
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
def get_vm_details(vm_id: str, hostname: str | None = None) -> str:
    """Get detailed information about a specific virtual machine.

    Args:
        vm_id: Virtual machine ID or name
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
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
def power_on_vm(vm_id: str, hostname: str | None = None) -> str:
    """Power on a virtual machine.

    Args:
        vm_id: Virtual machine ID
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
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
def power_off_vm(vm_id: str, hostname: str | None = None) -> str:
    """Power off a virtual machine.

    Args:
        vm_id: Virtual machine ID
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
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
def list_hosts(hostname: str | None = None) -> str:
    """List all ESXi hosts.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        response = client.get("vcenter/host")
        hosts = response.get("value", [])

        if not hosts:
            return "No ESXi hosts found"

        result = f"Found {len(hosts)} ESXi hosts:\n\n"
        for host in hosts:
            result += f"• {host.get('name', 'Unknown')} (ID: {host.get('host')})\n"
            result += f"  Connection State: {host.get('connection_state', 'Unknown')}\n"
            result += f"  Power State: {host.get('power_state', 'Unknown')}\n\n"

        return result.strip()

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "listing hosts")
    finally:
        client.close()


@mcp.tool()
def get_host_details(hostname: str | None = None, host_id: str = None) -> str:
    """Get detailed information about an ESXi host.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        host_id: ESXi host ID
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return 'Error: No hostname provided and VCENTER_HOST not set in environment'
    client = VSphereClient(hostname)
    try:
        response = client.get(f"vcenter/host/{host_id}")
        host = response.get("value", {})

        if not host:
            return f"ESXi host {host_id} not found"

        result = f"Host Details: {host.get('name', 'Unknown')}\n"
        result += f"ID: {host_id}\n"
        result += f"Connection State: {host.get('connection_state', 'Unknown')}\n"
        result += f"Power State: {host.get('power_state', 'Unknown')}\n"

        return result

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"getting host {host_id} details")
    finally:
        client.close()


@mcp.tool()
def list_datacenters(hostname: str | None = None) -> str:
    """List all datacenters.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
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
def get_datacenter_details(hostname: str | None = None, datacenter_id: str = None) -> str:
    """Get detailed information about a datacenter.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        datacenter_id: Datacenter ID
    """
    if not hostname:
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
def list_datastores(hostname: str | None = None) -> str:
    """List all datastores with capacity information.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        response = client.get("vcenter/datastore")
        datastores = response.get("value", [])

        if not datastores:
            return "No datastores found"

        result = f"Found {len(datastores)} datastores:\n\n"
        for ds in datastores:
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
def get_datastore_details(hostname: str | None = None, datastore_id: str = None) -> str:
    """Get detailed information about a datastore.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        datastore_id: Datastore ID
    """
    if not hostname:
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
def list_folders(hostname: str | None = None, folder_type: str = "VIRTUAL_MACHINE") -> str:
    """List folders by type.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        folder_type: Folder type (VIRTUAL_MACHINE, HOST, DATACENTER, DATASTORE, NETWORK)
    """
    if not hostname:
        hostname = os.environ.get("VCENTER_HOST")
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        response = client.get(f"vcenter/folder?filter.type={folder_type}")
        folders = response.get("value", [])

        if not folders:
            return f"No {folder_type} folders found"

        result = f"Found {len(folders)} {folder_type} folders:\n\n"
        for folder in folders:
            result += (
                f"• {folder.get('name', 'Unknown')} (ID: {folder.get('folder')})\n"
            )

        return result.strip()

    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, f"listing {folder_type} folders")
    finally:
        client.close()


@mcp.tool()
def get_folder_details(hostname: str | None = None, folder_id: str = None) -> str:
    """Get detailed information about a folder.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        folder_id: Folder ID
    """
    if not hostname:
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
def list_networks(hostname: str | None = None) -> str:
    """List all networks with VLAN information.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    client = VSphereClient(hostname)
    try:
        response = client.get("vcenter/network")
        networks = response.get("value", [])

        if not networks:
            return "No networks found"

        result = f"Found {len(networks)} networks:\n\n"
        for network in networks:
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
def get_network_details(hostname: str | None = None, network_id: str = None) -> str:
    """Get detailed information about a network.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        network_id: Network ID
    """
    if not hostname:
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
def get_vlan_info(hostname: str | None = None, vlan_query: str = "") -> str:
    """Get information about a VLAN by name or VLAN ID.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
        vlan_query: VLAN name (e.g., v1306-MEL03-Secure-Management) or VLAN ID (e.g., 1306)
    """
    if not hostname:
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
def list_vlans(hostname: str | None = None) -> str:
    """Extract and list VLAN information from network names.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
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
def get_vm_disk_usage(hostname: str | None = None) -> str:
    """Get disk usage information for all VMs.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # Get all VMs
        response = client.get("vcenter/vm")
        vms = response.get("value", [])
        
        if not vms:
            return "No virtual machines found"
        
        result = f"Disk Usage Report for {len(vms)} VMs:\n\n"
        
        for vm in vms:
            vm_id = vm.get('vm')
            vm_name = vm.get('name', 'Unknown')
            
            try:
                # Get detailed VM information including disks
                vm_details = client.get(f"vcenter/vm/{vm_id}")
                vm_data = vm_details.get("value", {})
                
                # Get disk information
                disks = vm_data.get("disks", [])
                
                result += f"• {vm_name} (ID: {vm_id})\n"
                
                if disks:
                    for i, disk in enumerate(disks):
                        capacity = disk.get("capacity", 0)
                        if capacity > 0:
                            # Convert bytes to GB
                            capacity_gb = capacity / (1024**3)
                            result += f"  Disk {i}: {capacity_gb:.1f} GB\n"
                        else:
                            result += f"  Disk {i}: Capacity not available\n"
                else:
                    result += "  No disk information available\n"
                
                result += "\n"
                
            except Exception as e:
                result += f"• {vm_name} (ID: {vm_id}) - Error getting disk info: {str(e)}\n\n"
        
        return result.strip()
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "getting VM disk usage")
    finally:
        client.close()


@mcp.tool()
def get_vm_storage_info(hostname: str | None = None) -> str:
    """Get detailed storage information for all VMs including datastore usage.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # Get all VMs
        response = client.get("vcenter/vm")
        vms = response.get("value", [])
        
        if not vms:
            return "No virtual machines found"
        
        result = f"Storage Information for {len(vms)} VMs:\n\n"
        
        for vm in vms:
            vm_id = vm.get('vm')
            vm_name = vm.get('name', 'Unknown')
            
            try:
                # Get detailed VM information
                vm_details = client.get(f"vcenter/vm/{vm_id}")
                vm_data = vm_details.get("value", {})
                
                result += f"• {vm_name} (ID: {vm_id})\n"
                
                # Get disk information
                disks = vm_data.get("disks", [])
                if disks:
                    for i, disk in enumerate(disks):
                        capacity = disk.get("capacity", 0)
                        if capacity > 0:
                            capacity_gb = capacity / (1024**3)
                            result += f"  Disk {i}: {capacity_gb:.1f} GB allocated\n"
                        else:
                            result += f"  Disk {i}: Size not available\n"
                else:
                    result += "  No disk information available\n"
                
                # Get datastore information
                datastores = vm_data.get("datastores", [])
                if datastores:
                    result += "  Datastores:\n"
                    for ds in datastores:
                        result += f"    - {ds}\n"
                
                result += "\n"
                
            except Exception as e:
                result += f"• {vm_name} (ID: {vm_id}) - Error: {str(e)}\n\n"
        
        result += "\nNote: For actual disk usage percentage, VMware Tools must be installed in VMs and vRealize Operations or similar tools are needed.\n"
        result += "This report shows allocated disk space, not actual usage."
        
        return result.strip()
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "getting VM storage information")
    finally:
        client.close()


@mcp.tool()
def get_datastore_usage(hostname: str | None = None) -> str:
    """Get datastore usage information to identify potential storage issues.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
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
def get_vm_performance_info(hostname: str | None = None) -> str:
    """Get performance information for all VMs including CPU, RAM, and network.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # Get all VMs
        response = client.get("vcenter/vm")
        vms = response.get("value", [])
        
        if not vms:
            return "No virtual machines found"
        
        result = f"Performance Information for {len(vms)} VMs:\n\n"
        
        for vm in vms:
            vm_id = vm.get('vm')
            vm_name = vm.get('name', 'Unknown')
            power_state = vm.get('power_state', 'Unknown')
            
            try:
                # Get detailed VM information
                vm_details = client.get(f"vcenter/vm/{vm_id}")
                vm_data = vm_details.get("value", {})
                
                result += f"• {vm_name} (ID: {vm_id})\n"
                result += f"  Power State: {power_state}\n"
                
                if power_state == "POWERED_ON":
                    # CPU Information
                    cpu_info = vm_data.get("cpu", {})
                    if cpu_info:
                        cpu_count = cpu_info.get("count", "Unknown")
                        result += f"  CPU: {cpu_count} vCPUs\n"
                    
                    # Memory Information
                    memory_info = vm_data.get("memory", {})
                    if memory_info:
                        memory_mb = memory_info.get("size_MiB", "Unknown")
                        if memory_mb != "Unknown":
                            memory_gb = memory_mb / 1024
                            result += f"  Memory: {memory_gb:.1f} GB ({memory_mb} MB)\n"
                        else:
                            result += f"  Memory: {memory_mb}\n"
                    
                    # Network Information
                    nics = vm_data.get("nics", [])
                    if nics:
                        result += f"  Network Interfaces: {len(nics)}\n"
                        for i, nic in enumerate(nics):
                            if isinstance(nic, dict):
                                backing = nic.get("backing", {})
                                if isinstance(backing, dict):
                                    network_name = backing.get("network_name", "Unknown")
                                    result += f"    NIC {i}: {network_name}\n"
                    
                    # Guest OS Information
                    guest_os = vm_data.get("guest_OS", "Unknown")
                    result += f"  Guest OS: {guest_os}\n"
                    
                else:
                    result += f"  VM is {power_state.lower()} - performance data not available\n"
                
                result += "\n"
                
            except Exception as e:
                result += f"• {vm_name} (ID: {vm_id}) - Error: {str(e)}\n\n"
        
        result += "\nNote: This shows allocated resources, not actual usage. For real-time performance metrics, vRealize Operations or similar tools are needed."
        
        return result.strip()
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "getting VM performance information")
    finally:
        client.close()


@mcp.tool()
def get_host_performance_info(hostname: str | None = None) -> str:
    """Get performance information for all ESXi hosts.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # Get all hosts
        response = client.get("vcenter/host")
        hosts = response.get("value", [])
        
        if not hosts:
            return "No ESXi hosts found"
        
        result = f"Host Performance Information for {len(hosts)} hosts:\n\n"
        
        for host in hosts:
            host_id = host.get('host')
            host_name = host.get('name', 'Unknown')
            connection_state = host.get('connection_state', 'Unknown')
            power_state = host.get('power_state', 'Unknown')
            
            result += f"• {host_name} (ID: {host_id})\n"
            result += f"  Connection State: {connection_state}\n"
            result += f"  Power State: {power_state}\n"
            
            if connection_state == "CONNECTED":
                try:
                    # Get detailed host information
                    host_details = client.get(f"vcenter/host/{host_id}")
                    host_data = host_details.get("value", {})
                    
                    # CPU Information
                    cpu_info = host_data.get("cpu", {})
                    if cpu_info:
                        cpu_count = cpu_info.get("count", "Unknown")
                        result += f"  CPU: {cpu_count} physical CPUs\n"
                    
                    # Memory Information
                    memory_info = host_data.get("memory", {})
                    if memory_info:
                        memory_mb = memory_info.get("size_MiB", "Unknown")
                        if memory_mb != "Unknown":
                            memory_gb = memory_mb / 1024
                            result += f"  Memory: {memory_gb:.1f} GB ({memory_mb} MB)\n"
                        else:
                            result += f"  Memory: {memory_mb}\n"
                    
                    # Network Information
                    nics = host_data.get("nics", [])
                    if nics:
                        result += f"  Network Interfaces: {len(nics)}\n"
                        for i, nic in enumerate(nics):
                            if isinstance(nic, dict):
                                nic_name = nic.get("device", "Unknown")
                                result += f"    NIC {i}: {nic_name}\n"
                    
                except Exception as e:
                    result += f"  Error getting detailed info: {str(e)}\n"
            else:
                result += f"  Host is {connection_state.lower()} - detailed info not available\n"
            
            result += "\n"
        
        result += "\nNote: This shows host hardware configuration, not real-time performance metrics."
        
        return result.strip()
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "getting host performance information")
    finally:
        client.close()


@mcp.tool()
def get_vms_with_high_resource_usage(hostname: str | None = None) -> str:
    """Get VMs that might have high resource usage based on configuration.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # Get all VMs
        response = client.get("vcenter/vm")
        vms = response.get("value", [])
        
        if not vms:
            return "No virtual machines found"
        
        result = f"VMs with High Resource Configuration:\n\n"
        high_cpu_vms = []
        high_memory_vms = []
        
        for vm in vms:
            vm_id = vm.get('vm')
            vm_name = vm.get('name', 'Unknown')
            power_state = vm.get('power_state', 'Unknown')
            
            if power_state == "POWERED_ON":
                try:
                    # Get detailed VM information
                    vm_details = client.get(f"vcenter/vm/{vm_id}")
                    vm_data = vm_details.get("value", {})
                    
                    # Check CPU
                    cpu_info = vm_data.get("cpu", {})
                    if cpu_info:
                        cpu_count = cpu_info.get("count", 0)
                        if cpu_count >= 8:  # VMs with 8+ vCPUs
                            high_cpu_vms.append(f"{vm_name} ({cpu_count} vCPUs)")
                    
                    # Check Memory
                    memory_info = vm_data.get("memory", {})
                    if memory_info:
                        memory_mb = memory_info.get("size_MiB", 0)
                        if memory_mb >= 16384:  # VMs with 16GB+ RAM
                            memory_gb = memory_mb / 1024
                            high_memory_vms.append(f"{vm_name} ({memory_gb:.1f} GB)")
                    
                except Exception as e:
                    continue
        
        if high_cpu_vms:
            result += "🔴 VMs with High CPU Configuration (8+ vCPUs):\n"
            for vm in high_cpu_vms:
                result += f"  - {vm}\n"
            result += "\n"
        
        if high_memory_vms:
            result += "🔴 VMs with High Memory Configuration (16GB+ RAM):\n"
            for vm in high_memory_vms:
                result += f"  - {vm}\n"
            result += "\n"
        
        if not high_cpu_vms and not high_memory_vms:
            result += "✅ No VMs found with high resource configuration.\n"
        
        result += "\nNote: This shows resource allocation, not actual usage. High allocation doesn't necessarily mean high usage."
        
        return result.strip()
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "getting VMs with high resource usage")
    finally:
        client.close()


# Snapshot Management Tools (using SOAP API - vSphere 8 removed REST snapshot endpoints)
@mcp.tool()
def list_vm_snapshots(vm_id: str, hostname: str | None = None) -> str:
    """List all snapshots for a specific VM.

    Args:
        vm_id: Virtual machine ID or name
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    soap = SoapClient(hostname)
    try:
        vm, result = soap.list_snapshots(vm_id)
        if vm is None:
            return result  # error message

        vm_name = vm.name
        if not result:
            return f"No snapshots found for VM {vm_name}"

        output = f"Snapshots for VM {vm_name}:\n\n"
        for snap in result:
            output += f"  * {snap['name']} (ID: {snap['id']})\n"
            output += f"    Created: {snap['create_time']}\n"
            output += f"    State: {snap['state']}\n"
            if snap['description']:
                output += f"    Description: {snap['description']}\n"
            if snap['is_current']:
                output += f"    Current: Yes\n"
            output += "\n"

        return output.strip()

    except Exception as e:
        return _handle_error(e, f"listing snapshots for VM {vm_id}")
    finally:
        soap.disconnect()

@mcp.tool()
def create_vm_snapshot(vm_id: str, snapshot_name: str, description: str = "", hostname: str | None = None) -> str:
    """Create a snapshot for a specific VM.

    Args:
        vm_id: Virtual machine ID or name
        snapshot_name: Name for the snapshot
        description: Description for the snapshot (optional)
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    soap = SoapClient(hostname)
    try:
        return soap.create_snapshot(vm_id, snapshot_name, description)
    except Exception as e:
        return _handle_error(e, f"creating snapshot for VM {vm_id}")
    finally:
        soap.disconnect()


# Template Management Tools
@mcp.tool()
def list_templates(hostname: str | None = None) -> str:
    """List all VM templates.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
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
def get_vm_events(vm_id: str, hostname: str | None = None) -> str:
    """Get recent events for a specific VM.

    Args:
        vm_id: Virtual machine ID or name
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
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
def get_alarms(hostname: str | None = None) -> str:
    """Get active alarms in the vSphere environment.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
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
def get_port_groups(hostname: str | None = None) -> str:
    """Get all port groups in the vSphere environment.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
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
        
        result = f"Network Port Groups ({len(networks)}):\n\n"
        for network in networks:
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
def generate_vm_report(hostname: str | None = None) -> str:
    """Generate a comprehensive report of all VMs.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # Get all VMs
        response = client.get("vcenter/vm")
        vms = response.get("value", [])
        
        if not vms:
            return "No virtual machines found"
        
        # Get datastores for storage info
        ds_response = client.get("vcenter/datastore")
        datastores = ds_response.get("value", [])
        
        # Get hosts for host info
        host_response = client.get("vcenter/host")
        hosts = host_response.get("value", [])
        
        result = f"=== vSphere VM Report ===\n"
        result += f"Generated: {os.popen('date').read().strip()}\n"
        result += f"Total VMs: {len(vms)}\n"
        result += f"Total Hosts: {len(hosts)}\n"
        result += f"Total Datastores: {len(datastores)}\n\n"
        
        # VM Summary
        powered_on = sum(1 for vm in vms if vm.get('power_state') == 'POWERED_ON')
        powered_off = len(vms) - powered_on
        
        result += f"=== VM Summary ===\n"
        result += f"Powered On: {powered_on}\n"
        result += f"Powered Off: {powered_off}\n\n"
        
        # Detailed VM List
        result += f"=== Detailed VM List ===\n"
        for vm in vms:
            vm_name = vm.get('name', 'Unknown')
            power_state = vm.get('power_state', 'Unknown')
            result += f"• {vm_name} - {power_state}\n"
        
        result += f"\n=== Datastore Summary ===\n"
        for ds in datastores:
            ds_name = ds.get('name', 'Unknown')
            capacity = ds.get("capacity", 0)
            free_space = ds.get("free_space", 0)
            if capacity > 0:
                used_pct = ((capacity - free_space) / capacity * 100)
                result += f"• {ds_name}: {used_pct:.1f}% used\n"
        
        return result.strip()
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "generating VM report")
    finally:
        client.close()


@mcp.tool()
def get_resource_utilization_summary(hostname: str | None = None) -> str:
    """Get a summary of resource utilization across the vSphere environment.

    Args:
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"
    
    client = VSphereClient(hostname)
    try:
        # Get all VMs
        vm_response = client.get("vcenter/vm")
        vms = vm_response.get("value", [])
        
        # Get all hosts
        host_response = client.get("vcenter/host")
        hosts = host_response.get("value", [])
        
        # Get all datastores
        ds_response = client.get("vcenter/datastore")
        datastores = ds_response.get("value", [])
        
        result = f"=== Resource Utilization Summary ===\n\n"
        
        # CPU Summary
        total_vcpus = 0
        total_physical_cpus = 0
        
        for vm in vms:
            if vm.get('power_state') == 'POWERED_ON':
                try:
                    vm_details = client.get(f"vcenter/vm/{vm.get('vm')}")
                    vm_data = vm_details.get("value", {})
                    cpu_info = vm_data.get("cpu", {})
                    if cpu_info:
                        total_vcpus += cpu_info.get("count", 0)
                except:
                    continue
        
        for host in hosts:
            if host.get('connection_state') == 'CONNECTED':
                try:
                    host_details = client.get(f"vcenter/host/{host.get('host')}")
                    host_data = host_details.get("value", {})
                    cpu_info = host_data.get("cpu", {})
                    if cpu_info:
                        total_physical_cpus += cpu_info.get("count", 0)
                except:
                    continue
        
        result += f"CPU Utilization:\n"
        result += f"  Total vCPUs allocated: {total_vcpus}\n"
        result += f"  Total physical CPUs: {total_physical_cpus}\n"
        if total_physical_cpus > 0:
            cpu_ratio = total_vcpus / total_physical_cpus
            result += f"  vCPU to Physical CPU ratio: {cpu_ratio:.2f}:1\n"
        result += "\n"
        
        # Memory Summary
        total_vm_memory = 0
        total_host_memory = 0
        
        for vm in vms:
            if vm.get('power_state') == 'POWERED_ON':
                try:
                    vm_details = client.get(f"vcenter/vm/{vm.get('vm')}")
                    vm_data = vm_details.get("value", {})
                    memory_info = vm_data.get("memory", {})
                    if memory_info:
                        total_vm_memory += memory_info.get("size_MiB", 0)
                except:
                    continue
        
        for host in hosts:
            if host.get('connection_state') == 'CONNECTED':
                try:
                    host_details = client.get(f"vcenter/host/{host.get('host')}")
                    host_data = host_details.get("value", {})
                    memory_info = host_data.get("memory", {})
                    if memory_info:
                        total_host_memory += memory_info.get("size_MiB", 0)
                except:
                    continue
        
        result += f"Memory Utilization:\n"
        result += f"  Total VM memory allocated: {total_vm_memory / 1024:.1f} GB\n"
        result += f"  Total host memory: {total_host_memory / 1024:.1f} GB\n"
        if total_host_memory > 0:
            memory_ratio = (total_vm_memory / total_host_memory) * 100
            result += f"  Memory overcommitment: {memory_ratio:.1f}%\n"
        result += "\n"
        
        # Storage Summary
        total_capacity = 0
        total_free = 0
        
        for ds in datastores:
            total_capacity += ds.get("capacity", 0)
            total_free += ds.get("free_space", 0)
        
        total_used = total_capacity - total_free
        used_percentage = (total_used / total_capacity * 100) if total_capacity > 0 else 0
        
        result += f"Storage Utilization:\n"
        result += f"  Total capacity: {total_capacity / (1024**3):.1f} GB\n"
        result += f"  Total used: {total_used / (1024**3):.1f} GB ({used_percentage:.1f}%)\n"
        result += f"  Total free: {total_free / (1024**3):.1f} GB\n"
        
        return result.strip()
        
    except (ConnectionError, ValueError, KeyError) as e:
        return _handle_error(e, "getting resource utilization summary")
    finally:
        client.close()


# Automation Tools
@mcp.tool()
def bulk_power_operations(operation: str, vm_list: str, hostname: str | None = None) -> str:
    """Perform bulk power operations on multiple VMs.

    Args:
        operation: Power operation ('on', 'off', 'restart')
        vm_list: Comma-separated list of VM names or IDs
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not hostname:
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
def delete_vm_snapshot(vm_id: str, snapshot_name: str, confirm: bool = False, hostname: str | None = None) -> str:
    """Delete a snapshot for a specific VM by snapshot name. REQUIRES CONFIRMATION.

    Args:
        vm_id: Virtual machine ID or name
        snapshot_name: Name of the snapshot to delete
        confirm: Must be True to proceed with deletion
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not confirm:
        return f"DESTRUCTIVE OPERATION: Delete snapshot '{snapshot_name}' for VM {vm_id}. This cannot be undone. Call again with confirm=True to proceed."

    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    soap = SoapClient(hostname)
    try:
        return soap.delete_snapshot_by_name(vm_id, snapshot_name)
    except Exception as e:
        return _handle_error(e, f"deleting snapshot '{snapshot_name}' for VM {vm_id}")
    finally:
        soap.disconnect()


@mcp.tool()
def revert_vm_snapshot(vm_id: str, snapshot_name: str, confirm: bool = False, hostname: str | None = None) -> str:
    """Revert a VM to a named snapshot. REQUIRES CONFIRMATION.

    Args:
        vm_id: Virtual machine ID or name
        snapshot_name: Name of the snapshot to revert to
        confirm: Must be True to proceed (reverting discards current disk state)
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not confirm:
        return f"⚠️  DESTRUCTIVE OPERATION: Revert VM {vm_id} to snapshot '{snapshot_name}'\n\nCurrent disk state will be discarded. This cannot be undone!\n\nTo proceed, call this function again with confirm=True"

    if not hostname:
        hostname = os.environ.get('VCENTER_HOST')
        if not hostname:
            return "Error: No hostname provided and VCENTER_HOST not set in environment"

    soap = SoapClient(hostname)
    try:
        return soap.revert_to_snapshot(vm_id, snapshot_name)
    except Exception as e:
        return _handle_error(e, f"reverting VM {vm_id} to snapshot '{snapshot_name}'")
    finally:
        soap.disconnect()


@mcp.tool()
def delete_vm(vm_id: str, confirm: bool = False, hostname: str | None = None) -> str:
    """Delete a virtual machine. REQUIRES CONFIRMATION.

    Args:
        vm_id: Virtual machine ID or name
        confirm: Must be True to proceed with deletion
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not confirm:
        return f"⚠️  DESTRUCTIVE OPERATION: Delete VM {vm_id}\n\nThis operation will permanently delete the virtual machine and all its data!\nThis operation cannot be undone!\n\nTo proceed, call this function again with confirm=True"
    
    if not hostname:
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
def modify_vm_resources(vm_id: str, cpu_count: int = None, memory_gb: int = None, confirm: bool = False, hostname: str | None = None) -> str:
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
    
    if not hostname:
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
def bulk_delete_vms(vm_list: str, confirm: bool = False, hostname: str | None = None) -> str:
    """Delete multiple VMs. REQUIRES CONFIRMATION.

    Args:
        vm_list: Comma-separated list of VM names or IDs
        confirm: Must be True to proceed with deletion
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not confirm:
        vm_names = [name.strip() for name in vm_list.split(',')]
        return f"⚠️  DESTRUCTIVE OPERATION: Delete Multiple VMs\n\nVMs to be deleted:\n" + "\n".join(f"  - {name}" for name in vm_names) + "\n\nThis operation will permanently delete all specified VMs and all their data!\nThis operation cannot be undone!\n\nTo proceed, call this function again with confirm=True"
    
    if not hostname:
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
def force_power_off_vm(vm_id: str, confirm: bool = False, hostname: str | None = None) -> str:
    """Force power off a virtual machine (equivalent to pulling the power cord). REQUIRES CONFIRMATION.

    Args:
        vm_id: Virtual machine ID or name
        confirm: Must be True to proceed with force power off
        hostname: vSphere hostname (optional, uses VCENTER_HOST from environment if not provided)
    """
    if not confirm:
        return f"⚠️  DESTRUCTIVE OPERATION: Force Power Off VM {vm_id}\n\nThis operation will immediately power off the VM without graceful shutdown!\nThis is equivalent to pulling the power cord and may cause data loss!\n\nTo proceed, call this function again with confirm=True"
    
    if not hostname:
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
def list_datacenters_pyvmomi(hostname: str | None = None) -> str:
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
def list_clusters_pyvmomi(datacenter: str = None, hostname: str | None = None) -> str:
    """List all clusters in a datacenter using pyVmomi.

    Args:
        datacenter: Datacenter name (required). Use list_datacenters_pyvmomi first.
        hostname: vSphere hostname (optional)
    """
    if not datacenter:
        return "Error: datacenter name is required."

    client = PyVmomiClient(host=hostname)
    try:
        dc = client.find_datacenter(datacenter)
        if dc is None:
            return f"Datacenter '{datacenter}' not found."

        view = client.get_container_view(vim.ClusterComputeResource, container=dc.hostFolder)
        try:
            clusters = list(view.view)
        finally:
            view.Destroy()

        if not clusters:
            return f"No clusters found in datacenter '{datacenter}'."

        result = f"Clusters in '{datacenter}' ({len(clusters)}):\n\n"
        for cluster in clusters:
            result += f"  • {cluster.name}\n"
        return result.strip()
    except Exception as e:
        return f"Error listing clusters via pyVmomi: {e}"
    finally:
        client.close()


@mcp.tool()
def find_template_pyvmomi(name: str, datacenter: str = None, hostname: str | None = None) -> str:
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
    hostname: str | None = None,
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
def get_vm_networks_pyvmomi(vm_name: str, hostname: str | None = None) -> str:
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
    import uvicorn
    from dotenv import load_dotenv

    # Load environment variables
    load_dotenv()

    # Configure FastMCP settings for streamable HTTP transport
    mcp.settings.host = os.getenv("SERVER_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("SERVER_PORT", "8000"))
    mcp.settings.stateless_http = True  # Enable stateless mode

    # Build the app first, then optionally add auth middleware
    starlette_app = mcp.streamable_http_app()
    auth_token = os.getenv("MCP_AUTH_TOKEN")
    if auth_token:
        starlette_app.add_middleware(BearerTokenAuthMiddleware, token=auth_token)

    # Run with uvicorn directly (not mcp.run, which would create a new app)
    config = uvicorn.Config(
        starlette_app,
        host=mcp.settings.host,
        port=mcp.settings.port,
    )
    server = uvicorn.Server(config)
    server.run()


# Export the Starlette/FastAPI app for testing and external use
app = mcp.streamable_http_app()


if __name__ == "__main__":
    main()
