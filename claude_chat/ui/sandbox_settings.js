let sandboxEnvironmentStatus = null;

function normalizeSandboxTimeout(value) {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed)) return 30;
    return Math.max(1, Math.min(600, parsed));
}

function renderCodeSandboxStatus(status) {
    sandboxEnvironmentStatus = status || null;
    if (!codeSandboxStatusText || !codeSandboxComponents) return;

    codeSandboxComponents.replaceChildren();
    if (!status) {
        codeSandboxStatusText.textContent = "检测失败，请查看日志";
        codeSandboxStatusText.style.color = "var(--red)";
        if (enableCodeSandboxInput) enableCodeSandboxInput.disabled = true;
        if (autoRunCodeInput) autoRunCodeInput.disabled = true;
        return;
    }

    const ready = !!status.ready;
    const backendName = status.backend === "docker" ? "Docker" :
        (status.backend === "appcontainer" ? "AppContainer" : status.backend || "未知后端");
    codeSandboxStatusText.textContent = ready ? `${backendName} 沙盒可用` : `${backendName} 沙盒不可用`;
    codeSandboxStatusText.style.color = ready ? "var(--green)" : "var(--red)";

    for (const component of status.components || []) {
        const row = document.createElement("div");
        row.style.display = "flex";
        row.style.alignItems = "flex-start";
        row.style.gap = "6px";
        const badge = document.createElement("span");
        badge.textContent = component.ready ? "✓" : "✕";
        badge.style.color = component.ready ? "var(--green)" : "var(--red)";
        badge.style.fontWeight = "700";
        const textNode = document.createElement("span");
        const detail = component.reason || component.detail || "";
        textNode.textContent = detail ? `${component.name}：${detail}` : component.name;
        textNode.style.color = component.ready ? "var(--subtext0)" : "var(--text)";
        row.append(badge, textNode);
        codeSandboxComponents.appendChild(row);
    }

    if (enableCodeSandboxInput) {
        enableCodeSandboxInput.disabled = !ready;
        enableCodeSandboxInput.checked = ready && !!config.enable_code_sandbox;
    }
    if (autoRunCodeInput) {
        autoRunCodeInput.disabled = !ready || !(enableCodeSandboxInput && enableCodeSandboxInput.checked);
        if (!ready) autoRunCodeInput.checked = false;
    }
    if (installCodeSandboxBtn) {
        const showInstall = !ready && (!!status.can_install || !!status.download_url);
        installCodeSandboxBtn.style.display = showInstall ? "inline-flex" : "none";
        installCodeSandboxBtn.textContent = status.can_install ? "下载 Python/Node 环境" : "获取 Docker";
    }
}

async function refreshCodeSandboxStatus() {
    if (!apiBridge.check_code_sandbox_environment) return;
    if (codeSandboxStatusText) {
        codeSandboxStatusText.textContent = "正在检测...";
        codeSandboxStatusText.style.color = "var(--subtext0)";
    }
    if (refreshCodeSandboxBtn) refreshCodeSandboxBtn.disabled = true;
    try {
        renderCodeSandboxStatus(await apiBridge.check_code_sandbox_environment());
    } catch (error) {
        console.error("代码沙盒检测失败:", error);
        renderCodeSandboxStatus(null);
    } finally {
        if (refreshCodeSandboxBtn) refreshCodeSandboxBtn.disabled = false;
    }
}

if (refreshCodeSandboxBtn) refreshCodeSandboxBtn.onclick = refreshCodeSandboxStatus;

if (installCodeSandboxBtn) {
    installCodeSandboxBtn.onclick = async () => {
        if (sandboxEnvironmentStatus && sandboxEnvironmentStatus.download_url && !sandboxEnvironmentStatus.can_install) {
            window.open(sandboxEnvironmentStatus.download_url, "_blank", "noopener,noreferrer");
            return;
        }
        installCodeSandboxBtn.disabled = true;
        installCodeSandboxBtn.textContent = "正在下载...";
        try {
            const result = await apiBridge.install_code_sandbox_environment();
            if (result && result.download_url) window.open(result.download_url, "_blank", "noopener,noreferrer");
            if (result && result.status) renderCodeSandboxStatus(result.status);
            if (!result || !result.success) {
                alert(result && result.error ? result.error : "沙盒运行环境下载失败，请查看日志。");
            }
        } catch (error) {
            console.error("沙盒环境下载失败:", error);
            alert("沙盒运行环境下载失败，请查看日志。");
        } finally {
            installCodeSandboxBtn.disabled = false;
            await refreshCodeSandboxStatus();
        }
    };
}

if (enableCodeSandboxInput) {
    enableCodeSandboxInput.onchange = () => {
        if (autoRunCodeInput) {
            autoRunCodeInput.disabled = !enableCodeSandboxInput.checked;
            if (!enableCodeSandboxInput.checked) autoRunCodeInput.checked = false;
        }
    };
}
