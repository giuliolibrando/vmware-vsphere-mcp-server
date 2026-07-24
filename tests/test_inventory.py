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
        assert "flux_capacitor" in error
        assert "hardware_version" in error  # names the valid fields

    def test_unknown_group_by_and_fields_are_reported(self):
        error = validate_query("host", None, "nope1", ["nope2"])
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
