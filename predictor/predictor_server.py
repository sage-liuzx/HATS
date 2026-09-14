# predictor_server.py
import json
import logging
from flask import Flask, request, jsonify
from token_predictor_infer import predict_bucket, load_bucket_config, bucket_id_to_expected_tokens
import os

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# 配置文件路径
MODEL_DIR = "/app/model"
BUCKET_CONFIG = "/app/bucket_config.json"

# 预加载模型和配置
@app.before_first_request
def load_model_and_config():
    global bucket_edges
    try:
        cfg = load_bucket_config(BUCKET_CONFIG)
        bucket_edges = cfg["bucket_edges"]
        app.logger.info(f"Loaded bucket config with {len(bucket_edges)-1} buckets")
    except Exception as e:
        app.logger.error(f"Failed to load bucket config: {e}")
        bucket_edges = None

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"})

@app.route('/predict', methods=['POST'])
def predict():
    try:
        data = request.json
        if not data or 'prompt' not in data:
            return jsonify({"error": "Missing 'prompt' in request"}), 400
        
        prompt = data['prompt']
        
        # 预测 bucket
        bucket_id = predict_bucket(MODEL_DIR, prompt)
        
        # 计算预期 token 数
        if bucket_edges:
            expected_tokens = bucket_id_to_expected_tokens(bucket_id, bucket_edges)
        else:
            # 如果没有 bucket config，使用简单的估算
            expected_tokens = 1000  # 默认值
        
        # 估算输入 token 数（简单方法）
        input_tokens = len(prompt.split()) * 1.3  # 粗略估算
        
        response = {
            "bucket_id": bucket_id,
            "predicted_output_tokens": expected_tokens,
            "input_tokens": input_tokens,
            "prompt": prompt[:100]  # 只返回前100字符用于调试
        }
        
        app.logger.info(f"Prediction: {bucket_id=}, tokens={expected_tokens:.1f}")
        return jsonify(response)
    
    except Exception as e:
        app.logger.error(f"Prediction error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)