const CONVERSATION_RENDER_BATCH_SIZE = 60;
let conversationRenderState = { groups: [], renderedStart: 0 };

function buildConversationMessageGroups(messages) {
    const groups = [];
    for (let i = 0; i < messages.length; i++) {
        const msg = messages[i];
        if (msg.role === "user" && isToolResultMsg(msg.content)) continue;

        let toolCalls = extractToolCallsFromMsg(msg, i + 1 < messages.length ? messages[i + 1] : null);
        let mergedThinking = msg.thinking || "";
        let mergedContent = Array.isArray(msg.content) ? [...msg.content] : msg.content;
        const messageIndex = i;

        if (msg.role === "assistant") {
            while (i + 2 < messages.length &&
                   messages[i + 1].role === "user" && isToolResultMsg(messages[i + 1].content) &&
                   messages[i + 2].role === "assistant") {
                const nextAssistant = messages[i + 2];
                if (nextAssistant.thinking) {
                    mergedThinking += `${mergedThinking ? "\n\n" : ""}${nextAssistant.thinking}`;
                }
                const extraText = typeof nextAssistant.content === "string"
                    ? nextAssistant.content
                    : (nextAssistant.content || [])
                        .filter(item => item && item.type === "text")
                        .map(item => item.text)
                        .join("\n");
                if (extraText) {
                    if (typeof mergedContent === "string") {
                        mergedContent += `${mergedContent ? "\n\n" : ""}${extraText}`;
                    } else if (Array.isArray(mergedContent)) {
                        mergedContent.push({ type: "text", text: `\n\n${extraText}` });
                    }
                }
                const nextToolCalls = extractToolCallsFromMsg(
                    nextAssistant,
                    i + 3 < messages.length ? messages[i + 3] : null
                );
                if (nextToolCalls.length) toolCalls = toolCalls.concat(nextToolCalls);
                i += 2;
            }
        }

        groups.push({
            role: msg.role,
            content: mergedContent,
            thinking: mergedThinking,
            messageIndex,
            toolCalls
        });
    }
    return groups;
}

function renderConversationGroupRange(groups, start, end) {
    const fragment = document.createDocumentFragment();
    for (const group of groups.slice(start, end)) {
        appendMessage(
            group.role,
            group.content,
            group.thinking,
            false,
            group.messageIndex,
            group.toolCalls,
            fragment
        );
    }
    return fragment;
}

function updateLoadOlderMessagesButton() {
    document.getElementById("load-older-messages-btn")?.remove();
    if (conversationRenderState.renderedStart <= 0) return;
    const button = document.createElement("button");
    button.id = "load-older-messages-btn";
    button.className = "load-older-messages-btn";
    button.textContent = `加载更早消息（剩余 ${conversationRenderState.renderedStart} 组）`;
    button.onclick = loadOlderConversationMessages;
    messageList.insertBefore(button, messageList.firstChild);
}

function loadOlderConversationMessages() {
    const { groups, renderedStart } = conversationRenderState;
    if (renderedStart <= 0) return;
    const previousHeight = chatViewport.scrollHeight;
    const previousTop = chatViewport.scrollTop;
    document.getElementById("load-older-messages-btn")?.remove();

    const nextStart = Math.max(0, renderedStart - CONVERSATION_RENDER_BATCH_SIZE);
    const fragment = renderConversationGroupRange(groups, nextStart, renderedStart);
    messageList.insertBefore(fragment, messageList.firstChild);
    conversationRenderState.renderedStart = nextStart;
    updateLoadOlderMessagesButton();

    requestAnimationFrame(() => {
        chatViewport.scrollTop = previousTop + (chatViewport.scrollHeight - previousHeight);
    });
}

function renderConversationMessages(messages) {
    const groups = buildConversationMessageGroups(Array.isArray(messages) ? messages : []);
    const renderedStart = Math.max(0, groups.length - CONVERSATION_RENDER_BATCH_SIZE);
    conversationRenderState = { groups, renderedStart };
    messageList.appendChild(renderConversationGroupRange(groups, renderedStart, groups.length));
    updateLoadOlderMessagesButton();
}
