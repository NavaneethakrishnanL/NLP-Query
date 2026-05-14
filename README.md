# Natural Language to BigQuery Generator

Local-first Streamlit app for generating BigQuery SQL from pasted schemas.

It has two paths:

- **No-LLM mode:** paste schemas, define joins and reusable metrics, then build SQL with form controls.
- **Optional local natural language mode:** if Ollama is running locally, ask a local model for suggested builder selections. The final SQL is still generated deterministically by the app.

No ChatGPT or paid API is required.

## Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Example Schema

Paste this into the Schema tab:

```text
table: orders
order_id STRING
customer_id STRING
order_date DATE
revenue NUMERIC

table: customers
customer_id STRING
country STRING
segment STRING
```

Then:

1. Define a relationship from `orders.customer_id` to `customers.customer_id`.
2. Add a metric:

```sql
SUM(orders.revenue)
```

3. Build a query with `customers.country` as a dimension and the metric selected.

## Notes

- SQL generation can work offline.
- Running SQL against BigQuery requires Google Cloud access and may incur BigQuery costs.
- Schemas are saved locally in `data/catalog.json`.
