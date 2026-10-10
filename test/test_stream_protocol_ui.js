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
}).then(async () => {
    if (received.length !== 2) throw new Error("duplicate or stale stream event was dispatched");
    if (window.getStreamTaskState().status !== "completed") throw new Error("terminal state not applied");
    window.onStreamEvent({version:1,task_id:"task-1",conversation_id:"conv-1",sequence:3,type:"text",data:"late"});
    if (received.length !== 2) throw new Error("output accepted after terminal event");
    startUiStreamTask("conv-2");
    window.onStreamEvent({version:1,task_id:"foreign",conversation_id:"conv-1",sequence:1,type:"text",data:"wrong conversation"});
    window.onStreamEvent({version:1,task_id:"task-1",conversation_id:"conv-2",sequence:4,type:"text",data:"retired task"});
    if (window.getStreamTaskState().taskId !== null) throw new Error("stale event adopted by a new task");
    let release;
    window.onStreamMessage = () => new Promise(resolve => { release = resolve; });
    const terminal = window.onStreamEvent({version:1,task_id:"task-2",conversation_id:"conv-2",sequence:1,type:"done",data:{}});
    startUiStreamTask("conv-3");
    release();
    await terminal;
    if (window.getStreamTaskState().status !== "starting" || window.getStreamTaskState().conversationId !== "conv-3") {
        throw new Error("old terminal handler finished a newer task");
    }
    console.log("frontend stream protocol test passed");
});
`, testContext);
