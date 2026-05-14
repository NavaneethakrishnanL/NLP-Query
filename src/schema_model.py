from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


CATALOG_PATH = Path("data/catalog.json")


@dataclass
class Column:
    name: str
    type: str = "STRING"
    description: str = ""


@dataclass
class Table:
    name: str
    project: str = ""
    dataset: str = ""
    columns: list[Column] = field(default_factory=list)
    description: str = ""

    @property
    def fq_name(self) -> str:
        parts = [self.project, self.dataset, self.name]
        return ".".join(part for part in parts if part)


@dataclass
class Relationship:
    name: str
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    join_type: str = "LEFT JOIN"


@dataclass
class Metric:
    name: str
    expression: str
    description: str = ""


@dataclass
class SemanticCatalog:
    tables: list[Table] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)

    def table_names(self) -> list[str]:
        return [table.name for table in self.tables]

    def get_table(self, name: str) -> Table | None:
        return next((table for table in self.tables if table.name == name), None)

    def get_metric(self, name: str) -> Metric | None:
        return next((metric for metric in self.metrics if metric.name == name), None)

    def column_names(self, table_name: str) -> list[str]:
        table = self.get_table(table_name)
        return [column.name for column in table.columns] if table else []

    def save(self, path: Path = CATALOG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = CATALOG_PATH) -> "SemanticCatalog":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return catalog_from_dict(data)


def catalog_from_dict(data: dict[str, Any]) -> SemanticCatalog:
    tables = [
        Table(
            name=table["name"],
            project=table.get("project", ""),
            dataset=table.get("dataset", ""),
            description=table.get("description", ""),
            columns=[
                Column(
                    name=column["name"],
                    type=column.get("type", "STRING"),
                    description=column.get("description", ""),
                )
                for column in table.get("columns", [])
            ],
        )
        for table in data.get("tables", [])
    ]
    relationships = [Relationship(**relationship) for relationship in data.get("relationships", [])]
    metrics = [Metric(**metric) for metric in data.get("metrics", [])]
    return SemanticCatalog(tables=tables, relationships=relationships, metrics=metrics)


def parse_schema_text(raw_schema: str, default_project: str = "", default_dataset: str = "") -> list[Table]:
    """Parse common pasted schema formats into tables.

    Supports:
    - CREATE TABLE statements with column definitions.
    - BigQuery-style lines: column_name TYPE description...
    - CSV-ish lines: column_name,type,description
    - Multiple table blocks introduced by "table: name".
    """
    raw_schema = raw_schema.strip()
    if not raw_schema:
        return []

    create_tables = _parse_create_table_statements(raw_schema, default_project, default_dataset)
    if create_tables:
        return create_tables

    blocks = _split_named_blocks(raw_schema)
    if not blocks:
        blocks = [("pasted_table", raw_schema)]

    tables: list[Table] = []
    for table_name, block in blocks:
        columns = _parse_column_lines(block)
        if columns:
            tables.append(
                Table(
                    name=_clean_identifier(table_name),
                    project=default_project,
                    dataset=default_dataset,
                    columns=columns,
                )
            )
    return tables


def _parse_create_table_statements(raw_schema: str, default_project: str, default_dataset: str) -> list[Table]:
    pattern = re.compile(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+`?([\w.-]+)`?\s*\((.*?)\)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    tables: list[Table] = []
    for match in pattern.finditer(raw_schema):
        table_ref, body = match.groups()
        parts = table_ref.split(".")
        project = default_project
        dataset = default_dataset
        table_name = parts[-1]
        if len(parts) == 3:
            project, dataset, table_name = parts
        elif len(parts) == 2:
            dataset, table_name = parts
        columns = _parse_column_lines(body.replace(",", "\n"))
        tables.append(Table(name=table_name, project=project, dataset=dataset, columns=columns))
    return tables


def _split_named_blocks(raw_schema: str) -> list[tuple[str, str]]:
    table_header = re.compile(r"^\s*(?:table|name)\s*[:=]\s*`?([\w.-]+)`?\s*$", re.IGNORECASE)
    blocks: list[tuple[str, list[str]]] = []
    current_name = ""
    current_lines: list[str] = []

    for line in raw_schema.splitlines():
        header = table_header.match(line)
        if header:
            if current_name and current_lines:
                blocks.append((current_name, current_lines))
            current_name = header.group(1).split(".")[-1]
            current_lines = []
            continue
        if current_name:
            current_lines.append(line)

    if current_name and current_lines:
        blocks.append((current_name, current_lines))

    return [(name, "\n".join(lines)) for name, lines in blocks]


def _parse_column_lines(block: str) -> list[Column]:
    columns: list[Column] = []
    ignored_prefixes = ("#", "--", "partition", "cluster", "constraint", "primary key", "foreign key")
    for raw_line in block.splitlines():
        line = raw_line.strip().strip(",")
        if not line or line.lower().startswith(ignored_prefixes):
            continue
        line = re.sub(r"\s+OPTIONS\s*\(.*?\)", "", line, flags=re.IGNORECASE)
        line = line.replace("`", "")

        if "," in line:
            parts = [part.strip() for part in line.split(",", 2)]
        else:
            parts = re.split(r"\s+", line, maxsplit=2)

        if len(parts) < 2:
            continue

        name = _clean_identifier(parts[0])
        column_type = parts[1].upper()
        description = parts[2].strip() if len(parts) > 2 else ""

        if _looks_like_column(name, column_type):
            columns.append(Column(name=name, type=column_type, description=description))
    return columns


def _looks_like_column(name: str, column_type: str) -> bool:
    valid_type = re.match(
        r"^(STRING|INT64|INTEGER|FLOAT64|FLOAT|NUMERIC|BIGNUMERIC|BOOLEAN|BOOL|DATE|DATETIME|TIME|TIMESTAMP|JSON|BYTES|ARRAY|STRUCT|RECORD)",
        column_type,
    )
    return bool(name and valid_type and re.match(r"^[A-Za-z_][\w$]*$", name))


def _clean_identifier(value: str) -> str:
    return value.strip().strip("`").split(".")[-1]
