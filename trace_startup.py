import time
import sys

t_start = time.time()
print(f"{time.time() - t_start:.2f}s: Started")

from claude_chat.app import ClaudeChatApp
import webview

print(f"{time.time() - t_start:.2f}s: Imports done")

app = ClaudeChatApp()
print(f"{time.time() - t_start:.2f}s: App init done")

from pathlib import Path
ui_dir = Path("claude_chat/ui")
html_file = ui_dir / "index.html"
from claude_chat.app import WebAPI
api = WebAPI(app)

print(f"{time.time() - t_start:.2f}s: API init done")

def on_loaded():
    print(f"{time.time() - t_start:.2f}s: webview loaded event fired")
    app.window.destroy()

app.window = webview.create_window(
    title="Claude Chat Profiler",
    url=str(html_file.resolve()),
    js_api=api,
)
app.window.events.loaded += on_loaded

print(f"{time.time() - t_start:.2f}s: create_window done, starting webview...")
webview.start()
print(f"{time.time() - t_start:.2f}s: webview loop finished")
