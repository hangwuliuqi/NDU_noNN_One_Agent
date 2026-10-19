"""Export complete, rendered first-turn LLM message pairs for both projects.

Run with --project pointing at either project root. No LLM or HFSS is called.
For the two-agent experience examples, two measured first-iteration curves from
the named archived run provide a reproducible post-HFSS example state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    sys.path.insert(0, str(project))

    from llmo.config import load_config
    from llmo.metrics import evaluate_curve
    from llmo.models import CurveData, SampleRecord
    from llmo.optimizer import OptimizationEngine

    dual = project.name == "NDU_noNN_Two_Agent"
    output = project / "docs" / "prompt_exports"
    output.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("prompt_export")
    manifest = []

    class CapturingClient:
        prompt = ""

        def complete_json(self, prompt, iteration, mock_factory=None, **kwargs):
            self.prompt = prompt
            return mock_factory()

    def save(name: str, conf, prompt: str, sample_count: int, note: str) -> None:
        system = conf.get_string("llm.system_prompt")
        body = f"SYSTEM MESSAGE\n{system}\n\nUSER MESSAGE\n{prompt}"
        path = output / name
        path.write_bytes(body.encode("utf-8"))
        manifest.append({
            "file": name,
            "state": note,
            "samples_in_user_prompt": sample_count,
            "system_characters": len(system),
            "user_characters": len(prompt),
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        })

    for use_initial in (True, False):
        mode = "parquet_40" if use_initial else "zero_start"
        overrides = [] if use_initial else ["project.use_initial_parquet=false"]
        conf = load_config(project / "project.conf", overrides)
        with TemporaryDirectory(prefix="prompt_export_", dir=output) as temporary:
            engine = OptimizationEngine(conf, project, Path(temporary), None, logger)
            engine.validate_and_bootstrap()
            count = min(conf.get_int("optimization.top_k"), len(engine.pools.records))
            sampling_rows = engine.pools.ranked_summaries(
                limit=count, exclude_iteration=True
            )
            client = CapturingClient()
            engine.sampling_agent.client = client
            if dual:
                engine.sampling_agent.propose(
                    sampling_rows, engine.guidebook.text(), 1,
                    allowed_rule_ids=engine.guidebook.sampling_rule_ids(),
                )
            else:
                engine.sampling_agent.propose(sampling_rows, 1)
            save(
                f"{mode}_sampling.txt", conf, client.prompt, len(sampling_rows),
                "First sampling turn in a new run, before any new HFSS simulation.",
            )

            if dual:
                if args.archive is None:
                    raise ValueError("--archive is required for the two-agent project")
                raw_records = json.loads(
                    (args.archive / "complete_information_pool.json").read_text(encoding="utf-8")
                )
                first_iteration = [item for item in raw_records if item["iteration"] == 1]
                if not first_iteration:
                    raise ValueError("archive has no first-iteration HFSS results")
                records = []
                for item in first_iteration:
                    curve = CurveData(**item["curve"])
                    records.append(SampleRecord(
                        sample_id=item["sample_id"],
                        variables={key: float(value) for key, value in item["variables"].items()},
                        curve=curve,
                        metrics=evaluate_curve(curve.frequency_ghz, curve.s11_db, conf),
                        iteration=1,
                        source="remote_hfss",
                        metadata={},
                    ))
                engine.pools.add_many(records)
                count = min(conf.get_int("optimization.top_q"), len(engine.pools.records))
                experience_rows = engine.pools.ranked_summaries(limit=count)
                prompt = engine.experience_agent._build_prompt(experience_rows)
                save(
                    f"{mode}_experience.txt", conf, prompt, len(experience_rows),
                    "Post-HFSS example using two measured first-iteration curves from "
                    f"{args.archive.name}; zero-start is illustrative, not a recorded zero-start run.",
                )

    conf = load_config(project / "project.conf")
    preflight = conf.get_config("llm.preflight")
    preflight_body = (
        "SYSTEM MESSAGE\n" + preflight.get_string("system_prompt")
        + "\n\nUSER MESSAGE\n" + preflight.get_string("user_prompt")
    )
    (output / "preflight.txt").write_bytes(preflight_body.encode("utf-8"))
    manifest.append({
        "file": "preflight.txt",
        "state": "Sent once per LLM module before formal optimization calls when preflight is enabled.",
        "sha256": hashlib.sha256(preflight_body.encode("utf-8")).hexdigest(),
    })
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
