class JobInterruptionRequested(Exception):
    """Raised when a pause/stop signal is detected during row processing."""


class WebsearchFailure(Exception):
    """Raised when a web search operation fails unexpectedly."""
