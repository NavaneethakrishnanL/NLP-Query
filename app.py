from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.append(str(Path(__file__).parent / "src"))

from local_llm import ollama_available, suggest_query_plan
from schema_model import CATALOG_PATH, Metric, Relationship, SemanticCatalog, parse_schema_text
from sql_generator import FilterSpec, QuerySpec, SqlGenerationError, generate_sql


st.set_page_config(page_title="Natural Language to BigQuery", page_icon="BQ", layout="wide")

JOIN_TYPES = ["LEFT JOIN", "INNER JOIN", "RIGHT JOIN", "FULL OUTER JOIN"]
FILTER_OPERATORS = ["=", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "LIKE", "NOT LIKE", "IS NULL", "IS NOT NULL"]


def load_catalog() -> SemanticCatalog:
    if "catalog" not in st.session_state:
        st.session_state.catalog = SemanticCatalog.load()
    return st.session_state.catalog


def save_catalog(catalog: SemanticCatalog) -> None:
    catalog.save()
    st.session_state.catalog = catalog


def field_options(catalog: SemanticCatalog, selected_tables: list[str]) -> list[str]:
    fields: list[str] = []
    for table_name in selected_tables:
        for column_name in catalog.column_names(table_name):
            fields.append(f"{table_name}.{column_name}")
    return fields


def upsert_metric(catalog: SemanticCatalog, metric: Metric) -> None:
    catalog.metrics = [existing for existing in catalog.metrics if existing.name != metric.name]
    catalog.metrics.append(metric)
    save_catalog(catalog)


def upsert_relationship(catalog: SemanticCatalog, relationship: Relationship) -> None:
    catalog.relationships = [existing for existing in catalog.relationships if existing.name != relationship.name]
    catalog.relationships.append(relationship)
    save_catalog(catalog)


catalog = load_catalog()

st.title("Natural Language to BigQuery Generator")
st.caption("Free local app: deterministic SQL builder first, optional local Ollama suggestions second.")

with st.sidebar:
    st.header("Catalog")
    st.write(f"Saved catalog: `{CATALOG_PATH}`")
    if st.button("Reload catalog", use_container_width=True):
        st.session_state.catalog = SemanticCatalog.load()
        st.rerun()
    if st.button("Clear in-memory catalog", use_container_width=True):
        st.session_state.catalog = SemanticCatalog()
        st.rerun()

tabs = st.tabs(["Schema", "Relationships", "Metrics", "Build SQL", "Local NL"])

with tabs[0]:
    left, right = st.columns([0.58, 0.42], gap="large")
    with left:
        st.subheader("Paste Table Schema")
        default_project = st.text_input("Default BigQuery project", placeholder="my-project")
        default_dataset = st.text_input("Default dataset", placeholder="analytics")
        schema_text = st.text_area(
            "Schema text",
            height=300,
            placeholder="""table: orders
order_id STRING
customer_id STRING
order_date DATE
revenue NUMERIC

table: customers
customer_id STRING
country STRING
segment STRING""",
        )
        if st.button("Parse and save schema", type="primary"):
            parsed_tables = parse_schema_text(schema_text, default_project, default_dataset)
            if not parsed_tables:
                st.error("I could not find columns in that schema. Try CREATE TABLE syntax or lines like: column_name TYPE.")
            else:
                existing_by_name = {table.name: table for table in catalog.tables}
                for table in parsed_tables:
                    existing_by_name[table.name] = table
                catalog.tables = list(existing_by_name.values())
                save_catalog(catalog)
                st.success(f"Saved {len(parsed_tables)} table(s).")
                st.rerun()

    with right:
        st.subheader("Current Tables")
        if not catalog.tables:
            st.info("Paste a schema to begin.")
        for table in catalog.tables:
            with st.expander(f"{table.name} ({len(table.columns)} columns)", expanded=False):
                st.write(f"Reference: `{table.fq_name or table.name}`")
                for column in table.columns:
                    description = f" - {column.description}" if column.description else ""
                    st.code(f"{column.name} {column.type}{description}", language="text")

with tabs[1]:
    st.subheader("Define Joins")
    if len(catalog.tables) < 2:
        st.info("Add at least two tables before defining relationships.")
    else:
        table_names = catalog.table_names()
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            left_table = st.selectbox("Left table", table_names)
            left_column = st.selectbox("Left column", catalog.column_names(left_table))
        with col2:
            right_table = st.selectbox("Right table", [name for name in table_names if name != left_table])
            right_column = st.selectbox("Right column", catalog.column_names(right_table))
        with col3:
            join_type = st.selectbox("Join type", JOIN_TYPES)
        with col4:
            relationship_name = st.text_input("Relationship name", value=f"{left_table}_{right_table}")

        if st.button("Save relationship", type="primary"):
            upsert_relationship(
                catalog,
                Relationship(
                    name=relationship_name,
                    left_table=left_table,
                    left_column=left_column,
                    right_table=right_table,
                    right_column=right_column,
                    join_type=join_type,
                ),
            )
            st.success("Relationship saved.")
            st.rerun()

    st.divider()
    if catalog.relationships:
        st.write("Saved relationships")
        for relationship in catalog.relationships:
            st.code(
                f"{relationship.name}: {relationship.left_table}.{relationship.left_column} "
                f"{relationship.join_type} {relationship.right_table}.{relationship.right_column}",
                language="text",
            )

with tabs[2]:
    st.subheader("Reusable Metrics and Formulas")
    st.caption("Use BigQuery expressions with table aliases, for example: SAFE_DIVIDE(SUM(orders.revenue), COUNT(DISTINCT orders.order_id))")
    metric_name = st.text_input("Metric name", placeholder="average_order_value")
    metric_expression = st.text_area("Metric expression", placeholder="SAFE_DIVIDE(SUM(orders.revenue), COUNT(DISTINCT orders.order_id))")
    metric_description = st.text_input("Description", placeholder="Revenue divided by number of orders")
    if st.button("Save metric", type="primary"):
        if not metric_name or not metric_expression:
            st.error("Metric name and expression are required.")
        else:
            upsert_metric(catalog, Metric(name=metric_name, expression=metric_expression, description=metric_description))
            st.success("Metric saved.")
            st.rerun()

    st.divider()
    if catalog.metrics:
        for metric in catalog.metrics:
            with st.expander(metric.name):
                st.code(metric.expression, language="sql")
                if metric.description:
                    st.write(metric.description)

with tabs[3]:
    st.subheader("Build BigQuery SQL")
    if not catalog.tables:
        st.info("Add schemas before building SQL.")
    else:
        table_names = catalog.table_names()
        base_table = st.selectbox("Base table", table_names)
        joinable_tables = [
            name
            for name in table_names
            if name != base_table and any({rel.left_table, rel.right_table} == {base_table, name} for rel in catalog.relationships)
        ]
        joined_tables = st.multiselect("Joined tables", joinable_tables)
        selected_tables = [base_table] + joined_tables
        fields = field_options(catalog, selected_tables)

        dimensions = st.multiselect("Dimensions", fields)
        metrics = st.multiselect("Metrics / formulas", [metric.name for metric in catalog.metrics])

        st.write("Filters")
        filter_count = st.number_input("Number of filters", min_value=0, max_value=10, value=0, step=1)
        filters: list[FilterSpec] = []
        for index in range(filter_count):
            f1, f2, f3 = st.columns([0.4, 0.2, 0.4])
            with f1:
                filter_field = st.selectbox(f"Filter field {index + 1}", fields, key=f"filter_field_{index}")
            with f2:
                operator = st.selectbox("Operator", FILTER_OPERATORS, key=f"filter_operator_{index}")
            with f3:
                value = st.text_input("Value", key=f"filter_value_{index}", disabled=operator in {"IS NULL", "IS NOT NULL"})
            table_name, column_name = filter_field.split(".", 1)
            filters.append(FilterSpec(table=table_name, column=column_name, operator=operator, value=value))

        order_candidates = [field.split(".", 1)[1] for field in dimensions] + metrics
        order_by = st.selectbox("Order by", [""] + order_candidates)
        limit = st.number_input("Limit", min_value=0, max_value=100000, value=100, step=10)

        if st.button("Generate SQL", type="primary"):
            try:
                spec = QuerySpec(
                    base_table=base_table,
                    joined_tables=joined_tables,
                    dimensions=dimensions,
                    metrics=metrics,
                    filters=filters,
                    order_by=order_by,
                    limit=int(limit),
                )
                st.session_state.generated_sql = generate_sql(catalog, spec)
            except SqlGenerationError as exc:
                st.error(str(exc))

        generated_sql = st.session_state.get("generated_sql", "")
        if generated_sql:
            st.code(generated_sql, language="sql")
            st.download_button("Download SQL", generated_sql, file_name="query.sql", mime="text/sql")

with tabs[4]:
    st.subheader("Optional Local Natural Language")
    st.caption("This uses Ollama on your own machine. It is optional and does not call ChatGPT or any paid API.")
    model = st.text_input("Ollama model", value="qwen2.5-coder")
    question = st.text_area("Business question", placeholder="Show monthly revenue by country for 2025")
    available = ollama_available()
    st.write("Ollama status:", "available" if available else "not running")
    if st.button("Suggest builder selections", disabled=not available or not question):
        try:
            suggestion = suggest_query_plan(question, catalog, model)
            st.code(suggestion, language="json")
        except Exception as exc:
            st.error(f"Local model request failed: {exc}")
