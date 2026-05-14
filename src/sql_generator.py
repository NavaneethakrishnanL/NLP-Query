from __future__ import annotations

import re
from dataclasses import dataclass, field

from schema_model import Relationship, SemanticCatalog, Table


@dataclass
class FilterSpec:
    table: str
    column: str
    operator: str
    value: str


@dataclass
class QuerySpec:
    base_table: str
    joined_tables: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    filters: list[FilterSpec] = field(default_factory=list)
    order_by: str = ""
    limit: int = 100


class SqlGenerationError(ValueError):
    pass


def generate_sql(catalog: SemanticCatalog, spec: QuerySpec) -> str:
    base_table = catalog.get_table(spec.base_table)
    if not base_table:
        raise SqlGenerationError("Choose a base table before generating SQL.")

    select_parts = []
    group_by_parts = []

    for dimension in spec.dimensions:
        table_name, column_name = split_field_ref(dimension)
        expression = f"{quote_ident(table_name)}.{quote_ident(column_name)}"
        alias = safe_alias(f"{table_name}_{column_name}")
        select_parts.append(f"  {expression} AS {quote_ident(alias)}")
        group_by_parts.append(expression)

    for metric_name in spec.metrics:
        metric = catalog.get_metric(metric_name)
        if not metric:
            raise SqlGenerationError(f"Unknown metric: {metric_name}")
        select_parts.append(f"  {metric.expression} AS {quote_ident(safe_alias(metric.name))}")

    if not select_parts:
        select_parts.append("  *")

    sql = ["SELECT", ",\n".join(select_parts), f"FROM {table_ref(base_table)} AS {quote_ident(base_table.name)}"]

    for table_name in spec.joined_tables:
        join = find_relationship(catalog, spec.base_table, table_name)
        if not join:
            raise SqlGenerationError(f"No relationship found between {spec.base_table} and {table_name}.")
        right_table = catalog.get_table(table_name)
        if not right_table:
            raise SqlGenerationError(f"Unknown joined table: {table_name}")
        sql.append(render_join(join, spec.base_table, table_name, right_table))

    where_parts = [render_filter(filter_spec) for filter_spec in spec.filters if filter_spec.value.strip()]
    if where_parts:
        sql.append("WHERE " + "\n  AND ".join(where_parts))

    if group_by_parts and spec.metrics:
        sql.append("GROUP BY " + ", ".join(group_by_parts))

    if spec.order_by:
        sql.append(f"ORDER BY {quote_ident(safe_alias(spec.order_by))} DESC")

    if spec.limit:
        sql.append(f"LIMIT {int(spec.limit)}")

    return "\n".join(sql)


def find_relationship(catalog: SemanticCatalog, base_table: str, target_table: str) -> Relationship | None:
    for relationship in catalog.relationships:
        table_pair = {relationship.left_table, relationship.right_table}
        if table_pair == {base_table, target_table}:
            return relationship
    return None


def render_join(join: Relationship, base_table: str, target_table: str, target: Table) -> str:
    join_type = normalize_join_type(join.join_type)
    if join.left_table == base_table and join.right_table == target_table:
        left = f"{quote_ident(join.left_table)}.{quote_ident(join.left_column)}"
        right = f"{quote_ident(join.right_table)}.{quote_ident(join.right_column)}"
    else:
        left = f"{quote_ident(join.right_table)}.{quote_ident(join.right_column)}"
        right = f"{quote_ident(join.left_table)}.{quote_ident(join.left_column)}"

    return f"{join_type} {table_ref(target)} AS {quote_ident(target.name)}\n  ON {left} = {right}"


def render_filter(filter_spec: FilterSpec) -> str:
    field = f"{quote_ident(filter_spec.table)}.{quote_ident(filter_spec.column)}"
    operator = filter_spec.operator.upper()
    value = filter_spec.value.strip()

    if operator in {"IS NULL", "IS NOT NULL"}:
        return f"{field} {operator}"
    if operator in {"IN", "NOT IN"}:
        values = ", ".join(format_literal(part.strip()) for part in value.split(",") if part.strip())
        return f"{field} {operator} ({values})"
    if operator in {"LIKE", "NOT LIKE"}:
        return f"{field} {operator} {format_literal(value)}"
    return f"{field} {operator} {format_literal(value)}"


def table_ref(table: Table) -> str:
    if table.project and table.dataset:
        return f"`{table.project}.{table.dataset}.{table.name}`"
    if table.dataset:
        return f"`{table.dataset}.{table.name}`"
    return f"`{table.name}`"


def quote_ident(identifier: str) -> str:
    clean = identifier.replace("`", "")
    return f"`{clean}`"


def safe_alias(value: str) -> str:
    return re.sub(r"\W+", "_", value).strip("_").lower()


def split_field_ref(field_ref: str) -> tuple[str, str]:
    table_name, column_name = field_ref.split(".", 1)
    return table_name, column_name


def normalize_join_type(join_type: str) -> str:
    allowed = {"INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL OUTER JOIN"}
    normalized = join_type.upper()
    return normalized if normalized in allowed else "LEFT JOIN"


def format_literal(value: str) -> str:
    if re.fullmatch(r"-?\d+(\.\d+)?", value):
        return value
    if re.fullmatch(r"DATE\s+'.+'|TIMESTAMP\s+'.+'|DATETIME\s+'.+'", value, flags=re.IGNORECASE):
        return value
    if value.upper() in {"TRUE", "FALSE", "NULL"}:
        return value.upper()
    escaped = value.replace("'", "\\'")
    return f"'{escaped}'"
