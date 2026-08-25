"""JSON Schema export used by the web UI to build forms."""
from __future__ import annotations

from typing import Any

from .models import AppConfig


def app_schema(board_models: dict[str, type], source_models: dict[str, type]) -> dict[str, Any]:
    """Root schema with plugin config models spliced under ``boards`` / ``sources``."""
    schema = AppConfig.model_json_schema()
    schema["properties"]["boards"] = _plugin_section(board_models, "Board settings")
    schema["properties"]["sources"] = _plugin_section(source_models, "Data source settings")
    return schema


def _plugin_section(models: dict[str, type], title: str) -> dict[str, Any]:
    props = {key: model.model_json_schema() for key, model in models.items()}
    return {"type": "object", "title": title, "properties": props}
