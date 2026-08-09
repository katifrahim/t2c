"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  LockIcon, GlobeIcon, PencilIcon, Trash2Icon, EyeIcon,
  Loader2Icon, CheckIcon, XIcon,
} from "lucide-react";
import {
  Tooltip, TooltipTrigger, TooltipContent,
} from "@/components/ui/tooltip";
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle,
  AlertDialogDescription, AlertDialogFooter, AlertDialogCancel, AlertDialogAction,
} from "@/components/ui/alert-dialog";
import { useSessionStore } from "@/lib/session-store";

// The Template Library panel. Opens in place of the chat (like the history panel) and
// lists every template the user can reach: their own (private + any they made public)
// plus everyone's approved public ones. Users can preview each as a 3D model in the
// left viewer, toggle whether it feeds their RAG retrieval, and — for their own
// private templates only — edit the title/description (auto re-embeds) or delete it.
// It never shows or fetches the templates' `steps`.

// A compact on/off switch: green when the template is usable by the AI.
function Switch({ checked, disabled, onChange }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            type="button"
            role="switch"
            aria-checked={checked}
            aria-label="Toggle template for AI retrieval"
            disabled={disabled}
            onClick={(e) => { e.stopPropagation(); onChange(!checked); }}
            style={{
              width: 34, height: 20, borderRadius: 999, border: "none", padding: 2,
              cursor: disabled ? "default" : "pointer", flexShrink: 0,
              background: checked ? "#16a34a" : "#d4d4d4",
              opacity: disabled ? 0.6 : 1, transition: "background .15s",
              display: "inline-flex", alignItems: "center",
            }}
          />
        }
      >
        <span
          style={{
            width: 16, height: 16, borderRadius: "50%", background: "#fff", display: "block",
            transform: checked ? "translateX(14px)" : "translateX(0)", transition: "transform .15s",
          }}
        />
      </TooltipTrigger>
      <TooltipContent side="bottom" sideOffset={8}>
        {checked ? "On — the AI can use this template" : "Off — excluded from your AI retrieval"}
      </TooltipContent>
    </Tooltip>
  );
}

// A borderless row-action icon (pencil / trash), matching the chat-history rows.
function IconAction({ tooltip, danger, onClick, children }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            type="button"
            aria-label={tooltip}
            onClick={(e) => { e.stopPropagation(); onClick(); }}
            style={{ border: "none", background: "none", cursor: "pointer", color: "#bbb", padding: 4, borderRadius: 6, display: "inline-flex", flexShrink: 0 }}
            onMouseEnter={(e) => { e.currentTarget.style.color = danger ? "#dc2626" : "#111"; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = "#bbb"; }}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent side="bottom" sideOffset={8}>{tooltip}</TooltipContent>
    </Tooltip>
  );
}

// The small lock/globe badge, matching the save-template popup's visibility icons.
function VisibilityBadge({ visibility }) {
  const isPublic = visibility === "public";
  const Icon = isPublic ? GlobeIcon : LockIcon;
  return (
    <Tooltip>
      <TooltipTrigger
        render={<span style={{ color: "#9ca3af", display: "inline-flex", flexShrink: 0 }} aria-label={isPublic ? "Public template" : "Private template"} />}
      >
        <Icon size={14} />
      </TooltipTrigger>
      <TooltipContent side="bottom" sideOffset={8}>
        {isPublic ? "Public — shared with everyone" : "Private — only you can use it"}
      </TooltipContent>
    </Tooltip>
  );
}

const inputStyle = {
  width: "100%", padding: "6px 8px", fontSize: 13, color: "#222",
  border: "1px solid #d4d4d4", borderRadius: 6, outline: "none", boxSizing: "border-box",
};

function TemplateRow({ t, active, onPreview, onToggle, onEdited, onDelete }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(t.title);
  const [description, setDescription] = useState(t.description);
  const [saving, setSaving] = useState(false);

  const canManage = t.is_owner && t.visibility === "private";

  const startEdit = () => { setTitle(t.title); setDescription(t.description); setEditing(true); };
  const cancel = () => setEditing(false);
  const save = async () => {
    const nt = title.trim();
    const nd = description.trim();
    if (!nt || !nd) return;
    if (nt === t.title && nd === t.description) { setEditing(false); return; }
    setSaving(true);
    const ok = await onEdited(t.id, nt, nd);
    setSaving(false);
    if (ok) setEditing(false);
  };

  if (editing) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "8px 10px", borderBottom: "1px solid #f2f2f2", background: "#fafafa" }}>
        <input autoFocus value={title} maxLength={40} onChange={(e) => setTitle(e.target.value)} placeholder="Title" style={inputStyle} />
        <textarea value={description} maxLength={300} rows={2} onChange={(e) => setDescription(e.target.value)} placeholder="Description" style={{ ...inputStyle, resize: "vertical" }} />
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 6 }}>
          <button type="button" onClick={cancel} disabled={saving} style={{ fontSize: 12, padding: "5px 10px", borderRadius: 6, border: "1px solid #e5e5e5", background: "#fff", cursor: "pointer", color: "#555" }}>Cancel</button>
          <button type="button" onClick={save} disabled={saving || !title.trim() || !description.trim()} style={{ fontSize: 12, padding: "5px 10px", borderRadius: 6, border: "none", background: "#111", color: "#fff", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4, opacity: saving ? 0.7 : 1 }}>
            {saving ? <Loader2Icon size={13} className="animate-spin" /> : <CheckIcon size={13} />} Save
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      onClick={() => onPreview(t)}
      title="Show this template's 3D model"
      style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", borderBottom: "1px solid #f2f2f2", cursor: "pointer", background: active ? "#f1f1f1" : "transparent" }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = "#f7f7f7"; }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = "transparent"; }}
    >
      <VisibilityBadge visibility={t.visibility} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "#1f2937", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.title}</span>
          {active && !t.has_model && (
            <span style={{ fontSize: 10, color: "#9ca3af", flexShrink: 0 }}>no preview yet</span>
          )}
        </div>
        <div style={{ fontSize: 12, color: "#8a8f98", overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>{t.description}</div>
      </div>
      {active && (
        <span style={{ color: "#6b7280", display: "inline-flex", flexShrink: 0 }} aria-hidden><EyeIcon size={14} /></span>
      )}
      {canManage && (
        <>
          <IconAction tooltip="Edit title & description" onClick={startEdit}><PencilIcon size={15} /></IconAction>
          <IconAction tooltip="Delete template" danger onClick={() => onDelete(t)}><Trash2Icon size={15} /></IconAction>
        </>
      )}
      <Switch checked={t.enabled} onChange={(v) => onToggle(t.id, v)} />
    </div>
  );
}

function Section({ label, hint, children }) {
  return (
    <div style={{ marginBottom: 4 }}>
      <div style={{ padding: "10px 10px 4px", display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.05em", textTransform: "uppercase", color: "#9ca3af" }}>{label}</span>
        {hint && <span style={{ fontSize: 11, color: "#c4c4c4" }}>{hint}</span>}
      </div>
      {children}
    </div>
  );
}

export default function TemplateLibrary() {
  const setPreview = useSessionStore((s) => s.setPreview);
  const clearPreview = useSessionStore((s) => s.clearPreview);
  const [templates, setTemplates] = useState(null); // null = loading
  const [error, setError] = useState(false);
  const [activeId, setActiveId] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const previewing = useRef(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/templates");
        const data = await res.json().catch(() => null);
        if (cancelled) return;
        if (!res.ok) { setError(true); setTemplates([]); return; }
        setTemplates(data?.templates ?? []);
      } catch {
        if (!cancelled) { setError(true); setTemplates([]); }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const { mine, shared } = useMemo(() => {
    const rows = templates ?? [];
    return {
      mine: rows.filter((t) => t.is_owner),
      shared: rows.filter((t) => !t.is_owner),
    };
  }, [templates]);

  const preview = async (t) => {
    setActiveId(t.id);
    if (previewing.current) return;
    previewing.current = true;
    try {
      const res = await fetch(`/api/templates/${t.id}/preview`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (data?.available && data.sessionId) setPreview(data.sessionId);
      else clearPreview(); // legacy template with no stored model — keep viewer on the live model
    } catch {
      clearPreview();
    } finally {
      previewing.current = false;
    }
  };

  const toggle = async (id, enabled) => {
    setTemplates((rows) => rows.map((t) => (t.id === id ? { ...t, enabled } : t)));
    try {
      const res = await fetch(`/api/templates/${id}/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) throw new Error();
    } catch {
      setTemplates((rows) => rows.map((t) => (t.id === id ? { ...t, enabled: !enabled } : t))); // revert
    }
  };

  const edit = async (id, title, description) => {
    try {
      const res = await fetch(`/api/templates/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, description }),
      });
      if (!res.ok) return false;
      setTemplates((rows) => rows.map((t) => (t.id === id ? { ...t, title, description } : t)));
      return true;
    } catch {
      return false;
    }
  };

  const doDelete = async (t) => {
    setConfirmDelete(null);
    setTemplates((rows) => rows.filter((r) => r.id !== t.id));
    if (activeId === t.id) { setActiveId(null); clearPreview(); }
    try {
      await fetch(`/api/templates/${t.id}`, { method: "DELETE" });
    } catch { /* best-effort; the row is already gone from view */ }
  };

  const rowProps = { onPreview: preview, onToggle: toggle, onEdited: edit, onDelete: setConfirmDelete };

  return (
    <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "4px 0" }}>
      {templates === null ? (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, padding: 24, color: "#9ca3af", fontSize: 13 }}>
          <Loader2Icon size={15} className="animate-spin" /> Loading templates…
        </div>
      ) : error ? (
        <div style={{ padding: 24, textAlign: "center", color: "#9ca3af", fontSize: 13 }}>Couldn&apos;t load your templates.</div>
      ) : (mine.length === 0 && shared.length === 0) ? (
        <div style={{ padding: "24px 20px", textAlign: "center", color: "#9ca3af", fontSize: 13, lineHeight: 1.5 }}>
          No templates yet.<br />Save a model as a template from a chat to build your library.
        </div>
      ) : (
        <>
          <Section label="Your templates" hint={mine.length ? null : "none yet"}>
            {mine.map((t) => (
              <TemplateRow key={t.id} t={t} active={activeId === t.id} {...rowProps} />
            ))}
          </Section>
          {shared.length > 0 && (
            <Section label="Public library" hint="shared with everyone">
              {shared.map((t) => (
                <TemplateRow key={t.id} t={t} active={activeId === t.id} {...rowProps} />
              ))}
            </Section>
          )}
        </>
      )}

      <AlertDialog open={!!confirmDelete} onOpenChange={(o) => { if (!o) setConfirmDelete(null); }}>
        <AlertDialogContent size="sm" className="gap-3">
          <AlertDialogHeader className="gap-3">
            <AlertDialogTitle>Delete template?</AlertDialogTitle>
            <AlertDialogDescription>
              &ldquo;{confirmDelete?.title}&rdquo; will be permanently removed from your library and can no longer be suggested to you.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="h-10">Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => confirmDelete && doDelete(confirmDelete)} className="h-10">Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
