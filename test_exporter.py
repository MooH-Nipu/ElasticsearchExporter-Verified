import io
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from elasticsearch import BadRequestError

import ElasticExporter
import ElasticExporterCLI
import ElasticExporterSettings


class QueryConfigTests(unittest.TestCase):
    def test_parse_filter_specs_preserves_equals_in_value(self):
        self.assertEqual(
            [("agent.name", "HOST-*"), ("message", "a=b")],
            ElasticExporterCLI.parse_filter_specs(
                ["agent.name=HOST-*", "message=a=b"]
            ),
        )

    def test_parse_filter_specs_rejects_missing_field_or_value(self):
        with self.assertRaisesRegex(ValueError, "field=value"):
            ElasticExporterCLI.parse_filter_specs(["agent.name"])
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            ElasticExporterCLI.parse_filter_specs(["=wanted"])
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            ElasticExporterCLI.parse_filter_specs(["agent.name="])

    def test_add_field_filters_builds_and_query_without_match_all(self):
        query = {
            "bool": {
                "filter": [
                    {"match_all": {}},
                    {"range": {"@timestamp": {"gte": "start", "lte": "end"}}},
                ]
            }
        }

        result = ElasticExporterCLI.add_field_filters(
            query,
            [("agent.name", "HOST-*")],
            logic="and",
            fields=["agent.name", "agent.name.keyword"],
        )

        self.assertEqual(
            [
                {"range": {"@timestamp": {"gte": "start", "lte": "end"}}},
                {"wildcard": {"agent.name.keyword": "HOST-*"}},
            ],
            result["bool"]["filter"],
        )
        self.assertEqual({"match_all": {}}, query["bool"]["filter"][0])

    def test_add_field_filters_builds_or_group(self):
        result = ElasticExporterCLI.add_field_filters(
            {"match_all": {}},
            [("agent.name", "HOST-01"), ("event.kind", "alert")],
            logic="or",
            fields=["agent.name", "agent.name.keyword", "event.kind"],
        )

        self.assertEqual(
            {
                "bool": {
                    "filter": [
                        {
                            "bool": {
                                "should": [
                                    {"term": {"agent.name.keyword": "HOST-01"}},
                                    {"match_phrase": {"event.kind": "alert"}},
                                ],
                                "minimum_should_match": 1,
                            }
                        }
                    ]
                }
            },
            result,
        )

    def test_add_field_filters_rejects_invalid_logic(self):
        with self.assertRaisesRegex(ValueError, "and or"):
            ElasticExporterCLI.add_field_filters(
                {"match_all": {}}, [("event.kind", "alert")], logic="xor"
            )

    def test_add_exclude_filters_builds_must_not_clause(self):
        query = {
            "bool": {
                "filter": [{"match_all": {}}],
                "must_not": [],
            }
        }

        result = ElasticExporterCLI.add_exclude_filters(
            query,
            [("decoder.name", "windows_eventchannel")],
            fields=["decoder.name", "decoder.name.keyword"],
        )

        self.assertEqual(
            [{"term": {"decoder.name.keyword": "windows_eventchannel"}}],
            result["bool"]["must_not"],
        )
        self.assertEqual([], result["bool"]["filter"])
        self.assertEqual({"match_all": {}}, query["bool"]["filter"][0])

    def test_parse_export_fields_supports_all_and_ordered_paths(self):
        self.assertEqual("all", ElasticExporterCLI.parse_export_fields("all"))
        self.assertEqual(
            ["@timestamp", "agent.name", "message"],
            ElasticExporterCLI.parse_export_fields(
                "@timestamp, agent.name, message"
            ),
        )

    def test_parse_export_fields_rejects_empty_items_and_mixed_all(self):
        with self.assertRaisesRegex(ValueError, "field list"):
            ElasticExporterCLI.parse_export_fields("")
        with self.assertRaisesRegex(ValueError, "all"):
            ElasticExporterCLI.parse_export_fields("all,message")

    def test_cli_time_options_require_both_dates(self):
        settings = {
            "query_filter": {"bool": {"filter": []}},
            "timestamp": "@timestamp",
            "local_utc_offset": 7,
        }
        with self.assertRaisesRegex(ValueError, "Both start and end"):
            ElasticExporterCLI.apply_cli_query_options(
                settings, {"--start": "2026-07-01 00:00:00", "--end": None}
            )

    def test_cli_time_options_skip_prompt_when_dates_are_given(self):
        settings = {
            "query_filter": {"bool": {"filter": []}},
            "timestamp": "@timestamp",
            "local_utc_offset": 7,
        }
        options = {"--start": "2026-07-01 00:00:00", "--end": "2026-07-01 01:00:00"}

        with patch.object(ElasticExporterCLI, "prompt_time_range") as prompt:
            ElasticExporterCLI.apply_cli_query_options(settings, options)

        prompt.assert_not_called()
        self.assertEqual(
            "2026-06-30T17:00:00Z",
            settings["query_filter"]["bool"]["filter"][0]["range"]["@timestamp"]["gte"],
        )

    def test_cli_output_name_overrides_existing_setting(self):
        settings = {"output_name": "from-env"}
        ElasticExporterCLI.apply_cli_output_options(
            settings, {"--output-name": "from-cli", "--fields": "agent.name,message"}
        )
        self.assertEqual("from-cli", settings["output_name"])
        self.assertEqual(["agent.name", "message"], settings["export_fields"])

    def test_docopt_accepts_all_non_interactive_cli_options(self):
        options = ElasticExporterCLI.docopt(
            ElasticExporterCLI.__doc__,
            argv=[
                "--start=2026-07-01 00:00:00",
                "--end=2026-07-01 23:59:59",
                "--output-name=incident",
                "--filter=agent.name=HOST-*",
                "--filter=event.kind=alert",
                "--filter-logic=or",
                "--exclude-filter=decoder.name=windows_eventchannel",
                "--fields=@timestamp,agent.name",
            ],
        )
        self.assertEqual(
            ["agent.name=HOST-*", "event.kind=alert"],
            options["--filter"],
        )
        self.assertEqual("or", options["--filter-logic"])
        self.assertEqual(
            ["decoder.name=windows_eventchannel"],
            options["--exclude-filter"],
        )
        self.assertEqual("@timestamp,agent.name", options["--fields"])

    def test_cli_exclude_filter_adds_must_not_clause(self):
        es = Mock()
        es.field_caps.return_value = {"fields": {
            "decoder.name": {"text": {"searchable": True}},
            "decoder.name.keyword": {"keyword": {"searchable": True}},
        }}
        settings = {
            "es": es,
            "index_name": "hids-*",
            "query_filter": {"bool": {"filter": [{"match_all": {}}]}},
            "timestamp": "@timestamp",
            "local_utc_offset": 7,
        }
        options = {
            "--filter": [],
            "--exclude-filter": ["decoder.name=windows_eventchannel"],
            "--filter-logic": "and",
            "--start": None,
            "--end": None,
        }

        with patch.dict(os.environ, {"PROMPT_TIME_RANGE": "false"}, clear=True):
            ElasticExporterCLI.apply_cli_query_options(settings, options)

        self.assertEqual(
            [{"term": {"decoder.name.keyword": "windows_eventchannel"}}],
            settings["query_filter"]["bool"]["must_not"],
        )

    def test_cli_filter_skips_interactive_field_prompt(self):
        es = Mock()
        es.field_caps.return_value = {"fields": {
            "agent.name": {"text": {"searchable": True}},
            "agent.name.keyword": {"keyword": {"searchable": True}},
        }}
        settings = {
            "es": es,
            "index_name": "logs-*",
            "query_filter": {"bool": {"filter": [{"match_all": {}}]}},
            "timestamp": "@timestamp",
            "local_utc_offset": 7,
        }
        options = {
            "--filter": ["agent.name=HOST-01"],
            "--filter-logic": "and",
            "--start": None,
            "--end": None,
        }

        with patch.object(ElasticExporterCLI, "prompt_field_filter") as prompt, \
             patch.dict(os.environ, {"PROMPT_TIME_RANGE": "false"}, clear=True):
            ElasticExporterCLI.apply_cli_query_options(settings, options)

        prompt.assert_not_called()

    def test_cli_filter_logic_is_validated_without_filters(self):
        settings = {
            "query_filter": {"bool": {"filter": []}},
            "timestamp": "@timestamp",
            "local_utc_offset": 7,
        }
        with patch.dict(os.environ, {"PROMPT_TIME_RANGE": "false"}, clear=True), \
             self.assertRaisesRegex(ValueError, "and or or"):
            ElasticExporterCLI.apply_cli_query_options(
                settings,
                {"--filter": [], "--filter-logic": "xor", "--start": None, "--end": None},
            )

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

    def test_field_caps_falls_back_to_query_parameter_for_old_server(self):
        es = Mock()
        es.field_caps.side_effect = BadRequestError(
            "specified fields can't be null or empty",
            Mock(),
            {"error": {"reason": "specified fields can't be null or empty"}},
        )
        response = {"fields": {
            "agent.name": {"text": {"searchable": True}},
            "agent.name.keyword": {"keyword": {"searchable": True}},
        }}
        es.perform_request.return_value = response

        capabilities = ElasticExporterCLI.searchable_field_capabilities(es, "hids-*")

        self.assertEqual(response["fields"], capabilities)
        es.perform_request.assert_called_once_with(
            "GET",
            "/hids-*/_field_caps",
            params={"fields": "*"},
        )

    def test_field_caps_does_not_fallback_for_unrelated_bad_request(self):
        es = Mock()
        error = BadRequestError(
            "index is unavailable",
            Mock(),
            {"error": {"reason": "index is unavailable"}},
        )
        es.field_caps.side_effect = error

        with self.assertRaises(BadRequestError):
            ElasticExporterCLI.searchable_field_capabilities(es, "hids-*")

        es.perform_request.assert_not_called()

    def test_filter_uses_keyword_after_field_caps_fallback_without_warning(self):
        es = Mock()
        es.field_caps.side_effect = BadRequestError(
            "specified fields can't be null or empty",
            Mock(),
            {"error": {"reason": "specified fields can't be null or empty"}},
        )
        es.perform_request.return_value = {"fields": {
            "decoder.name": {"text": {"searchable": True}},
            "decoder.name.keyword": {"keyword": {"searchable": True}},
        }}
        settings = {
            "es": es,
            "index_name": "hids-*",
            "query_filter": {"bool": {"filter": [{"match_all": {}}]}},
            "timestamp": "@timestamp",
            "local_utc_offset": 7,
        }
        options = {
            "--filter": ["decoder.name=windows_eventchannel"],
            "--filter-logic": "and",
            "--start": None,
            "--end": None,
        }

        with patch.dict(os.environ, {"PROMPT_TIME_RANGE": "false"}, clear=True), \
             patch("builtins.print") as output:
            ElasticExporterCLI.apply_cli_query_options(settings, options)

        self.assertEqual(
            {"term": {"decoder.name.keyword": "windows_eventchannel"}},
            settings["query_filter"]["bool"]["filter"][0],
        )
        messages = [str(call.args[0]) for call in output.call_args_list if call.args]
        self.assertFalse(any("Field discovery failed" in message for message in messages))

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

    def test_write_results_selected_fields_keep_nested_source_without_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = {
                "fullpath": folder,
                "export_fields": ["agent.name", "message"],
                "timestamp": "@timestamp",
                "export_utc_offset": 7,
            }
            results = {
                "timed_out": False,
                "_shards": {"failed": 0},
                "hits": {
                    "total": 1,
                    "hits": [{
                        "_index": "logs-2026.07.01",
                        "_id": "abc",
                        "_score": None,
                        "sort": [1],
                        "_source": {
                            "agent": {"name": "HOST-01"},
                            "message": "keep",
                            "secret": "drop",
                        },
                    }],
                },
            }

            result = ElasticExporter.WriteResults(settings, "Other", 1, results)

            self.assertEqual([1], result["sort"])
            with open(os.path.join(folder, "Other.ndjson"), encoding="utf-8") as exported:
                item = json.loads(exported.readline())
            self.assertEqual(
                {"agent": {"name": "HOST-01"}, "message": "keep"},
                item,
            )

    def test_write_results_all_fields_keep_source_without_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = {"fullpath": folder, "export_fields": "all"}
            results = {
                "timed_out": False,
                "_shards": {"failed": 0},
                "hits": {
                    "total": 1,
                    "hits": [{
                        "_index": "logs-2026.07.01",
                        "_id": "abc",
                        "_source": {"message": "keep", "secret": "also-keep"},
                    }],
                },
            }

            ElasticExporter.WriteResults(settings, "Other", 1, results)

            with open(os.path.join(folder, "Other.ndjson"), encoding="utf-8") as exported:
                self.assertEqual(
                    {"message": "keep", "secret": "also-keep"},
                    json.loads(exported.readline()),
                )

    def test_write_results_without_fields_keeps_legacy_hit_shape(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = {"fullpath": folder}
            hit = {"_index": "logs", "_id": "abc", "_source": {"message": "keep"}}
            results = {
                "timed_out": False,
                "_shards": {"failed": 0},
                "hits": {"total": 1, "hits": [hit]},
            }

            ElasticExporter.WriteResults(settings, "Other", 1, results)

            with open(os.path.join(folder, "Other.ndjson"), encoding="utf-8") as exported:
                self.assertEqual(hit, json.loads(exported.readline()))

    def test_write_results_uses_utf8_for_unicode_source_when_default_is_cp1252(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = {"fullpath": folder}
            results = {
                "timed_out": False,
                "_shards": {"failed": 0},
                "hits": {
                    "total": 1,
                    "hits": [{"_source": {"message": "left\u200e-to-right"}}],
                },
            }

            def cp1252_default_open(file, mode="r", *args, **kwargs):
                if "b" not in mode and "encoding" not in kwargs:
                    kwargs["encoding"] = "cp1252"
                return io.open(file, mode, *args, **kwargs)

            with patch("builtins.open", side_effect=cp1252_default_open):
                ElasticExporter.WriteResults(settings, "Other", 1, results)

            with io.open(os.path.join(folder, "Other.ndjson"), encoding="utf-8") as exported:
                item = json.loads(exported.readline())
            self.assertEqual("left\u200e-to-right", item["_source"]["message"])

    def test_convert_csv_selected_fields_preserve_order_and_missing_values(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "Other.ndjson")
            with open(source, "w", encoding="utf-8") as output:
                output.write(json.dumps({"agent": {"name": "one"}, "message": "first"}) + "\n")
                output.write(json.dumps({"agent": {"name": "two"}}) + "\n")

            result = ElasticExporter.convertCSV(
                source,
                event_keys=["agent.name", "message"],
            )

            with open(result, newline="", encoding="utf-8") as exported:
                reader = __import__("csv").DictReader(exported)
                rows = list(reader)
            self.assertEqual(["agent.name", "message"], reader.fieldnames)
            self.assertEqual("", rows[1]["message"])

    def test_convert_csv_reads_utf8_ndjson_when_default_is_cp1252(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "Other.ndjson")
            with io.open(source, "w", encoding="utf-8") as output:
                output.write(json.dumps({"_source": {"message": "left\u200e-to-right"}}, ensure_ascii=False) + "\n")

            def cp1252_default_open(file, mode="r", *args, **kwargs):
                if "b" not in mode and "encoding" not in kwargs:
                    kwargs["encoding"] = "cp1252"
                return io.open(file, mode, *args, **kwargs)

            with patch("builtins.open", side_effect=cp1252_default_open):
                result = ElasticExporter.convertCSV(source)

            with io.open(result, newline="", encoding="utf-8") as exported:
                row = next(__import__("csv").DictReader(exported))
            self.assertEqual("left\u200e-to-right", row["message"])

    def test_source_search_kwargs_include_selected_fields(self):
        self.assertEqual(
            {"source_includes": ["agent.name", "message"]},
            ElasticExporter.source_search_kwargs({
                "export_fields": ["agent.name", "message"],
            }),
        )
        self.assertEqual(
            {},
            ElasticExporter.source_search_kwargs({"export_fields": "all"}),
        )

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
