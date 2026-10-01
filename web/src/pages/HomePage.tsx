import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ComponentType,
} from "react";
import { useNavigate } from "react-router";
import {
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  CheckCircle2,
  Cpu,
  FileText,
  LayoutDashboard,
  MessageSquare,
  Package,
  Plug,
  Puzzle,
  Radio,
  RefreshCw,
  Settings,
  Terminal,
  Wrench,
  XCircle,
} from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { cn, timeAgo } from "@/lib/utils";
import { api } from "@/lib/api";
import type {
  AnalyticsResponse,
  CronJob,
  MessagingPlatform,
  SessionInfo,
  SkillInfo,
  StatusResponse,
  SystemStats,
} from "@/lib/api";
import { useI18n } from "@/i18n";
import { gatewayLine } from "@/components/SidebarStatusStrip";
import { useSystemActions } from "@/contexts/useSystemActions";
import { useProfileScope } from "@/contexts/useProfileScope";
import { PluginSlot, usePlugins } from "@/plugins";
import type { PluginManifest } from "@/plugins";
import { useTheme } from "@/themes";

const POLL_MS = 30_000;

export default function HomePage() {
  const { t } = useI18n();
  const { profile } = useProfileScope();
  const { theme } = useTheme();
  const { manifests } = usePlugins();
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [analytics, setAnalytics] = useState<AnalyticsResponse | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [cronJobs, setCronJobs] = useState<CronJob[] | null>(null);
  const [skills, setSkills] = useState<SkillInfo[] | null>(null);
  const [platforms, setPlatforms] = useState<MessagingPlatform[] | null>(null);
  const [systemStats, setSystemStats] = useState<SystemStats | null>(null);
  const [latestError, setLatestError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [
        statusRes,
        analyticsRes,
        sessionsRes,
        cronRes,
        skillsRes,
        platformsRes,
        statsRes,
        logsRes,
      ] = await Promise.allSettled([
        api.getStatus(),
        api.getAnalytics(1),
        api.getSessions(6, 0, profile || undefined, "recent"),
        api.getCronJobs("all"),
        api.getSkills(profile || undefined),
        api.getMessagingPlatforms(),
        api.getSystemStats(),
        api.getLogs({ lines: 1, level: "ERROR" }),
      ]);

      if (statusRes.status === "fulfilled") setStatus(statusRes.value);
      if (analyticsRes.status === "fulfilled") setAnalytics(analyticsRes.value);
      if (sessionsRes.status === "fulfilled") setSessions(sessionsRes.value.sessions);
      if (cronRes.status === "fulfilled") setCronJobs(cronRes.value);
      if (skillsRes.status === "fulfilled") setSkills(skillsRes.value);
      if (platformsRes.status === "fulfilled")
        setPlatforms(platformsRes.value.platforms);
      if (statsRes.status === "fulfilled") setSystemStats(statsRes.value);
      if (logsRes.status === "fulfilled" && logsRes.value.lines.length > 0) {
        setLatestError(logsRes.value.lines[0]);
      }

      const firstFailure = [
        statusRes,
        analyticsRes,
        sessionsRes,
        cronRes,
        skillsRes,
        platformsRes,
        statsRes,
      ].find((r) => r.status === "rejected") as
        | PromiseSettledResult<unknown>
        | undefined;
      if (firstFailure?.status === "rejected") {
        setError(
          firstFailure.reason instanceof Error
            ? firstFailure.reason.message
            : String(firstFailure.reason),
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [profile]);

  useEffect(() => {
    document.title = "Cockpit | Hermes Agent";
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const density = theme.layout.density ?? "comfortable";
  const gapClass = density === "compact" ? "gap-3" : density === "spacious" ? "gap-5" : "gap-4";

  return (
    <div className="mx-auto w-full max-w-[1440px] px-4 sm:px-6 lg:px-8">
      <header className="mb-4 flex items-center justify-between sm:mb-6">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-[-0.01em] text-midground">
          <LayoutDashboard className="h-5 w-5" />
          {t.cockpit?.title ?? "Cockpit"}
        </h1>
        <div className="flex items-center gap-2">
          <PluginSlot name="header-right" />
        </div>
      </header>

      {error && (
        <div
          className={cn(
            "mb-4 flex items-start gap-3 rounded-[var(--radius)] border border-l-[3px] border-warning/20 border-l-warning bg-card p-4",
          )}
          role="alert"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
          <div className="flex-1">
            <p className="text-sm text-midground">
              {t.cockpit?.loadingError ?? "Unable to load cockpit data."}
            </p>
            <p className="mt-1 text-xs text-text-secondary">{error}</p>
          </div>
          <Button outlined size="sm" onClick={() => void load()}>
            {t.common.retry}
          </Button>
        </div>
      )}

      <div className={cn("grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4", gapClass)}>
        <MetricTile
          icon={MessageSquare}
          label={t.cockpit?.activeSessions ?? "Active Sessions"}
          loading={loading && status === null}
          onClick="/sessions"
          value={status?.active_sessions ?? 0}
          valueLabel={t.cockpit?.active ?? "active"}
        />
        <GatewayMetricTile
          loading={loading && status === null}
          status={status}
        />
        <MetricTile
          icon={BarChart3}
          label={t.cockpit?.tokensToday ?? "Tokens Today"}
          loading={loading && analytics === null}
          onClick="/analytics"
          value={compactNumber(
            (analytics?.totals.total_input ?? 0) +
              (analytics?.totals.total_output ?? 0),
          )}
          valueLabel={
            analytics
              ? `${compactNumber(analytics.totals.total_input)} in / ${compactNumber(analytics.totals.total_output)} out`
              : undefined
          }
        />
        <MetricTile
          icon={BarChart3}
          label={t.cockpit?.costToday ?? "Cost Today"}
          loading={loading && analytics === null}
          onClick="/analytics"
          value={`$${(analytics?.totals.total_estimated_cost ?? 0).toFixed(2)}`}
          valueLabel={t.cockpit?.today ?? "today"}
        />
      </div>

      <div
        className={cn(
          "mt-4 grid grid-cols-1 lg:grid-cols-2",
          gapClass,
          density === "compact" ? "mt-3" : density === "spacious" ? "mt-5" : "mt-4",
        )}
      >
        <RecentActivityCard loading={loading} sessions={sessions} />
        <QuickActionsCard />
      </div>

      <div
        className={cn(
          "mt-4 grid grid-cols-1 lg:grid-cols-2",
          gapClass,
          density === "compact" ? "mt-3" : density === "spacious" ? "mt-5" : "mt-4",
        )}
      >
        <SystemHealthCard
          latestError={latestError}
          loading={loading}
          platforms={platforms}
          stats={systemStats}
        />
        <CronSchedulesCard jobs={cronJobs} loading={loading} />
      </div>

      <div
        className={cn(
          "mt-4",
          density === "compact" ? "mt-3" : density === "spacious" ? "mt-5" : "mt-4",
        )}
      >
        <SkillsPluginsCard manifests={manifests} skills={skills} />
      </div>

      <PluginSlot name="cockpit-metric-row" />
    </div>
  );
}

function MetricTile({
  icon: Icon,
  label,
  loading,
  onClick,
  value,
  valueLabel,
}: MetricTileProps) {
  const navigate = useNavigate();
  return (
    <article
      aria-label={label}
      className={cn(
        "group relative flex flex-col justify-between rounded-[var(--radius)] bg-midground/5 p-4 transition-colors hover:bg-midground/[0.07] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40",
        onClick && "cursor-pointer",
      )}
      onClick={onClick ? () => navigate(onClick) : undefined}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === "Enter") navigate(onClick);
            }
          : undefined
      }
      tabIndex={onClick ? 0 : undefined}
    >
      {loading ? (
        <>
          <div className="h-3 w-16 animate-pulse rounded-sm bg-midground/10" />
          <div className="mt-3 h-7 w-24 animate-pulse rounded-sm bg-midground/10" />
        </>
      ) : (
        <>
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-[0.1em] text-text-tertiary">
              {label}
            </span>
            <Icon className="h-3.5 w-3.5 text-text-tertiary" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-xl font-semibold tracking-[-0.01em] text-midground">
              {value}
            </span>
            {valueLabel && (
              <span className="text-xs text-text-secondary">{valueLabel}</span>
            )}
          </div>
          {onClick && (
            <ArrowUpRight className="absolute top-3 right-3 h-3.5 w-3.5 text-text-tertiary opacity-0 transition-opacity group-hover:opacity-100" />
          )}
        </>
      )}
    </article>
  );
}

function GatewayMetricTile({
  loading,
  status,
}: {
  loading: boolean;
  status: StatusResponse | null;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const gw = status ? gatewayLine(status, t) : null;
  const StatusIcon =
    gw?.tone === "text-destructive"
      ? XCircle
      : gw?.tone === "text-warning"
        ? AlertTriangle
        : CheckCircle2;

  return (
    <article
      aria-label={t.cockpit?.gatewayStatus ?? "Gateway Status"}
      className="group relative flex cursor-pointer flex-col justify-between rounded-[var(--radius)] bg-midground/5 p-4 transition-colors hover:bg-midground/[0.07] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40"
      onClick={() => navigate("/system")}
      onKeyDown={(e) => {
        if (e.key === "Enter") navigate("/system");
      }}
      tabIndex={0}
    >
      {loading ? (
        <>
          <div className="h-3 w-20 animate-pulse rounded-sm bg-midground/10" />
          <div className="mt-3 h-7 w-28 animate-pulse rounded-sm bg-midground/10" />
        </>
      ) : (
        <>
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-medium uppercase tracking-[0.1em] text-text-tertiary">
              {t.cockpit?.gatewayStatus ?? "Gateway Status"}
            </span>
            <StatusIcon
              className={cn(
                "h-3.5 w-3.5",
                gw?.tone ?? "text-text-tertiary",
              )}
            />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-xl font-semibold tracking-[-0.01em] text-midground">
              {gw?.label ?? t.common.unknown}
            </span>
            {status?.gateway_updated_at && (
              <span className="text-xs text-text-secondary">
                {t.cockpit?.since ?? "since"}{" "}
                {timeAgo(new Date(status.gateway_updated_at).getTime() / 1000)}
              </span>
            )}
          </div>
          <ArrowUpRight className="absolute top-3 right-3 h-3.5 w-3.5 text-text-tertiary opacity-0 transition-opacity group-hover:opacity-100" />
        </>
      )}
    </article>
  );
}

function RecentActivityCard({
  loading,
  sessions,
}: {
  loading: boolean;
  sessions: SessionInfo[] | null;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const density = useTheme().theme.layout.density ?? "comfortable";

  return (
    <section className="surface-card flex flex-col rounded-[var(--radius)] border border-border bg-background-base p-4 sm:p-5">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-medium tracking-[0.01em] text-midground">
        <MessageSquare className="h-4 w-4" />
        {t.cockpit?.recentActivity ?? "Recent Activity"}
      </h2>

      {loading && sessions === null ? (
        <div className="space-y-3">
          <div className="h-10 animate-pulse rounded-sm bg-midground/10" />
          <div className="h-10 animate-pulse rounded-sm bg-midground/10" />
          <div className="h-10 animate-pulse rounded-sm bg-midground/10" />
        </div>
      ) : sessions && sessions.length > 0 ? (
        <ul
          className={cn(
            "-mx-2 flex flex-col overflow-y-auto pr-1",
            density === "compact"
              ? "max-h-[240px]"
              : density === "spacious"
                ? "max-h-[320px]"
                : "max-h-[280px]",
          )}
        >
          {sessions.map((session) => (
            <li key={session.id}>
              <button
                className="w-full rounded-[var(--radius)] px-2 py-2 text-left transition-colors hover:bg-midground/5 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-midground/40"
                onClick={() => navigate(`/sessions?id=${session.id}`)}
                type="button"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium text-midground">
                    {session.title || t.sessions.untitledSession}
                  </span>
                  <span className="shrink-0 text-[11px] text-text-tertiary">
                    {timeAgo(session.last_active)}
                  </span>
                </div>
                <p className="mt-0.5 truncate text-xs text-text-secondary">
                  {session.preview || t.cockpit?.noRecentActivity}
                </p>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center py-6 text-center">
          <MessageSquare className="mb-2 h-8 w-8 text-text-tertiary" />
          <p className="text-sm text-text-secondary">
            {t.cockpit?.noRecentActivity ?? "No recent activity"}
          </p>
          <Button
            className="mt-3"
            onClick={() => navigate("/chat")}
            size="sm"
            outlined
          >
            {t.cockpit?.startFirstConversation ?? "Start your first conversation"}
          </Button>
        </div>
      )}
    </section>
  );
}

function QuickActionsCard() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { isBusy, runAction } = useSystemActions();
  const [restartConfirm, setRestartConfirm] = useState(false);

  const actions: QuickAction[] = [
    { icon: Terminal, label: t.cockpit?.openChat ?? "Open Chat", path: "/chat" },
    {
      icon: MessageSquare,
      label: t.cockpit?.newSession ?? "New Session",
      path: "/chat",
    },
    { icon: FileText, label: t.cockpit?.checkLogs ?? "Check Logs", path: "/logs" },
    {
      icon: RefreshCw,
      label: t.cockpit?.restartGateway ?? "Restart Gateway",
      onClick: () => setRestartConfirm(true),
    },
    {
      icon: Package,
      label: t.cockpit?.browseSkills ?? "Browse Skills",
      path: "/skills",
    },
    { icon: Settings, label: t.cockpit?.viewConfig ?? "View Config", path: "/config" },
  ];

  const handleRestart = async () => {
    setRestartConfirm(false);
    await runAction("restart");
  };

  return (
    <section className="surface-card flex flex-col rounded-[var(--radius)] border border-border bg-background-base p-4 sm:p-5">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-medium tracking-[0.01em] text-midground">
        <LayoutDashboard className="h-4 w-4" />
        {t.cockpit?.quickActions ?? "Quick Actions"}
      </h2>

      <div className="grid flex-1 grid-cols-2 gap-2">
        {actions.map((action) => (
          <Button
            disabled={action.label === t.cockpit?.restartGateway && isBusy}
            key={action.label}
            onClick={() =>
              action.onClick
                ? action.onClick()
                : action.path && navigate(action.path)
            }
            outlined
            size="sm"
          >
            <action.icon className="mr-2 h-3.5 w-3.5" />
            {action.label}
          </Button>
        ))}
      </div>

      {restartConfirm && (
        <RestartConfirmDialog
          onCancel={() => setRestartConfirm(false)}
          onConfirm={handleRestart}
        />
      )}
    </section>
  );
}

function RestartConfirmDialog({
  onCancel,
  onConfirm,
}: {
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div
        className="w-full max-w-md rounded-[var(--radius)] border border-border bg-card p-5 shadow-lg"
        role="alertdialog"
      >
        <h3 className="text-lg font-medium text-midground">
          {t.status.restartGatewayConfirmTitle}
        </h3>
        <p className="mt-2 text-sm text-text-secondary">
          {t.status.restartGatewayConfirmMessage}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button outlined onClick={onCancel}>
            {t.common.cancel}
          </Button>
          <Button onClick={onConfirm}>{t.common.confirm}</Button>
        </div>
      </div>
    </div>
  );
}

function SystemHealthCard({
  latestError,
  loading,
  platforms,
  stats,
}: {
  latestError: string | null;
  loading: boolean;
  platforms: MessagingPlatform[] | null;
  stats: SystemStats | null;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();

  const healthyChannels =
    platforms?.filter((p) => p.state === "connected" || p.gateway_running).length ?? 0;
  const totalChannels = platforms?.length ?? 0;
  const hasError = totalChannels > 0 && healthyChannels < totalChannels;

  return (
    <section
      className={cn(
        "surface-card flex flex-col rounded-[var(--radius)] border border-border bg-background-base p-4 sm:p-5",
        hasError && "border-l-[3px] border-l-warning",
      )}
    >
      <h2 className="mb-3 flex items-center gap-2 text-sm font-medium tracking-[0.01em] text-midground">
        <Wrench className="h-4 w-4" />
        {t.cockpit?.systemHealth ?? "System Health"}
      </h2>

      {loading && platforms === null ? (
        <div className="space-y-3">
          <div className="h-8 animate-pulse rounded-sm bg-midground/10" />
          <div className="h-8 animate-pulse rounded-sm bg-midground/10" />
        </div>
      ) : (
        <div className="flex flex-1 flex-col justify-center gap-3">
          <button
            className="flex items-center justify-between rounded-[var(--radius)] px-2 py-1.5 text-left transition-colors hover:bg-midground/5"
            onClick={() => navigate("/channels")}
            type="button"
          >
            <span className="text-sm text-text-secondary">
              {t.cockpit?.channelsHealthy
                ? t.cockpit.channelsHealthy.replace("{healthy}", String(healthyChannels)).replace("{total}", String(totalChannels))
                : `${healthyChannels}/${totalChannels} healthy`}
            </span>
            <Radio className="h-3.5 w-3.5 text-text-tertiary" />
          </button>

          <button
            className="flex items-center justify-between rounded-[var(--radius)] px-2 py-1.5 text-left transition-colors hover:bg-midground/5"
            onClick={() => navigate("/system")}
            type="button"
          >
            <span className="text-sm text-text-secondary">
              {t.cockpit?.cpuCores
                ? t.cockpit.cpuCores.replace("{count}", String(stats?.cpu_count ?? "—"))
                : `${stats?.cpu_count ?? "—"} cores`}
            </span>
            <Cpu className="h-3.5 w-3.5 text-text-tertiary" />
          </button>

          {stats?.memory && (
            <div className="px-2">
              <div className="mb-1 flex justify-between text-xs text-text-secondary">
                <span>Memory</span>
                <span>{stats.memory.percent}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-midground/10">
                <div
                  className={cn(
                    "h-full rounded-full",
                    stats.memory.percent >= 90
                      ? "bg-destructive"
                      : stats.memory.percent >= 75
                        ? "bg-warning"
                        : "bg-success",
                  )}
                  style={{ width: `${Math.min(stats.memory.percent, 100)}%` }}
                />
              </div>
            </div>
          )}

          {latestError && (
            <button
              className="flex flex-col gap-1 rounded-[var(--radius)] border-l-[3px] border-l-destructive px-2 py-1.5 text-left transition-colors hover:bg-midground/5"
              onClick={() => navigate("/logs")}
              type="button"
            >
              <span className="text-[11px] uppercase tracking-[0.1em] text-text-tertiary">
                {t.cockpit?.latestError ?? "Latest error"}
              </span>
              <span className="line-clamp-2 text-xs text-destructive">{latestError}</span>
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function CronSchedulesCard({
  jobs,
  loading,
}: {
  jobs: CronJob[] | null;
  loading: boolean;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();

  const upcoming = useMemo(() => {
    if (!jobs) return [];
    return jobs
      .filter((j) => j.enabled && j.next_run_at)
      .slice(0, 3)
      .sort(
        (a, b) =>
          new Date(a.next_run_at!).getTime() - new Date(b.next_run_at!).getTime(),
      );
  }, [jobs]);

  return (
    <section className="surface-card flex flex-col rounded-[var(--radius)] border border-border bg-background-base p-4 sm:p-5">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-medium tracking-[0.01em] text-midground">
        <RefreshCw className="h-4 w-4" />
        {t.cockpit?.cronSchedules ?? "Cron & Schedules"}
      </h2>

      {loading && jobs === null ? (
        <div className="space-y-3">
          <div className="h-8 animate-pulse rounded-sm bg-midground/10" />
          <div className="h-8 animate-pulse rounded-sm bg-midground/10" />
        </div>
      ) : upcoming.length > 0 ? (
        <ul className="flex flex-1 flex-col justify-center gap-2">
          {upcoming.map((job) => (
            <li key={job.id}>
              <button
                className="flex w-full items-center justify-between rounded-[var(--radius)] px-2 py-1.5 text-left transition-colors hover:bg-midground/5"
                onClick={() => navigate("/cron")}
                type="button"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-midground">
                    {job.name || t.common.untitled}
                  </p>
                  <p className="truncate text-xs text-text-secondary">
                    {job.schedule_display || job.schedule?.display || job.schedule?.expr}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {job.state === "running" ? (
                    <>
                      <Spinner className="h-3 w-3 text-text-tertiary" />
                      <span className="text-xs text-text-secondary">
                        {t.cockpit?.running ?? "Running"}
                      </span>
                    </>
                  ) : (
                    <span className="text-xs text-text-secondary">
                      {new Date(job.next_run_at!) < new Date()
                        ? `${t.cockpit?.overdue ?? "Overdue since"} ${timeAgo(
                            new Date(job.next_run_at!).getTime() / 1000,
                          )}`
                        : `${t.cockpit?.next ?? "Next"} ${timeAgo(
                            new Date(job.next_run_at!).getTime() / 1000,
                          )}`}
                    </span>
                  )}
                </div>
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="flex flex-1 items-center text-sm text-text-secondary">
          {t.cron.noJobs}
        </p>
      )}
    </section>
  );
}

function SkillsPluginsCard({
  manifests,
  skills,
}: {
  manifests: PluginManifest[];
  skills: SkillInfo[] | null;
}) {
  const { t } = useI18n();
  const navigate = useNavigate();

  const recentSkills = useMemo(() => {
    if (!skills) return [];
    return skills.slice(0, 6);
  }, [skills]);

  const recentPlugins = useMemo(() => {
    return manifests.filter((m) => !m.tab.hidden).slice(0, 4);
  }, [manifests]);

  if (recentSkills.length === 0 && recentPlugins.length === 0) return null;

  return (
    <section className="surface-card rounded-[var(--radius)] border border-border bg-background-base p-4 sm:p-5">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-medium tracking-[0.01em] text-midground">
        <Puzzle className="h-4 w-4" />
        {t.cockpit?.skillsPlugins ?? "Skills & Plugins"}
      </h2>

      <div className="flex flex-wrap gap-2">
        {recentSkills.map((skill) => (
          <button
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs text-text-secondary transition-colors hover:bg-midground/5 hover:text-midground"
            key={skill.name}
            onClick={() => navigate("/skills")}
            type="button"
          >
            <Package className="h-3 w-3" />
            {skill.name}
          </button>
        ))}
        {recentPlugins.map((manifest) => (
          <button
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1.5 text-xs text-text-secondary transition-colors hover:bg-midground/5 hover:text-midground"
            key={manifest.name}
            onClick={() => navigate(manifest.tab.path)}
            type="button"
          >
            <Plug className="h-3 w-3" />
            {manifest.label}
          </button>
        ))}
      </div>
    </section>
  );
}

function compactNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

interface MetricTileProps {
  icon: ComponentType<{ className?: string }>;
  label: string;
  loading: boolean;
  onClick?: string;
  value: number | string;
  valueLabel?: string;
}

interface QuickAction {
  icon: ComponentType<{ className?: string }>;
  label: string;
  path?: string;
  onClick?: () => void;
}
