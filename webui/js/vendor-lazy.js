// Load optional vendor libraries after first paint.
// These are only needed when the user opens an editor, math content, the QR
// modal or a date picker; loading them during startup competes with the app
// bundle for bandwidth and parse time.
(() => {
  const VENDOR = {
    ace: "vendor/ace-min/ace.js",
    qrcode: "vendor/qrcode.min.js",
    flatpickr: "vendor/flatpickr/flatpickr.min.js",
    katex: "vendor/katex/katex.min.js",
    katexAutoRender: "vendor/katex/katex.auto-render.min.js",
  };

  const STYLES = {
    ace: "vendor/ace-min/ace.min.css",
    katex: "vendor/katex/katex.min.css",
  };

  const loaded = new Map();

  const loadStyle = (href, { crossorigin = false } = {}) => {
    if (loaded.has(href)) return loaded.get(href);
    const promise = new Promise((resolve, reject) => {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      if (crossorigin) link.crossOrigin = "anonymous";
      link.onload = () => resolve(href);
      link.onerror = () => reject(new Error(`Failed to load ${href}`));
      document.head.appendChild(link);
    });
    loaded.set(href, promise);
    return promise;
  };

  const loadScript = (src, { crossorigin = false } = {}) => {
    if (loaded.has(src)) return loaded.get(src);
    const promise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.async = true;
      if (crossorigin) script.crossOrigin = "anonymous";
      script.onload = () => resolve(src);
      script.onerror = () => reject(new Error(`Failed to load ${src}`));
      document.head.appendChild(script);
    });
    loaded.set(src, promise);
    return promise;
  };

  const start = () => {
    const katex = loadScript(VENDOR.katex, { crossorigin: true }).then(() =>
      loadScript(VENDOR.katexAutoRender, { crossorigin: true }),
    );
    const jobs = {
      ace: loadScript(VENDOR.ace),
      qrcode: loadScript(VENDOR.qrcode),
      flatpickr: loadScript(VENDOR.flatpickr),
      katex,
      aceStyles: loadStyle(STYLES.ace),
      katexStyles: loadStyle(STYLES.katex, { crossorigin: true }),
    };
    window.a0Vendor = { ...window.a0Vendor, ...jobs };
    Promise.allSettled([
      jobs.aceStyles,
      jobs.katexStyles,
      jobs.ace,
      jobs.qrcode,
      jobs.flatpickr,
      jobs.katex,
    ]).then((results) => {
      const failed = results.filter((result) => result.status === "rejected");
      if (failed.length) console.warn("Optional vendor assets failed:", failed.map((f) => f.reason?.message));
      window.dispatchEvent(new CustomEvent("a0-vendor-ready"));
    });
  };

  // Consumers can await a specific library even before loading has started.
  window.a0VendorWhen = (name) => {
    const pending = window.a0Vendor && window.a0Vendor[name];
    if (pending) return pending;
    return new Promise((resolve) => {
      globalThis.addEventListener(
        "a0-vendor-ready",
        () => resolve(window.a0Vendor && window.a0Vendor[name]),
        { once: true },
      );
    });
  };

  if (document.readyState !== "loading") start();
  else document.addEventListener("DOMContentLoaded", start, { once: true });
})();
