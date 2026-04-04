from typing import List, Optional
from pydantic import BaseModel, Field


class ActionSchema(BaseModel):
    """Single atomic browser action decided by the Actor."""
    thought: str = Field(
        ...,
        description="The Actor's internal reasoning for why this is the correct next step."
    )
    action_type: str = Field(
        ...,
        description=(
            "Type of action. One of: 'click', 'type', 'scroll', "
            "'navigate', 'wait', 'done', 'WAIT_FOR_HUMAN'."
        )
    )
    target_description: str = Field(
        ...,
        description=(
            "Natural-language description of the UI element or URL to interact with. "
            "E.g. 'Google Search bar at the center of the page'."
        )
    )
    text: Optional[str] = Field(
        default="",
        description="Text to type (only relevant when action_type is 'type')."
    )
    scroll_amount: Optional[int] = Field(
        default=500,
        description="Pixel amount to scroll (only relevant when action_type is 'scroll')."
    )
    selector_type: str = Field(
        default="vision",
        description="Method to locate the target. One of: 'vision', 'css', 'id', 'text', 'xpath'."
    )



class PlanSchema(BaseModel):
    """High-level strategic plan produced by the Planner."""
    steps: List[str] = Field(
        ...,
        description="Ordered list of 5 high-level, human-readable steps to accomplish the goal."
    )
