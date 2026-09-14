"""在线反馈增量微调脚本。

从任务执行日志中收集 (prompt, 实际输出长度) 样本，
累计达到阈值后触发一次微调，更新预测器模型。

用法：
    python online_finetune.py \
        --feedback feedback.jsonl \
        --model ./distilbert-base-uncased-finetuned \
        --threshold 500
"""

import argparse
import json
import os
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from transformers import DistilBertModel, DistilBertTokenizer

from train_predictor import LengthDataset, LengthClassifier, load_jsonl

BUCKET_CONFIG = Path(__file__).parent / "bucket_config.json"


def append_feedback(feedback_path, prompt, output_length):
    """每条任务完成后调用一次，追加反馈样本。"""
    with open(feedback_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"prompt": prompt, "output_length": output_length}, ensure_ascii=False) + "\n")


def count_feedback(feedback_path):
    if not os.path.exists(feedback_path):
        return 0
    with open(feedback_path, "r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def finetune(model_dir, feedback_path, epochs=1, batch_size=16, lr=1e-5):
    with open(BUCKET_CONFIG) as f:
        cfg = json.load(f)
    buckets = cfg["buckets"]
    num_classes = cfg["num_buckets"]

    samples = load_jsonl(feedback_path)
    print(f"Fine-tuning on {len(samples)} samples")

    tokenizer = DistilBertTokenizer.from_pretrained(model_dir)
    dataset = LengthDataset(samples, tokenizer, buckets)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    model = LengthClassifier(num_classes)
    model.bert = DistilBertModel.from_pretrained(model_dir)
    classifier_path = os.path.join(model_dir, "classifier.bin")
    if os.path.exists(classifier_path):
        model.classifier.load_state_dict(torch.load(classifier_path, map_location="cpu"))

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    model.train()

    for epoch in range(epochs):
        total_loss = 0.0
        for batch in loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["label"].to(device)

            optimizer.zero_grad()
            logits = model(input_ids, attention_mask)
            loss = criterion(logits, labels)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        print(f"Epoch {epoch + 1}/{epochs}, loss={total_loss / len(loader):.4f}")

    model.bert.save_pretrained(model_dir)
    tokenizer.save_pretrained(model_dir)
    torch.save(model.classifier.state_dict(), classifier_path)
    print(f"Updated model saved to {model_dir}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--feedback", default="./feedback.jsonl")
    parser.add_argument("--model", default="./distilbert-base-uncased-finetuned")
    parser.add_argument("--threshold", type=int, default=500)
    parser.add_argument("--epochs", type=int, default=1)
    args = parser.parse_args()

    n = count_feedback(args.feedback)
    print(f"Current feedback samples: {n}")
    if n < args.threshold:
        print(f"Below threshold ({args.threshold}), skip fine-tuning.")
        return

    finetune(args.model, args.feedback, epochs=args.epochs)

    # 微调完成后清空反馈文件，等待下一轮累积
    backup = args.feedback + ".used"
    os.rename(args.feedback, backup)
    print(f"Feedback file moved to {backup}")


if __name__ == "__main__":
    main()
