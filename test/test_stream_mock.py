"""
End-to-end mock test: simulates the full streaming queue consumer loop
as it runs in app.py _process_sending_stream, validating that search
events are correctly dispatched to the JS layer.
"""
import queue
import json

class MockWindow:
    def __init__(self):
        self.js_calls = []
    def evaluate_js(self, js_code):
        self.js_calls.append(js_code)
        print(f"  >> JS: {js_code[:120]}{'...' if len(js_code) > 120 else ''}")

def simulate_stream(events, window):
    """Simulates _process_sending_stream logic from app.py"""
    streaming_text = ""
    streaming_thinking_text = ""
    q = queue.Queue()
    for e in events:
        q.put(e)
    
    while True:
        msg = q.get()
        msg_type, msg_data = msg
        
        if msg_type == "text":
            streaming_text += msg_data
            if window:
                js_code = f"if (window.onStreamMessage) window.onStreamMessage('text', {json.dumps(msg_data)});"
                window.evaluate_js(js_code)
        elif msg_type == "thinking":
            streaming_thinking_text += msg_data
            if window:
                js_code = f"if (window.onStreamMessage) window.onStreamMessage('thinking', {json.dumps(msg_data)});"
                window.evaluate_js(js_code)
        elif msg_type == "done":
            if window:
                js_code = f"if (window.onStreamMessage) window.onStreamMessage('done', {json.dumps(msg_data)});"
                window.evaluate_js(js_code)
            break
        elif msg_type == "aborted":
            if window:
                js_code = f"if (window.onStreamMessage) window.onStreamMessage('aborted', {{}});"
                window.evaluate_js(js_code)
            break
        elif msg_type == "search_start":
            if window:
                js_code = f"if (window.onStreamMessage) window.onStreamMessage('search_start', {json.dumps(msg_data)});"
                window.evaluate_js(js_code)
        elif msg_type == "search_done":
            if window:
                js_code = f"if (window.onStreamMessage) window.onStreamMessage('search_done', {json.dumps(msg_data)});"
                window.evaluate_js(js_code)
        elif msg_type == "fetch_start":
            if window:
                js_code = f"if (window.onStreamMessage) window.onStreamMessage('fetch_start', {json.dumps(msg_data)});"
                window.evaluate_js(js_code)
        elif msg_type == "fetch_done":
            if window:
                js_code = f"if (window.onStreamMessage) window.onStreamMessage('fetch_done', {json.dumps(msg_data)});"
                window.evaluate_js(js_code)
        elif msg_type == "error":
            if window:
                js_code = f"if (window.onStreamMessage) window.onStreamMessage('error', {json.dumps(msg_data)});"
                window.evaluate_js(js_code)
            break

    return streaming_text, window.js_calls

print("=== Mock End-to-End Stream Simulation ===\n")
window = MockWindow()

# Simulate a DeepSeek search followed by answer
events = [
    ("text", "Let me search for that...\n"),
    ("search_start", {"query": "今日最新新闻"}),
    ("search_done", {
        "query": "今日最新新闻",
        "results": [
            {"title": "新闻A", "url": "https://news.example.com/a", "snippet": "内容A"},
            {"title": "新闻B", "url": "https://news.example.com/b", "snippet": "内容B"},
        ],
        "engine": "google",
        "usage": None
    }),
    ("text", "Based on the search results, here is the summary..."),
    ("done", {"input_tokens": 100, "output_tokens": 50, "content_blocks": [{"type": "text", "text": "summary"}]}),
]

text, calls = simulate_stream(events, window)

print(f"\n=== Results ===")
print(f"Streamed text: {repr(text)}")

dispatched_types = []
for call in calls:
    import re
    m = re.search(r"onStreamMessage\('(\w+)'", call)
    if m:
        dispatched_types.append(m.group(1))

print(f"Events dispatched to JS: {dispatched_types}")

expected = ["text", "search_start", "search_done", "text", "done"]
if dispatched_types == expected:
    print("\n[OK] All search events correctly dispatched to the frontend!")
else:
    print(f"\n[FAILED] Mismatch! Expected {expected}, got {dispatched_types}")
