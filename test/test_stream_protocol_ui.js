const fs = require("fs");
const path = require("path");
const vm = require("vm");

const received = [];
const source = fs.readFileSync(
    path.join(__dirname, "..", "claude_chat", "ui", "stream_protocol.js"),
    "utf8"
);

const testContext = vm.createContext({ console, window: {}, isStreaming: false, Promise, Set, received });
vm.runInContext(`${source}
window.onStreamMessage = async (type, data) => { received.push([type, data]); };
startUiStreamTask("conv-1");
window.onStreamEvent({
    version: 1, task_id: "task-1", conversation_id: "conv-1", sequence: 1,
    type: "text", task_state: "running", data: "hello"
});
window.onStreamEvent({
    version: 1, task_id: "task-1", conversation_id: "conv-1", sequence: 1,
    type: "text", task_state: "running", data: "duplicate"
});
window.onStreamEvent({
    version: 1, task_id: "stale", conversation_id: "conv-1", sequence: 2,
    type: "text", task_state: "running", data: "stale"
});
window.onStreamEvent({
    version: 1, task_id: "task-1", conversation_id: "conv-1", sequence: 2,
    type: "done", task_state: "completed", data: {}
}).then(() => {
    if (received.length !== 2) throw new Error("duplicate or stale stream event was dispatched");
    if (window.getStreamTaskState().status !== "completed") throw new Error("terminal state not applied");
    console.log("frontend stream protocol test passed");
});
`, testContext);
