import os
import sys
import threading
import time
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_chat.app import ClaudeChatApp, WebAPI

class DummyWindow:
    def evaluate_js(self, js):
        pass

app = ClaudeChatApp()
app.window = DummyWindow()
api = WebAPI(app)

api.save_config({"active_platform": "deepseek"})

conv = app.conv_manager.new_conversation()
conv["model"] = "deepseek-chat"
app.conv_manager.save_conversation(conv)

app.conv_manager.add_message(conv["id"], "user", [{"type": "text", "text": "hello"}])
app.current_conv = app.conv_manager.load_conversation(conv["id"])

print("Sending message...")
res = api.send_message(conv["id"], "How are you?", [])
print("send_message returned:", res)

def queue_reader():
    while True:
        try:
            msg_type, msg_data = app.streaming_queue.get(timeout=10)
            print(f"[QUEUE] {msg_type}: {msg_data}")
            if msg_type in ["done", "aborted", "error"]:
                break
        except Exception as e:
            print("[QUEUE] timeout")
            break

t = threading.Thread(target=queue_reader)
t.start()
t.join()
