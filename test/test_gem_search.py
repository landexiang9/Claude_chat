import os
import sys
import threading
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_chat.app import ClaudeChatApp, WebAPI

class DummyWindow:
    def evaluate_js(self, js):
        pass

app = ClaudeChatApp()
app.window = DummyWindow()
api = WebAPI(app)

api.save_config({"active_platform": "gemini", "gemini_search": True})

conv = app.conv_manager.new_conversation()
conv["model"] = "gemini-2.5-pro"
app.conv_manager.save_conversation(conv)

app.conv_manager.add_message(conv["id"], "user", [{"type": "text", "text": "What is the stock price of AAPL today?"}])
app.current_conv = app.conv_manager.load_conversation(conv["id"])

print("Sending message with Gemini search enabled...")
res = api.send_message(conv["id"], "What is the stock price of AAPL today?", [])
print("send_message returned:", res)

def queue_reader():
    while True:
        try:
            msg_type, msg_data = app.streaming_queue.get(timeout=20)
            print(f"[QUEUE] {msg_type}: {msg_data}")
            if msg_type in ["done", "aborted", "error"]:
                break
        except Exception as e:
            print("[QUEUE] timeout")
            break

t = threading.Thread(target=queue_reader)
t.start()
t.join()
