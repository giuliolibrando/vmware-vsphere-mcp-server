"""Tests for the pure query logic in vsphere_mcp_server.inventory."""

from vsphere_mcp_server.inventory import FilterCondition, apply_filters


def fc(field, op, value):
    return FilterCondition(field=field, op=op, value=value)


ROWS = [
    {"name": "prod-web-01", "memory_mb": 4096,
     "guest_os": "Ubuntu Linux (64-bit)", "template": False,
     "ip_address": "10.0.0.5"},
    {"name": "prod-db-01", "memory_mb": 16384,
     "guest_os": "Microsoft Windows Server 2019 (64-bit)", "template": False,
     "ip_address": None},
    {"name": "test-web-01", "memory_mb": 2048,
     "guest_os": None, "template": True,
     "ip_address": None},
]


def names(rows):
    return [r["name"] for r in rows]


class TestApplyFilters:
    def test_no_filters_returns_all_rows(self):
        assert apply_filters(ROWS, []) == ROWS

    def test_eq_string_is_case_insensitive(self):
        out = apply_filters(ROWS, [fc("name", "eq", "PROD-WEB-01")])
        assert names(out) == ["prod-web-01"]

    def test_eq_number_matches_string_value(self):
        out = apply_filters(ROWS, [fc("memory_mb", "eq", "16384")])
        assert names(out) == ["prod-db-01"]

    def test_ne_excludes_matches_and_keeps_none(self):
        out = apply_filters(ROWS, [fc("guest_os", "ne", "Ubuntu Linux (64-bit)")])
        assert names(out) == ["prod-db-01", "test-web-01"]

    def test_contains_is_case_insensitive_substring(self):
        out = apply_filters(ROWS, [fc("guest_os", "contains", "windows")])
        assert names(out) == ["prod-db-01"]

    def test_not_contains_keeps_none_values(self):
        out = apply_filters(ROWS, [fc("guest_os", "not_contains", "windows")])
        assert names(out) == ["prod-web-01", "test-web-01"]

    def test_gt_gte_lt_lte(self):
        assert names(apply_filters(ROWS, [fc("memory_mb", "gt", 4096)])) == ["prod-db-01"]
        assert names(apply_filters(ROWS, [fc("memory_mb", "gte", 4096)])) == [
            "prod-web-01", "prod-db-01"]
        assert names(apply_filters(ROWS, [fc("memory_mb", "lt", 4096)])) == ["test-web-01"]
        assert names(apply_filters(ROWS, [fc("memory_mb", "lte", 4096)])) == [
            "prod-web-01", "test-web-01"]

    def test_in_accepts_mixed_value_list(self):
        out = apply_filters(ROWS, [fc("memory_mb", "in", ["2048", 4096])])
        assert names(out) == ["prod-web-01", "test-web-01"]

    def test_conditions_are_anded(self):
        out = apply_filters(ROWS, [
            fc("name", "contains", "prod"),
            fc("memory_mb", "gt", 8000),
        ])
        assert names(out) == ["prod-db-01"]

    def test_none_fails_eq_contains_and_numeric_ops(self):
        assert apply_filters(ROWS, [fc("ip_address", "eq", "10.0.0.5")]) == [ROWS[0]]
        assert apply_filters(ROWS, [fc("guest_os", "contains", "linux")]) == [ROWS[0]]
        assert names(apply_filters(ROWS, [fc("guest_os", "gt", 1)])) == []

    def test_numeric_op_on_non_numeric_value_fails_without_raising(self):
        out = apply_filters(ROWS, [fc("guest_os", "gt", 5)])
        assert out == []

    def test_bool_field_matches_string_and_bool_values(self):
        assert names(apply_filters(ROWS, [fc("template", "eq", True)])) == ["test-web-01"]
        assert names(apply_filters(ROWS, [fc("template", "eq", "true")])) == ["test-web-01"]
        assert names(apply_filters(ROWS, [fc("template", "eq", "false")])) == [
            "prod-web-01", "prod-db-01"]

    def test_missing_key_is_treated_as_none(self):
        out = apply_filters([{"name": "x"}], [fc("guest_os", "ne", "anything")])
        assert names(out) == ["x"]


from vsphere_mcp_server.inventory import (
    OBJECT_TYPES,
    aggregate,
    format_distribution,
    format_rows,
    validate_query,
)


class TestValidateQuery:
    def test_valid_query_returns_none(self):
        assert validate_query(
            "vm", [fc("hardware_version", "eq", "vmx-13")],
            "guest_os", ["name", "cluster"]) is None

    def test_unknown_filter_field_lists_valid_fields(self):
        error = validate_query("vm", [fc("flux_capacitor", "eq", 1)], None, None)
        assert error is not None
        assert "flux_capacitor" in error
        assert "hardware_version" in error  # names the valid fields

    def test_unknown_group_by_and_fields_are_reported(self):
        error = validate_query("host", None, "nope1", ["nope2"])
        assert error is not None
        assert "nope1" in error and "nope2" in error

    def test_every_object_type_has_valid_default_fields(self):
        for object_type, cfg in OBJECT_TYPES.items():
            for field in cfg["default_fields"]:
                assert field in cfg["fields"], (object_type, field)


class TestAggregate:
    def test_counts_sorted_desc_then_value_and_unknown_bucket(self):
        rows = [{"os": "Ubuntu"}, {"os": "Windows"}, {"os": "Ubuntu"},
                {"os": None}, {"os": "Debian"}]
        assert aggregate(rows, "os") == [
            ("Ubuntu", 2), ("(unknown)", 1), ("Debian", 1), ("Windows", 1)]

    def test_empty_rows(self):
        assert aggregate([], "os") == []


class TestFormatDistribution:
    def test_shows_counts_and_percentages(self):
        out = format_distribution(
            [("Ubuntu", 2), ("Windows", 1)], "guest_os", "VMs")
        assert "guest_os" in out and "3 VMs" in out
        assert "Ubuntu: 2 (66.7%)" in out
        assert "Windows: 1 (33.3%)" in out

    def test_empty_distribution(self):
        assert format_distribution([], "guest_os", "VMs") == "No matching VMs"


class TestFormatRows:
    ROWS = [{"name": f"vm-{i:02d}", "power_state": "poweredOn"} for i in range(60)]

    def test_caps_at_limit_with_header(self):
        out = format_rows(self.ROWS, ["name", "power_state"], 100, "VMs", 50)
        assert "Matched 60 of 100 VMs (showing 50" in out
        assert "vm-49" in out and "vm-50" not in out

    def test_limit_zero_is_unlimited(self):
        out = format_rows(self.ROWS, ["name"], 100, "VMs", 0)
        assert "vm-59" in out and "(showing" not in out

    def test_zero_matches(self):
        assert format_rows([], ["name"], 100, "VMs", 50) == "Matched 0 of 100 VMs"

    def test_row_shows_only_requested_fields(self):
        out = format_rows(
            [{"name": "db01", "guest_os": "Ubuntu", "memory_mb": 4096}],
            ["name", "memory_mb"], 1, "VMs", 50)
        assert "• db01 — memory_mb: 4096" in out
        assert "Ubuntu" not in out


from vsphere_mcp_server import inventory


class TestRunInventoryQuery:
    def test_group_by_returns_distribution_only(self, monkeypatch):
        rows = [{"guest_os": "Ubuntu"}, {"guest_os": "Ubuntu"},
                {"guest_os": "Windows"}]
        monkeypatch.setattr(
            inventory, "fetch_inventory",
            lambda client, object_type, field_names, container=None: rows)
        out = inventory.run_inventory_query(None, "vm", group_by="guest_os")
        assert "Ubuntu: 2 (66.7%)" in out
        assert "Windows: 1 (33.3%)" in out

    def test_filters_and_limit_in_listing(self, monkeypatch):
        rows = [{"name": f"vm-{i:03d}", "power_state": "poweredOn",
                 "guest_os": "Ubuntu"} for i in range(80)]
        monkeypatch.setattr(
            inventory, "fetch_inventory",
            lambda client, object_type, field_names, container=None: rows)
        out = inventory.run_inventory_query(
            None, "vm",
            filters=[fc("power_state", "eq", "poweredOn")], limit=50)
        assert "Matched 80 of 80 VMs (showing 50" in out

    def test_validation_error_short_circuits_without_fetching(self, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError("fetch_inventory must not be called")
        monkeypatch.setattr(inventory, "fetch_inventory", boom)
        out = inventory.run_inventory_query(
            None, "vm", filters=[fc("bogus", "eq", 1)])
        assert "Unknown field" in out and "bogus" in out

    def test_group_by_distribution_is_capped_at_limit(self, monkeypatch):
        rows = [{"name": f"vm-{i:03d}"} for i in range(80)]
        monkeypatch.setattr(
            inventory, "fetch_inventory",
            lambda client, object_type, field_names, container=None: rows)
        out = inventory.run_inventory_query(None, "vm", group_by="name",
                                            limit=50)
        assert out.count("vm-0") + out.count("vm-1") <= 50 + 2  # header slack
        assert "vm-079" not in out
        assert "30 more" in out

    def test_group_by_limit_zero_shows_all_values(self, monkeypatch):
        rows = [{"name": f"vm-{i:03d}"} for i in range(80)]
        monkeypatch.setattr(
            inventory, "fetch_inventory",
            lambda client, object_type, field_names, container=None: rows)
        out = inventory.run_inventory_query(None, "vm", group_by="name",
                                            limit=0)
        assert "vm-079" in out
        assert "more" not in out


class TestPrefixNumberComparisons:
    ROWS = [
        {"name": "old", "hardware_version": "vmx-08"},
        {"name": "mid", "hardware_version": "vmx-13"},
        {"name": "new", "hardware_version": "vmx-19"},
        {"name": "unset", "hardware_version": None},
    ]

    def test_lte_on_prefix_number_strings(self):
        out = apply_filters(self.ROWS, [fc("hardware_version", "lte", "vmx-13")])
        assert names(out) == ["old", "mid"]

    def test_gt_on_prefix_number_strings(self):
        out = apply_filters(self.ROWS, [fc("hardware_version", "gt", "vmx-13")])
        assert names(out) == ["new"]

    def test_plain_number_against_prefix_number_field(self):
        out = apply_filters(self.ROWS, [fc("hardware_version", "lt", 13)])
        assert names(out) == ["old"]

    def test_eq_stays_string_based(self):
        out = apply_filters(self.ROWS, [fc("hardware_version", "eq", "vmx-13")])
        assert names(out) == ["mid"]
        assert apply_filters(self.ROWS, [fc("hardware_version", "eq", "13")]) == []

    def test_plain_words_still_fail_numeric_ops(self):
        rows = [{"guest_os": "Ubuntu Linux (64-bit)"}]
        assert apply_filters(rows, [fc("guest_os", "gt", 5)]) == []

    def test_dotted_versions_do_not_parse_as_suffix_numbers(self):
        rows = [{"esxi_version": "7.0.3"}, {"esxi_version": "8.0.1"}]
        out = apply_filters(rows, [fc("esxi_version", "gte", "esx-8")])
        assert out == []


from vsphere_mcp_server.inventory import sort_rows


class TestSortRows:
    ROWS = [
        {"name": "b", "memory_mb": 4096, "hardware_version": "vmx-13"},
        {"name": "a", "memory_mb": 16384, "hardware_version": "vmx-08"},
        {"name": "c", "memory_mb": None, "hardware_version": "vmx-9"},
        {"name": "d", "memory_mb": 2048, "hardware_version": None},
    ]

    def test_numeric_descending_by_default_with_none_last(self):
        assert names(sort_rows(self.ROWS, "memory_mb")) == ["a", "b", "d", "c"]

    def test_ascending_also_keeps_none_last(self):
        out = sort_rows(self.ROWS, "memory_mb", descending=False)
        assert names(out) == ["d", "b", "a", "c"]

    def test_prefix_number_strings_sort_numerically(self):
        out = sort_rows(self.ROWS, "hardware_version", descending=False)
        assert names(out) == ["a", "c", "b", "d"]  # vmx-08 < vmx-9 < vmx-13

    def test_plain_strings_sort_case_insensitively(self):
        rows = [{"name": "Zed"}, {"name": "apple"}, {"name": "Mango"}]
        out = sort_rows(rows, "name", descending=False)
        assert names(out) == ["apple", "Mango", "Zed"]

    def test_mixed_numeric_and_word_values_fall_back_to_string_order(self):
        rows = [{"v": "10"}, {"v": "high"}, {"v": "2"}]
        out = sort_rows(rows, "v", descending=False)
        assert [r["v"] for r in out] == ["10", "2", "high"]

    def test_input_list_is_not_mutated(self):
        rows = list(self.ROWS)
        sort_rows(rows, "memory_mb")
        assert rows == self.ROWS


class TestRunInventoryQuerySort:
    def test_sort_by_orders_listing_before_limit(self, monkeypatch):
        rows = [{"name": f"vm-{i}", "memory_mb": i * 1024} for i in range(1, 6)]
        monkeypatch.setattr(
            inventory, "fetch_inventory",
            lambda client, object_type, field_names, container=None: rows)
        out = inventory.run_inventory_query(
            None, "vm", fields=["name", "memory_mb"],
            sort_by="memory_mb", limit=2)
        assert out.index("vm-5") < out.index("vm-4")
        assert "vm-3" not in out

    def test_unknown_sort_by_reports_error(self, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError("fetch_inventory must not be called")
        monkeypatch.setattr(inventory, "fetch_inventory", boom)
        out = inventory.run_inventory_query(None, "vm", sort_by="warp_factor")
        assert "Unknown field" in out and "warp_factor" in out

    def test_sort_field_need_not_be_displayed(self, monkeypatch):
        rows = [{"name": "small", "memory_mb": 1024},
                {"name": "big", "memory_mb": 8192}]
        monkeypatch.setattr(
            inventory, "fetch_inventory",
            lambda client, object_type, field_names, container=None: rows)
        out = inventory.run_inventory_query(
            None, "vm", fields=["name"], sort_by="memory_mb", limit=1)
        assert "big" in out and "small" not in out


class TestFieldPropagationToFetch:
    """fetch_inventory must be asked for every field the query touches —
    the fakes elsewhere ignore field_names, so regressions in the
    `needed` computation would otherwise go unnoticed."""

    def _capture(self, monkeypatch):
        captured = {}

        def fake(client, object_type, field_names, container=None):
            captured["field_names"] = list(field_names)
            return []

        monkeypatch.setattr(inventory, "fetch_inventory", fake)
        return captured

    def test_listing_fetch_includes_display_filter_and_sort_fields(
            self, monkeypatch):
        captured = self._capture(monkeypatch)
        inventory.run_inventory_query(
            None, "vm", fields=["name"],
            filters=[fc("power_state", "eq", "poweredOn")],
            sort_by="memory_mb")
        assert {"name", "power_state", "memory_mb"} <= set(
            captured["field_names"])

    def test_group_by_fetch_includes_group_and_filter_fields(
            self, monkeypatch):
        captured = self._capture(monkeypatch)
        inventory.run_inventory_query(
            None, "vm", group_by="guest_os",
            filters=[fc("cpu_count", "gte", 8)])
        assert {"guest_os", "cpu_count"} <= set(captured["field_names"])


from datetime import datetime
from types import SimpleNamespace


class TestFlattenSnapshotTree:
    @staticmethod
    def _node(name, moid, children=(), description="", quiesced=False):
        return SimpleNamespace(
            name=name,
            snapshot=SimpleNamespace(_moId=moid),
            createTime=datetime(2026, 1, 2, 3, 4, 5),
            state="poweredOff",
            description=description,
            quiesced=quiesced,
            childSnapshotList=list(children),
        )

    def test_nested_tree_flattens_depth_first_with_depth(self):
        tree = [
            self._node("base", "snapshot-1", children=[
                self._node("child", "snapshot-2", children=[
                    self._node("grandchild", "snapshot-3")])]),
            self._node("sibling", "snapshot-4"),
        ]
        rows = inventory.flatten_snapshot_tree(tree)
        assert [(r["name"], r["depth"]) for r in rows] == [
            ("base", 0), ("child", 1), ("grandchild", 2), ("sibling", 0)]
        assert rows[2]["snapshot_id"] == "snapshot-3"
        assert rows[0]["created"] == "2026-01-02 03:04:05"
        assert rows[0]["snapshot_obj"] is tree[0].snapshot

    def test_empty_or_none_tree_yields_no_rows(self):
        assert inventory.flatten_snapshot_tree(None) == []
        assert inventory.flatten_snapshot_tree([]) == []

    def test_rows_carry_raw_created_datetime(self):
        tree = [self._node("base", "snapshot-1")]
        rows = inventory.flatten_snapshot_tree(tree)
        assert rows[0]["created_dt"] == datetime(2026, 1, 2, 3, 4, 5)
        assert rows[0]["created"] == "2026-01-02 03:04:05"

    def test_created_none_yields_none_created_and_created_dt(self):
        node = self._node("base", "snapshot-1")
        node.createTime = None
        rows = inventory.flatten_snapshot_tree([node])
        assert rows[0]["created"] is None
        assert rows[0]["created_dt"] is None


class TestMatchSnapshot:
    ROWS = [
        {"name": "base", "snapshot_id": "snapshot-1"},
        {"name": "pre-upgrade", "snapshot_id": "snapshot-2"},
        {"name": "PRE-UPGRADE", "snapshot_id": "snapshot-3"},
    ]

    def test_matches_by_id_first(self):
        row, err = inventory.match_snapshot(self.ROWS, "snapshot-2")
        assert err is None and row is not None
        assert row["name"] == "pre-upgrade"

    def test_matches_unique_name_case_insensitively(self):
        row, err = inventory.match_snapshot(self.ROWS, "BASE")
        assert err is None and row is not None
        assert row["snapshot_id"] == "snapshot-1"

    def test_ambiguous_name_returns_error_listing_ids(self):
        row, err = inventory.match_snapshot(self.ROWS, "pre-upgrade")
        assert row is None
        assert err is not None
        assert "snapshot-2" in err and "snapshot-3" in err

    def test_unknown_ident_returns_error_listing_available(self):
        row, err = inventory.match_snapshot(self.ROWS, "nope")
        assert row is None and err is not None and "base" in err


class TestFindObject:
    PAIRS = [
        (SimpleNamespace(_moId="vm-1"), {"name": "Alpha"}),
        (SimpleNamespace(_moId="vm-2"), {"name": "beta"}),
        (SimpleNamespace(_moId="vm-3"), {"name": "BETA"}),
    ]

    def _patch(self, monkeypatch):
        monkeypatch.setattr(
            inventory, "_collect",
            lambda client, vim_type, path_specs, container=None: self.PAIRS)

    def test_resolves_moid_when_ident_has_id_prefix(self, monkeypatch):
        self._patch(monkeypatch)
        obj, err = inventory.find_object(None, "vm", "vm-2", "vm-")
        assert err is None and obj is not None
        assert obj._moId == "vm-2"

    def test_resolves_unique_name_case_insensitively(self, monkeypatch):
        self._patch(monkeypatch)
        obj, err = inventory.find_object(None, "vm", "ALPHA", "vm-")
        assert err is None and obj is not None
        assert obj._moId == "vm-1"

    def test_duplicate_names_error_lists_ids(self, monkeypatch):
        self._patch(monkeypatch)
        obj, err = inventory.find_object(None, "vm", "beta", "vm-")
        assert obj is None and err is not None
        assert "vm-2" in err and "vm-3" in err

    def test_unknown_ident_errors(self, monkeypatch):
        self._patch(monkeypatch)
        obj, err = inventory.find_object(None, "vm", "vm-99", "vm-")
        assert obj is None and err is not None
        assert "not found" in err.lower()
