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

function setUiStreamTaskState(status, patch = {}) {
    currentStreamTask = { ...currentStreamTask, ...patch, status };
    isStreaming = ACTIVE_STREAM_TASK_STATES.has(status);
}

function startUiStreamTask(conversationId) {
    currentStreamTask = {
        taskId: null,
        conversationId: conversationId || null,
        status: "starting",
        lastSequence: 0
    };
    isStreaming = true;
}

function requestUiStreamCancellation() {
    if (isStreaming) setUiStreamTaskState("cancelling");
}

function finishUiStreamTask(status) {
    setUiStreamTaskState(status);
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
            return Promise.resolve(result).finally(() => finishUiStreamTask(terminalState));
        }
        return result;
    }
};

window.getStreamTaskState = () => ({ ...currentStreamTask });
