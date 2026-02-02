"""Tokyo Night color theme for polectl CLI output."""

from rich.console import Console
from rich.style import Style
from rich.theme import Theme


# Tokyo Night color palette
TOKYO_NIGHT = {
    "blue": "#7aa2f7",
    "cyan": "#7dcfff",
    "green": "#9ece6a",
    "yellow": "#e0af68",
    "red": "#f7768e",
    "magenta": "#bb9af7",
    "comment": "#565f89",
    "fg": "#c0caf5",
    "bg": "#1a1b26",
}

# Rich theme using Tokyo Night palette
POLECTL_THEME = Theme({
    # Status colors
    "status.running": Style(color=TOKYO_NIGHT["green"], bold=True),
    "status.pending": Style(color=TOKYO_NIGHT["yellow"]),
    "status.completed": Style(color=TOKYO_NIGHT["green"]),
    "status.failed": Style(color=TOKYO_NIGHT["red"], bold=True),
    "status.killed": Style(color=TOKYO_NIGHT["red"]),
    "status.timeout": Style(color=TOKYO_NIGHT["yellow"]),

    # Headers and labels
    "header": Style(color=TOKYO_NIGHT["blue"], bold=True),
    "label": Style(color=TOKYO_NIGHT["comment"]),

    # Identifiers
    "id": Style(color=TOKYO_NIGHT["cyan"]),
    "ticket": Style(color=TOKYO_NIGHT["magenta"]),
    "path": Style(color=TOKYO_NIGHT["fg"]),

    # Log levels
    "log.info": Style(color=TOKYO_NIGHT["cyan"]),
    "log.warn": Style(color=TOKYO_NIGHT["yellow"]),
    "log.error": Style(color=TOKYO_NIGHT["red"], bold=True),
    "log.debug": Style(color=TOKYO_NIGHT["comment"]),
    "log.timestamp": Style(color=TOKYO_NIGHT["comment"]),

    # Messages
    "success": Style(color=TOKYO_NIGHT["green"]),
    "error": Style(color=TOKYO_NIGHT["red"], bold=True),
    "warning": Style(color=TOKYO_NIGHT["yellow"]),
    "info": Style(color=TOKYO_NIGHT["cyan"]),
    "muted": Style(color=TOKYO_NIGHT["comment"]),
})


def get_console(force_terminal: bool = None) -> Console:
    """Get a Rich console with Tokyo Night theme.

    Args:
        force_terminal: Force terminal output (useful for testing).
                       None = auto-detect, True = force colors, False = no colors.
    """
    return Console(theme=POLECTL_THEME, force_terminal=force_terminal)


# Status icon mapping with semantic meaning
STATUS_ICONS = {
    "pending": "⏳",
    "running": "🔄",
    "completed": "✅",
    "failed": "❌",
    "killed": "💀",
    "timeout": "⏰",
}


def get_status_style(status: str) -> str:
    """Get the Rich style name for a polecat status."""
    return f"status.{status}" if status in STATUS_ICONS else "muted"


def format_id(polecat_id: str) -> str:
    """Format a polecat ID with theme markup."""
    return f"[id]{polecat_id}[/id]"


def format_ticket(ticket_ref: str) -> str:
    """Format a ticket reference with theme markup."""
    return f"[ticket]{ticket_ref}[/ticket]"


def format_status_badge(status: str) -> str:
    """Format a status badge with icon and themed text."""
    icon = STATUS_ICONS.get(status, "❓")
    style = get_status_style(status)
    return f"{icon} [{style}]{status}[/{style}]"


def format_label(label: str) -> str:
    """Format a label (field name) with theme markup."""
    return f"[label]{label}:[/label]"


def format_timestamp(ts: str) -> str:
    """Format a timestamp with theme markup."""
    return f"[log.timestamp]{ts}[/log.timestamp]"


def format_log_level(level: str) -> str:
    """Format a log level with appropriate theme."""
    level_upper = level.upper()
    style_map = {
        "INFO": "log.info",
        "WARN": "log.warn",
        "WARNING": "log.warn",
        "ERROR": "log.error",
        "ERR": "log.error",
        "DEBUG": "log.debug",
    }
    style = style_map.get(level_upper, "muted")
    return f"[{style}]{level_upper}[/{style}]"
