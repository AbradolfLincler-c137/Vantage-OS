import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
import json
import logging
import asyncio
from typing import Optional, Tuple, Dict, Any

from dotenv import load_dotenv
from google import genai
from PIL import Image
import random
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
try:
    from playwright_stealth import stealth_async
except ImportError:
    try:
        from playwright_stealth import stealth_async_page as stealth_async
    except:
        async def stealth_async(page): pass


class VantageEngine:
    def __init__(self, bridge_file: str = "task_bridge.json", log_file: str = "vantage.log"):
        self.bridge_file: str = bridge_file
        
        # Setup Logger
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        self.logger.info("Initializing VantageEngine...")

        # Setup Gemini
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or api_key == "your_api_key_here":
            self.logger.warning("Invalid or missing GEMINI_API_KEY in .env file.")
            self.client = None
        else:
            self.client = genai.Client(api_key=api_key)

        # Playwright internal state
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        
        self._is_running = True

    def _compress_image(self, image_path: str) -> None:
        """Resizes to 1280px width and converts to JPEG (quality 60)."""
        try:
            img = Image.open(image_path)
            # Maintain aspect ratio
            w_percent = (1280 / float(img.size[0]))
            h_size = int((float(img.size[1]) * float(w_percent)))
            img = img.resize((1280, h_size), Image.Resampling.LANCZOS)
            
            # Save as JPEG
            img.convert("RGB").save(image_path, "JPEG", quality=60)
            self.logger.info(f"Image compressed: {image_path} (1280x{h_size})")
        except Exception as e:
            self.logger.error(f"Compression error: {e}")

    async def _human_type(self, text: str) -> None:
        """Types with random delays between keystrokes (0.05s to 0.15s)."""
        if not self.page: return
        for char in text:
            await self.page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.05, 0.15))

    async def _human_click(self, x: int, y: int) -> None:
        """Adds ±3px random jitter to the target coordinates."""
        if not self.page: return
        jitter_x = x + random.randint(-3, 3)
        jitter_y = y + random.randint(-3, 3)
        self.logger.info(f"Human-click target ({x}, {y}) -> Jitter ({jitter_x}, {jitter_y})")
        await self.page.mouse.click(jitter_x, jitter_y, delay=random.randint(150, 300))

    async def _inject_stealth_scripts(self) -> None:
        """Hides navigator.webdriver and other bot detection signals."""
        if not self.page: return
        stealth_js = """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => False
        });
        """
        await self.page.add_init_script(stealth_js)
        self.logger.info("Custom stealth script injected.")

    async def setup_browser(self, start_url: str = "about:blank") -> None:
        """Initializes the standard Playwright browser and navigates to the start URL."""
        self.logger.info("Launching Playwright Chromium (Standard Launch + Stealth)...")
        self.playwright = await async_playwright().start()
        
        # Standard launch with maximized window
        print("[ENGINE] Attempting launch...")
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=["--start-maximized", "--no-sandbox"]
        )
        print("[ENGINE] Browser visible.")
        
        # Create context and page manually
        self.context = await self.browser.new_context(
            no_viewport=True, # Allow --start-maximized to take effect
        )
        self.page = await self.context.new_page()

        # Ready Signal for CEO Handshake
        try:
            with open(self.bridge_file, "r") as f:
                bridge_data = json.load(f)
            bridge_data["status"] = "ready"
            with open(self.bridge_file, "w") as f:
                json.dump(bridge_data, f, indent=2)
            self.logger.info("Handshake READY signal sent to bridge.")
        except Exception as e:
            self.logger.error(f"Failed to send READY signal: {e}")
        
        # Apply Stealth
        await stealth_async(self.page)
        await self._inject_stealth_scripts()
        self.logger.info("Stealth mode active.")

        
        try:
            self.logger.info(f"Navigating to start URL: {start_url}")
            await self.page.goto(start_url)
            await self.page.wait_for_load_state("networkidle")
        except Exception as e:
            self.logger.error(f"Failed to load start URL: {e}")

    async def _capture_and_locate(self, description: str) -> Optional[Tuple[int, int]]:
        """Takes a screenshot, passes it to Gemini, and extracts 1280x720 pixel coordinates."""
        if not self.page:
            self.logger.error("Browser page not initialized.")
            return None

        self.logger.info(f"Capturing screen and analyzing target '{description}'...")
        await self.page.wait_for_load_state("networkidle")
        await self.page.screenshot(path="viewport.png")
        self._compress_image("viewport.png")
        
        try:
            if not getattr(self, 'client', None):
                raise Exception("Gemini API client not initialized.")
                
            image = Image.open("viewport.png")
            prompt = f"Identify the [x, y] coordinates for '{description}'. Return ONLY JSON: {{'x': 0-1000, 'y': 0-1000}}."
            
            # Use the compressed image for Gemini
            response = self.client.models.generate_content(
                model='gemini-1.5-flash', 
                contents=[prompt, image],
                config=genai.types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            response_text = response.text.replace('```json', '').replace('```', '').strip()
            
            coords_percent = json.loads(response_text)
            x_percent = coords_percent.get("x", 0)
            y_percent = coords_percent.get("y", 0)
            
            x_pixel = int((x_percent / 1000) * 1280)
            y_pixel = int((y_percent / 1000) * 720)
            
            self.logger.info(f"Target found at coordinates: (x:{x_pixel}, y:{y_pixel})")
            return x_pixel, y_pixel
        except Exception as e:
            self.logger.error(f"Vision API logic error: {e}")
            return None

    async def _screenshot_pulse(self) -> None:
        """Capture a fresh viewport.png so the Actor always sees the latest state."""
        if self.page:
            await self.page.screenshot(path="viewport.png")
            self._compress_image("viewport.png")
            self.logger.info("[Pulse] viewport.png updated and compressed.")

    async def _execute_action(self, action_type: str, target: str, text: str, scroll_amount: int, selector_type: str = "vision") -> None:
        """Translates intent into physical mouse/keyboard actions."""
        if not self.page:
            raise Exception("Browser page not initialized.")

        if action_type == "navigate":
            self.logger.info(f"Executing navigate to: {target}")
            try:
                await self.page.goto(target, wait_until="networkidle")
            except Exception as e:
                self.logger.warning(f"Navigation failed: {e}. Retrying with reload...")
                await self.page.reload(wait_until="networkidle")
            self.logger.info(f"Navigation complete. Current URL: {self.page.url}")

        elif action_type in ["click", "type"]:
            # -- Locator Strategy ------------------------------------------
            locator = None
            if selector_type == "text":
                self.logger.info(f"Locating via text: '{target}'")
                locator = self.page.get_by_text(target).first
            elif selector_type == "css":
                self.logger.info(f"Locating via CSS: '{target}'")
                locator = self.page.locator(target).first
            elif selector_type == "id":
                self.logger.info(f"Locating via ID: '{target}'")
                locator = self.page.locator(f"id={target}").first
            elif selector_type == "xpath":
                self.logger.info(f"Locating via XPath: '{target}'")
                locator = self.page.locator(f"xpath={target}").first

            # -- Execution Logic -------------------------------------------
            if locator:
                try:
                    # Ensure element is visible and scrolled into view
                    await locator.scroll_into_view_if_needed()
                    
                    # Human-Move
                    box = await locator.bounding_box()
                    if box:
                        center_x = box['x'] + box['width'] / 2
                        center_y = box['y'] + box['height'] / 2
                        await self.page.mouse.move(center_x, center_y, steps=10)
                        self.logger.info(f"Human-move to locator: ({center_x}, {center_y})")
                    
                    # Jitter
                    await asyncio.sleep(random.uniform(0.5, 1.2))
                    
                    if action_type == "click":
                        await locator.click(delay=random.randint(150, 300))
                    elif action_type == "type":
                        await locator.fill("") # Clear before typing
                        await self._human_type(text)
                        await self.page.keyboard.press("Enter")
                except Exception as e:
                    self.logger.warning(f"Selector {selector_type} failed: {e}. Falling back to vision.")
                    locator = None # Trigger vision fallback

            if not locator or selector_type == "vision":
                coords = await self._capture_and_locate(target)
                if coords:
                    x, y = coords
                    
                    # Human-Move
                    await self.page.mouse.move(x, y, steps=10)
                    self.logger.info(f"Human-move to vision target: ({x}, {y})")
                    
                    # Jitter
                    await asyncio.sleep(random.uniform(0.5, 1.2))
                    
                    if action_type == "click":
                        self.logger.info(f"Executing vision-click at ({x}, {y})")
                        await self._human_click(x, y)
                    elif action_type == "type":
                        self.logger.info(f"Executing vision-type at ({x}, {y})")
                        await self._human_click(x, y)
                        await self._human_type(text)
                        await self.page.keyboard.press("Enter")
                else:
                    raise Exception(f"Element '{target}' not found via {selector_type}.")

        elif action_type == "scroll":
            self.logger.info(f"Executing scroll amount: {scroll_amount}")
            await self.page.mouse.wheel(0, scroll_amount)

        elif action_type == "wait":
            self.logger.info(f"Waiting 2s as requested: {target}")
            await asyncio.sleep(2)

        # Screenshot pulse - update viewport.png after every action
        await self._screenshot_pulse()

    def _update_bridge(self, bridge_data: Dict[str, Any], status: str, error: str = "") -> None:
        """Saves state back to the communication JSON channel."""
        bridge_data["status"] = status
        if self.page:
            bridge_data["current_url"] = self.page.url
        if error:
            bridge_data["error"] = error
        
        try:
            with open(self.bridge_file, "w") as f:
                json.dump(bridge_data, f, indent=2)
            self.logger.info(f"Bridge updated: Status={status}")
        except Exception as e:
            self.logger.error(f"Failed to update task bridge JSON file: {e}")

    async def run_loop(self) -> None:
        """The core polling loop acting as the Nervous System connecting Dev 1 commands to browser execution."""
        self.logger.info("Ready. Entering task_bridge.json loop.")
        safety_words = ["submit", "pay", "delete", "confirm"]

        while self._is_running:
            await asyncio.sleep(1)
            
            try:
                with open(self.bridge_file, "r") as f:
                    bridge_data = json.load(f)
            except Exception as e:
                self.logger.error(f"Error reading bridge file: {e}")
                continue
                
            status = bridge_data.get("status")
            
            if status == "pending":
                action_type = bridge_data.get("action_type", "")
                target = bridge_data.get("target", "")
                text = bridge_data.get("text", "")
                scroll_amount = bridge_data.get("scroll_amount", 500)
                selector_type = bridge_data.get("selector_type", "vision")
                
                self.logger.info(f"Received new task: [Action: {action_type}] [Selector: {selector_type}] [Target: {target}]")
                
                # Safety Gate
                if action_type == "click" and any(word in target.lower() for word in safety_words):
                    self.logger.info(f"[SAFETY] Gate triggered. Target '{target}' requires approval.")
                    self._update_bridge(bridge_data, "awaiting_approval")
                    continue

                # Execution Block  
                try:
                    await self._execute_action(action_type, target, text, scroll_amount, selector_type)
                    self._update_bridge(bridge_data, "completed")
                except Exception as e:
                    error_str = str(e)
                    self.logger.error(f"Task Execution Error: {error_str}")
                    self._update_bridge(bridge_data, "failed", error=error_str)
                    
            elif status == "WAIT_FOR_HUMAN":
                # Special Heartbeat: CAPTCHA detected, wait for human intervention
                self.logger.info("[HITL] CAPTCHA Detected. Pausing for human solve...")
                try:
                    await self.page.bring_to_front()
                except:
                    pass

                # Pulse during wait: refresh viewport every 2s
                while True:
                    await asyncio.sleep(2)
                    await self._screenshot_pulse()
                    
                    # Check bridge again to see if status changed from WAIT_FOR_HUMAN
                    try:
                        with open(self.bridge_file, "r") as f:
                            current_bridge = json.load(f)
                        if current_bridge.get("status") != "WAIT_FOR_HUMAN":
                            self.logger.info("[HITL] Mission resumed by human.")
                            break
                    except:
                        continue
                
            elif status == "awaiting_approval":
                pass # Wait for manual UI toggle
                
    async def shutdown(self) -> None:
        """Gracefully closes resources upon termination."""
        self.logger.info("Executing graceful shutdown...")
        self._is_running = False
        
        if self.context:
            await self.context.close()
            
        if self.browser:
            await self.browser.close()
            
        if self.playwright:
            await self.playwright.stop()
            
        self.logger.info("VantageEngine terminated smoothly.")
