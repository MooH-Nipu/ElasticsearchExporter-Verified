"""
Download an elasticsearch index to ndjson using a PIT search

Usage:
  ElasticExporterCLI.py [--index=<indexname>] [--multiple-indexes] [--backup-folder=<backup_folder>] [--export-csv]
                        [--start=<datetime>] [--end=<datetime>] [--output-name=<name>]
                        [--filter=<field=value>]... [--exclude-filter=<field=value>]...
                        [--filter-logic=<logic>] [--fields=<fields>]

Options:
  --index=<indexname>  Set the index to export
  --multiple-indexes   Export multiple indexes at once. use a wildcard for --index=
                       e.g. --index=logstash*
  --backup-folder=<backup_folder>
                       Sets the folder to save the export to
  --export-csv         Also convert the json file to csv.
  --start=<datetime>   Start date/time local to the configured UTC offset.
  --end=<datetime>     End date/time local to the configured UTC offset.
  --output-name=<name> Output file name without extension.
  --filter=<field=value>
                       Filter documents by an exact value or * / ? wildcard. Repeatable.
  --exclude-filter=<field=value>
                       Exclude documents matching an exact value or * / ? wildcard. Repeatable.
  --filter-logic=<logic>
                       Combine repeated filters with and or or [default: and].
  --fields=<fields>    Export all source fields or a comma-separated list of source paths.
"""

from elasticsearch import BadRequestError, Elasticsearch
import json
import os
from datetime import datetime, timedelta, timezone
from docopt import docopt

#library for ElasticExporter
import ElasticExporter

#local config 
import ElasticExporterSettings


def index_choices(indexes):
  groups = {}
  for index in indexes:
    prefix, separator, _ = index.partition('-')
    if separator:
      groups.setdefault(prefix + '-*', []).append(index)
  grouped = sorted(group for group, members in groups.items() if len(members) > 1)
  grouped_members = {member for group in grouped for member in groups[group]}
  singletons = sorted(index for index in indexes if index not in grouped_members)
  return grouped + singletons


def select_index(es):
  indexes = sorted({item['index'] for item in es.cat.indices(format='json', h='index', expand_wildcards='open')})
  if not indexes:
    raise ValueError('No Elasticsearch indexes found')
  choices = index_choices(indexes)
  print('Available index groups:')
  for number, index in enumerate(choices, 1):
    print('%s) %s' % (number, index))
  while True:
    choice = input('Select number: ').strip()
    if choice.isdigit() and 1 <= int(choice) <= len(choices):
      return choices[int(choice) - 1]
    print('Invalid index selection')


def searchable_fields(es, index_name):
  return sorted(searchable_field_capabilities(es, index_name))


def _is_empty_field_caps_error(error):
  message = str(error)
  if "specified fields can't be null or empty" in message:
    return True
  body = getattr(error, 'body', None)
  try:
    return "specified fields can't be null or empty" in json.dumps(body, ensure_ascii=False)
  except (TypeError, ValueError):
    return False


def field_caps_response(es, index_name):
  try:
    return es.field_caps(index=index_name, fields=['*'])
  except BadRequestError as error:
    if not _is_empty_field_caps_error(error):
      raise
    return es.perform_request(
      'GET',
      '/%s/_field_caps' % index_name,
      params={'fields': '*'},
    )


def searchable_field_capabilities(es, index_name):
  response = field_caps_response(es, index_name)
  return {
    field: capabilities
    for field, capabilities in response['fields'].items()
    if not field.startswith('_') and any(item.get('searchable') for item in capabilities.values())
  }


def _field_names(fields):
  return set(fields.keys()) if isinstance(fields, dict) else set(fields or [])


def _keyword_fields(fields):
  if not isinstance(fields, dict):
    return set()
  keyword_types = {'keyword', 'constant_keyword', 'wildcard'}
  return {
    field for field, capabilities in fields.items()
    if any(field_type in keyword_types for field_type in capabilities)
  }


def _field_filter_clause(field, value, fields):
  field_names = _field_names(fields)
  exact_field = field + '.keyword' if field + '.keyword' in field_names else field
  if '*' in value or '?' in value:
    return {'wildcard': {exact_field: value}}
  if exact_field.endswith('.keyword') or exact_field in _keyword_fields(fields):
    return {'term': {exact_field: value}}
  return {'match_phrase': {exact_field: value}}


def parse_filter_specs(specs):
  if isinstance(specs, str):
    specs = [specs]
  parsed = []
  for spec in specs or []:
    if '=' not in spec:
      raise ValueError('Filter must use field=value')
    field, value = spec.split('=', 1)
    field = field.strip()
    value = value.strip()
    if not field or not value:
      raise ValueError('Filter field and value cannot be empty')
    parsed.append((field, value))
  return parsed


def add_field_filters(query, filters, logic='and', fields=None):
  logic = (logic or 'and').lower()
  if logic not in ('and', 'or'):
    raise ValueError('Filter logic must be and or or')
  query = json.loads(json.dumps(query))
  if 'bool' not in query:
    query = {'bool': {} if 'match_all' in query else {'must': [query]}}
  existing = query['bool'].setdefault('filter', [])
  existing = [
    item for item in existing
    if not (isinstance(item, dict) and 'match_all' in item)
  ]
  clauses = [_field_filter_clause(field, value, fields) for field, value in filters]
  if logic == 'and':
    existing.extend(clauses)
  elif clauses:
    existing.append({
      'bool': {
        'should': clauses,
        'minimum_should_match': 1,
      }
    })
  query['bool']['filter'] = existing
  return query


def add_exclude_filters(query, filters, fields=None):
  query = json.loads(json.dumps(query))
  if 'bool' not in query:
    query = {'bool': {} if 'match_all' in query else {'must': [query]}}
  existing = query['bool'].setdefault('filter', [])
  query['bool']['filter'] = [
    item for item in existing
    if not (isinstance(item, dict) and 'match_all' in item)
  ]
  clauses = [_field_filter_clause(field, value, fields) for field, value in filters]
  query['bool'].setdefault('must_not', []).extend(clauses)
  return query


def add_field_filter(query, field, value, fields):
  return add_field_filters(query, [(field, value)], fields=fields)


def parse_export_fields(value):
  if value is None:
    return None
  if not isinstance(value, str) or not value.strip():
    raise ValueError('Export field list cannot be empty')
  parts = [part.strip() for part in value.split(',')]
  if any(not part for part in parts):
    raise ValueError('Export field list cannot contain empty fields')
  lowered = [part.lower() for part in parts]
  if 'all' in lowered:
    if len(parts) != 1 or lowered[0] != 'all':
      raise ValueError('The all field selection cannot be combined with other fields')
    return 'all'
  result = []
  for part in parts:
    if part not in result:
      result.append(part)
  return result


def apply_cli_query_options(settings, options):
  query = settings['query_filter']
  filter_logic = (options.get('--filter-logic') or 'and').lower()
  if filter_logic not in ('and', 'or'):
    raise ValueError('Filter logic must be and or or')
  start = options.get('--start')
  end = options.get('--end')
  if start is not None or end is not None:
    if not start or not end:
      raise ValueError('Both start and end date/time are required')
    query = add_time_range(
      query,
      start,
      end,
      settings['timestamp'],
      settings['local_utc_offset'],
    )
  elif os.getenv('PROMPT_TIME_RANGE', 'true').lower() == 'true':
    query = prompt_time_range(
      query,
      settings['timestamp'],
      settings['local_utc_offset'],
    )

  raw_filters = options.get('--filter') or []
  raw_exclude_filters = options.get('--exclude-filter') or []
  if raw_filters or raw_exclude_filters:
    filters = parse_filter_specs(raw_filters)
    exclude_filters = parse_filter_specs(raw_exclude_filters)
    try:
      fields = searchable_field_capabilities(settings['es'], settings['index_name'])
    except Exception as error:
      print('Field discovery failed: %s' % error)
      fields = []
    if filters:
      query = add_field_filters(query, filters, logic=filter_logic, fields=fields)
    if exclude_filters:
      query = add_exclude_filters(query, exclude_filters, fields=fields)
  elif field_filter_enabled():
    query = prompt_field_filter(settings['es'], settings['index_name'], query, settings)
  settings['query_filter'] = query
  return query


def apply_cli_output_options(settings, options):
  if options.get('--output-name') is not None:
    if not options['--output-name'].strip():
      raise ValueError('OUTPUT_NAME cannot be empty')
    settings['output_name'] = options['--output-name']
  settings['export_fields'] = parse_export_fields(options.get('--fields'))
  return settings


def prompt_field_filter(es, index_name, query, settings=None):
  if input('Filter by a field value? [y/N]: ').strip().lower() not in ('y', 'yes'):
    return query
  try:
    fields = searchable_fields(es, index_name)
  except Exception as error:
    print('Field discovery failed: %s' % error)
    field = input('Enter field name manually: ').strip()
    value = input('Field value to export: ').strip()
    if not field or not value:
      raise ValueError('Field name and value cannot be empty')
    if settings is not None:
      settings['output_filter_field'] = field
      settings['output_filter_value'] = value
    return add_field_filter(query, field, value, [field])
  search = input('Search field name (example: agent.name): ').strip().lower()
  matches = [field for field in fields if search in field.lower()][:30]
  if not matches:
    raise ValueError('No searchable fields matched %r' % search)
  for number, field in enumerate(matches, 1):
    print('%s) %s' % (number, field))
  while True:
    choice = input('Select field number: ').strip()
    if choice.isdigit() and 1 <= int(choice) <= len(matches):
      field = matches[int(choice) - 1]
      break
    print('Invalid field selection')
  value = input('Field value to export: ').strip()
  if not value:
    raise ValueError('Field value cannot be empty')
  exact_field = field + '.keyword' if field + '.keyword' in fields else field
  if settings is not None:
    settings['output_filter_field'] = exact_field
    settings['output_filter_value'] = value
  return add_field_filter(query, field, value, fields)


def to_utc(value, utc_offset=7):
  parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
  if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone(timedelta(hours=utc_offset)))
  return parsed.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def add_time_range(query, start, end, timestamp='@timestamp', utc_offset=7):
  start = to_utc(start, utc_offset)
  end = to_utc(end, utc_offset)
  query = json.loads(json.dumps(query))
  if 'bool' not in query:
    query = {'bool': {'must': [query]}}
  filters = query['bool'].setdefault('filter', [])
  query['bool']['filter'] = [
    item for item in filters
    if not (isinstance(item, dict) and 'range' in item and timestamp in item['range'])
  ]
  query['bool']['filter'].append(
    {'range': {timestamp: {'gte': start, 'lte': end}}}
  )
  return query


def prompt_time_range(query, timestamp, utc_offset=7):
  start = input('Start date/time local UTC%+g (blank for no start): ' % utc_offset).strip()
  end = input('End date/time local UTC%+g (blank for no end): ' % utc_offset).strip()
  if not start and not end:
    return query
  if not start or not end:
    raise ValueError('Both start and end date/time are required')
  return add_time_range(query, start, end, timestamp, utc_offset)


def field_filter_enabled():
  return os.getenv('PROMPT_FIELD_FILTER', 'false').lower() == 'true'


def main():
  options = docopt(__doc__)

  #Load local config
  settings = ElasticExporterSettings.LoadSettings()

  if settings.get('debug'):
    print ("Loaded settings : %s" % settings)

  if options.get('--index'):
    settings['index_name'] = options['--index']

  if not settings.get('index_name'):
    settings['index_name'] = select_index(settings['es'])

  #if options['--no_group']:
  #  settings['NoGroup'] = True
  #else:
  #  settings['NoGroup'] = False
  #set default setting until this feature is added
  settings['NoGroup'] = False

  settings['query_filter'] = { "bool": { "filter": [ { "match_all": {} } ], } }

  apply_cli_query_options(settings, options)
  apply_cli_output_options(settings, options)

  if not settings.get('output_name'):
    default_name = settings['index_name'].replace('*', 'all')
    settings['output_name'] = input('Output file name (without extension, blank for %s): ' % default_name).strip() or default_name
  if os.path.basename(settings['output_name']) != settings['output_name'] or settings['output_name'] in ('.', '..'):
    raise ValueError('OUTPUT_NAME must be a file name without a path')
  settings['output_name'] = os.path.splitext(settings['output_name'])[0]
  settings['FileNameOther'] = settings['output_name']

  #folder to save exported ndjson files
  if options.get('--backup-folder'):
    settings['backup_folder'] = options['--backup-folder']
    
  settings['export-csv'] = options.get('--export-csv') or settings['output_format'] == 'csv'

  if settings.get('debug'):
    print (settings)
    
  if options.get('--multiple-indexes'):
    ElasticExporter.ProcessMultipleIndexes(settings)
    return

  ElasticExporter.ProcessIndex(settings)


if __name__ == "__main__":
  main()
