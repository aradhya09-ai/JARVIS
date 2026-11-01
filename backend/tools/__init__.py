from tools import (
    application_launcher,
    assistant_tools,
    browser_tools,
    coding_tools,
    document_generator,
    file_manager,
    image_tool,
    personal_tools,
    phone_tools,
    system_monitor,
)
from tools.base import (
    ToolContext,
    ToolDefinition,
    ToolError,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
)


def build_registry(timeout_seconds: float = 120.0) -> ToolRegistry:
    registry = ToolRegistry(timeout_seconds=timeout_seconds)
    for module in (
        system_monitor,
        application_launcher,
        file_manager,
        document_generator,
        coding_tools,
        browser_tools,
        image_tool,
        assistant_tools,
        personal_tools,
        phone_tools,
    ):
        for tool in module.build():
            registry.register(tool)
    return registry


__all__ = [
    "ToolContext",
    "ToolDefinition",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "ToolValidationError",
    "build_registry",
]
