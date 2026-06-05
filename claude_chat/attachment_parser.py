import os
import base64
import logging
from pathlib import Path

logger = logging.getLogger("claude_chat.attachment_parser")

def get_mime_type(file_path):
    suffix = Path(file_path).suffix.lower()
    mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    return mapping.get(suffix, "application/octet-stream")

def parse_docx(file_path):
    """解析 Word (.docx) 文件并返回 Markdown"""
    try:
        import docx
    except ImportError:
        return "[错误: 未安装 python-docx 库，无法解析 Word 文档]"
    
    try:
        doc = docx.Document(file_path)
        markdown_lines = []
        
        # 遍历文档元素（段落和表格）
        for element in doc.element.body:
            if element.tag.endswith('p'):
                # 处理段落
                for p in doc.paragraphs:
                    if p._element is element:
                        text = p.text.strip()
                        if text:
                            # 简单处理标题样式
                            if p.style.name.startswith('Heading'):
                                try:
                                    level = int(p.style.name.replace('Heading', '').strip())
                                    markdown_lines.append("#" * level + " " + text)
                                except ValueError:
                                    markdown_lines.append("### " + text)
                            else:
                                markdown_lines.append(text)
                            markdown_lines.append("")
                        break
            elif element.tag.endswith('tbl'):
                # 处理表格
                for table in doc.tables:
                    if table._element is element:
                        table_lines = []
                        for i, row in enumerate(table.rows):
                            row_cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                            table_lines.append("| " + " | ".join(row_cells) + " |")
                            if i == 0:
                                table_lines.append("| " + " | ".join(["---"] * len(row_cells)) + " |")
                        markdown_lines.extend(table_lines)
                        markdown_lines.append("")
                        break
                        
        # 兜底：如果遍历 element body 没提取到内容，直接读取 paragraphs
        if not markdown_lines:
            for p in doc.paragraphs:
                text = p.text.strip()
                if text:
                    markdown_lines.append(text)
                    markdown_lines.append("")
                    
        return "\n".join(markdown_lines)
    except Exception as e:
        logger.exception(f"解析 docx 失败: {e}")
        return f"[解析 Word 文档出错: {e}]"

def parse_xlsx(file_path):
    """解析 Excel (.xlsx) 文件并返回 Markdown 表格"""
    try:
        import openpyxl
    except ImportError:
        return "[错误: 未安装 openpyxl 库，无法解析 Excel 表格]"
        
    try:
        wb = openpyxl.load_workbook(file_path, data_only=True)
        markdown_lines = []
        
        for name in wb.sheetnames:
            sheet = wb[name]
            markdown_lines.append(f"### 工作表: {name}")
            markdown_lines.append("")
            
            rows = list(sheet.iter_rows(values_only=True))
            if not rows:
                markdown_lines.append("*(空表)*")
                markdown_lines.append("")
                continue
                
            # 找到有数据的最大边界以缩小表格
            max_col = 0
            has_data_rows = []
            for r_idx, row in enumerate(rows):
                # 检查行是否全部为空
                if any(val is not None for val in row):
                    has_data_rows.append(r_idx)
                    for c_idx, val in enumerate(row):
                        if val is not None and c_idx > max_col:
                            max_col = c_idx
                            
            if not has_data_rows:
                markdown_lines.append("*(空表)*")
                markdown_lines.append("")
                continue
                
            start_row = min(has_data_rows)
            end_row = max(has_data_rows)
            
            for r_idx in range(start_row, end_row + 1):
                row = rows[r_idx]
                row_cells = []
                for val in row[:max_col + 1]:
                    if val is None:
                        row_cells.append("")
                    else:
                        row_cells.append(str(val).strip().replace("\n", " "))
                markdown_lines.append("| " + " | ".join(row_cells) + " |")
                if r_idx == start_row:
                    markdown_lines.append("| " + " | ".join(["---"] * len(row_cells)) + " |")
            markdown_lines.append("")
            
        return "\n".join(markdown_lines)
    except Exception as e:
        logger.exception(f"解析 xlsx 失败: {e}")
        return f"[解析 Excel 失败: {e}]"

def parse_pptx(file_path):
    """解析 PowerPoint (.pptx) 文件并返回 Markdown"""
    try:
        import pptx
    except ImportError:
        return "[错误: 未安装 python-pptx 库，无法解析 PPT 文档]"
        
    try:
        prs = pptx.Presentation(file_path)
        markdown_lines = []
        
        for idx, slide in enumerate(prs.slides):
            markdown_lines.append(f"## Slide {idx + 1}")
            markdown_lines.append("")
            
            # 提取幻灯片内所有文本框中的文本
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    text = shape.text.strip()
                    # 根据字号或粗细简单分类标题或普通文本
                    if shape.name.startswith("Title"):
                        markdown_lines.append(f"### {text}")
                    else:
                        markdown_lines.append(text)
                    markdown_lines.append("")
            
            # 提取备注
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    markdown_lines.append(f"*备注:* {notes}")
                    markdown_lines.append("")
                    
        return "\n".join(markdown_lines)
    except Exception as e:
        logger.exception(f"解析 pptx 失败: {e}")
        return f"[解析 PPT 失败: {e}]"

def parse_pdf(file_path):
    """解析 PDF 文件并提取文本内容"""
    try:
        import pypdf
    except ImportError:
        return "[错误: 未安装 pypdf 库，无法解析 PDF 文档]"
        
    try:
        reader = pypdf.PdfReader(file_path)
        markdown_lines = []
        
        for idx, page in enumerate(reader.pages):
            text = page.extract_text()
            markdown_lines.append(f"## Page {idx + 1}")
            markdown_lines.append("")
            if text and text.strip():
                markdown_lines.append(text.strip())
            else:
                markdown_lines.append("*(本页未提取到文本，可能为扫描件或空白页)*")
            markdown_lines.append("")
            
        return "\n".join(markdown_lines)
    except Exception as e:
        logger.exception(f"解析 pdf 失败: {e}")
        return f"[解析 PDF 失败: {e}]"

def ocr_image_local_or_cloud(file_path, ocr_mode="auto", cloud_provider="gemini", api_key="", proxy_mode="system", proxy_url=""):
    """处理图像的 OCR 提取"""
    ext = Path(file_path).suffix.lower()
    mime_type = get_mime_type(file_path)
    
    if ocr_mode == "none":
        return "[已禁用 OCR 图片文字识别]"
        
    # 尝试本地 OCR (easyocr)
    if ocr_mode in ("auto", "local"):
        try:
            import easyocr
            # easyocr 初始化 (可支持中英文识别)
            reader = easyocr.Reader(['ch_sim', 'en'])
            results = reader.readtext(file_path, detail=0)
            if results:
                return "\n".join(results)
            elif ocr_mode == "local":
                return "[本地 OCR 未识别到任何文字]"
        except Exception as e:
            logger.info(f"本地 easyocr 识别不可用或失败，将尝试云端识别: {e}")
            if ocr_mode == "local":
                return f"[本地 OCR 不可用或执行出错: {e}]"
                
    # 尝试云端 OCR
    if not api_key:
        return "[未配置 API Key，无法使用云端 OCR 备用服务识别图片内容]"
        
    try:
        # 读取图片数据
        with open(file_path, "rb") as f:
            image_bytes = f.read()
            
        prompt = "Please transcribe all text visible in this image. Output only the transcribed text, formatting it as markdown if there are headings, tables, or lists. Do not add any introductory or concluding comments."
        
        if cloud_provider == "gemini":
            try:
                from google import genai
                from google.genai import types
                from claude_chat.client import build_http_client
                
                # 构建带有 proxy 的 httpx 客户端
                http_client = build_http_client(proxy_mode, proxy_url)
                client = genai.Client(api_key=api_key, http_options={"httpx_client": http_client})
                
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        prompt
                    ]
                )
                if response and response.text:
                    return response.text.strip()
                else:
                    return "[云端 Gemini OCR 识别响应空或解析失败]"
            except Exception as gemini_err:
                logger.error(f"Gemini OCR 失败: {gemini_err}")
                return f"[云端 Gemini OCR 识别失败: {gemini_err}]"
                
        elif cloud_provider == "claude":
            try:
                from anthropic import Anthropic
                from claude_chat.client import build_http_client
                
                # 构建带有 proxy 的 httpx 客户端
                http_client = build_http_client(proxy_mode, proxy_url)
                client = Anthropic(api_key=api_key, http_client=http_client)
                
                b64_data = base64.b64encode(image_bytes).decode("utf-8")
                response = client.messages.create(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=4096,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": mime_type,
                                        "data": b64_data
                                    }
                                },
                                {
                                    "type": "text",
                                    "text": prompt
                                }
                            ]
                        }
                    ]
                )
                ocr_text = ""
                for block in response.content:
                    if block.type == "text":
                        ocr_text += block.text
                return ocr_text.strip()
            except Exception as claude_err:
                logger.error(f"Claude OCR 失败: {claude_err}")
                return f"[云端 Claude OCR 识别失败: {claude_err}]"
        else:
            return f"[未知的云端 OCR 服务商: {cloud_provider}]"
            
    except Exception as outer_err:
        logger.error(f"云端 OCR 外层失败: {outer_err}")
        return f"[云端 OCR 执行出错: {outer_err}]"


def parse_attachment_to_markdown(att, ocr_mode="auto", cloud_provider="gemini", api_key="", proxy_mode="system", proxy_url=""):
    """解析各种附件为 Markdown 文本"""
    file_path = att["path"]
    ext = Path(file_path).suffix.lower()
    
    from claude_chat.config import IMAGE_EXTENSIONS, PDF_EXTENSIONS
    
    if ext in IMAGE_EXTENSIONS:
        return ocr_image_local_or_cloud(
            file_path,
            ocr_mode=ocr_mode,
            cloud_provider=cloud_provider,
            api_key=api_key,
            proxy_mode=proxy_mode,
            proxy_url=proxy_url
        )
    elif ext in PDF_EXTENSIONS:
        return parse_pdf(file_path)
    elif ext == ".docx":
        return parse_docx(file_path)
    elif ext == ".xlsx":
        return parse_xlsx(file_path)
    elif ext == ".pptx":
        return parse_pptx(file_path)
    else:
        # 默认使用纯文本解析兜底
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            return f"[读取文件文本内容失败: {e}]"

# 兼容性别名，供旧版测试用例导入使用
parse_docx_to_markdown = parse_docx
parse_xlsx_to_markdown = parse_xlsx
parse_pptx_to_markdown = parse_pptx
parse_pdf_to_markdown = parse_pdf
