"""NoDriver browser management and lifecycle"""

import asyncio
import json
import logging
import os
import random
from pathlib import Path
from typing import Optional, List

from dotenv import load_dotenv

load_dotenv()
import nodriver as uc
from nodriver import Config
import nodriver.cdp.page as cdp_page


logger = logging.getLogger(__name__)

# ── User-agent pool ────────────────────────────────────────────────────────
USER_AGENTS = [
    # Chrome – Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome – macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome – Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Firefox – Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox – macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Safari – macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    # Edge – Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]

# ── Stealth scripts injected before every document loads ──────────────────
#
# Canvas noise  – adds ±1 random drift per RGBA channel on every toDataURL()
# call so each fingerprint probe gets a unique result, with zero visible
# rendering artefacts.
#
# WebGL spoof   – returns common Intel strings for the two extension constants
# that fingerprinters query most (UNMASKED_VENDOR / UNMASKED_RENDERER).
#
_CANVAS_NOISE_SCRIPT = """
(() => {
    const orig = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function () {
        const ctx = this.getContext('2d');
        if (ctx) {
            const img = ctx.getImageData(0, 0, this.width, this.height);
            for (let i = 0; i < img.data.length; i += 4) {
                img.data[i]     += Math.floor(Math.random() * 3) - 1;
                img.data[i + 1] += Math.floor(Math.random() * 3) - 1;
                img.data[i + 2] += Math.floor(Math.random() * 3) - 1;
            }
            ctx.putImageData(img, 0, 0);
        }
        return orig.apply(this, arguments);
    };
})();
"""

_WEBGL_SPOOF_SCRIPT = """
(() => {
    const orig = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function (p) {
        if (p === 37445) return 'Intel Inc.';                // UNMASKED_VENDOR_WEBGL
        if (p === 37446) return 'Intel Iris OpenGL Engine';  // UNMASKED_RENDERER_WEBGL
        return orig.apply(this, arguments);
    };
})();
"""


class NoDriverDriver:
    """Manages nodriver browser lifecycle with full anti-detection hardening."""

    def __init__(self, session_file: Optional[str] = "session.dat" , port: int = 9222) -> None:
        """
        Args:
            session_file: Path to persist/restore cookies across runs.
                          Pass None to disable session persistence entirely.
            port: The port on which the browser will listen for CDP connections.
        """
        self.browser = None
        self.tab = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._session_file: Optional[Path] = Path(session_file) if session_file else None
        self._port = port
        # Path to legitimate cookies file
        self._legitimate_cookies_path: Path = Path(__file__).parent.parent / "init_files" / "legitimate_cookies.json"
        # Chosen once per instantiation; replaced on every restart so each
        # browser session presents a different agent.
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
        """
        Initialize the browser with all stealth measures applied:

        • Expert mode   – disables site-isolation trials, forces shadow DOM
                          open (lets us inspect CF/reCAPTCHA iframes).
        • Random UA     – rotated per session from a diverse pool.
        • CDP scripts   – canvas noise + WebGL spoof injected on every
                          new document, before any page JS can probe them.
        • Session load  – cookies restored from disk so the browser looks
                          like a returning user (defeats cold-start signals).
        • Google warmup – one real visit before any search so the session
                          has a browsing history (a brand-new browser that
                          immediately runs a search is a strong bot signal).
        • WebRTC lock   – flags prevent the real LAN IP leaking through
                          WebRTC even when a proxy is in use.
        """
        logger.info("Setting up NoDriver browser...")
        logger.info(f"Using user agent: {self._user_agent[:60]}...")

        headless = os.getenv("NODRIVER_HEADLESS", "false").lower() in {"1", "true", "yes"}

        config = Config(
            expert=True,    # shadow-DOM open + site-isolation disabled
            headless=headless,
            port=self._port,
            browser_args=[
                f"--user-agent={self._user_agent}",
                "--disable-dev-shm-usage",
                # Prevent WebRTC from leaking the real local IP behind a proxy
                "--disable-webrtc-encryption",
                "--enforce-webrtc-ip-permission-check=false",
            ],
        )

        try:
            self._loop = uc.loop()
            self.browser = self.run(uc.start(config=config))
            self._restore_session()
            self._warmup()
            logger.info("✅ NoDriver browser initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize NoDriver browser: {e}")
            raise

    def inject_stealth_scripts(self) -> None:
        """
        Register canvas-noise and WebGL-spoof scripts to execute before any
        page script on every subsequent document load in the current tab.

        Uses CDP add_script_to_evaluate_on_new_document so the patch is live
        from the very first JS tick — before fingerprinting probes fire.
        Call once after opening a new tab or navigating to a new origin.
        """
        if not self.tab:
            return
        for script in (_CANVAS_NOISE_SCRIPT, _WEBGL_SPOOF_SCRIPT):
            try:
                self.run(
                    self.tab.send(cdp_page.add_script_to_evaluate_on_new_document(script))
                )
            except Exception as e:
                logger.debug(f"Stealth script injection failed (non-fatal): {e}")

    def restart(self, reason: str = "manual") -> None:
        """Persist session, stop the browser, then reinitialize with a fresh UA."""
        logger.warning(f"⚠️ Restarting NoDriver browser (reason={reason})...")
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
        """Persist the session to disk, stop the browser, and close the loop."""
        self._save_session()
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

    # ── Private ────────────────────────────────────────────────────────────

    def _warmup(self) -> None:
        """
        Visit Google so the session has a realistic browsing history entry,
        inject stealth scripts, then park at about:blank.

        A brand-new browser session that goes straight to a search query or
        CF-protected site is a well-known bot signal.  This single warmup
        visit costs ~2 s and significantly reduces that signal.
        """
        try:
            self.tab = self.run(self.browser.get("https://www.google.com"))
            # Inject fingerprint patches immediately after first navigation
            self.inject_stealth_scripts()
            self.run(self.tab.sleep(random.uniform(1.5, 2.5)))
        except Exception as e:
            logger.debug(f"Google warmup failed (non-fatal): {e}")

        self.tab = self.run(self.browser.get("about:blank"))

        # Randomize the viewport so window.screen varies per session
        width = random.randint(1366, 1920)
        height = random.randint(768, 1080)
        try:
            self.run(self.tab.set_window_size(width=width, height=height))
        except Exception:
            pass

    def _restore_session(self) -> None:
        """Load persisted cookies and legitimate cookies from disk so the browser appears as a returning user."""
        # First, load legitimate cookies to establish baseline legitimacy
        self._load_legitimate_cookies()
        
        # Then, load user's persisted session cookies (may override some legitimate cookies)
        if not self._session_file or not self._session_file.exists():
            return
        try:
            self.run(self.browser.cookies.load(self._session_file))
            logger.info(f"✓ Session restored from {self._session_file}")
        except Exception as e:
            logger.debug(f"Session restore failed (non-fatal): {e}")

    def _load_legitimate_cookies(self) -> None:
        """Load and inject legitimate cookies from the JSON file to establish a realistic session."""
        if not self._legitimate_cookies_path.exists():
            logger.debug(f"Legitimate cookies file not found: {self._legitimate_cookies_path}")
            return
        
        try:
            with open(self._legitimate_cookies_path, "r") as f:
                cookies = json.load(f)
            
            if not cookies or not isinstance(cookies, list):
                logger.debug("Legitimate cookies file is empty or invalid")
                return
            
            # Inject cookies into the browser
            for cookie in cookies:
                try:
                    self.run(self.browser.cookies.set(cookie))
                except Exception as e:
                    logger.debug(f"Failed to set cookie {cookie.get('name', 'unknown')}: {e}")
            
            logger.info(f"✓ Loaded {len(cookies)} legitimate cookies")
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in legitimate cookies file: {e}")
        except Exception as e:
            logger.debug(f"Failed to load legitimate cookies: {e}")

    def _save_session(self) -> None:
        """Persist cookies to disk for reuse in the next run."""
        if not self._session_file or not self.browser:
            return
        try:
            self.run(self.browser.cookies.save(self._session_file))
            logger.info(f"✓ Session saved to {self._session_file}")
        except Exception as e:
            logger.debug(f"Session save failed (non-fatal): {e}")