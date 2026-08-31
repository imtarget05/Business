"""Export Telegram conversations from PostgreSQL to a QLoRA training dataset.

Usage:
    python scripts/export_dataset.py --out finetune/train.jsonl --min-turns 2

Each line: {"messages": [{"role": "user", "content": ...}, {"role": "assistant", "content": ...}]}
Pairs consecutive user/assistant turns from each Telegram conversation.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import select

from packages.config.settings import get_settings
from packages.database.models import Conversation, Message
from packages.database.session import get_session_factory


async def export(out_path: str, min_turns: int) -> int:
    factory = get_session_factory(get_settings())
    pairs = 0
    async with factory() as session:
        convs = (await session.scalars(select(Conversation))).all()
        with open(out_path, "w", encoding="utf-8") as f:
            for conv in convs:
                msgs = (
                    await session.scalars(
                        select(Message)
                        .where(Message.conversation_id == conv.id)
                        .order_by(Message.sequence)
                    )
                ).all()
                messages: list[dict] = []
                for m in msgs:
                    role = m.role.value if hasattr(m.role, "value") else str(m.role)
                    if role == "tool":
                        continue
                    if role == "user":
                        if messages and messages[-1]["role"] == "user":
                            messages[-1]["content"] += " " + m.content
                        else:
                            messages.append({"role": "user", "content": m.content})
                    elif role == "assistant" and messages:
                        messages.append({"role": "assistant", "content": m.content})
                        if len(messages) >= 2:
                            f.write(
                                json.dumps({"messages": list(messages[-2:])}, ensure_ascii=False)
                                + "\n"
                            )
                            pairs += 1
    print(f"Exported {pairs} pairs (min_turns={min_turns}) -> {out_path}")
    return pairs


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="finetune/train.jsonl")
    p.add_argument("--min-turns", type=int, default=2)
    args = p.parse_args()
    asyncio.run(export(args.out, args.min_turns))
