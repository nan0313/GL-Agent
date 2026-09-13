from pathlib import Path
from typing import Any

import yaml


DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "rules.yaml"
DEFAULT_BLOCK_MESSAGE = (
    "该请求涉及生产控制操作，Agent 不执行此类操作，请按电网生产规程由具备权限的人员处理。"
)


class SafetyChecker:
    def __init__(self, config_path: str | Path = DEFAULT_RULES_PATH) -> None:
        self.config_path = Path(config_path)
        (
            self.high_risk_keywords,
            self.high_risk_patterns,
            self.block_message,
        ) = self._load_rules()

    def _load_rules(self) -> tuple[list[str], list[dict[str, Any]], str]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Rules config not found: {self.config_path}")

        with self.config_path.open("r", encoding="utf-8") as file:
            raw_config = yaml.safe_load(file) or {}

        safety_config = raw_config.get("safety", {})
        keywords = safety_config.get("high_risk_keywords", [])
        patterns = safety_config.get("high_risk_patterns", [])
        message = safety_config.get("default_block_message", DEFAULT_BLOCK_MESSAGE)
        return list(keywords), list(patterns), message

    def check(self, query: str) -> dict[str, Any]:
        matched_keywords = [
            keyword for keyword in self.high_risk_keywords if keyword in query
        ]

        for pattern in self.high_risk_patterns:
            all_keywords = pattern.get("all_keywords", [])
            if all(keyword in query for keyword in all_keywords):
                matched_keywords.extend(
                    keyword for keyword in all_keywords if keyword not in matched_keywords
                )

        if matched_keywords:
            return {
                "blocked": True,
                "risk_level": "high",
                "matched_keywords": matched_keywords,
                "message": self.block_message,
            }

        return {
            "blocked": False,
            "risk_level": "low",
            "matched_keywords": [],
        }
