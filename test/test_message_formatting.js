const assert = require("assert/strict");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.join(__dirname, "..");
const markedSource = fs.readFileSync(path.join(root, "claude_chat", "ui", "libs", "marked.min.js"), "utf8");
const uiSource = fs.readFileSync(path.join(root, "claude_chat", "ui", "ui.js"), "utf8");
const markdownSource = uiSource.slice(0, uiSource.indexOf("function applyFontMode"));
const markdownContext = {
    escapeHtml(value) {
        return String(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#039;");
    },
    DOMPurify: { sanitize: value => value },
};
vm.createContext(markdownContext);
vm.runInContext(markedSource, markdownContext);
vm.runInContext(markdownSource, markdownContext);

const rendered = markdownContext.parseMarkdown("**bold** <button onclick='bad()'>danger</button>");
assert.match(rendered, /<strong>bold<\/strong>/);
assert.doesNotMatch(rendered, /<button/i);
assert.match(rendered, /&lt;button/);
const plainBody = {
    classList: { values: [], add(value) { this.values.push(value); } },
    innerHTML: "unchanged",
    textContent: "",
};
markdownContext.renderMessageBody(plainBody, "**not bold** <b>not HTML</b>", false);
assert.equal(plainBody.textContent, "**not bold** <b>not HTML</b>");
assert.equal(plainBody.innerHTML, "unchanged");
assert.deepEqual(plainBody.classList.values, ["plain-text"]);

const eventsSource = fs.readFileSync(path.join(root, "claude_chat", "ui", "events.js"), "utf8");
const pasteStart = eventsSource.indexOf("function clipboardFiles");
const pasteEnd = eventsSource.indexOf("// 移动端侧边栏", pasteStart);
const listeners = {};
const uploadedNames = [];
let renderedAttachments = 0;
const inputBox = {
    value: "before ",
    selectionStart: 7,
    selectionEnd: 7,
    addEventListener(type, listener) { listeners[type] = listener; },
    dispatchEvent() {},
    focus() {},
};
const pasteContext = {
    console,
    Date: { now: () => 1234 },
    Event: class Event {},
    inputBox,
    statusLabel: { textContent: "" },
    attachments: [],
    checkIsNative: () => true,
    apiBridge: {
        async paste_attachments_from_clipboard() {
            return { attachments: [{ name: "native.png" }], errors: [] };
        },
    },
    renderAttachments() { renderedAttachments += 1; },
    async handleDroppedFile(_file, displayName) { uploadedNames.push(displayName); },
};
vm.createContext(pasteContext);
vm.runInContext(eventsSource.slice(pasteStart, pasteEnd), pasteContext);

let prevented = false;
const image = { name: "", type: "image/png", size: 3 };
const documentFile = { name: "notes.txt", type: "text/plain", size: 4 };
(async () => {
    await listeners.paste({
        clipboardData: {
            files: [image, documentFile],
            getData: type => type === "text/plain" ? "caption" : "",
        },
        preventDefault() { prevented = true; },
    });

    assert.equal(prevented, true);
    assert.equal(inputBox.value, "before caption");
    assert.deepEqual(uploadedNames, ["粘贴图片-1234-1.png", "notes.txt"]);

    await listeners.paste({
        clipboardData: { files: [], items: [], getData: () => "" },
        preventDefault() {},
    });
    assert.equal(pasteContext.attachments.length, 1);
    assert.equal(pasteContext.attachments[0].name, "native.png");
    assert.equal(renderedAttachments, 1);
    console.log("message formatting and clipboard paste tests passed");
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
