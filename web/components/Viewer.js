"use client";
import { useEffect, useRef, useState } from "react";
import { LoaderIcon } from "lucide-react";
import { useSessionStore } from "@/lib/session-store";
import { useViewerThemeStore } from "@/lib/viewer-theme-store";

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

const renderOptionKeys = ["ambient_intensity", "direct_intensity", "metalness", "roughness", "default_edgecolor", "default_opacity", "normal_len"];
const viewerOptionKeys = ["axes", "axes0", "black_edges", "grid", "collapse", "ortho", "ticks", "center_grid", "grid_font_size", "timeit", "tools", "glass", "up", "transparent", "control", "pan_speed", "zoom_speed", "rotate_speed", "clip_slider_0", "clip_slider_1", "clip_slider_2", "clip_normal_0", "clip_normal_1", "clip_normal_2", "clip_intersection", "clip_planes", "clip_object_colors", "zebra_count", "zebra_opacity", "zebra_direction", "zebra_color_scheme", "zebra_mapping_mode", "studio_environment", "studio_env_intensity", "studio_env_rotation", "studio_background", "studio_tone_mapping", "studio_exposure", "studio_shadow_intensity", "studio_shadow_softness", "studio_ao_intensity", "studio_texture_mapping", "studio_4k_env_maps"];

function getDisplayOptions(config, w, h, theme) {
  const glass = preset(config, "glass", displayDefaultOptions.glass);
  const tools = preset(config, "tools", displayDefaultOptions.tools);
  const treeWidth = preset(config, "tree_width", displayDefaultOptions.treeWidth);
  const tw = glass || !tools ? 0 : treeWidth;
  // three-cad-viewer sizes itself in absolute px (container width = cadWidth + 2).
  // The old `minWidth` (450px) floor made the widget wider than a phone viewport,
  // pushing the model off-center and letting the page scroll. Drop the floor so the
  // canvas fits its container at every width (no mobile branch — that created a hard
  // 500px discontinuity). The -20 keeps a small right margin so the toolbar's
  // right-aligned group isn't shoved against the viewport edge (where the wrapper's
  // overflow:hidden would clip it). The -65 height reserve stays: the toolbar sits
  // above the canvas, so without it the bottom of the scene (axes legend) is cut.
  const cadWidth = Math.max(240, w - tw - 20);
  return {
    glass, treeWidth, tools,
    cadWidth,
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
  // Light/dark background of the 3D scene (three-cad-viewer's own theme). The
  // toggle lives in the chat top bar; we read/apply it via a shared store.
  const theme = useViewerThemeStore((s) => s.theme);
  const setTheme = useViewerThemeStore((s) => s.setTheme);
  const themeRef = useRef(theme);

  useEffect(() => {
    const saved = localStorage.getItem("tcv-theme");
    if (saved === "dark" || saved === "light") {
      setTheme(saved);
    } else if (window.matchMedia?.("(prefers-color-scheme: dark)").matches) {
      // No saved choice: follow the OS like main's "browser" default did, so the
      // viewer background (and part contrast) matches what it was before.
      setTheme("dark");
    }
  }, [setTheme]);

  // Apply theme changes to the live viewer without a rebuild (keeps the camera).
  useEffect(() => {
    themeRef.current = theme;
    try { ref.current.viewer?.setTheme(theme); } catch {}
  }, [theme]);

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

  // Mobile overrides: on narrow screens the viewer already groups toolbar items into
  // collapsible categories (≤760px), but expanding them can still overflow the width.
  // Let the toolbar itself scroll horizontally (the library's default `overflow-x:
  // clip` hides the overflow) so every tool stays reachable without wrapping onto the
  // canvas. Kept to just the toolbar — the 3D view never scrolls.
  useEffect(() => {
    const ID = "tcv-mobile-overrides";
    if (document.getElementById(ID)) return;
    const style = document.createElement("style");
    style.id = ID;
    style.textContent = `
      @media (max-width: 760px) {
        .tcv-scope .tcv_cad_toolbar {
          flex-wrap: nowrap;
          overflow-x: auto;
          overflow-y: hidden;
          overscroll-behavior-x: contain;
          -webkit-overflow-scrolling: touch;
        }
        /* margin-left:auto on the last group creates phantom scrollable width
           inside an overflow container — neutralize it so the toolbar only scrolls
           on genuine overflow (an expanded category), not when collapsed. */
        .tcv-scope .tcv_cad_toolbar > *:last-child {
          margin-left: 0;
        }
      }
    `;
    document.head.appendChild(style);
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
      // Mobile: TrackballControls has poor multi-touch handling (two-finger
      // zoom/pan is jumpy). OrbitControls has purpose-built touch gestures
      // (1 finger rotate, 2 finger pinch-zoom + pan), so use it on phones.
      // Desktop keeps trackball (free-roll rotation preferred with a mouse).
      if (window.innerWidth < 768) viewerOptions.control = "orbit";

      const display = new TCV.Display(container, displayOptions);
      const viewer = new TCV.Viewer(display, displayOptions, notify, null);
      // Mobile: keep the X/Y/Z orientation legend visible regardless of the Tools
      // panel state. The library ties the marker to the panel — showToolsPanel(flag)
      // hides it when collapsed — so wrap showToolsPanel to always restore the marker
      // on phones. Covers every toggle path (initial collapse and user taps).
      if (window.innerWidth < 768) {
        const showToolsPanel = display.showToolsPanel.bind(display);
        display.showToolsPanel = (flag) => {
          showToolsPanel(flag);
          try {
            viewer.rendered?.orientationMarker?.setVisible(true);
            viewer.update(true, false);
          } catch { /* ignore */ }
        };
      }
      viewer.render(payload.data, renderOptions, viewerOptions);
      viewer.glassMode(displayOptions.glass);
      viewer.showTools(displayOptions.tools);

      const rc = preset(config, "reset_camera", "iso");
      if (["iso", "left", "right", "top", "bottom", "rear", "front"].includes(rc)) {
        try { viewer.setView(rc); } catch {}
      }
      ref.current.viewer = viewer;
      setHasModel(true);

      // Mobile: start with the Tools panel collapsed to declutter the small screen.
      // (The wrapped showToolsPanel above keeps the X/Y/Z legend visible.) Render the
      // tree while it's still visible first — the library only re-renders the tree on
      // a tab switch, not on expand, so a tree built/collapsed while zero-sized would
      // show empty when re-expanded. If the pane is currently hidden (built while on
      // the Chat tab), defer to the hidden→visible transition in handleResize.
      ref.current.onShow = null;
      if (window.innerWidth < 768) {
        const collapse = () => {
          try {
            viewer.treeview?.update();
            display.showToolsPanel(false);
          } catch { /* ignore */ }
        };
        if (container.clientWidth > 0) collapse();
        else ref.current.onShow = collapse;
      }

      // Touch pick support: the viewer's Raycaster listens only to mouse events and
      // gates picking on a prior mousemove, so on touch the measure/select tools
      // (distance, properties, copy id) never register a hit. Translate a single-
      // finger tap into the same pick the mouse path runs: seed the ray coords from
      // the tap, resolve the shape, then fire the left-click selection. Gated on an
      // active tool (raycastMode) and a stationary tap so it never eats a rotation.
      try {
        const canvas = display.getCanvas();
        let down = null;
        canvas?.addEventListener("pointerdown", (e) => {
          if (e.pointerType === "mouse" || !e.isPrimary) return;
          down = { x: e.clientX, y: e.clientY };
        });
        canvas?.addEventListener("pointerup", (e) => {
          if (e.pointerType === "mouse" || !e.isPrimary || !down) return;
          const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y);
          down = null;
          const rc = viewer.raycaster;
          if (!rc?.raycastMode || moved > 10) return; // no tool active, or a drag/rotate
          try {
            rc.onPointerMove(e);        // seed ray coords + mark as moved
            viewer.handleRaycast();     // resolve the tapped shape → lastObject
            viewer.handleRaycastEvent({ mouse: "left", shift: e.shiftKey });
          } catch { /* ignore */ }
        });
      } catch { /* ignore */ }

      // Touch drag for the measure/properties popup: the library drags it via a
      // panel mousedown + document mousemove/mouseup, none of which a finger drag
      // produces. Translate a one-finger drag on the panel into those mouse events
      // (clientX/Y position the panel; movementX/Y drive its edge-clamping).
      try {
        const panels = container.querySelectorAll(
          ".tcv_distance_measurement_panel, .tcv_properties_measurement_panel"
        );
        panels.forEach((panel) => {
          panel.style.touchAction = "none";
          panel.addEventListener("touchstart", (e) => {
            if (e.touches.length !== 1) return;
            const t0 = e.touches[0];
            panel.dispatchEvent(new MouseEvent("mousedown", { clientX: t0.clientX, clientY: t0.clientY, button: 0 }));
            e.preventDefault();
            let prev = { x: t0.clientX, y: t0.clientY };
            const move = (ev) => {
              if (ev.touches.length !== 1) return;
              const t = ev.touches[0];
              document.dispatchEvent(new MouseEvent("mousemove", {
                clientX: t.clientX, clientY: t.clientY,
                movementX: t.clientX - prev.x, movementY: t.clientY - prev.y,
              }));
              prev = { x: t.clientX, y: t.clientY };
              ev.preventDefault();
            };
            const end = () => {
              document.dispatchEvent(new MouseEvent("mouseup", { button: 0 }));
              document.removeEventListener("touchmove", move);
              document.removeEventListener("touchend", end);
              document.removeEventListener("touchcancel", end);
            };
            document.addEventListener("touchmove", move, { passive: false });
            document.addEventListener("touchend", end);
            document.addEventListener("touchcancel", end);
          }, { passive: false });
        });
      } catch { /* ignore */ }
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
        // Hidden→visible transition (mobile 3D/Chat toggle): a tree built while the
        // container was display:none renders empty, so refresh it now that it has a
        // real size, and run any deferred mobile Tools-collapse.
        const visible = container.clientWidth > 0;
        if (visible && !ref.current.prevVisible) {
          viewer.treeview?.update();
          if (ref.current.onShow) { ref.current.onShow(); ref.current.onShow = null; }
        }
        ref.current.prevVisible = visible;
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
    <div style={{ position: "relative", width: "100%", height: "100%", overflow: "hidden" }}>
      <div
        ref={containerRef}
        className="tcv-scope"
        style={{
          width: "100%", height: "100%", overflow: "hidden", userSelect: "none",
          // Fills the gap around the TCV widget — was the global `body` bg before scoping.
          background: "var(--tcv-bg-color)",
        }}
      />

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
