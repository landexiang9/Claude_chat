const fs = require("fs");
const path = require("path");
const vm = require("vm");

const context = {
    console,
    document: {
        activeElement: null,
        addEventListener() {},
        contains() { return false; },
        getElementById() { return null; }
    }
};
vm.createContext(context);
const displaySource = fs.readFileSync(
    path.join(__dirname, "..", "claude_chat", "ui", "attachment_display.js"),
    "utf8"
);
const previewSource = fs.readFileSync(
    path.join(__dirname, "..", "claude_chat", "ui", "attachment_preview.js"),
    "utf8"
);
const uiSource = fs.readFileSync(
    path.join(__dirname, "..", "claude_chat", "ui", "ui.js"),
    "utf8"
);

vm.runInContext(`${displaySource}\n${previewSource}
function assert(condition, message) {
    if (!condition) throw new Error(message);
}

const secretPayload = "SECRET_ATTACHMENT_PAYLOAD";
const persisted = [
    { type: "text", text: "请总结这个文件" },
    {
        type: "text",
        text: "\\n\\n--- 附件文件: 报告.md ---\\n" + secretPayload + "\\n--- 附件结束 ---",
        _attachment: {
            name: "报告.md",
            size: 2048,
            media_type: "text/markdown",
            kind: "text",
            preview_id: "0123456789abcdef0123456789abcdef",
            preview_available: true
        }
    }
];
const persistedDisplay = normalizeMessageDisplayContent(persisted, { extractAttachments: true });
assert(persistedDisplay.text === "请总结这个文件", "attachment payload must not enter visible text");
assert(!persistedDisplay.text.includes(secretPayload), "secret payload leaked into display text");
assert(persistedDisplay.attachments.length === 1, "persisted attachment card missing");
assert(persistedDisplay.attachments[0].name === "报告.md", "unicode attachment name changed");
assert(persistedDisplay.attachments[0].size === 2048, "persisted attachment size missing");
assert(persistedDisplay.attachments[0].previewId === "0123456789abcdef0123456789abcdef", "preview id missing");
assert(persistedDisplay.attachments[0].previewAvailable === true, "preview availability missing");
assert(persistedDisplay.attachments[0].blockIndex === 1, "persisted block index missing");

const pending = buildPendingAttachmentContent("请总结这个文件", [
    {
        name: "报告.md",
        path: "C:\\\\private\\\\报告.md",
        size: 2048,
        preview_id: "0123456789abcdef0123456789abcdef",
        preview_available: true
    }
]);
const pendingDisplay = normalizeMessageDisplayContent(pending, { extractAttachments: true });
assert(pendingDisplay.text === persistedDisplay.text, "pending and persisted text must match");
assert(pendingDisplay.attachments[0].name === persistedDisplay.attachments[0].name, "pending filename changed after reload");
assert(pendingDisplay.attachments[0].size === persistedDisplay.attachments[0].size, "pending size changed after reload");
assert(pendingDisplay.attachments[0].previewId === persistedDisplay.attachments[0].previewId, "pending preview id was lost");
assert(pendingDisplay.attachments[0].previewScope === "pending", "pending preview scope missing");
assert(!JSON.stringify(pending).includes("private"), "pending display content leaked a local path");

const pendingRequest = attachmentPreviewRequest(pendingDisplay.attachments[0], { scope: "pending" });
assert(pendingRequest.scope === "pending", "pending preview request scope changed");
assert(pendingRequest.preview_id === "0123456789abcdef0123456789abcdef", "pending preview request lost its id");
const messageRequest = attachmentPreviewRequest(persistedDisplay.attachments[0], {
    scope: "message",
    convId: "conversation-1",
    messageIndex: 4
});
assert(messageRequest.conv_id === "conversation-1", "message preview request lost conversation id");
assert(messageRequest.message_index === 4 && messageRequest.block_index === 1, "message preview coordinates changed");
assert(!Object.prototype.hasOwnProperty.call(messageRequest, "path"), "preview request must never contain a path");

const codeAttachment = normalizeAttachmentDescriptor({
    name: "analysis.py",
    media_type: "text/x-python",
    kind: "text"
});
assert(codeAttachment.kind === "code", "code extension should keep a stable visual kind after reload");
assert(normalizeAttachmentDescriptor({ name: "build.bat" }).kind === "code", "bat files should render as code");
assert(normalizeAttachmentDescriptor({ name: "app.kt" }).kind === "code", "Kotlin files should render as code");
assert(normalizeAttachmentDescriptor({ name: "settings.cfg" }).kind === "text", "cfg files should render as text");
assert(attachmentPreviewLanguageForName("analysis.py") === "python", "Python preview language mapping changed");
assert(attachmentPreviewLanguageForName("component.ts") === "typescript", "TypeScript preview language mapping changed");
assert(attachmentPreviewLanguageForName("page.html") === "xml", "HTML preview should reuse XML highlighting");

const legacy = normalizeMessageDisplayContent([
    { type: "text", text: "正文" },
    { type: "text", text: "\\n--- 附件文件: old.txt ---\\nLEGACY_SECRET\\n--- 附件结束 ---" }
], { extractAttachments: true });
assert(legacy.text === "正文", "legacy attachment payload must be hidden");
assert(legacy.attachments[0].name === "old.txt", "legacy attachment name missing");

const oldImage = normalizeMessageDisplayContent([
    { type: "image", source: { file_path: "D:\\\\private\\\\photo.png", media_type: "image/png" } }
], { extractAttachments: true });
assert(oldImage.text === "", "image-only message should not contain fake text");
assert(oldImage.attachments.length === 1, "image-only message must keep a visible attachment card");
assert(oldImage.attachments[0].name === "photo.png", "local path must be reduced to basename");
assert(!oldImage.attachments[0].name.includes("private"), "local path leaked into attachment name");

const assistantLiteral = normalizeMessageDisplayContent([
    { type: "text", text: "--- 附件文件: quoted.txt ---\\nnot an actual attachment\\n--- 附件结束 ---" }
], { extractAttachments: false });
assert(assistantLiteral.attachments.length === 0, "assistant text must not be mistaken for an attachment");
assert(assistantLiteral.text.includes("not an actual attachment"), "assistant literal text was hidden");

const copyText = messageContentForCopy(persisted, "user");
assert(copyText.includes("请总结这个文件"), "copy text lost authored content");
assert(copyText.includes("附件：报告.md"), "copy text lost attachment name");
assert(!copyText.includes(secretPayload), "copy text leaked attachment payload");
assert(!copyText.includes("file_path"), "copy text leaked local path");

const malicious = normalizeAttachmentDescriptor({ name: "<img src=x onerror=alert(1)>.txt", size: 1 });
assert(malicious.name === "<img src=x onerror=alert(1)>.txt", "descriptor should preserve the literal filename for textContent rendering");
assert(formatAttachmentSize(2048) === "2 KB", "attachment size formatting changed");
`, context);

context.hljs = {
    getLanguage(language) {
        return ["python", "typescript", "xml"].includes(language) ? {} : null;
    }
};
if (vm.runInContext('attachmentPreviewLanguageForName("analysis.py")', context) !== "python") {
    throw new Error("supported preview language should keep its explicit class");
}
if (vm.runInContext('attachmentPreviewLanguageForName("script.ps1")', context) !== "") {
    throw new Error("unsupported preview language should safely fall back to auto detection");
}

const highlightStart = uiSource.indexOf("function highlightCodeBlocks");
const highlightEnd = uiSource.indexOf("// ==========================================", highlightStart);
if (highlightStart < 0 || highlightEnd < 0) {
    throw new Error("highlightCodeBlocks source could not be located");
}
let highlighted = false;
let headerQueries = 0;
const codeBlock = { parentNode: null };
const pre = {
    querySelector() {
        headerQueries += 1;
        return null;
    }
};
codeBlock.parentNode = pre;
const highlightContext = {
    console,
    isStreaming: false,
    streamingText: "",
    hljs: {
        highlightElement(block) {
            if (block !== codeBlock) throw new Error("unexpected code block");
            highlighted = true;
        }
    }
};
vm.createContext(highlightContext);
vm.runInContext(uiSource.slice(highlightStart, highlightEnd), highlightContext);
highlightContext.highlightCodeBlocks({
    querySelectorAll(selector) {
        if (selector !== "pre code") throw new Error("unexpected selector");
        return [codeBlock];
    }
}, { includeActions: false });
if (!highlighted) throw new Error("attachment preview did not invoke syntax highlighting");
if (headerQueries !== 0) throw new Error("attachment preview must not create conversation code actions");

console.log("attachment display regression tests passed");
