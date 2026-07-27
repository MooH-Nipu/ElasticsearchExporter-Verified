# ElasticsearchExporter Verified

Exports an Elasticsearch index to NDJSON using PIT and `search_after`. It counts the requested query first, exports matching documents, then verifies exported count against Elasticsearch.

## Windows PowerShell setup

```powershell
git clone https://github.com/MooH-Nipu/ElasticsearchExporter-Verified.git
cd ElasticsearchExporter-Verified
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Configure `.env`:

```dotenv
ELASTICSEARCH_URL=https://192.168.31.1:9200
ELASTICSEARCH_USERNAME=elastic
ELASTICSEARCH_PASSWORD=change-me
ELASTICSEARCH_INDEX=
QUERY_FILE=filter.json
BACKUP_FOLDER=exported
OUTPUT_FORMAT=json
OUTPUT_NAME=
PROMPT_TIME_RANGE=true
```

`.env` is ignored by Git. Do not commit credentials.

For HTTPS, the script retrieves the server certificate and calculates its SHA-256 fingerprint automatically. This is trust-on-first-use: convenient, but vulnerable if the first connection is intercepted. For stronger verification, obtain the fingerprint through a trusted channel and set:

```dotenv
ELASTICSEARCH_CERT_FINGERPRINT=AA:BB:CC:DD
```

## Query file

Put the index query in `filter.json` beside `.env` and `ElasticExporterCLI.py`. `.env` selects it with `QUERY_FILE=filter.json`.

The query limits which documents are exported from `ELASTICSEARCH_INDEX`. Leave `QUERY_FILE=` blank to export every document. The file contains the query clause only; do not add an outer `query` key.

```json
{
  "bool": {
    "filter": [
      {
        "range": {
          "@timestamp": {
            "gte": "2026-07-01T00:00:00.000Z",
            "lte": "2026-07-02T00:00:00.000Z"
          }
        }
      }
    ]
  }
}
```

## Index and time range

Leave `ELASTICSEARCH_INDEX=` blank. Script scans Elasticsearch, lists existing indexes, and asks you to choose one. Set a name when you always want the same index.

Indexes sharing a prefix are grouped. For example, `hids-2026.07.01` and `hids-2026.07.02` appear as `hids-*`; selecting that group exports all matching concrete indexes in one run.

Optional UTC time range accepts seconds:

```text
Start date/time UTC (blank for no start): 2026-07-01T00:00:00Z
End date/time UTC (blank for no end): 2026-07-02T23:59:59Z
```

Both values are required when filtering by time. Bare input is treated as WIB (`UTC+7`) and converted to UTC before querying Elasticsearch. For example, `2026-07-01 14:30:15` becomes `2026-07-01T07:30:15Z`. Explicit offsets such as `2026-07-01T09:30:15+02:00` are respected. Terminal time is authoritative: any existing range for `TIMESTAMP_FIELD` in `filter.json` is replaced, while other filters remain. Set `PROMPT_TIME_RANGE=false` to use the query file's time instead. `TIMESTAMP_FIELD` defaults to `@timestamp`.

Export runs in pages. `PAGE_SIZE=2000` controls documents per request; `REQUEST_TIMEOUT=120` controls request timeout in seconds. Smaller pages avoid slow 10,000-document responses. `PAGE_DELAY=0.05` adds a short pause between pages to reduce sustained cluster/CPU pressure. Set it to `0` for maximum throughput.

## Output format

Choose in `.env`:

```dotenv
OUTPUT_FORMAT=json
```

`json` writes newline-delimited JSON, one valid JSON object per line. Use `OUTPUT_FORMAT=csv` to write only CSV; temporary NDJSON is removed after conversion. Exported `TIMESTAMP_FIELD` values are converted to `EXPORT_UTC_OFFSET=7`. CSV values no longer get artificial apostrophe wrappers from Python `repr()`; legitimate spaces inside log text are preserved.

## Field filter

Elasticsearch-side field filtering is disabled by default because `_field_caps` compatibility varies across server/client versions. Use `FilterExport.py` after export for reliable agent filtering. Set `PROMPT_FIELD_FILTER=true` only to opt into the legacy interactive Elasticsearch-side filter.

Example flow:

```text
Filter by a field value? [y/N]: y
Search field name (example: agent.name): agent.name
1) agent.name
2) agent.name.keyword
Select field number: 2
Field value to export: my-agent
Query matched 1,234 documents
Continue export? [y/N]: y
```

For exact values the exporter uses `term` on `.keyword`. For text-only fields it uses `match_phrase`. The selected field/value is also checked against every returned `_source` before writing, so rows from another agent cannot enter NDJSON or CSV. Set `PROMPT_FIELD_FILTER=false` to skip this prompt and use only `filter.json` plus the terminal time range.

Set `OUTPUT_NAME=incident-july` to create `incident-july.ndjson` or `incident-july.csv`. Leave blank to choose at runtime.

`BACKUP_FOLDER` is parent directory for exports and verification files. With `BACKUP_FOLDER=exported`, index `logs-a`, and output name `incident-july`, output lands under `exported\logs-a\incident-july.csv`. It is not an Elasticsearch snapshot or server-side backup.

## Run

```powershell
python .\ElasticExporterCLI.py
```

The script counts matches, then requires human approval before creating export output:

```text
Query matched 1,234 documents
Continue export? [y/N]: y
VERIFIED: exported 1,234 of 1,234 matched documents
```

Only `y` or `yes` continues. Enter, `n`, or any other input cancels. A completed count discrepancy prints `MISMATCH`.

During export, CLI shows a progress bar with percentage, documents completed, and total.

CLI values override `.env`:

```powershell
python .\ElasticExporterCLI.py `
  --index="another-index" `
  --backup-folder="another-export" `
  --query-file="another-filter.json"
```

Multiple indexes:

```powershell
python .\ElasticExporterCLI.py --index="filebeat-*" --multiple-indexes
```

## Filter an existing export

`FilterExport.py` filters CSV or newline-delimited JSON after export without contacting Elasticsearch. It streams one row at a time, so large files do not need to fit in RAM. The original file is never overwritten.

CSV:

```powershell
python .\FilterExport.py `
  ".\exported\hids-all\incident\incident.csv" `
  --field "agent.name" `
  --value "wanted-agent" `
  --output ".\incident-wanted-agent.csv"
```

NDJSON:

```powershell
python .\FilterExport.py `
  ".\exported\hids-all\incident\incident.ndjson" `
  --field "agent.name" `
  --value "wanted-agent" `
  --output ".\incident-wanted-agent.ndjson"
```

Or run interactively:

```powershell
python .\FilterExport.py
```

Result:

```text
Scanned: 100,000 | Kept: 3,214 | Output: incident-wanted-agent.csv
```

Matching is case-sensitive. Values without wildcard characters match exactly. `*` matches any number of characters and `?` matches one character. Pass multiple `--value` options or comma-separate them:

```powershell
python .\FilterExport.py ".\incident.csv" `
  --field "agent.name" `
  --value "TESTAGENT-*" `
  --value "SPECIAL-AGENT" `
  --output ".\selected-agents.csv"
```

Equivalent:

```powershell
python .\FilterExport.py ".\incident.csv" `
  --field "agent.name" `
  --value "TESTAGENT-*,SPECIAL-AGENT"
```

This works for CSV and NDJSON. CSV expects flattened `agent.name`; NDJSON reads nested `_source.agent.name`.

## Tests

```powershell
python -m unittest -v
```

Original project: https://github.com/DisorganizedWizardry/ElasticsearchExporter
