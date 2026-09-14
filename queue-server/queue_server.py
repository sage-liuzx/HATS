"""每个节点上的本地队列服务，用于串行化 llama-server 请求。"""

import requests
from flask import Flask, request, jsonify
from queue import Queue
from threading import Thread

app = Flask(__name__)
q = Queue()

LLAMA_URL = "http://localhost:8080/completion"
WORKERS = 4


def worker():
    while True:
        data = q.get()
        try:
            requests.post(LLAMA_URL, json=data, timeout=600)
        except Exception as e:
            print("Request failed:", e)
        finally:
            q.task_done()


for _ in range(WORKERS):
    Thread(target=worker, daemon=True).start()


@app.route("/submit", methods=["POST"])
def submit():
    data = request.json
    q.put(data)
    return jsonify({"status": "queued", "queue_size": q.qsize()})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "queue_size": q.qsize()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8082)
