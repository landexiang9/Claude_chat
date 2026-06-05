try:
    from google import genai
    from google.genai import types
    
    c = types.Content(**{"role": "user", "parts": [{"inline_data": {"mime_type": "image/png", "data": b"123"}}]})
    print("Content with blob:", c)
    
except Exception as e:
    import traceback
    traceback.print_exc()
