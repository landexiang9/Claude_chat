const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

class FakeElement {
    constructor(tagName) {
        this.tagName = String(tagName).toUpperCase();
        this.children = [];
        this.parentNode = null;
        this.dataset = {};
        this.label = "";
        this.textContent = "";
        this.value = "";
    }

    appendChild(child) {
        child.parentNode = this;
        this.children.push(child);
        return child;
    }

    removeChild(child) {
        const index = this.children.indexOf(child);
        if (index === -1) {
            const error = new Error("The node to be removed is not a child of this node");
            error.name = "NotFoundError";
            throw error;
        }
        this.children.splice(index, 1);
        child.parentNode = null;
        return child;
    }

    remove() {
        if (this.parentNode) this.parentNode.removeChild(this);
    }

    querySelectorAll(selector) {
        if (selector !== 'optgroup[data-custom-providers="true"]') {
            throw new Error(`Unsupported selector in fake DOM: ${selector}`);
        }
        const matches = [];
        const visit = element => {
            for (const child of element.children) {
                if (child.tagName === "OPTGROUP" && child.dataset.customProviders === "true") {
                    matches.push(child);
                }
                visit(child);
            }
        };
        visit(this);
        return matches;
    }
}

class FakeSelect extends FakeElement {
    constructor() {
        super("select");
        this._value = "";
    }

    get options() {
        const options = [];
        const visit = element => {
            for (const child of element.children) {
                if (child.tagName === "OPTION") options.push(child);
                visit(child);
            }
        };
        visit(this);
        return options;
    }

    get value() {
        return this._value;
    }

    set value(nextValue) {
        this._value = this.options.some(option => option.value === nextValue) ? nextValue : "";
    }
}

function makeOption(value, text) {
    const option = new FakeElement("option");
    option.value = value;
    option.textContent = text;
    return option;
}

function extractSourceBetween(source, startMarker, endMarker, fileName) {
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start);
    assert.notStrictEqual(start, -1, `${startMarker} should exist in ${fileName}`);
    assert.notStrictEqual(end, -1, `${endMarker} should follow ${startMarker} in ${fileName}`);
    return source.slice(start, end);
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

async function waitFor(predicate, message) {
    for (let attempt = 0; attempt < 20; attempt += 1) {
        if (predicate()) return;
        await Promise.resolve();
    }
    assert.fail(message);
}

function readPlatformSwitchSource(uiDir) {
    const uiSource = fs.readFileSync(path.join(uiDir, "ui.js"), "utf8");
    return extractSourceBetween(
        uiSource,
        "let platformSwitchVersion = 0;",
        "\nif (platformSelect)",
        "ui.js"
    );
}

async function testSwitchActivePlatform(uiDir) {
    const switchSource = readPlatformSwitchSource(uiDir);

    const claudeModels = [
        { id: "claude-sonnet", display_name: "Claude Sonnet" },
        { id: "claude-opus", display_name: "Claude Opus" }
    ];
    const config = { active_platform: "custom:provider-a", model: "claude-sonnet" };
    const platformSelect = { value: "custom:provider-a" };
    const currentConv = { id: "conversation-1", platform: "custom:provider-a" };
    const conversationSummary = { id: "conversation-1", platform: "custom:provider-a" };
    const saveRequests = [];
    const fetchedPlatforms = [];
    const renderedModelLists = [];
    const modelStatuses = [];
    let ledUpdates = 0;
    let searchButtonUpdates = 0;

    const switchContext = vm.createContext({
        config,
        platformSelect,
        statusLabel: { textContent: "" },
        currentConv,
        currentConvId: "conversation-1",
        conversations: [conversationSummary],
        modelCache: new Map(),
        availableModels: [],
        apiBridge: {
            save_config: async payload => {
                saveRequests.push(payload);
                return true;
            },
            fetch_models: async platform => {
                fetchedPlatforms.push(platform);
                return claudeModels;
            }
        },
        setModelListStatus: message => modelStatuses.push(message),
        updateLedStatus: () => { ledUpdates += 1; },
        updateSearchBtnUI: () => { searchButtonUpdates += 1; },
        updateModelList: (...args) => renderedModelLists.push(args)
    });

    vm.runInContext(switchSource, switchContext, { filename: "ui.js:switchActivePlatform" });
    const switched = await vm.runInContext('switchActivePlatform("claude")', switchContext);

    assert.strictEqual(switched, true);
    assert.strictEqual(saveRequests.length, 1);
    assert.strictEqual(saveRequests[0].active_platform, "claude");
    assert.strictEqual(config.active_platform, "claude");
    assert.strictEqual(platformSelect.value, "claude");
    assert.strictEqual(currentConv.platform, "claude");
    assert.strictEqual(conversationSummary.platform, "claude");
    assert.deepStrictEqual(fetchedPlatforms, ["claude"]);
    assert.strictEqual(switchContext.modelCache.get("claude"), claudeModels);
    assert.strictEqual(switchContext.availableModels, claudeModels);
    assert.strictEqual(renderedModelLists.length, 1);
    assert.strictEqual(renderedModelLists[0][0], claudeModels);
    assert.strictEqual(renderedModelLists[0][1], "claude-sonnet");
    assert.strictEqual(renderedModelLists[0][2], false);
    assert.deepStrictEqual(modelStatuses, ["正在加载模型..."]);
    assert.strictEqual(ledUpdates, 1);
    assert.strictEqual(searchButtonUpdates, 1);
}

async function testRapidPlatformSwitchKeepsLatestResult(uiDir) {
    const switchSource = readPlatformSwitchSource(uiDir);
    const providerASave = deferred();
    const providerAFetch = deferred();
    const providerAModels = [{ id: "model-a", display_name: "Model A" }];
    const providerBModels = [{ id: "model-b", display_name: "Model B" }];
    const config = { active_platform: "claude", model: "model-b" };
    const platformSelect = { value: "claude", selectedOptions: [] };
    const currentConv = { id: "conversation-1", platform: "claude" };
    const conversationSummary = { id: "conversation-1", platform: "claude" };
    const saveOrder = [];
    const fetchOrder = [];
    const renderedModelLists = [];

    const switchContext = vm.createContext({
        console,
        config,
        platformSelect,
        statusLabel: { textContent: "" },
        currentConv,
        currentConvId: "conversation-1",
        conversations: [conversationSummary],
        modelCache: new Map(),
        availableModels: [],
        apiBridge: {
            save_config: payload => {
                saveOrder.push(payload.active_platform);
                if (payload.active_platform === "custom:provider-a") return providerASave.promise;
                return Promise.resolve(true);
            },
            fetch_models: platform => {
                fetchOrder.push(platform);
                if (platform === "custom:provider-a") return providerAFetch.promise;
                return Promise.resolve(providerBModels);
            }
        },
        setModelListStatus() {},
        updateLedStatus() {},
        updateSearchBtnUI() {},
        updateModelList: (...args) => renderedModelLists.push(args)
    });

    vm.runInContext(switchSource, switchContext, { filename: "ui.js:switchActivePlatform" });

    // A 的保存先被挂起；释放后再让 A 的模型请求保持挂起，模拟两个独立的迟到阶段。
    const switchA = vm.runInContext('switchActivePlatform("custom:provider-a")', switchContext);
    await waitFor(() => saveOrder.length === 1, "provider A save should start");
    assert.deepStrictEqual(saveOrder, ["custom:provider-a"]);
    providerASave.resolve(true);
    await waitFor(() => fetchOrder.length === 1, "provider A model fetch should start after its save");
    assert.deepStrictEqual(fetchOrder, ["custom:provider-a"]);

    // A 的模型仍在路上时切换 B；保存队列必须保持 A → B，B 可先完成并渲染。
    const switchB = vm.runInContext('switchActivePlatform("custom:provider-b")', switchContext);
    await waitFor(() => saveOrder.length === 2, "provider B save should follow provider A save");
    assert.deepStrictEqual(saveOrder, ["custom:provider-a", "custom:provider-b"]);
    const switchedB = await switchB;
    assert.strictEqual(switchedB, true);
    assert.deepStrictEqual(fetchOrder, ["custom:provider-a", "custom:provider-b"]);
    assert.strictEqual(renderedModelLists.length, 1);
    assert.strictEqual(renderedModelLists[0][0], providerBModels);

    // A 最后返回时必须被版本检查丢弃，不能覆盖 B 的状态或模型。
    providerAFetch.resolve(providerAModels);
    const switchedA = await switchA;
    assert.strictEqual(switchedA, false);
    assert.strictEqual(config.active_platform, "custom:provider-b");
    assert.strictEqual(platformSelect.value, "custom:provider-b");
    assert.strictEqual(currentConv.platform, "custom:provider-b");
    assert.strictEqual(conversationSummary.platform, "custom:provider-b");
    assert.strictEqual(switchContext.availableModels, providerBModels);
    assert.strictEqual(switchContext.modelCache.has("custom:provider-a"), false);
    assert.strictEqual(switchContext.modelCache.get("custom:provider-b"), providerBModels);
    assert.strictEqual(renderedModelLists.length, 1, "stale provider A models must not render");
}

async function testFailedSwitchRestoresConfirmedPlatform(uiDir) {
    for (const firstOutcome of [false, true, "reject"]) {
        const firstSave = deferred();
        const config = { active_platform: "claude", model: "original-model" };
        const originalModels = [{ id: "original-model" }];
        const rendered = [];
        const saves = [];
        const context = vm.createContext({
            console: { error() {} }, config,
            platformSelect: { value: "claude", selectedOptions: [] },
            statusLabel: { textContent: "" }, currentConv: null, currentConvId: null,
            conversations: [], availableModels: originalModels,
            modelCache: new Map([["claude", originalModels], ["custom:a", originalModels]]),
            setModelListStatus() {}, updateLedStatus() {}, updateSearchBtnUI() {},
            updateModelList: (...args) => rendered.push(args),
            apiBridge: {
                save_config(payload) {
                    saves.push(payload.active_platform);
                    return saves.length === 1 ? firstSave.promise : Promise.resolve(false);
                },
                fetch_models() { throw new Error("Recovery should use cached models"); }
            }
        });
        vm.runInContext(readPlatformSwitchSource(uiDir), context);
        const first = vm.runInContext('switchActivePlatform("custom:a")', context);
        await waitFor(() => saves.length === 1, "first save should start");
        const second = vm.runInContext('switchActivePlatform("custom:b")', context);
        assert.strictEqual(saves.length, 1, "second save should wait for first save");
        if (firstOutcome === "reject") firstSave.reject(new Error("save failed"));
        else firstSave.resolve(firstOutcome);
        await Promise.all([first, second]);
        const expected = firstOutcome === true ? "custom:a" : "claude";
        assert.strictEqual(config.active_platform, expected);
        assert.strictEqual(context.platformSelect.value, expected);
        assert.strictEqual(context.availableModels, originalModels);
        assert.strictEqual(rendered.length, 1, "failure should restore a usable model list");
        assert.strictEqual(rendered[0][2], true, "recovery must preserve the selected model");
        assert.strictEqual(config.model, "original-model");
    }
}

async function testAddProviderRefreshesBeforeCompletion(uiDir) {
    const settings = fs.readFileSync(path.join(uiDir, "settings.js"), "utf8");
    const source = extractSourceBetween(settings, "if (cpSaveBtn) {", "// 供 main.js", "settings.js");
    const refreshed = deferred();
    const providers = [{ id: "new", name: "New Provider" }];
    const calls = [];
    const input = value => ({ value });
    const context = vm.createContext({
        cpSaveBtn: {}, cpNameInput: input("New Provider"), cpApiUrlInput: input("https://example.com"),
        cpModelsApiUrlInput: input(""), cpModelsInput: input("model-one"),
        cpTempInput: input("0.7"), cpMaxTokensInput: input("4096"),
        cpApiKeyInput: input("test-key"), cpEditId: { textContent: "" },
        cpForm: { classList: { add() {} } }, statusLabel: { textContent: "" },
        alert: message => assert.fail(message),
        apiBridge: { add_custom_provider: async () => { calls.push("add"); return providers[0]; } },
        refreshCustomProvidersUI: async () => { calls.push("settings"); return providers; },
        refreshPlatformSelect: data => {
            assert.strictEqual(data, providers, "top dropdown should use the fresh provider snapshot");
            calls.push("platform");
            return refreshed.promise;
        }
    });
    vm.runInContext(source, context);
    let complete = false;
    const save = context.cpSaveBtn.onclick().then(() => { complete = true; });
    await waitFor(() => calls.length === 3, "save should refresh both provider lists");
    assert.deepStrictEqual(calls, ["add", "settings", "platform"]);
    assert.strictEqual(complete, false, "save completion must wait for the top dropdown refresh");
    refreshed.resolve();
    await save;
    assert.match(context.statusLabel.textContent, /已新增提供商/);
}

function assertActiveProviderRemovalFallback(uiDir) {
    const settingsSource = fs.readFileSync(path.join(uiDir, "settings.js"), "utf8");
    const removeSource = extractSourceBetween(
        settingsSource,
        "async function removeCustomProvider(id)",
        "\nif (addCustomProviderBtn)",
        "settings.js"
    );
    assert.match(
        removeSource,
        /const\s+removedActiveProvider\s*=\s*config\.active_platform\s*===\s*`custom:\$\{id\}`\s*;/,
        "deletion should detect whether the removed provider is active"
    );
    assert.match(
        removeSource,
        /if\s*\(removedActiveProvider\)\s+await\s+switchActivePlatform\(["']claude["']\)\s*;/,
        "deleting the active provider should await a switch back to Claude"
    );
}

async function run() {
    const uiDir = path.join(__dirname, "..", "claude_chat", "ui");
    const platformSelect = new FakeSelect();
    platformSelect.appendChild(makeOption("claude", "Anthropic Claude"));
    platformSelect.appendChild(makeOption("deepseek", "DeepSeek AI"));
    platformSelect.appendChild(makeOption("gemini", "Google Gemini"));

    let providers = [
        { id: "provider-a", platform_id: "custom:provider-a", name: "Provider A" }
    ];
    const config = { active_platform: "claude" };
    const document = {
        readyState: "loading",
        createElement: tagName => new FakeElement(tagName)
    };
    const window = {
        location: { search: "" },
        addEventListener() {}
    };
    const context = vm.createContext({
        console,
        window,
        document,
        localStorage: { getItem: () => "", setItem() {} },
        URL,
        URLSearchParams,
        platformSelect,
        config,
        apiBridge: {
            list_custom_providers: async () => providers
        }
    });

    const mainSource = fs.readFileSync(
        path.join(uiDir, "main.js"),
        "utf8"
    );
    vm.runInContext(mainSource, context, { filename: "main.js" });

    await vm.runInContext("refreshPlatformSelect()", context);
    assert.deepStrictEqual(
        platformSelect.options.map(option => option.value),
        ["claude", "deepseek", "gemini", "custom:provider-a"]
    );

    providers = [
        { id: "provider-a", platform_id: "custom:provider-a", name: "Provider A" },
        { id: "provider-b", platform_id: "custom:provider-b", name: "Provider B" }
    ];
    await vm.runInContext("refreshPlatformSelect()", context);
    assert.deepStrictEqual(
        platformSelect.options.map(option => option.value),
        ["claude", "deepseek", "gemini", "custom:provider-a", "custom:provider-b"]
    );
    assert.strictEqual(
        platformSelect.querySelectorAll('optgroup[data-custom-providers="true"]').length,
        1,
        "refresh should replace the custom provider group instead of duplicating it"
    );

    config.active_platform = "custom:provider-b";
    providers = [
        { id: "provider-b", platform_id: "custom:provider-b", name: "Provider B" }
    ];
    await vm.runInContext("refreshPlatformSelect()", context);
    assert.deepStrictEqual(
        platformSelect.options.map(option => option.value),
        ["claude", "deepseek", "gemini", "custom:provider-b"]
    );
    assert.strictEqual(platformSelect.value, "custom:provider-b");

    await testSwitchActivePlatform(uiDir);
    await testRapidPlatformSwitchKeepsLatestResult(uiDir);
    await testFailedSwitchRestoresConfirmedPlatform(uiDir);
    await testAddProviderRefreshesBeforeCompletion(uiDir);
    assertActiveProviderRemovalFallback(uiDir);

    console.log("platform select refresh test passed");
}

run().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
