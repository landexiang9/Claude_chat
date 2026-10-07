"""Repeatable offline retrieval/latency/request benchmark with controlled embeddings.

Run .venv/Scripts/python.exe test/test_memory_benchmark.py. No real provider calls.
"""

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_chat.db import DatabaseManager  # noqa: E402
from claude_chat.memory_store import MemoryStore  # noqa: E402

DATASET = [
    ("PHP 反序列化", "对象还原漏洞"),
    ("Docker 容器", "隔离运行环境"),
    ("SQLite 数据库", "嵌入式关系存储"),
    ("pytest 单元测试", "自动检验代码"),
    ("TypeScript 类型", "静态约束脚本"),
    ("ARIA 无障碍", "屏幕阅读适配"),
    ("Mermaid 流程图", "文本绘制拓扑"),
    ("REST API", "资源接口规范"),
]


class ControlledEmbedding:
    signature = "offline-controlled-1536-dimensions"

    def __init__(self):
        self.requests = 0
        self.inputs = 0

    def embed(self, texts, query=False):
        self.requests += 1
        self.inputs += len(texts)
        output = []
        for text in texts:
            axis = next((i for i, pair in enumerate(DATASET) if any(term in text for term in pair)), len(DATASET))
            vector = [0.0] * 1536
            vector[axis] = 1.0
            output.append(vector)
        return output


def benchmark():
    with tempfile.TemporaryDirectory(prefix="memory-benchmark-") as directory:
        root = Path(directory)
        with (
            patch("claude_chat.db.DB_PATH", root / "benchmark.db"),
            patch("claude_chat.db.CONVERSATIONS_DIR", root / "none"),
        ):
            db = DatabaseManager()
            store = MemoryStore(db)
            cid = db.new_conversation()["id"]
            expected = {}
            for text, synonym in DATASET:
                row = store.put({"content": "长期研究 " + text, "category": "project"})
                expected[text] = expected[synonym] = row["id"]
            for number in range(300):
                store.put({"content": f"无关园艺备忘事项编号 {number}", "category": "other"})
            store.set_options({"top_k": 3})

            def measure():
                hit, latencies, counts = 0, [], []
                for query, wanted in expected.items():
                    start = time.perf_counter()
                    _, context = store.retrieve(cid, query)
                    latencies.append((time.perf_counter() - start) * 1000)
                    counts.append(len(context["memories"]))
                    hit += wanted in {memory["id"] for memory in context["memories"]}
                return {
                    "recall_at_3": hit / len(expected),
                    "median_ms": round(statistics.median(latencies), 2),
                    "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 2),
                    "max_prompt_memories": max(counts),
                }

            baseline = measure()
            store.set_options({"embedding_platform": "gemini", "embedding_model": "offline-controlled"})
            provider = ControlledEmbedding()
            store.engine.index.provider_factory = lambda options: provider
            start = time.perf_counter()
            store.engine.index.build()
            index_ms = (time.perf_counter() - start) * 1000
            index_requests, index_inputs = provider.requests, provider.inputs
            hybrid = measure()
            query_requests = provider.requests - index_requests
            warm = measure()
            repeat_requests = provider.requests - index_requests - query_requests
            assert hybrid["recall_at_3"] == 1 and hybrid["recall_at_3"] > baseline["recall_at_3"]
            assert repeat_requests == 0 and hybrid["max_prompt_memories"] <= 3
            return {
                "mode": "offline-controlled embeddings; validates pipeline, not real-model retrieval quality",
                "notes": 308,
                "queries": len(expected),
                "dimensions": 1536,
                "keyword": baseline,
                "hybrid": hybrid,
                "warm_cache": warm,
                "index_ms": round(index_ms, 2),
                "index_requests": index_requests,
                "index_inputs": index_inputs,
                "query_embedding_requests": query_requests,
                "repeat_query_requests": repeat_requests,
                "paid_api_requests": 0,
            }


def test_benchmark_pipeline():
    result = benchmark()
    assert result["hybrid"]["recall_at_3"] == 1


if __name__ == "__main__":
    result = benchmark()
    output = Path(__file__).resolve().parent.parent / "scratch" / "memory-validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
