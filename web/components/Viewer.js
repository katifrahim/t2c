"use client";
import { useEffect, useRef, useState } from "react";
import { LoaderIcon, MoonIcon, SunIcon } from "lucide-react";
import { useSessionStore } from "@/lib/session-store";

// Ported verbatim from ocp_vscode's viewer.html so the look/toolbar (studio
// background, zebra/measure/explode tools, etc.) match the standalone viewer.
const displayDefaultOptions = {
  cadWidth: 730, height: 525, treeWidth: 240, glass: true, theme: "browser",
  tools: true, pinning: false,
  keymap: { shift: "shiftKey", ctrl: "ctrlKey", meta: "metaKey", alt: "altKey" },
  newTreeBehavior: true, measurementDebug: false, measureTools: true,
  selectTool: true, explodeTool: true, zscaleTool: false, zebraTool: true,
};
const viewerDefaultOptions = {
  timeit: false, tools: true, glass: true, up: "Z", zoom: 1.0,
  position: null, quaternion: null, target: null, control: "trackball",
  centerGrid: false, gridFontSize: 12, newTreeBehavior: true,
  studioEnvironment: "studio", studioEnvIntensity: 1.0, studioEnvRotation: 0,
  studioBackground: "environment", studioToneMapping: "neutral", studioExposure: 1.0,
  studioShadowIntensity: 0.5, studioShadowSoftness: 0.2, studioAOIntensity: 0.5,
  studioTextureMapping: "triplanar", studio4kEnvMaps: false,
};
const renderDefaultOptions = {
  ambientIntensity: 1.0, directIntensity: 1.1, metalness: 0.3, roughness: 0.65,
  edgeColor: 0x707070, defaultOpacity: 0.5, normalLen: 0,
};

const toCamel = (s) => s.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
const overrides = { default_edgecolor: "edgeColor", clip_planes: "clipPlaneHelpers", studio_4k_env_maps: "studio4kEnvMaps" };
const preset = (cfg, k, v) => (cfg == null || cfg[k] == null ? v : cfg[k]);
const minWidth = 450;

const renderOptionKeys = ["ambient_intensity", "direct_intensity", "metalness", "roughness", "default_edgecolor", "default_opacity", "normal_len"];
const viewerOptionKeys = ["axes", "axes0", "black_edges", "grid", "collapse", "ortho", "ticks", "center_grid", "grid_font_size", "timeit", "tools", "glass", "up", "transparent", "control", "pan_speed", "zoom_speed", "rotate_speed", "clip_slider_0", "clip_slider_1", "clip_slider_2", "clip_normal_0", "clip_normal_1", "clip_normal_2", "clip_intersection", "clip_planes", "clip_object_colors", "zebra_count", "zebra_opacity", "zebra_direction", "zebra_color_scheme", "zebra_mapping_mode", "studio_environment", "studio_env_intensity", "studio_env_rotation", "studio_background", "studio_tone_mapping", "studio_exposure", "studio_shadow_intensity", "studio_shadow_softness", "studio_ao_intensity", "studio_texture_mapping", "studio_4k_env_maps"];

function getDisplayOptions(config, w, h, theme) {
  const glass = preset(config, "glass", displayDefaultOptions.glass);
  const tools = preset(config, "tools", displayDefaultOptions.tools);
  const treeWidth = preset(config, "tree_width", displayDefaultOptions.treeWidth);
  const tw = glass || !tools ? 0 : treeWidth;
  return {
    glass, treeWidth, tools,
    cadWidth: Math.max(minWidth - tw, w - tw - 20),
    height: h - 65,
    theme: theme || config?.theme || displayDefaultOptions.theme,
    keymap: preset(config, "modifier_keys", displayDefaultOptions.keymap),
    newTreeBehavior: preset(config, "new_tree_behavior", viewerDefaultOptions.newTreeBehavior),
    measureTools: displayDefaultOptions.measureTools,
    selectTool: displayDefaultOptions.selectTool,
    explodeTool: displayDefaultOptions.explodeTool,
    zscaleTool: displayDefaultOptions.zscaleTool,
    zebraTool: displayDefaultOptions.zebraTool,
    measurementDebug: displayDefaultOptions.measurementDebug,
  };
}

function buildOptions(keys, config, defaults) {
  const o = {};
  for (const k of keys) {
    const ok = overrides[k] || toCamel(k);
    o[ok] = preset(config, k, defaults[ok]);
  }
  return o;
}

// Load the vendored ESM via a native module <script> so the bundler doesn't
// touch it (matches the backend tessellator's data protocol exactly).
function loadTCV() {
  return new Promise((resolve, reject) => {
    if (window.__TCV) return resolve(window.__TCV);
    const s = document.createElement("script");
    s.type = "module";
    s.textContent =
      'import * as TCV from "/tcv/three-cad-viewer.esm.js";' +
      'window.__TCV = TCV; window.dispatchEvent(new Event("tcv-ready"));';
    window.addEventListener("tcv-ready", () => resolve(window.__TCV), { once: true });
    s.onerror = () => reject(new Error("failed to load three-cad-viewer"));
    document.head.appendChild(s);
  });
}

export default function Viewer() {
  const containerRef = useRef(null);
  const ref = useRef({ TCV: null, viewer: null, lastVersion: -1, payload: null });
  const sessionId = useSessionStore((s) => s.sessionId);
  const sidRef = useRef(sessionId);
  // Blank white until the backend/MCP server delivers the first model; show a
  // loader in the viewer area until then.
  const [hasModel, setHasModel] = useState(false);
  // Light/dark background of the 3D scene (three-cad-viewer's own theme).
  const [theme, setThemeState] = useState("light");
  const themeRef = useRef("light");

  useEffect(() => {
    const saved = localStorage.getItem("tcv-theme");
    if (saved === "dark" || saved === "light") { themeRef.current = saved; setThemeState(saved); }
  }, []);

  function toggleTheme() {
    const next = themeRef.current === "dark" ? "light" : "dark";
    themeRef.current = next;
    setThemeState(next);
    try { localStorage.setItem("tcv-theme", next); } catch {}
    try { ref.current.viewer?.setTheme(next); } catch {}
  }

  // On chat/session switch: keep the current scene on screen (no blanking) and
  // force the next poll to re-render this session's model — or its placeholder
  // (grid + tools + empty scene), so the viewer widget is NEVER torn down.
  useEffect(() => {
    sidRef.current = sessionId;
    ref.current.lastVersion = -1;
  }, [sessionId]);

  // Load the viewer stylesheet only when the viewer mounts (it's scoped under
  // .tcv-scope, so it never leaks into the rest of the app).
  useEffect(() => {
    const HREF = "/tcv/three-cad-viewer.scoped.css";
    if (!document.querySelector(`link[href="${HREF}"]`)) {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = HREF;
      document.head.appendChild(link);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer = null;

    // Forward measurement tool events (distance/properties) to the backend and
    // feed the computed result back to the viewer.
    async function notify(change) {
      try {
        const flat = {};
        for (const k in change) {
          const v = change[k];
          flat[k] = v && typeof v === "object" && "new" in v ? v.new : v;
        }
        // "copy id to clipboard" (standalone does this server-side via pyperclip)
        if (flat.selected != null) {
          try { await navigator.clipboard.writeText([].concat(flat.selected).join(",")); } catch {}
        }
        if (!("activeTool" in flat) && !("selectedShapeIDs" in flat)) return;
        const resp = await fetch(`/api/backend?session=${sidRef.current}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(flat),
        });
        if (!resp.ok) return;
        const data = await resp.json();
        if (data && data.type === "backend_response" && ref.current.viewer) {
          ref.current.viewer.handleBackendResponse(data);
        }
      } catch { /* ignore */ }
    }

    function renderModel(payload) {
      const { TCV } = ref.current;
      const container = containerRef.current;
      if (!TCV || !container) return;
      ref.current.payload = payload; // remember for re-fitting on resize
      if (ref.current.viewer) { try { ref.current.viewer.dispose(); } catch {} }
      container.innerHTML = "";

      const config = payload.config || {};
      const w = container.clientWidth || 800;
      const h = container.clientHeight || 600;
      const displayOptions = getDisplayOptions(config, w, h, themeRef.current);
      const renderOptions = buildOptions(renderOptionKeys, config, renderDefaultOptions);
      const viewerOptions = buildOptions(viewerOptionKeys, config, viewerDefaultOptions);

      const display = new TCV.Display(container, displayOptions);
      const viewer = new TCV.Viewer(display, displayOptions, notify, null);
      viewer.render(payload.data, renderOptions, viewerOptions);
      viewer.glassMode(displayOptions.glass);
      viewer.showTools(displayOptions.tools);

      const rc = preset(config, "reset_camera", "iso");
      if (["iso", "left", "right", "top", "bottom", "rear", "front"].includes(rc)) {
        try { viewer.setView(rc); } catch {}
      }
      ref.current.viewer = viewer;
      setHasModel(true);
    }

    async function poll() {
      const sid = sidRef.current;
      try {
        const v = (await (await fetch(`/api/version?session=${sid}`)).json()).version;
        if (sid !== sidRef.current || v === ref.current.lastVersion) return;
        const resp = await fetch(`/api/model?session=${sid}`);
        if (sid !== sidRef.current) return; // session switched mid-poll
        if (resp.status !== 200) return; // no payload yet — keep the current scene
        ref.current.lastVersion = v;
        const payload = await resp.json();
        if (!cancelled) renderModel(payload);
      } catch { /* backend not reachable yet */ }
    }

    (async () => {
      try { ref.current.TCV = await loadTCV(); }
      catch (e) { console.error(e); return; }
      if (cancelled) return;
      await poll();
      timer = setInterval(poll, 1000);
    })();

    // Re-fit the canvas to the container on window resize AND slider drags,
    // preserving the camera/model (mirrors ocp_vscode's viewer.html — re-render
    // is avoided so the user's view isn't reset).
    function handleResize() {
      const viewer = ref.current.viewer;
      const container = containerRef.current;
      if (!viewer || !container) return;
      const config = (ref.current.payload && ref.current.payload.config) || {};
      const w = container.clientWidth || 800;
      const h = container.clientHeight || 600;
      const d = getDisplayOptions(config, w, h, themeRef.current);
      try {
        viewer.resizeCadView(d.cadWidth, d.treeWidth, d.height, d.glass);
        if (viewer.gridHelper) {
          viewer.gridHelper.clearCache();
          viewer.gridHelper.update(viewer.getCameraZoom(), true);
        }
        viewer.update(true, true);
      } catch { /* ignore */ }
    }

    let rafId = null;
    const ro = new ResizeObserver(() => {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(handleResize);
    });
    if (containerRef.current) ro.observe(containerRef.current);

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
      if (rafId) cancelAnimationFrame(rafId);
      ro.disconnect();
      if (ref.current.viewer) { try { ref.current.viewer.dispose(); } catch {} }
    };
  }, []);

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      <div
        ref={containerRef}
        className="tcv-scope"
        style={{
          width: "100%", height: "100%", overflow: "hidden", userSelect: "none",
          // Fills the gap around the TCV widget — was the global `body` bg before scoping.
          background: "var(--tcv-bg-color)",
        }}
      />
      <button
        type="button"
        onClick={toggleTheme}
        aria-label={theme === "dark" ? "Use light background" : "Use dark background"}
        title={theme === "dark" ? "Light background" : "Dark background"}
        className="absolute right-3 bottom-3 z-20 grid size-8 cursor-pointer place-items-center rounded-md border border-border bg-background/90 text-foreground shadow-sm backdrop-blur transition-colors hover:bg-muted"
      >
        {theme === "dark" ? <SunIcon size={16} /> : <MoonIcon size={16} />}
      </button>

      {!hasModel && (
        <div
          style={{
            position: "absolute", inset: 0, display: "flex",
            alignItems: "center", justifyContent: "center", pointerEvents: "none",
          }}
        >
          <LoaderIcon className="animate-spin text-muted-foreground" size={28} />
        </div>
      )}
    </div>
  );
}
