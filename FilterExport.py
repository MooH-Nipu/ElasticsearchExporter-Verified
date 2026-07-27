#!/usr/bin/env python3
import argparse
import csv
import fnmatch
import json
from pathlib import Path


def nested_value(item, field):
  current = item.get('_source', item)
  for part in field.removesuffix('.keyword').split('.'):
    if not isinstance(current, dict) or part not in current:
      return None
    current = current[part]
  return current


def patterns(values):
  values = [values] if isinstance(values, str) else values
  result = [part.strip() for value in values for part in value.split(',') if part.strip()]
  if not result:
    raise ValueError('At least one filter value is required')
  return result


def matches(value, values):
  return isinstance(value, str) and any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns(values))


def filter_csv(source, target, field, values):
  with source.open(newline='', encoding='utf-8-sig') as input_file:
    reader = csv.DictReader(input_file)
    if not reader.fieldnames or field not in reader.fieldnames:
      raise ValueError("CSV field %r not found. Available: %s" % (field, ', '.join(reader.fieldnames or [])))
    with target.open('w', newline='', encoding='utf-8') as output_file:
      writer = csv.DictWriter(output_file, fieldnames=reader.fieldnames)
      writer.writeheader()
      scanned = kept = 0
      for row in reader:
        scanned += 1
        if matches(row[field], values):
          writer.writerow(row)
          kept += 1
  return scanned, kept


def filter_ndjson(source, target, field, values):
  scanned = kept = 0
  with source.open(encoding='utf-8-sig') as input_file, target.open('w', encoding='utf-8') as output_file:
    for line_number, line in enumerate(input_file, 1):
      if not line.strip():
        continue
      scanned += 1
      try:
        item = json.loads(line)
      except json.JSONDecodeError as error:
        raise ValueError('Invalid NDJSON on line %s: %s' % (line_number, error)) from error
      if matches(nested_value(item, field), values):
        output_file.write(json.dumps(item, ensure_ascii=False, separators=(',', ':')) + '\n')
        kept += 1
  return scanned, kept


def filter_file(source, target, field, values):
  source = Path(source).resolve()
  target = Path(target).resolve()
  if source == target:
    raise ValueError('Output must differ from input')
  if not source.is_file():
    raise FileNotFoundError(source)
  target.parent.mkdir(parents=True, exist_ok=True)
  if source.suffix.lower() == '.csv':
    return filter_csv(source, target, field, values)
  if source.suffix.lower() in ('.ndjson', '.jsonl', '.json'):
    return filter_ndjson(source, target, field, values)
  raise ValueError('Supported input: .csv, .ndjson, .jsonl, or newline-delimited .json')


def main():
  parser = argparse.ArgumentParser(description='Stream-filter exported CSV or NDJSON by field values or wildcards.')
  parser.add_argument('input', nargs='?', help='Input CSV or NDJSON file')
  parser.add_argument('--field', default='agent.name', help='Field path (default: agent.name)')
  parser.add_argument('--value', action='append', help='Value or wildcard to keep; repeat or comma-separate')
  parser.add_argument('--output', help='Output file; defaults to <input>.filtered.<ext>')
  args = parser.parse_args()

  source = Path(args.input or input('Input CSV/NDJSON path: ').strip())
  values = args.value or [input('%s values/patterns to keep (comma-separated): ' % args.field).strip()]
  values = patterns(values)
  target = Path(args.output) if args.output else source.with_name(source.stem + '.filtered' + source.suffix)
  scanned, kept = filter_file(source, target, args.field, values)
  print('Scanned: %s | Kept: %s | Output: %s' % (f'{scanned:,}', f'{kept:,}', target))


if __name__ == '__main__':
  main()
