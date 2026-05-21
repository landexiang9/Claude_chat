"""
Claude Chat - Desktop GUI for Anthropic Claude API
customtkinter UI, model switching, file upload, conversation history,
token counting, proxy settings, Markdown rendering
"""

import json
import os
import re
import base64
import uuid
import queue
import threading
import mimetypes
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from customtkinter import filedialog
import httpx
from anthropic import Anthropic, APIStatusError, APITimeoutError, BadRequestError

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"
CONVERSATIONS_DIR = BASE_DIR / "conversations"

FALLBACK_MODELS = [
    "claude-sonnet-4-20250514",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
]

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PDF_EXTENSIONS = {".pdf"}
TEXT_EXTENSIONS = {".txt", ".py", ".js", ".ts", ".html", ".css", ".md", ".json",
                   ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".c", ".cpp",
                   ".h", ".hpp", ".java", ".go", ".rs", ".rb", ".php", ".sh", ".bat",
                   ".ps1", ".sql", ".r", ".swift", ".kt", ".scala", ".lua", ".csv"}

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

MD_TAG_CODE_BLOCK = "md_code_block"
MD_TAG_INLINE_CODE = "md_inline_code"
MD_TAG_H1 = "md_h1"
MD_TAG_H2 = "md_h2"
MD_TAG_H3 = "md_h3"
MD_TAG_BOLD = "md_bold"
MD_TAG_ITALIC = "md_italic"
MD_TAG_BLOCKQUOTE = "md_blockquote"
MD_TAG_HR = "md_hr"
MD_TAG_TABLE = "md_table"
MD_TAG_TABLE_HEADER = "md_table_header"
MD_TAG_TABLE_SEP = "md_table_sep"


class ConfigManager:
    def __init__(self):
        self.data = {
            "api_key": "",
            "model": "claude-sonnet-4-20250514",
            "temperature": 0.7,
            "max_tokens": 4096,
            "thinking_enabled": False,
            "thinking_type": "adaptive",
            "thinking_budget": 16000,
            "proxy_mode": "system",
            "proxy_url": "",
        }
        self.load()

    def load(self):
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self.data.update(loaded)
            except (json.JSONDecodeError, Exception):
                pass

    def save(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()


class ConversationManager:
    def __init__(self):
        CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
        self._conversations = []

    @property
    def conversations(self):
        return self._conversations

    def refresh(self):
        self._conversations = []
        for f in sorted(CONVERSATIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (json.JSONDecodeError, Exception):
                continue
            self._conversations.append({
                "id": data.get("id", f.stem),
                "title": data.get("title", "\u672a\u547d\u540d\u5bf9\u8bdd"),
                "model": data.get("model", ""),
                "updated_at": data.get("updated_at", ""),
                "messages": data.get("messages", []),
            })
        return self._conversations

    def load_conversation(self, conv_id):
        path = CONVERSATIONS_DIR / f"{conv_id}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_conversation(self, conv_data):
        conv_data["updated_at"] = datetime.now().isoformat()
        path = CONVERSATIONS_DIR / f"{conv_data['id']}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(conv_data, f, indent=2, ensure_ascii=False)

    def delete_conversation(self, conv_id):
        path = CONVERSATIONS_DIR / f"{conv_id}.json"
        if path.exists():
            path.unlink()
        self.refresh()

    def new_conversation(self):
        conv_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat()
        data = {
            "id": conv_id, "title": "\u65b0\u5bf9\u8bdd",
            "created_at": now, "updated_at": now,
            "model": "", "temperature": 0.7, "max_tokens": 4096,
            "thinking": None, "messages": [],
        }
        self.save_conversation(data)
        self.refresh()
        return data

    def auto_clean(self, keep=50):
        files = sorted(CONVERSATIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime)
        for f in files[:-keep]:
            f.unlink()


def get_mime_type(file_path):
    ext = Path(file_path).suffix.lower()
    mime_map = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf",
        ".txt": "text/plain", ".py": "text/x-python", ".js": "text/javascript",
        ".ts": "text/typescript", ".html": "text/html", ".css": "text/css",
        ".json": "application/json", ".xml": "application/xml",
        ".yaml": "text/yaml", ".yml": "text/yaml", ".md": "text/markdown",
        ".csv": "text/csv", ".sql": "text/x-sql",
    }
    if ext in mime_map:
        return mime_map[ext]
    mime, _ = mimetypes.guess_type(file_path)
    return mime or "application/octet-stream"


def build_message_content(text, file_paths):
    content = []
    if text.strip():
        content.append({"type": "text", "text": text})
    for fp in file_paths:
        ext = Path(fp).suffix.lower()
        mime = get_mime_type(fp)
        if ext in IMAGE_EXTENSIONS:
            try:
                with open(fp, "rb") as f:
                    b64_data = base64.b64encode(f.read()).decode("utf-8")
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": b64_data}
                })
            except Exception as e:
                content.append({"type": "text", "text": f"\n[\u6587\u4ef6\u8bfb\u53d6\u5931\u8d25: {fp} - {e}]"})
        elif ext in PDF_EXTENSIONS:
            try:
                with open(fp, "rb") as f:
                    b64_data = base64.b64encode(f.read()).decode("utf-8")
                content.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": b64_data}
                })
            except Exception as e:
                content.append({"type": "text", "text": f"\n[\u6587\u4ef6\u8bfb\u53d6\u5931\u8d25: {fp} - {e}]"})
        elif ext in TEXT_EXTENSIONS:
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    file_text = f.read()
                content.append({"type": "text", "text": f"\n\n--- \u6587\u4ef6: {Path(fp).name} ---\n{file_text}\n--- \u6587\u4ef6\u7ed3\u675f ---"})
            except Exception as e:
                content.append({"type": "text", "text": f"\n[\u6587\u4ef6\u8bfb\u53d6\u5931\u8d25: {fp} - {e}]"})
        else:
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    file_text = f.read()
                content.append({"type": "text", "text": f"\n\n--- \u6587\u4ef6: {Path(fp).name} ---\n{file_text}\n--- \u6587\u4ef6\u7ed3\u675f ---"})
            except Exception:
                content.append({"type": "text", "text": f"\n[\u4e0d\u652f\u6301\u7684\u6587\u4ef6\u7c7b\u578b: {Path(fp).name}]"})
    return content


def extract_api_message(msg):
    role = msg.get("role")
    content = msg.get("content")
    if not isinstance(content, (list, str)):
        return {"role": role, "content": [{"type": "text", "text": str(content)}]}
    items_source = content if isinstance(content, list) else [{"type": "text", "text": content}]
    api_content_list = []
    for item in items_source:
        if isinstance(item, dict) and item.get("type") == "image":
            source = item.get("source", {})
            if "file_path" in source:
                try:
                    with open(source["file_path"], "rb") as f:
                        b64_data = base64.b64encode(f.read()).decode("utf-8")
                    api_content_list.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": source.get("media_type", "image/png"), "data": b64_data}
                    })
                except Exception:
                    fname = Path(source.get("file_path", "")).name
                    api_content_list.append({"type": "text", "text": f"[\u56fe\u7247\u4e0d\u53ef\u7528: {fname}]"})
            elif "data" in source:
                api_content_list.append(item)
        elif isinstance(item, dict) and item.get("type") == "document":
            source = item.get("source", {})
            if "file_path" in source:
                try:
                    with open(source["file_path"], "rb") as f:
                        b64_data = base64.b64encode(f.read()).decode("utf-8")
                    api_content_list.append({
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": b64_data}
                    })
                except Exception:
                    fname = Path(source.get("file_path", "")).name
                    api_content_list.append({"type": "text", "text": f"[\u6587\u6863\u4e0d\u53ef\u7528: {fname}]"})
            elif "data" in source:
                api_content_list.append(item)
        elif isinstance(item, str):
            api_content_list.append({"type": "text", "text": item})
        elif isinstance(item, dict):
            api_content_list.append(item)
        else:
            api_content_list.append({"type": "text", "text": str(item)})
    return {"role": role, "content": api_content_list}


def build_http_client(proxy_mode="system", proxy_url=""):
    if proxy_mode == "none":
        return httpx.Client(trust_env=False)
    elif proxy_mode == "custom" and proxy_url.strip():
        return httpx.Client(proxy=proxy_url.strip(), trust_env=False)
    else:
        return httpx.Client()


def render_markdown(textbox, text):
    tb = textbox._textbox
    tb.configure(state="normal")
    tb.delete("0.0", "end")
    if not text:
        tb.configure(state="disabled")
        return

    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("```"):
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            if code_lines:
                code_start = tb.index("end-1c")
                tb.insert("end", "\n".join(code_lines) + "\n")
                code_end = tb.index("end-1c")
                tb.tag_add(MD_TAG_CODE_BLOCK, code_start, code_end)
            continue

        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            table_lines = [line]
            j = i + 1
            while j < len(lines):
                ns = lines[j].strip()
                if ns.startswith("|") and ns.endswith("|"):
                    table_lines.append(lines[j])
                    j += 1
                else:
                    break
            _render_table(tb, table_lines)
            i = j
            continue

        if line.startswith("### "):
            tb.insert("end", line[4:] + "\n", MD_TAG_H3)
        elif line.startswith("## "):
            tb.insert("end", line[3:] + "\n", MD_TAG_H2)
        elif line.startswith("# "):
            tb.insert("end", line[2:] + "\n", MD_TAG_H1)
        elif line.startswith("> "):
            tb.insert("end", "  " + line[2:] + "\n", MD_TAG_BLOCKQUOTE)
            _apply_inline_tags(tb)
        elif re.match(r"^---+\s*$", line.strip()):
            tb.insert("end", "\u2500" * 40 + "\n", MD_TAG_HR)
        elif re.match(r"^\s*[-*+]\s", line):
            tb.insert("end", "  \u2022 " + re.sub(r"^\s*[-*+]\s", "", line) + "\n")
            _apply_inline_tags(tb)
        elif re.match(r"^\s*\d+\.\s", line):
            tb.insert("end", "  " + line.strip() + "\n")
            _apply_inline_tags(tb)
        elif line.strip() == "":
            tb.insert("end", "\n")
        else:
            tb.insert("end", line + "\n")
            _apply_inline_tags(tb)

        i += 1

    tb.configure(state="disabled")


def _apply_inline_tags(textbox):
    tb = textbox
    line_start = tb.index("end-2c linestart")
    line_end = tb.index("end-2c lineend")
    line_text = tb.get(line_start, line_end)

    for match in re.finditer(r"`([^`]+)`", line_text):
        start = f"{line_start}+{match.start()}c"
        end = f"{line_start}+{match.end()}c"
        tb.tag_add(MD_TAG_INLINE_CODE, start, end)

    for match in re.finditer(r"\*\*([^*]+)\*\*", line_text):
        start = f"{line_start}+{match.start()}c"
        end = f"{line_start}+{match.end()}c"
        tb.tag_add(MD_TAG_BOLD, start, end)

    for match in re.finditer(r"(?<!\*)\*([^*]+)\*(?!\*)", line_text):
        start = f"{line_start}+{match.start()}c"
        end = f"{line_start}+{match.end()}c"
        tb.tag_add(MD_TAG_ITALIC, start, end)


def _display_width(s):
    w = 0
    for ch in s:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x303F or
                0xFF00 <= cp <= 0xFFEF or 0x2E80 <= cp <= 0x2FFF):
            w += 2
        else:
            w += 1
    return w


def _render_table(tb, table_lines):
    rows = []
    for line in table_lines:
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        cells = [c.strip() for c in line.split("|")]
        rows.append(cells)

    if len(rows) < 2:
        for line in table_lines:
            tb.insert("end", line + "\n")
        return

    header = rows[0]
    sep = rows[1]
    body = rows[2:] if len(rows) > 2 else []
    ncols = len(header)

    if not all(re.match(r"^[:\- ]+$", c) for c in sep):
        for line in table_lines:
            tb.insert("end", line + "\n")
        return

    all_rows = [header] + body
    col_widths = [0] * ncols
    for row in all_rows:
        for i in range(min(len(row), ncols)):
            col_widths[i] = max(col_widths[i], _display_width(row[i]))

    def _pad_row(row):
        parts = []
        for i in range(ncols):
            cell = row[i] if i < len(row) else ""
            pad = max(0, col_widths[i] - _display_width(cell))
            parts.append(cell + " " * pad)
        return " | ".join(parts)

    line_start = tb.index("end-1c")
    tb.insert("end", _pad_row(header) + "\n", (MD_TAG_TABLE_HEADER,))
    _apply_inline_tags(tb)
    sep_width = sum(col_widths) + 3 * (ncols - 1)
    tb.insert("end", "-" * sep_width + "\n", MD_TAG_TABLE_SEP)
    for row in body:
        tb.insert("end", _pad_row(row) + "\n", MD_TAG_TABLE)
        _apply_inline_tags(tb)


def configure_markdown_tags(textbox):
    tb = textbox._textbox
    code_font = ("Consolas", 12)
    bold_font = ("", 13, "bold")
    h1_font = ("", 18, "bold")
    h2_font = ("", 15, "bold")
    h3_font = ("", 13, "bold")
    italic_font = ("", 13, "italic")

    tb.tag_configure(MD_TAG_CODE_BLOCK, font=code_font, background="#1E1E2E",
                     foreground="#CDD6F4", lmargin1=10, lmargin2=10, rmargin=10,
                     spacing1=4, spacing3=4)
    tb.tag_configure(MD_TAG_INLINE_CODE, font=code_font, foreground="#F38BA8",
                     background="#1E1E2E")
    tb.tag_configure(MD_TAG_H1, font=h1_font, foreground="#89B4FA", spacing1=12, spacing3=4)
    tb.tag_configure(MD_TAG_H2, font=h2_font, foreground="#89B4FA", spacing1=8, spacing3=4)
    tb.tag_configure(MD_TAG_H3, font=h3_font, foreground="#89B4FA", spacing1=6, spacing3=2)
    tb.tag_configure(MD_TAG_BOLD, font=bold_font)
    tb.tag_configure(MD_TAG_ITALIC, font=italic_font)
    tb.tag_configure(MD_TAG_BLOCKQUOTE, foreground="#A6ADC8", lmargin1=15, lmargin2=15,
                     background="#2A2A3A", spacing1=4, spacing3=4)
    tb.tag_configure(MD_TAG_HR, foreground="#45475A", spacing1=8, spacing3=8)
    tb.tag_configure(MD_TAG_TABLE, font=("Consolas", 12), foreground="#CDD6F4", lmargin1=8, lmargin2=8,
                     spacing1=2, spacing3=2)
    tb.tag_configure(MD_TAG_TABLE_HEADER, font=("Consolas", 12, "bold"), foreground="#89B4FA",
                     lmargin1=8, lmargin2=8)
    tb.tag_configure(MD_TAG_TABLE_SEP, font=("Consolas", 12), foreground="#45475A", lmargin1=8, lmargin2=8)


def _fit_textbox_to_content(textbox, text):
    lines = text.count("\n") + 1
    height = max(48, min(lines * 22 + 12, 650))
    textbox.configure(height=height)


class ProxyDialog(ctk.CTkToplevel):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config
        self.result = None
        self.title("\u4ee3\u7406\u8bbe\u7f6e")
        self.geometry("420x300")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ctk.CTkFrame(self)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(frame, text="\ud83c\udf10 \u4ee3\u7406\u8bbe\u7f6e", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(10, 15))

        self.mode_var = ctk.StringVar(value=config.get("proxy_mode", "system"))

        modes_frame = ctk.CTkFrame(frame, fg_color="transparent")
        modes_frame.pack(pady=(0, 10))

        ctk.CTkRadioButton(modes_frame, text="\u4e0d\u4f7f\u7528\u4ee3\u7406", variable=self.mode_var,
                          value="none", command=self._toggle_url).pack(anchor="w", pady=3)
        ctk.CTkRadioButton(modes_frame, text="\u4f7f\u7528\u7cfb\u7edf\u4ee3\u7406 (\u73af\u5883\u53d8\u91cf)", variable=self.mode_var,
                          value="system", command=self._toggle_url).pack(anchor="w", pady=3)
        ctk.CTkRadioButton(modes_frame, text="\u81ea\u5b9a\u4e49\u4ee3\u7406 URL:", variable=self.mode_var,
                          value="custom", command=self._toggle_url).pack(anchor="w", pady=3)

        self.url_frame = ctk.CTkFrame(frame, fg_color="transparent")
        self.url_frame.pack(fill="x", pady=(0, 10), padx=10)

        self.proxy_entry = ctk.CTkEntry(self.url_frame, placeholder_text="http://127.0.0.1:10808")
        self.proxy_entry.insert(0, config.get("proxy_url", ""))
        self.proxy_entry.pack(fill="x")
        self._toggle_url()

        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.pack(pady=10)
        ctk.CTkButton(btn_frame, text="\u4fdd\u5b58", command=self._on_save).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="\u53d6\u6d88", command=self.destroy, fg_color="gray40").pack(side="left", padx=5)

    def _toggle_url(self):
        if self.mode_var.get() == "custom":
            self.url_frame.pack(fill="x", pady=(0, 10), padx=10)
        else:
            self.url_frame.pack_forget()

    def _on_save(self):
        self.result = {
            "proxy_mode": self.mode_var.get(),
            "proxy_url": self.proxy_entry.get().strip(),
        }
        self.destroy()


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config
        self.result = None
        self.title("\u53c2\u6570\u8bbe\u7f6e")
        self.geometry("420x420")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ctk.CTkFrame(self)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        ctk.CTkLabel(frame, text="\u2699 \u5bf9\u8bdd\u53c2\u6570\u8bbe\u7f6e", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(10, 5))

        ctk.CTkLabel(frame, text=f"Temperature: {self.config.get('temperature', 0.7):.2f}").pack()
        self.temp_var = ctk.DoubleVar(value=self.config.get("temperature", 0.7))
        self.temp_slider = ctk.CTkSlider(frame, from_=0.0, to=2.0, number_of_steps=40,
                                         variable=self.temp_var,
                                         command=lambda v: self._update_temp_label(v))
        self.temp_slider.pack(pady=(0, 10), padx=20, fill="x")
        self.temp_label = ctk.CTkLabel(frame, text=f"{self.config.get('temperature', 0.7):.2f}")
        self.temp_label.pack()

        ctk.CTkLabel(frame, text="Max Tokens:").pack(pady=(10, 0))
        self.max_tokens_entry = ctk.CTkEntry(frame)
        self.max_tokens_entry.insert(0, str(self.config.get("max_tokens", 4096)))
        self.max_tokens_entry.pack(pady=(0, 10))

        ctk.CTkLabel(frame, text="Extended Thinking:").pack()
        self.thinking_frame = ctk.CTkFrame(frame, fg_color="transparent")
        self.thinking_frame.pack(pady=(0, 10))

        self.thinking_var = ctk.StringVar(value="disabled")
        if self.config.get("thinking_enabled"):
            ttype = self.config.get("thinking_type", "adaptive")
            self.thinking_var.set(ttype)

        ctk.CTkRadioButton(self.thinking_frame, text="Adaptive", variable=self.thinking_var,
                          value="adaptive", command=self._toggle_budget).pack(side="left", padx=5)
        ctk.CTkRadioButton(self.thinking_frame, text="Enabled", variable=self.thinking_var,
                          value="enabled", command=self._toggle_budget).pack(side="left", padx=5)
        ctk.CTkRadioButton(self.thinking_frame, text="Disabled", variable=self.thinking_var,
                          value="disabled", command=self._toggle_budget).pack(side="left", padx=5)

        self.budget_frame = ctk.CTkFrame(frame, fg_color="transparent")
        self.budget_frame.pack(pady=(0, 10))
        ctk.CTkLabel(self.budget_frame, text="Budget Tokens:").pack(side="left", padx=5)
        self.budget_entry = ctk.CTkEntry(self.budget_frame, width=100)
        self.budget_entry.insert(0, str(self.config.get("thinking_budget", 16000)))
        self.budget_entry.pack(side="left", padx=5)
        self._toggle_budget()

        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.pack(pady=10)
        ctk.CTkButton(btn_frame, text="\u4fdd\u5b58", command=self._on_save).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="\u53d6\u6d88", command=self.destroy, fg_color="gray40").pack(side="left", padx=5)

    def _update_temp_label(self, val):
        self.temp_label.configure(text=f"{float(val):.2f}")

    def _toggle_budget(self):
        if self.thinking_var.get() == "disabled":
            self.budget_frame.pack_forget()
        else:
            self.budget_frame.pack(pady=(0, 10))

    def _on_save(self):
        thinking_type = self.thinking_var.get()
        self.result = {
            "temperature": self.temp_var.get(),
            "max_tokens": int(self.max_tokens_entry.get()),
            "thinking_enabled": thinking_type != "disabled",
            "thinking_type": thinking_type,
            "thinking_budget": int(self.budget_entry.get() or 16000),
        }
        self.destroy()


class ClaudeChatApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Claude Chat")
        self.geometry("1200x800")
        self.minsize(900, 600)

        self.config = ConfigManager()
        self.conv_manager = ConversationManager()

        self.current_conv = None
        self.available_models = list(FALLBACK_MODELS)
        self.streaming_queue = queue.Queue()
        self.is_streaming = False
        self.streaming_text = ""
        self.streaming_thinking_text = ""
        self.streaming_md_container = None
        self.attached_files = []
        self.message_containers = []
        self.textbox_height = 3

        try:
            self.iconbitmap(default="")
        except Exception:
            pass

        self._build_ui()
        self._load_conversations()
        self._refresh_models_async()
        self._start_queue_processor()

        if not self.config.get("api_key"):
            self.after(500, self._prompt_api_key)

    def _build_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        left_panel = ctk.CTkFrame(self, width=260, corner_radius=0)
        left_panel.grid(row=0, column=0, rowspan=3, sticky="nsw")
        left_panel.grid_propagate(False)

        ctk.CTkLabel(left_panel, text="\u5bf9\u8bdd\u5217\u8868", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(10, 5))

        self.conv_list_frame = ctk.CTkScrollableFrame(left_panel)
        self.conv_list_frame.pack(fill="both", expand=True, padx=5, pady=5)

        new_btn = ctk.CTkButton(left_panel, text="+ \u65b0\u5efa\u5bf9\u8bdd", command=self._new_conversation)
        new_btn.pack(pady=(5, 10), padx=10, fill="x")

        top_bar = ctk.CTkFrame(self, height=45, corner_radius=0)
        top_bar.grid(row=0, column=1, sticky="new", padx=0, pady=0)
        top_bar.grid_propagate(False)

        ctk.CTkLabel(top_bar, text="\u6a21\u578b:", font=ctk.CTkFont(size=13)).pack(side="left", padx=(10, 5))
        self.model_var = ctk.StringVar(value=self.config.get("model", FALLBACK_MODELS[0]))
        self.model_menu = ctk.CTkOptionMenu(top_bar, values=self.available_models,
                                            variable=self.model_var, command=self._on_model_change)
        self.model_menu.pack(side="left", padx=5)

        ctk.CTkButton(top_bar, text="\u2699 \u8bbe\u7f6e", width=70, command=self._open_settings).pack(side="left", padx=5)
        ctk.CTkButton(top_bar, text="\ud83c\udf10 \u4ee3\u7406", width=70, command=self._open_proxy_settings).pack(side="left", padx=5)
        ctk.CTkButton(top_bar, text="\ud83d\uddd1 \u6e05\u7a7a\u5bf9\u8bdd", width=80, command=self._clear_chat).pack(side="left", padx=5)

        proxy_mode = self.config.get("proxy_mode", "system")
        mode_labels = {"none": "\u65e0\u4ee3\u7406", "system": "\u7cfb\u7edf\u4ee3\u7406", "custom": "\u81ea\u5b9a\u4e49"}
        self.proxy_indicator = ctk.CTkLabel(top_bar, text=f"| {mode_labels.get(proxy_mode, proxy_mode)}",
                                           font=ctk.CTkFont(size=10), text_color="gray60")
        self.proxy_indicator.pack(side="left", padx=5)

        self.token_label = ctk.CTkLabel(top_bar, text="Token: --", font=ctk.CTkFont(size=12))
        self.token_label.pack(side="right", padx=10)

        self.chat_frame = ctk.CTkScrollableFrame(self)
        self.chat_frame.grid(row=1, column=1, sticky="nsew", padx=5, pady=5)

        input_area = ctk.CTkFrame(self, height=80, corner_radius=0)
        input_area.grid(row=2, column=1, sticky="sew", padx=5, pady=(0, 5))
        input_area.grid_propagate(False)

        self.file_frame = ctk.CTkFrame(input_area, fg_color="transparent", height=25)
        self.file_frame.pack(fill="x", padx=5, pady=(2, 0))
        self.file_label = ctk.CTkLabel(self.file_frame, text="", font=ctk.CTkFont(size=11), text_color="gray70")
        self.file_label.pack(side="left")

        input_row = ctk.CTkFrame(input_area, fg_color="transparent")
        input_row.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        self.input_box = ctk.CTkTextbox(input_row, height=35)
        self.input_box.pack(side="left", fill="both", expand=True, padx=(0, 5))

        attach_btn = ctk.CTkButton(input_row, text="\ud83d\udcce", width=40, command=self._attach_files)
        attach_btn.pack(side="left", padx=2)

        send_btn = ctk.CTkButton(input_row, text="\u53d1\u9001", width=60, command=self._send_message)
        send_btn.pack(side="left", padx=2)
        self.send_btn = send_btn

        self.input_box.bind("<Control-Return>", lambda e: self._send_message())
        self.input_box.bind("<Shift-Return>", lambda e: None)

        bottom_bar = ctk.CTkFrame(self, height=35, corner_radius=0)
        bottom_bar.grid(row=3, column=0, columnspan=2, sticky="sew")
        bottom_bar.grid_propagate(False)

        ctk.CTkLabel(bottom_bar, text="API Key:", font=ctk.CTkFont(size=12)).pack(side="left", padx=(10, 5))
        self.api_key_entry = ctk.CTkEntry(bottom_bar, show="*", width=300)
        if self.config.get("api_key"):
            self.api_key_entry.insert(0, self.config.get("api_key"))
        self.api_key_entry.pack(side="left", padx=5)

        ctk.CTkButton(bottom_bar, text="\u2713 \u4fdd\u5b58", width=60,
                      command=self._save_api_key).pack(side="left", padx=5)

        self.status_label = ctk.CTkLabel(bottom_bar, text="", font=ctk.CTkFont(size=11), text_color="gray70")
        self.status_label.pack(side="right", padx=10)

    def _prompt_api_key(self):
        dialog = ctk.CTkInputDialog(text="\u8bf7\u8f93\u5165 Anthropic API Key:", title="API Key \u8bbe\u7f6e")
        api_key = dialog.get_input()
        if api_key:
            self.config.set("api_key", api_key.strip())
            self.api_key_entry.delete(0, "end")
            self.api_key_entry.insert(0, api_key.strip())
            self._refresh_models_async()

    def _load_conversations(self):
        self.conv_manager.auto_clean(keep=50)
        self.conv_manager.refresh()
        self._refresh_conv_list()

    def _refresh_conv_list(self):
        for w in self.conv_list_frame.winfo_children():
            w.destroy()
        for conv in self.conv_manager.conversations:
            frame = ctk.CTkFrame(self.conv_list_frame, corner_radius=6)
            frame.pack(fill="x", padx=5, pady=2)
            title_text = conv.get("title", "\u672a\u547d\u540d\u5bf9\u8bdd")
            if len(title_text) > 20:
                title_text = title_text[:20] + "..."
            lbl = ctk.CTkLabel(frame, text=title_text, cursor="hand2", anchor="w")
            lbl.pack(side="left", fill="x", expand=True, padx=8, pady=5)
            lbl.bind("<Button-1>", lambda e, cid=conv["id"]: self._load_conversation(cid))
            menu_btn = ctk.CTkButton(frame, text="...", width=28, height=20,
                                     command=lambda cid=conv["id"]: self._show_conv_menu(cid))
            menu_btn.pack(side="right", padx=(0, 5), pady=5)
            if self.current_conv and self.current_conv.get("id") == conv["id"]:
                frame.configure(fg_color="#2B5B84")

    def _show_conv_menu(self, conv_id):
        menu = ctk.CTkToplevel(self)
        menu.title("")
        menu.geometry("160x110")
        menu.resizable(False, False)
        menu.transient(self)
        menu.grab_set()
        menu.overrideredirect(True)
        x, y = self.winfo_pointerx(), self.winfo_pointery()
        menu.geometry(f"+{x}+{y}")
        frame = ctk.CTkFrame(menu)
        frame.pack(fill="both", expand=True)
        ctk.CTkButton(frame, text="\u91cd\u547d\u540d", command=lambda: [menu.destroy(), self._rename_conversation(conv_id)],
                      fg_color="transparent", hover_color="#3B3B3B").pack(fill="x", padx=2, pady=(2, 0))
        ctk.CTkButton(frame, text="\u5220\u9664", command=lambda: [menu.destroy(), self._delete_conversation(conv_id)],
                      fg_color="transparent", hover_color="#8B0000", text_color="#FF6B6B").pack(fill="x", padx=2, pady=0)
        ctk.CTkButton(frame, text="\u53d6\u6d88", command=menu.destroy,
                      fg_color="transparent", hover_color="#3B3B3B").pack(fill="x", padx=2, pady=(0, 2))

    def _rename_conversation(self, conv_id):
        dialog = ctk.CTkInputDialog(text="\u8bf7\u8f93\u5165\u65b0\u540d\u79f0:", title="\u91cd\u547d\u540d\u5bf9\u8bdd")
        new_name = dialog.get_input()
        if new_name:
            conv = self.conv_manager.load_conversation(conv_id)
            if conv:
                conv["title"] = new_name.strip()
                self.conv_manager.save_conversation(conv)
                if self.current_conv and self.current_conv.get("id") == conv_id:
                    self.current_conv = conv
                self._load_conversations()

    def _delete_conversation(self, conv_id):
        dialog = ctk.CTkToplevel(self)
        dialog.title("\u786e\u8ba4\u5220\u9664")
        dialog.geometry("300x120")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text="\u786e\u5b9a\u8981\u5220\u9664\u8fd9\u4e2a\u5bf9\u8bdd\u5417\uff1f\n\u6b64\u64cd\u4f5c\u4e0d\u53ef\u6062\u590d\u3002").pack(pady=20)
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack()
        ctk.CTkButton(btn_frame, text="\u786e\u8ba4\u5220\u9664", fg_color="#8B0000", hover_color="#A00000",
                      command=lambda: [dialog.destroy(), self._do_delete(conv_id)]).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="\u53d6\u6d88", command=dialog.destroy, fg_color="gray40").pack(side="left", padx=5)

    def _do_delete(self, conv_id):
        if self.current_conv and self.current_conv.get("id") == conv_id:
            self._new_conversation(silent=True)
        self.conv_manager.delete_conversation(conv_id)
        self._refresh_conv_list()

    def _new_conversation(self, silent=False):
        conv_data = self.conv_manager.new_conversation()
        conv_data["model"] = self.model_var.get()
        conv_data["temperature"] = self.config.get("temperature", 0.7)
        conv_data["max_tokens"] = self.config.get("max_tokens", 4096)
        if self.config.get("thinking_enabled"):
            conv_data["thinking"] = {
                "type": self.config.get("thinking_type", "adaptive"),
                "budget_tokens": self.config.get("thinking_budget", 16000),
            }
        self.conv_manager.save_conversation(conv_data)
        self.current_conv = conv_data
        self._clear_chat_display()
        self._refresh_conv_list()
        self._update_title()
        if not silent:
            self.status_label.configure(text="\u65b0\u5efa\u5bf9\u8bdd")

    def _load_conversation(self, conv_id):
        conv = self.conv_manager.load_conversation(conv_id)
        if conv is None:
            return
        self.current_conv = conv
        if conv.get("model"):
            self.model_var.set(conv["model"])
            self.config.set("model", conv["model"])
        self._clear_chat_display()
        for msg in conv.get("messages", []):
            self._add_message_to_display(msg)
        self._scroll_to_bottom()
        self._refresh_conv_list()
        self._update_title()
        self.status_label.configure(text=f"\u5df2\u52a0\u8f7d\u5bf9\u8bdd: {conv.get('title', '')}")

    def _clear_chat(self):
        if not self.current_conv:
            return
        self.current_conv["messages"] = []
        self.current_conv["updated_at"] = datetime.now().isoformat()
        self.conv_manager.save_conversation(self.current_conv)
        self._clear_chat_display()
        self.status_label.configure(text="\u5bf9\u8bdd\u5df2\u6e05\u7a7a")

    def _clear_chat_display(self):
        for w in self.chat_frame.winfo_children():
            w.destroy()
        self.message_containers.clear()
        self.streaming_md_container = None
        self.streaming_thinking_text = ""

    def _update_title(self):
        if self.current_conv:
            title = self.current_conv.get("title", "\u65b0\u5bf9\u8bdd")
            self.title(f"Claude Chat - {title}")

    def _add_message_to_display(self, msg):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        thinking = msg.get("thinking", "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif item.get("type") == "image":
                        src = item.get("source", {})
                        fname = Path(src.get("file_path", "")).name if src.get("file_path") else "\u56fe\u7247"
                        parts.append(f"[\u56fe\u7247: {fname}]")
                    elif item.get("type") == "document":
                        src = item.get("source", {})
                        fname = Path(src.get("file_path", "")).name if src.get("file_path") else "PDF"
                        parts.append(f"[\u6587\u6863: {fname}]")
                elif isinstance(item, str):
                    parts.append(item)
            text = "\n".join(parts)
        elif isinstance(content, str):
            text = content
        else:
            text = str(content)
        container = self._create_message_container(role, text)
        if thinking and container:
            self._create_thinking_panel(container, thinking)

    def _create_message_container(self, role, text, is_streaming=False):
        container = ctk.CTkFrame(self.chat_frame, corner_radius=10,
                                 fg_color="#1E3A5F" if role == "user" else "#1F2A3A")
        container.pack(fill="x", padx=10, pady=5)

        header_frame = ctk.CTkFrame(container, fg_color="transparent")
        header_frame.pack(fill="x", padx=10, pady=(5, 0))

        role_icon = "\ud83e\uddd1" if role == "user" else "\ud83e\udd16"
        role_name = "You" if role == "user" else "Claude"
        ctk.CTkLabel(header_frame, text=f"{role_icon} {role_name}",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        time_str = datetime.now().strftime("%H:%M")
        ctk.CTkLabel(header_frame, text=time_str,
                     font=ctk.CTkFont(size=10), text_color="gray60").pack(side="right")

        textbox = ctk.CTkTextbox(container, wrap="word",
                                 fg_color="transparent", border_width=0,
                                 font=ctk.CTkFont(size=13))
        textbox.pack(fill="both", expand=True, padx=8, pady=(3, 8))
        configure_markdown_tags(textbox)
        render_markdown(textbox, text)
        _fit_textbox_to_content(textbox, text)

        container._md_textbox = textbox
        container._role = role

        if is_streaming:
            self.streaming_md_container = container
        else:
            self.message_containers.append(container)
        return container

    def _update_textbox_height(self, textbox, text):
        _fit_textbox_to_content(textbox, text)

    def _scroll_to_bottom(self):
        self.chat_frame._parent_canvas.yview_moveto(1.0)

    def _create_thinking_panel(self, container, thinking_text):
        toggle_frame = ctk.CTkFrame(container, fg_color="#2A2545", corner_radius=6)
        children = container.winfo_children()
        md_textbox = container._md_textbox
        for child in children:
            if child is md_textbox:
                toggle_frame.pack(fill="x", padx=10, pady=(0, 2), before=child)
                break
        else:
            toggle_frame.pack(fill="x", padx=10, pady=(0, 2))

        thinking_box = ctk.CTkTextbox(toggle_frame, height=80, wrap="word",
                                      fg_color="#1E1E2E", border_width=0,
                                      font=ctk.CTkFont(size=12))
        thinking_box._textbox.configure(foreground="#C9CBFF")
        thinking_box.insert("0.0", thinking_text)
        thinking_box.configure(state="disabled")

        tokens = len(thinking_text) // 2
        toggle_btn = ctk.CTkButton(toggle_frame, text=f"\u25b6 \u601d\u8003\u8fc7\u7a0b (~{tokens} tokens)",
                                   fg_color="transparent", hover_color="#3A3060",
                                   anchor="w", font=ctk.CTkFont(size=11))
        toggle_btn.pack(fill="x", padx=5, pady=(2, 0))

        def _toggle():
            if thinking_box.winfo_viewable():
                thinking_box.pack_forget()
                toggle_btn.configure(text=f"\u25b6 \u601d\u8003\u8fc7\u7a0b (~{tokens} tokens)")
            else:
                thinking_box.pack(fill="x", padx=5, pady=(2, 5))
                toggle_btn.configure(text=f"\u25bc \u601d\u8003\u8fc7\u7a0b (~{tokens} tokens)")

        toggle_btn.configure(command=_toggle)

        container._thinking_panel = toggle_frame
        container._thinking_box = thinking_box

    def _attach_files(self):
        file_paths = filedialog.askopenfilenames(
            title="\u9009\u62e9\u6587\u4ef6",
            filetypes=[
                ("All Supported", "*.png;*.jpg;*.jpeg;*.gif;*.webp;*.pdf;*.txt;*.py;*.js;*.ts;*.html;*.css;*.md;*.json;*.xml;*.yaml;*.yml;*.csv;*.sql;*.c;*.cpp;*.h"),
                ("Images", "*.png;*.jpg;*.jpeg;*.gif;*.webp"),
                ("PDF", "*.pdf"),
                ("Text", "*.txt;*.py;*.js;*.ts;*.html;*.css;*.md;*.json;*.xml;*.yaml;*.yml;*.csv;*.sql"),
            ]
        )
        if file_paths:
            self.attached_files = list(file_paths)
            names = [Path(f).name for f in file_paths]
            self.file_label.configure(text="\ud83d\udcce " + ", ".join(names[:3]) +
                                      (f" ...+{len(names)-3}" if len(names) > 3 else ""))

    def _remove_attachment(self):
        self.attached_files.clear()
        self.file_label.configure(text="")

    def _send_message(self):
        if self.is_streaming:
            return
        if not self.config.get("api_key"):
            self.status_label.configure(text="\u8bf7\u5148\u8bbe\u7f6e API Key")
            self._prompt_api_key()
            return

        user_input = self.input_box.get("0.0", "end").strip()
        if not user_input and not self.attached_files:
            return

        if self.current_conv is None:
            self._new_conversation(silent=True)

        self.input_box.delete("0.0", "end")

        display_content = user_input
        if self.attached_files:
            display_content += "\n[\u9644\u4ef6: " + ", ".join(Path(f).name for f in self.attached_files) + "]"

        self._create_message_container("user", display_content)
        self._scroll_to_bottom()

        user_msg_display = {"role": "user", "content": user_input}
        if self.attached_files:
            user_msg_display["content"] = [{"type": "text", "text": user_input}]
            for fp in self.attached_files:
                ext = Path(fp).suffix.lower()
                mime = get_mime_type(fp)
                if ext in IMAGE_EXTENSIONS:
                    user_msg_display["content"].append({"type": "image", "source": {"file_path": fp, "media_type": mime}})
                elif ext in PDF_EXTENSIONS:
                    user_msg_display["content"].append({"type": "document", "source": {"file_path": fp, "media_type": "application/pdf"}})
                else:
                    user_msg_display["content"].append({"type": "text", "text": f"[\u9644\u4ef6: {Path(fp).name}]"})

        self.current_conv["messages"].append(user_msg_display)
        self._remove_attachment()

        api_messages = [extract_api_message(msg) for msg in self.current_conv["messages"]]

        self.is_streaming = True
        self.send_btn.configure(state="disabled", text="...")
        self.status_label.configure(text="Claude \u601d\u8003\u4e2d...")
        self.streaming_text = ""
        self.streaming_thinking_text = ""

        stream_container = self._create_message_container("assistant", "\u601d\u8003\u4e2d...", is_streaming=True)
        self.streaming_md_container = stream_container
        self._scroll_to_bottom()

        thinking_config = None
        if self.config.get("thinking_enabled"):
            ttype = self.config.get("thinking_type", "adaptive")
            if ttype == "adaptive":
                thinking_config = {"type": "adaptive"}
            elif ttype == "enabled":
                thinking_config = {"type": "enabled", "budget_tokens": self.config.get("thinking_budget", 16000)}

        thread = threading.Thread(
            target=self._stream_thread,
            args=(api_messages, self.model_var.get(), self.config.get("max_tokens", 4096),
                  self.config.get("temperature", 0.7), thinking_config),
            daemon=True
        )
        thread.start()

    def _stream_thread(self, messages, model, max_tokens, temperature, thinking_config):
        try:
            http_client = build_http_client(
                self.config.get("proxy_mode", "system"),
                self.config.get("proxy_url", "")
            )
            client = Anthropic(api_key=self.config.get("api_key"), http_client=http_client)
            kwargs = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
            }
            if thinking_config:
                kwargs["thinking"] = thinking_config

            with client.messages.stream(**kwargs) as stream:
                for event in stream:
                    etype = event.type if hasattr(event, 'type') else ''
                    if etype == 'thinking':
                        t = getattr(event, 'thinking', '')
                        if t:
                            self.streaming_queue.put(("thinking", t))
                    elif etype == 'content_block_delta':
                        d = event.delta
                        dtypes = getattr(d, 'type', '')
                        if dtypes == 'text_delta':
                            self.streaming_queue.put(("text", getattr(d, 'text', '')))
                        elif dtypes == 'thinking_delta':
                            self.streaming_queue.put(("thinking", getattr(d, 'thinking', '')))

            final = stream.get_final_message()
            input_tokens = final.usage.input_tokens if hasattr(final, 'usage') and final.usage else 0
            output_tokens = final.usage.output_tokens if hasattr(final, 'usage') and final.usage else 0
            self.streaming_queue.put(("done", {"input_tokens": input_tokens, "output_tokens": output_tokens}))
        except BadRequestError as e:
            self.streaming_queue.put(("error", f"\u8bf7\u6c42\u9519\u8bef: {e}"))
        except APITimeoutError:
            self.streaming_queue.put(("error", "\u8bf7\u6c42\u8d85\u65f6\uff0c\u8bf7\u91cd\u8bd5"))
        except APIStatusError as e:
            self.streaming_queue.put(("error", f"API \u9519\u8bef [{e.status_code}]: {e}"))
        except Exception as e:
            self.streaming_queue.put(("error", f"\u672a\u77e5\u9519\u8bef: {e}"))

    def _start_queue_processor(self):
        self._process_queue()

    def _process_queue(self):
        try:
            while True:
                msg = self.streaming_queue.get_nowait()
                msg_type, msg_data = msg

                if msg_type == "text":
                    # first text event: finalize thinking panel
                    if self.streaming_thinking_text and self.streaming_md_container and \
                       not getattr(self.streaming_md_container, '_thinking_created', False):
                        self._create_thinking_panel(self.streaming_md_container, self.streaming_thinking_text)
                        self.streaming_md_container._thinking_created = True

                    self.streaming_text += msg_data
                    if self.streaming_md_container and hasattr(self.streaming_md_container, '_md_textbox'):
                        tb = self.streaming_md_container._md_textbox
                        raw = tb._textbox
                        raw.configure(state="normal")
                        if raw.get("0.0", "end-1c").strip() == "\u601d\u8003\u4e2d...":
                            raw.delete("0.0", "end")
                        raw.delete("end-2c", "end")
                        raw.insert("end", self.streaming_text[-2000:])
                        _fit_textbox_to_content(tb, self.streaming_text[-2000:])
                        raw.configure(state="disabled")
                        self._scroll_to_bottom()

                elif msg_type == "thinking":
                    self.streaming_thinking_text += msg_data

                elif msg_type == "done":
                    full_text = self.streaming_text
                    thoughts = self.streaming_thinking_text
                    if self.streaming_md_container and hasattr(self.streaming_md_container, '_md_textbox'):
                        if self.streaming_thinking_text and not getattr(self.streaming_md_container, '_thinking_created', False):
                            self._create_thinking_panel(self.streaming_md_container, self.streaming_thinking_text)
                            self.streaming_md_container._thinking_created = True
                        tb = self.streaming_md_container._md_textbox
                        configure_markdown_tags(tb)
                        render_markdown(tb, full_text)
                        _fit_textbox_to_content(tb, full_text)
                    self.streaming_md_container = None
                    self.streaming_thinking_text = ""

                    if self.current_conv:
                        self.current_conv["messages"].append({
                            "role": "assistant",
                            "content": full_text,
                            "thinking": thoughts if thoughts else None
                        })
                        if len(self.current_conv["messages"]) == 2:
                            title = full_text[:30].replace("\n", " ")
                            self.current_conv["title"] = title or "\u65b0\u5bf9\u8bdd"
                        self.current_conv["updated_at"] = datetime.now().isoformat()
                        self.conv_manager.save_conversation(self.current_conv)

                    self.token_label.configure(
                        text=f"Token: {msg_data['input_tokens']} in / {msg_data['output_tokens']} out")
                    self.is_streaming = False
                    self.send_btn.configure(state="normal", text="\u53d1\u9001")
                    self.status_label.configure(text="\u5c31\u7eea")
                    self._update_title()
                    self.conv_manager.refresh()
                    self._refresh_conv_list()
                    self._scroll_to_bottom()

                elif msg_type == "error":
                    if self.streaming_md_container and hasattr(self.streaming_md_container, '_md_textbox'):
                        if self.streaming_thinking_text and not getattr(self.streaming_md_container, '_thinking_created', False):
                            self._create_thinking_panel(self.streaming_md_container, self.streaming_thinking_text)
                            self.streaming_md_container._thinking_created = True
                        tb = self.streaming_md_container._md_textbox
                        if self.streaming_text == "":
                            configure_markdown_tags(tb)
                            render_markdown(tb, f"\u274c \u9519\u8bef: {msg_data}")
                        else:
                            full_text = self.streaming_text + f"\n\n\u274c \u9519\u8bef: {msg_data}"
                            configure_markdown_tags(tb)
                            render_markdown(tb, full_text)
                    self.streaming_md_container = None
                    self.streaming_thinking_text = ""

                    if self.current_conv and self.streaming_text:
                        self.current_conv["messages"].append({"role": "assistant", "content": self.streaming_text})
                    self.is_streaming = False
                    self.send_btn.configure(state="normal", text="\u53d1\u9001")
                    self.status_label.configure(text=f"\u9519\u8bef: {msg_data[:50]}")

        except queue.Empty:
            pass
        self.after(50, self._process_queue)

    def _on_model_change(self, choice):
        self.config.set("model", choice)
        if self.current_conv:
            self.current_conv["model"] = choice
            self.conv_manager.save_conversation(self.current_conv)
        self.status_label.configure(text=f"\u6a21\u578b\u5207\u6362: {choice}")

    def _open_settings(self):
        dialog = SettingsDialog(self, self.config)
        self.wait_window(dialog)
        if dialog.result:
            for k, v in dialog.result.items():
                self.config.set(k, v)
            if self.current_conv:
                self.current_conv["temperature"] = dialog.result["temperature"]
                self.current_conv["max_tokens"] = dialog.result["max_tokens"]
                if dialog.result["thinking_enabled"]:
                    self.current_conv["thinking"] = {
                        "type": dialog.result["thinking_type"],
                        "budget_tokens": dialog.result["thinking_budget"],
                    }
                else:
                    self.current_conv["thinking"] = None
                self.conv_manager.save_conversation(self.current_conv)
            self.status_label.configure(text="\u8bbe\u7f6e\u5df2\u66f4\u65b0")

    def _open_proxy_settings(self):
        dialog = ProxyDialog(self, self.config)
        self.wait_window(dialog)
        if dialog.result:
            for k, v in dialog.result.items():
                self.config.set(k, v)
            mode_labels = {"none": "\u65e0\u4ee3\u7406", "system": "\u7cfb\u7edf\u4ee3\u7406", "custom": "\u81ea\u5b9a\u4e49"}
            self.proxy_indicator.configure(
                text=f"| {mode_labels.get(dialog.result['proxy_mode'], dialog.result['proxy_mode'])}")
            self.status_label.configure(text="\u4ee3\u7406\u8bbe\u7f6e\u5df2\u66f4\u65b0")

    def _save_api_key(self):
        api_key = self.api_key_entry.get().strip()
        if api_key:
            self.config.set("api_key", api_key)
            self.status_label.configure(text="API Key \u5df2\u4fdd\u5b58")
            self._refresh_models_async()

    def _refresh_models_async(self):
        def _fetch():
            api_key = self.config.get("api_key")
            if not api_key:
                return
            try:
                http_client = build_http_client(
                    self.config.get("proxy_mode", "system"),
                    self.config.get("proxy_url", "")
                )
                client = Anthropic(api_key=api_key, http_client=http_client)
                models = client.models.list()
                model_ids = []
                has_opus_47 = False
                for m in models.data:
                    mid = m.id
                    if hasattr(m, 'deprecation_date') and m.deprecation_date:
                        continue
                    model_ids.append(mid)
                    if "opus-4-7" in mid.lower():
                        has_opus_47 = True
                if not has_opus_47:
                    model_ids.insert(0, "claude-opus-4-7")
                if model_ids:
                    self.after(0, lambda: self._update_model_list(model_ids))
            except Exception:
                pass

        thread = threading.Thread(target=_fetch, daemon=True)
        thread.start()

    def _update_model_list(self, model_ids):
        self.available_models = model_ids
        current = self.model_var.get()
        self.model_menu.configure(values=model_ids)
        if current in model_ids:
            self.model_var.set(current)
        else:
            self.model_var.set(model_ids[0])
        self.status_label.configure(text=f"\u5df2\u52a0\u8f7d {len(model_ids)} \u4e2a\u53ef\u7528\u6a21\u578b")


if __name__ == "__main__":
    app = ClaudeChatApp()
    app.mainloop()
