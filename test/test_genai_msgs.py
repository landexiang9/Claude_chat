import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from claude_chat.clients import convert_messages_to_gemini

messages = [
    {"role": "user", "content": [{"type": "text", "text": "Hello, Gemini!"}]},
    {"role": "assistant", "content": [{"type": "text", "text": "Hi, User!"}]},
    {"role": "user", "content": [{"type": "image", "source": {"data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="}}]},
]

gemini_msgs = convert_messages_to_gemini(messages)
print("Converted:", gemini_msgs)

try:
    from google import genai
    from google.genai import types
    
    # Check if Pydantic accepts the result
    contents = [types.Content(**msg) for msg in gemini_msgs]
    print("Pydantic successfully parsed:", contents)
except Exception as e:
    import traceback
    traceback.print_exc()
