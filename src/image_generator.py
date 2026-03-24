"""AI image generator — creates post images via NVIDIA Stable Diffusion when screenshots fail."""

import base64
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

NVIDIA_IMAGE_URL = "https://ai.api.nvidia.com/v1/genai/stabilityai/stable-diffusion-xl"
IMAGE_DIR = Path(__file__).parent.parent / "screenshots"


@dataclass
class GeneratedImage:
    """Result of AI image generation."""

    path: str
    prompt: str
    width: int = 1024
    height: int = 1024


def build_image_prompt(topic_category: str, topic_title: str, post_text: str = "") -> str:
    """Build an image generation prompt based on post topic and content.

    Creates a professional, LinkedIn-suitable image prompt.
    Uses post_text to make the image relate to the actual content.
    """
    category_styles = {
        "ai_news": "futuristic digital neural network visualization, blue and purple neon, tech aesthetic",
        "tech_talk": "modern code editor on dark background, terminal with green text, developer workspace",
        "ai_gadgets": "sleek modern AI hardware device, product photography, minimalist white background",
        "my_agent": "AI chatbot interface with data visualizations, dark theme dashboard, modern UI",
        "biohacker": "scientific laboratory with supplements and data charts, modern health tech aesthetic",
        "big_tech": "corporate tech campus with digital overlay, modern architecture, blue tones",
        "my_agent_git": "code diff visualization, git commit history, terminal with green text on dark background",
    }

    style = category_styles.get(topic_category, "modern technology concept, professional, minimal")

    # Use first 150 chars of post text for content-specific imagery
    content_hint = post_text[:150].replace("\n", " ").strip() if post_text else topic_title[:100]

    return f"{style}, about: {content_hint}, photorealistic, high quality, 4k, no text, no words"


async def generate_image(
    topic_category: str,
    topic_title: str,
    post_text: str = "",
    steps: int = 25,
) -> GeneratedImage | None:
    """Generate an image using NVIDIA Stable Diffusion XL.

    Returns GeneratedImage on success, None on failure.
    """
    api_key = os.getenv("NVIDIA_API_KEY", "")
    if not api_key:
        logger.warning("NVIDIA_API_KEY not set, cannot generate image")
        return None

    prompt = build_image_prompt(topic_category, topic_title, post_text)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                NVIDIA_IMAGE_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "text_prompts": [{"text": prompt}],
                    "steps": steps,
                    "width": 1024,
                    "height": 1024,
                    "cfg_scale": 7,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()

        artifacts = data.get("artifacts", [])
        if not artifacts:
            logger.warning("No image artifacts returned")
            return None

        image_bytes = base64.b64decode(artifacts[0]["base64"])

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{timestamp}-generated-{topic_category}.png"
        filepath = IMAGE_DIR / filename
        filepath.write_bytes(image_bytes)

        logger.info("Generated image saved: %s", filepath)
        return GeneratedImage(path=str(filepath), prompt=prompt)

    except Exception as e:
        logger.warning("Image generation failed: %s", e)
        return None
