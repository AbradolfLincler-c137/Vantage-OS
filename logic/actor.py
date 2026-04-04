import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
import json
import logging
import time
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image

from logic.schema import ActionSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Waterfall model tier - tried in order on 429 / exhaustion
# ---------------------------------------------------------------------------
_ACTOR_MODELS = [
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash",
    "gemini-1.5-flash"
]

_RETRY_SLEEP_S    = 1.0    # Pause between model switches
_COOLDOWN_SLEEP_S = 60   # Hard cooldown if ALL models exhausted

_SYSTEM_INSTRUCTION = """
You are an Elite Navigator inside Vantage-OS. You guide complex autonomous browser missions with surgical precision.

DETECTION RULE (CRITICAL):
  If the screenshot shows a Google CAPTCHA ("Are you a robot?"), a "Verify you are human" checkbox, a cloudflare challenge, or a "Blocked" page, you MUST STOP IMMEDIATELY.
  Set action_type="WAIT_FOR_HUMAN" and thought="CAPTCHA detected. I need Paras to solve this."

Available action_type values:
  "click"          - Click a UI element; set target_description and selector_type.
  "type"           - Type into a focused field; set text, target_description, and selector_type.
  "scroll"         - Scroll the page; set scroll_amount (positive=down, negative=up).
  "navigate"       - Go to a URL; put the full URL in target_description.
  "wait"           - Wait for something; describe it in target_description.
  "WAIT_FOR_HUMAN" - Special state: CAPTCHA detected. The mission pauses for a human solve.
  "done"           - This step is fully complete. Use ONLY when the step is achieved.
  
STATE-AWARE NAVIGATION RULE:
  - If current_url already matches the target of a navigation step, you MUST return action_type="done" immediately. Do NOT attempt to navigate again.

SELECTOR_TYPE STRATEGY:
  - "vision": Vision-based coordinate identification.
  - "text":   Visible text match.
  - "css":    CSS selector or class (e.g. "input[type='search']").
  - "id":     ID (e.g. "search-box").
  - "xpath":  Structural paths.

MULTI-LINGUAL / UNIVERSAL NAVIGATION:
  - Translate intent to context. Identify "ಹುಡುಕಿ" as Search.
  - Look for aria-label, placeholder, or title.

SELF-HEALING RULE:
  If LAST_ERROR is present, re-examine. Choose a COMPLETELY DIFFERENT approach.

Output ONLY valid JSON - no markdown:
{
  "thought": "...",
  "action_type": "...",
  "selector_type": "text | css | id | vision",
  "target_description": "...",
  "text": "",
  "scroll_amount": 0
}
"""







class Actor:
    """
    Tactical Actor - uses Gemini 3.1 Flash Lite Preview (with waterfall fallback)
    to decide the next atomic browser action from a viewport screenshot.

    Waterfall tier (in order):
        gemini-3.1-flash-lite-preview -> gemini-2.5-flash -> gemini-1.5-flash
    """

    def __init__(self) -> None:
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or api_key.strip() == "your_api_key_here":
            raise RuntimeError("GEMINI_API_KEY is missing or invalid in .env")
        self.client = genai.Client(api_key=api_key)
        logger.info(f"[Actor] Initialized - primary model: {_ACTOR_MODELS[0]}")

    def _call_model(self, model_name: str, prompt: str, image: Image.Image) -> str:
        """Call a single model with vision content and return raw response text."""
        response = self.client.models.generate_content(
            model=model_name,
            contents=[prompt, image],
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        return response.text.strip()

    def determine_action(
        self,
        goal: str,
        full_plan: list[str],
        current_step_idx: int,
        current_url: str,
        last_error: Optional[str] = None,
        thought_history: Optional[list[str]] = None
    ) -> ActionSchema:
        """
        Decide the next atomic action given viewport and context.

        Implements a waterfall fallback across _ACTOR_MODELS on 429 errors,
        with a 60-second hard cooldown if every model is exhausted.

        Args:
            goal:         The overall mission goal (for broader context).
            current_step: The specific step currently being executed.
            last_error:   Error string from the previous failed attempt, or None.
                          When set, self-healing mode is activated in the prompt.

        Returns:
            A validated ActionSchema.

        Raises:
            FileNotFoundError: If viewport.png does not exist in the project root.
            RuntimeError:      If all fallback models fail.
        """
        # Resolve viewport.png to absolute path
        project_root  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        viewport_path = os.path.join(project_root, "viewport.png")

        if not os.path.exists(viewport_path):
            raise FileNotFoundError(
                f"[Actor] viewport.png not found at '{viewport_path}'. "
                "The VantageEngine must capture a screenshot first."
            )

        # Build prompt --------------------------------------------------------
        current_step = full_plan[current_step_idx] if current_step_idx < len(full_plan) else "FINISHING"
        thought_str = "\n".join([f" - {t}" for t in thought_history]) if thought_history else "None"
        
        parts = [
            f"Overall Mission: {goal}",
            f"Current URL:     {current_url}",
            f"Prior Thoughts:\n{thought_str}",
            f"Full Plan:        {json.dumps(full_plan, indent=2)}",
            f"Current Step Index: {current_step_idx}",
            f"Current Step Target: {current_step}",
        ]
        if last_error:
            parts.append(
                f"\nLAST_ERROR - SELF-HEALING MODE ACTIVE:\n"
                f"  The previous action FAILED with this error:\n"
                f"  \"{last_error}\"\n"
                f"  You MUST pick a COMPLETELY DIFFERENT element or approach. "
                f"  Study the screenshot carefully. Do NOT retry the same target."
            )
        parts.append("\nAnalyze the screenshot and output the single best next action as JSON.")
        prompt = "\n".join(parts)

        logger.info(
            f"[Actor] Deciding action - step='{current_step}'"
            + (f" | healing from: '{last_error}'" if last_error else "")
        )

        image = Image.open(viewport_path)
        last_exception: Exception = RuntimeError("No models attempted.")

        for model_name in _ACTOR_MODELS:
            try:
                # Update Bridge directly through CEO passing the state, but Actor logs the attempt
                try:
                    bridge_path = os.path.join(project_root, "task_bridge.json")
                    with open(bridge_path, "r") as f:
                        bridge_data = json.load(f)
                    bridge_data["active_brain"] = f"ACTOR: {model_name.replace('gemini-', '')}"
                    bridge_data["brain_status"] = f"Determining Step {current_step_idx}..."
                    with open(bridge_path, "w") as f:
                        json.dump(bridge_data, f, indent=2)
                except Exception: pass
                
                logger.info(f"[Actor] Trying model: {model_name}")
                raw = self._call_model(model_name, prompt, image)
                logger.info(f"[Actor] Response from {model_name} ({len(raw)} chars)")
                
                # If we suceed, update main script
                self.successful_model = model_name
                return self._parse(raw)

            except Exception as e:
                logger.warning(
                    f"[RECOVERY] {model_name} failed (429/Safety/NotFound): {e}. "
                    f"Trying next model in {_RETRY_SLEEP_S}s..."
                )
                last_exception = e
                time.sleep(_RETRY_SLEEP_S)
                continue

        # -- All models exhausted -> Hard Cooldown --------------------------
        print(
            "\n[QUOTA] All Gemini models are exhausted. "
            f"Sleeping for {_COOLDOWN_SLEEP_S}s to reset quota...\n"
        )
        logger.warning(
            f"[QUOTA] All Actor models exhausted. "
            f"Hard cooldown for {_COOLDOWN_SLEEP_S}s before last attempt."
        )
        time.sleep(_COOLDOWN_SLEEP_S)

        # -- Final attempt with the most capable fallback ------------------
        last_model = _ACTOR_MODELS[-1]
        logger.info(f"[Actor] Final attempt with: {last_model}")
        try:
            raw = self._call_model(last_model, prompt, image)
            return self._parse(raw)
        except Exception as e:
            raise RuntimeError(
                f"[Actor] All models failed. Last error: {e}"
            ) from e

    def _parse(self, raw: str) -> ActionSchema:
        """Parse raw JSON response into a validated ActionSchema."""
        try:
            data   = json.loads(raw)
            action = ActionSchema(**data)
        except Exception as e:
            raise RuntimeError(
                f"[Actor] Could not parse response into ActionSchema: {e}\nRaw: {raw}"
            ) from e

        logger.info(
            f"[Actor] -> action='{action.action_type}' | "
            f"selector='{action.selector_type}' | "
            f"target='{action.target_description}'"
        )
        return action

