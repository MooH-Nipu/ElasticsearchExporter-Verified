"""
Download an elasticsearch index to ndjson using a PIT search

Usage:
  ElasticExporterCLI.py [--index=<indexname>] [--multiple-indexes] [--backup-folder=<backup_folder>] [--export-csv]

Options:
  --index=<indexname>  Set the index to export
  --multiple-indexes   Export multiple indexes at once. use a wildcard for --index=
                       e.g. --index=logstash*
  --backup-folder=<backup_folder>
                       Sets the folder to save the export to
  --export-csv         Also convert the json file to csv.
"""

from elasticsearch import Elasticsearch
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
  response = es.field_caps(index=index_name, fields=['*'])
  return sorted(
    field for field, capabilities in response['fields'].items()
    if not field.startswith('_') and any(item.get('searchable') for item in capabilities.values())
  )


def add_field_filter(query, field, value, fields):
  query = json.loads(json.dumps(query))
  if 'bool' not in query:
    query = {'bool': {'must': [query]}}
  filters = [item for item in query['bool'].setdefault('filter', []) if 'match_all' not in item]
  exact_field = field + '.keyword' if field + '.keyword' in fields else field
  clause = {'term': {exact_field: value}} if exact_field.endswith('.keyword') else {'match_phrase': {exact_field: value}}
  filters.append(clause)
  query['bool']['filter'] = filters
  return query


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

  if os.getenv('PROMPT_TIME_RANGE', 'true').lower() == 'true':
    settings['query_filter'] = prompt_time_range(settings['query_filter'], settings['timestamp'], settings['local_utc_offset'])

  if field_filter_enabled():
    settings['query_filter'] = prompt_field_filter(settings['es'], settings['index_name'], settings['query_filter'], settings)

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


