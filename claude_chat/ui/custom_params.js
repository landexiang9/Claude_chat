(function (global) {
    "use strict";
    const reserved = new Set(["model", "messages", "contents", "stream", "stream_options", "system", "system_instruction", "tools", "tool_choice", "api_key", "base_url", "api_url", "http_options", "extra_headers", "extra_query", "extra_body", "timeout", "__proto__", "constructor", "prototype"]);
    function hasNonFiniteNumber(value) {
        if (typeof value === "number") return !Number.isFinite(value);
        return value !== null && typeof value === "object" && Object.values(value).some(hasNonFiniteNumber);
    }
    function parseRows(rows) {
        const output = {};
        for (const row of rows) {
            const name = row.name.trim();
            if (!name || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) throw new Error("参数名须以字母或下划线开头，只能包含字母、数字和下划线。");
            if (reserved.has(name)) throw new Error(`“${name}”由程序管理，请使用其他参数名。`);
            if (Object.hasOwn(output, name)) throw new Error(`参数名“${name}”重复。`);
            let value = row.value;
            if (row.type === "number") {
                if (!value.trim() || !Number.isFinite(Number(value))) throw new Error(`“${name}”需要有效数字。`);
                value = Number(value);
            } else if (row.type === "boolean") {
                if (!["true", "false"].includes(value.trim())) throw new Error(`“${name}”请填写 true 或 false。`);
                value = value.trim() === "true";
            } else if (row.type === "json") {
                try { value = JSON.parse(value); }
                catch (_) { throw new Error(`“${name}”的 JSON 格式不正确。`); }
            }
            if (hasNonFiniteNumber(value)) throw new Error(`“${name}”不能包含无限大的数字。`);
            output[name] = value;
        }
        return output;
    }
    let serial = 0;
    const rowElements = [];
    function addRow(name = "", value = "") {
        const container = document.getElementById("custom-params-list");
        if (!container) return;
        const row = document.createElement("div");
        row.className = "custom-param-row";
        const key = document.createElement("input");
        key.type = "text";
        key.value = name;
        key.placeholder = "参数名，如 top_p";
        key.setAttribute("aria-label", "参数名");
        const type = document.createElement("select");
        type.id = `custom-param-type-${++serial}`;
        type.setAttribute("aria-label", "参数类型");
        for (const [id, label] of [["string", "文本"], ["number", "数字"], ["boolean", "布尔值"], ["json", "JSON"]]) {
            const option = document.createElement("option");
            option.value = id; option.textContent = label; type.appendChild(option);
        }
        type.value = value === null || typeof value === "object" ? "json" : typeof value;
        const input = document.createElement("textarea");
        input.rows = 1;
        input.setAttribute("aria-label", "参数值");
        input.value = typeof value === "string" ? value : JSON.stringify(value);
        const updateHint = () => { input.placeholder = ({ string: "参数值", number: "例如 0.9", boolean: "true 或 false", json: '例如 {"enabled": true}' })[type.value]; };
        type.addEventListener("change", updateHint);
        updateHint();
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "btn btn-secondary btn-sm remove-param";
        remove.textContent = "删除";
        remove.setAttribute("aria-label", "删除此参数");
        const entry = { row, key, type, input };
        remove.onclick = () => {
            rowElements.splice(rowElements.indexOf(entry), 1);
            row.remove();
            document.getElementById("custom-params-error").textContent = "";
            global.SelectPicker?.refreshAll();
            document.getElementById("add-custom-param-btn").focus();
            document.dispatchEvent(new Event("customparamschange"));
        };
        const clearError = () => { document.getElementById("custom-params-error").textContent = ""; };
        row.addEventListener("input", clearError);
        row.addEventListener("change", clearError);
        row.append(key, type, input, remove);
        container.appendChild(row);
        rowElements.push(entry);
        global.SelectPicker?.enhance(type);
        if (!name) key.focus();
    }
    function load(params, enabled = true) {
        rowElements.splice(0);
        document.getElementById("custom-params-list")?.replaceChildren();
        const error = document.getElementById("custom-params-error");
        if (error) error.textContent = "";
        const add = document.getElementById("add-custom-param-btn");
        if (add) add.disabled = !enabled;
        Object.entries(params || {}).forEach(([name, value]) => addRow(name, value));
        global.SelectPicker?.refreshAll();
    }
    function read(report = true) {
        const error = document.getElementById("custom-params-error");
        try {
            const params = parseRows(rowElements.map(({ key, type, input }) => ({ name: key.value, type: type.value, value: input.value })));
            if (error) error.textContent = "";
            return params;
        } catch (err) {
            if (error && report) { error.textContent = err.message; error.scrollIntoView({ block: "center" }); }
            return null;
        }
    }
    global.CustomParams = { parseRows, load, read };
    document.addEventListener("DOMContentLoaded", () => {
        const add = document.getElementById("add-custom-param-btn");
        if (add) add.onclick = () => { addRow(); document.dispatchEvent(new Event("customparamschange")); };
    });
})(window);
