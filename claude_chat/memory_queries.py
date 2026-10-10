"""Recognize memory inventories without mistaking them for a topic to retrieve."""

import re


def overview_query(text):
    text = re.sub(r"[\s，。！？、：；,.!?:;]", "", text).casefold()
    text = re.sub(r"^(?:你好|您好|请问|请|hello|hi)", "", text)
    return bool(
        re.fullmatch(
            r"(?:你|您)(?:还|都|现在|到底)*(?:记得|记住|记下|保存)(?:了)?"
            r"(?:什么|些什么|哪些(?:事情|东西|内容|信息|记忆)?|我(?:什么|哪些事情)?)(?:吗|呢)?"
            r"|(?:你|您)(?:对我|关于我)(?:都|还)?(?:记得|知道|了解)(?:什么|些什么|哪些信息)(?:吗|呢)?"
            r"|(?:你|您)(?:有|保存了)(?:哪些|什么)(?:关于我的)?记忆"
            r"|(?:我们|我和你)(?:之前|以前|最近|都|曾经|这段时间)*(?:聊|讨论)(?:过|了)?"
            r"(?:什么|些什么|哪些(?:事情|东西|内容|话题)?)(?:吗|呢)?"
            r"|(?:总结|列出|看看)(?:一下)?(?:你对我的|关于我的|我的|已保存的)?(?:记忆|历史话题)"
            r"|whatdoyouremember(?:aboutme)?|whatdoyouknowaboutme|doyourememberme"
            r"|what(?:havewe|didwe)(?:talk|talked|discuss|discussed)about|(?:list|show)(?:my|your)savedmemories",
            text,
        )
    )


def low_information(text):
    """Pure greetings/acknowledgements and inventory questions are not useful history excerpts."""
    normalized = re.sub(r"[\s，。！？、：；,.!?:;]", "", text).casefold()
    return overview_query(text) or normalized in {
        "你好",
        "您好",
        "嗨",
        "在吗",
        "谢谢",
        "好的",
        "好",
        "明白",
        "收到",
        "嗯",
        "ok",
        "okay",
        "hi",
        "hello",
        "thanks",
        "thankyou",
    }
