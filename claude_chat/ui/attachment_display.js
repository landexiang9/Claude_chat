const LEGACY_ATTACHMENT_BLOCK_PATTERN = /^\s*---\s*附件文件:\s*(.+?)\s*---\s*(?:\r?\n)[\s\S]*?(?:\r?\n)---\s*附件结束\s*---\s*$/;

const ATTACHMENT_KIND_LABELS = {
    image: "图片",
    document: "文档",
    code: "代码",
    text: "文本",
    file: "文件"
};

const IMAGE_ATTACHMENT_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp"]);
const DOCUMENT_ATTACHMENT_EXTENSIONS = new Set(["pdf", "docx", "xlsx", "pptx"]);
const CODE_ATTACHMENT_EXTENSIONS = new Set([
    "py", "js", "ts", "html", "css", "json", "xml", "yaml", "yml",
    "sql", "sh", "bat", "ps1", "java", "c", "h", "cpp", "hpp", "go", "rs", "php", "rb",
    "r", "swift", "kt", "scala", "lua"
]);
const TEXT_ATTACHMENT_EXTENSIONS = new Set(["txt", "md", "csv", "ini", "cfg", "toml"]);

function attachmentBasename(value) {
    const normalized = String(value || "").replace(/\\/g, "/");
    const parts = normalized.split("/").filter(Boolean);
    return parts.length ? parts[parts.length - 1] : "";
}

function attachmentExtension(name) {
    const basename = attachmentBasename(name);
    const dotIndex = basename.lastIndexOf(".");
    if (dotIndex <= 0 || dotIndex === basename.length - 1) return "";
    return basename.slice(dotIndex + 1).toLowerCase();
}

function inferAttachmentKind(name, declaredKind = "", mediaType = "") {
    const normalizedKind = String(declaredKind || "").toLowerCase();
    if (["image", "document", "code"].includes(normalizedKind)) {
        return normalizedKind;
    }

    const normalizedMime = String(mediaType || "").toLowerCase();
    if (normalizedMime.startsWith("image/")) return "image";
    if (normalizedMime === "application/pdf" || normalizedMime.includes("officedocument")) return "document";

    const extension = attachmentExtension(name);
    if (IMAGE_ATTACHMENT_EXTENSIONS.has(extension)) return "image";
    if (DOCUMENT_ATTACHMENT_EXTENSIONS.has(extension)) return "document";
    if (CODE_ATTACHMENT_EXTENSIONS.has(extension)) return "code";
    if (normalizedKind === "text" || normalizedMime.startsWith("text/")) return "text";
    if (TEXT_ATTACHMENT_EXTENSIONS.has(extension)) return "text";
    return "file";
}

function normalizeAttachmentDescriptor(raw = {}, fallback = {}) {
    const source = raw && typeof raw === "object" ? raw : {};
    const fallbackSource = fallback && typeof fallback === "object" ? fallback : {};
    const name = attachmentBasename(
        source.name || source.file_name || fallbackSource.name || fallbackSource.file_name || fallbackSource.file_path
    ) || "未命名附件";
    const numericSize = Number(source.size ?? fallbackSource.size);
    const size = Number.isFinite(numericSize) && numericSize >= 0 ? numericSize : null;
    const mediaType = String(
        source.media_type || source.mediaType || source.mime_type
        || fallbackSource.media_type || fallbackSource.mediaType || ""
    );
    const kind = inferAttachmentKind(name, source.kind || fallbackSource.kind, mediaType);
    const previewIdValue = source.preview_id ?? source.previewId ?? fallbackSource.preview_id ?? fallbackSource.previewId;
    const previewId = typeof previewIdValue === "string" && previewIdValue.trim()
        ? previewIdValue.trim()
        : null;
    const explicitPreviewAvailable = source.preview_available ?? source.previewAvailable
        ?? fallbackSource.preview_available ?? fallbackSource.previewAvailable;
    const previewAvailable = explicitPreviewAvailable == null
        ? Boolean(previewId)
        : Boolean(explicitPreviewAvailable);
    const blockIndexValue = source.block_index ?? source.blockIndex ?? fallbackSource.block_index ?? fallbackSource.blockIndex;
    const numericBlockIndex = Number(blockIndexValue);
    const blockIndex = Number.isInteger(numericBlockIndex) && numericBlockIndex >= 0 ? numericBlockIndex : null;
    const previewScope = source.preview_scope === "pending" || source.previewScope === "pending"
        ? "pending"
        : null;
    return { name, size, mediaType, kind, previewId, previewAvailable, blockIndex, previewScope };
}

function parseLegacyAttachmentBlock(text) {
    const match = String(text || "").match(LEGACY_ATTACHMENT_BLOCK_PATTERN);
    if (!match) return null;
    return normalizeAttachmentDescriptor({ name: match[1] });
}

function attachmentDescriptorFromContentBlock(block, blockIndex = null) {
    if (!block || typeof block !== "object") return null;
    if (block._attachment && typeof block._attachment === "object") {
        return normalizeAttachmentDescriptor(
            { ...block._attachment, block_index: blockIndex },
            block.source
        );
    }
    if (block.type === "attachment") {
        return normalizeAttachmentDescriptor(
            { ...block, block_index: blockIndex, preview_scope: block.preview_scope || "pending" },
            block.source
        );
    }
    if (["image", "document", "file", "audio", "video"].includes(block.type)) {
        return normalizeAttachmentDescriptor(
            {
                name: block.name,
                size: block.size,
                media_type: block.media_type,
                kind: block.type,
                block_index: blockIndex,
                preview_id: block.preview_id,
                preview_available: block.preview_available
            },
            block.source
        );
    }
    if (block.type === "text") {
        const descriptor = parseLegacyAttachmentBlock(block.text);
        return descriptor ? { ...descriptor, blockIndex } : null;
    }
    return null;
}

function normalizeMessageDisplayContent(content, { extractAttachments = false } = {}) {
    if (typeof content === "string") return { text: content, attachments: [] };
    if (!Array.isArray(content)) {
        return { text: content == null ? "" : String(content), attachments: [] };
    }

    const textParts = [];
    const messageAttachments = [];
    content.forEach((block, blockIndex) => {
        if (extractAttachments) {
            const descriptor = attachmentDescriptorFromContentBlock(block, blockIndex);
            if (descriptor) {
                messageAttachments.push(descriptor);
                return;
            }
        }
        if (typeof block === "string") {
            textParts.push(block);
        } else if (block && block.type === "text") {
            textParts.push(String(block.text || ""));
        }
    });
    return { text: textParts.join(""), attachments: messageAttachments };
}

function buildPendingAttachmentContent(text, selectedAttachments) {
    const blocks = [{ type: "text", text: String(text || "") }];
    (Array.isArray(selectedAttachments) ? selectedAttachments : []).forEach(attachment => {
        const descriptor = normalizeAttachmentDescriptor(attachment, { file_path: attachment?.path });
        blocks.push({
            type: "attachment",
            name: descriptor.name,
            size: descriptor.size,
            media_type: descriptor.mediaType,
            kind: descriptor.kind,
            preview_id: descriptor.previewId,
            preview_available: descriptor.previewAvailable,
            preview_scope: "pending"
        });
    });
    return blocks;
}

function formatAttachmentSize(size) {
    const numericSize = Number(size);
    if (!Number.isFinite(numericSize) || numericSize < 0) return "";
    if (numericSize < 1024) return `${numericSize} B`;
    if (numericSize < 1024 * 1024) return `${Math.max(1, Math.round(numericSize / 1024))} KB`;
    return `${(numericSize / (1024 * 1024)).toFixed(numericSize < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

function attachmentTypeLabel(attachment) {
    const extension = attachmentExtension(attachment?.name);
    if (extension) return `${extension.toUpperCase()} ${ATTACHMENT_KIND_LABELS[attachment?.kind] || "文件"}`;
    return ATTACHMENT_KIND_LABELS[attachment?.kind] || "附件";
}

function attachmentIconLabel(attachment) {
    const extension = attachmentExtension(attachment?.name).toUpperCase();
    if (extension) return extension.slice(0, 4);
    if (attachment?.kind === "image") return "IMG";
    if (attachment?.kind === "document") return "DOC";
    if (attachment?.kind === "code") return "CODE";
    if (attachment?.kind === "text") return "TXT";
    return "FILE";
}

function messageContentForCopy(content, role) {
    const display = normalizeMessageDisplayContent(content, { extractAttachments: role === "user" });
    const parts = [];
    if (display.text) parts.push(display.text);
    if (display.attachments.length) {
        parts.push(`附件：${display.attachments.map(item => item.name).join("、")}`);
    }
    return parts.join("\n\n");
}

function createAttachmentCard(attachment, { mode = "message", onPreview = null, onRemove = null } = {}) {
    const descriptor = normalizeAttachmentDescriptor(attachment);
    const wrapper = document.createElement("div");
    wrapper.className = `attachment-card-shell ${mode === "composer" ? "composer-attachment" : "message-attachment-shell"}`;

    const canPreview = descriptor.previewAvailable && typeof onPreview === "function";
    const item = document.createElement(canPreview ? "button" : "div");
    item.className = `message-attachment${canPreview ? " is-previewable" : ""}`;
    if (canPreview) {
        item.type = "button";
        item.title = `预览 ${descriptor.name}`;
        item.setAttribute("aria-label", `预览附件 ${descriptor.name}`);
        item.onclick = () => onPreview(descriptor, item);
    } else {
        item.setAttribute("aria-label", `附件 ${descriptor.name}${descriptor.previewAvailable ? "" : "，预览不可用"}`);
    }

    const icon = document.createElement("span");
    icon.className = `message-attachment-icon kind-${descriptor.kind}`;
    icon.textContent = attachmentIconLabel(descriptor);
    icon.setAttribute("aria-hidden", "true");

    const details = document.createElement("span");
    details.className = "message-attachment-details";

    const name = document.createElement("span");
    name.className = "message-attachment-name";
    name.textContent = descriptor.name;
    name.title = descriptor.name;

    const metadata = document.createElement("span");
    metadata.className = "message-attachment-meta";
    const sizeLabel = formatAttachmentSize(descriptor.size);
    const previewLabel = canPreview ? "点击预览" : (descriptor.previewAvailable ? "可预览" : "预览不可用");
    metadata.textContent = [attachmentTypeLabel(descriptor), sizeLabel, previewLabel].filter(Boolean).join(" · ");

    details.append(name, metadata);
    item.append(icon, details);

    if (canPreview) {
        const arrow = document.createElement("span");
        arrow.className = "message-attachment-arrow";
        arrow.textContent = "›";
        arrow.setAttribute("aria-hidden", "true");
        item.appendChild(arrow);
    }
    wrapper.appendChild(item);

    if (typeof onRemove === "function") {
        const removeButton = document.createElement("button");
        removeButton.className = "remove-att-btn";
        removeButton.type = "button";
        removeButton.setAttribute("aria-label", `移除附件 ${descriptor.name}`);
        removeButton.title = `移除 ${descriptor.name}`;
        removeButton.textContent = "×";
        removeButton.onclick = event => {
            event.stopPropagation();
            onRemove(descriptor);
        };
        wrapper.appendChild(removeButton);
    }

    return wrapper;
}

function renderMessageAttachmentCards(messageAttachments, { onPreview = null } = {}) {
    const container = document.createElement("div");
    container.className = "message-attachments";
    container.setAttribute("role", "list");
    container.setAttribute("aria-label", "消息附件");

    messageAttachments.forEach(attachment => {
        const card = createAttachmentCard(attachment, { mode: "message", onPreview });
        card.setAttribute("role", "listitem");
        container.appendChild(card);
    });
    return container;
}
