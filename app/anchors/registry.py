from pathlib import Path

import yaml

from app.schemas.anchor import AnchorDefinition


DEFAULT_ANCHORS_PATH = Path(__file__).resolve().parents[2] / "config" / "anchors.yaml"


class AnchorRegistry:
    def __init__(self, config_path: str | Path = DEFAULT_ANCHORS_PATH) -> None:
        self.config_path = Path(config_path)
        self._anchors = self._load_anchors()

    def _load_anchors(self) -> dict[str, AnchorDefinition]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Anchor config not found: {self.config_path}")

        with self.config_path.open("r", encoding="utf-8") as file:
            raw_config = yaml.safe_load(file) or {}

        raw_anchors = raw_config.get("anchors", raw_config if isinstance(raw_config, list) else [])
        anchors = [AnchorDefinition.model_validate(item) for item in raw_anchors]
        return {anchor.anchor_id: anchor for anchor in anchors}

    def list_anchors(self) -> list[AnchorDefinition]:
        return list(self._anchors.values())

    def get_anchor(self, anchor_id: str) -> AnchorDefinition | None:
        return self._anchors.get(anchor_id)

    def find_by_scenario(self, scenario: str) -> list[AnchorDefinition]:
        return [anchor for anchor in self._anchors.values() if anchor.scenario == scenario]

    def find_by_object_type(self, object_type: str) -> list[AnchorDefinition]:
        return [
            anchor
            for anchor in self._anchors.values()
            if object_type in anchor.object_types
        ]
