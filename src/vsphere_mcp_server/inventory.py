"""In-process inventory query engine: bulk property fetch, filter, count, format.

The list_* tools in server.py route through run_inventory_query() whenever the
caller supplies filters/group_by/fields. The engine fetches the needed
properties for ALL objects of a type in one PropertyCollector round-trip,
filters and aggregates in process, and returns only the result — so the
calling LLM never has to ingest the full inventory.
"""

import re
from typing import List, Literal, Union

from pydantic import BaseModel
from pyVmomi import vim, vmodl


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


_PREFIX_NUMBER = re.compile(r"[A-Za-z_-]+(\d+)")


def _numeric(value):
    """float(value), falling back to the trailing number of prefix-number
    strings like 'vmx-13' → 13.0. Returns None when not comparable."""
    try:
        return float(value)
    except (TypeError, ValueError):
        if isinstance(value, str):
            match = _PREFIX_NUMBER.fullmatch(value.strip())
            if match:
                return float(match.group(1))
        return None


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
    left, right = _numeric(value), _numeric(expected)
    if left is None or right is None:
        return False
    return {"gt": left > right, "gte": left >= right,
            "lt": left < right, "lte": left <= right}[op]


def apply_filters(rows: list, filters: list) -> list:
    """Return the rows matching ALL conditions."""
    return [row for row in rows
            if all(_matches(row.get(c.field), c) for c in filters)]


def sort_rows(rows: list, sort_by: str, descending: bool = True) -> list:
    """Order rows by sort_by; rows with a missing value go last either way.

    Sorts numerically when every present value is numeric (including
    prefix-number strings like 'vmx-13'), otherwise case-insensitively
    as strings.
    """
    present = [row for row in rows if row.get(sort_by) is not None]
    absent = [row for row in rows if row.get(sort_by) is None]

    def numeric_key(row) -> float:
        value = _numeric(row[sort_by])
        return value if value is not None else 0.0

    def string_key(row) -> str:
        return str(row[sort_by]).lower()

    if present and all(_numeric(row[sort_by]) is not None for row in present):
        ordered = sorted(present, key=numeric_key, reverse=descending)
    else:
        ordered = sorted(present, key=string_key, reverse=descending)
    return ordered + absent


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


def flatten_snapshot_tree(node_list):
    """Flatten vm.snapshot.rootSnapshotList depth-first into row dicts.

    Each row carries display values plus the live vim.vm.Snapshot managed
    object (snapshot_obj) so callers can act on it (e.g. RemoveSnapshot_Task).
    """
    rows = []

    def walk(nodes, depth):
        for node in nodes or []:
            rows.append({
                "name": node.name,
                "snapshot_id": getattr(node.snapshot, "_moId", None),
                "created": str(node.createTime) if node.createTime else None,
                "state": str(node.state) if node.state else None,
                "description": node.description or "",
                "quiesced": bool(node.quiesced),
                "depth": depth,
                "snapshot_obj": node.snapshot,
            })
            walk(getattr(node, "childSnapshotList", None), depth + 1)

    walk(node_list, 0)
    return rows


def match_snapshot(rows, ident):
    """Pick one snapshot row by moId (exact) or name (case-insensitive).

    Returns (row, None) on a unique match, else (None, error_message).
    """
    by_id = [r for r in rows if r["snapshot_id"] == ident]
    if by_id:
        return by_id[0], None
    by_name = [r for r in rows if (r["name"] or "").lower() == ident.lower()]
    if len(by_name) == 1:
        return by_name[0], None
    if not by_name:
        available = ", ".join(
            f"{r['name']} ({r['snapshot_id']})" for r in rows)
        return None, (f"Snapshot '{ident}' not found. "
                      f"Available snapshots: {available or '(none)'}")
    ids = ", ".join(r["snapshot_id"] for r in by_name)
    return None, (f"Snapshot name '{ident}' is ambiguous "
                  f"({len(by_name)} matches: {ids}). Use the snapshot ID.")


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


def _vm_provisioned_gb(props, ctx):
    committed = props.get("summary.storage.committed")
    if committed is None:
        return None
    return _gb(committed + (props.get("summary.storage.uncommitted") or 0))


def _vm_disk_count(props, ctx):
    devices = props.get("config.hardware.device")
    if devices is None:
        return None
    return sum(1 for device in devices
               if isinstance(device, vim.vm.device.VirtualDisk))


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
    "storage_provisioned_gb": {
        "paths": ["summary.storage.committed", "summary.storage.uncommitted"],
        "extract": _vm_provisioned_gb,
    },
    "disk_count": {"paths": ["config.hardware.device"],
                   "extract": _vm_disk_count},
    "snapshot_count": {
        "paths": ["snapshot.rootSnapshotList"],
        "extract": lambda p, c: _snapshot_count(p.get("snapshot.rootSnapshotList")),
    },
    "boot_time": _direct("runtime.bootTime", str),
    "cpu_usage_mhz": _direct("summary.quickStats.overallCpuUsage"),
    "memory_usage_gb": _direct("summary.quickStats.hostMemoryUsage",
                               lambda v: round(v / 1024, 1)),
    "uptime_days": _direct("summary.quickStats.uptimeSeconds",
                           lambda v: round(v / 86400, 1)),
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

def validate_query(object_type, filters, group_by, fields, sort_by=None):
    """Return an error string naming the valid fields, or None if the query is OK."""
    valid = OBJECT_TYPES[object_type]["fields"]
    unknown = [c.field for c in (filters or []) if c.field not in valid]
    if group_by and group_by not in valid:
        unknown.append(group_by)
    if sort_by and sort_by not in valid:
        unknown.append(sort_by)
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


def format_distribution(pairs, group_by, type_label, limit=0):
    total = sum(count for _, count in pairs)
    if total == 0:
        return f"No matching {type_label}"
    shown = pairs if limit == 0 or len(pairs) <= limit else pairs[:limit]
    lines = [f"Distribution of {group_by} across {total} {type_label}:", ""]
    for value, count in shown:
        lines.append(f"  {value}: {count} ({count / total * 100:.1f}%)")
    if len(shown) < len(pairs):
        rest = pairs[len(shown):]
        lines.append(f"  … and {len(rest)} more values covering "
                     f"{sum(c for _, c in rest)} {type_label} "
                     f"(raise limit for the full distribution)")
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


# ── Bulk fetch (one PropertyCollector round-trip per object type) ─

def _collect(client, vim_type, path_specs, container=None):
    """Fetch properties for every object of vim_type under container.

    path_specs: {spec_vim_type: [property paths]} — one PropertySpec per
    entry, so subtype-only properties (e.g. DVPortgroup config) can ride
    along with the base type's paths.
    Returns a list of (managed_object, {path: value}) tuples. Properties
    vCenter does not return (unset / not applicable) are simply absent.
    """
    view = client.get_container_view(vim_type, container=container)
    try:
        collector = client.content.propertyCollector
        # pyVmomi's VMODL types take keyword args at runtime, but its type
        # stubs declare no constructor parameters — hence the pyright ignores.
        traversal_spec = vmodl.query.PropertyCollector.TraversalSpec(
            name="traverse", type=vim.view.ContainerView, path="view", skip=False)  # pyright: ignore[reportCallIssue]
        obj_spec = vmodl.query.PropertyCollector.ObjectSpec(
            obj=view, skip=True, selectSet=[traversal_spec])  # pyright: ignore[reportCallIssue]
        prop_specs = [
            vmodl.query.PropertyCollector.PropertySpec(
                type=spec_type, pathSet=sorted(paths), all=False)  # pyright: ignore[reportCallIssue]
            for spec_type, paths in path_specs.items()
        ]
        filter_spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[obj_spec], propSet=prop_specs)  # pyright: ignore[reportCallIssue]
        options = vmodl.query.PropertyCollector.RetrieveOptions()

        results = []
        response = collector.RetrievePropertiesEx([filter_spec], options)
        while response:
            for obj_content in response.objects:
                props = {p.name: p.val for p in obj_content.propSet}
                results.append((obj_content.obj, props))
            if not response.token:
                break
            response = collector.ContinueRetrievePropertiesEx(response.token)
        return results
    finally:
        view.Destroy()


def fetch_inventory(client, object_type, field_names, container=None):
    """Fetch field_names for all objects of object_type; returns list of dicts."""
    cfg = OBJECT_TYPES[object_type]
    base_type = cfg["vim_type"]
    field_map = cfg["fields"]

    path_specs = {}
    for field_name in field_names:
        spec = field_map[field_name]
        spec_type = spec.get("spec_type", base_type)
        path_specs.setdefault(spec_type, set()).update(spec["paths"])
    # The base PropertySpec must not be empty; "name" is always safe.
    path_specs.setdefault(base_type, set()).add("name")

    raw = _collect(client, base_type, path_specs, container=container)

    # Resolve managed-object references (host/cluster) to names with bulk
    # lookups keyed by moId — never per-object calls.
    ctx = {}
    if object_type == "vm" and ("host" in field_names or "cluster" in field_names):
        host_paths = {vim.HostSystem: {"name", "parent"}}
        ctx["host_info"] = {
            obj._moId: props
            for obj, props in _collect(client, vim.HostSystem, host_paths)
        }
    if "cluster" in field_names and object_type in ("vm", "host"):
        ctx["cr_names"] = {
            obj._moId: props.get("name")
            for obj, props in _collect(
                client, vim.ComputeResource, {vim.ComputeResource: {"name"}})
        }

    rows = []
    for obj, props in raw:
        props["_moId"] = obj._moId
        props["_type"] = type(obj).__name__
        row = {}
        for field_name in field_names:
            try:
                row[field_name] = field_map[field_name]["extract"](props, ctx)
            except Exception:
                row[field_name] = None
        rows.append(row)
    return rows


def find_object(client, object_type, ident, id_prefix):
    """Resolve a moId (ident starting with id_prefix) or a display name to
    the managed object, via one bulk name fetch — never per-object calls.

    Names match case-insensitively and must be unique. Returns
    (managed_object, None) on success, else (None, error_message).
    """
    cfg = OBJECT_TYPES[object_type]
    pairs = _collect(client, cfg["vim_type"], {cfg["vim_type"]: {"name"}})
    if ident.startswith(id_prefix):
        for obj, _ in pairs:
            if obj._moId == ident:
                return obj, None
        return None, f"'{ident}' not found (no {cfg['label']} with that ID)"
    matches = [(obj, props) for obj, props in pairs
               if (props.get("name") or "").lower() == ident.lower()]
    if len(matches) == 1:
        return matches[0][0], None
    if not matches:
        return None, f"'{ident}' not found among {len(pairs)} {cfg['label']}"
    ids = ", ".join(obj._moId for obj, _ in matches)
    return None, (f"Name '{ident}' is ambiguous ({len(matches)} matches: "
                  f"{ids}). Use the ID instead.")


def run_inventory_query(client, object_type, filters=None, group_by=None,
                        fields=None, limit=50, container=None,
                        sort_by=None, descending=True):
    """Validate → bulk-fetch → filter → sort → aggregate or list. Returns display text."""
    error = validate_query(object_type, filters, group_by, fields, sort_by)
    if error:
        return error
    cfg = OBJECT_TYPES[object_type]
    display_fields = fields or cfg["default_fields"]
    filter_fields = {c.field for c in (filters or [])}
    if group_by and fields is None:
        needed = {group_by} | filter_fields
    else:
        needed = set(display_fields) | filter_fields
        if group_by:
            needed.add(group_by)
        if sort_by:
            needed.add(sort_by)
    rows = fetch_inventory(client, object_type, sorted(needed),
                           container=container)
    total = len(rows)
    matched = apply_filters(rows, filters or [])
    if group_by:
        return format_distribution(aggregate(matched, group_by), group_by,
                                   cfg["label"], limit)
    if sort_by:
        matched = sort_rows(matched, sort_by, descending)
    return format_rows(matched, display_fields, total, cfg["label"], limit)
