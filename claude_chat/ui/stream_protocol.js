const STREAM_PROTOCOL_VERSION = 1;
const STREAM_EVENT_TYPES = new Set([
    "text", "thinking", "search_start", "search_done", "fetch_start", "fetch_done",
    "done", "aborted", "error"
]);
const ACTIVE_STREAM_TASK_STATES = new Set(["starting", "running", "cancelling"]);

let currentStreamTask = {
    taskId: null,
    conversationId: null,
    status: "idle",
    lastSequence: 0
};
const retiredStreamTaskIds = new Set();

function setUiStreamTaskState(status, patch = {}) {
    Object.assign(currentStreamTask, patch, { status });
    isStreaming = ACTIVE_STREAM_TASK_STATES.has(status);
}

function startUiStreamTask(conversationId) {
    if (currentStreamTask.taskId) {
        retiredStreamTaskIds.add(currentStreamTask.taskId);
        if (retiredStreamTaskIds.size > 100) retiredStreamTaskIds.delete(retiredStreamTaskIds.values().next().value);
    }
    currentStreamTask = {
        taskId: null,
        conversationId: conversationId || null,
        status: "starting",
        lastSequence: 0,
        row: typeof document !== "undefined" ? document.getElementById("streaming-msg-row") : null,
        viewNodes: typeof messageList !== "undefined" ? Array.from(messageList.childNodes) : [],
        terminalReceived: false
    };
    isStreaming = true;
}

function requestUiStreamCancellation() {
    if (isStreaming) setUiStreamTaskState("cancelling");
}

function finishUiStreamTask(status) {
    setUiStreamTaskState(status);
    currentStreamTask.row = null;
    currentStreamTask.viewNodes = [];
}

async function confirmUiStreamStarted(task) {
    // Stop can arrive before send_message has created the backend task.
    if (currentStreamTask === task && task.status === "cancelling" && !task.terminalReceived) {
        try {
            const result = await apiBridge.abort_generation();
            if (!result || result.success === false) throw new Error("终止请求失败，请重试");
        } catch (error) {
            console.error("Unable to cancel the started stream:", error);
            if (currentStreamTask === task) statusLabel.textContent = "终止请求失败，请重试";
        }
    }
}

function normalizeStreamEvent(rawEvent) {
    if (!rawEvent || typeof rawEvent !== "object") return null;
    const type = String(rawEvent.type || "");
    if (!STREAM_EVENT_TYPES.has(type)) return null;
    const version = Number(rawEvent.version || STREAM_PROTOCOL_VERSION);
    if (version !== STREAM_PROTOCOL_VERSION) {
        console.error(`Unsupported stream protocol version: ${version}`);
        return null;
    }
    return {
        version,
        task_id: rawEvent.task_id ? String(rawEvent.task_id) : null,
        conversation_id: rawEvent.conversation_id ? String(rawEvent.conversation_id) : null,
        sequence: Number.isInteger(rawEvent.sequence) ? rawEvent.sequence : 0,
        type,
        task_state: rawEvent.task_state ? String(rawEvent.task_state) : null,
        data: rawEvent.data
    };
}

window.onStreamEvent = (rawEvent) => {
    const event = normalizeStreamEvent(rawEvent);
    if (!event) return;
    if (!isStreaming || currentStreamTask.terminalReceived) return;
    if (event.task_id && retiredStreamTaskIds.has(event.task_id)) return;
    if (event.conversation_id && currentStreamTask.conversationId
            && event.conversation_id !== String(currentStreamTask.conversationId)) return;

    if (event.task_id) {
        if (currentStreamTask.taskId && currentStreamTask.taskId !== event.task_id) {
            console.warn("Ignored event from a stale stream task", event.task_id);
            return;
        }
        if (event.sequence && event.sequence <= currentStreamTask.lastSequence) return;
        currentStreamTask.taskId = event.task_id;
        currentStreamTask.lastSequence = event.sequence || currentStreamTask.lastSequence;
    }
    if (event.conversation_id) currentStreamTask.conversationId = event.conversation_id;
    if (currentStreamTask.status === "starting") setUiStreamTaskState("running");

    if (typeof window.onStreamMessage === "function") {
        const result = window.onStreamMessage(event.type, event.data, event);
        const terminalState = {
            done: "completed",
            aborted: "aborted",
            error: "failed"
        }[event.type];
        if (terminalState) {
            const task = currentStreamTask;
            task.terminalReceived = true;
            return Promise.resolve(result).finally(() => {
                if (currentStreamTask === task) finishUiStreamTask(terminalState);
            });
        }
        return result;
    }
};

window.getStreamTaskState = () => ({
    taskId: currentStreamTask.taskId,
    conversationId: currentStreamTask.conversationId,
    status: currentStreamTask.status,
    lastSequence: currentStreamTask.lastSequence
});
