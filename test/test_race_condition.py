import threading
import time
import uuid
import os
import claude_chat.db as db_module
from claude_chat.db import DatabaseManager
from claude_chat.conversation import ConversationManager
from pathlib import Path

# Override DB_PATH for testing
test_db_path = Path("test.db")
if test_db_path.exists():
    test_db_path.unlink()
db_module.DB_PATH = test_db_path

db = DatabaseManager()
db.init_db()
conv_manager = ConversationManager()

conv = conv_manager.new_conversation()
conv_id = conv["id"]
print(f"Created conversation: {conv_id}")

def add_message(content, delay=0):
    # Load conversation (Thread reads from DB)
    c = conv_manager.load_conversation(conv_id)
    time.sleep(delay) # Simulate some processing time
    
    # Append message
    c["messages"].append({"role": "user", "content": content})
    
    # Save conversation (Thread writes to DB)
    conv_manager.save_conversation(c)
    print(f"Saved message: {content}")

# Start two threads that simulate concurrent processing
# Thread 1 starts, reads DB, sleeps 0.5s, appends msg1, saves.
# Thread 2 starts, reads DB, sleeps 0.2s, appends msg2, saves.

t1 = threading.Thread(target=add_message, args=("Message 1 (takes longer)", 0.5))
t2 = threading.Thread(target=add_message, args=("Message 2 (takes shorter)", 0.2))

t1.start()
t2.start()

t1.join()
t2.join()

# Now let's check the final state of the conversation
final_conv = conv_manager.load_conversation(conv_id)
print("\nFinal Messages:")
for m in final_conv["messages"]:
    print("-", m["content"])

# We expect BOTH messages to be there if it's thread-safe.
# But with the current design, Thread 1 overwrites Thread 2's message!
