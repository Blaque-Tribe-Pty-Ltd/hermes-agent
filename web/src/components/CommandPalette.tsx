import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import {
  ArrowRight,
  FileText,
  Moon,
  RotateCw,
  Search,
  Sun,
  Terminal,
  Zap,
} from "lucide-react";
import { useNavigate } from "react-router";
import { cn } from "@/lib/utils";
import { useI18n } from "@/i18n";
import { api } from "@/lib/api";
import type { SessionInfo } from "@/lib/api";
import { useSystemActions } from "@/contexts/useSystemActions";
import { useTheme } from "@/themes";
import { ALL_BUILTIN_NAV_ITEMS, type NavItem } from "@/lib/navigation";
import { usePlugins } from "@/plugins";

export interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

interface CommandResult {
  id: string;
  label: string;
  group: string;
  icon?: React.ComponentType<{ className?: string }>;
  shortcut?: string;
  onSelect: () => void;
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { themeName, availableThemes, setTheme } = useTheme();
  const { runAction } = useSystemActions();
  const { manifests } = usePlugins();
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [recentSessions, setRecentSessions] = useState<SessionInfo[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setSelectedIndex(0);
    api
      .getSessions(10, 0, "recent")
      .then((res) => setRecentSessions(res.sessions))
      .catch(() => setRecentSessions([]));
  }, [open]);

  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
    }
  }, [open]);

  const allPageResults: CommandResult[] = useMemo(() => {
    const pageItems: NavItem[] = [...ALL_BUILTIN_NAV_ITEMS];
    for (const manifest of manifests) {
      if (manifest.tab.override) continue;
      if (manifest.tab.hidden) continue;
      pageItems.push({
        path: manifest.tab.path,
        label: manifest.label,
        icon: undefined,
      });
    }
    return pageItems.map((item, index) => ({
      id: `page:${item.path}`,
      label: item.label,
      group:
        item.labelKey && (t.app.nav as Record<string, string>)[item.labelKey]
          ? (t.app.nav as Record<string, string>)[item.labelKey]
          : item.label,
      icon: item.icon as React.ComponentType<{ className?: string }> | undefined,
      shortcut: index < 9 ? `⌘${index + 1}` : undefined,
      onSelect: () => {
        navigate(item.path);
        onClose();
      },
    }));
  }, [manifests, navigate, onClose, t.app.nav]);

  const actionResults: CommandResult[] = useMemo(
    () => [
      {
        id: "action:restart",
        label: t.status.restartGateway,
        group: t.status.actions,
        icon: RotateCw,
        onSelect: () => {
          void runAction("restart");
          onClose();
        },
      },
      {
        id: "action:update",
        label: t.status.updateHermes,
        group: t.status.actions,
        icon: Zap,
        onSelect: () => {
          void runAction("update");
          onClose();
        },
      },
      {
        id: "action:theme",
        label: themeName === "nous-blue" ? "Switch to dark theme" : "Switch to light theme",
        group: t.status.actions,
        icon: themeName === "nous-blue" ? Moon : Sun,
        onSelect: () => {
          const next =
            themeName === "nous-blue"
              ? "default"
              : availableThemes.find((th) => th.name === "nous-blue")?.name ?? themeName;
          void setTheme(next);
          onClose();
        },
      },
    ],
    [
      availableThemes,
      onClose,
      runAction,
      setTheme,
      t.status.actions,
      t.status.restartGateway,
      t.status.updateHermes,
      themeName,
    ],
  );

  const recentResults: CommandResult[] = useMemo(
    () =>
      recentSessions.map((session) => ({
        id: `recent:${session.id}`,
        label: session.title || t.sessions.untitledSession,
        group: t.status.recentSessions,
        icon: Terminal,
        onSelect: () => {
          navigate(`/sessions?id=${session.id}`);
          onClose();
        },
      })),
    [navigate, onClose, recentSessions, t.sessions.untitledSession, t.status.recentSessions],
  );

  const allResults = useMemo(
    () => [...allPageResults, ...actionResults, ...recentResults],
    [allPageResults, actionResults, recentResults],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return allResults;
    return allResults.filter((r) => r.label.toLowerCase().includes(q));
  }, [allResults, query]);

  const grouped = useMemo(() => {
    const map = new Map<string, CommandResult[]>();
    for (const result of filtered) {
      const list = map.get(result.group) ?? [];
      list.push(result);
      map.set(result.group, list);
    }
    return map;
  }, [filtered]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (!open) return;

      const digitMatch = e.key.match(/^(\d)$/);
      if ((e.metaKey || e.ctrlKey) && digitMatch) {
        e.preventDefault();
        const idx = parseInt(digitMatch[1], 10) - 1;
        if (filtered[idx]) {
          filtered[idx].onSelect();
        }
        return;
      }

      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => (i + 1) % Math.max(filtered.length, 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) =>
          i <= 0 ? Math.max(filtered.length - 1, 0) : i - 1,
        );
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (filtered[selectedIndex]) {
          filtered[selectedIndex].onSelect();
        }
      }
    },
    [filtered, onClose, open, selectedIndex],
  );

  useEffect(() => {
    if (!open) return;
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown, open]);

  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>("[data-selected='true']");
    el?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  if (!open) return null;

  return createPortal(
    <div
      aria-modal="true"
      className="fixed inset-0 z-[100] flex items-start justify-center bg-black/60 p-4 pt-[10vh] backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
    >
      <div className="w-full max-w-lg overflow-hidden rounded-[var(--radius)] border border-border bg-card shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border px-3 py-3">
          <Search className="h-4 w-4 text-text-tertiary" />
          <input
            ref={inputRef}
            aria-label={t.cockpit?.search ?? "Search"}
            className="flex-1 bg-transparent text-sm text-midground placeholder:text-text-tertiary focus:outline-none"
            onChange={(e) => setQuery(e.target.value)}
            placeholder={
              t.cockpit?.openCommandPalette ?? "Search commands, pages, sessions…"
            }
            type="text"
            value={query}
          />
          <span className="hidden text-xs text-text-tertiary sm:inline">
            ESC
          </span>
        </div>

        <div
          ref={listRef}
          aria-live="polite"
          className="max-h-[60vh] overflow-y-auto p-2"
        >
          {filtered.length === 0 ? (
            <div className="px-3 py-8 text-center text-sm text-text-secondary">
              {t.common.noResults} {query && `for "${query}"`}
            </div>
          ) : (
            Array.from(grouped.entries()).map(([group, items]) => (
              <div key={group} className="mb-2">
                <div className="sticky top-0 bg-card px-3 py-1 text-[11px] font-medium uppercase tracking-[0.1em] text-text-tertiary">
                  {group}
                </div>
                <ul className="flex flex-col">
                  {items.map((item) => {
                    const absoluteIndex = filtered.findIndex(
                      (r) => r.id === item.id,
                    );
                    const isSelected = absoluteIndex === selectedIndex;
                    const Icon = item.icon ?? FileText;
                    return (
                      <li key={item.id}>
                        <button
                          className={cn(
                            "flex w-full items-center gap-3 rounded-[var(--radius)] px-3 py-2 text-left text-sm transition-colors",
                            isSelected
                              ? "bg-midground/10 text-midground"
                              : "text-text-secondary hover:bg-midground/5 hover:text-midground",
                          )}
                          data-selected={isSelected}
                          onClick={item.onSelect}
                          onMouseEnter={() => setSelectedIndex(absoluteIndex)}
                          type="button"
                        >
                          <Icon className="h-3.5 w-3.5 shrink-0 text-text-tertiary" />
                          <span className="min-w-0 flex-1 truncate">
                            {item.label}
                          </span>
                          {item.shortcut && (
                            <kbd className="hidden rounded border border-border px-1.5 py-0.5 text-[10px] text-text-tertiary sm:inline">
                              {item.shortcut}
                            </kbd>
                          )}
                          {isSelected && (
                            <ArrowRight className="h-3.5 w-3.5 text-text-tertiary" />
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))
          )}
        </div>

        <div className="flex items-center justify-between border-t border-border px-3 py-2 text-[11px] text-text-tertiary">
          <span>
            ⌘K {t.cockpit?.openCommandPalette ?? "to open"} · ↑↓ {t.common.search}
          </span>
          <span>{filtered.length} results</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
