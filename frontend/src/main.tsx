import React, {
  useEffect,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Bell,
  Check,
  CheckCircle2,
  ChevronRight,
  ClipboardList,
  Clock,
  Download,
  Factory,
  Layers3,
  LayoutDashboard,
  LogOut,
  Plus,
  RefreshCw,
  ScanLine,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  TriangleAlert,
  Upload,
  Users,
  Wrench,
  X,
  Camera,
  Printer,
  Radio,
  Workflow,
} from "lucide-react";
import type { IScannerControls } from "@zxing/browser";
import {
  api,
  ApiError,
  downloadTemplate,
  employeeLogin,
  eventStream,
  Row,
  setToken,
} from "./api";
import { Lang, translate } from "./i18n";
import "./style.css";
import { Context, useApp } from "./ctx";
import * as Mes from "./mes";
import { MultiPick } from "./multipick";

const fmt = (n: any) =>
  new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 }).format(
    Number(n || 0),
  );
const dt = (n: any) => (n ? new Date(n).toLocaleString() : "—");
const permissionMap: Record<string, string> = {
  split: "run_cards:manage",
  edit: "run_cards:manage",
  cancel: "run_cards:manage",
  rework: "rework:create",
  scrap: "quality:judge",
  release: "quality:judge",
  revoke: "run_cards:revoke_work",
  backfill: "work_records:backfill",
  "abnormal-end": "work_records:abnormal_end",
};
const actionLabels: Record<string, string> = {
  "check-in": "checkin",
  "check-out": "checkout",
  "abnormal-end": "abnormal",
};
function QRCode({ value }: { value: string }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    import("qrcode")
      .then((q) =>
        q.toDataURL(value, {
          width: 180,
          margin: 1,
          errorCorrectionLevel: "M",
        }),
      )
      .then(setUrl);
  }, [value]);
  return url ? (
    <img className="qr-code" src={url} width={90} height={90} alt={value} />
  ) : null;
}
function Badge({ value }: { value: string }) {
  const { t } = useApp();
  return (
    <span className={"badge " + value.toLowerCase()}>
      <i />
      {t(value)}
    </span>
  );
}
function Field({
  label,
  children,
  wide = false,
}: {
  label: string;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <label className={"field " + (wide ? "wide" : "")}>
      <span>{label}</span>
      {children}
    </label>
  );
}
function Empty() {
  const { t } = useApp();
  return (
    <div className="empty">
      <Layers3 size={28} />
      <span>{t("empty")}</span>
    </div>
  );
}
function Modal({
  title,
  children,
  onClose,
  footer,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
  footer?: React.ReactNode;
}) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <h2>{title}</h2>
          <button className="icon" onClick={onClose} aria-label="Close">
            <X />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </section>
    </div>
  );
}
function ErrorBox({ error }: { error: string }) {
  const ref = useRef<HTMLDivElement>(null);
  // Errors often appear at the top of a long form/dialog while the user is at the bottom (tablet): bring it into view.
  useEffect(() => {
    if (error) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [error]);
  return error ? (
    <div className="error" role="alert" ref={ref}>
      <TriangleAlert size={18} />
      {error}
    </div>
  ) : null;
}
function useCommand() {
  const { refresh, t, onAuthError } = useApp();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const receipt = useRef({ payload: "", key: "" });
  async function send(path: string, body: any, method = "POST") {
    if (!navigator.onLine) {
      setError(t("offline"));
      return null;
    }
    const fingerprint = JSON.stringify({ path, body, method });
    if (receipt.current.payload !== fingerprint)
      receipt.current = { payload: fingerprint, key: crypto.randomUUID() };
    setBusy(true);
    setError("");
    try {
      const result = await api(path, method, body, receipt.current.key);
      refresh();
      return result;
    } catch (e) {
      const err = e as ApiError;
      setError(err.code === "VERSION_CONFLICT" ? t("stale") : err.message);
      if (err.status === 401) onAuthError();
      return null;
    } finally {
      setBusy(false);
    }
  }
  return { send, busy, error };
}

function App() {
  const [lang, setLang] = useState<Lang>(
      (localStorage.getItem("mes-lang") as Lang) || "vi",
    ),
    [actor, setActor] = useState<Row | null>(null),
    [ready, setReady] = useState(false),
    [catalog, setCatalog] = useState<Row>({}),
    [revision, setRevision] = useState(0),
    [online, setOnline] = useState(navigator.onLine),
    [notice, setNotice] = useState("");
  const [mode, setMode] = useState<"admin" | "operator">(
    location.pathname.startsWith("/operator") ? "operator" : "admin",
  );
  const [page, setPage] = useState("overview"),
    [selected, setSelected] = useState<{ kind: string; id: string } | null>(
      null,
    ),
    [unread, setUnread] = useState(0);
  const t = (key: string) => translate(key, lang);
  const refresh = () => setRevision((n) => n + 1);
  const onAuthError = () => {
    setToken("");
    setActor(null);
    setNotice(t("sessionExpired"));
  };
  useEffect(() => {
    document.documentElement.lang = lang === "zh" ? "zh-Hant" : "vi";
    localStorage.setItem("mes-lang", lang);
  }, [lang]);
  useEffect(() => {
    api("/auth/me")
      .then((a) => {
        setActor(a);
        setMode(a.mode);
      })
      .catch(() => setToken(""))
      .finally(() => setReady(true));
    const on = () => setOnline(navigator.onLine);
    window.addEventListener("online", on);
    window.addEventListener("offline", on);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", on);
    };
  }, []);
  useEffect(() => {
    if (!actor) return;
    Promise.all([api("/catalog"), api("/notifications")])
      .then(([c, n]) => {
        setCatalog(c);
        setUnread(n.unread);
      })
      .catch((e) => {
        if (e.status === 401) onAuthError();
        else setNotice(e.message);
      });
  }, [actor, revision]);
  useEffect(() => {
    if (!actor) return;
    const ctl = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const connect = () => {
      eventStream(refresh, ctl.signal).then((status) => {
        if (ctl.signal.aborted) return;
        if (status === 401) return onAuthError(); // token hết hạn/bị thu hồi: dừng, không thử lại mãi
        timer = setTimeout(connect, 5000);
      });
    };
    connect();
    return () => {
      ctl.abort();
      clearTimeout(timer);
    };
  }, [actor]);
  const goto = (p: string) => {
    setPage(p);
    setSelected(null);
    setNotice("");
  };
  const logout = async () => {
    await api("/auth/logout", "POST", {}).catch(() => {});
    setToken("");
    setActor(null);
    setSelected(null);
  };
  const select = (kind: string, id: string) => setSelected({ kind, id });
  const value = {
    t,
    lang,
    actor,
    catalog,
    revision,
    refresh,
    select,
    goto,
    onAuthError,
    online,
  };
  const nav = [
    ["overview", LayoutDashboard],
    ["cards", Layers3],
    ["equipment", Factory],
    ["routes", Workflow],
    ["workorders", ClipboardList],
    ["schedule", Mes.icons.schedule],
    ["materials", Mes.icons.materials],
    ["trace", Mes.icons.trace],
    ["quality", Mes.icons.quality],
    ["reports", Mes.icons.reports],
    ["maintenance", Wrench],
    ["people", Mes.icons.people],
    ["documents", Mes.icons.documents],
    ["integration", Mes.icons.integration],
    ["records", ClipboardList],
    ["audit", ShieldCheck],
    ["notifications", Bell],
    ["sync", Radio],
    ["settings", Settings2],
  ] as const;
  return (
    <Context.Provider value={value}>
      {!ready ? (
        <div className="splash">
          RunCard <span>MES</span>
        </div>
      ) : !actor ? (
        <Login
          mode={mode}
          setMode={setMode}
          setActor={setActor}
          lang={lang}
          setLang={setLang}
          notice={notice}
        />
      ) : (
        <div className={"app " + (mode === "operator" ? "operator-app" : "")}>
          {mode === "admin" && (
            <aside className="sidebar">
              <a className="brand" href="/admin">
                <span className="brand-icon">
                  <Layers3 />
                </span>
                <strong>
                  RunCard<span>MES PLATFORM</span>
                </strong>
              </a>
              <div className="workspace">
                <span className="workspace-icon">D</span>
                <div>
                  DEMO FACTORY<small>PCBA · Line 01</small>
                </div>
                <ChevronRight size={15} />
              </div>
              <span className="nav-label">{t("brand")}</span>
              <nav>
                {nav
                  .filter(
                    ([id]) =>
                      id !== "audit" ||
                      actor.permissions.includes("audit:view_system"),
                  )
                  .filter(
                    ([id]) =>
                      id !== "sync" ||
                      actor.permissions.includes("mes_sync:manage"),
                  )
                  .map(([id, Icon]) => (
                    <button
                      key={id}
                      className={page === id && !selected ? "active" : ""}
                      onClick={() => goto(id)}
                    >
                      <Icon size={19} />
                      {t(id)}
                      {id === "notifications" && unread > 0 && <b>{unread}</b>}
                    </button>
                  ))}
              </nav>
              <div className="sidebar-bottom">
                <div className="system-status">
                  <i />
                  {t("online")}
                  <small>MES / v1.0</small>
                </div>
                <div className="profile">
                  <span className="avatar">
                    {actor.display_name.slice(0, 1).toUpperCase()}
                  </span>
                  <div>
                    {actor.display_name}
                    <small>{actor.role}</small>
                  </div>
                  <button className="icon" onClick={logout} title={t("logout")}>
                    <LogOut size={18} />
                  </button>
                </div>
              </div>
            </aside>
          )}
          <div className="main">
            <header className="topbar">
              <div>
                {mode === "operator" ? (
                  <span className="operator-brand">
                    <Layers3 /> RunCard <b>OPERATOR</b>
                  </span>
                ) : (
                  <>
                    <span className="muted">DEMO FACTORY</span>
                    <ChevronRight size={14} />
                    <strong>
                      {t(
                        selected
                          ? selected.kind === "runcard"
                            ? "cards"
                            : "equipment"
                          : page,
                      )}
                    </strong>
                  </>
                )}
              </div>
              <div className="top-actions">
                <span
                  className={"connection " + (online ? "" : "disconnected")}
                >
                  <i />
                  {t(online ? "online" : "offline")}
                </span>
                <button
                  className="lang"
                  onClick={() => setLang(lang === "vi" ? "zh" : "vi")}
                >
                  {lang === "vi" ? "繁體中文" : "Tiếng Việt"}
                </button>
                <button
                  className="icon notification-bell"
                  title={t("notifications")}
                  onClick={() => goto("notifications")}
                >
                  <Bell size={20} />
                  {unread > 0 && <i />}
                </button>
                {mode === "operator" && (
                  <button className="operator-user" onClick={logout}>
                    {actor.employee_no}
                    <LogOut size={16} />
                  </button>
                )}
              </div>
            </header>
            <main>
              {notice && <ErrorBox error={notice} />}{" "}
              {!online && <div className="warning">{t("offline")}</div>}
              {selected?.kind === "runcard" ? (
                <CardPage id={selected.id} back={() => setSelected(null)} />
              ) : selected?.kind === "equipment" ? (
                <EquipmentPage
                  id={selected.id}
                  back={() => setSelected(null)}
                />
              ) : page === "notifications" ? (
                <Notifications />
              ) : mode === "operator" ? (
                <ScanPage />
              ) : page === "overview" ? (
                <Dashboard />
              ) : page === "cards" ? (
                <Cards />
              ) : page === "equipment" ? (
                <EquipmentList />
              ) : page === "routes" ? (
                <Routes />
              ) : page === "settings" ? (
                <Settings />
              ) : Mes.pages[page] ? (
                React.createElement(Mes.pages[page])
              ) : page === "records" ? (
                <Resource resource="work-records" title="records" />
              ) : page === "audit" ? (
                <Resource resource="audit" title="audit" />
              ) : page === "sync" ? (
                <Resource resource="outbox" title="sync" />
              ) : (
                <Dashboard />
              )}
            </main>
            <div className="page-footer">
              <span>RUNCARD MES</span>
              <span>DEMO FACTORY · PCBA</span>
            </div>
          </div>
        </div>
      )}
    </Context.Provider>
  );
}
function Login({ mode, setMode, setActor, lang, setLang, notice }: any) {
  const { t } = useApp();
  const [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [employee, setEmployee] = useState(""),
    [device, setDevice] = useState(sessionStorage.getItem("mes-device") || ""),
    [reuse, setReuse] = useState(true),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result =
        mode === "admin"
          ? await api("/auth/login", "POST", { username, password })
          : await employeeLogin(employee.replace(/^EMP:/i, ""), device, reuse);
      setToken(result.token);
      setActor(result.actor);
      if (mode === "operator") {
        sessionStorage.setItem("mes-device", device);
        sessionStorage.setItem("mes-reuse", String(reuse));
      }
      history.replaceState({}, "", mode === "admin" ? "/admin" : "/operator");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login">
      <div className="login-story">
        <a className="brand" href="/">
          <span className="brand-icon">
            <Layers3 />
          </span>
          <strong>
            RunCard<span>MES PLATFORM</span>
          </strong>
        </a>
        <div>
          <span className="eyebrow">FACTORY INTELLIGENCE</span>
          <h1>
            {lang === "vi" ? (
              <>
                Mỗi công đoạn.
                <br />
                Một dòng chảy.
              </>
            ) : (
              <>
                每道工序，
                <br />
                清晰掌握。
              </>
            )}
          </h1>
          <p>
            {lang === "vi"
              ? "Theo dõi từng lô hàng, kết nối từng trạm và giữ mọi thao tác trong tầm kiểm soát."
              : "追蹤每個批次、串聯每個站點，讓每項作業清楚可追溯。"}
          </p>
          <div className="login-flow">
            <span>
              <ScanLine />
              01
            </span>
            <i />
            <span>
              <Factory />
              02
            </span>
            <i />
            <span>
              <CheckCircle2 />
              03
            </span>
          </div>
        </div>
        <small>DEMO FACTORY / PRODUCTION CONTROL</small>
      </div>
      <div className="login-panel">
        <button
          className="lang"
          onClick={() => setLang(lang === "vi" ? "zh" : "vi")}
        >
          {lang === "vi" ? "繁體中文" : "Tiếng Việt"}
        </button>
        <form onSubmit={submit}>
          <span className="eyebrow">WELCOME TO RUNCARD</span>
          <h2>{t("welcome")}</h2>
          <p className="muted">{t("loginHint")}</p>
          <div className="segmented">
            <button
              type="button"
              className={mode === "admin" ? "selected" : ""}
              onClick={() => setMode("admin")}
            >
              {t("admin")}
            </button>
            <button
              type="button"
              className={mode === "operator" ? "selected" : ""}
              onClick={() => setMode("operator")}
            >
              {t("operator")}
            </button>
          </div>
          <ErrorBox error={error || notice} />
          {mode === "admin" ? (
            <>
              <Field label={t("username")}>
                <input
                  autoFocus
                  autoComplete="username"
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </Field>
              <Field label={t("password")}>
                <input
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
            </>
          ) : (
            <>
              <Field label={t("employee")}>
                <input
                  autoFocus
                  required
                  value={employee}
                  onChange={(e) => setEmployee(e.target.value)}
                  placeholder="EMP:OP001"
                />
              </Field>
              <Field label={t("device")}>
                <input
                  type="password"
                  required
                  value={device}
                  onChange={(e) => setDevice(e.target.value)}
                  autoComplete="off"
                />
              </Field>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={reuse}
                  onChange={(e) => setReuse(e.target.checked)}
                />
                {t("session")}
              </label>
            </>
          )}
          <button className="primary login-submit" disabled={busy}>
            {t(busy ? "busy" : "login")}
            <ArrowRight size={18} />
          </button>
        </form>
      </div>
    </div>
  );
}
function Title({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="title-row">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      <div className="button-row">{children}</div>
    </div>
  );
}
function Dashboard() {
  const { t, revision, select, goto, lang } = useApp();
  const [data, setData] = useState<Row | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    api("/dashboard")
      .then(setData)
      .catch((e) => setError(e.message));
  }, [revision]);
  if (!data) return <ErrorBox error={error || t("loading")} />;
  const count = (s: string) =>
      Number(data.counts.find((c: Row) => c.status === s)?.cards || 0),
    qty = data.counts
      .filter(
        (c: Row) =>
          !["COMPLETED", "CANCELLED", "SCRAPPED", "TRANSFERRED"].includes(
            c.status,
          ),
      )
      .reduce((a: number, c: Row) => a + Number(c.qty), 0);
  const stations = Object.values(
    data.wip.reduce((a: Row, r: Row) => {
      a[r.id] ??= { ...r, qty: 0, cards: 0, waiting: 0, running: 0, hold: 0 };
      a[r.id].qty += Number(r.qty);
      a[r.id].cards += Number(r.cards);
      a[r.id][
        r.status === "WAITING"
          ? "waiting"
          : r.status === "IN_PROGRESS"
            ? "running"
            : "hold"
      ] += Number(r.cards);
      return a;
    }, {}),
  ) as Row[];
  const max = Math.max(1, ...stations.map((s) => s.cards));
  return (
    <>
      <Title
        title={t("overview")}
        subtitle={new Date().toLocaleDateString(
          lang === "vi" ? "vi-VN" : "zh-TW",
          { weekday: "long", day: "numeric", month: "long", year: "numeric" },
        )}
      >
        <span className="live-label">
          <i />
          {t("online")}
        </span>
      </Title>
      <div className="kpis">
        {[
          [t("wip"), fmt(qty), "pcs", Layers3, "green"],
          [t("waiting"), count("WAITING"), t("cards"), Clock, "amber"],
          [
            t("held"),
            count("QUALITY_HOLD") + count("DEFECT_HOLD") + count("ABNORMAL"),
            t("cards"),
            ShieldCheck,
            "red",
          ],
          [
            t("machines"),
            data.equipments.filter((e: Row) => e.state === "ENABLED").length,
            `/ ${data.equipments.length}`,
            Activity,
            "blue",
          ],
        ].map(([title, n, unit, Icon, color]: any) => (
          <div className="kpi" key={title}>
            <div>
              <span>{title}</span>
              <Icon size={19} className={color} />
            </div>
            <strong>
              {n}
              <small>{unit}</small>
            </strong>
            <div className="kpi-line">
              <i className={color} />
              <span>DEMO PCBA</span>
            </div>
          </div>
        ))}
      </div>
      <div className="dashboard-grid">
        <section className="panel wip-panel">
          <div className="panel-heading">
            <div>
              <h2>{t("byStation")}</h2>
              <p>{t("route")} · v1.0</p>
            </div>
            <button className="text-button" onClick={() => goto("cards")}>
              {t("viewAll")}
              <ArrowUpRight size={15} />
            </button>
          </div>
          <div className="legend">
            <span>
              <i className="legend-wait" />
              {t("waiting")}
            </span>
            <span>
              <i className="legend-run" />
              {t("running")}
            </span>
            <span>
              <i className="legend-hold" />
              {t("held")}
            </span>
          </div>
          <div className="station-chart">
            {stations.map((s, i) => (
              <div className="station-row" key={s.id}>
                <span className="station-index">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div className="station-name">
                  <strong>{s.code.replace("DEMO_", "")}</strong>
                  <small>{s.name.replace("DEMO ", "")}</small>
                </div>
                <div className="bar-track">
                  <div
                    className="bar waiting"
                    style={{ width: `${(s.waiting / max) * 100}%` }}
                  />
                  <div
                    className="bar running"
                    style={{ width: `${(s.running / max) * 100}%` }}
                  />
                  <div
                    className="bar held"
                    style={{ width: `${(s.hold / max) * 100}%` }}
                  />
                </div>
                <div className="station-qty">
                  <b>{fmt(s.qty)}</b>
                  <small>
                    {s.cards} {t("cards")}
                  </small>
                </div>
              </div>
            ))}
          </div>
        </section>
        <section className="panel daily-panel">
          <span className="eyebrow">PRODUCTION PULSE</span>
          <div className="pulse-icon">
            <Activity size={50} />
          </div>
          <h2>{t("running")}</h2>
          <strong className="big-number">
            {count("IN_PROGRESS")}
            <span>{t("cards")}</span>
          </strong>
          <div className="pulse-divider" />
          <div className="pulse-stat">
            <span>{t("COMPLETED")}</span>
            <b>{count("COMPLETED")}</b>
          </div>
          <div className="pulse-stat">
            <span>{t("maintenance")}</span>
            <b>
              {
                data.equipments.filter((e: Row) => e.state === "MAINTENANCE")
                  .length
              }
            </b>
          </div>
          <p>
            {lang === "vi"
              ? "Cập nhật tự động khi có thao tác sản xuất."
              : "生產作業異動時自動更新。"}
          </p>
        </section>
      </div>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>{t("equipmentOverview")}</h2>
            <p>DEMO FACTORY · LINE 01</p>
          </div>
          <button className="text-button" onClick={() => goto("equipment")}>
            {t("viewAll")}
            <ArrowUpRight size={15} />
          </button>
        </div>
        <div className="equipment-grid">
          {data.equipments.map((e: Row) => (
            <button
              className="equipment-tile"
              key={e.id}
              onClick={() => select("equipment", e.id)}
            >
              <div className="equipment-tile-top">
                <Factory size={22} />
                <Badge value={e.state} />
              </div>
              <h3>{e.mes_code}</h3>
              <p>{e.name.replace("DEMO ", "")}</p>
              <footer>
                <span>
                  <i className={Number(e.occupied) ? "dot green" : "dot"} />
                  {Number(e.occupied)
                    ? `${e.occupied} ${t("occupied")}`
                    : t("idle")}
                </span>
                <span>
                  {e.reason_code}
                  <ChevronRight size={14} />
                </span>
              </footer>
            </button>
          ))}
        </div>
      </section>
    </>
  );
}
function Cards() {
  const { t, revision, select, actor } = useApp();
  const [q, setQ] = useState(""),
    [status, setStatus] = useState(""),
    [source, setSource] = useState(""),
    [rows, setRows] = useState<Row[]>([]),
    [total, setTotal] = useState(0),
    [offset, setOffset] = useState(0),
    [error, setError] = useState(""),
    [modal, setModal] = useState("");
  useEffect(() => {
    const tm = setTimeout(
      () =>
        api(
          `/cards?q=${encodeURIComponent(q)}&status=${status}&source_type=${source}&offset=${offset}`,
        )
          .then((d) => {
            setRows(d.items);
            setTotal(d.total);
          })
          .catch((e) => setError(e.message)),
      150,
    );
    return () => clearTimeout(tm);
  }, [q, status, source, offset, revision]);
  return (
    <>
      <Title
        title={t("cards")}
        subtitle={`${total} ${t("cards")} · DEMO FACTORY`}
      >
        {actor.permissions.includes("run_cards:create") && (
          <>
            <button className="secondary" onClick={() => setModal("import")}>
              <Upload size={16} />
              {t("import")}
            </button>
            <button className="primary" onClick={() => setModal("create")}>
              <Plus size={17} />
              {t("newCard")}
            </button>
          </>
        )}
      </Title>
      <ErrorBox error={error} />
      <section className="panel">
        <div className="table-toolbar">
          <div className="search">
            <Search size={18} />
            <input
              placeholder={t("search")}
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setOffset(0);
              }}
            />
          </div>
          <select
            aria-label={t("filter")}
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">
              {t("all")} · {t("state")}
            </option>
            {[
              "WAITING",
              "IN_PROGRESS",
              "QUALITY_HOLD",
              "DEFECT_HOLD",
              "ABNORMAL",
              "COMPLETED",
              "SCRAPPED",
              "CANCELLED",
              "TRANSFERRED",
            ].map((s) => (
              <option key={s} value={s}>
                {t(s)}
              </option>
            ))}
          </select>
          <select
            aria-label={t("source")}
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">
              {t("all")} · {t("source")}
            </option>
            {["NORMAL", "SPLIT", "REWORK", "DEFECT"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {[
                  "cardNo",
                  "workOrder",
                  "item",
                  "state",
                  "operation",
                  "qty",
                  "actions",
                ].map((h) => (
                  <th key={h}>{t(h)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id}>
                  <td>
                    <button
                      className="card-link"
                      onClick={() => select("runcard", c.id)}
                    >
                      <span className="tiny-card">
                        <Layers3 size={15} />
                      </span>
                      {c.card_no}
                    </button>
                    <small>{c.source_type}</small>
                  </td>
                  <td>{c.work_order_no || "—"}</td>
                  <td>
                    <strong>{c.item_no || "—"}</strong>
                    <small>{c.item_name}</small>
                  </td>
                  <td>
                    <Badge value={c.status} />
                  </td>
                  <td>{c.operation_name?.replace("DEMO ", "") || "—"}</td>
                  <td className="numeric">
                    <b>{fmt(c.current_qty)}</b> / {fmt(c.original_qty)}
                    <small>{c.unit}</small>
                  </td>
                  <td>
                    <button
                      className="icon"
                      onClick={() => select("runcard", c.id)}
                      aria-label={t("actions")}
                    >
                      <ArrowUpRight size={18} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!rows.length && <Empty />}
        <div className="pagination">
          <span>
            {Math.min(offset + 1, total)}–
            {Math.min(offset + rows.length, total)} / {total}
          </span>
          <button
            className="secondary"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 100))}
          >
            {t("pagePrevious")}
          </button>
          <button
            className="secondary"
            disabled={offset + 100 >= total}
            onClick={() => setOffset(offset + 100)}
          >
            {t("pageNext")}
          </button>
        </div>
      </section>
      {modal === "create" && <CreateCard onClose={() => setModal("")} />}
      {modal === "import" && <ImportCards onClose={() => setModal("")} />}
    </>
  );
}
function CreateCard({ onClose }: { onClose: () => void }) {
  const { t, catalog } = useApp();
  const [data, setData] = useState<Row>({
    card_no: "",
    unit: "pcs",
    original_qty: "",
    route_free: false,
    route_version_id: "",
  });
  const { send, busy, error } = useCommand();
  const update = (key: string, value: any) =>
    setData({ ...data, [key]: value });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await send("/cards", {
      ...data,
      route_version_id: data.route_free ? null : Number(data.route_version_id),
    });
    if (r) onClose();
  };
  return (
    <Modal
      title={t("newCard")}
      onClose={onClose}
      footer={
        <button form="new-card" className="primary" disabled={busy}>
          {t(busy ? "busy" : "confirm")}
        </button>
      }
    >
      <form id="new-card" onSubmit={submit} className="form-grid">
        <ErrorBox error={error} />
        {["card_no", "work_order_no", "item_no", "item_name"].map((k, i) => (
          <Field key={k} label={t(["cardNo", "workOrder", "item", "name"][i])}>
            <input
              required={!i}
              value={data[k] || ""}
              onChange={(e) => update(k, e.target.value)}
            />
          </Field>
        ))}
        <Field label={t("qty")}>
          <input
            type="number"
            min="0.000001"
            step="any"
            required
            value={data.original_qty}
            onChange={(e) => update("original_qty", e.target.value)}
          />
        </Field>
        <Field label={t("unit")}>
          <input
            required
            value={data.unit}
            onChange={(e) => update("unit", e.target.value)}
          />
        </Field>
        <label className="checkbox wide">
          <input
            type="checkbox"
            checked={data.route_free}
            onChange={(e) => update("route_free", e.target.checked)}
          />
          {t("noRoute")}
        </label>
        {!data.route_free && (
          <Field label={t("route")} wide>
            <select
              required
              value={data.route_version_id}
              onChange={(e) => update("route_version_id", e.target.value)}
            >
              <option value="">{t("choose")}</option>
              {catalog.process_route_versions
                ?.filter((v: Row) => v.status === "ACTIVE")
                .map((v: Row) => (
                  <option key={v.id} value={v.id}>
                    {
                      catalog.process_routes.find(
                        (r: Row) => r.id === v.route_id,
                      )?.name
                    }{" "}
                    / {v.version}
                  </option>
                ))}
            </select>
          </Field>
        )}
      </form>
    </Modal>
  );
}
function ImportCards({ onClose }: { onClose: () => void }) {
  const { t } = useApp();
  const [file, setFile] = useState<File | null>(null),
    [valid, setValid] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const { refresh } = useApp();
  const key = useRef(crypto.randomUUID());
  const submit = async (validate: boolean) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      await api(
        "/import/cards?validate_only=" + validate,
        "POST",
        form,
        key.current,
      );
      if (validate) setValid(true);
      else {
        refresh();
        onClose();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={t("import")}
      onClose={onClose}
      footer={
        <button
          className="primary"
          disabled={!file || busy}
          onClick={() => submit(!valid)}
        >
          {t(busy ? "busy" : valid ? "confirm" : "validate")}
        </button>
      }
    >
      <button
        className="secondary"
        onClick={() => downloadTemplate().catch((e) => setError(e.message))}
      >
        <Download size={16} />
        {t("template")}
      </button>
      <Field label="XLSX · ≤ 2 MB">
        <input
          type="file"
          accept=".xlsx"
          onChange={(e) => {
            setFile(e.target.files?.[0] || null);
            setValid(false);
            key.current = crypto.randomUUID();
          }}
        />
      </Field>
      {valid && <p className="success">{t("importValid")}</p>}
      <ErrorBox error={error} />
    </Modal>
  );
}
function ScanPage() {
  const { t, select, actor, onAuthError } = useApp();
  const [code, setCode] = useState(""),
    [error, setError] = useState(""),
    [camera, setCamera] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const scan = async (value: string) => {
    setError("");
    try {
      const d = await api("/scan?code=" + encodeURIComponent(value));
      if (d.kind === "employee") {
        const device = sessionStorage.getItem("mes-device") || "";
        const s = await employeeLogin(
          d.value.employee_no,
          device,
          sessionStorage.getItem("mes-reuse") !== "false",
        );
        setToken(s.token);
        location.reload();
      } else if (d.kind === "operation")
        setError(`${d.value.name} · ${t("scanHint")}`);
      else select(d.kind, d.value.id);
      setCode("");
    } catch (e) {
      const err = e as ApiError;
      setError(err.message);
      if (err.status === 401) onAuthError();
    } finally {
      input.current?.focus();
    }
  };
  return (
    <div className="scan-page">
      <div className="operator-greeting">
        <span className="eyebrow">DEMO FACTORY · LINE 01</span>
        <span>
          <span className="dot green" />
          {actor.display_name} / {actor.employee_no}
        </span>
      </div>
      <div className="scan-hero">
        <div className="scan-icon">
          <ScanLine size={52} />
        </div>
        <h1>{t("scan")}</h1>
        <p>{t("scanHint")}</p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            scan(code);
          }}
          className="scanner-input"
        >
          <ScanLine />
          <input
            autoFocus
            ref={input}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder={t("scanPlaceholder")}
            aria-label={t("scan")}
            required
            autoComplete="off"
          />
          <button className="primary" aria-label={t("confirm")}>
            <ArrowRight />
          </button>
        </form>
        <ErrorBox error={error} />
        <button className="secondary" onClick={() => setCamera(true)}>
          <Camera size={20} />
          {t("camera")}
        </button>
      </div>
      <div className="scan-guide">
        <span>
          <Layers3 size={23} />
          <b>RC:</b> DEMO-RC-0001
        </span>
        <span>
          <Factory size={23} />
          <b>EQ:</b> DEMO-PRT01
        </span>
        <span>
          <Users size={23} />
          <b>EMP:</b> OP001
        </span>
      </div>
      {camera && (
        <CameraScan
          onClose={() => setCamera(false)}
          onScan={(v) => {
            setCamera(false);
            scan(v);
          }}
        />
      )}
    </div>
  );
}
function CameraScan({
  onClose,
  onScan,
}: {
  onClose: () => void;
  onScan: (s: string) => void;
}) {
  const { t } = useApp();
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let controls: IScannerControls | undefined;
    let disposed = false;
    import("@zxing/browser")
      .then(({ BrowserMultiFormatReader }) =>
        new BrowserMultiFormatReader().decodeFromVideoDevice(
          undefined,
          video.current!,
          (result) => {
            if (result && !disposed) {
              disposed = true;
              controls?.stop();
              onScan(result.getText());
            }
          },
        ),
      )
      .then((c) => {
        controls = c;
        if (disposed) c.stop();
      })
      .catch((e) => setError(e.message));
    return () => {
      disposed = true;
      controls?.stop();
    };
  }, []);
  return (
    <Modal title={t("camera")} onClose={onClose}>
      <video ref={video} className="camera-video" />
      <ErrorBox error={error} />
    </Modal>
  );
}
function CardPage({ id, back }: { id: string; back: () => void }) {
  const { t, revision, actor, select } = useApp();
  const [data, setData] = useState<Row | null>(null),
    [error, setError] = useState(""),
    [action, setAction] = useState("");
  useEffect(() => {
    api("/cards/" + id)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [id, revision]);
  if (!data) return <ErrorBox error={error || t("loading")} />;
  const c = data.card,
    admin = actor.mode === "admin";
  const choices: Record<string, string[]> = {
    WAITING: admin
      ? ["split", "rework", "temporary", "backfill", "revoke", "edit", "cancel"]
      : ["check-in", "temporary", "skip"],
    IN_PROGRESS: admin ? ["backfill", "revoke"] : ["check-out", "abnormal-end"],
    QUALITY_HOLD: admin ? ["release", "scrap", "rework", "revoke"] : [],
    ABNORMAL: admin ? ["release", "scrap", "rework", "revoke"] : [],
    DEFECT_HOLD: admin ? ["scrap", "rework"] : [],
  };
  const actions = (choices[c.status] || [])
    .filter(
      (k) => !permissionMap[k] || actor.permissions.includes(permissionMap[k]),
    )
    .filter(
      (k) => k !== "revoke" || data.records.some((r: Row) => !r.revoked_at),
    );
  return (
    <>
      <button className="back-link" onClick={back}>
        <ArrowLeft size={16} />
        {t("back")}
      </button>
      <Title
        title={c.card_no}
        subtitle={`${c.work_order_no || "—"} · ${c.item_name || c.item_no || "—"}`}
      >
        <Badge value={c.status} />
        <button className="secondary" onClick={() => window.print()}>
          <Printer size={16} />
          {t("print")}
        </button>
      </Title>
      <ErrorBox error={error} />
      <section className="panel card-summary">
        <QRCode value={"RC:" + c.card_no} />
        <div>
          <span>{t("current")}</span>
          <strong>
            {fmt(c.current_qty)}
            <small>
              {" "}
              / {fmt(c.original_qty)} {c.unit}
            </small>
          </strong>
        </div>
        <div>
          <span>{t("operation")}</span>
          <b>
            {data.nodes.find((n: Row) => n.id === c.current_node_id)?.name ||
              t("noRoute")}
          </b>
        </div>
        <div>
          <span>{t("routeVersion")}</span>
          <b>{data.route ? data.route.version : "—"}</b>
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <h2>{t("progress")}</h2>
          <span className="muted">
            {data.route ? data.route.name : t("noRoute")}
          </span>
        </div>
        <div className="route-timeline">
          {data.nodes.map((n: Row, i: number) => (
            <div
              key={n.id}
              className={n.id === c.current_node_id ? "current-node" : ""}
            >
              <span>
                {n.id === c.current_node_id ? <ArrowRight size={15} /> : i + 1}
              </span>
              <div>
                <b>{n.code.replace("DEMO_", "")}</b>
                <small>{n.name.replace("DEMO ", "")}</small>
                {c.abnormal_counts?.[n.id] > 0 && (
                  <small className="red">⚠ × {c.abnormal_counts[n.id]}</small>
                )}
              </div>
            </div>
          ))}
        </div>
      </section>
      <Mes.CardExtras card={c} />
      <section className="panel action-panel">
        <h2>{t("actions")}</h2>
        <div className="action-buttons">
          {actions.map((a, i) => (
            <button
              key={a}
              className={i === 0 ? "primary" : "secondary"}
              onClick={() => setAction(a)}
            >
              {t(actionLabels[a] || a)}
              <ArrowUpRight size={15} />
            </button>
          ))}
          {!actions.length && <p className="muted">{t("readonly")}</p>}
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <h2>{t("balance")}</h2>
          <ShieldCheck size={19} className="green" />
        </div>
        <div className="balance-grid">
          {[
            "current",
            "scrap",
            "rework",
            "split",
            "defect",
            "returned",
            "transferred",
          ].map((k) => (
            <div key={k}>
              <span>{t(k)}</span>
              <b>{fmt(c[k + "_qty"])}</b>
            </div>
          ))}
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <h2>{t("history")}</h2>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {[
                  "operation",
                  "equipment",
                  "employee",
                  "qty",
                  "state",
                  "history",
                ].map((k) => (
                  <th key={k}>{t(k)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.records.map((r: Row) => (
                <tr key={r.id} className={r.revoked_at ? "revoked" : ""}>
                  <td>
                    {r.operation_name}
                    {r.is_temporary && <small>{t("temporary")}</small>}
                  </td>
                  <td>{r.mes_code || "—"}</td>
                  <td>{r.operator_name}</td>
                  <td>
                    {fmt(r.input_qty)} →{" "}
                    {r.output_qty === null ? "—" : fmt(r.output_qty)}
                  </td>
                  <td>
                    {r.revoked_at ? t("revoke") : r.end_type ? t(r.end_type) : t("running")}
                  </td>
                  <td>
                    <small>{dt(r.started_at)}</small>
                    <small>{dt(r.ended_at)}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!data.records.length && <Empty />}
      </section>
      {data.children.length > 0 && (
        <section className="panel">
          <div className="panel-heading">
            <h2>{t("cards")}</h2>
          </div>
          {data.children.map((c: Row) => (
            <button
              key={c.id}
              className="child-card"
              onClick={() => select("runcard", c.id)}
            >
              <Layers3 size={18} />
              {c.card_no}
              <Badge value={c.status} />
              <span>
                {fmt(c.current_qty)} {c.unit}
              </span>
              <ChevronRight size={16} />
            </button>
          ))}
        </section>
      )}
      {action && (
        <ActionModal data={data} kind={action} onClose={() => setAction("")} />
      )}
    </>
  );
}
function ActionModal({
  data,
  kind,
  onClose,
}: {
  data: Row;
  kind: string;
  onClose: () => void;
}) {
  const { t, catalog, actor } = useApp();
  const [snapshot] = useState(data);
  const c = snapshot.card,
    opened = snapshot.records.find((r: Row) => !r.ended_at && !r.revoked_at);
  const start =
      ["check-in", "temporary", "skip"].includes(kind) ||
      (kind === "backfill" && c.status === "WAITING"),
    checkout =
      kind === "check-out" ||
      (kind === "backfill" && c.status === "IN_PROGRESS");
  const [form, setForm] = useState<Row>({
    version: c.version,
    output_qty: String(opened?.input_qty || c.current_qty),
    qty: "",
    reason: "",
    equipment_id: "",
    operation_id: "",
    target_node_id: "",
    operation_code: "",
    disposition: kind === "cancel" ? "scrap" : "keep",
    actual_operator_id: "",
    occurred_at: "",
    finish_route_free: false,
  });
  const [defects, setDefects] = useState<Row>({}),
    [qc, setQc] = useState<Row>({}),
    [scan, setScan] = useState(""),
    [scanError, setScanError] = useState(""),
    [camera, setCamera] = useState(false),
    [supervisor, setSupervisor] = useState({ username: "", password: "" });
  const { send, busy, error } = useCommand();
  const update = (k: string, v: any) => setForm((f: Row) => ({ ...f, [k]: v }));
  const selectedNode = snapshot.nodes.find(
    (n: Row) =>
      n.id ===
      Number(kind === "skip" ? form.target_node_id : c.current_node_id),
  );
  const operationId = checkout
    ? opened?.operation_id
    : kind === "temporary" || c.route_free
      ? Number(form.operation_id)
      : selectedNode?.operation_id;
  const op = catalog.operations?.find((o: Row) => o.id === operationId);
  const available =
    catalog.equipments?.filter((e: Row) =>
      catalog.equipment_operations.some(
        (eo: Row) =>
          eo.equipment_id === e.id && eo.operation_id === operationId,
      ),
    ) || [];
  const codes =
    catalog.defect_codes?.filter((d: Row) =>
      catalog.operation_defect_codes.some(
        (od: Row) =>
          od.operation_id === operationId && od.defect_code_id === d.id,
      ),
    ) || [];
  const items =
    catalog.operation_qc_items?.filter(
      (q: Row) => q.operation_id === operationId,
    ) || [];
  const missing = Number(opened?.input_qty || 0) - Number(form.output_qty),
    allocated = Object.values(defects).reduce(
      (a: number, v: any) => a + Number(v || 0),
      0,
    );
  const qcFail = items.some(
    (q: Row) =>
      qc[q.id] !== undefined &&
      qc[q.id] !== "" &&
      ((q.lower_limit !== null && Number(qc[q.id]) < Number(q.lower_limit)) ||
        (q.upper_limit !== null && Number(qc[q.id]) > Number(q.upper_limit))),
  );
  const readScan = async (code: string) => {
    try {
      const r = await api("/scan?code=" + encodeURIComponent(code));
      if (r.kind === "equipment") update("equipment_id", r.value.id);
      else if (r.kind === "operation") {
        update("operation_code", r.value.code);
        if (kind === "temporary" || c.route_free)
          update("operation_id", r.value.id);
      } else setScanError(t("equipment") + " / " + t("operation"));
      setScan("");
    } catch (e) {
      setScanError((e as Error).message);
    }
  };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    let body: Row = { version: form.version };
    for (const [k, v] of Object.entries(form))
      if (v !== "" && v !== null) body[k] = v;
    for (const k of ["operation_id", "target_node_id"])
      if (body[k]) body[k] = Number(body[k]);
    if (body.occurred_at)
      body.occurred_at = new Date(body.occurred_at).toISOString();
    if (checkout) {
      body.defects = Object.entries(defects)
        .filter(([, v]) => Number(v) > 0)
        .map(([id, qty]) => ({ defect_code_id: Number(id), qty }));
      body.qc = items.map((q: Row) => ({ qc_item_id: q.id, value: qc[q.id] }));
    }
    if (kind === "edit")
      body = {
        version: c.version,
        fields: {
          work_order_no: form.work_order_no ?? c.work_order_no ?? "",
          item_no: form.item_no ?? c.item_no ?? "",
          item_name: form.item_name ?? c.item_name ?? "",
          unit: form.unit ?? c.unit,
        },
      };
    if (kind === "skip") {
      try {
        const auth = await api("/authorizations/skip", "POST", {
          ...supervisor,
          run_card_id: c.id,
          target_node_id: Number(form.target_node_id),
        });
        body.authorization_token = auth.authorization_token;
      } catch (e) {
        setScanError((e as Error).message);
        return;
      }
    }
    const r = await send(`/cards/${c.id}/${kind}`, body);
    if (r) onClose();
  };
  return (
    <Modal
      title={t(actionLabels[kind] || kind) + " · " + c.card_no}
      onClose={onClose}
      footer={
        <>
          <span className="muted">
            v{c.version} · {fmt(c.current_qty)} {c.unit}
          </span>
          <button
            className="primary"
            form="card-action"
            disabled={
              busy ||
              (checkout &&
                (Math.abs(missing - allocated) > 0.000000001 || missing < 0))
            }
          >
            {busy ? t("busy") : t("confirm")}
            <Check size={17} />
          </button>
        </>
      }
    >
      <form id="card-action" onSubmit={submit} className="form-grid">
        <div className="wide">
          <ErrorBox error={error || scanError} />
        </div>
        {(kind === "skip" || kind === "rework") && !c.route_free && (
          <Field label={t("target")} wide>
            <select
              required
              value={form.target_node_id}
              onChange={(e) => update("target_node_id", e.target.value)}
            >
              <option value="">{t("choose")}</option>
              {snapshot.nodes
                .filter(
                  (n: Row) =>
                    kind !== "skip" ||
                    n.seq >
                      (snapshot.nodes.find(
                        (n: Row) => n.id === c.current_node_id,
                      )?.seq || 0),
                )
                .map((n: Row) => (
                  <option key={n.id} value={n.id}>
                    {n.seq} · {n.name}
                  </option>
                ))}
            </select>
          </Field>
        )}
        {start && (kind === "temporary" || c.route_free) && (
          <Field label={t("operation")} wide>
            <select
              required
              value={form.operation_id}
              onChange={(e) => update("operation_id", e.target.value)}
            >
              <option value="">{t("choose")}</option>
              {catalog.operations
                ?.filter((o: Row) => o.is_active)
                .map((o: Row) => (
                  <option key={o.id} value={o.id}>
                    {o.code} · {o.name}
                  </option>
                ))}
            </select>
          </Field>
        )}
        {start && (
          <>
            <div className="wide inline-scan">
              <ScanLine size={20} />
              <input
                value={scan}
                onChange={(e) => setScan(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    readScan(scan);
                  }
                }}
                placeholder={t("scanPlaceholder")}
              />
              <button
                type="button"
                className="icon"
                title={t("camera")}
                onClick={() => setCamera(true)}
              >
                <Camera size={20} />
              </button>
            </div>
            {op?.requires_equipment ? (
              <Field label={t("equipment")} wide>
                <select
                  required
                  value={form.equipment_id}
                  onChange={(e) => update("equipment_id", e.target.value)}
                >
                  <option value="">{t("choose")}</option>
                  {available.map((e: Row) => (
                    <option
                      key={e.id}
                      value={e.id}
                      disabled={e.state !== "ENABLED"}
                    >
                      {e.mes_code} · {t(e.state)}
                    </option>
                  ))}
                </select>
              </Field>
            ) : (
              <Field label={t("operation")} wide>
                <input
                  required
                  value={form.operation_code}
                  onChange={(e) => update("operation_code", e.target.value)}
                  placeholder={op?.code || "OP:DEMO_PKG"}
                />
              </Field>
            )}
          </>
        )}
        {checkout && (
          <>
            <p className="info wide">{t("noScroll")}</p>
            <Field label={t("output")} wide>
              <input
                type="number"
                required
                min="0"
                max={opened.input_qty}
                step="any"
                value={form.output_qty}
                onChange={(e) => update("output_qty", e.target.value)}
                className="large-number"
              />
            </Field>
            <div
              className={
                "quantity-check wide " +
                (Math.abs(missing - allocated) < 1e-9
                  ? "balanced"
                  : "unbalanced")
              }
            >
              <span>
                {t("missing")}
                <b>{fmt(missing)}</b>
              </span>
              <span>
                {t("allocated")}
                <b>{fmt(allocated)}</b>
              </span>
            </div>
            {(missing > 0 || allocated > 0) && (
              <div className="wide defect-list">
                <h3>{t("defectCodes")}</h3>
                {codes.map((d: Row) => (
                  <Field key={d.id} label={`${d.code} · ${d.name}`}>
                    <input
                      type="number"
                      min="0"
                      max={missing}
                      step="any"
                      value={defects[d.id] || ""}
                      onChange={(e) =>
                        setDefects({ ...defects, [d.id]: e.target.value })
                      }
                    />
                  </Field>
                ))}
              </div>
            )}
            {items.length > 0 && <h3 className="wide">{t("qc")}</h3>}
            {items.map((q: Row) => (
              <Field
                key={q.id}
                label={`${q.name} (${q.lower_limit ?? "−∞"}–${q.upper_limit ?? "∞"} ${q.unit || ""})`}
              >
                <input
                  type="number"
                  required
                  step="any"
                  value={qc[q.id] ?? ""}
                  onChange={(e) => setQc({ ...qc, [q.id]: e.target.value })}
                />
              </Field>
            ))}
            {qcFail && <p className="warning wide">{t("qcHold")}</p>}
            <Field label={t("note")} wide>
              <textarea
                value={form.production_note || ""}
                onChange={(e) => update("production_note", e.target.value)}
              />
            </Field>
            {c.route_free && (
              <label className="checkbox wide">
                <input
                  type="checkbox"
                  checked={form.finish_route_free}
                  onChange={(e) =>
                    update("finish_route_free", e.target.checked)
                  }
                />
                {t("finish")}
              </label>
            )}
          </>
        )}
        {["split", "rework", "scrap"].includes(kind) && (
          <Field label={t("qty")} wide>
            <input
              type="number"
              min="0.000001"
              max={c.current_qty}
              step="any"
              required
              value={form.qty}
              onChange={(e) => update("qty", e.target.value)}
            />
          </Field>
        )}
        {kind === "cancel" && (
          <>
            <p className="info wide">{t("cancelHint")}</p>
            <Field label={t("disposition")} wide>
              <select
                value={form.disposition}
                onChange={(e) => update("disposition", e.target.value)}
              >
                <option value="scrap">
                  {t("scrap")} · {fmt(c.current_qty)}
                </option>
                <option value="return">
                  {t("returned")} · {fmt(c.current_qty)}
                </option>
                <option value="transfer">
                  {t("transferred")} · {fmt(c.current_qty)}
                </option>
              </select>
            </Field>
          </>
        )}
        {kind === "abnormal-end" && (
          <Field label={t("disposition")} wide>
            <select
              value={form.disposition}
              onChange={(e) => update("disposition", e.target.value)}
            >
              <option value="keep">{t("keep")}</option>
              <option value="mark">{t("mark")}</option>
            </select>
          </Field>
        )}
        {kind === "backfill" && (
          <>
            <Field label={t("actualOperator")}>
              <select
                required
                value={form.actual_operator_id}
                onChange={(e) => update("actual_operator_id", e.target.value)}
              >
                <option value="">{t("choose")}</option>
                {catalog.accounts
                  ?.filter((a: Row) => a.is_active)
                  .map((a: Row) => (
                    <option key={a.id} value={a.id}>
                      {a.employee_no} · {a.display_name}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label={t("occurred")}>
              <input
                type="datetime-local"
                value={form.occurred_at}
                onChange={(e) => update("occurred_at", e.target.value)}
              />
            </Field>
          </>
        )}
        {kind === "skip" && (
          <>
            <h3 className="wide">{t("supervisor")}</h3>
            <Field label={t("username")}>
              <input
                autoComplete="off"
                required
                value={supervisor.username}
                onChange={(e) =>
                  setSupervisor({ ...supervisor, username: e.target.value })
                }
              />
            </Field>
            <Field label={t("password")}>
              <input
                type="password"
                autoComplete="off"
                required
                value={supervisor.password}
                onChange={(e) =>
                  setSupervisor({ ...supervisor, password: e.target.value })
                }
              />
            </Field>
          </>
        )}
        {kind === "edit" &&
          ["work_order_no", "item_no", "item_name", "unit"].map((k, i) => (
            <Field label={t(["workOrder", "item", "name", "unit"][i])} key={k}>
              <input
                required={k === "unit"}
                value={form[k] ?? c[k] ?? ""}
                onChange={(e) => update(k, e.target.value)}
              />
            </Field>
          ))}
        {!["check-in", "check-out", "edit"].includes(kind) && (
          <Field
            label={
              t("reason") +
              (kind === "release" && !catalog.settings?.release_reason_required
                ? ""
                : " *")
            }
            wide
          >
            <textarea
              required={
                !["split", "rework", "skip"].includes(kind) &&
                (kind !== "release" ||
                  catalog.settings?.release_reason_required)
              }
              value={form.reason}
              onChange={(e) => update("reason", e.target.value)}
            />
          </Field>
        )}
        {kind === "revoke" && (
          <div className="warning wide">
            {t("revoke")} ·{" "}
            {snapshot.records.find((r: Row) => !r.revoked_at)?.operation_name}
            <br />
            {dt(snapshot.records.find((r: Row) => !r.revoked_at)?.started_at)}
          </div>
        )}
      </form>
      {camera && (
        <CameraScan
          onClose={() => setCamera(false)}
          onScan={(s) => {
            setCamera(false);
            readScan(s);
          }}
        />
      )}
    </Modal>
  );
}
function EquipmentList() {
  const { t, catalog, select, actor } = useApp();
  const [edit, setEdit] = useState<Row | null>(null),
    [history, setHistory] = useState(false);
  return (
    <>
      <Title title={t("equipment")} subtitle="DEMO FACTORY · LINE 01">
        <button className="secondary" onClick={() => setHistory(!history)}>
          <Clock size={17} />
          {t("equipmentHistory")}
        </button>
        {actor.permissions.includes("equipments:manage") && (
          <button className="primary" onClick={() => setEdit({})}>
            <Plus size={17} />
            {t("add")}
          </button>
        )}
      </Title>
      {history ? (
        <Resource resource="equipment-history" title="equipmentHistory" />
      ) : (
        <div className="equipment-grid standalone">
          {catalog.equipments?.map((e: Row) => (
            <section key={e.id} className="panel equipment-tile">
              <div className="equipment-tile-top">
                <Factory />
                <Badge value={e.state} />
              </div>
              <h3>{e.mes_code}</h3>
              <p>{e.name}</p>
              <footer>
                <span>{e.reason_code}</span>
                <button
                  className="text-button"
                  onClick={() => select("equipment", e.id)}
                >
                  {t("actions")}
                  <ArrowRight size={16} />
                </button>
                {actor.permissions.includes("equipments:manage") && (
                  <button
                    className="icon"
                    onClick={() => setEdit(e)}
                    title={t("edit")}
                  >
                    <SlidersHorizontal size={17} />
                  </button>
                )}
              </footer>
            </section>
          ))}
        </div>
      )}
      {edit && (
        <ConfigModal
          kind="equipment"
          item={edit}
          onClose={() => setEdit(null)}
        />
      )}
    </>
  );
}
function EquipmentPage({ id, back }: { id: string; back: () => void }) {
  const { t, revision, select, actor } = useApp();
  const [data, setData] = useState<Row | null>(null),
    [error, setError] = useState(""),
    [modal, setModal] = useState("");
  useEffect(() => {
    api("/equipments/" + id)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [id, revision]);
  if (!data) return <ErrorBox error={error || t("loading")} />;
  const e = data.equipment;
  return (
    <>
      <button className="back-link" onClick={back}>
        <ArrowLeft size={16} />
        {t("back")}
      </button>
      <Title title={e.mes_code} subtitle={e.name}>
        <Badge value={e.state} />
      </Title>
      <Mes.EquipmentExtras id={id} />
      <section className="panel equipment-detail">
        <QRCode value={"EQ:" + e.mes_code} />
        <div>
          <span className="eyebrow">{t("statusReason")}</span>
          <h2>{e.reason_code}</h2>
          <p>{e.location || "DEMO FACTORY · LINE 01"}</p>
        </div>
        <div className="button-row">
          {actor.permissions.includes("equipment_status:set") && (
            <button className="secondary" onClick={() => setModal("status")}>
              {t("setStatus")}
            </button>
          )}
          {actor.permissions.includes("work_records:abnormal_end") &&
            actor.permissions.includes("equipment_status:set") && (
              <button className="danger" onClick={() => setModal("breakdown")}>
                <Wrench size={18} />
                {t("breakdown")}
              </button>
            )}
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <h2>{t("occupied")}</h2>
          <span>{data.cards.length}</span>
        </div>
        {data.cards.length ? (
          data.cards.map((c: Row) => (
            <button
              className="child-card"
              key={c.id}
              onClick={() => select("runcard", c.id)}
            >
              <Layers3 />
              {c.card_no}
              <Badge value={c.status} />
              <b>
                {fmt(c.current_qty)} {c.unit}
              </b>
              <span>{t("checkout")}</span>
              <ChevronRight />
            </button>
          ))
        ) : (
          <Empty />
        )}
      </section>
      <section className="panel">
        <div className="panel-heading">
          <h2>{t("history")}</h2>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t("state")}</th>
                <th>{t("reason")}</th>
                <th>{t("history")}</th>
              </tr>
            </thead>
            <tbody>
              {data.history.map((h: Row) => (
                <tr key={h.id}>
                  <td>
                    <Badge value={h.state} />
                  </td>
                  <td>
                    {h.reason_code}
                    <small>{h.note}</small>
                  </td>
                  <td>
                    {dt(h.started_at)} → {dt(h.ended_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {modal && (
        <EquipmentModal
          data={data}
          breakdown={modal === "breakdown"}
          onClose={() => setModal("")}
        />
      )}
    </>
  );
}
function EquipmentModal({
  data,
  breakdown,
  onClose,
}: {
  data: Row;
  breakdown: boolean;
  onClose: () => void;
}) {
  const { t, catalog, actor } = useApp();
  const [snapshot] = useState(data),
    [reason, setReason] = useState(""),
    [code, setCode] = useState("");
  const { send, busy, error } = useCommand();
  let reasons =
    catalog.equipment_reasons?.filter((r: Row) =>
      breakdown
        ? r.code.startsWith("DU_")
        : actor.mode === "operator" && snapshot.cards.length
          ? ["UP", "US_2", "US_5"].includes(r.code)
          : true,
    ) || [];
  const whitelist =
    catalog.equipment_reason_whitelist?.filter(
      (r: Row) => r.equipment_id === snapshot.equipment.id,
    ) || [];
  if (whitelist.length)
    reasons = reasons.filter((r: Row) =>
      whitelist.some((w: Row) => w.reason_code === r.code),
    );
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const body: Row = { reason, reason_code: code };
    if (breakdown)
      body.card_versions = Object.fromEntries(
        snapshot.cards.map((c: Row) => [c.id, c.version]),
      );
    const r = await send(
      `/equipments/${snapshot.equipment.id}/${breakdown ? "report-breakdown" : "set-equipment-status"}`,
      body,
    );
    if (r) onClose();
  };
  return (
    <Modal
      title={t(breakdown ? "breakdown" : "setStatus")}
      onClose={onClose}
      footer={
        <button
          form="equipment-action"
          className={breakdown ? "danger" : "primary"}
          disabled={busy}
        >
          {t(busy ? "busy" : "confirm")}
        </button>
      }
    >
      <form id="equipment-action" onSubmit={submit}>
        <ErrorBox error={error} />
        {breakdown && (
          <>
            <p className="warning">{t("breakdownHint")}</p>
            {snapshot.cards.map((c: Row) => (
              <div className="review-row" key={c.id}>
                <b>{c.card_no}</b>
                <span>
                  {fmt(c.current_qty)} {c.unit} · v{c.version}
                </span>
              </div>
            ))}
          </>
        )}
        <Field label={t("statusReason")}>
          <select
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
          >
            <option value="">{t("choose")}</option>
            {reasons.map((r: Row) => (
              <option key={r.code} value={r.code}>
                {r.code} · {t(r.state)} · {r.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("reason")}>
          <textarea
            required={breakdown}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>
      </form>
    </Modal>
  );
}
function Routes() {
  const { t, catalog, actor } = useApp();
  const [edit, setEdit] = useState<Row | null>(null);
  return (
    <>
      <Title title={t("routes")} subtitle={t("routeImmutable")}>
        {actor.permissions.includes("process_routes:manage") && (
          <button className="primary" onClick={() => setEdit({})}>
            <Plus size={17} />
            {t("newVersion")}
          </button>
        )}
      </Title>
      {catalog.process_route_versions?.map((v: Row) => (
        <section className="panel" key={v.id}>
          <div className="panel-heading">
            <div>
              <h2>
                {
                  catalog.process_routes.find((r: Row) => r.id === v.route_id)
                    ?.name
                }
              </h2>
              <p>
                {v.version} · {v.status}
              </p>
            </div>
            {actor.permissions.includes("process_routes:manage") && (
              <button
                className="secondary"
                onClick={() =>
                  setEdit({
                    route_id: v.route_id,
                    route_name: catalog.process_routes.find(
                      (r: Row) => r.id === v.route_id,
                    )?.name,
                    operation_ids: catalog.route_nodes
                      .filter((n: Row) => n.route_version_id === v.id)
                      .sort((a: Row, b: Row) => a.seq - b.seq)
                      .map((n: Row) => n.operation_id),
                  })
                }
              >
                {t("newVersion")}
              </button>
            )}
          </div>
          <div className="route-timeline">
            {catalog.route_nodes
              ?.filter((n: Row) => n.route_version_id === v.id)
              .sort((a: Row, b: Row) => a.seq - b.seq)
              .map((n: Row) => (
                <div key={n.id}>
                  <span>{n.seq}</span>
                  <div>
                    <b>
                      {
                        catalog.operations.find(
                          (o: Row) => o.id === n.operation_id,
                        )?.code
                      }
                    </b>
                    <small>
                      {
                        catalog.operations.find(
                          (o: Row) => o.id === n.operation_id,
                        )?.name
                      }
                    </small>
                  </div>
                </div>
              ))}
          </div>
        </section>
      ))}
      {edit && (
        <ConfigModal kind="routes" item={edit} onClose={() => setEdit(null)} />
      )}
    </>
  );
}
function Resource({ resource, title }: { resource: string; title: string }) {
  const { t, revision } = useApp();
  const [rows, setRows] = useState<Row[]>([]),
    [offset, setOffset] = useState(0),
    [error, setError] = useState(""),
    [detail, setDetail] = useState<Row | null>(null);
  const cmd = useCommand();
  useEffect(() => {
    api(`/admin/${resource}?offset=${offset}`)
      .then(setRows)
      .catch((e) => setError(e.message));
  }, [resource, revision, offset]);
  const columns: Record<string, string[]> = {
    "work-records": [
      "card_no",
      "operation_name",
      "input_qty",
      "output_qty",
      "end_type",
      "started_at",
      "ended_at",
    ],
    audit: [
      "at",
      "action",
      "display_name",
      "card_no",
      "from_status",
      "to_status",
      "reason",
    ],
    outbox: [
      "id",
      "event_type",
      "status",
      "attempts",
      "last_error",
      "created_at",
    ],
    "equipment-history": [
      "mes_code",
      "state",
      "reason_code",
      "started_at",
      "ended_at",
      "duration_seconds",
    ],
  };
  const cols = columns[resource] || Object.keys(rows[0] || {}).slice(0, 6);
  return (
    <>
      <Title title={t(title)} />
      <ErrorBox error={error || cmd.error} />
      <section className="panel">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {cols.map((c) => (
                  <th key={c}>{columnLabel(c, t)}</th>
                ))}
                <th>{t("actions")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.id || i}>
                  {cols.map((c) => (
                    <td key={c}>
                      {c.endsWith("_at") || c === "at"
                        ? dt(r[c])
                        : r[c] === null
                          ? "—"
                          : String(r[c])}
                    </td>
                  ))}
                  <td>
                    <button
                      className="icon"
                      onClick={() => setDetail(r)}
                      aria-label={t("history")}
                    >
                      <Search size={16} />
                    </button>
                    {resource === "outbox" && r.status !== "SENT" && (
                      <button
                        className="text-button"
                        disabled={cmd.busy}
                        onClick={() => cmd.send(`/outbox/${r.id}/retry`, {})}
                      >
                        {t("retry")}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!rows.length && <Empty />}
        <div className="pagination">
          <button
            className="secondary"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 100))}
          >
            {t("pagePrevious")}
          </button>
          <button
            className="secondary"
            disabled={rows.length < 100}
            onClick={() => setOffset(offset + 100)}
          >
            {t("pageNext")}
          </button>
        </div>
      </section>
      {detail && (
        <Modal title={t("history")} onClose={() => setDetail(null)}>
          <dl className="detail-list">
            {Object.entries(detail).map(([k, v]) => (
              <div key={k}>
                <dt>{columnLabel(k, t)}</dt>
                <dd>
                  {typeof v === "object" ? (
                    <pre>{JSON.stringify(v, null, 2)}</pre>
                  ) : (
                    String(v ?? "—")
                  )}
                </dd>
              </div>
            ))}
          </dl>
        </Modal>
      )}
    </>
  );
}
function columnLabel(c: string, t: (s: string) => string) {
  const labels: Record<string, string> = {
    card_no: "cardNo",
    operation_name: "operation",
    input_qty: "original",
    output_qty: "output",
    end_type: "state",
    started_at: "checkin",
    ended_at: "checkout",
    at: "history",
    action: "actions",
    display_name: "employee",
    from_status: "state",
    to_status: "state",
    reason: "reason",
    event_type: "actions",
    status: "state",
    created_at: "history",
    mes_code: "equipment",
    reason_code: "statusReason",
  };
  return labels[c] ? t(labels[c]) : c;
}
function Notifications() {
  const { t, revision, select } = useApp();
  const [data, setData] = useState<Row>({ items: [], unread: 0 }),
    [offset, setOffset] = useState(0),
    [error, setError] = useState("");
  const cmd = useCommand();
  useEffect(() => {
    api("/notifications?offset=" + offset)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [revision, offset]);
  return (
    <>
      <Title title={t("notifications")} subtitle={String(data.unread)}>
        <button
          className="secondary"
          disabled={cmd.busy}
          onClick={() => cmd.send("/notifications/read-all", {})}
        >
          <Check size={17} />
          {t("readAll")}
        </button>
      </Title>
      <ErrorBox error={error || cmd.error} />
      <section className="panel notifications">
        {data.items.map((n: Row) => (
          <button
            key={n.id}
            className={"notification " + (!n.read_at ? "unread" : "")}
            onClick={() => {
              if (n.run_card_id) select("runcard", n.run_card_id);
              else if (n.equipment_id) select("equipment", n.equipment_id);
            }}
          >
            <span className="notification-icon">
              <Bell size={20} />
            </span>
            <div>
              <h3>{n.title}</h3>
              <p>{n.body}</p>
              <small>{dt(n.created_at)}</small>
            </div>
            {!n.read_at && <i />}
          </button>
        ))}
        {!data.items.length && <Empty />}
        <div className="pagination">
          <button
            className="secondary"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            {t("pagePrevious")}
          </button>
          <button
            className="secondary"
            disabled={data.items.length < 50}
            onClick={() => setOffset(offset + 50)}
          >
            {t("pageNext")}
          </button>
        </div>
      </section>
    </>
  );
}
function Settings() {
  const { t, catalog, actor, revision } = useApp();
  const [tab, setTab] = useState("operations"),
    [edit, setEdit] = useState<Row | null>(null),
    [rules, setRules] = useState<Row[]>([]),
    [targets, setTargets] = useState<Row[]>([]),
    [error, setError] = useState("");
  const cmd = useCommand();
  const perms: Record<string, string> = {
    accounts: "accounts:manage",
    roles: "accounts:manage",
    operations: "operations:manage",
    defectCodes: "defect_codes:manage",
    notificationRules: "notifications:manage",
    policy: "accounts:manage",
  };
  const tabs = Object.keys(perms).filter((k) =>
    actor.permissions.includes(perms[k]),
  );
  useEffect(() => {
    if (tabs.length && !tabs.includes(tab)) setTab(tabs[0]);
  }, [actor]);
  useEffect(() => {
    if (tab === "notificationRules")
      Promise.all([
        api("/admin/notification-rules"),
        api("/notification-targets"),
      ])
        .then(([r, tr]) => {
          setRules(r);
          setTargets(tr);
        })
        .catch((e) => setError(e.message));
  }, [tab, revision]);
  const rows =
    tab === "notificationRules"
      ? rules
      : catalog[
          {
            accounts: "accounts",
            roles: "roles",
            operations: "operations",
            defectCodes: "defect_codes",
          }[tab] || ""
        ] || [];
  return (
    <>
      <Title title={t("settings")} subtitle={t("configuration")} />
      <div className="tabs">
        {tabs.map((k) => (
          <button
            key={k}
            className={tab === k ? "selected" : ""}
            onClick={() => setTab(k)}
          >
            {t(k)}
          </button>
        ))}
      </div>
      <ErrorBox error={error || cmd.error} />
      {!tabs.length ? (
        <Empty />
      ) : tab === "policy" ? (
        <section className="panel settings-policy">
          <h2>{t("policy")}</h2>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={!!catalog.settings?.release_reason_required}
              disabled={cmd.busy}
              onChange={(e) =>
                cmd.send(
                  "/settings",
                  { release_reason_required: e.target.checked },
                  "PUT",
                )
              }
            />
            {t("requiredReason")}
          </label>
          <p className="muted">BR-24 / docs/04</p>
        </section>
      ) : (
        <section className="panel">
          <div className="panel-heading">
            <h2>{t(tab)}</h2>
            {tab !== "notificationRules" && (
              <button className="primary" onClick={() => setEdit({})}>
                <Plus size={16} />
                {t("add")}
              </button>
            )}
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>{t("code")}</th>
                  <th>{t("name")}</th>
                  <th>{t("state")}</th>
                  <th>{t("actions")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r: Row) => (
                  <tr key={r.id || r.event_code}>
                    <td>{r.code || r.username || r.event_code}</td>
                    <td>{r.name || r.display_name}</td>
                    <td>
                      {r.is_active === false ? (
                        "—"
                      ) : (
                        <Check size={16} className="green" />
                      )}
                    </td>
                    <td>
                      <button
                        className="secondary"
                        disabled={tab === "roles" && r.code === "admin"}
                        onClick={() =>
                          setEdit(
                            tab === "notificationRules"
                              ? {
                                  ...r,
                                  targets: targets.filter(
                                    (t) => t.event_code === r.event_code,
                                  ),
                                }
                              : r,
                          )
                        }
                      >
                        {t("edit")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {edit && (
        <ConfigModal kind={tab} item={edit} onClose={() => setEdit(null)} />
      )}
    </>
  );
}
function ConfigModal({
  kind,
  item,
  onClose,
}: {
  kind: string;
  item: Row;
  onClose: () => void;
}) {
  const { t, catalog } = useApp();
  const { send, busy, error } = useCommand();
  const defaults: Record<string, Row> = {
    accounts: {
      username: "",
      display_name: "",
      employee_no: "",
      email: "",
      role_id: "",
      password: "",
      is_active: true,
    },
    roles: { code: "", name: "", permissions: [] },
    operations: {
      code: "",
      name: "",
      requires_equipment: true,
      is_active: true,
      requires_skill: false,
      requires_first_article: false,
      defect_code_ids: [],
      qc_items: [],
    },
    defectCodes: { code: "", name: "", is_active: true },
    equipment: {
      code: "",
      name: "",
      location: "",
      is_active: true,
      operation_ids: [],
      reason_codes: [],
    },
    routes: { route_name: "", version: "", operation_ids: [], activate: true },
    notificationRules: {
      channel_email: false,
      channel_inapp: true,
      repeat_enabled: false,
      repeat_every_min: 30,
      repeat_max: 5,
      escalate_enabled: false,
      escalate_after_min: 30,
      role_ids: [],
      account_ids: [],
      escalation_role_ids: [],
      escalation_account_ids: [],
    },
  };
  const initial = { ...defaults[kind], ...item };
  if (kind === "roles" && item.id)
    initial.permissions = catalog.role_permissions
      .filter((p: Row) => p.role_id === item.id)
      .map((p: Row) => p.permission_code);
  if (kind === "operations" && item.id) {
    initial.defect_code_ids = catalog.operation_defect_codes
      .filter((d: Row) => d.operation_id === item.id)
      .map((d: Row) => d.defect_code_id);
    initial.qc_items = catalog.operation_qc_items
      .filter((q: Row) => q.operation_id === item.id)
      .map(({ operation_id, ...q }: Row) => q);
  }
  if (kind === "equipment" && item.id) {
    initial.code = item.mes_code;
    initial.operation_ids = catalog.equipment_operations
      .filter((o: Row) => o.equipment_id === item.id)
      .map((o: Row) => o.operation_id);
    initial.reason_codes = catalog.equipment_reason_whitelist
      .filter((r: Row) => r.equipment_id === item.id)
      .map((r: Row) => r.reason_code);
  }
  if (kind === "notificationRules") {
    initial.role_ids = (item.targets || [])
      .filter((t: Row) => !t.is_escalation && t.role_id)
      .map((t: Row) => t.role_id);
    initial.account_ids = (item.targets || [])
      .filter((t: Row) => !t.is_escalation && t.account_id)
      .map((t: Row) => t.account_id);
    initial.escalation_role_ids = (item.targets || [])
      .filter((t: Row) => t.is_escalation && t.role_id)
      .map((t: Row) => t.role_id);
    initial.escalation_account_ids = (item.targets || [])
      .filter((t: Row) => t.is_escalation && t.account_id)
      .map((t: Row) => t.account_id);
  }
  const [form, setForm] = useState<Row>(initial);
  const change = (key: string, value: any) =>
    setForm({ ...form, [key]: value });
  const textField = (
    key: string,
    label: string,
    required = true,
    type = "text",
  ) => (
    <Field key={key} label={t(label)}>
      <input
        type={type}
        required={required}
        disabled={!!item.id && ["code", "username"].includes(key)}
        value={form[key] ?? ""}
        onChange={(e) => change(key, e.target.value)}
      />
    </Field>
  );
  const check = (key: string, label: string) => (
    <label className="checkbox" key={key}>
      <input
        type="checkbox"
        checked={!!form[key]}
        onChange={(e) => change(key, e.target.checked)}
      />
      {t(label)}
    </label>
  );
  const multi = (
    key: string,
    label: string,
    options: Row[],
    idKey = "id",
    nameKey = "name",
  ) => (
    <MultiPick
      key={key}
      label={t(label)}
      options={options}
      value={form[key]}
      onChange={(next) => change(key, next)}
      idKey={idKey}
      nameKey={nameKey}
    />
  );
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    let body: Row = {},
      path = "",
      method = "PUT";
    for (const key of Object.keys(defaults[kind])) body[key] = form[key];
    if (kind === "accounts") {
      body.role_id = Number(body.role_id);
      if (!body.password) delete body.password;
      path = "/accounts/" + encodeURIComponent(form.username);
    }
    if (kind === "roles") path = "/roles/" + encodeURIComponent(form.code);
    if (kind === "operations") {
      body.qc_items = body.qc_items.map((q: Row) => ({
        ...q,
        lower_limit: q.lower_limit === "" ? null : q.lower_limit,
        upper_limit: q.upper_limit === "" ? null : q.upper_limit,
      }));
      path = "/operations/" + encodeURIComponent(form.code);
    }
    if (kind === "defectCodes") {
      delete body.code;
      path = "/defect-codes/" + encodeURIComponent(form.code);
    }
    if (kind === "equipment") {
      delete body.code;
      path = "/equipment-config/" + encodeURIComponent(form.code);
    }
    if (kind === "routes") {
      if (item.route_id) body.route_id = item.route_id;
      path = "/routes";
      method = "POST";
    }
    if (kind === "notificationRules") {
      for (const k of ["repeat_every_min", "repeat_max", "escalate_after_min"])
        body[k] = Number(body[k]);
      path = "/notification-rules/" + item.event_code;
    }
    const r = await send(path, body, method);
    if (r) onClose();
  };
  return (
    <Modal
      title={t(kind === "equipment" ? "equipment" : kind)}
      onClose={onClose}
      footer={
        <button className="primary" form="config-form" disabled={busy}>
          {t(busy ? "busy" : "save")}
        </button>
      }
    >
      <form id="config-form" onSubmit={submit} className="form-grid">
        <div className="wide">
          <ErrorBox error={error} />
        </div>
        {kind === "accounts" && (
          <>
            {textField("username", "username")}
            {textField("display_name", "name")}
            {textField("employee_no", "employee")}
            {textField("email", "email", false, "email")}
            {textField("password", "password", !item.id, "password")}
            <Field label={t("roles")}>
              <select
                required
                value={form.role_id}
                onChange={(e) => change("role_id", e.target.value)}
              >
                <option value="">{t("choose")}</option>
                {catalog.roles?.map((r: Row) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </Field>
            {check("is_active", "active")}
          </>
        )}
        {["roles", "operations", "defectCodes", "equipment"].includes(kind) && (
          <>
            {textField("code", "code")}
            {textField("name", "name")}
          </>
        )}
        {kind === "roles" &&
          multi(
            "permissions",
            "permission",
            catalog.permissions.map((p: string) => ({ id: p, name: p })),
          )}
        {kind === "operations" && (
          <>
            {check("requires_equipment", "needsEquipment")}
            {check("is_active", "active")}
            {check("requires_skill", "needsSkill")}
            {check("requires_first_article", "needsFirstArticle")}
            {multi("defect_code_ids", "defectCodes", catalog.defect_codes)}
            <div className="wide">
              <div className="panel-heading">
                <h3>{t("qcItems")}</h3>
                <button
                  type="button"
                  className="secondary"
                  onClick={() =>
                    change("qc_items", [
                      ...form.qc_items,
                      {
                        name: "",
                        unit: "",
                        lower_limit: null,
                        upper_limit: null,
                      },
                    ])
                  }
                >
                  <Plus size={16} />
                  {t("add")}
                </button>
              </div>
              {form.qc_items.map((q: Row, i: number) => (
                <div className="qc-config" key={i}>
                  {["name", "unit", "lower_limit", "upper_limit"].map(
                    (key, j) => (
                      <Field
                        key={key}
                        label={t(["name", "unit", "lower", "upper"][j])}
                      >
                        <input
                          required={key === "name"}
                          type={j > 1 ? "number" : "text"}
                          step="any"
                          value={q[key] ?? ""}
                          onChange={(e) =>
                            change(
                              "qc_items",
                              form.qc_items.map((x: Row, n: number) =>
                                n === i ? { ...x, [key]: e.target.value } : x,
                              ),
                            )
                          }
                        />
                      </Field>
                    ),
                  )}
                </div>
              ))}
            </div>
          </>
        )}
        {kind === "defectCodes" && check("is_active", "active")}
        {kind === "equipment" && (
          <>
            {textField("location", "location", false)}
            {check("is_active", "active")}
            {multi("operation_ids", "allowedOperations", catalog.operations)}
            {multi(
              "reason_codes",
              "reasonWhitelist",
              catalog.equipment_reasons,
              "code",
              "name",
            )}
          </>
        )}
        {kind === "routes" && (
          <>
            {textField("route_name", "name")}
            {textField("version", "version")}
            {check("activate", "activate")}
            <div className="wide">
              <h3>{t("progress")}</h3>
              {form.operation_ids.map((id: number, i: number) => (
                <div className="route-edit-row" key={i}>
                  <b>{i + 1}</b>
                  <select
                    value={id}
                    onChange={(e) =>
                      change(
                        "operation_ids",
                        form.operation_ids.map((x: number, n: number) =>
                          n === i ? Number(e.target.value) : x,
                        ),
                      )
                    }
                  >
                    {catalog.operations.map((o: Row) => (
                      <option value={o.id} key={o.id}>
                        {o.code} · {o.name}
                      </option>
                    ))}
                  </select>
                  <button
                    className="icon"
                    type="button"
                    onClick={() =>
                      change(
                        "operation_ids",
                        form.operation_ids.filter(
                          (_: any, n: number) => i !== n,
                        ),
                      )
                    }
                  >
                    <X size={17} />
                  </button>
                </div>
              ))}
              <button
                type="button"
                className="secondary"
                onClick={() =>
                  change("operation_ids", [
                    ...form.operation_ids,
                    catalog.operations[0]?.id,
                  ])
                }
              >
                <Plus size={17} />
                {t("operation")}
              </button>
            </div>
          </>
        )}
        {kind === "notificationRules" && (
          <>
            {check("channel_email", "email")}
            {check("channel_inapp", "inapp")}
            {check("repeat_enabled", "repeat")}
            {check("escalate_enabled", "escalate")}
            {textField("repeat_every_min", "repeatEvery", true, "number")}
            {textField("repeat_max", "repeatMax", true, "number")}
            {textField("escalate_after_min", "escalateAfter", true, "number")}
            {multi("role_ids", "recipients", catalog.roles)}
            {multi(
              "account_ids",
              "accounts",
              catalog.accounts,
              "id",
              "display_name",
            )}
            {multi(
              "escalation_role_ids",
              "escalationRecipients",
              catalog.roles,
            )}
            {multi(
              "escalation_account_ids",
              "accounts",
              catalog.accounts,
              "id",
              "display_name",
            )}
          </>
        )}
      </form>
    </Modal>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
if ("serviceWorker" in navigator && import.meta.env.PROD)
  window.addEventListener("load", () =>
    navigator.serviceWorker.register("/sw.js").catch(console.error),
  );
