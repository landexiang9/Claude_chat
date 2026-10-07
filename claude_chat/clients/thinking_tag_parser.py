"""
流式思考标签解析器 (Streaming Thinking-Tag Parser)

部分模型/聚合服务(如 gemini-3.5-flash preview、opencode go 等 OpenAI 兼容聚合端点)
不在协议层分离思考与正文,而是把整段思考直接写在正文里,用标签包裹,常见形式:

    <thought>思考过程...</thought>正文内容...

它们在流式 delta 中以普通 text/content 形式到达(没有 reasoning_content、没有 thought=True
的 part),导致全部内容被当成正文渲染到气泡里,「思考过程」框为空。

本解析器是一个有状态的状态机:逐块喂入文本增量,实时把标签内文本路由到思考通道,
标签外文本路由到正文通道,并容忍标签跨 chunk 切断的情况。

用法:
    p = ThinkingTagStreamParser()
    for chunk_text in chunks:
        text_part, thinking_part = p.feed(chunk_text)
        if thinking_part: queue.put(("thinking", thinking_part))
        if text_part:     queue.put(("text", text_part))
    text_part, thinking_part = p.flush()  # 结束时取走残余(下文)

注意:只在协议层未分离思考(无 reasoning_content / 无 thought part)时启用本解析器。
若模型已通过协议字段分离思考,正文里不会出现这些标签,本解析器也能正确放行。
"""

import re

# 支持的思考开标签名(大小写不敏感,匹配 <tag ...>,闭标签为同名 </tag>)。
# 命中开标签后,以同名的闭标签作为思考段结束。新增格式在此扩展即可。
_THINK_TAGS = ("thought", "think", "reasoning")

# 匹配一个完整标签(开或闭),容忍属性:<thought ...> / </thought>
_TAG_RE = re.compile(r"<(/?)(\w+)([^>]*)>", re.IGNORECASE)


class ThinkingTagStreamParser:
    def __init__(self, tags=_THINK_TAGS):
        self._open_tags = tuple(t.lower() for t in tags)
        self._in_thinking = False       # 当前是否正处于思考标签内
        self._cur_open_tag = None       # 当前打开的标签名(小写)
        self._pending = ""              # 尚未判定归属的尾部缓冲(可能是不完整标签前缀)

    def feed(self, chunk):
        """喂入一段文本增量,返回 (text_part, thinking_part),两者均可能为 ""。"""
        if not chunk:
            return ("", "")
        buf = self._pending + chunk
        self._pending = ""

        out_text = []
        out_thinking = []
        pos = 0
        while pos < len(buf):
            lt = buf.find("<", pos)
            if lt == -1:
                self._append_current(buf[pos:], out_text, out_thinking)
                break
            if lt > pos:
                self._append_current(buf[pos:lt], out_text, out_thinking)

            gt = buf.find(">", lt + 1)
            if gt == -1:
                candidate = buf[lt:]
                if self._could_be_partial_tag(candidate):
                    self._pending = candidate
                else:
                    self._append_current(candidate, out_text, out_thinking)
                break

            raw_tag = buf[lt:gt + 1]
            match = _TAG_RE.fullmatch(raw_tag)
            if match:
                closing = match.group(1) == "/"
                tagname = match.group(2).lower()
                attrs = match.group(3).strip()
                if self._in_thinking and closing and tagname == self._cur_open_tag:
                    self._in_thinking = False
                    self._cur_open_tag = None
                elif not self._in_thinking and not closing and tagname in self._open_tags and attrs != "/":
                    self._in_thinking = True
                    self._cur_open_tag = tagname
                else:
                    # Similar or nested tags are ordinary content. In particular,
                    # </thoughtful> must never close a <thought> block.
                    self._append_current(raw_tag, out_text, out_thinking)
            else:
                self._append_current(raw_tag, out_text, out_thinking)
            pos = gt + 1

        return ("".join(out_text), "".join(out_thinking))

    def flush(self):
        """流结束时取走残余缓冲。返回 (text_part, thinking_part)。"""
        rem = self._pending
        was_thinking = self._in_thinking
        self._pending = ""
        self._in_thinking = False
        self._cur_open_tag = None
        if not rem:
            return ("", "")
        if was_thinking:
            # 被截断/未写闭标签:残余视为思考内容
            return ("", rem)
        return (rem, "")

    def _append_current(self, value, out_text, out_thinking):
        if not value:
            return
        (out_thinking if self._in_thinking else out_text).append(value)

    def _could_be_partial_tag(self, candidate):
        """Whether an unterminated ``<...`` suffix can still become a supported tag."""
        value = candidate.lower()
        for tag in self._open_tags:
            for closing in (False, True):
                prefix = f"</{tag}" if closing else f"<{tag}"
                if prefix.startswith(value):
                    return True
                if value.startswith(prefix):
                    remainder = value[len(prefix):]
                    if not remainder or remainder[0].isspace() or (not closing and remainder[0] == "/"):
                        return True
        return False
