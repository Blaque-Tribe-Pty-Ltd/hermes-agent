import type { ComponentType } from "react";
import {
  BarChart3,
  BookOpen,
  Brain,
  Clock,
  Cpu,
  Database,
  FileText,
  FolderOpen,
  Globe,
  Home,
  KeyRound,
  Laptop,
  MessageSquare,
  Package,
  Plug,
  Puzzle,
  Radio,
  Settings,
  ShieldCheck,
  Terminal,
  Users,
  Webhook,
  Wrench,
} from "lucide-react";
import type { PluginManifest } from "@/plugins";

/** Single navigation entry rendered as a sidebar link or command-palette result. */
export interface NavItem {
  path: string;
  label: string;
  labelKey?: string;
  icon?: ComponentType<{ className?: string }>;
  /** Optional shortcut badge shown in the command palette (e.g. "⌘1"). */
  shortcut?: string;
}

/** A collapsible sidebar bucket. */
export interface NavSection {
  id: string;
  labelKey: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  items: NavItem[];
}

export const HOME_NAV_ITEM: NavItem = {
  path: "/",
  labelKey: "home",
  label: "Home",
  icon: Home,
};

export const CHAT_NAV_ITEM: NavItem = {
  path: "/chat",
  labelKey: "chat",
  label: "Chat",
  icon: Terminal,
};

/** Built-in routes organised by operator intent (Home, MoonPie, Knowledge, System, Network, Reference). */
export const BUILTIN_NAV_SECTIONS: NavSection[] = [
  {
    id: "moonpie",
    labelKey: "moonpie",
    label: "MoonPie",
    icon: MessageSquare,
    items: [
      CHAT_NAV_ITEM,
      {
        path: "/sessions",
        labelKey: "sessions",
        label: "Sessions",
        icon: MessageSquare,
      },
    ],
  },
  {
    id: "knowledge",
    labelKey: "knowledge",
    label: "Knowledge",
    icon: Database,
    items: [
      { path: "/files", labelKey: "files", label: "Files", icon: FolderOpen },
      { path: "/skills", labelKey: "skills", label: "Skills", icon: Package },
      { path: "/plugins", labelKey: "plugins", label: "Plugins", icon: Puzzle },
      { path: "/mcp", label: "MCP", icon: Plug },
    ],
  },
  {
    id: "system",
    labelKey: "system",
    label: "System",
    icon: Cpu,
    items: [
      {
        path: "/analytics",
        labelKey: "analytics",
        label: "Analytics",
        icon: BarChart3,
      },
      { path: "/models", labelKey: "models", label: "Models", icon: Brain },
      { path: "/cron", labelKey: "cron", label: "Cron", icon: Clock },
      { path: "/logs", labelKey: "logs", label: "Logs", icon: FileText },
      {
        path: "/profiles",
        labelKey: "profiles",
        label: "Profiles",
        icon: Users,
      },
      { path: "/config", labelKey: "config", label: "Config", icon: Settings },
      { path: "/env", labelKey: "keys", label: "Keys", icon: KeyRound },
      { path: "/system", labelKey: "diagnostics", label: "Diagnostics", icon: Wrench },
    ],
  },
  {
    id: "network",
    labelKey: "network",
    label: "Network",
    icon: Globe,
    items: [
      {
        path: "/channels",
        label: "Channels",
        icon: Radio,
      },
      {
        path: "/webhooks",
        label: "Webhooks",
        icon: Webhook,
      },
      {
        path: "/pairing",
        label: "Pairing",
        icon: ShieldCheck,
      },
      {
        path: "/devices",
        label: "Devices",
        icon: Laptop,
      },
    ],
  },
  {
    id: "reference",
    labelKey: "reference",
    label: "Reference",
    icon: BookOpen,
    items: [
      {
        path: "/docs",
        labelKey: "documentation",
        label: "Documentation",
        icon: BookOpen,
      },
    ],
  },
];

/** Flat list of every built-in nav item (used by the command palette and legacy helpers). */
export const ALL_BUILTIN_NAV_ITEMS: NavItem[] = [
  HOME_NAV_ITEM,
  ...BUILTIN_NAV_SECTIONS.flatMap((s) => s.items),
];

/** Map of icon names shipped by plugins back to Lucide components. */
export const ICON_MAP: Record<string, ComponentType<{ className?: string }>> = {
  BarChart3,
  BookOpen,
  Brain,
  Clock,
  Cpu,
  Database,
  FileText,
  FolderOpen,
  Globe,
  Home,
  KeyRound,
  Laptop,
  MessageSquare,
  Package,
  Plug,
  Puzzle,
  Radio,
  Settings,
  ShieldCheck,
  Terminal,
  Users,
  Webhook,
  Wrench,
};

export function resolveIcon(name: string): ComponentType<{ className?: string }> {
  return ICON_MAP[name] ?? Puzzle;
}

/** Resolve a plugin manifest's tab position into a concrete location.
 *
 *  Supported position syntax:
 *    - "end"                                    → append to the plugin section at the bottom
 *    - "after:<pathSegment>"                    → after a built-in item path (e.g. "after:skills")
 *    - "before:<pathSegment>"                   → before a built-in item path
 *    - "section:<sectionId>"                    → place inside the named section
 *    - "section:<sectionId>:after:<pathSegment>"→ place inside section after a child path
 */
export function parsePluginPosition(
  position: string | undefined,
): {
  sectionId?: string;
  afterPath?: string;
  beforePath?: string;
  end: boolean;
} {
  const pos = position ?? "end";
  if (pos === "end") return { end: true };
  if (pos.startsWith("before:")) {
    return { beforePath: "/" + pos.slice(7), end: false };
  }
  if (pos.startsWith("after:")) {
    return { afterPath: "/" + pos.slice(6), end: false };
  }
  if (pos.startsWith("section:")) {
    const rest = pos.slice(8);
    const afterIdx = rest.indexOf(":after:");
    if (afterIdx >= 0) {
      const sectionId = rest.slice(0, afterIdx);
      const afterPath = "/" + rest.slice(afterIdx + 7);
      return { sectionId, afterPath, end: false };
    }
    return { sectionId: rest, end: false };
  }
  return { end: true };
}

/** Insert plugin items into the grouped section structure.
 *
 *  Returns a new list of sections where each section may have plugin entries
 *  appended/placed inside it, plus an optional `pluginSection` for items that
 *  did not request a specific section.
 */
export function buildSectionedNav(
  sections: NavSection[],
  manifests: PluginManifest[],
): {
  sections: NavSection[];
  pluginSection: NavSection | null;
} {
  const sectionMap = new Map<string, NavSection>(
    sections.map((s) => [s.id, { ...s, items: [...s.items] }]),
  );
  const pluginItems: NavItem[] = [];

  for (const manifest of manifests) {
    if (manifest.tab.override) continue;
    if (manifest.tab.hidden) continue;

    const pluginItem: NavItem = {
      path: manifest.tab.path,
      label: manifest.label,
      icon: resolveIcon(manifest.icon),
    };

    const { sectionId, afterPath, beforePath, end } = parsePluginPosition(
      manifest.tab.position,
    );

    if (sectionId && sectionMap.has(sectionId)) {
      const section = sectionMap.get(sectionId)!;
      if (afterPath) {
        const idx = section.items.findIndex((i) => i.path === afterPath);
        section.items.splice(idx >= 0 ? idx + 1 : section.items.length, 0, pluginItem);
      } else if (beforePath) {
        const idx = section.items.findIndex((i) => i.path === beforePath);
        section.items.splice(idx >= 0 ? idx : section.items.length, 0, pluginItem);
      } else {
        section.items.push(pluginItem);
      }
    } else if (!sectionId && (afterPath || beforePath) && !end) {
      // Legacy after:/before: without a section — find the first section that
      // contains the target path and place it there; otherwise fall back to plugin section.
      let placed = false;
      for (const section of sectionMap.values()) {
        const target = afterPath ?? beforePath;
        const idx = section.items.findIndex((i) => i.path === target);
        if (idx >= 0) {
          section.items.splice(
            afterPath ? idx + 1 : idx,
            0,
            pluginItem,
          );
          placed = true;
          break;
        }
      }
      if (!placed) pluginItems.push(pluginItem);
    } else {
      pluginItems.push(pluginItem);
    }
  }

  const pluginSection: NavSection | null =
    pluginItems.length > 0
      ? {
          id: "plugins",
          labelKey: "plugins",
          label: "Plugins",
          icon: Puzzle,
          items: pluginItems,
        }
      : null;

  return { sections: Array.from(sectionMap.values()), pluginSection };
}

/** Flatten sectioned nav back to a simple list (preserves order). */
export function flattenSectionedNav(
  sectioned: ReturnType<typeof buildSectionedNav>,
): NavItem[] {
  const items = [...ALL_BUILTIN_NAV_ITEMS];
  for (const section of sectioned.sections) {
    // Built-in items already included; append any plugin items (those whose path
    // is not in the built-in flat list).
    const builtinPaths = new Set(ALL_BUILTIN_NAV_ITEMS.map((i) => i.path));
    for (const item of section.items) {
      if (!builtinPaths.has(item.path)) items.push(item);
    }
  }
  if (sectioned.pluginSection) {
    items.push(...sectioned.pluginSection.items);
  }
  return items;
}

/** Whether a pathname belongs to a given section. */
export function sectionContainsPath(section: NavSection, pathname: string): boolean {
  const normalized = pathname.replace(/\/$/, "") || "/";
  return section.items.some((item) => {
    if (item.path === "/") return normalized === "/";
    if (item.path === "/sessions") return normalized === item.path;
    return normalized === item.path || normalized.startsWith(item.path + "/");
  });
}
