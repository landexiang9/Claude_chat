(function () {
    const components = new Map();

    window.PlatformSettings = {
        register(platform, component) {
            if (!platform || !component) throw new Error("平台设置组件缺少名称或实现");
            components.set(platform, component);
        },

        bindAll() {
            components.forEach(component => component.bind && component.bind());
        },

        loadAll(currentConfig) {
            components.forEach(component => component.load && component.load(currentConfig));
        },

        saveAll(currentConfig) {
            components.forEach(component => component.save && component.save(currentConfig));
        },

        registeredPlatforms() {
            return Array.from(components.keys());
        }
    };
})();
