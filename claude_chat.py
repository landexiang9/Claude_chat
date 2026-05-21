"""
Claude Chat - Desktop GUI for Anthropic Claude API (Entry Point)
Imports and runs the modularized ClaudeChatApp from the claude_chat package.
"""

import sys
from pathlib import Path

# Ensure the root directory is in python path
sys.path.insert(0, str(Path(__file__).parent))

from claude_chat.app import ClaudeChatApp

if __name__ == "__main__":
    app = ClaudeChatApp()
    app.mainloop()
