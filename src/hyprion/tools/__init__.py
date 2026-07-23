"""Agent-callable tools (navigation / retrieval / execution) + plugin slots.

Domain-specific tools (bluez/wpa/kernel parsers, state-machine dicts) live in
`tools/plugins/<name>/` and are toggled via `config.yaml -> tools`. The core
toolset is domain-agnostic so the agent works on any codebase/log today; domain
tools slot in later without touching the core.

See docs/architecture.md §4.3 / §5.
"""
