"""Validate, persist, and render the current experience-LLM rule snapshot."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .models import InteractionRule, VariableSpec
from .json_utils import dumps_readable

EFFECT_KEYS = {
    "low_resonance_point",
    "s11_at_2_45",
    "high_resonance_point",
    "s11_at_4_50",
}


class GuidebookValidationError(ValueError):
    """Validation errors that can be corrected by asking the experience LLM again."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))
RESONANCE_EFFECT_KEYS = {"low_resonance_point", "high_resonance_point"}
S11_EFFECT_KEYS = {"s11_at_2_45", "s11_at_4_50"}
RESONANCE_EFFECT_SYMBOLS = {"l", "h", "k"}
S11_EFFECT_SYMBOLS = {"d", "s", "k"}


class GuidebookManager:
    """保存经验 LLM 给出的当前规则快照，并生成采样 LLM 使用的手册。"""

    def __init__(
        self,
        runtime_dir: Path,
        specs: dict[str, VariableSpec],
        max_active_rules: int,
    ) -> None:
        self.path = runtime_dir / "variable_design_guidebook.md"
        self.rules_path = runtime_dir / "experience_rule_snapshot.json"
        self.history_dir = runtime_dir / "logs" / "variable_design_guidebook_history"
        self.specs = specs
        self.max_active_rules = max_active_rules
        runtime_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        if not self.rules_path.exists():
            self.rules_path.write_text("[]\n", encoding="utf-8")
        stored = self._load_rules()
        self._save_rules(stored)
        self._rebuild_guidebook(
            stored,
            self._latest_rule_iteration(stored),
            archive_history=False,
        )

    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def active_rules(self) -> list[dict[str, Any]]:
        """把当前经验规则原样提供给下一轮经验 LLM。"""
        return [dict(item) for item in self._load_rules()]

    def sampling_rule_ids(self) -> set[str]:
        return {str(item["rule"]) for item in self._load_rules()}

    def replace_with_llm_rules(
        self,
        payload: dict[str, Any],
        iteration: int,
        source_ids: list[str],
    ) -> list[InteractionRule]:
        """用经验 LLM 本轮给出的完整规则集替换上一轮快照。"""
        raw_rules = payload.get("rules", [])
        if not isinstance(raw_rules, list):
            raise GuidebookValidationError(
                ["experience response field 'rules' must be a list"]
            )
        if len(raw_rules) > self.max_active_rules:
            raise GuidebookValidationError(
                [
                    "experience LLM returned "
                    f"{len(raw_rules)} rules; maximum is {self.max_active_rules}"
                ]
            )

        domain_errors = self._collect_domain_errors(raw_rules)
        if domain_errors:
            raise GuidebookValidationError(domain_errors)

        rules: list[InteractionRule] = []
        stored: list[dict[str, Any]] = []
        clean_source_ids = list(dict.fromkeys(str(item) for item in source_ids))
        validation_errors: list[str] = []
        for index, raw in enumerate(raw_rules, start=1):
            try:
                rule = self._validate(
                    raw,
                    f"r-{iteration:04d}-{index:02d}",
                    clean_source_ids,
                )
            except (TypeError, ValueError) as exc:
                validation_errors.append(f"rule {index}: {exc}")
                continue
            rules.append(rule)
            stored.append(self._serialize_rule(rule, iteration))
        if validation_errors:
            raise GuidebookValidationError(validation_errors)

        self._save_rules(stored)
        self._rebuild_guidebook(stored, iteration, archive_history=True)
        return rules

    def _collect_domain_errors(self, raw_rules: list[Any]) -> list[str]:
        """Collect every out-of-range domain entry before normal validation stops."""
        errors: list[str] = []
        for index, raw in enumerate(raw_rules, start=1):
            if not isinstance(raw, dict):
                continue
            domain = raw.get("domain", "global")
            if domain == "global" or not isinstance(domain, dict):
                continue
            for raw_name, raw_bounds in domain.items():
                name = str(raw_name).lower()
                if name not in self.specs or not isinstance(raw_bounds, dict):
                    continue
                try:
                    lower = float(raw_bounds.get("min"))
                    upper = float(raw_bounds.get("max"))
                except (TypeError, ValueError):
                    continue
                spec = self.specs[name]
                if (
                    not math.isfinite(lower)
                    or not math.isfinite(upper)
                    or lower > upper
                    or lower < spec.minimum
                    or upper > spec.maximum
                ):
                    errors.append(
                        f"rule {index} domain {name}=[{lower},{upper}] is outside "
                        f"[{spec.minimum},{spec.maximum}]"
                    )
        return errors

    def _validate(
        self,
        raw: dict[str, Any],
        rule_id: str,
        source_ids: list[str],
    ) -> InteractionRule:
        if not isinstance(raw, dict):
            raise TypeError("each experience rule must be an object")

        domain_raw = raw.get("domain", "global")
        if domain_raw == "global":
            domain: dict[str, dict[str, float]] | str = "global"
        else:
            if not isinstance(domain_raw, dict) or not domain_raw:
                raise ValueError("rule domain must be 'global' or a non-empty object")
            domain = {}
            for raw_name, raw_bounds in domain_raw.items():
                name = str(raw_name).lower()
                if name not in self.specs or not isinstance(raw_bounds, dict):
                    raise ValueError(
                        f"invalid continuous rule domain entry: {raw_name}={raw_bounds}"
                    )
                lower = float(raw_bounds.get("min"))
                upper = float(raw_bounds.get("max"))
                spec = self.specs[name]
                if not math.isfinite(lower) or not math.isfinite(upper):
                    raise ValueError(f"non-finite domain bounds for {name}")
                if lower > upper or lower < spec.minimum or upper > spec.maximum:
                    raise ValueError(
                        f"domain {name}=[{lower},{upper}] is outside "
                        f"[{spec.minimum},{spec.maximum}]"
                    )
                domain[name] = {"min": round(lower, 2), "max": round(upper, 2)}

        changes = {
            str(key).lower(): str(value).lower()
            for key, value in raw.get("var_change", {}).items()
        }
        if (
            not changes
            or not set(changes).issubset(self.specs)
            or not set(changes.values()).issubset({"u", "d", "k"})
        ):
            raise ValueError(f"invalid var_change: {changes}")

        effects = {
            str(key).lower(): str(value).lower()
            for key, value in raw.get("effect", {}).items()
        }
        if set(effects) != EFFECT_KEYS:
            raise ValueError(f"effect must contain exactly {sorted(EFFECT_KEYS)}")
        if any(effects[key] not in RESONANCE_EFFECT_SYMBOLS for key in RESONANCE_EFFECT_KEYS):
            raise ValueError("resonance-point effects must use l/h/k")
        if any(effects[key] not in S11_EFFECT_SYMBOLS for key in S11_EFFECT_KEYS):
            raise ValueError("target-frequency s11 effects must use d/s/k")

        confidence = float(raw.get("confidence"))
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("rule confidence must be a finite number between 0 and 1")

        note = " ".join(str(raw.get("note", "")).split())
        if not note:
            raise ValueError("rule note must be non-empty")

        return InteractionRule(
            rule=rule_id,
            domain=domain,
            var_change=changes,
            effect=effects,
            confidence=round(confidence, 2),
            source=list(source_ids),
            note=note,
        )

    @staticmethod
    def _serialize_rule(rule: InteractionRule, iteration: int) -> dict[str, Any]:
        return {
            "rule": rule.rule,
            "domain": rule.domain,
            "var_change": rule.var_change,
            "effect": rule.effect,
            "confidence": rule.confidence,
            "source": list(rule.source),
            "note": rule.note,
            "created_iteration": iteration,
        }

    def _load_rules(self) -> list[dict[str, Any]]:
        stored = json.loads(self.rules_path.read_text(encoding="utf-8"))
        if not isinstance(stored, list):
            raise TypeError("experience_rule_snapshot.json must contain a list")
        return [self._validate_stored_rule(dict(item)) for item in stored]

    def _validate_stored_rule(self, item: dict[str, Any]) -> dict[str, Any]:
        """拒绝旧维护架构的规则，避免把 b/w/n 静默误译成物理移动方向。"""
        required = {
            "rule",
            "domain",
            "var_change",
            "effect",
            "confidence",
            "source",
            "note",
            "created_iteration",
        }
        if not required.issubset(item):
            raise ValueError(
                "runtime/experience_rule_snapshot.json does not match the current rule schema"
            )
        effects = item.get("effect", {})
        if (
            not isinstance(effects, dict)
            or set(effects) != EFFECT_KEYS
            or any(effects[key] not in RESONANCE_EFFECT_SYMBOLS for key in RESONANCE_EFFECT_KEYS)
            or any(effects[key] not in S11_EFFECT_SYMBOLS for key in S11_EFFECT_KEYS)
        ):
            raise ValueError(
                "runtime/experience_rule_snapshot.json contains invalid effect symbols"
            )
        confidence = float(item["confidence"])
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("stored rule confidence must be between 0 and 1")
        item["confidence"] = round(confidence, 2)
        return item

    def _save_rules(self, stored: list[dict[str, Any]]) -> None:
        self.rules_path.write_text(
            dumps_readable(stored) + "\n",
            encoding="utf-8",
        )

    def _rebuild_guidebook(
        self,
        stored: list[dict[str, Any]],
        iteration: int,
        *,
        archive_history: bool,
    ) -> None:
        sections = [
            "# runtime variable design guidebook",
            (
                "this file is the direct, code-validated rule snapshot returned by the "
                "experience llm. confidence is the experience llm's evidence assessment, "
                "not a probability calculated or upgraded by code. every rule is a trend, "
                "not a universal physical law."
            ),
        ]
        if not stored:
            sections.append("active_rules=none")
        else:
            sections.append("## active rules")
            sections.extend(f"\n\n{self._render(item)}" for item in stored)
        text = "\n\n".join(sections).rstrip() + "\n"
        self._write_text_atomic(self.path, text)
        if archive_history:
            history_path = self.history_dir / (
                f"variable_design_guidebook_iteration_{iteration:04d}.md"
            )
            self._write_text_atomic(history_path, text)

    @staticmethod
    def _write_text_atomic(path: Path, text: str) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)

    @staticmethod
    def _render(item: dict[str, Any]) -> str:
        def domain_text(domain: dict[str, Any] | str) -> str:
            if domain == "global":
                return "global"
            return "{" + ",".join(
                f"{name}:[{bounds['min']:.2f},{bounds['max']:.2f}]"
                for name, bounds in domain.items()
            ) + "}"

        def mapping(value: dict[str, str]) -> str:
            return "{" + ",".join(f"{key}:{symbol}" for key, symbol in value.items()) + "}"

        return "\n".join(
            [
                f"rule={item['rule']}",
                f"domain={domain_text(item['domain'])}",
                f"var_change={mapping(item['var_change'])}",
                f"effect={mapping(item['effect'])}",
                f"confidence={float(item['confidence']):.2f}",
                f"note={item['note']}",
            ]
        )

    @staticmethod
    def _rule_iteration(rule_id: str) -> int:
        parts = str(rule_id).split("-")
        return int(parts[1]) if len(parts) >= 3 and parts[1].isdigit() else 0

    @classmethod
    def _latest_rule_iteration(cls, stored: list[dict[str, Any]]) -> int:
        return max((cls._rule_iteration(item.get("rule", "")) for item in stored), default=0)
