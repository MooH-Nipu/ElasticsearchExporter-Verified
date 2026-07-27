# ElasticsearchExporter Verified

Interactive Python CLI for exporting Elasticsearch indexes to NDJSON or CSV. It discovers indexes, applies optional time and field filters, counts matches before writing, requires confirmation, paginates with PIT + `search_after`, and verifies the exported row count.

## Features

- Elasticsearch index discovery with grouped wildcard choices such as `hids-*`
- Interactive WIB (`UTC+7`) time range converted to UTC for Elasticsearch
- Optional Elasticsearch-side exact field filtering through `_field_caps`
- PIT + `search_after` pagination with configurable page size, timeout, and delay
- One-line progress bar with percentage, count, and ETA
- NDJSON or flattened CSV output
- Exported timestamp conversion to a configured UTC offset
- Per-file SHA-1 checksum metadata and final count verification
- Standalone streaming filter for existing CSV and NDJSON exports

## Requirements

- Python 3.9+
- Direct access to Elasticsearch REST API, normally port `9200`
- Elasticsearch credentials when authentication is enabled

Do not point `ELASTICSEARCH_URL` at Kibana, normally port `5601`.

## Setup

### Windows PowerShell

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

### Linux or macOS

```bash
git clone https://github.com/MooH-Nipu/ElasticsearchExporter-Verified.git
cd ElasticsearchExporter-Verified
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

## Configuration

Copy `.env.example` to `.env`, then edit it:

```dotenv
ELASTICSEARCH_URL=https://192.168.31.1:9200
ELASTICSEARCH_USERNAME=elastic
ELASTICSEARCH_PASSWORD=change-me
ELASTICSEARCH_INDEX=
PROMPT_FIELD_FILTER=false
BACKUP_FOLDER=exported
OUTPUT_FORMAT=json
OUTPUT_NAME=
PROMPT_TIME_RANGE=true
LOCAL_UTC_OFFSET=7
EXPORT_UTC_OFFSET=7
PAGE_SIZE=2000
REQUEST_TIMEOUT=120
PAGE_DELAY=0.05
```

`.env` is ignored by Git. Never commit real credentials.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ELASTICSEARCH_URL` | required | Elasticsearch REST URL |
| `ELASTICSEARCH_USERNAME` | empty | Basic-auth username; set with password |
| `ELASTICSEARCH_PASSWORD` | empty | Basic-auth password; set with username |
| `ELASTICSEARCH_INDEX` | empty | Fixed index or wildcard; empty opens index selection |
| `PROMPT_FIELD_FILTER` | `false` | Enable interactive Elasticsearch-side field filtering |
| `BACKUP_FOLDER` | `exported` | Local export root |
| `OUTPUT_FORMAT` | `json` | `json` for NDJSON or `csv` |
| `OUTPUT_NAME` | empty | Output basename; empty prompts at runtime |
| `PROMPT_TIME_RANGE` | `true` | Prompt for start and end times |
| `LOCAL_UTC_OFFSET` | `7` | Local offset shown by time prompts |
| `EXPORT_UTC_OFFSET` | `7` | Offset applied to exported `TIMESTAMP_FIELD` values |
| `PAGE_SIZE` | `2000` | Documents per request; accepted range `100`–`10000` |
| `REQUEST_TIMEOUT` | `120` | Elasticsearch request timeout in seconds |
| `PAGE_DELAY` | `0.05` | Delay between pages in seconds |
| `TIMESTAMP_FIELD` | `@timestamp` | Field used for time range, sorting, and conversion |
| `TIME_SERIES` | `true` | Set `false` for indexes without a time-series field |
| `DEBUG` | `false` | Print loaded settings and query details |

### HTTPS certificate verification

For HTTPS, the exporter uses `ELASTICSEARCH_CERT_FINGERPRINT` when configured. Otherwise it downloads the server certificate and trusts its SHA-256 fingerprint on first use (TOFU).

TOFU is convenient but cannot detect interception of the first connection. Prefer a fingerprint obtained through a trusted channel:

```dotenv
ELASTICSEARCH_CERT_FINGERPRINT=AA:BB:CC:DD
```

## Run exporter

```powershell
python .\ElasticExporterCLI.py
```

Typical flow:

```text
Available index groups:
1) hids-*
2) logs-single
Select number: 1
Start date/time local UTC+7 (blank for no start): 2026-07-01 00:00:00
End date/time local UTC+7 (blank for no end): 2026-07-01 23:59:59
Output file name (without extension, blank for hids-all): incident-july
Query matched 1,234 documents
Continue export? [y/N]: y
VERIFIED: exported 1,234 of 1,234 matched documents
```

Both time values are required when either is supplied. Timezone-less input is treated as WIB (`UTC+7`) and converted to UTC. Explicit offsets and `Z` are respected.

Only `y` or `yes` starts export. Blank input or any other answer cancels before PIT creation or file output.

### CLI overrides

```powershell
python .\ElasticExporterCLI.py `
  --index="another-index" `
  --backup-folder="another-export"
```

Available options:

```text
--index=<indexname>             Override ELASTICSEARCH_INDEX
--multiple-indexes             Resolve a wildcard and export each concrete index
--backup-folder=<folder>       Override BACKUP_FOLDER
--export-csv                   Force CSV conversion
```

Multiple concrete indexes:

```powershell
python .\ElasticExporterCLI.py --index="filebeat-*" --multiple-indexes
```

## Output

For index `logs-*`, output name `incident-july`, and `BACKUP_FOLDER=exported`:

```text
exported/
└── logs-all/
    └── incident-july/
        ├── incident-july.ndjson
        ├── incident-july.checksums
        └── all.checksums
```

`OUTPUT_FORMAT=csv` creates `incident-july.csv` and removes the temporary NDJSON after successful conversion. Nested objects become dotted CSV columns. Lists are stored as JSON text.

Existing `all.checksums` marks a completed run and causes that output directory to be skipped. Use a different `OUTPUT_NAME` for a distinct query or remove the old output directory deliberately before rerunning it.

`BACKUP_FOLDER` is local file storage, not an Elasticsearch snapshot repository.

## Optional Elasticsearch-side field filter

Set:

```dotenv
PROMPT_FIELD_FILTER=true
```

The CLI discovers searchable fields with `_field_caps`, lets you search and select one, then adds:

- `term` for a `.keyword` field
- `match_phrase` when no keyword field exists

It also checks each returned `_source` against the selected exact value before writing. Field prompting stays disabled by default because `_field_caps` behavior varies across Elasticsearch client/server versions.

For the more compatible option, export first and use `FilterExport.py`.

## Filter an existing export

`FilterExport.py` streams CSV or newline-delimited JSON without contacting Elasticsearch. It never overwrites the input file.

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

Interactive mode:

```powershell
python .\FilterExport.py
```

Values are case-sensitive. Exact values, `*`, and `?` patterns are supported. Repeat `--value` or comma-separate values:

```powershell
python .\FilterExport.py ".\incident.csv" `
  --field "agent.name" `
  --value "TESTAGENT-*" `
  --value "SPECIAL-AGENT"
```

CSV uses flattened fields such as `agent.name`. NDJSON resolves nested paths under `_source`, also using `agent.name` syntax.

Supported input extensions: `.csv`, `.ndjson`, `.jsonl`, and newline-delimited `.json`.

## Tests

```powershell
python -m unittest -v
```

Tests use mocks and temporary files. They do not require a live Elasticsearch cluster.

## Project files

```text
ElasticExporterCLI.py       Interactive CLI and query construction
ElasticExporterSettings.py  .env loading, TLS, and client settings
ElasticExporter.py          PIT export, progress, output, checksum, and CSV logic
FilterExport.py             Streaming post-export filter
test_exporter.py            Exporter and configuration tests
test_filter_export.py       Post-filter tests
.env.example                Configuration template
```

Original project: https://github.com/DisorganizedWizardry/ElasticsearchExporter
