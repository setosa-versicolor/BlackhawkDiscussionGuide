#!/usr/bin/env python3
“””
scripts/generate_hero.py

Generates a hero header image for the discussion guide using OpenAI’s
gpt-image-1 model. Reads data/guide.json (or the specified path) to
extract sermon themes, then produces a 1536x640 abstract image that
matches the app’s dark color palette.

Usage:
python scripts/generate_hero.py                        # default paths
python scripts/generate_hero.py –json site/data/guide.json –out site/data/hero.webp

Requires:
pip install openai
OPENAI_API_KEY environment variable

If OPENAI_API_KEY is not set, the script exits silently (no error),
so CI builds still succeed without an API key.
“””

import argparse
import base64
import json
import os
import sys
from pathlib import Path

def summarize_content(guide: dict) -> str:
“”“Build a short thematic summary from the guide JSON.”””
questions = guide.get(“questions”, [])
sections = guide.get(“sections”, [])

```
# Combine all text for thematic extraction
all_text = "\n".join(questions)
for s in sections:
    all_text += f"\n{s.get('title', '')}: {s.get('body', '')}"

# Truncate to keep the prompt reasonable
if len(all_text) > 1200:
    all_text = all_text[:1200] + "…"

return all_text
```

def build_prompt(content_summary: str) -> str:
“”“Create the image generation prompt.”””
return f””“Create an abstract, atmospheric landscape illustration for a church discussion guide header image.

THEME derived from this week’s content:
{content_summary}

STYLE REQUIREMENTS (CRITICAL — follow exactly):

- Abstract and evocative, NOT literal depictions of Bible scenes or people
- NO text, NO words, NO letters, NO numbers anywhere in the image
- NO people, NO faces, NO hands, NO human figures
- Use dark, moody tones that match this exact palette:
  - Deep navy/charcoal background (#0d0f13 to #15171d)
  - Cool steel blue accents (#5b8def)
  - Muted teal (#3dae8e)
  - Warm amber/gold highlights (#d4943a) used sparingly
- Think: dramatic landscapes, abstract light, flowing water, atmospheric depth,
  geometric patterns in nature, vast skies, layered horizons
- Cinematic composition with strong horizontal flow (image is very wide)
- Subtle film grain or painterly texture
- Minimalist and contemplative mood — elegant restraint, not busy

The image should feel like a premium editorial magazine header — atmospheric,
beautiful, and thought-provoking without being explicitly religious.”””

def generate_image(prompt: str, api_key: str, output_path: str):
“”“Call OpenAI image generation and save the result.”””
from openai import OpenAI

```
client = OpenAI(api_key=api_key)

response = client.images.generate(
    model="gpt-image-1",
    prompt=prompt,
    n=1,
    size="1536x1024",   # closest wide ratio available
    quality="medium",
)

# gpt-image-1 returns base64
image_b64 = response.data[0].b64_json
if not image_b64:
    # Fallback: might be a URL
    import requests
    url = response.data[0].url
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    image_bytes = r.content
else:
    image_bytes = base64.b64decode(image_b64)

Path(output_path).parent.mkdir(parents=True, exist_ok=True)
Path(output_path).write_bytes(image_bytes)
print(f"Hero image saved to {output_path} ({len(image_bytes)} bytes)")
```

def main():
ap = argparse.ArgumentParser(description=“Generate weekly hero image”)
ap.add_argument(”–json”, default=“site/data/guide.json”,
help=“Path to guide.json”)
ap.add_argument(”–out”, default=“site/data/hero.webp”,
help=“Output image path”)
args = ap.parse_args()

```
api_key = os.environ.get("OPENAI_API_KEY", "").strip()
if not api_key:
    print("OPENAI_API_KEY not set — skipping hero image generation.")
    return 0

json_path = Path(args.json)
if not json_path.exists():
    print(f"Guide JSON not found at {json_path} — skipping hero image.")
    return 0

guide = json.loads(json_path.read_text(encoding="utf-8"))
content = summarize_content(guide)

if not content.strip():
    print("No content in guide.json — skipping hero image.")
    return 0

prompt = build_prompt(content)
print(f"Generating hero image from {len(guide.get('questions', []))} questions…")

try:
    generate_image(prompt, api_key, args.out)
except Exception as e:
    print(f"Hero image generation failed (non-fatal): {e}", file=sys.stderr)
    return 0  # Don't break the build

return 0
```

if **name** == “**main**”:
sys.exit(main())
