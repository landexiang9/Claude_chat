import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_chat.app import ClaudeChatApp
app = ClaudeChatApp()

print("DeepSeek API Key:", app.config.get("deepseek_api_key"))
print("DeepSeek API URL:", app.config.get("deepseek_api_url"))
print("Gemini API Key:", app.config.get("gemini_api_key"))

# Let's try to list deepseek models using openai client directly
from openai import OpenAI
try:
    client = OpenAI(
        api_key=app.config.get("deepseek_api_key", ""),
        base_url=app.config.get("deepseek_api_url", "https://api.deepseek.com")
    )
    models = client.models.list()
    print("DeepSeek models:")
    for m in models.data:
        print(m.id)
except Exception as e:
    print("Error getting deepseek models:", e)

