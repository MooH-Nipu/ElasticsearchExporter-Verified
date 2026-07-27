import io
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import ElasticExporter
import ElasticExporterCLI
import ElasticExporterSettings


class QueryConfigTests(unittest.TestCase):
    def test_field_filter_prompt_is_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(ElasticExporterCLI.field_filter_enabled())

    def test_field_filter_prompt_can_be_enabled(self):
        with patch.dict(os.environ, {"PROMPT_FIELD_FILTER": "true"}, clear=True):
            self.assertTrue(ElasticExporterCLI.field_filter_enabled())

    def test_wib_time_converts_to_utc(self):
        self.assertEqual(
            "2026-07-01T07:30:15Z",
            ElasticExporterCLI.to_utc("2026-07-01 14:30:15", 7),
        )

    def test_explicit_timezone_is_respected(self):
        self.assertEqual(
            "2026-07-01T07:30:15Z",
            ElasticExporterCLI.to_utc("2026-07-01T09:30:15+02:00", 7),
        )

    def test_query_time_range_builds_range_filter(self):
        result = ElasticExporterCLI.add_time_range(
            {"bool": {"filter": [{"match_all": {}}]}},
            "2026-07-01T00:00:00+00:00",
            "2026-07-02T23:59:59+00:00",
        )

        self.assertEqual(
            {"range": {"@timestamp": {"gte": "2026-07-01T00:00:00Z", "lte": "2026-07-02T23:59:59Z"}}},
            result["bool"]["filter"][-1],
        )

    def test_query_time_range_wraps_non_bool_query(self):
        result = ElasticExporterCLI.add_time_range(
            {"term": {"event.kind": "alert"}},
            "2026-07-01T00:00:00+00:00",
            "2026-07-02T23:59:59+00:00",
        )

        self.assertEqual({"term": {"event.kind": "alert"}}, result["bool"]["must"][0])
        self.assertEqual("2026-07-01T00:00:00Z", result["bool"]["filter"][0]["range"]["@timestamp"]["gte"])

    def test_terminal_time_replaces_existing_timestamp_range(self):
        query = {"bool": {"filter": [
            {"range": {"@timestamp": {"gte": "old-start", "lte": "old-end"}}},
            {"term": {"agent.name.keyword": "xxx"}},
        ]}}
        result = ElasticExporterCLI.add_time_range(
            query,
            "2026-07-01 00:00:00",
            "2026-07-01 23:59:59",
        )

        filters = result["bool"]["filter"]
        self.assertEqual(2, len(filters))
        self.assertEqual({"term": {"agent.name.keyword": "xxx"}}, filters[0])
        self.assertEqual("2026-06-30T17:00:00Z", filters[1]["range"]["@timestamp"]["gte"])
        self.assertEqual("old-start", query["bool"]["filter"][0]["range"]["@timestamp"]["gte"])

    def test_prompt_time_range_uses_configured_local_offset(self):
        with patch("builtins.input", side_effect=["2026-07-01 12:00:00", "2026-07-01 13:00:00"]):
            result = ElasticExporterCLI.prompt_time_range(
                {"bool": {"filter": []}},
                "@timestamp",
                utc_offset=5,
            )

        time_range = result["bool"]["filter"][0]["range"]["@timestamp"]
        self.assertEqual("2026-07-01T07:00:00Z", time_range["gte"])
        self.assertEqual("2026-07-01T08:00:00Z", time_range["lte"])

    def test_searchable_fields_uses_field_caps(self):
        es = Mock()
        es.field_caps.return_value = {"fields": {
            "agent.name": {"text": {"searchable": True}},
            "agent.name.keyword": {"keyword": {"searchable": True}},
            "host": {"object": {"searchable": False}},
            "_id": {"_id": {"searchable": True}},
        }}

        fields = ElasticExporterCLI.searchable_fields(es, "hids-*")

        self.assertEqual(["agent.name", "agent.name.keyword"], fields)
        es.field_caps.assert_called_once_with(index="hids-*", fields=["*"])

    def test_field_caps_failure_allows_manual_field(self):
        es = Mock()
        es.field_caps.side_effect = Exception("field caps unavailable")
        answers = iter(["y", "agent.name.keyword", "agent-01"])

        with patch("builtins.input", side_effect=lambda _="": next(answers)):
            result = ElasticExporterCLI.prompt_field_filter(
                es,
                "hids-*",
                {"bool": {"filter": [{"match_all": {}}]}},
            )

        self.assertEqual(
            {"bool": {"filter": [{"term": {"agent.name.keyword": "agent-01"}}]}},
            result,
        )

    def test_exact_field_filter_prefers_keyword_subfield(self):
        query = {"bool": {"filter": [{"match_all": {}}]}}

        result = ElasticExporterCLI.add_field_filter(
            query,
            "agent.name",
            "agent-01",
            ["agent.name", "agent.name.keyword"],
        )

        self.assertEqual(
            [{"term": {"agent.name.keyword": "agent-01"}}],
            result["bool"]["filter"],
        )

    def test_index_choices_include_wildcard_groups(self):
        indexes = ["hids-2026.07.01", "hids-2026.07.02", "logs-single"]

        self.assertEqual(
            ["hids-*", "logs-single"],
            ElasticExporterCLI.index_choices(indexes),
        )

    def test_select_index_returns_selected_group(self):
        es = Mock()
        es.cat.indices.return_value = [
            {"index": "hids-2026.07.01"},
            {"index": "hids-2026.07.02"},
            {"index": "logs-single"},
        ]
        with patch("builtins.input", return_value="1"):
            self.assertEqual("hids-*", ElasticExporterCLI.select_index(es))


class SettingsTests(unittest.TestCase):
    def test_loads_dotenv_and_auto_fetches_https_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            env_file = os.path.join(folder, ".env")
            with open(env_file, "w", encoding="utf-8") as output:
                output.write(
                    "ELASTICSEARCH_URL=https://elastic.local:9200\n"
                    "ELASTICSEARCH_USERNAME=elastic\n"
                    "ELASTICSEARCH_PASSWORD='secret value'\n"
                    "ELASTICSEARCH_INDEX=logs-test\n"
                    "BACKUP_FOLDER=exports\n"
                )

            with patch.dict(os.environ, {}, clear=True), \
                 patch.object(ElasticExporterSettings.ssl, "get_server_certificate", return_value="CERT"), \
                 patch.object(ElasticExporterSettings.ssl, "PEM_cert_to_DER_cert", return_value=b"certificate"), \
                 patch.object(ElasticExporterSettings, "Elasticsearch") as client:
                settings = ElasticExporterSettings.LoadSettings(env_file)

            expected = ElasticExporterSettings.hashlib.sha256(b"certificate").hexdigest()
            client.assert_called_once_with(
                ["https://elastic.local:9200"],
                basic_auth=("elastic", "secret value"),
                ssl_assert_fingerprint=expected,
                http_compress=True,
            )
            self.assertEqual("logs-test", settings["index_name"])
            self.assertEqual("exports", settings["backup_folder"])

    def test_loads_csv_output_format(self):
        with patch.dict(os.environ, {"ELASTICSEARCH_URL": "http://elastic.local:9200", "OUTPUT_FORMAT": "csv"}, clear=True), \
             patch.object(ElasticExporterSettings, "Elasticsearch"):
            settings = ElasticExporterSettings.LoadSettings("missing.env")

        self.assertEqual("csv", settings["output_format"])

    def test_explicit_fingerprint_skips_certificate_download(self):
        env = {
            "ELASTICSEARCH_URL": "https://elastic.local:9200",
            "ELASTICSEARCH_CERT_FINGERPRINT": "AA:BB",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch.object(ElasticExporterSettings.ssl, "get_server_certificate") as download, \
             patch.object(ElasticExporterSettings, "Elasticsearch") as client:
            ElasticExporterSettings.LoadSettings("missing.env")

        download.assert_not_called()
        self.assertEqual("AA:BB", client.call_args.kwargs["ssl_assert_fingerprint"])


class ProcessIndexTests(unittest.TestCase):
    def test_progress_line_reports_percentage(self):
        self.assertEqual(
            "Progress: [#####-----] 50.0% | 5,000/10,000 | ETA 00:10",
            ElasticExporter.progress_line(5000, 10000, elapsed=10, width=10),
        )

    def test_progress_redraw_uses_one_terminal_line(self):
        output = io.StringIO()
        ElasticExporter.write_progress("first long status", stream=output)
        ElasticExporter.write_progress("done", final=True, stream=output)

        self.assertEqual("\r\x1b[2Kfirst long status\r\x1b[2Kdone\n", output.getvalue())
        self.assertNotIn("\\r", output.getvalue())

    def test_rejecting_confirmation_aborts_before_export(self):
        with tempfile.TemporaryDirectory() as backup_folder:
            es = Mock()
            es.indices.exists.return_value = True
            es.count.return_value = {"count": 1234}
            settings = {
                "es": es,
                "index_name": "logs-test",
                "backup_folder": backup_folder,
                "query_filter": {"match_all": {}},
                "debug": False,
                "NoGroup": False,
            }

            with patch("builtins.input", return_value="n"), \
                 patch.object(ElasticExporter, "ExportIndex") as export, \
                 patch("builtins.print") as output:
                ElasticExporter.ProcessIndex(settings)

            export.assert_not_called()
            self.assertIn("Export cancelled", [str(call.args[0]) for call in output.call_args_list if call.args])

    def test_output_field_filter_keeps_only_matching_agent(self):
        results = ElasticExporter.filter_hits(
            [{"_source": {"agent": {"name": "wanted"}}}, {"_source": {"agent": {"name": "other"}}}],
            "agent.name",
            "wanted",
        )

        self.assertEqual(1, len(results))
        self.assertEqual("wanted", results[0]["_source"]["agent"]["name"])

    def test_export_timestamp_converts_to_utc_plus_7(self):
        item = {"_source": {"@timestamp": "2026-07-01T07:30:15.123Z", "message": "hello"}}

        converted = ElasticExporter.convert_timestamps(item, "@timestamp", 7)

        self.assertEqual("2026-07-01T14:30:15.123+07:00", converted["_source"]["@timestamp"])
        self.assertEqual("2026-07-01T07:30:15.123Z", item["_source"]["@timestamp"])

    def test_csv_conversion_keeps_only_csv_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "Other.ndjson")
            with open(source, "w", encoding="utf-8") as output:
                output.write('{"_source":{"message":"hello"}}\n')

            result = ElasticExporter.convertCSV(source, remove_source=True)

            self.assertEqual(os.path.join(folder, "Other.csv"), result)
            self.assertTrue(os.path.exists(result))
            self.assertFalse(os.path.exists(source))
            with open(result, newline="", encoding="utf-8") as exported:
                row = next(__import__("csv").DictReader(exported))
            self.assertEqual("hello", row["message"])

    def test_csv_conversion_includes_fields_from_later_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "Other.ndjson")
            with open(source, "w", encoding="utf-8") as output:
                output.write('{"_source":{"message":"first"}}\n')
                output.write('{"_source":{"message":"second","agent":{"name":"agent-01"}}}\n')

            result = ElasticExporter.convertCSV(source)

            with open(result, newline="", encoding="utf-8") as exported:
                rows = list(__import__("csv").DictReader(exported))
            self.assertEqual(["agent.name", "message"], list(rows[0]))
            self.assertEqual("", rows[0]["agent.name"])
            self.assertEqual("agent-01", rows[1]["agent.name"])

    def test_make_folders_creates_missing_backup_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            fullpath = os.path.join(folder, "missing", "logs-test")
            ElasticExporter.MakeFolders({"backup_folder": os.path.dirname(fullpath), "fullpath": fullpath})
            self.assertTrue(os.path.isdir(fullpath))

    def test_counts_query_before_export_and_verifies_exported_count(self):
        with tempfile.TemporaryDirectory() as backup_folder:
            es = Mock()
            es.indices.exists.return_value = True
            es.count.return_value = {"count": 2}
            settings = {
                "es": es,
                "index_name": "logs-test",
                "backup_folder": backup_folder,
                "query_filter": {"term": {"event.kind": "alert"}},
                "debug": False,
                "NoGroup": False,
            }

            def fake_export(_es, configured_settings, _time_series, AllItems=True):
                os.makedirs(configured_settings["fullpath"], exist_ok=True)
                with open(os.path.join(configured_settings["fullpath"], "Other.checksums"), "w") as output:
                    json.dump({"Other.ndjson": {"events": 2}}, output)

            with patch("builtins.input", return_value="yes"), \
                 patch.object(ElasticExporter, "ExportIndex", side_effect=fake_export), \
                 patch("builtins.print") as output:
                ElasticExporter.ProcessIndex(settings)

            es.count.assert_called_once_with(index="logs-test", query=settings["query_filter"])
            messages = [str(call.args[0]) for call in output.call_args_list if call.args]
            self.assertIn("Query matched 2 documents", messages)
            self.assertIn("VERIFIED: exported 2 of 2 matched documents", messages)


if __name__ == "__main__":
    unittest.main()
