"""Dependency-free regressions for CI planning and its required result gate."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import ci


GROUPS = ("python", "react", "desktop")


def flags(*groups):
    return {group: "true" if group in groups else "false" for group in GROUPS}


def needs_for(*groups):
    outputs = flags(*groups)
    return {
        "plan": {"result": "success", "outputs": outputs},
        **{
            group: {"result": "success" if group in groups else "skipped"}
            for group in GROUPS
        },
    }


class ClassificationTests(unittest.TestCase):
    def test_explanatory_documents_and_document_media_need_no_business_checks(self):
        paths = [
            "README.md", "CONTRIBUTING.md", "app/README.md",
            ".github/pull_request_template.md", "docs/source-boundaries.md",
            "docs/brand/logo.svg", "docs/showcase/demo.mp4",
            "docs/brand/font.woff2", "docs/brand/licenses/NotoSansSC-OFL.txt",
            "LICENSE",
        ]
        self.assertEqual(ci.classify(paths), flags())
        self.assertEqual(ci.classify([]), flags())

    def test_source_boundaries_select_the_expected_groups(self):
        cases = {
            "app/importer.py": flags("python"),
            "static/graph-view.js": flags("python"),
            "static/vendor/G6-LICENSE": flags("python"),
            "tests/test_cases.py": flags("python"),
            "benchmarks/runner.py": flags("python"),
            "run.py": flags("python"),
            "run.bat": flags("python"),
            "scripts/cleanup_imports.py": flags("python"),
            "frontend/src/App.tsx": flags("react"),
            "frontend/package-lock.json": flags("react"),
            "contracts/openapi-v1.json": flags("python", "react"),
            "app/api/models.py": flags("python", "react"),
            "scripts/export_api_contract.py": flags("python", "react"),
            "desktop/main.cjs": flags("python", "desktop"),
            "desktop/package-lock.json": flags("python", "desktop"),
            "desktop_backend.py": flags("python", "desktop"),
            "desktop_synthetic.py": flags("python", "desktop"),
            "requirements-desktop.lock": flags("python", "desktop"),
            "scripts/build-windows.ps1": flags("python", "desktop"),
            "scripts/build_desktop_backend.py": flags("python", "desktop"),
            "scripts/test-windows-installer.ps1": flags("python", "desktop"),
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(ci.classify([path]), expected)
        self.assertEqual(ci.classify(["app/db.py", "frontend/src/App.tsx"]), flags("python", "react"))

    def test_governance_shared_inputs_and_unknown_paths_fail_closed(self):
        paths = [
            "AGENTS.md", "CLAUDE.md", "docs/AGENTS.md", "frontend/CLAUDE.md",
            "requirements.txt", "requirements-dev.in", "requirements-dev.lock",
            "pyproject.toml", ".python-version", "scripts/test.py", "scripts/ci.py",
            ".github/workflows/ci.yml", ".gitignore", "new-module/logic.py",
            "docs/examples/verify.py", "docs/examples/result.json",
            "docs/examples/flows.csv", "docs/examples/flows.xlsx",
            "docs/brand/generate.py", "docs/brand/render.mjs", "docs/unknown.txt",
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(ci.classify([path]), flags(*GROUPS))


class GitDiffTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.check_output([
            "git", "-c", "commit.gpgsign=false", "-c",
            f"core.hooksPath={root / 'no-hooks'}", *args,
        ], cwd=root).decode().strip()

    def test_renames_deletions_and_unusual_names_keep_both_sides(self):
        with tempfile.TemporaryDirectory(prefix="funds-trace-ci-") as temporary:
            root = Path(temporary)
            self.git(root, "init", "-q")
            self.git(root, "config", "user.name", "Synthetic CI Test")
            self.git(root, "config", "user.email", "synthetic@example.invalid")
            (root / "app").mkdir()
            (root / "frontend").mkdir()
            (root / "app" / "old.py").write_text("synthetic\n", encoding="utf-8")
            (root / "frontend" / "delete.ts").write_text("synthetic\n", encoding="utf-8")
            self.git(root, "add", ".")
            self.git(root, "commit", "-qm", "Synthetic base")
            base = self.git(root, "rev-parse", "HEAD")
            new_path = "docs/new name 合成.md"
            (root / "docs").mkdir()
            (root / "app" / "old.py").rename(root / new_path)
            (root / "frontend" / "delete.ts").unlink()
            self.git(root, "add", "-A")
            self.git(root, "commit", "-qm", "Synthetic rename and delete")
            head = self.git(root, "rev-parse", "HEAD")
            paths = ci.changed_paths(base, head, root)
            self.assertEqual(set(paths), {"app/old.py", "frontend/delete.ts", new_path})
            self.assertEqual(ci.classify(paths), flags("python", "react"))

    def test_nul_records_preserve_tabs_and_newlines_without_a_shell(self):
        root = Path("synthetic-repository")
        with patch.object(ci.subprocess, "run", return_value=subprocess.CompletedProcess(
            [], 0, stdout=b"app/tab\tname.py\0frontend/line\nname.ts\0",
        )) as run:
            self.assertEqual(ci.changed_paths("a" * 40, "b" * 40, root), [
                "app/tab\tname.py", "frontend/line\nname.ts",
            ])
        run.assert_called_once_with(
            ["git", "diff", "--name-only", "-z", "--no-renames", f"{'a' * 40}...{'b' * 40}"],
            cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def test_invalid_revisions_are_rejected_before_git(self):
        with patch.object(ci.subprocess, "run") as run:
            for invalid in ("HEAD", "a" * 39, "g" * 40, "a" * 40 + ";echo unsafe"):
                with self.subTest(revision=invalid):
                    with self.assertRaises(ValueError):
                        ci.changed_paths(invalid, "b" * 40, Path("."))
                    with self.assertRaises(ValueError):
                        ci.changed_paths("a" * 40, invalid, Path("."))
            run.assert_not_called()


class GateTests(unittest.TestCase):
    def test_docs_only_and_every_required_group_combination_pass(self):
        for mask in range(8):
            groups = [group for index, group in enumerate(GROUPS) if mask & (1 << index)]
            with self.subTest(groups=groups):
                ci.check_gate(needs_for(*groups))

    def test_plan_failure_cancellation_and_skip_fail(self):
        for result in ("failure", "cancelled", "skipped", None):
            needs = needs_for()
            needs["plan"]["result"] = result
            with self.subTest(result=result):
                with self.assertRaises(ValueError):
                    ci.check_gate(needs)

    def test_required_failure_cancellation_and_skip_fail_for_each_group(self):
        for group in GROUPS:
            for result in ("failure", "cancelled", "skipped", None):
                needs = needs_for(group)
                needs[group]["result"] = result
                with self.subTest(group=group, result=result):
                    with self.assertRaises(ValueError):
                        ci.check_gate(needs)

    def test_unrelated_group_must_be_skipped(self):
        for group in GROUPS:
            for result in ("success", "failure", "cancelled", None):
                needs = needs_for()
                needs[group]["result"] = result
                with self.subTest(group=group, result=result):
                    with self.assertRaises(ValueError):
                        ci.check_gate(needs)

    def test_missing_jobs_and_malformed_outputs_fail(self):
        invalid_needs = [None, [], {}, {**needs_for(), "extra": {"result": "success"}}]
        for group in ("plan", *GROUPS):
            needs = needs_for()
            del needs[group]
            invalid_needs.append(needs)
            needs = needs_for()
            needs[group] = "success"
            invalid_needs.append(needs)
        for output in (None, {}, {**flags(), "extra": "false"}):
            needs = needs_for()
            needs["plan"]["outputs"] = output
            invalid_needs.append(needs)
        for value in (True, False, None, 0, "True", "", "yes"):
            needs = needs_for()
            needs["plan"]["outputs"]["python"] = value
            invalid_needs.append(needs)
        for needs in invalid_needs:
            with self.subTest(needs=needs):
                with self.assertRaises(ValueError):
                    ci.check_gate(needs)


class CommandTests(unittest.TestCase):
    def test_plan_appends_fixed_outputs_and_prints_a_short_summary(self):
        with tempfile.TemporaryDirectory(prefix="funds-trace-ci-output-") as temporary:
            output = Path(temporary) / "github-output"
            output.write_text("existing=value\n", encoding="utf-8")
            stdout = io.StringIO()
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch.object(
                ci, "changed_paths", return_value=["frontend/src/App.tsx"]
            ) as changed, redirect_stdout(stdout):
                result = ci.main(["plan", "--base", "a" * 40, "--head", "b" * 40])
            self.assertEqual(result, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing=value\npython=false\nreact=true\ndesktop=false\n")
            self.assertIn("1 changed paths", stdout.getvalue())
            self.assertEqual(changed.call_args.args[:2], ("a" * 40, "b" * 40))

    def test_gate_command_reads_json_environment_and_returns_failure(self):
        for raw, expected in ((json.dumps(needs_for()), 0), ("not json", 1), ("{}", 1), ("", 1)):
            with self.subTest(raw=raw), patch.dict(os.environ, {"CI_NEEDS": raw}):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(ci.main(["gate"]), expected)


if __name__ == "__main__":
    unittest.main()
