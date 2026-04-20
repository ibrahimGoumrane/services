"""NoDriver browser management and lifecycle"""

import asyncio
import logging
import os
import random
import time
from typing import Optional, List
from dotenv import load_dotenv

load_dotenv()
import nodriver as uc


logger = logging.getLogger(__name__)

# User agents for rotation
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 OPR/105.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0',
]


class NoDriverDriver:
    """Manages nodriver browser lifecycle and anti-bot-friendly defaults"""
    
    def __init__(self):
        """Initialize nodriver manager"""
        self.browser = None
        self.tab = None
        self._loop = None
        self._restart_epoch = 0
        self._last_health_restart_epoch = 0

    def _run(self, coro, timeout_seconds: Optional[float] = None):
        """Execute nodriver coroutine in the dedicated event loop."""
        if not self._loop:
            raise RuntimeError("Driver loop not initialized. Call setup() first.")
        if timeout_seconds and timeout_seconds > 0:
            return self._loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout_seconds))
        return self._loop.run_until_complete(coro)

    @property
    def current_url(self) -> str:
        """Return current tab URL when available."""
        if not self.tab or not getattr(self.tab, "target", None):
            return ""
        return str(getattr(self.tab.target, "url", "") or "")
    
    def setup(self) -> None:
        """Initialize nodriver browser and a reusable tab."""
        logger.info("Setting up NoDriver browser...")
        user_agent = random.choice(USER_AGENTS)
        logger.info(f"Using user agent: {user_agent[:50]}...")

        headless = os.getenv("NODRIVER_HEADLESS", "false").lower() in {"1", "true", "yes"}
        browser_args = [
            f"--user-agent={user_agent}",
            "--disable-dev-shm-usage",
        ]

        try:
            self._loop = uc.loop()
            self.browser = self._run(uc.start(headless=headless, browser_args=browser_args))

            # Warm up the browser session on Google before normal crawling.
            try:
                self.tab = self._run(self.browser.get("https://www.google.com"))
                self._run(self.tab.sleep(random.uniform(1.0, 1.6)))
            except Exception as warmup_exc:
                logger.debug(f"Google warmup step failed: {warmup_exc}")

            self.tab = self._run(self.browser.get("about:blank"))

            width = random.randint(1366, 1920)
            height = random.randint(768, 1080)
            try:
                self._run(self.tab.set_window_size(width=width, height=height))
            except Exception:
                pass

            logger.info("✅ NoDriver browser initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize NoDriver browser: {e}")
            raise
    
    def restart(self, reason: str = "manual") -> None:
        """Restart the browser if unresponsive."""
        logger.warning("⚠️ Restarting NoDriver browser...")
        try:
            self.quit()
        except Exception:
            pass
        self.setup()
        self._restart_epoch += 1
        if reason == "health":
            self._last_health_restart_epoch = self._restart_epoch

    @property
    def restart_epoch(self) -> int:
        return self._restart_epoch

    def had_health_restart_since(self, since_epoch: int) -> bool:
        return self._last_health_restart_epoch > since_epoch

    def get(self, url: str, timeout_seconds: Optional[float] = None) -> None:
        """Navigate the current tab to the given URL."""
        if not self.tab:
            raise RuntimeError("Tab not initialized. Call setup() first.")
        self._run(self.tab.get(url), timeout_seconds=timeout_seconds)

    def sleep(self, seconds: float) -> None:
        """Async-aware sleep on the active tab."""
        if not self.tab:
            return
        self._run(self.tab.sleep(seconds))

    def get_content(self, timeout_seconds: Optional[float] = None) -> str:
        """Fetch current page HTML."""
        if not self.tab:
            return ""
        content = self._run(self.tab.get_content(), timeout_seconds=timeout_seconds)
        return str(content or "")

    def evaluate(self, expression: str, return_by_value: bool = True):
        """Evaluate JavaScript expression in current tab."""
        if not self.tab:
            return None
        return self._run(self.tab.evaluate(expression, return_by_value=return_by_value))

    def select(self, selector: str, timeout: float = 10):
        """Find a single element by CSS selector."""
        if not self.tab:
            return None
        return self._run(self.tab.select(selector, timeout=timeout))

    def find(self, text: str, best_match: bool = True, return_enclosing_element: bool = True, timeout: float = 10):
        """Find a single element by visible text."""
        if not self.tab:
            return None
        return self._run(
            self.tab.find(
                text,
                best_match=best_match,
                return_enclosing_element=return_enclosing_element,
                timeout=timeout,
            )
        )

    def send_keys(self, element, text: str) -> None:
        """Send keys to a nodriver element and wait for completion."""
        if element is None:
            return
        self._run(element.send_keys(text))

    def click(self, element) -> None:
        """Click a nodriver element and wait for completion."""
        if element is None:
            return
        self._run(element.click())

    def select(self, selector: str, timeout: float = 10):
        """Find a single element by CSS selector."""
        if not self.tab:
            return None
        return self._run(self.tab.select(selector, timeout=timeout))

    def select_all(self, selector: str, timeout: float = 10, include_frames: bool = False):
        """Find all elements by CSS selector."""
        if not self.tab:
            return []
        return self._run(self.tab.select_all(selector, timeout=timeout, include_frames=include_frames))

    def find(self, text: str, best_match: bool = True, return_enclosing_element: bool = True, timeout: float = 10):
        """Find a single element by visible text."""
        if not self.tab:
            return None
        return self._run(
            self.tab.find(
                text,
                best_match=best_match,
                return_enclosing_element=return_enclosing_element,
                timeout=timeout,
            )
        )

    def find_all(self, text: str, timeout: float = 10):
        """Find all elements matching visible text."""
        if not self.tab:
            return []
        return self._run(self.tab.find_all(text, timeout=timeout))

    def _list_tabs(self) -> List:
        if not self.browser:
            return []

        tabs = getattr(self.browser, "tabs", None)
        if isinstance(tabs, dict):
            return [tab for tab in tabs.values() if tab is not None]
        if isinstance(tabs, list):
            return [tab for tab in tabs if tab is not None]
        if tabs:
            return [tabs]
        return [self.tab] if self.tab else []

    def cleanup_tabs_for_next_batch(self) -> None:
        """Close popups/new tabs and reset to a clean working tab between batches."""
        if not self.browser:
            return

        tabs = self._list_tabs()
        primary_tab = self.tab or (tabs[0] if tabs else None)
        closed_tabs = 0
        close_errors = 0

        for tab in tabs:
            if tab is None or tab is primary_tab:
                continue
            try:
                self._run(tab.close())
                closed_tabs += 1
            except Exception:
                close_errors += 1
                continue

        try:
            self.tab = self._run(self.browser.get("about:blank"))
            logger.debug(f"Batch tab reset succeeded (closed_tabs={closed_tabs})")
        except Exception:
            self.tab = primary_tab
            try:
                self.get("about:blank")
            except Exception:
                pass

        if close_errors > 0:
            logger.debug(f"Batch tab cleanup had close errors (errors={close_errors})")
    
    def quit(self) -> None:
        """Close the browser and clean up loop resources."""
        try:
            if self.browser:
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
