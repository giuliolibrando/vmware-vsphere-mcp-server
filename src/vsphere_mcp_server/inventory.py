"""In-process inventory query engine: bulk property fetch, filter, count, format.

The list_* tools in server.py route through run_inventory_query() whenever the
caller supplies filters/group_by/fields. The engine fetches the needed
properties for ALL objects of a type in one PropertyCollector round-trip,
filters and aggregates in process, and returns only the result — so the
calling LLM never has to ingest the full inventory.
"""

from typing import List, Literal, Union

from pydantic import BaseModel
from pyVmomi import vim


class FilterCondition(BaseModel):
    """One filter condition; conditions in a list are ANDed."""

    field: str
    op: Literal["eq", "ne", "contains", "not_contains",
                "gt", "gte", "lt", "lte", "in"]
    value: Union[str, int, float, bool, List[Union[str, int, float]]]


# ── Filter evaluation (pure) ─────────────────────────────────────

def _loose_eq(a, b) -> bool:
    """Equality that tolerates str/number/bool type mismatches from the LLM."""
    if isinstance(a, str) or isinstance(b, str):
        return str(a).lower() == str(b).lower()
    return a == b


def _matches(value, cond: FilterCondition) -> bool:
    op, expected = cond.op, cond.value
    if value is None:
        return op in ("ne", "not_contains")
    if op in ("eq", "ne"):
        equal = _loose_eq(value, expected)
        return equal if op == "eq" else not equal
    if op in ("contains", "not_contains"):
        hit = str(expected).lower() in str(value).lower()
        return hit if op == "contains" else not hit
    if op == "in":
        options = expected if isinstance(expected, list) else [expected]
        return any(_loose_eq(value, option) for option in options)
    try:
        left, right = float(value), float(expected)
    except (TypeError, ValueError):
        return False
    return {"gt": left > right, "gte": left >= right,
            "lt": left < right, "lte": left <= right}[op]


def apply_filters(rows: list, filters: list) -> list:
    """Return the rows matching ALL conditions."""
    return [row for row in rows
            if all(_matches(row.get(c.field), c) for c in filters)]


# ── Field maps ───────────────────────────────────────────────────
# One curated dict per object type: LLM-friendly field name → which
# PropertyCollector paths to fetch and how to turn the raw values into a
# scalar. "spec_type" narrows a path to a subtype (used for DVPortgroup-only
# properties on the network view).

def _direct(path, convert=None):
    def extract(props, ctx):
        value = props.get(path)
        if value is None:
            return None
        return convert(value) if convert else value
    return {"paths": [path], "extract": extract}


def _gb(value):
    return round(value / (1024 ** 3), 1)


def _ref_id(ref):
    return getattr(ref, "_moId", None)


def _snapshot_count(node_list):
    if not node_list:
        return 0
    count = 0
    for node in node_list:
        count += 1 + _snapshot_count(getattr(node, "childSnapshotList", None))
    return count


def _vm_host(props, ctx):
    info = ctx.get("host_info", {}).get(_ref_id(props.get("runtime.host")))
    return info.get("name") if info else None


def _vm_cluster(props, ctx):
    info = ctx.get("host_info", {}).get(_ref_id(props.get("runtime.host")))
    if not info:
        return None
    return ctx.get("cr_names", {}).get(_ref_id(info.get("parent")))


def _host_cluster(props, ctx):
    return ctx.get("cr_names", {}).get(_ref_id(props.get("parent")))


def _ds_free_pct(props, ctx):
    capacity, free = props.get("summary.capacity"), props.get("summary.freeSpace")
    if not capacity or free is None:
        return None
    return round(free / capacity * 100, 1)


def _network_vlan(props, ctx):
    # Plain VLAN ID on a distributed portgroup only; trunk/PVLAN and standard
    # portgroups yield None (best-effort per spec).
    port_config = props.get("config.defaultPortConfig")
    vlan = getattr(port_config, "vlan", None)
    vlan_id = getattr(vlan, "vlanId", None)
    return vlan_id if isinstance(vlan_id, int) else None


VM_FIELDS = {
    "name": _direct("name"),
    "vm_id": {"paths": [], "extract": lambda p, c: p["_moId"]},
    "power_state": _direct("runtime.powerState", str),
    "connection_state": _direct("runtime.connectionState", str),
    "guest_os": _direct("config.guestFullName"),
    "guest_os_running": _direct("guest.guestFullName"),
    "hardware_version": _direct("config.version"),
    "cpu_count": _direct("config.hardware.numCPU"),
    "memory_mb": _direct("config.hardware.memoryMB"),
    "template": _direct("config.template"),
    "tools_status": _direct("guest.toolsStatus", str),
    "tools_version": _direct("guest.toolsVersion"),
    "ip_address": _direct("guest.ipAddress"),
    "guest_hostname": _direct("guest.hostName"),
    "uuid": _direct("config.uuid"),
    "annotation": _direct("config.annotation"),
    "host": {"paths": ["runtime.host"], "extract": _vm_host},
    "cluster": {"paths": ["runtime.host"], "extract": _vm_cluster},
    "storage_committed_gb": _direct("summary.storage.committed", _gb),
    "snapshot_count": {
        "paths": ["snapshot.rootSnapshotList"],
        "extract": lambda p, c: _snapshot_count(p.get("snapshot.rootSnapshotList")),
    },
    "boot_time": _direct("runtime.bootTime", str),
}

HOST_FIELDS = {
    "name": _direct("name"),
    "host_id": {"paths": [], "extract": lambda p, c: p["_moId"]},
    "connection_state": _direct("runtime.connectionState", str),
    "power_state": _direct("runtime.powerState", str),
    "maintenance_mode": _direct("runtime.inMaintenanceMode"),
    "esxi_version": _direct("summary.config.product.version"),
    "build": _direct("summary.config.product.build"),
    "vendor": _direct("summary.hardware.vendor"),
    "model": _direct("summary.hardware.model"),
    "cpu_model": _direct("summary.hardware.cpuModel"),
    "cpu_cores": _direct("summary.hardware.numCpuCores"),
    "cpu_threads": _direct("summary.hardware.numCpuThreads"),
    "memory_gb": _direct("summary.hardware.memorySize", _gb),
    "cluster": {"paths": ["parent"], "extract": _host_cluster},
    "vm_count": {"paths": ["vm"], "extract": lambda p, c: len(p.get("vm") or [])},
    "cpu_usage_mhz": _direct("summary.quickStats.overallCpuUsage"),
    "memory_usage_gb": _direct("summary.quickStats.overallMemoryUsage",
                               lambda v: round(v / 1024, 1)),
    "uptime_days": _direct("summary.quickStats.uptime",
                           lambda v: round(v / 86400, 1)),
}

DATASTORE_FIELDS = {
    "name": _direct("name"),
    "datastore_id": {"paths": [], "extract": lambda p, c: p["_moId"]},
    "type": _direct("summary.type"),
    "capacity_gb": _direct("summary.capacity", _gb),
    "free_gb": _direct("summary.freeSpace", _gb),
    "free_pct": {"paths": ["summary.capacity", "summary.freeSpace"],
                 "extract": _ds_free_pct},
    "accessible": _direct("summary.accessible"),
    "maintenance_mode": _direct("summary.maintenanceMode"),
    "vm_count": {"paths": ["vm"], "extract": lambda p, c: len(p.get("vm") or [])},
    "host_count": {"paths": ["host"], "extract": lambda p, c: len(p.get("host") or [])},
}

NETWORK_FIELDS = {
    "name": _direct("name"),
    "network_id": {"paths": [], "extract": lambda p, c: p["_moId"]},
    "type": {"paths": [], "extract": lambda p, c: p["_type"].split(".")[-1]},
    "vlan_id": {"paths": ["config.defaultPortConfig"],
                "spec_type": vim.dvs.DistributedVirtualPortgroup,
                "extract": _network_vlan},
    "vm_count": {"paths": ["vm"], "extract": lambda p, c: len(p.get("vm") or [])},
}

CLUSTER_FIELDS = {
    "name": _direct("name"),
    "cluster_id": {"paths": [], "extract": lambda p, c: p["_moId"]},
    "host_count": _direct("summary.numHosts"),
    "effective_host_count": _direct("summary.numEffectiveHosts"),
    "total_cpu_ghz": _direct("summary.totalCpu", lambda v: round(v / 1000, 1)),
    "total_memory_gb": _direct("summary.totalMemory", _gb),
    "drs_enabled": _direct("configuration.drsConfig.enabled"),
    "ha_enabled": _direct("configuration.dasConfig.enabled"),
    "overall_status": _direct("overallStatus", str),
}

OBJECT_TYPES = {
    "vm": {"vim_type": vim.VirtualMachine, "fields": VM_FIELDS, "label": "VMs",
           "default_fields": ["name", "power_state", "guest_os"]},
    "host": {"vim_type": vim.HostSystem, "fields": HOST_FIELDS, "label": "hosts",
             "default_fields": ["name", "connection_state", "esxi_version"]},
    "datastore": {"vim_type": vim.Datastore, "fields": DATASTORE_FIELDS,
                  "label": "datastores",
                  "default_fields": ["name", "type", "capacity_gb", "free_gb",
                                     "free_pct"]},
    "network": {"vim_type": vim.Network, "fields": NETWORK_FIELDS,
                "label": "networks",
                "default_fields": ["name", "type", "vlan_id"]},
    "cluster": {"vim_type": vim.ClusterComputeResource, "fields": CLUSTER_FIELDS,
                "label": "clusters",
                "default_fields": ["name", "host_count", "drs_enabled",
                                   "ha_enabled"]},
}


# ── Validation, aggregation, formatting (pure) ───────────────────

def validate_query(object_type, filters, group_by, fields):
    """Return an error string naming the valid fields, or None if the query is OK."""
    valid = OBJECT_TYPES[object_type]["fields"]
    unknown = [c.field for c in (filters or []) if c.field not in valid]
    if group_by and group_by not in valid:
        unknown.append(group_by)
    unknown.extend(f for f in (fields or []) if f not in valid)
    if unknown:
        return (f"Unknown field(s) {sorted(set(unknown))} for {object_type}. "
                f"Valid fields: {', '.join(sorted(valid))}")
    return None


def aggregate(rows, group_by):
    """Count rows per distinct value of group_by; None becomes '(unknown)'."""
    counts = {}
    for row in rows:
        value = row.get(group_by)
        key = "(unknown)" if value is None else str(value)
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def format_distribution(pairs, group_by, type_label):
    total = sum(count for _, count in pairs)
    if total == 0:
        return f"No matching {type_label}"
    lines = [f"Distribution of {group_by} across {total} {type_label}:", ""]
    for value, count in pairs:
        lines.append(f"  {value}: {count} ({count / total * 100:.1f}%)")
    return "\n".join(lines)


def format_rows(rows, field_names, total, type_label, limit):
    matched = len(rows)
    if matched == 0:
        return f"Matched 0 of {total} {type_label}"
    shown = rows if limit == 0 or matched <= limit else rows[:limit]
    header = f"Matched {matched} of {total} {type_label}"
    if len(shown) < matched:
        header += f" (showing {len(shown)} — refine filters or raise limit)"
    lines = [header + ":", ""]
    for row in shown:
        rest = ", ".join(f"{name}: {row.get(name)}" for name in field_names[1:])
        lines.append(f"• {row.get(field_names[0])}" + (f" — {rest}" if rest else ""))
    return "\n".join(lines)
