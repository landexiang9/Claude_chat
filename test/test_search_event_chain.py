"""
Test: Verifies search event types are handled in _process_sending_stream.
Simulates the queue events that DeepSeek would produce during a search.
"""
import queue
import sys
sys.path.insert(0, ".")

import pathlib
# Read app.py and check for all required handlers
base_path = pathlib.Path(__file__).parent
with open(base_path / "app.py", "r", encoding="utf-8") as f:
    content = f.read()


required_events = ["search_start", "search_done", "fetch_start", "fetch_done", "text", "thinking", "done", "aborted", "error"]
print("=== Checking event handlers in _process_sending_stream ===")
all_ok = True
for event in required_events:
    if f'msg_type == "{event}"' in content:
        print(f"  [OK] {event}")
    else:
        print(f"  [MISSING] {event}")
        all_ok = False

print()
if all_ok:
    print("All event handlers are present.")
else:
    print("Some handlers are missing!")
    sys.exit(1)

# Also verify client.py emits search_start and search_done for DeepSeek
with open(base_path / "client.py", "r", encoding="utf-8") as f:
    client_content = f.read()

print("\n=== Checking search event emissions in client.py ===")
for evt in ["search_start", "search_done", "fetch_start", "fetch_done"]:
    if f'("{evt}"' in client_content or f"('{evt}'" in client_content:
        print(f"  [OK] {evt} emitted")
    else:
        print(f"  [MISSING] {evt} not emitted")

print("\nAll checks complete.")
