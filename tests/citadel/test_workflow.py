"""Exercise the workflow's actual gate script with a mocked GitHub API."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

_WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/citadel-probe.yml"
_HARNESS = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const output = [];
const summary = {
  addHeading() { return this; },
  addRaw(value) { output.push(value); return this; },
  async write() {},
};
const github = {rest: {actions: {
  async getWorkflowRun() { return {data: {created_at: '2026-10-05T22:00:00Z'}}; },
  async listWorkflowRuns() { return {data: {workflow_runs: input.previous}}; },
}}};
vm.runInNewContext('(async () => {' + input.script + '})()', {
  context: input.context, github, core: {summary}, process,
}).then(() => process.stdout.write(output.join(''))).catch(error => {
  console.error(error); process.exitCode = 1;
});
"""


@pytest.mark.parametrize("event,outcome,previous,expected", [
    ("schedule", "success", [], 1),
    ("schedule", "success", [("success", "sha"), ("success", "sha")], 3),
    ("schedule", "success", [("failure", "sha"), ("success", "sha")], 1),
    ("schedule", "success", [("success", "sha"), ("failure", "sha")], 2),
    ("schedule", "success", [("success", "old-sha"), ("success", "old-sha")], 1),
    ("schedule", "success", [(None, "sha"), ("success", "sha")], 1),
    ("schedule", "failure", [("success", "sha"), ("success", "sha")], 0),
    ("workflow_dispatch", "success", [("success", "sha"), ("success", "sha")], 0),
])
def test_activation_gate_requires_consecutive_scheduled_passes(event, outcome, previous, expected):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to check the GitHub Actions gate script")
    match = re.search(r"          script: \|\n(.*?)\n      - name: Upload", _WORKFLOW.read_text(), re.S)
    assert match
    script = "\n".join(line[12:] for line in match[1].splitlines())
    runs = [
        {"id": index, "conclusion": conclusion, "head_sha": sha,
         "created_at": f"2026-10-0{4 - index}T22:00:00Z"}
        for index, (conclusion, sha) in enumerate(previous)
    ]
    result = subprocess.run(
        [node, "--eval", _HARNESS], capture_output=True, text=True, check=True,
        input=json.dumps({"script": script, "previous": runs, "context": {
            "repo": {"owner": "test", "repo": "test"}, "runId": 100,
            "eventName": event, "ref": "refs/heads/main", "sha": "sha",
        }}), env={**os.environ, "PROBE_OUTCOME": outcome},
    )
    assert f"{expected}/3" in result.stdout
    assert ("Gate passed" in result.stdout) == (expected == 3)
