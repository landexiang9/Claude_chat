(function (global) {
    "use strict";
    const $ = id => document.getElementById(id);
    const clone = value => JSON.parse(JSON.stringify(value));
    let params = {}, model = "", platform = "claude", base = {}, revision = 0;
    let dirty = false, loading = null, ready = false, jsonInvalid = false, formInvalid = false;
    let controlStates = null, editVersion = 0;
    function setBusy(busy) {
        if (busy && !controlStates) {
            controlStates = new Map(Array.from($("model-specific-settings-card").querySelectorAll("input, select, button")).map(el => [el, el.disabled]));
            controlStates.forEach((_, el) => { el.disabled = true; });
        } else if (!busy && controlStates) {
            controlStates.forEach((disabled, el) => { el.disabled = disabled; });
            controlStates = null;
        }
    }
    const knownKeys = () => platform === "claude" ? ["temperature", "max_tokens", "thinking", "output_config"]
        : platform === "gemini" ? ["temperature", "max_output_tokens", "thinking_config"]
        : ["temperature", "max_tokens", "reasoning_effort"];
    const tokenKey = () => platform === "gemini" ? "max_output_tokens" : "max_tokens";
    const extras = value => Object.fromEntries(Object.entries(value).filter(([key]) => !knownKeys().includes(key)));
    function error(message = "") {
        const element = $("model-request-json-error");
        if (element) element.textContent = message;
    }
    function validate(value) {
        if (!value || Array.isArray(value) || typeof value !== "object") throw new Error("最终参数 JSON 必须是对象，例如 {\"temperature\": 0.7}。");
        CustomParams.parseRows(Object.entries(value).map(([name, val]) => ({name, type: "json", value: JSON.stringify(val)})));
        if ("temperature" in value && (typeof value.temperature !== "number" || !Number.isFinite(value.temperature) || value.temperature < 0 || value.temperature > 2)) throw new Error("temperature 必须是 0 到 2 之间的数字。");
        for (const key of ["max_tokens", "max_output_tokens", "max_completion_tokens"]) {
            if (key in value && (!Number.isSafeInteger(value[key]) || value[key] <= 0)) throw new Error(`${key} 必须是正整数。`);
        }
        if (platform === "claude" && !("max_tokens" in value)) throw new Error("Claude 请求必须包含 max_tokens。");
        for (const key of ["thinking", "thinking_config", "output_config"]) {
            if (key in value && (!value[key] || Array.isArray(value[key]) || typeof value[key] !== "object")) throw new Error(`${key} 必须是对象。`);
        }
        if ("reasoning_effort" in value && typeof value.reasoning_effort !== "string") throw new Error("reasoning_effort 必须是文本。");
        // JSON.parse permits numeric overflow; reject it before a JSON round-trip could turn it into null.
        const finite = val => typeof val === "number" ? Number.isFinite(val) : !val || typeof val !== "object" || Object.values(val).every(finite);
        if (!finite(value)) throw new Error("JSON 中的数字不能超出有限范围。");
        return value;
    }
    function setValue(id, value) { if ($(id)) $(id).value = value ?? ""; }
    function show(id, visible) { $(id)?.classList.toggle("hidden", !visible); }
    function effortValue() {
        return platform === "claude" ? params.output_config?.effort
            : platform === "gemini" ? params.thinking_config?.thinking_level : params.reasoning_effort;
    }
    function syncControls(rebuildExtras = true) {
        const temp = params.temperature;
        $("model-temp-enabled").checked = Object.hasOwn(params, "temperature");
        $("model-temp-input").disabled = $("model-temp-slider").disabled = !Object.hasOwn(params, "temperature");
        setValue("model-temp-input", temp);
        setValue("model-temp-slider", temp ?? 0.7);
        $("model-temp-label-title").textContent = "Temperature";
        const token = tokenKey();
        $("model-max-tokens-enabled").checked = Object.hasOwn(params, token);
        $("model-max-tokens-enabled").disabled = platform === "claude";
        $("model-max-tokens-label").textContent = platform === "gemini" ? "Max Output Tokens" : "Max Tokens";
        $("model-max-tokens-input").disabled = !Object.hasOwn(params, token);
        setValue("model-max-tokens-input", params[token]);
        show("model-thinking-container", true);
        const thinking = platform === "claude" ? params.thinking : platform === "gemini" ? params.thinking_config : null;
        const enabled = platform === "claude" ? !!thinking && thinking.type !== "disabled"
            : platform === "gemini" ? !!thinking : Object.hasOwn(params, "reasoning_effort");
        $("model-thinking-enabled-input").checked = enabled;
        show("model-thinking-options", enabled);
        show("model-thinking-type-group", platform === "claude");
        const mode = thinking?.type || "adaptive";
        document.querySelectorAll('input[name="model-thinking-type"]').forEach(input => {
            input.disabled = false; input.checked = input.value === mode;
            input.closest(".radio-label").style.opacity = "1";
        });
        show("model-thinking-budget-group", platform === "gemini" || (platform === "claude" && mode === "enabled"));
        show("model-thinking-level-group", platform !== "claude" || mode === "adaptive");
        setValue("model-thinking-budget-input", (platform === "claude" ? thinking?.budget_tokens : thinking?.thinking_budget) ?? 1024);
        const effort = effortValue();
        const level = $("model-thinking-level-select");
        level.replaceChildren();
        const choices = ["", "low", "medium", "high", "xhigh", "max"];
        if (effort && !choices.includes(effort)) choices.push(effort);
        for (const value of choices) { const option = document.createElement("option"); option.value = value; option.textContent = value || "不指定等级"; level.appendChild(option); }
        level.value = effort ?? "";
        if (rebuildExtras) CustomParams.load(extras(params));
        global.SelectPicker?.refreshAll();
    }
    function writeJson() {
        $("model-request-json").value = JSON.stringify(params, null, 2);
        jsonInvalid = false; formInvalid = false;
        error();
        $("model-request-json-status").textContent = "表单与 JSON 已同步；保存设置后生效";
    }
    function reset() { setBusy(false); revision++; ready = false; dirty = false; jsonInvalid = false; formInvalid = false; loading = null; }
    function isEditing(id, currentPlatform) { return ready && dirty && model === id && platform === currentPlatform; }
    async function load(id, currentPlatform, currentConfig) {
        const token = ++revision;
        model = id; platform = currentPlatform; base = clone(currentConfig || {});
        ready = false; dirty = false; jsonInvalid = false; formInvalid = false;
        error();
        $("model-request-json").disabled = true;
        $("model-request-json-status").textContent = "正在生成最终参数…";
        setBusy(true);
        global.SelectPicker?.refreshAll();
        loading = (async () => {
            try {
                const result = await apiBridge.preview_model_request({model: id, platform: currentPlatform, model_config: base});
                if (token !== revision) return;
                if (!result || result.error || !result.params) throw new Error(result?.error || "无法读取最终参数，请确认程序已重启。");
                params = validate(result.params);
                ready = true;
                syncControls(); writeJson();
            } catch (err) {
                if (token !== revision) return;
                params = clone(base.request_params || base.custom_params || {});
                $("model-request-json").value = JSON.stringify(params, null, 2);
                ready = true; jsonInvalid = true;
                error(err.message);
                $("model-request-json-status").textContent = "读取失败，可修正 JSON 后重新保存";
            } finally {
                if (token === revision) {
                    setBusy(false);
                    $("model-request-json").disabled = false;
                    if (!jsonInvalid) syncControls();
                    global.SelectPicker?.refreshAll();
                }
            }
        })();
        await loading;
    }
    function applyJson() {
        if (!ready) return false;
        dirty = true; editVersion++;
        try {
            const next = validate(JSON.parse($("model-request-json").value));
            params = clone(next); jsonInvalid = false; formInvalid = false; error(); syncControls();
            $("model-request-json-status").textContent = "JSON 已识别，界面参数已更新；保存设置后生效";
            return true;
        } catch (err) {
            jsonInvalid = true;
            error(err instanceof SyntaxError ? "JSON 格式不正确，请检查逗号、引号和括号。" : err.message);
            $("model-request-json-status").textContent = "JSON 尚未生效，界面保留上一份有效内容";
            return false;
        }
    }
    function formChanged(event) {
        if (!ready || event.target.id === "model-request-json") return;
        dirty = true; editVersion++;
        if (jsonInvalid) { error("请先修正 JSON，或点击“恢复有效 JSON”后再修改表单。"); return; }
        const target = event.target;
        const id = target.id;
        const next = clone(params);
        try {
            if (target.closest?.(".custom-param-row") || event.type === "customparamschange") {
                const custom = CustomParams.read(false);
                if (custom === null) { formInvalid = true; error("自定义参数尚未填写完整，JSON 保留上一份有效内容。"); return; }
                for (const key of Object.keys(next)) if (!knownKeys().includes(key)) delete next[key];
                Object.assign(next, custom);
            } else if (["model-temp-slider", "model-temp-input", "model-temp-enabled"].includes(id)) {
                if (!$("model-temp-enabled").checked) delete next.temperature;
                else {
                    const text = id === "model-temp-slider" ? target.value : $("model-temp-input").value;
                    next.temperature = text === "" && id === "model-temp-enabled" ? 0.7 : text === "" ? NaN : Number(text);
                }
            } else if (["model-max-tokens-input", "model-max-tokens-enabled"].includes(id)) {
                if (!$("model-max-tokens-enabled").checked) delete next[tokenKey()];
                else { const text = $("model-max-tokens-input").value; next[tokenKey()] = text === "" && id.endsWith("enabled") ? 16384 : text === "" ? NaN : Number(text); }
            } else if (id.startsWith("model-thinking") || target.name === "model-thinking-type") {
                const enabled = $("model-thinking-enabled-input").checked;
                const effort = $("model-thinking-level-select").value;
                const budget = Number($("model-thinking-budget-input").value);
                if (platform === "claude") {
                    if (!enabled) { delete next.thinking; if (next.output_config) { delete next.output_config.effort; if (!Object.keys(next.output_config).length) delete next.output_config; } }
                    else {
                        const type = document.querySelector('input[name="model-thinking-type"]:checked')?.value || "adaptive";
                        next.thinking = {...(next.thinking || {}), type};
                        if (type === "enabled") { next.thinking.budget_tokens = budget; if (next.output_config) { delete next.output_config.effort; if (!Object.keys(next.output_config).length) delete next.output_config; } }
                        else {
                            delete next.thinking.budget_tokens;
                            next.output_config = {...(next.output_config || {})};
                            if (effort) next.output_config.effort = effort; else delete next.output_config.effort;
                            if (!Object.keys(next.output_config).length) delete next.output_config;
                        }
                    }
                } else if (platform === "gemini") {
                    if (!enabled) delete next.thinking_config;
                    else {
                        next.thinking_config = {...(next.thinking_config || {})};
                        if (id === "model-thinking-budget-input" || (!effort && id === "model-thinking-enabled-input")) { next.thinking_config.thinking_budget = budget; delete next.thinking_config.thinking_level; }
                        else if (effort) { next.thinking_config.thinking_level = effort; delete next.thinking_config.thinking_budget; }
                        else delete next.thinking_config.thinking_level;
                    }
                } else {
                    if (enabled) next.reasoning_effort = effort || "high"; else delete next.reasoning_effort;
                }
            } else return;
            params = validate(next); writeJson(); syncControls(false);
        } catch (err) { formInvalid = true; error(err.message); }
    }
    async function prepareSave() {
        if (loading) await loading;
        if (!ready || !model) { error("请先选择模型并等待参数加载完成。"); return null; }
        if (formInvalid) { CustomParams.read(); return null; }
        if (!applyJson()) return null;
        const custom = CustomParams.read();
        if (custom === null) return null;
        const token = revision;
        const expectedEdit = editVersion;
        try {
            const result = await apiBridge.preview_model_request({model, platform, model_config: {request_params: params}});
            if (token !== revision || expectedEdit !== editVersion) throw new Error("参数已改变，请重新保存。");
            if (!result || result.error || !result.params) throw new Error(result?.error || "无法校验最终参数。");
            params = result.params;
            syncControls(); writeJson();
            const thinking = platform === "claude" ? params.thinking : params.thinking_config;
            return {...base, request_platform: platform, request_params: clone(params), custom_params: extras(params),
                temperature: params.temperature ?? base.temperature ?? 0.7,
                max_tokens: params[tokenKey()] ?? base.max_tokens ?? 16384,
                thinking_enabled: $("model-thinking-enabled-input").checked,
                thinking_type: thinking?.type || "adaptive", thinking_level: effortValue() || "high",
                thinking_budget: thinking?.budget_tokens ?? thinking?.thinking_budget ?? 1024};
        } catch (err) { error(err.message); return null; }
    }
    global.ModelConfigEditor = {load, reset, isEditing, prepareSave, validate};
    document.addEventListener("DOMContentLoaded", () => {
        const card = $("model-specific-settings-card"); if (!card) return;
        card.addEventListener("input", formChanged); card.addEventListener("change", formChanged);
        document.addEventListener("customparamschange", formChanged);
        $("model-request-json").addEventListener("input", applyJson);
        $("format-model-json-btn").onclick = () => { if (applyJson()) writeJson(); };
        $("restore-model-json-btn").onclick = () => { writeJson(); syncControls(); };
    });
})(window);
