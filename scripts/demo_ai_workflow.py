#!/usr/bin/env python3
"""Demo: AI Workflow Automation with Ollama.

This script demonstrates how to use local Ollama for:
1. Content classification
2. Summarization
3. Intelligent routing

Usage:
    python scripts/demo_ai_workflow.py
    python scripts/demo_ai_workflow.py --text "Your text here"
    python scripts/demo_ai_workflow.py --file document.txt
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:3b"

CLASSIFICATION_CATEGORIES = ["support", "sales", "urgent", "general"]

CLASSIFICATION_PROMPT = (
    "Classify the following document into exactly one category: "
    "support, sales, urgent, or general. Respond with only the "
    "category name, nothing else.\n\nDocument: {text}\n\nCategory:"
)

SUMMARIZATION_PROMPT = (
    "Summarize the following document in 2-3 sentences. "
    "Focus on key actions needed.\n\nDocument: {text}\n\nSummary:"
)


async def call_ollama(prompt: str, model: str = OLLAMA_MODEL) -> dict:
    """Call Ollama API with a prompt and return the response."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
            },
        )
        response.raise_for_status()
        return response.json()


async def classify_content(text: str, model: str = OLLAMA_MODEL) -> dict:
    """Use Ollama to classify content into categories.

    Returns:
        dict with 'category' and 'raw_response' keys.
    """
    prompt = CLASSIFICATION_PROMPT.format(text=text)
    try:
        result = await call_ollama(prompt, model=model)
        raw = result.get("response", "").strip().lower()

        category = "general"
        for cat in CLASSIFICATION_CATEGORIES:
            if cat in raw:
                category = cat
                break

        return {
            "category": category,
            "raw_response": raw,
        }
    except httpx.HTTPError as e:
        return {
            "category": "general",
            "raw_response": f"error: {e}",
        }


async def summarize_content(text: str, model: str = OLLAMA_MODEL) -> str:
    """Use Ollama to generate a summary.

    Returns:
        Summary text string.
    """
    prompt = SUMMARIZATION_PROMPT.format(text=text)
    try:
        result = await call_ollama(prompt, model=model)
        return result.get("response", "").strip()
    except httpx.HTTPError as e:
        return f"Error generating summary: {e}"


def route_by_classification(classification: dict) -> str:
    """Determine action based on AI classification.

    Returns:
        Action string describing the routing decision.
    """
    category = classification.get("category", "general")

    routing_map = {
        "support": "POST /v1/conversations — Create support ticket",
        "sales": "POST /v1/tasks — Create sales task",
        "urgent": "POST Slack webhook — Send urgent notification",
        "general": "POST /v1/tasks — Save to database",
    }

    return routing_map.get(category, "POST /v1/tasks — Save to database (default)")


async def process_document(text: str, model: str = OLLAMA_MODEL) -> dict:
    """Full pipeline: classify -> summarize -> route.

    Returns:
        dict with classification, summary, and action.
    """
    classification = await classify_content(text, model=model)
    summary = await summarize_content(text, model=model)
    action = route_by_classification(classification)

    return {
        "classification": classification["category"],
        "summary": summary,
        "action": action,
    }


async def main() -> int:
    """Run the demo."""
    parser = argparse.ArgumentParser(description="AI Workflow Automation Demo with Ollama")
    parser.add_argument(
        "--text",
        type=str,
        help="Text to process (if not provided, runs samples)",
    )
    parser.add_argument(
        "--file",
        type=str,
        help="Path to a text file to process",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=OLLAMA_MODEL,
        help=f"Ollama model to use (default: {OLLAMA_MODEL})",
    )
    args = parser.parse_args()
    model = args.model

    if args.text:
        text = args.text
        print(f"Processing custom text ({len(text)} chars)...")
        result = await process_document(text, model=model)
        print(json.dumps(result, indent=2))
        return 0

    if args.file:
        try:
            with open(args.file, encoding="utf-8") as f:
                text = f.read()
            print(f"Processing file: {args.file} ({len(text)} chars)...")
            result = await process_document(text, model=model)
            print(json.dumps(result, indent=2))
            return 0
        except FileNotFoundError:
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            return 1

    samples = [
        {
            "text": (
                "Customer complaint: My order #12345 was supposed to "
                "arrive yesterday and it's still not here. I need a "
                "refund or replacement immediately."
            ),
            "expected": "support",
        },
        {
            "text": (
                "Hi, I'm interested in your enterprise pricing for "
                "50 seats. Could you send me a quote and schedule a "
                "demo for next week?"
            ),
            "expected": "sales",
        },
        {
            "text": (
                "URGENT: Production server is down! All customers "
                "affected. Need immediate assistance to restore service."
            ),
            "expected": "urgent",
        },
        {
            "text": (
                "Please find attached the monthly newsletter for our "
                "team. Let me know if you have any feedback on the content."
            ),
            "expected": "general",
        },
        {
            "text": (
                "I'd like to upgrade my subscription from basic to "
                "premium. What are the additional features included?"
            ),
            "expected": "sales",
        },
    ]

    print("=" * 60)
    print("AI Workflow Automation Demo")
    print(f"Ollama URL: {OLLAMA_URL}")
    print(f"Model: {model}")
    print("=" * 60)
    print()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.get(f"{OLLAMA_URL}/api/tags")
    except httpx.HTTPError:
        print(
            "Error: Cannot connect to Ollama. Make sure it is running:",
            file=sys.stderr,
        )
        print("  1. Install Ollama: https://ollama.com", file=sys.stderr)
        print(
            f"  2. Pull model: ollama pull {model}",
            file=sys.stderr,
        )
        print("  3. Start Ollama: ollama serve", file=sys.stderr)
        return 1

    for i, sample in enumerate(samples, 1):
        print(f"--- Sample {i}/{len(samples)} ---")
        print(f"Text: {sample['text'][:80]}...")
        print(f"Expected: {sample['expected']}")
        print()

        result = await process_document(sample["text"], model=model)

        print(f"Classification: {result['classification']}")
        print(f"Summary: {result['summary'][:120]}...")
        print(f"Action: {result['action']}")
        match = "Y" if result["classification"] == sample["expected"] else "N"
        print(f"Match: {match}")
        print()

    print("=" * 60)
    print("Demo complete!")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
