#!/usr/bin/env python3
"""
scripts/generate_hero.py

Generates a hero header image for the discussion guide using OpenAI's
gpt-image-1 model. Reads guide.json to extract sermon themes, then
produces an abstract image that matches the app's dark color palette.

Usage:
    python scripts/generate_hero.py
    python scripts/generate_hero.py --json site/data/guide.json --out site/data/hero.webp

Requires:
    pip install openai
    OPENAI_API_KEY environment variable

If OPENAI_API_KEY is not set, the script exits silently so CI builds
still succeed without an API key.
"""

import argparse
import base64
import json
import os
import sys
from pathlib import Path


def summarize_content(guide):
    """Build a short thematic summary from the guide JSON."""
    questions = guide.get("questions", [])
    sections = guide.get("sections", [])

    all_text = "\n".join(questions)
    for s in sections:
        all_text += "\n" + s.get("title", "") + ": " + s.get("body", "")

    if len(all_text) > 1200:
        all_text = all_text[:1200] + "..."

    return all_text


def build_prompt(content_summary):
    """Create the image generation prompt."""
    return (
        "Create an abstract, atmospheric landscape illustration for a "
        "church discussion guide header image.\n\n"
        "THEME derived from this week's content:\n"
        + content_summary + "\n\n"
        "STYLE REQUIREMENTS (CRITICAL -- follow exactly):\n"
        "- Evocative and thematic, NOT literal depictions of Bible scenes or people\n"
        "- NO text, NO words, NO letters, NO numbers anywhere in the image\n"
        "- NO people, NO faces, NO hands, NO human figures\n"
        "- Use dark, moody tones that match this exact palette:\n"
        "  - Deep navy/charcoal background (#0d0f13 to #15171d)\n"
        "  - Cool steel blue accents (#5b8def)\n"
        "  - Muted teal (#3dae8e)\n"
        "  - Warm amber/gold highlights (#d4943a) used sparingly\n"
        "- Choose a single thematic visual element inspired by the content.\n"
        "  Examples of the kind of subject matter to consider: a loaf of bread,\n"
        "  a tree of life, a natural spring, a small boat on still water, a scale\n"
        "  (for justice), a winding path, a single flame, an open door, a clay vessel,\n"
        "  a vine with branches, parted waters, a shepherd's staff, a stone rolled away.\n"
        "  These are just examples -- pick whatever best fits the theme of the content.\n"
        "- The subject should be rendered abstractly or atmospherically, not as a\n"
        "  photorealistic product shot. It should feel like fine art, not clip art.\n"
        "- Dramatic landscapes and vast skies are also fine when they fit the theme.\n"
        "- Cinematic composition with strong horizontal flow (image is very wide)\n"
        "- Subtle film grain or painterly texture\n"
        "- Minimalist and contemplative mood -- elegant restraint, not busy\n\n"
        "The image should feel like a premium editorial magazine header -- atmospheric,\n"
        "beautiful, and thought-provoking without being explicitly religious."
    )


def generate_image(prompt, api_key, output_path):
    """Call OpenAI image generation and save the result."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    response = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        n=1,
        size="1536x1024",
        quality="medium",
    )

    image_b64 = response.data[0].b64_json
    if not image_b64:
        import requests as req
        url = response.data[0].url
        r = req.get(url, timeout=60)
        r.raise_for_status()
        image_bytes = r.content
    else:
        image_bytes = base64.b64decode(image_b64)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(image_bytes)
    print("Hero image saved to {} ({} bytes)".format(output_path, len(image_bytes)))


def main():
    ap = argparse.ArgumentParser(description="Generate weekly hero image")
    ap.add_argument("--json", default="site/data/guide.json",
                    help="Path to guide.json")
    ap.add_argument("--out", default="site/data/hero.webp",
                    help="Output image path")
    args = ap.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("OPENAI_API_KEY not set -- skipping hero image generation.")
        return 0

    json_path = Path(args.json)
    if not json_path.exists():
        print("Guide JSON not found at {} -- skipping hero image.".format(json_path))
        return 0

    guide = json.loads(json_path.read_text(encoding="utf-8"))
    content = summarize_content(guide)

    if not content.strip():
        print("No content in guide.json -- skipping hero image.")
        return 0

    prompt = build_prompt(content)
    print("Generating hero image from {} questions...".format(
        len(guide.get("questions", []))))

    try:
        generate_image(prompt, api_key, args.out)
    except Exception as e:
        print("Hero image generation failed (non-fatal): {}".format(e),
              file=sys.stderr)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
