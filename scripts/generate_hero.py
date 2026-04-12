#!/usr/bin/env python3
"""
scripts/generate_hero.py

Generates a hero header image for the discussion guide.
Uses a two-pass approach:
1. A text model extracts a single visual metaphor from the guide content.
2. An image model renders that metaphor in a specific, dark, premium style.

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


def extract_visual_concept(content_summary, api_key):
    """Uses a text model to isolate a single visual metaphor."""
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system", 
                "content": "You are an expert art director. Read the provided text and extract exactly ONE concrete, highly visual object or metaphor that represents the core theme. Respond ONLY with that object (e.g., 'A shattered clay jar', 'An open doorway', 'A single glowing ember'). Do not include any other text."
            },
            {"role": "user", "content": content_summary}
        ],
        temperature=0.7
    )
    return response.choices[0].message.content.strip()


def build_image_prompt(visual_concept):
    """Creates the exact art direction prompt based on the isolated concept."""
    return (
        f"A cinematic, high-fidelity fine art rendering of: {visual_concept}.\n\n"
        "ART DIRECTION & STYLE REQUIREMENTS (CRITICAL):\n"
        "- The object must be the absolute central focal point.\n"
        "- Rendered abstractly or atmospherically, not as a photorealistic product shot.\n"
        "- NO text, NO words, NO letters, NO numbers.\n"
        "- NO people, NO faces, NO hands.\n"
        "- Palette: Deep navy/charcoal background (#0d0f13 to #15171d), cool steel blue accents, muted teal, and sparse warm gold highlights.\n"
        "- Cinematic composition with strong horizontal flow.\n"
        "- Subtle film grain or painterly texture.\n"
        "- Minimalist, elegant, and contemplative mood."
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

    try:
        print("Extracting visual concept...")
        visual_concept = extract_visual_concept(content, api_key)
        print(f"Concept isolated: {visual_concept}")

        prompt = build_image_prompt(visual_concept)
        print("Generating hero image...")
        generate_image(prompt, api_key, args.out)

    except Exception as e:
        print("Hero image generation failed (non-fatal): {}".format(e),
              file=sys.stderr)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
