const fs = require("fs");
const vm = require("vm");
const assert = require("assert/strict");
const context = { window: {}, document: { addEventListener() {} } };
vm.runInNewContext(fs.readFileSync(require("path").join(__dirname, "../claude_chat/ui/custom_params.js"), "utf8"), context);
const parse = context.window.CustomParams.parseRows;
const rows = [
    { name: "top_p", type: "number", value: "0.8" },
    { name: "enabled", type: "boolean", value: "false" },
    { name: "stop", type: "json", value: '["END"]' },
    { name: "user", type: "string", value: "123" },
    { name: "nullable", type: "json", value: "null" }
];
assert.equal(JSON.stringify(parse(rows)), '{"top_p":0.8,"enabled":false,"stop":["END"],"user":"123","nullable":null}');
assert.equal(JSON.stringify(parse([])), "{}");
for (const row of [
    { name: "stream", type: "boolean", value: "false" },
    { name: "__proto__", type: "json", value: "{}" },
    { name: "", type: "string", value: "x" },
    { name: "top_p", type: "number", value: "Infinity" },
    { name: "top_p", type: "number", value: "" },
    { name: "enabled", type: "boolean", value: "yes" },
    { name: "stop", type: "json", value: "[" },
    { name: "overflow", type: "json", value: "[1e400]" }
]) assert.throws(() => parse([row]));
assert.throws(() => parse([rows[0], rows[0]]));
console.log("custom parameter type and validation tests passed");
