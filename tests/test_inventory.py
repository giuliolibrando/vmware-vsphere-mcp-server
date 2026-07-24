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
