import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest

from agentcost.api import AnalysisOptions, LogSource, analyze_sources, discover_sources
from agentcost.core import Prices
from agentcost.report import write_report


class PublicAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, model="example-openai-model"):
        path = self.folder / "synthetic.jsonl"
        event = dict(
            type="usage",
            timestamp="2026-10-01T00:00:00Z",
            agent="fixture-agent",
            run_id="synthetic-run-canary",
            session_id="synthetic-session-canary",
            response_id="synthetic-response-canary",
            model=model,
            usage={"input_uncached": 100, "output": 20},
            prompt="synthetic-prompt-canary",
            secret="synthetic-secret-canary",
        )
        path.write_text(json.dumps(event) + "\n")
        return path

    def test_api_returns_aggregates_without_writing(self):
        path = self.fixture()
        before = set(self.folder.iterdir())
        data = analyze_sources(
            [LogSource("generic", path)], AnalysisOptions("2026-10-02T00:00:00Z")
        )
        self.assertEqual(data["totals"]["tokens"], 120)
        self.assertEqual(data["price_catalog_kind"], "synthetic_example")
        self.assertEqual(set(self.folder.iterdir()), before)

    def test_raw_identifiers_and_content_not_exported(self):
        data = analyze_sources(
            [LogSource("generic", self.fixture())], AnalysisOptions("2026-10-02T00:00:00Z")
        )
        report = write_report(data, self.folder / "out")
        combined = report.read_text() + report.with_name("usage.json").read_text()
        for marker in (
            "synthetic-run-canary",
            "synthetic-session-canary",
            "synthetic-response-canary",
            "synthetic-prompt-canary",
            "synthetic-secret-canary",
        ):
            self.assertNotIn(marker, combined)

    def test_arbitrary_real_model_is_not_given_fictional_price(self):
        data = analyze_sources(
            [LogSource("generic", self.fixture("unlisted-provider-model"))],
            AnalysisOptions("2026-10-02T00:00:00Z"),
        )
        self.assertEqual(data["totals"]["tokens"], 120)
        self.assertIsNone(data["totals"]["total_api_equivalent_usd"])

    def test_custom_price_catalog_is_separate_from_fixture_catalog(self):
        catalog = json.loads(
            pathlib.Path(__file__).resolve().parents[1].joinpath("examples/prices.json").read_text()
        )
        catalog["synthetic_example"] = False
        path = self.folder / "prices.json"
        path.write_text(json.dumps(catalog))
        data = analyze_sources(
            [LogSource("generic", self.fixture())],
            AnalysisOptions("2026-10-02T00:00:00Z"),
            Prices(path),
        )
        self.assertEqual(data["price_catalog_kind"], "user_supplied")

    def test_api_validates_boundaries(self):
        for option in (
            AnalysisOptions("2026-10-02T00:00:00Z", checkpoint="0"),
            AnalysisOptions("2026-10-02T00:00:00Z", budget_tokens=True),
            AnalysisOptions("2026-10-02T00:00:00Z", since="2026-10-03", until="2026-10-01"),
        ):
            with self.assertRaises(ValueError):
                analyze_sources([], option)
        with self.assertRaises(ValueError):
            analyze_sources(
                [LogSource("unsupported", self.fixture())], AnalysisOptions("2026-10-02T00:00:00Z")
            )

    def test_generic_discovery_does_not_add_local_agent_roots(self):
        path = self.fixture()
        sources = discover_sources(agent="generic", inputs=[path])
        self.assertEqual(sources, [LogSource("generic", path)])


class ReleaseChecks(unittest.TestCase):
    def checker(self):
        script = pathlib.Path(__file__).resolve().parents[1] / "scripts/check_release.py"
        spec = importlib.util.spec_from_file_location("release_check", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_release_scan_reports_rules_without_secret_values(self):
        module = self.checker()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            secret = "gh" + "p_" + "A" * 36
            (root / "README.md").write_text(secret)
            result = module.check_tree(root)
            self.assertEqual(result["status"], "failed")
            self.assertNotIn(secret, json.dumps(result))
            (root / "README.md").write_text("synthetic example")
            (root / "unexpected.txt").write_text("not release material")
            self.assertEqual(
                module.check_tree(root)["findings"][0]["rule"], "not_release_allowlisted"
            )

    def test_staged_secret_is_detected_even_if_worktree_is_clean(self):
        module = self.checker()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            secret = "gh" + "p_" + "B" * 36
            (root / "README.md").write_text(secret)
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            (root / "README.md").write_text("synthetic public content")
            result = module.check_tree(root)
            self.assertEqual(result["status"], "failed")
            self.assertIn("staged_api_credential", [item["rule"] for item in result["findings"]])
            self.assertNotIn(secret, json.dumps(result))

    def test_force_added_private_report_is_not_release_allowed(self):
        module = self.checker()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "reports").mkdir()
            (root / "reports/private.json").write_text("{}")
            subprocess.run(["git", "add", "reports/private.json"], cwd=root, check=True)
            result = module.check_tree(root)
            self.assertIn(
                "staged_not_release_allowlisted", [item["rule"] for item in result["findings"]]
            )

    def test_research_datasets_are_rejected_if_staged(self):
        module = self.checker()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            study = root / module.RESEARCH_ROOT
            (study / "data").mkdir(parents=True)
            (study / "data/private.csv").write_text("synthetic local records")
            self.assertEqual(module.check_tree(root)["status"], "passed")
            subprocess.run(
                ["git", "add", module.RESEARCH_ROOT + "/data/private.csv"],
                cwd=root,
                check=True,
            )
            result = module.check_tree(root)
            self.assertIn(
                "staged_not_release_allowlisted", [item["rule"] for item in result["findings"]]
            )

    def test_research_allowlist_still_scans_private_paths(self):
        module = self.checker()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            study = root / module.RESEARCH_ROOT
            study.mkdir(parents=True)
            (study / "README.md").write_text("/home/" + "fictional-person/fixture")
            result = module.check_tree(root)
            self.assertIn(
                "personal_absolute_path", [item["rule"] for item in result["findings"]]
            )


if __name__ == "__main__":
    unittest.main()
