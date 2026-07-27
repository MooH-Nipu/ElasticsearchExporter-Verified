import csv
import json
import tempfile
import unittest
from pathlib import Path

import FilterExport


class FilterExportTests(unittest.TestCase):
    def test_parses_repeated_and_comma_separated_patterns(self):
        self.assertEqual(
            ["TESTAGENT-*", "SPECIAL", "BACKUP-?"],
            FilterExport.patterns(["TESTAGENT-*, SPECIAL", "BACKUP-?"]),
        )

    def test_filters_csv_by_dotted_field(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.csv"
            target = Path(folder) / "filtered.csv"
            with source.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=["agent.name", "message"])
                writer.writeheader()
                writer.writerows([
                    {"agent.name": "wanted", "message": "keep"},
                    {"agent.name": "other", "message": "drop"},
                ])

            scanned, kept = FilterExport.filter_file(source, target, "agent.name", "wanted")

            self.assertEqual((2, 1), (scanned, kept))
            with target.open(newline="", encoding="utf-8") as result:
                self.assertEqual([{"agent.name": "wanted", "message": "keep"}], list(csv.DictReader(result)))

    def test_filters_csv_by_multiple_exact_and_wildcard_values(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.csv"
            target = Path(folder) / "filtered.csv"
            with source.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=["agent.name"])
                writer.writeheader()
                writer.writerows([
                    {"agent.name": "TESTAGENT-01"},
                    {"agent.name": "TESTAGENT-02"},
                    {"agent.name": "SPECIAL"},
                    {"agent.name": "OTHER"},
                ])

            scanned, kept = FilterExport.filter_file(
                source, target, "agent.name", ["TESTAGENT-*", "SPECIAL"]
            )

            self.assertEqual((4, 3), (scanned, kept))
            with target.open(newline="", encoding="utf-8") as result:
                self.assertEqual(
                    ["TESTAGENT-01", "TESTAGENT-02", "SPECIAL"],
                    [row["agent.name"] for row in csv.DictReader(result)],
                )

    def test_filters_ndjson_by_nested_source_field(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.ndjson"
            target = Path(folder) / "filtered.ndjson"
            rows = [
                {"_source": {"agent": {"name": "wanted"}, "message": "keep"}},
                {"_source": {"agent": {"name": "other"}, "message": "drop"}},
            ]
            source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

            scanned, kept = FilterExport.filter_file(source, target, "agent.name", "wanted")

            self.assertEqual((2, 1), (scanned, kept))
            self.assertEqual(rows[0], json.loads(target.read_text(encoding="utf-8")))

    def test_refuses_to_overwrite_input(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.csv"
            source.write_text("agent.name\nwanted\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                FilterExport.filter_file(source, source, "agent.name", "wanted")


if __name__ == "__main__":
    unittest.main()
