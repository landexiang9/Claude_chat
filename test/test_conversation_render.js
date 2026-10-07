const fs = require("fs");
const path = require("path");
const vm = require("vm");

function makeContainer() {
    return {
        children: [],
        firstChild: null,
        appendChild(node) {
            if (node && Array.isArray(node.children)) this.children.push(...node.children);
            else this.children.push(node);
            this.firstChild = this.children[0] || null;
        },
        insertBefore(node, reference) {
            const nodes = node && Array.isArray(node.children) ? node.children : [node];
            const index = reference ? this.children.indexOf(reference) : -1;
            this.children.splice(index >= 0 ? index : this.children.length, 0, ...nodes);
            this.firstChild = this.children[0] || null;
        }
    };
}

const messageList = makeContainer();
const document = {
    createDocumentFragment: makeContainer,
    createElement() {
        return { id: "", className: "", textContent: "", onclick: null, remove() {} };
    },
    getElementById(id) {
        if (id !== "load-older-messages-btn") return null;
        const item = messageList.children.find(child => child && child.id === id) || null;
        if (item) item.remove = () => {
            messageList.children = messageList.children.filter(child => child !== item);
            messageList.firstChild = messageList.children[0] || null;
        };
        return item;
    }
};
const context = {
    console,
    document,
    messageList,
    chatViewport: { scrollHeight: 1000, scrollTop: 100 },
    requestAnimationFrame: callback => callback(),
    isToolResultMsg: () => false,
    extractToolCallsFromMsg: () => [],
    appendMessage(role, content, thinking, streaming, index, tools, target, renderMarkdown, isError) {
        target.appendChild({ role, content, index, renderMarkdown, isError });
    }
};
vm.createContext(context);
const source = fs.readFileSync(
    path.join(__dirname, "..", "claude_chat", "ui", "conversation_render.js"),
    "utf8"
);
vm.runInContext(`${source}
const messages = Array.from({length: 500}, (_, index) => ({
    role: index % 2 ? "assistant" : "user",
    content: "message-" + index
}));
renderConversationMessages(messages);
if (messageList.children.length !== 61) throw new Error("initial render must contain 60 groups and one button");
loadOlderConversationMessages();
if (messageList.children.length !== 121) throw new Error("loading older messages must add one 60-group batch");
const plainMessage = buildConversationMessageGroups([
    { role: "user", content: "**plain**", render_markdown: false }
])[0];
if (plainMessage.renderMarkdown !== false) throw new Error("message markdown choice must survive grouping");
const errorMessage = buildConversationMessageGroups([
    { role: "assistant", content: [{ type: "text", text: "failed", _stream_error: true }] }
])[0];
if (errorMessage.isError !== true) throw new Error("persisted stream errors must survive grouping");
`, context);

console.log("long conversation render test passed");
