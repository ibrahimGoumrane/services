"""Selenium browser management and lifecycle"""

import os
import random
import time
from typing import Any, Optional, List

from dotenv import load_dotenv
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    InvalidSessionIdException,
)
from api.services.utils.log_socket import get_seeding_logger

load_dotenv()
logger = get_seeding_logger()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

PAGE_LOAD_TIMEOUT = 18
SCRIPT_TIMEOUT = 18


class SeleniumDriver:
    """Manages selenium + undetected-chromedriver browser lifecycle."""

    def __init__(self) -> None:
        self.driver: Optional[uc.Chrome] = None
        self._headless = os.getenv("HEADLESS_BROWSER", "false").lower() in {"1", "true", "yes"}
        self._version_main = int(os.getenv("CHROME_VERSION_MAIN", "148"))
        self._user_agent: str = random.choice(USER_AGENTS)

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def current_url(self) -> str:
        return self.driver.current_url if self.driver else ""

    @property
    def page_source(self) -> str:
        return self.driver.page_source if self.driver else ""

    def setup(self) -> None:
        logger.info("Setting up Selenium browser...")
        logger.info(f"Using user agent: {self._user_agent[:60]}...")

        options = uc.ChromeOptions()
        options.add_argument(f"--user-agent={self._user_agent}")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-browser-side-navigation")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")

        if self._headless:
            options.add_argument("--headless=new")

        try:
            self.driver = uc.Chrome(options=options, version_main=self._version_main, use_subprocess=True)
            self.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
            self.driver.set_script_timeout(SCRIPT_TIMEOUT)
            self.driver.get("about:blank")
            logger.info("Selenium browser initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Selenium browser: {e}")
            raise

    def get(self, url: str, timeout: Optional[float] = None) -> None:
        self._ensure_alive()
        if timeout is not None and timeout > 0:
            self.driver.set_page_load_timeout(timeout)
        try:
            self.driver.get(url)
        except TimeoutException:
            logger.warning(
                f"Page load timed out after {timeout}s for {url}, "
                "stopping load and continuing with partial content..."
            )
            try:
                self.driver.execute_script("window.stop()")
            except Exception:
                pass
        finally:
            if timeout is not None and timeout > 0:
                self.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def find_text(self, text: str, timeout: float = 1.0) -> Optional[Any]:
        self._ensure_alive()
        try:
            wait = WebDriverWait(self.driver, timeout)
            return wait.until(
                EC.element_to_be_clickable(
                    (By.XPATH, f"//*[contains(text(),'{text}')]")
                )
            )
        except (TimeoutException, NoSuchElementException):
            return None
        except InvalidSessionIdException:
            return None

    def select_css(self, css_selector: str, timeout: float = 1.5) -> Optional[Any]:
        self._ensure_alive()
        try:
            wait = WebDriverWait(self.driver, timeout)
            return wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
            )
        except (TimeoutException, NoSuchElementException):
            return None
        except InvalidSessionIdException:
            return None

    def move_and_click(self, element: Any) -> None:
        self._ensure_alive()
        try:
            ActionChains(self.driver) \
                .move_to_element(element) \
                .pause(random.uniform(0.02, 0.06)) \
                .click(element) \
                .perform()
        except InvalidSessionIdException:
            pass

    def scroll(self, pixels: int) -> None:
        self._ensure_alive()
        try:
            self.driver.execute_script(f"window.scrollBy(0, {pixels})")
        except InvalidSessionIdException:
            pass

    def send_enter_key(self, field: Any) -> None:
        """Focus *field* via click, then send Enter via ActionChains."""
        self._ensure_alive()
        try:
            field.click()
            time.sleep(random.uniform(0.05, 0.1))
            ActionChains(self.driver).send_keys(Keys.ENTER).perform()
        except InvalidSessionIdException:
            pass

    def cleanup_tabs_for_next_batch(self) -> None:
        self._ensure_alive()
        if not self.driver:
            return
        handles = self.driver.window_handles
        current = self.driver.current_window_handle
        for handle in handles:
            if handle != current:
                self.driver.switch_to.window(handle)
                try:
                    self.driver.close()
                except Exception:
                    pass
        if len(handles) > 1:
            self.driver.switch_to.window(current)
        self.driver.get("about:blank")

    def restart(self, reason: str = "manual") -> None:
        logger.warning(f"Restarting Selenium browser (reason={reason})...")
        try:
            self.quit()
        except Exception:
            pass
        self._user_agent = random.choice(USER_AGENTS)
        self.setup()

    def quit(self) -> None:
        if self.driver:
            try:
                self.driver.quit()
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")
            finally:
                self.driver = None

    def _is_browser_alive(self) -> bool:
        if not self.driver:
            return False
        try:
            _ = self.driver.current_url
            return True
        except InvalidSessionIdException:
            return False
        except Exception:
            return False

    def _ensure_alive(self) -> None:
        if not self._is_browser_alive():
            logger.warning("Browser session is dead – auto-restarting…")
            self.restart(reason="session_died")
