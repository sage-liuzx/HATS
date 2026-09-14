"""输出长度预测器初始训练脚本。

用法：
    python train_predictor.py \
        --data train.jsonl \
        --output ./distilbert-base-uncased-finetuned \
        --epochs 3
"""

import argparse
import json
import os
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from transformers import DistilBertModel, DistilBertTokenizer

BUCKET_CONFIG = Path(__file__).parent / "bucket_config.json"


class LengthDataset(Dataset):
    def __init__(self, samples, tokenizer, buckets, max_len=512):
        self.samples = samples
        self.tokenizer = tokenizer
        self.buckets = buckets
        self.max_len = max_len

    def __len__(self):
        return len(self.samples)

    def _bucket_id(self, length):
        for b in self.buckets:
            if b["min"] <= length <= b["max"]:
                return b["id"] - 1
        return len(self.buckets) - 1

    def __getitem__(self, idx):
        s = self.samples[idx]
        enc = self.tokenizer(
            s["prompt"],
            truncation=True,
            max_length=self.max_len,
            padding="max_length",
            return_tensors="pt",
        )
        label = self._bucket_id(int(s["output_length"]))
        return {
            "input_ids": enc["input_ids"].squeeze(0),
            "attention_mask": enc["attention_mask"].squeeze(0),
            "label": torch.tensor(label, dtype=torch.long),
        }


class LengthClassifier(nn.Module):
    def __init__(self, num_classes: int):
        super().__init__()
        self.bert = DistilBertModel.from_pretrained("distilbert-base-uncased")
        self.classifier = nn.Linear(self.bert.config.hidden_size, num_classes)

    def forward(self, input_ids, attention_mask):
        out = self.bert(input_ids=input_ids, attention_mask=attention_mask)
        cls = out.last_hidden_state[:, 0, :]
        return self.classifier(cls)


def load_jsonl(path):
    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="jsonl 数据集，每行含 prompt/output_length")
    parser.add_argument("--output", default="./distilbert-base-uncased-finetuned")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=2e-5)
    args = parser.parse_args()

    with open(BUCKET_CONFIG) as f:
        cfg = json.load(f)
    buckets = cfg["buckets"]
    num_classes = cfg["num_buckets"]

    samples = load_jsonl(args.data)
    print(f"Loaded {len(samples)} samples")

    tokenizer = DistilBertTokenizer.from_pretrained("distilbert-base-uncased")
    dataset = LengthDataset(samples, tokenizer, buckets)
    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=True)

    model = LengthClassifier(num_classes)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    criterion = nn.CrossEntropyLoss()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)

    model.train()
    for epoch in range(args.epochs):
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

        print(f"Epoch {epoch + 1}/{args.epochs}, loss={total_loss / len(loader):.4f}")

    os.makedirs(args.output, exist_ok=True)
    model.bert.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)
    torch.save(model.classifier.state_dict(), os.path.join(args.output, "classifier.bin"))
    print(f"Saved model to {args.output}")


if __name__ == "__main__":
    main()
