import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_chat.app import ClaudeChatApp
app = ClaudeChatApp()

from openai import OpenAI
try:
    client = OpenAI(
        api_key=app.config.get("deepseek_api_key", ""),
        base_url=app.config.get("deepseek_api_url", "https://api.deepseek.com")
    )
    # The client might be hitting a third-party API!
    # Because deepseek-v4-flash doesn't exist in official DeepSeek, they use deepseek-chat and deepseek-reasoner.
    # So this must be a third-party proxy URL being set for deepseek!
    print("Base URL is:", app.config.get("deepseek_api_url", "https://api.deepseek.com"))
    
    response_stream = client.chat.completions.create(
        model="deepseek-v4-flash", # or whatever
        messages=[{"role": "user", "content": "Hello"}],
        stream=True
    )
    for chunk in response_stream:
        print(chunk.choices[0].delta.content or "", end="")
    print()
except Exception as e:
    print("Error:", e)
