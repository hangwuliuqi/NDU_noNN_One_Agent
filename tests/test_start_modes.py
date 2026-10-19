"""Cold-start mode skips the initial package and renders honest evidence counts."""

import logging
from pathlib import Path

import pytest

from llmo.config import load_config
from llmo.models import AntennaMetrics, CurveData, SampleRecord
from llmo.optimizer import OptimizationEngine


ROOT = Path(__file__).resolve().parents[1]


class MockClient:
    def __init__(self):
        self.prompt = ""

    def complete_json(self, prompt, iteration, mock_factory=None, **kwargs):
        self.prompt = prompt
        return mock_factory()


def test_zero_sample_bootstrap_and_prompt_counts(tmp_path):
    conf = load_config(ROOT / "project.conf", [
        "project.use_initial_parquet=false",
        "project.initial_parquet=missing.parquet",
    ])
    engine = OptimizationEngine(conf, ROOT, tmp_path / "run", None, logging.getLogger(__name__))
    assert engine.validate_and_bootstrap() == 0
    assert engine.pools.records == []

    client = MockClient()
    engine.sampling_agent.client = client
    assert engine.sampling_agent.propose([], 1)
    assert "No evaluated samples are available yet" in client.prompt
    assert "0 available evaluated samples" in client.prompt

    variables = {name: (spec.minimum + spec.maximum) / 2 for name, spec in engine.specs.items()}
    engine.pools.add(SampleRecord(
        sample_id="iteration_0001_01", variables=variables,
        curve=CurveData([2.0, 5.0], [-10.0, -11.0]),
        metrics=AntennaMetrics(
            s11_at_2_45=-10.0, s11_at_4_50=-11.0,
            resonance_count=0, is_dual_resonance=False,
            low_resonance_point=0.0, s11_at_low_resonance=0.0,
            high_resonance_point=0.0, s11_at_high_resonance=0.0, score=1.0,
        ),
        iteration=1, source="remote_hfss", metadata={},
    ))
    samples = engine.pools.ranked_summaries(limit=1)
    assert engine.sampling_agent.propose(samples, 2)
    assert "Exactly 1 evaluated sample(s) are available" in client.prompt
    assert "1 available evaluated samples" in client.prompt

    conf.put("project.use_initial_parquet", True)
    with pytest.raises(ValueError, match="Parquet mode cannot resume"):
        engine.validate_and_bootstrap()
    conf.put("project.use_initial_parquet", False)
    engine.pools.records[0].source = "initial_parquet"
    with pytest.raises(ValueError, match="zero-sample mode cannot resume"):
        engine.validate_and_bootstrap()


def test_default_mode_still_imports_parquet(tmp_path):
    conf = load_config(ROOT / "project.conf")
    engine = OptimizationEngine(conf, ROOT, tmp_path / "run", None, logging.getLogger(__name__))
    assert engine.validate_and_bootstrap() == 40
    assert len(engine.pools.records) == 40
