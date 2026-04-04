import os
import json
import logging
import time
from typing import List

from dotenv import load_dotenv
from google import genai
from google.genai import types

from logic.schema import PlanSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Waterfall model tier - tried in order on 429 / exhaustion
# ---------------------------------------------------------------------------
_PLANNER_MODELS = [
    "gemini-3.1-pro-preview",   # Primary
    "gemini-2.5-pro",           # Tier 2
    "gemini-2.5-flash",         # Tier 3
    "gemini-1.5-flash",         # Emergency fallback
]

_RETRY_SLEEP_S   = 5    # Pause between model switches (rate-limiter breath)
_COOLDOWN_SLEEP_S = 60  # Hard cooldown if ALL models exhausted

_SYSTEM_INSTRUCTION = """
You are the Strategic Planner inside Vantage-OS, an autonomous browser-control AI.

Given a user goal, produce EXACTLY 5 ordered, self-contained browser-automation steps.

Rules:
- Each step must describe ONE distinct browser action or observation.
- Steps must be sequential - earlier steps enable later ones.
- Be specific: name the site, element, or URL where possible.
- Output ONLY valid JSON - no markdown, no explanation, nothing else:
  {"steps": ["step 1", "step 2", "step 3", "step 4", "step 5"]}
"""


def _is_rate_limit_error(e: Exception) -> bool:
    """Return True if `e` is a 429 RESOURCE_EXHAUSTED error."""
    # google.genai ClientError exposes .code (int HTTP status)
    code = getattr(e, "code", None)
    if code == 429:
        return True
    # Fallback: string inspection for environments where .code is absent
    return "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)


class Planner:
    """
    Strategic Planner - uses Gemini 3.1 Pro Preview (with waterfall fallback)
    to decompose a user goal into exactly 5 browser-automation steps.

    Waterfall tier (in order):
        gemini-3.1-pro-preview -> gemini-2.5-pro -> gemini-2.5-flash -> gemini-1.5-flash
    """

    def __init__(self) -> None:
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or api_key.strip() == "your_api_key_here":
            raise RuntimeError("GEMINI_API_KEY is missing or invalid in .env")
        self.client = genai.Client(api_key=api_key)
        logger.info(f"[Planner] Initialized - primary model: {_PLANNER_MODELS[0]}")

    def _call_model(self, model_name: str, prompt: str) -> str:
        """Call a single model and return raw response text."""
        response = self.client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        return response.text.strip()

    def create_plan(self, user_goal: str) -> List[str]:
        """
        Decompose `user_goal` into an ordered list of 5 steps.

        Implements a waterfall fallback across _PLANNER_MODELS on 429 errors,
        with a 60-second hard cooldown if every model is exhausted.

        Args:
            user_goal: Natural-language description of the end objective.

        Returns:
            List[str] of 5 step strings (validated via PlanSchema).

        Raises:
            RuntimeError: If all fallback models fail with non-429 errors,
                          or if the response cannot be parsed after recovery.
        """
        prompt = (
            f"User Goal: {user_goal}\n\n"
            "Produce an ordered 5-step browser automation plan as JSON."
        )
        logger.info(f"[Planner] Creating plan for: '{user_goal}'")

        last_exception: Exception = RuntimeError("No models attempted.")

        # -- First pass: waterfall through all models ----------------------
        for model_name in _PLANNER_MODELS:
            try:
                logger.info(f"[Planner] Trying model: {model_name}")
                raw = self._call_model(model_name, prompt)
                logger.info(f"[Planner] Response from {model_name} ({len(raw)} chars)")
                return self._parse(raw)

            except Exception as e:
                if _is_rate_limit_error(e):
                    logger.warning(
                        f"[RECOVERY] {model_name} exhausted (429). "
                        f"Trying next model in {_RETRY_SLEEP_S}s..."
                    )
                    last_exception = e
                    time.sleep(_RETRY_SLEEP_S)
                    continue
                else:
                    # Non-quota error - re-raise immediately
                    raise RuntimeError(
                        f"[Planner] Non-quota error on {model_name}: {e}"
                    ) from e

        # -- All models exhausted -> Hard Cooldown --------------------------
        print(
            "\n[QUOTA] All Gemini models are exhausted. "
            f"Sleeping for {_COOLDOWN_SLEEP_S}s to reset quota...\n"
        )
        logger.warning(
            f"[QUOTA] All Planner models exhausted. "
            f"Hard cooldown for {_COOLDOWN_SLEEP_S}s before last attempt."
        )
        time.sleep(_COOLDOWN_SLEEP_S)

        # -- Final attempt with the most capable fallback ------------------
        last_model = _PLANNER_MODELS[-1]
        logger.info(f"[Planner] Final attempt with: {last_model}")
        try:
            raw = self._call_model(last_model, prompt)
            return self._parse(raw)
        except Exception as e:
            raise RuntimeError(
                f"[Planner] All models failed. Last error: {e}"
            ) from e

    def _parse(self, raw: str) -> List[str]:
        """Parse raw JSON response into a validated list of steps."""
        try:
            data = json.loads(raw)
            plan = PlanSchema(**data)
        except Exception as e:
            raise RuntimeError(
                f"[Planner] Could not parse response into PlanSchema: {e}\nRaw: {raw}"
            ) from e

        logger.info(f"[Planner] Plan validated - {len(plan.steps)} steps:")
        for i, step in enumerate(plan.steps, 1):
            logger.info(f"  {i}. {step}")

        return plan.steps

