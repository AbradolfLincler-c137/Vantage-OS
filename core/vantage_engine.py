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
import time
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
        self.last_mouse_x = 640
        self.last_mouse_y = 360

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
        """Adds ±5px random jitter to the target coordinates."""
        if not self.page: return
        jitter_x = x + random.randint(-5, 5)
        jitter_y = y + random.randint(-5, 5)
        self.logger.info(f"Human-click target ({x}, {y}) -> Jitter ({jitter_x}, {jitter_y})")
        await self.page.mouse.click(jitter_x, jitter_y, delay=random.randint(150, 300))

    async def _human_move_bezier(self, target_x: int, target_y: int) -> None:
        """Moves the mouse to the target coordinates using a quadratic Bezier curve over 300-500ms."""
        if not self.page: return

        start_x, start_y = getattr(self, "last_mouse_x", 640), getattr(self, "last_mouse_y", 360)
        
        # Determine a random control point roughly between start and end but offset
        mid_x = (start_x + target_x) / 2
        mid_y = (start_y + target_y) / 2
        offset_x = random.randint(-150, 150)
        offset_y = random.randint(-150, 150)
        cp_x = max(0, min(1280, mid_x + offset_x))
        cp_y = max(0, min(720, mid_y + offset_y))

        steps = random.randint(10, 15)
        total_time = random.uniform(0.300, 0.500)
        sleep_time = total_time / steps

        self.logger.info(f"Human-move Bezier: ({start_x},{start_y}) -> ({target_x},{target_y}) via ({cp_x},{cp_y}) in {steps} steps.")

        for i in range(1, steps + 1):
            t = i / steps
            x = (1 - t)**2 * start_x + 2 * (1 - t) * t * cp_x + t**2 * target_x
            y = (1 - t)**2 * start_y + 2 * (1 - t) * t * cp_y + t**2 * target_y
            await self.page.mouse.move(x, y)
            await asyncio.sleep(sleep_time)
            
        self.last_mouse_x = target_x
        self.last_mouse_y = target_y

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

    async def _vantage_command_callback(self, source, command: str) -> None:
        """Called directly from the Browser UI when the user presses Enter."""
        self.logger.info(f"Received UI Command from Browser: {command}")
        bridge_payload = {
            "task_id": str(int(time.time())),
            "status": "new_mission", 
            "mission": command,
            "action_type": "",
            "selector_type": "",
            "target": "",
            "text": "",
            "scroll_amount": 0,
            "error": ""
        }
        try:
            with open(self.bridge_file, "w") as f:
                json.dump(bridge_payload, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to write UI command to bridge: {e}")

    def _get_ui_script(self) -> str:
        return """
        window.addEventListener("DOMContentLoaded", () => {
            if (document.getElementById('vantage-panel')) return;
            
            const panel = document.createElement('div');
            panel.id = 'vantage-panel';
            Object.assign(panel.style, {
                position: 'fixed', top: '0', right: '0', width: '300px', height: '100vh',
                backgroundColor: 'rgba(18, 18, 18, 0.85)', backdropFilter: 'blur(10px)',
                borderLeft: '1px solid #00f3ff', zIndex: '2147483647', display: 'flex',
                flexDirection: 'column', fontFamily: 'Segoe UI, sans-serif', color: '#fff',
                boxShadow: '-4px 0 15px rgba(0, 243, 255, 0.1)', overflow: 'hidden'
            });

            const header = document.createElement('div');
            Object.assign(header.style, {
                padding: '16px', borderBottom: '1px solid rgba(0, 243, 255, 0.3)',
                fontWeight: 'bold', fontSize: '18px', letterSpacing: '1px',
                color: '#00f3ff', textTransform: 'uppercase', display: 'flex',
                alignItems: 'center', gap: '8px', justifyContent: 'space-between'
            });
            header.innerHTML = `
                <div style="display:flex;align-items:center;gap:8px;">
                    <span style="display:inline-block;width:8px;height:8px;background:#00f3ff;border-radius:50%;box-shadow:0 0 8px #00f3ff;"></span> VANTAGE-OS
                </div>
                <div id="vantage-brain-badge" style="font-size:10px;background:rgba(0,243,255,0.1);border:1px solid rgba(0,243,255,0.5);border-radius:12px;padding:4px 8px;white-space:nowrap;color:#00f3ff;">
                    [BRAIN: IDLE]
                </div>
            `;
            panel.appendChild(header);

            const logArea = document.createElement('div');
            logArea.id = 'vantage-thought-log';
            Object.assign(logArea.style, {
                flex: '1', padding: '16px', overflowY: 'auto', fontSize: '13px',
                lineHeight: '1.5', color: '#ccc', display: 'flex', flexDirection: 'column', gap: '8px'
            });
            panel.appendChild(logArea);

            const inputArea = document.createElement('div');
            Object.assign(inputArea.style, {
                padding: '16px', borderTop: '1px solid rgba(0, 243, 255, 0.3)', background: 'rgba(0,0,0,0.4)'
            });
            
            const input = document.createElement('input');
            Object.assign(input.style, {
                width: '100%', padding: '10px 12px', background: 'rgba(255, 255, 255, 0.05)',
                border: '1px solid rgba(0, 243, 255, 0.5)', borderRadius: '4px',
                color: '#fff', outline: 'none', fontSize: '14px', boxSizing: 'border-box'
            });
            input.placeholder = 'Enter Mission...';
            
            input.addEventListener('keydown', async (e) => {
                if (e.key === 'Enter' && input.value.trim()) {
                    const cmd = input.value.trim();
                    if(window.addVantageLog) window.addVantageLog('USER: ' + cmd);
                    input.value = '';
                    input.disabled = true;
                    input.placeholder = 'Dispatching...';
                    
                    try {
                        await window.vantage_command_submit(cmd);
                    } catch (err) {
                        if(window.addVantageLog) window.addVantageLog('Error: ' + err.message);
                    }
                    
                    input.disabled = false;
                    input.placeholder = 'Enter Mission...';
                    input.focus();
                }
            });

            inputArea.appendChild(input);
            panel.appendChild(inputArea);

            window.addVantageLog = function(msg) {
                const entry = document.createElement('div');
                entry.textContent = msg;
                if (msg.startsWith('USER:')) {
                    entry.style.color = '#00f3ff';
                    entry.style.fontWeight = 'bold';
                } else {
                    entry.style.borderLeft = '2px solid rgba(255,255,255,0.2)';
                    entry.style.paddingLeft = '8px';
                }
                logArea.appendChild(entry);
                logArea.scrollTop = logArea.scrollHeight;
            };

            window.updateVantageBrain = function(modelName, status) {
                const badge = document.getElementById('vantage-brain-badge');
                if (badge) {
                    badge.innerHTML = `[BRAIN: ${modelName}] ${status}`;
                    badge.style.backgroundColor = 'rgba(0,243,255,0.4)';
                    setTimeout(() => badge.style.backgroundColor = 'rgba(0,243,255,0.1)', 300);
                }
            };

            document.body.appendChild(panel);
        });
        """

    async def setup_browser(self, start_url: str = "about:blank") -> None:
        """Initializes the standard Playwright browser and navigates to the start URL."""
        self.logger.info("Launching Playwright Chromium (Persistent Context + Stealth)...")
        self.playwright = await async_playwright().start()
        
        print("[ENGINE] Attempting persistent launch...")
        user_data_dir = os.path.join(os.getcwd(), "vantage_profile")
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel="msedge",
                headless=False,
                no_viewport=True,
                args=["--start-maximized", "--no-sandbox"]
            )
        except Exception as e:
            err_msg = str(e)
            if "closed" in err_msg or "locked" in err_msg.lower() or "EBUSY" in err_msg:
                print("\n[!!!] BROWSER ALREADY OPEN ERROR [!!!]")
                print("Please close all Edge windows and restart Vantage-OS.")
                sys.exit(1)
            else:
                raise e

        print("[ENGINE] Browser visible.")
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

        # Expose Python command hook to the Browser and inject the UI
        await self.context.expose_binding("vantage_command_submit", self._vantage_command_callback)
        await self.context.add_init_script(self._get_ui_script())

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

    async def _extract_simplified_dom(self) -> list:
        """Extracts a simplified DOM containing only clickable elements, links, and inputs."""
        if not self.page: return []
        script = """
        () => {
            const elements = document.querySelectorAll('a, button, input, [role="button"]');
            const result = [];
            elements.forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    result.push({
                        text: el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '',
                        x: rect.x + rect.width / 2,
                        y: rect.y + rect.height / 2
                    });
                }
            });
            return result;
        }
        """
        try:
            return await self.page.evaluate(script)
        except Exception as e:
            self.logger.error(f"Failed to extract simplified DOM: {e}")
            return []

    async def _execute_action(self, action_type: str, target: str, text: str, scroll_amount: int, selector_type: str = "vision", bridge_data: dict = None) -> None:
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
                        await self._human_move_bezier(center_x, center_y)
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
                found_in_dom = False
                
                if action_type in ["click", "type"]:
                    self.logger.info("Falling back from exact locators. Trying Hybrid DOM Logic first...")
                    dom_elements = await self._extract_simplified_dom()
                    target_lower = target.lower()
                    best_match = None
                    for el in dom_elements:
                        if target_lower in el.get("text", "").lower():
                            best_match = el
                            break
                    
                    if best_match:
                        found_in_dom = True
                        x, y = int(best_match['x']), int(best_match['y'])
                        self.logger.info(f"Target '{target}' found in Simplified DOM at ({x}, {y}). Hybrid execution!")
                        await self._human_move_bezier(x, y)
                        await asyncio.sleep(random.uniform(0.5, 1.2))
                        
                        if action_type == "click":
                            await self._human_click(x, y)
                        elif action_type == "type":
                            await self._human_click(x, y)
                            await self._human_type(text)
                            await self.page.keyboard.press("Enter")

                if not found_in_dom:
                    self.logger.info("Hybrid DOM logic failed. Triggering standard Vision API upload...")
                    if bridge_data:
                        self._update_bridge(bridge_data, "uploading")
                    coords = await self._capture_and_locate(target)
                    if coords:
                        x, y = coords
                        
                        # Human-Move
                        await self._human_move_bezier(x, y)
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
        last_action_time = time.time()

        while self._is_running:
            await asyncio.sleep(0.3)
            
            # Fast-Path Ad Skipper
            if self.page:
                try:
                    skip_btn = self.page.locator("button.ytp-skip-ad-button")
                    if await skip_btn.is_visible():
                        await skip_btn.click(timeout=1000)
                        self.logger.info("Fast-Path: Auto-clicked 'Skip Ad'!")
                        await self.page.evaluate("if(window.addVantageLog) window.addVantageLog('Fast-Path: Auto-clicked Skip Ad!');")
                except Exception:
                    pass
            
            try:
                with open(self.bridge_file, "r") as f:
                    bridge_data = json.load(f)
            except Exception as e:
                self.logger.error(f"Error reading bridge file: {e}")
                continue
                
            status = bridge_data.get("status")
            active_brain = bridge_data.get("active_brain")
            brain_status = bridge_data.get("brain_status")
            
            # Poll Brain Status for UI Updates dynamically
            if active_brain and brain_status:
                if getattr(self, "last_brain_state", None) != (active_brain, brain_status):
                    self.last_brain_state = (active_brain, brain_status)
                    try:
                        if self.page:
                            await self.page.evaluate(
                                f"if(window.updateVantageBrain) window.updateVantageBrain('{active_brain}', '{brain_status}');"
                            )
                    except Exception:
                        pass
            
            if status == "pending":
                action_type = bridge_data.get("action_type", "")
                target = bridge_data.get("target", "")
                text = bridge_data.get("text", "")
                scroll_amount = bridge_data.get("scroll_amount", 500)
                selector_type = bridge_data.get("selector_type", "vision")
                
                self.logger.info(f"Received new task: [Action: {action_type}] [Selector: {selector_type}] [Target: {target}]")
                
                try:
                    if self.page:
                        safe_target = target.replace('`', '').replace("'", "")
                        await self.page.evaluate(f"if(window.addVantageLog) window.addVantageLog('AI: {action_type} -> {safe_target}');")
                except Exception:
                    pass
                
                # Safety Gate
                if action_type == "click" and any(word in target.lower() for word in safety_words):
                    self.logger.info(f"[SAFETY] Gate triggered. Target '{target}' requires approval.")
                    self._update_bridge(bridge_data, "awaiting_approval")
                    continue

                # Execution Block  
                try:
                    self._update_bridge(bridge_data, "executing")
                    await self._execute_action(action_type, target, text, scroll_amount, selector_type, bridge_data)
                    self._update_bridge(bridge_data, "completed")
                    last_action_time = time.time()
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
            else:
                # Micro-Jitter while idle
                if time.time() - last_action_time > 3.0:
                    if self.page:
                        try:
                            self.last_mouse_x += random.choice([-2, -1, 1, 2])
                            self.last_mouse_y += random.choice([-2, -1, 1, 2])
                            # Keep within typical 720p bounds
                            self.last_mouse_x = max(0, min(self.last_mouse_x, 1280))
                            self.last_mouse_y = max(0, min(self.last_mouse_y, 720))
                            await self.page.mouse.move(self.last_mouse_x, self.last_mouse_y)
                            last_action_time = time.time() - 2 # Jitter every 1 second after initial 3s wait
                            self.logger.info(f"Micro-jitter to ({self.last_mouse_x}, {self.last_mouse_y})")
                        except:
                            pass
                
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
