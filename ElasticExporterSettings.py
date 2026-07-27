import hashlib
import os
import ssl
from typing import Any
from urllib.parse import urlparse

from elasticsearch import Elasticsearch


def _load_env(path):
  if not os.path.exists(path):
    return
  with open(path, encoding='utf-8-sig') as f:
    for raw_line in f:
      line = raw_line.strip()
      if not line or line.startswith('#') or '=' not in line:
        continue
      key, value = line.split('=', 1)
      value = value.strip()
      if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
      os.environ.setdefault(key.strip(), value)


def _certificate_fingerprint(url):
  parsed = urlparse(url)
  if parsed.scheme != 'https' or not parsed.hostname:
    return None
  pem = ssl.get_server_certificate((parsed.hostname, parsed.port or 443))
  der = ssl.PEM_cert_to_DER_cert(pem)
  return hashlib.sha256(der).hexdigest()


def LoadSettings(env_file='.env'):
  _load_env(env_file)
  url = os.getenv('ELASTICSEARCH_URL')
  if not url:
    raise ValueError('ELASTICSEARCH_URL is required in .env')

  options: dict[str, Any] = {'http_compress': True}
  username = os.getenv('ELASTICSEARCH_USERNAME')
  password = os.getenv('ELASTICSEARCH_PASSWORD')
  if username or password:
    if not username or not password:
      raise ValueError('ELASTICSEARCH_USERNAME and ELASTICSEARCH_PASSWORD must both be set')
    options['basic_auth'] = (username, password)

  if urlparse(url).scheme == 'https':
    fingerprint = os.getenv('ELASTICSEARCH_CERT_FINGERPRINT')
    if not fingerprint:
      print('Trusting certificate fingerprint retrieved from %s (TOFU)' % url)
      fingerprint = _certificate_fingerprint(url)
    options['ssl_assert_fingerprint'] = fingerprint

  settings = {'es': Elasticsearch([url], **options)}
  settings['index_name'] = os.getenv('ELASTICSEARCH_INDEX')
  settings['query_file'] = os.getenv('QUERY_FILE')
  settings['backup_folder'] = os.getenv('BACKUP_FOLDER', 'exported')
  settings['output_name'] = os.getenv('OUTPUT_NAME')
  settings['output_format'] = os.getenv('OUTPUT_FORMAT', 'json').lower()
  if settings['output_format'] not in ('json', 'csv'):
    raise ValueError('OUTPUT_FORMAT must be json or csv')
  settings['TimeSeries'] = os.getenv('TIME_SERIES', 'true').lower() == 'true'
  settings['timestamp'] = os.getenv('TIMESTAMP_FIELD', '@timestamp')
  settings['local_utc_offset'] = float(os.getenv('LOCAL_UTC_OFFSET', '7'))
  settings['export_utc_offset'] = float(os.getenv('EXPORT_UTC_OFFSET', '7'))
  settings['page_size'] = int(os.getenv('PAGE_SIZE', '2000'))
  if not 100 <= settings['page_size'] <= 10000:
    raise ValueError('PAGE_SIZE must be between 100 and 10000')
  settings['request_timeout'] = int(os.getenv('REQUEST_TIMEOUT', '120'))
  settings['page_delay'] = float(os.getenv('PAGE_DELAY', '0.05'))
  settings['FileNameOther'] = 'Other'
  settings['debug'] = os.getenv('DEBUG', 'false').lower() == 'true'
  return settings


if __name__ == '__main__':
  print('This is the config and settings for Elasticsearch Exporter')
