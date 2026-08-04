const attachmentPreviewModal = document.getElementById("attachment-preview-modal");
const attachmentPreviewDialog = document.getElementById("attachment-preview-dialog");
const attachmentPreviewTitle = document.getElementById("attachment-preview-title");
const attachmentPreviewMeta = document.getElementById("attachment-preview-meta");
const attachmentPreviewStatus = document.getElementById("attachment-preview-status");
const attachmentPreviewBody = document.getElementById("attachment-preview-body");
const attachmentPreviewCloseBtn = document.getElementById("attachment-preview-close-btn");

let attachmentPreviewVersion = 0;
let attachmentPreviewLastFocus = null;
let attachmentPreviewAbortController = null;
const attachmentPreviewBackground = typeof document.querySelector === "function"
    ? document.querySelector(".app-container")
    : null;
let attachmentPreviewBackgroundAriaHidden = null;
let attachmentPreviewBackgroundInactive = false;

const ATTACHMENT_PREVIEW_LANGUAGE_ALIASES = Object.freeze({
    py: "python",
    js: "javascript",
    ts: "typescript",
    html: "xml",
    xml: "xml",
    yml: "yaml",
    sh: "bash",
    bat: "dos",
    ps1: "powershell",
    h: "c",
    hpp: "cpp",
    rs: "rust",
    rb: "ruby",
    kt: "kotlin"
});

function attachmentPreviewLanguageForName(name) {
    const extension = attachmentExtension(name);
    const language = ATTACHMENT_PREVIEW_LANGUAGE_ALIASES[extension] || extension;
    if (!language) return "";
    if (typeof hljs !== "undefined" && typeof hljs.getLanguage === "function"
        && !hljs.getLanguage(language)) {
        return "";
    }
    return language;
}

function setAttachmentPreviewBackgroundInactive(inactive) {
    if (inactive === attachmentPreviewBackgroundInactive) return;
    attachmentPreviewBackgroundInactive = inactive;
    if (inactive) {
        document.documentElement.classList.add("attachment-preview-open");
        if (!attachmentPreviewBackground) return;
        attachmentPreviewBackgroundAriaHidden = attachmentPreviewBackground.getAttribute("aria-hidden");
        attachmentPreviewBackground.inert = true;
        attachmentPreviewBackground.setAttribute("aria-hidden", "true");
        return;
    }
    document.documentElement.classList.remove("attachment-preview-open");
    if (!attachmentPreviewBackground) return;
    attachmentPreviewBackground.inert = false;
    if (attachmentPreviewBackgroundAriaHidden == null) {
        attachmentPreviewBackground.removeAttribute("aria-hidden");
    } else {
        attachmentPreviewBackground.setAttribute("aria-hidden", attachmentPreviewBackgroundAriaHidden);
    }
    attachmentPreviewBackgroundAriaHidden = null;
}

function attachmentPreviewRequest(attachment, context = {}) {
    const previewId = attachment?.previewId || null;
    if (context.scope === "message") {
        const messageIndex = Number(context.messageIndex);
        const blockIndex = Number(attachment?.blockIndex ?? context.blockIndex);
        if (!context.convId || !Number.isInteger(messageIndex) || messageIndex < 0
            || !Number.isInteger(blockIndex) || blockIndex < 0) {
            return null;
        }
        return {
            scope: "message",
            conv_id: context.convId,
            message_index: messageIndex,
            block_index: blockIndex,
            ...(previewId ? { preview_id: previewId } : {})
        };
    }
    if (!previewId) return null;
    return { scope: "pending", preview_id: previewId };
}

function resetAttachmentPreviewBody() {
    if (attachmentPreviewBody) attachmentPreviewBody.replaceChildren();
    if (attachmentPreviewStatus) {
        attachmentPreviewStatus.textContent = "";
        attachmentPreviewStatus.className = "attachment-preview-status";
    }
}

function closeAttachmentPreview() {
    attachmentPreviewVersion += 1;
    attachmentPreviewAbortController?.abort();
    attachmentPreviewAbortController = null;
    attachmentPreviewModal?.classList.add("hidden");
    attachmentPreviewModal?.removeAttribute("aria-busy");
    resetAttachmentPreviewBody();
    setAttachmentPreviewBackgroundInactive(false);
    const focusTarget = attachmentPreviewLastFocus;
    attachmentPreviewLastFocus = null;
    if (focusTarget && document.contains(focusTarget) && typeof focusTarget.focus === "function") {
        focusTarget.focus();
    }
}

function showAttachmentPreviewError(message) {
    resetAttachmentPreviewBody();
    if (attachmentPreviewStatus) {
        attachmentPreviewStatus.className = "attachment-preview-status is-error";
        attachmentPreviewStatus.textContent = message || "附件暂时无法预览";
    }
}

function renderAttachmentPreviewResult(result, attachment) {
    resetAttachmentPreviewBody();
    const previewType = String(result?.preview_type || result?.type || "").toLowerCase();
    const resultName = attachmentBasename(result?.name || attachment?.name) || "附件预览";
    const resultMediaType = String(result?.media_type || attachment?.mediaType || "");
    const resultSize = result?.size ?? attachment?.size;
    const kind = inferAttachmentKind(resultName, result?.kind || attachment?.kind, resultMediaType);

    attachmentPreviewTitle.textContent = resultName;
    attachmentPreviewMeta.textContent = [attachmentTypeLabel({ name: resultName, kind }), formatAttachmentSize(resultSize)]
        .filter(Boolean)
        .join(" · ");

    if (previewType === "image") {
        const dataUrl = String(result?.data_url || "");
        if (!/^data:image\/(?:png|jpeg|webp|gif);base64,[a-z0-9+/=\s]+$/i.test(dataUrl)) {
            throw new Error("图片预览数据无效");
        }
        const image = document.createElement("img");
        image.className = "attachment-preview-image";
        image.alt = `${resultName} 的预览`;
        image.onerror = () => {
            if (image.isConnected) showAttachmentPreviewError("图片预览加载失败");
        };
        image.src = dataUrl;
        attachmentPreviewBody.appendChild(image);
    } else if (previewType === "text") {
        const pre = document.createElement("pre");
        pre.className = `attachment-preview-text${kind === "code" ? " is-code" : ""}`;
        const code = document.createElement("code");
        if (kind === "code") {
            const language = attachmentPreviewLanguageForName(resultName);
            if (language) code.classList.add(`language-${language}`);
        }
        code.textContent = String(result?.text || "");
        pre.appendChild(code);
        attachmentPreviewBody.appendChild(pre);
        if (kind === "code") {
            if (typeof highlightCodeBlocks === "function") {
                highlightCodeBlocks(attachmentPreviewBody, { includeActions: false });
            } else if (typeof hljs !== "undefined") {
                hljs.highlightElement(code);
            }
        }
    } else {
        throw new Error("此附件没有可显示的预览内容");
    }

    if (result?.truncated) {
        attachmentPreviewStatus.className = "attachment-preview-status is-warning";
        attachmentPreviewStatus.textContent = "内容较长，当前仅显示预览片段。";
    } else {
        attachmentPreviewStatus.textContent = "";
    }
}

async function openAttachmentPreview(attachment, context = {}, triggerElement = null) {
    if (!attachmentPreviewModal || !attachmentPreviewBody) return;
    const normalized = normalizeAttachmentDescriptor(attachment);
    const request = attachmentPreviewRequest(normalized, context);
    if (!normalized.previewAvailable || !request) {
        statusLabel.textContent = `暂时无法预览附件：${normalized.name}`;
        return;
    }

    attachmentPreviewVersion += 1;
    const requestVersion = attachmentPreviewVersion;
    attachmentPreviewLastFocus = triggerElement || document.activeElement;
    attachmentPreviewTitle.textContent = normalized.name;
    attachmentPreviewMeta.textContent = [attachmentTypeLabel(normalized), formatAttachmentSize(normalized.size)]
        .filter(Boolean)
        .join(" · ");
    resetAttachmentPreviewBody();
    attachmentPreviewStatus.className = "attachment-preview-status is-loading";
    attachmentPreviewStatus.textContent = "正在加载预览…";
    attachmentPreviewModal.classList.remove("hidden");
    attachmentPreviewModal.setAttribute("aria-busy", "true");
    attachmentPreviewCloseBtn?.focus();
    setAttachmentPreviewBackgroundInactive(true);

    attachmentPreviewAbortController?.abort();
    attachmentPreviewAbortController = typeof AbortController === "function" ? new AbortController() : null;

    try {
        const result = await apiBridge.get_attachment_preview(request, {
            signal: attachmentPreviewAbortController?.signal
        });
        if (requestVersion !== attachmentPreviewVersion) return;
        if (!result || result.error) {
            throw new Error(result?.error || "附件预览加载失败");
        }
        renderAttachmentPreviewResult(result, normalized);
        attachmentPreviewModal.removeAttribute("aria-busy");
    } catch (error) {
        if (requestVersion !== attachmentPreviewVersion) return;
        if (error?.name === "AbortError") return;
        attachmentPreviewModal.removeAttribute("aria-busy");
        showAttachmentPreviewError(error?.message || String(error));
    } finally {
        if (requestVersion === attachmentPreviewVersion) {
            attachmentPreviewAbortController = null;
        }
    }
}

attachmentPreviewCloseBtn?.addEventListener("click", closeAttachmentPreview);
attachmentPreviewModal?.addEventListener("click", event => {
    if (event.target === attachmentPreviewModal) closeAttachmentPreview();
});
attachmentPreviewDialog?.addEventListener("click", event => event.stopPropagation());
document.addEventListener("keydown", event => {
    const previewIsOpen = attachmentPreviewModal && !attachmentPreviewModal.classList.contains("hidden");
    if (!previewIsOpen) return;
    if (event.key === "Escape") {
        event.preventDefault();
        closeAttachmentPreview();
    } else if (event.key === "Tab") {
        const focusable = Array.from(attachmentPreviewDialog?.querySelectorAll(
            'button:not([disabled]), [href], input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        ) || []).filter(element => element.offsetParent !== null);
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }
});
