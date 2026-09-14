import argparse
import json
import math
from typing import Dict, Any


def load_bucket_config(config_path: str) -> Dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    edges = cfg.get("bucket_edges")
    if not isinstance(edges, list) or len(edges) < 2:
        raise ValueError("bucket_edges must be a list with >= 2 numbers")
    edges = [float(x) for x in edges]
    if any(math.isnan(x) for x in edges):
        raise ValueError("bucket_edges contains NaN")
    if sorted(edges) != edges:
        raise ValueError("bucket_edges must be sorted ascending")
    return {"bucket_edges": edges}


def bucket_id_to_expected_tokens(bucket_id: int, edges):
    # bucket i corresponds to [edges[i], edges[i+1])
    if bucket_id < 0 or bucket_id >= len(edges) - 1:
        raise ValueError(f"bucket_id {bucket_id} out of range for {len(edges)-1} buckets")
    lo = edges[bucket_id]
    hi = edges[bucket_id + 1]
    # expected token count: midpoint of bucket
    return (lo + hi) / 2.0


def predict_bucket(model_dir: str, text: str) -> int:
    # Lazy import so the script can still show a helpful error when deps missing.
    try:
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
    except Exception as e:
        raise RuntimeError(
            "Missing dependencies. Install with: pip install -U torch transformers safetensors"
        ) from e

    tok = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir)
    model.eval()

    inputs = tok(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        out = model(**inputs)
        # logits shape: [1, num_labels]
        bucket = int(out.logits.argmax(dim=-1).item())
    return bucket


def main():
    parser = argparse.ArgumentParser(
        description="Use a fine-tuned DistilBERT token-count bucket predictor."
    )
    parser.add_argument(
        "--model-dir",
        required=True,
        help="Path to the extracted HF model directory (config.json/model.safetensors/tokenizer.json).",
    )
    parser.add_argument(
        "--text",
        required=True,
        help="Input prompt/instruction text to predict output token bucket.",
    )
    parser.add_argument(
        "--bucket-config",
        default="bucket_config.json",
        help="JSON file describing bucket edges used during training (default: bucket_config.json in CWD).",
    )
    args = parser.parse_args()

    bucket = predict_bucket(args.model_dir, args.text)

    result = {"bucket_id": bucket}
    try:
        cfg = load_bucket_config(args.bucket_config)
        expected_tokens = bucket_id_to_expected_tokens(bucket, cfg["bucket_edges"])
        result["expected_output_tokens"] = expected_tokens
        result["bucket_edges"] = cfg["bucket_edges"]
    except Exception as e:
        # If no bucket config is provided, still return the predicted bucket.
        result["note"] = f"bucket_config not applied: {e}"

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

