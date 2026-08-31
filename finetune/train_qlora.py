"""QLoRA fine-tuning for Llama 3.2 3B (runs on your PC, NOT in Docker).

Requirements (run on host with a CUDA GPU, e.g. RTX 3060 12GB+):
    pip install unsloth

Usage:
    python finetune/train_qlora.py --dataset finetune/train.jsonl --out finetune/out

Pipeline:
    1. Export chats:      python scripts/export_dataset.py
    2. Train QLoRA:       python finetune/train_qlora.py
    3. Merge & convert:   (script does it) -> finetune/out/model
    4. Import to Ollama:  ollama create my-llama32 -f finetune/out/Modelfile
    5. Switch .env:       LLM_MODEL=my-llama32
"""

from __future__ import annotations

import argparse
import json


def load_dataset(path: str):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line)["messages"])
    return rows


def to_text(convo: list[dict]) -> str:
    """Llama-3 chat template format."""
    text = "<|begin_of_text|>"
    for m in convo:
        text += f"<|start_header_id|>{m['role']}<|end_header_id|>\n{m['content']}<|eot_id|>"
    text += "<|start_header_id|>assistant<|end_header_id|>\n"
    return text


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", default="finetune/train.jsonl")
    p.add_argument("--out", default="finetune/out")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--rank", type=int, default=16, help="LoRA rank (r)")
    args = p.parse_args()

    data = load_dataset(args.dataset)
    if len(data) < 10:
        print(f"⚠️ Only {len(data)} samples — collect more chats first (need >= 10, ideally 100+).")
        return
    print(f"Training on {len(data)} samples, LoRA r={args.rank}, {args.epochs} epochs")

    from unsloth import FastLanguageModel
    from datasets import Dataset
    from trl import SFTTrainer
    from transformers import TrainingArguments

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name="unsloth/Llama-3.2-3B-Instruct",
        max_seq_length=2048,
        load_in_4bit=True,  # QLoRA
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.rank,
        lora_alpha=args.rank * 2,
        lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth",
    )

    ds = Dataset.from_dict({"text": [to_text(c) for c in data]})
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=ds,
        dataset_text_field="text",
        args=TrainingArguments(
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            num_train_epochs=args.epochs,
            learning_rate=2e-4,
            fp16=True,
            logging_steps=5,
            output_dir=args.out,
        ),
    )
    trainer.train()

    model.save_pretrained_merged(args.out + "/model", tokenizer)
    with open(args.out + "/Modelfile", "w", encoding="utf-8") as f:
        f.write(
            "FROM ./model\n"
            "PARAMETER temperature 0.3\n"
            "PARAMETER top_p 0.9\n"
            'SYSTEM """Bạn là trợ lý Business Ops của Mai Nguyễn Bình Tân."""\n'
        )
    print(f"Done. Next: ollama create my-llama32 -f {args.out}/Modelfile")


if __name__ == "__main__":
    main()
