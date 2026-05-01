"""NoDriver browser management and lifecycle"""

import asyncio
import logging
import os
import random
from typing import Optional, List

from dotenv import load_dotenv

load_dotenv()
import nodriver as uc
from nodriver import Config

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]


class NoDriverDriver:
    """Manages nodriver browser lifecycle."""

    def __init__(self, port: int = 9222) -> None:
        self.browser = None
        self.tab = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._port = port
        self._user_agent: str = random.choice(USER_AGENTS)

    # ── Public API ─────────────────────────────────────────────────────────

    def run(self, coro, timeout_seconds: Optional[float] = None):
        """Execute a nodriver coroutine in the dedicated event loop."""
        if not self._loop:
            raise RuntimeError("Driver loop not initialized. Call setup() first.")
        if timeout_seconds and timeout_seconds > 0:
            coro = asyncio.wait_for(coro, timeout=timeout_seconds)
        return self._loop.run_until_complete(coro)

    @property
    def current_url(self) -> str:
        """Return the current tab URL, or an empty string if unavailable."""
        target = getattr(self.tab, "target", None)
        return str(getattr(target, "url", "") or "") if target else ""

    def setup(self) -> None:
        """Initialize the browser."""
        logger.info("Setting up NoDriver browser...")
        logger.info(f"Using user agent: {self._user_agent[:60]}...")

        headless = os.getenv("NODRIVER_HEADLESS", "false").lower() in {"1", "true", "yes"}

        config = Config(
            headless=headless,
            port=self._port,
            browser_args=[
                f"--user-agent={self._user_agent}",
                "--disable-dev-shm-usage",
                "--disable-browser-side-navigation",
            ],
        )

        try:
            self._loop = uc.loop()
            self.browser = self.run(uc.start(config=config))
            self.tab = self.run(self.browser.get("about:blank"))
            logger.info("NoDriver browser initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize NoDriver browser: {e}")
            raise

    def restart(self, reason: str = "manual") -> None:
        """Stop the browser, then reinitialize with a fresh user agent."""
        logger.warning(f"Restarting NoDriver browser (reason={reason})...")
        try:
            self.quit()
        except Exception:
            pass
        self._user_agent = random.choice(USER_AGENTS)
        self.setup()

    def cleanup_tabs_for_next_batch(self) -> None:
        """Close any extra tabs and reset the working tab to about:blank."""
        if not self.browser:
            return

        tabs: List = list(getattr(self.browser, "tabs", None) or [])
        if isinstance(tabs, dict):
            tabs = list(tabs.values())

        for tab in tabs:
            if tab is None or tab is self.tab:
                continue
            try:
                self.run(tab.close())
            except Exception:
                pass

        try:
            self.tab = self.run(self.browser.get("about:blank"))
        except Exception:
            if self.tab:
                try:
                    self.run(self.tab.get("about:blank"))
                except Exception:
                    pass

    def quit(self) -> None:
        """Stop the browser and close the loop."""
        try:
            if self.browser:
                tabs: List = list(getattr(self.browser, "tabs", None) or [])
                if isinstance(tabs, dict):
                    tabs = list(tabs.values())
                for tab in tabs:
                    if tab is None:
                        continue
                    try:
                        self.run(tab.close())
                    except Exception:
                        pass
                self.browser.stop()
                logger.info("NoDriver browser closed")
        except Exception as e:
            logger.warning(f"Error closing browser: {e}")
        finally:
            self.browser = None
            self.tab = None
            if self._loop:
                try:
                    self._loop.close()
                except Exception:
                    pass
                self._loop = None
