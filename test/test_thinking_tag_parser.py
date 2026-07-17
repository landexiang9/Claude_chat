"""Standalone regression tests for the streaming thinking-tag parser."""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_chat.clients.thinking_tag_parser import ThinkingTagStreamParser


def parse_chunks(chunks):
    parser = ThinkingTagStreamParser()
    text_parts = []
    thinking_parts = []
    for chunk in chunks:
        text, thinking = parser.feed(chunk)
        text_parts.append(text)
        thinking_parts.append(thinking)
    text, thinking = parser.flush()
    text_parts.append(text)
    thinking_parts.append(thinking)
    return "".join(text_parts), "".join(thinking_parts)


def main():
    assert parse_chunks(["<thou", "ght>abc</thou", "ght>tail"]) == ("tail", "abc")
    assert parse_chunks(["<thought>abc</thoughtful>def</thought>tail"]) == (
        "tail",
        "abc</thoughtful>def",
    )
    assert parse_chunks(["before <b>html</b> after"]) == ("before <b>html</b> after", "")
    assert parse_chunks(["<reasoning kind='x'>why", "</reasoning>answer"]) == ("answer", "why")
    assert parse_chunks(["<think>unfinished"]) == ("", "unfinished")
    print("thinking tag parser tests passed")


if __name__ == "__main__":
    main()
