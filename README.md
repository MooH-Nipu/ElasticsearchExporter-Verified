# ElasticsearchExporter Verified

Interactive Python CLI for exporting Elasticsearch indexes to NDJSON or CSV. It discovers indexes, applies optional time and field filters, counts matches before writing, requires confirmation, paginates with PIT + `search_after`, and verifies the exported row count.

## Features

- Elasticsearch index discovery with grouped wildcard choices such as `hids-*`
- Interactive WIB (`UTC+7`) time range converted to UTC for Elasticsearch
- Command-line date, output-name, document-filter, and source-field selection
- Optional Elasticsearch-side exact field filtering through `_field_caps`
- PIT + `search_after` pagination with configurable page size, timeout, and delay
- One-line progress bar with percentage, count, and ETA
- NDJSON or flattened CSV output
- Exported timestamp conversion to a configured UTC offset
- Per-file SHA-1 checksum metadata for NDJSON and final count verification
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
  --backup-folder="another-export" `
  --start="2026-07-01 00:00:00" `
  --end="2026-07-01 23:59:59" `
  --output-name="incident-july"
```

Available options:

```text
--index=<indexname>             Override ELASTICSEARCH_INDEX
--multiple-indexes             Resolve a wildcard and export each concrete index
--backup-folder=<folder>       Override BACKUP_FOLDER
--export-csv                   Force CSV conversion
--start=<datetime>             Start local date/time; requires --end
--end=<datetime>               End local date/time; requires --start
--output-name=<name>           Override OUTPUT_NAME
--filter=<field=value>         Filter documents; repeatable
--exclude-filter=<field=value> Exclude matching documents; repeatable
--filter-logic=<and|or>        Combine repeated filters (default: and)
--fields=<all|field1,field2>   Select source fields to write
```

For a fully non-interactive query, combine the date, output, document filter, and field selection options:

```powershell
python .\ElasticExporterCLI.py `
  --index="logs-*" `
  --start="2026-07-01 00:00:00" `
  --end="2026-07-01 23:59:59" `
  --output-name="incident-july" `
  --filter="agent.name=HOST-*" `
  --filter="event.kind=alert" `
  --filter-logic=and `
  --fields="@timestamp,agent.name,event.kind,message" `
  --export-csv
```

`--start` and `--end` use the configured local UTC offset. If neither is supplied, the existing date prompt is controlled by `PROMPT_TIME_RANGE`. `--output-name` takes precedence over `OUTPUT_NAME`; if both are empty, the existing output-name prompt is used.

`--filter` selects documents. Repeat it for multiple field/value pairs and use `--filter-logic=or` when any pair may match. `--exclude-filter` removes documents matching any supplied exclusion clause. Values containing `*` or `?` use an Elasticsearch wildcard query; other values use an exact keyword term when available and otherwise `match_phrase`. When command filters are supplied, the interactive `PROMPT_FIELD_FILTER` prompt is skipped.

`--fields` selects data written after filtering. `--fields=all` writes the complete `_source` object without Elasticsearch hit metadata. A comma-separated list writes only those nested source paths, keeping nested JSON in NDJSON and dotted column names in CSV. Missing fields are blank in CSV and omitted from that NDJSON object. Without `--fields`, the legacy full hit format is retained.

### Contoh: ekspor Kaspersky

Contoh berikut mengekspor data tanggal **19 Agustus 2026 pukul 00:00 sampai 18:00 WIB** dari index `kaspersky*` ke CSV dengan field yang dipilih:

```powershell
python .\ElasticExporterCLI.py `
  --index="kaspersky*" `
  --start="2026-08-19 00:00:00" `
  --end="2026-08-19 18:00:00" `
  --output-name="kaspersky-2026-08-19-00-18" `
  --fields="@timestamp,host,kaspersky.meta_data.hdn,kaspersky.meta_data.gn,kaspersky.details.Component,kaspersky.meta_data.etdn,kaspersky.meta_data.kscfqdn,kaspersky.meta_data.tdn" `
  --export-csv
```

Field waktu pada data Kaspersky menggunakan `@timestamp`, bukan `Time`. Karena rentang waktu ditulis tanpa offset, aplikasi memperlakukannya sebagai WIB (`UTC+7`). Setelah query selesai, periksa jumlah dokumen lalu jawab `y` pada prompt `Continue export? [y/N]:` untuk memulai ekspor. File CSV tersimpan di:

```text
exported/kaspersky-all/kaspersky-2026-08-19-00-18/kaspersky-2026-08-19-00-18.csv
```

Jika index yang tersedia bernama persis `kaspersky`, ganti `--index="kaspersky*"` menjadi `--index="kaspersky"`.

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

`OUTPUT_FORMAT=csv` creates `incident-july.csv` and removes the temporary NDJSON after successful conversion. Nested objects become dotted CSV columns. Lists are stored as JSON text. Current checksum metadata describes the temporary NDJSON, not the resulting CSV.

Existing `all.checksums` marks a completed run and causes that output directory to be skipped. Use a different `OUTPUT_NAME` for a distinct query or remove the old output directory deliberately before rerunning it.

`BACKUP_FOLDER` is local file storage, not an Elasticsearch snapshot repository.

## Optional interactive Elasticsearch-side field filter

Set:

```dotenv
PROMPT_FIELD_FILTER=true
```

When no command-line `--filter` is supplied, the CLI can discover searchable fields with `_field_caps`, let you search and select one, then add:

- `term` for a `.keyword` field
- `match_phrase` when no keyword field exists

It also checks each returned `_source` against the selected exact value before writing. Field prompting stays disabled by default because `_field_caps` behavior varies across Elasticsearch client/server versions.

For the more compatible option, export first and use `FilterExport.py`.

### Troubleshooting `_field_caps` pada Elasticsearch 7.17

Jika muncul:

```text
BadRequestError(400, 'illegal_argument_exception', "specified fields can't be null or empty")
```

CLI akan mencoba ulang `_field_caps` menggunakan format GET query-string (`?fields=*`) yang kompatibel dengan server Elasticsearch lama. Pada koneksi normal hanya satu request yang dilakukan; fallback hanya menambah satu request saat format pertama ditolak. Fallback tidak dijalankan untuk error permission, index, atau koneksi.

Client `elasticsearch` versi 9.x sebaiknya dipasangkan dengan server Elasticsearch versi yang kompatibel. Fallback ini mempertahankan client yang terpasang dan membantu deployment dengan server 7.17 tanpa mengubah alur ekspor.

### Troubleshooting Unicode/encoding pada Windows

Semua file NDJSON, CSV, dan checksum yang dibuat exporter sekarang ditulis dan dibaca sebagai UTF-8 tanpa BOM. Jika muncul error seperti:

```text
UnicodeEncodeError: 'charmap' codec can't encode character
```

versi exporter yang dijalankan kemungkinan masih membuka file dengan encoding default Windows (`cp1252`). Tidak perlu menambahkan opsi CLI atau mengubah konfigurasi `.env`; gunakan versi terbaru lalu jalankan command export yang sama. Output yang gagal atau rusak dari versi lama sebaiknya dihapus atau gunakan `OUTPUT_NAME` baru, kemudian ekspor ulang. File lama yang sudah tersimpan dalam `cp1252` tidak dikonversi otomatis.

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
