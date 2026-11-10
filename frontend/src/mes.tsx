// Extended MES pages: work orders, scheduling, materials/BOM, traceability, quality, reports/OEE,
// maintenance, people, SOP documents and integration. Plus card/equipment extras for the tablet.
import React, { useEffect, useRef, useState } from "react";
import {
  Boxes,
  CalendarClock,
  ChartLine,
  Download,
  FileText,
  Gauge,
  GitBranch,
  GraduationCap,
  Plug,
  Plus,
  Search,
  TriangleAlert,
  X,
} from "lucide-react";
import { api, ApiError, Row } from "./api";
import { useApp } from "./ctx";
import { messages, statusLabels } from "./i18n";
import { mesColumns, mesMessages, mesStatus } from "./i18n-mes";
import "./mes.css";

// Register the extended labels with the shared translator (column names never override existing keys).
for (const [k, v] of Object.entries(mesColumns)) if (!messages[k]) messages[k] = v;
Object.assign(messages, mesMessages);
Object.assign(statusLabels, mesStatus);

// Authenticated binary fetch for exports, ZPL labels and SOP files (same session token as api.ts).
async function fetchBlob(path: string) {
  let token = "";
  try {
    token = sessionStorage.getItem("mes-token") || "";
  } catch {}
  const r = await fetch("/api" + path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!r.ok) {
    const d = await r.json().catch(() => ({ detail: r.statusText }));
    throw new ApiError(r.status, d.code || "API", String(d.detail));
  }
  return r.blob();
}
async function download(path: string, filename: string) {
  const u = URL.createObjectURL(await fetchBlob(path));
  const a = document.createElement("a");
  a.href = u;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(u), 1000);
}
async function openFile(path: string) {
  const u = URL.createObjectURL(await fetchBlob(path));
  window.open(u, "_blank", "noopener");
  setTimeout(() => URL.revokeObjectURL(u), 60000);
}

export const icons = {
  schedule: CalendarClock,
  materials: Boxes,
  trace: GitBranch,
  quality: Gauge,
  reports: ChartLine,
  people: GraduationCap,
  documents: FileText,
  integration: Plug,
};

// ---------- small helpers ----------
const num = (v: any) =>
  v === null || v === undefined || v === "" || isNaN(Number(v))
    ? "—"
    : Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 });
const pct = (v: any) => (v === null || v === undefined ? "—" : (Number(v) * 100).toFixed(1) + "%");
const when = (v: any) => (v ? new Date(v).toLocaleString() : "—");
const hours = (sec: any) => num(Number(sec || 0) / 3600) + " h";
const PCT = ["availability", "performance", "quality", "oee", "yield", "life_used"];
const STATUS = /^(status|kind|result|state|txn_type|end_type)$/;
const NUM = /qty|_sec$|hours|count|^input$|^output$|^defect$|processed|^good$|required|issued|on_hand|^lots$|events|^used$|life_limit|^records$|breakdowns|pm_done|reported|^n$|cumulative/;

function useLoad<T = any>(path: string | null) {
  const { revision } = useApp();
  // Keep the path with the data so a tab switch never renders the previous tab's rows.
  const [state, setState] = useState<{ path: string | null; data: T | null }>({ path: null, data: null }),
    [error, setError] = useState("");
  useEffect(() => {
    if (!path) return;
    let live = true;
    setError("");
    api(path)
      .then((d) => live && setState({ path, data: d }))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [path, revision]);
  return { data: state.path === path ? state.data : null, error };
}

function useCmd() {
  const { refresh, onAuthError, t } = useApp();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const receipt = useRef({ payload: "", key: "" });
  async function send(path: string, body: any, method = "POST") {
    const fp = JSON.stringify({ path, body: body instanceof FormData ? [...body.keys()] : body, method });
    if (receipt.current.payload !== fp) receipt.current = { payload: fp, key: crypto.randomUUID() };
    setBusy(true);
    setError("");
    try {
      const r = await api(path, method, body, receipt.current.key);
      refresh();
      return r;
    } catch (e) {
      const err = e as ApiError;
      setError(err.code === "VERSION_CONFLICT" ? t("stale") : err.message);
      if (err.status === 401) onAuthError();
      return null;
    } finally {
      setBusy(false);
    }
  }
  return { send, busy, error, setError };
}

const can = (actor: Row, p: string) => actor?.permissions?.includes(p);

function Err({ error }: { error?: string }) {
  const ref = useRef<HTMLDivElement>(null);
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

function Head({ title, sub, children }: { title: string; sub?: string; children?: React.ReactNode }) {
  return (
    <div className="title-row">
      <div>
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      <div className="button-row">{children}</div>
    </div>
  );
}

function Tabs({ tabs, value, set }: { tabs: string[]; value: string; set: (v: string) => void }) {
  const { t } = useApp();
  return (
    <div className="tabs" role="tablist">
      {tabs.map((x) => (
        <button key={x} role="tab" aria-selected={x === value} className={x === value ? "selected" : ""} onClick={() => set(x)}>
          {t(x)}
        </button>
      ))}
    </div>
  );
}

function Status({ value }: { value: string }) {
  const { t } = useApp();
  return value ? (
    <span className={"badge " + String(value).toLowerCase()}>
      <i />
      {t(value)}
    </span>
  ) : null;
}

function Bar({ value, danger = false }: { value: number; danger?: boolean }) {
  const v = Math.max(0, Math.min(1, value || 0));
  return (
    <span className="mes-bar" title={pct(value)}>
      <i style={{ width: v * 100 + "%" }} className={danger ? "bar-bad" : ""} />
      <b>{pct(value)}</b>
    </span>
  );
}

function cell(k: string, v: any): React.ReactNode {
  if (v === null || v === undefined || v === "") return "—";
  if (k === "progress_pct") return <Bar value={v} />;
  if (k === "late") return v ? <Status value="FAIL" /> : "";
  if (typeof v === "boolean") return v ? "✓" : "—";
  if (PCT.includes(k)) return pct(v);
  if (STATUS.test(k)) return <Status value={String(v)} />;
  if (/_at$|^at$|planned_start|planned_end|^oldest$/.test(k)) return when(v);
  if (/_sec$|^seconds$/.test(k)) return hours(v);
  if (NUM.test(k)) return num(v);
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

function Table({ rows, cols, actions, onRow }: { rows: Row[] | null; cols: string[]; actions?: (r: Row) => React.ReactNode; onRow?: (r: Row) => void }) {
  const { t } = useApp();
  if (!rows) return <p className="muted">{t("loading")}</p>;
  return (
    <>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {cols.map((c) => (
                <th key={c}>{t(c)}</th>
              ))}
              {actions && <th>{t("actions")}</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.id ?? r.key ?? i} onClick={onRow ? () => onRow(r) : undefined} className={onRow ? "clickable" : ""}>
                {cols.map((c) => (
                  <td key={c}>{cell(c, r[c])}</td>
                ))}
                {actions && (
                  <td className="row-actions" onClick={(e) => e.stopPropagation()}>
                    {actions(r)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && <div className="empty">{t("empty")}</div>}
    </>
  );
}

function Modal({ title, children, onClose, footer }: { title: string; children: React.ReactNode; onClose: () => void; footer?: React.ReactNode }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <section className="modal" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
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

type F = { name: string; label?: string; type?: "text" | "number" | "date" | "datetime-local" | "select" | "checkbox" | "textarea" | "file"; options?: [string, string][]; required?: boolean; value?: any };

function FormModal({ title, fields, onClose, onSubmit }: { title: string; fields: F[]; onClose: () => void; onSubmit: (v: Row, cmd: ReturnType<typeof useCmd>) => Promise<any> }) {
  const { t } = useApp();
  const cmd = useCmd();
  const [v, setV] = useState<Row>(() => Object.fromEntries(fields.map((f) => [f.name, f.value ?? (f.type === "checkbox" ? false : "")])));
  const set = (k: string, x: any) => setV((o) => ({ ...o, [k]: x }));
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (await onSubmit(v, cmd)) onClose();
  }
  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="secondary" onClick={onClose}>
            {t("dismiss")}
          </button>
          <button className="primary" form="mes-form" disabled={cmd.busy}>
            {t("save")}
          </button>
        </>
      }
    >
      <Err error={cmd.error} />
      <form id="mes-form" className="form-grid" onSubmit={submit}>
        {fields.map((f) => (
          <label key={f.name} className={"field " + (f.type === "textarea" ? "wide" : "")}>
            <span>
              {t(f.label || f.name)}
              {f.required && " *"}
            </span>
            {f.type === "select" ? (
              <select value={v[f.name]} required={f.required} onChange={(e) => set(f.name, e.target.value)}>
                <option value="">—</option>
                {f.options?.map(([k, l]) => (
                  <option key={k} value={k}>
                    {l}
                  </option>
                ))}
              </select>
            ) : f.type === "checkbox" ? (
              <input type="checkbox" checked={!!v[f.name]} onChange={(e) => set(f.name, e.target.checked)} />
            ) : f.type === "textarea" ? (
              <textarea rows={5} value={v[f.name]} required={f.required} onChange={(e) => set(f.name, e.target.value)} />
            ) : f.type === "file" ? (
              <input type="file" required={f.required} onChange={(e) => set(f.name, e.target.files?.[0] || null)} />
            ) : (
              <input type={f.type || "text"} step="any" value={v[f.name]} required={f.required} onChange={(e) => set(f.name, e.target.value)} />
            )}
          </label>
        ))}
      </form>
    </Modal>
  );
}

const clean = (v: Row) => Object.fromEntries(Object.entries(v).filter(([, x]) => x !== "" && x !== null));
const eqOptions = (catalog: Row): [string, string][] => (catalog.equipments || []).map((e: Row) => [e.id, `${e.mes_code} · ${e.name}`]);
const opOptions = (catalog: Row): [string, string][] => (catalog.operations || []).map((o: Row) => [o.code, `${o.code} · ${o.name}`]);

// ======================================================================
// Work orders
// ======================================================================
function WorkOrders() {
  const { t, catalog, actor, select } = useApp();
  const { data, error } = useLoad<Row[]>("/work-orders");
  const cmd = useCmd();
  const [form, setForm] = useState(false),
    [detail, setDetail] = useState<Row | null>(null),
    [closing, setClosing] = useState<Row | null>(null);
  const versions: [string, string][] = (catalog.process_route_versions || []).map((v: Row) => [
    String(v.id),
    `${(catalog.process_routes || []).find((r: Row) => r.id === v.route_id)?.name || "#" + v.route_id} · ${v.version} (${v.status})`,
  ]);
  const rows = (data || []).map((r) => ({ ...r, progress_pct: Number(r.good_qty) / Number(r.planned_qty) }));
  return (
    <>
      <Head title={t("workorders")} sub="工單 → RunCard → 報工">
        {can(actor, "run_cards:create") && (
          <button className="primary" onClick={() => setForm(true)}>
            <Plus size={16} />
            {t("newWorkOrder")}
          </button>
        )}
      </Head>
      <Err error={error || cmd.error} />
      <section className="panel">
        <Table
          rows={data ? rows : null}
          cols={["wo_no", "item_no", "item_name", "planned_qty", "good_qty", "wip_qty", "scrap_qty", "progress_pct", "due_date", "priority", "status", "source"]}
          onRow={setDetail}
          actions={(r) =>
            can(actor, "run_cards:create") && (
              <>
                {r.status === "PLANNED" && (
                  <button className="text-button" disabled={cmd.busy} onClick={() => cmd.send(`/work-orders/${r.id}/release`, {})}>
                    {t("release")}
                  </button>
                )}
                {r.status !== "CLOSED" && r.status !== "CANCELLED" && (
                  <button className="text-button" onClick={() => setClosing(r)}>
                    {r.status === "PLANNED" ? t("cancelWo") : t("close")}
                  </button>
                )}
              </>
            )
          }
        />
      </section>
      {form && (
        <FormModal
          title={t("newWorkOrder")}
          onClose={() => setForm(false)}
          fields={[
            { name: "wo_no", required: true, label: "work_order_no" },
            { name: "item_no" },
            { name: "item_name" },
            { name: "planned_qty", type: "number", required: true },
            { name: "lot_size", type: "number" },
            { name: "unit", value: "pcs" },
            { name: "route_version_id", type: "select", options: versions, label: "routeVersion" },
            { name: "due_date", type: "date" },
            { name: "priority", type: "number", value: 5 },
          ]}
          onSubmit={(v, c) => c.send("/work-orders", { ...clean(v), route_version_id: v.route_version_id ? Number(v.route_version_id) : undefined, priority: Number(v.priority || 5) })}
        />
      )}
      {closing && (
        <FormModal
          title={`${closing.wo_no} · ${t(closing.status === "PLANNED" ? "cancelWo" : "close")}`}
          onClose={() => setClosing(null)}
          fields={[{ name: "reason", required: true, type: "textarea" }]}
          onSubmit={(v, c) => c.send(`/work-orders/${closing.id}/close`, { status: closing.status === "PLANNED" ? "CANCELLED" : "CLOSED", reason: v.reason })}
        />
      )}
      {detail && <WorkOrderDetail id={detail.id} title={detail.wo_no} onClose={() => setDetail(null)} onCard={(id: string) => select("runcard", id)} />}
    </>
  );
}

function WorkOrderDetail({ id, title, onClose, onCard }: { id: string; title: string; onClose: () => void; onCard: (id: string) => void }) {
  const { data, error } = useLoad<Row>(`/work-orders/${id}`);
  return (
    <Modal title={title} onClose={onClose}>
      <Err error={error} />
      <Table rows={data?.cards || null} cols={["card_no", "status", "current_qty", "original_qty", "scrap_qty", "defect_qty", "updated_at"]} onRow={(r) => onCard(r.id)} />
    </Modal>
  );
}

// ======================================================================
// Scheduling
// ======================================================================
function Schedule() {
  const { t, actor } = useApp();
  const { data, error } = useLoad<Row[]>("/schedule");
  const cmd = useCmd();
  const [result, setResult] = useState<Row | null>(null);
  const rows = data || [];
  const t0 = Math.min(...rows.map((r) => +new Date(r.planned_start)), Date.now());
  const t1 = Math.max(...rows.map((r) => +new Date(r.planned_end)), t0 + 3600e3);
  const machines = [...new Set(rows.map((r) => r.mes_code))];
  return (
    <>
      <Head title={t("schedule")} sub={`${rows.length} · ${when(t0)} → ${when(t1)}`}>
        {can(actor, "run_cards:manage") && (
          <button className="primary" disabled={cmd.busy} onClick={async () => setResult(await cmd.send("/schedule/auto", {}))}>
            <CalendarClock size={16} />
            {t("autoSchedule")}
          </button>
        )}
      </Head>
      <Err error={error || cmd.error} />
      {result && (
        <div className="warning">
          {t("plan")}: {result.planned}
          {result.unplanned?.length > 0 && ` · ${result.unplanned.map((u: Row) => u.card_no + " (" + u.reason + ")").join(", ")}`}
        </div>
      )}
      <section className="panel">
        <div className="gantt">
          {machines.map((m) => (
            <div className="gantt-row" key={m}>
              <span>{m}</span>
              <div>
                {rows
                  .filter((r) => r.mes_code === m)
                  .map((r) => (
                    <i
                      key={r.id}
                      className={r.late ? "late" : ""}
                      title={`${r.card_no} · ${r.operation_code} · ${when(r.planned_start)} → ${when(r.planned_end)}`}
                      style={{ left: ((+new Date(r.planned_start) - t0) / (t1 - t0)) * 100 + "%", width: Math.max(0.6, ((+new Date(r.planned_end) - +new Date(r.planned_start)) / (t1 - t0)) * 100) + "%" }}
                    >
                      {r.card_no}
                    </i>
                  ))}
              </div>
            </div>
          ))}
          {!rows.length && <div className="empty">{t("empty")}</div>}
        </div>
      </section>
      <section className="panel">
        <Table rows={data} cols={["mes_code", "card_no", "operation_code", "current_qty", "wo_no", "priority", "due_date", "planned_start", "planned_end", "late"]} />
      </section>
    </>
  );
}

// ======================================================================
// Materials, lots, BOM
// ======================================================================
function Materials() {
  const { t, actor } = useApp();
  const [tab, setTab] = useState("material");
  const mats = useLoad<Row[]>("/materials"),
    lots = useLoad<Row[]>("/lots"),
    boms = useLoad<Row[]>("/boms");
  const [form, setForm] = useState(""),
    [adjust, setAdjust] = useState<Row | null>(null);
  const write = can(actor, "run_cards:manage");
  const matOpts: [string, string][] = (mats.data || []).map((m) => [m.code, `${m.code} · ${m.name}`]);
  return (
    <>
      <Head title={t("materials")} sub="BOM · 批號 · 庫存">
        {write && tab === "material" && <button className="primary" onClick={() => setForm("material")}><Plus size={16} />{t("newMaterial")}</button>}
        {write && tab === "lots" && <button className="primary" onClick={() => setForm("lot")}><Plus size={16} />{t("receive")}</button>}
        {write && tab === "boms" && <button className="primary" onClick={() => setForm("bom")}><Plus size={16} />{t("newBom")}</button>}
      </Head>
      <Tabs tabs={["material", "lots", "boms"]} value={tab} set={setTab} />
      <Err error={mats.error || lots.error || boms.error} />
      {tab === "material" && (
        <section className="panel">
          <Table rows={mats.data} cols={["code", "name", "unit", "on_hand", "lots", "is_active"]} />
        </section>
      )}
      {tab === "lots" && (
        <section className="panel">
          <Table
            rows={lots.data}
            cols={["lot_no", "material_code", "material_name", "supplier", "received_qty", "remaining_qty", "unit", "status", "expires_at", "received_at"]}
            actions={(r) => (
              <>
                <button className="text-button" onClick={() => download(`/labels/lot/${encodeURIComponent(r.lot_no)}`, `${r.lot_no}.zpl`)}>{t("label")}</button>
                {write && <button className="text-button" onClick={() => setAdjust(r)}>{t("adjust")}</button>}
              </>
            )}
          />
        </section>
      )}
      {tab === "boms" &&
        (boms.data || []).map((b) => (
          <section className="panel" key={b.id}>
            <div className="panel-heading">
              <h2>
                {b.item_no} · {b.version}
              </h2>
              <Status value={b.status} />
            </div>
            <Table rows={b.lines} cols={["operation_code", "material_code", "material_name", "qty_per", "unit"]} />
          </section>
        ))}
      {form === "material" && (
        <FormModal title={t("newMaterial")} onClose={() => setForm("")} fields={[{ name: "code", required: true }, { name: "name", required: true }, { name: "unit", value: "pcs" }]}
          onSubmit={(v, c) => c.send("/materials", { ...v, is_active: true })} />
      )}
      {form === "lot" && (
        <FormModal title={t("receive")} onClose={() => setForm("")}
          fields={[{ name: "lot_no", required: true }, { name: "material_code", type: "select", options: matOpts, required: true }, { name: "qty", type: "number", required: true }, { name: "supplier" }, { name: "expires_at", type: "date" }]}
          onSubmit={(v, c) => c.send("/lots/receive", clean(v))} />
      )}
      {form === "bom" && (
        <FormModal title={t("newBom")} onClose={() => setForm("")}
          fields={[{ name: "item_no", required: true }, { name: "version", required: true }, { name: "lines", type: "textarea", label: "bomLines", required: true, value: "DEMO-PCB, DEMO_PRT, 1" }, { name: "activate", type: "checkbox", value: true }]}
          onSubmit={(v, c) =>
            c.send("/boms", {
              item_no: v.item_no,
              version: v.version,
              activate: v.activate,
              lines: String(v.lines).split("\n").map((l) => l.split(",").map((x) => x.trim())).filter((p) => p[0])
                .map(([m, o, q]) => ({ material_code: m, operation_code: o || undefined, qty_per: Number(q || 1) })),
            })
          } />
      )}
      {adjust && (
        <FormModal title={`${adjust.lot_no} · ${t("adjust")}`} onClose={() => setAdjust(null)}
          fields={[{ name: "qty", type: "number", value: 0 }, { name: "txn_type", type: "select", options: [["ADJUST", "ADJUST"], ["SCRAP", "SCRAP"], ["RETURN", "RETURN"]], value: "ADJUST" },
            { name: "status", type: "select", options: [["AVAILABLE", t("AVAILABLE")], ["HOLD", t("HOLD")]] }, { name: "reason", type: "textarea", required: true }]}
          onSubmit={(v, c) => c.send(`/lots/${encodeURIComponent(adjust.lot_no)}/adjust`, { ...clean(v), qty: Number(v.qty || 0) })} />
      )}
    </>
  );
}

// ======================================================================
// Traceability
// ======================================================================
function Trace() {
  const { t, select } = useApp();
  const [q, setQ] = useState(""),
    [mode, setMode] = useState("traceCard"),
    [data, setData] = useState<Row | null>(null),
    [error, setError] = useState("");
  async function run(e?: React.FormEvent) {
    e?.preventDefault();
    setError("");
    setData(null);
    try {
      const code = q.trim().replace(/^RC:/i, "");
      setData(await api(mode === "traceCard" ? `/trace/card/${encodeURIComponent(code)}` : `/trace/lot/${encodeURIComponent(code)}`));
    } catch (err) {
      setError((err as Error).message);
    }
  }
  return (
    <>
      <Head title={t("trace")} sub="Genealogy · 正向 / 反向追溯" />
      <Tabs tabs={["traceCard", "traceLot"]} value={mode} set={(m) => { setMode(m); setData(null); }} />
      <form className="panel trace-search" onSubmit={run}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("traceSearch")} autoFocus />
        <button className="primary">
          <Search size={16} />
          {t("traceGo")}
        </button>
      </form>
      <Err error={error} />
      {data && mode === "traceCard" && (
        <>
          <div className="warning">{data.ancestors.join(" → ")}{data.work_order && ` · ${data.work_order.wo_no}`}</div>
          <TraceNode node={data.tree} searched={data.searched} onCard={(id) => select("runcard", id)} />
        </>
      )}
      {data && mode === "traceLot" && (
        <>
          <section className="panel">
            <div className="panel-heading">
              <h2>{data.lot.lot_no} · {data.lot.material_code} {data.lot.material_name}</h2>
              <Status value={data.lot.status} />
            </div>
            <Table rows={data.used_by.map((u: Row) => ({ ...u, descendants: u.descendants.join(", ") }))} cols={["card_no", "work_order_no", "operation_code", "qty", "status", "descendants", "at"]} onRow={(r) => select("runcard", r.id)} />
          </section>
          <section className="panel">
            <Table rows={data.transactions} cols={["txn_type", "qty", "reason", "at"]} />
          </section>
        </>
      )}
    </>
  );
}

function TraceNode({ node, searched, onCard, depth = 0 }: { node: Row; searched: string; onCard: (id: string) => void; depth?: number }) {
  const c = node.card;
  return (
    <section className={"panel trace-node" + (c.card_no === searched ? " hit" : "")} style={{ marginLeft: Math.min(depth, 6) * 24 }}>
      <div className="panel-heading">
        <h2>
          <button className="text-button" onClick={() => onCard(c.id)}>{c.card_no}</button> <small className="muted">{c.source_type} · {num(c.current_qty)}/{num(c.original_qty)} {c.unit} · serial {node.serials}</small>
        </h2>
        <Status value={c.status} />
      </div>
      <Table rows={node.records} cols={["operation_code", "mes_code", "employee_no", "input_qty", "output_qty", "end_type", "started_at", "ended_at"]} />
      {node.materials.length > 0 && <Table rows={node.materials} cols={["operation_code", "material_code", "lot_no", "supplier", "qty", "at"]} />}
      {node.ncrs.length > 0 && <Table rows={node.ncrs} cols={["ncr_no", "status", "description"]} />}
      {node.children.map((ch: Row) => (
        <TraceNode key={ch.card.id} node={ch} searched={searched} onCard={onCard} depth={depth + 1} />
      ))}
    </section>
  );
}

// ======================================================================
// Quality
// ======================================================================
function Quality() {
  const { t, catalog, actor } = useApp();
  const [tab, setTab] = useState("spc");
  const items: Row[] = catalog.operation_qc_items || [];
  const [item, setItem] = useState<string>(""),
    [days, setDays] = useState(30);
  const qc = item || (items[0] ? String(items[0].id) : "");
  const spc = useLoad<Row>(tab === "spc" && qc ? `/quality/spc?qc_item_id=${qc}&days=${days}` : null);
  const par = useLoad<Row[]>(tab === "pareto" ? `/quality/pareto?days=${days}` : null);
  const ncr = useLoad<Row[]>(tab === "ncr" ? "/ncrs" : null);
  const fai = useLoad<Row[]>(tab === "fai" ? "/first-articles" : null);
  const cmd = useCmd();
  const [form, setForm] = useState<string | Row>("");
  const judge = can(actor, "quality:judge");
  return (
    <>
      <Head title={t("quality")} sub="SPC · Pareto · NCR · 首件">
        {tab === "ncr" && judge && <button className="primary" onClick={() => setForm("ncr")}><Plus size={16} />{t("openNcr")}</button>}
      </Head>
      <Tabs tabs={["spc", "pareto", "ncr", "fai"]} value={tab} set={setTab} />
      <Err error={spc.error || par.error || ncr.error || fai.error || cmd.error} />
      {(tab === "spc" || tab === "pareto") && (
        <div className="panel filters">
          {tab === "spc" && (
            <label className="field">
              <span>{t("qcItem")}</span>
              <select value={qc} onChange={(e) => setItem(e.target.value)}>
                {items.map((i) => (
                  <option key={i.id} value={i.id}>
                    {(catalog.operations || []).find((o: Row) => o.id === i.operation_id)?.code} · {i.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="field">
            <span>Days</span>
            <input type="number" min={1} max={366} value={days} onChange={(e) => setDays(Number(e.target.value) || 30)} />
          </label>
        </div>
      )}
      {tab === "spc" && spc.data && <SpcChart data={spc.data} />}
      {tab === "pareto" && par.data && <ParetoChart rows={par.data} />}
      {tab === "ncr" && (
        <section className="panel">
          <Table rows={ncr.data} cols={["ncr_no", "card_no", "defect_code", "qty", "description", "owner_name", "status", "root_cause", "corrective_action", "opened_at", "closed_at"]}
            actions={(r) => judge && r.status !== "CLOSED" && <button className="text-button" onClick={() => setForm(r)}>{t("edit")}</button>} />
        </section>
      )}
      {tab === "fai" && (
        <section className="panel">
          <Table rows={fai.data} cols={["mes_code", "operation_code", "card_no", "result", "note", "checked_by_no", "created_at", "approver", "approved_at"]}
            actions={(r) => judge && !r.approved_at && <button className="text-button" disabled={cmd.busy} onClick={() => cmd.send(`/first-articles/${r.id}/approve`, {})}>{t("approve")}</button>} />
        </section>
      )}
      {form === "ncr" && (
        <FormModal title={t("openNcr")} onClose={() => setForm("")}
          fields={[{ name: "card_no" }, { name: "defect_code", type: "select", options: (catalog.defect_codes || []).map((d: Row) => [d.code, `${d.code} · ${d.name}`]) },
            { name: "qty", type: "number" }, { name: "owner_username", type: "select", options: (catalog.accounts || []).map((a: Row) => [a.username, a.display_name]) },
            { name: "description", type: "textarea", required: true }]}
          onSubmit={(v, c) => c.send("/ncrs", { ...clean(v), qty: v.qty ? Number(v.qty) : undefined })} />
      )}
      {typeof form === "object" && (
        <FormModal title={form.ncr_no} onClose={() => setForm("")}
          fields={[{ name: "status", type: "select", options: ["OPEN", "INVESTIGATING", "CLOSED"].map((s) => [s, t(s)]), value: form.status, required: true },
            { name: "root_cause", type: "textarea", value: form.root_cause || "" }, { name: "corrective_action", type: "textarea", value: form.corrective_action || "" },
            { name: "preventive_action", type: "textarea", value: form.preventive_action || "" }]}
          onSubmit={(v, c) => c.send(`/ncrs/${form.id}`, clean(v))} />
      )}
    </>
  );
}

function SpcChart({ data }: { data: Row }) {
  const { t } = useApp();
  const pts: Row[] = data.points || [],
    s = data.stats || {},
    it = data.item;
  const vals = pts.map((p) => Number(p.value));
  const lines = [s.ucl, s.lcl, s.mean, it.upper_limit, it.lower_limit].filter((x) => x !== null && x !== undefined).map(Number);
  const lo = Math.min(...vals, ...lines),
    hi = Math.max(...vals, ...lines);
  const pad = (hi - lo || 1) * 0.1,
    y0 = lo - pad,
    y1 = hi + pad;
  const W = 760, H = 260, L = 50, R = 20;
  const x = (i: number) => L + (pts.length > 1 ? (i / (pts.length - 1)) * (W - L - R) : (W - L - R) / 2);
  const y = (v: number) => H - 25 - ((v - y0) / (y1 - y0)) * (H - 45);
  const hline = (v: any, cls: string, label: string) =>
    v === null || v === undefined ? null : (
      <g key={label}>
        <line x1={L} x2={W - R} y1={y(Number(v))} y2={y(Number(v))} className={cls} />
        <text x={W - R} y={y(Number(v)) - 4} textAnchor="end" className="chart-label">{label} {num(v)}</text>
      </g>
    );
  return (
    <>
      <div className="kpis">
        {[["n", s.n], ["mean", num(s.mean)], ["σ", num(s.sd)], ["Cp", num(s.cp)], ["Cpk", num(s.cpk)]].map(([k, v]) => (
          <div className="kpi" key={k}>
            <div>{k}</div>
            <strong>{v ?? "—"}</strong>
          </div>
        ))}
      </div>
      <section className="panel">
        <div className="panel-heading">
          <h2>{t("spc")} · {it.operation_code} · {it.name} ({it.unit || ""})</h2>
        </div>
        {pts.length < 2 ? (
          <div className="empty">{t("empty")}</div>
        ) : (
          <div className="table-scroll">
            <svg viewBox={`0 0 ${W} ${H}`} className="mes-chart" role="img" aria-label="SPC">
              {hline(it.upper_limit, "spec", "USL")}
              {hline(it.lower_limit, "spec", "LSL")}
              {hline(s.ucl, "ctl", "UCL")}
              {hline(s.lcl, "ctl", "LCL")}
              {hline(s.mean, "mean", "X̄")}
              <polyline className="series" points={vals.map((v, i) => `${x(i)},${y(v)}`).join(" ")} />
              {vals.map((v, i) => (
                <circle key={i} cx={x(i)} cy={y(v)} r={4} className={!pts[i].in_spec || s.out_of_control?.includes(i) ? "bad" : "ok"}>
                  <title>{`${pts[i].card_no} · ${num(v)} · ${when(pts[i].at)}`}</title>
                </circle>
              ))}
            </svg>
          </div>
        )}
      </section>
    </>
  );
}

function ParetoChart({ rows }: { rows: Row[] }) {
  const { t } = useApp();
  const max = Math.max(1, ...rows.map((r) => Number(r.qty)));
  const W = 760, H = 240, L = 40, B = 40;
  const bw = rows.length ? (W - L - 10) / rows.length : 0;
  return (
    <section className="panel">
      <div className="panel-heading"><h2>{t("pareto")}</h2></div>
      {!rows.length ? <div className="empty">{t("empty")}</div> : (
        <div className="table-scroll">
          <svg viewBox={`0 0 ${W} ${H}`} className="mes-chart" role="img" aria-label="Pareto">
            {rows.map((r, i) => {
              const h = (Number(r.qty) / max) * (H - B - 20);
              return (
                <g key={r.code}>
                  <rect x={L + i * bw + 4} y={H - B - h} width={bw - 8} height={h} className="bar"><title>{`${r.code} ${r.name}: ${num(r.qty)}`}</title></rect>
                  <text x={L + i * bw + bw / 2} y={H - B + 16} textAnchor="middle" className="chart-label">{r.code}</text>
                </g>
              );
            })}
            <polyline className="series" points={rows.map((r, i) => `${L + i * bw + bw / 2},${H - B - (r.cumulative_pct / 100) * (H - B - 20)}`).join(" ")} />
          </svg>
        </div>
      )}
      <Table rows={rows} cols={["code", "name", "qty", "events", "cumulative_pct"]} />
    </section>
  );
}

// ======================================================================
// Reports
// ======================================================================
const isoLocal = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

function Reports() {
  const { t } = useApp();
  const [tab, setTab] = useState("oee"),
    [group, setGroup] = useState("operation"),
    [from, setFrom] = useState(isoLocal(new Date(Date.now() - 7 * 864e5))),
    [to, setTo] = useState(isoLocal(new Date()));
  const qs = `?from=${encodeURIComponent(new Date(from).toISOString())}&to=${encodeURIComponent(new Date(to).toISOString())}&group=${group}`;
  const { data, error } = useLoad<Row[]>(`/reports/${tab}${qs}`);
  const [dlErr, setDlErr] = useState("");
  const cols: Record<string, string[]> = {
    oee: ["mes_code", "name", "planned_sec", "run_sec", "breakdown_sec", "processed", "good", "availability", "performance", "quality", "oee"],
    downtime: ["reason_code", "name", "state", "sub_category", "events", "seconds"],
    production: ["key", "records", "input", "output", "defect", "yield", "labor_sec"],
    maintenance: ["mes_code", "name", "breakdowns", "pm_done", "repair_hours", "mttr_hours", "mtbf_hours"],
    "machine-vs-reported": ["mes_code", "machine_count", "reported"],
  };
  const avg = (k: string) => {
    const xs = (data || []).map((r) => r[k]).filter((v) => v !== null && v !== undefined);
    return xs.length ? xs.reduce((a, b) => a + Number(b), 0) / xs.length : null;
  };
  return (
    <>
      <Head title={t("reports")} sub="OEE = A × P × Q">
        <button className="secondary" onClick={() => download(`/reports/${tab}.xlsx${qs}`, `${tab}.xlsx`).catch((e) => setDlErr(e.message))}>
          <Download size={16} />
          {t("exportXlsx")}
        </button>
      </Head>
      <Tabs tabs={["oee", "downtime", "production", "maintenance", "machine-vs-reported"]} value={tab} set={setTab} />
      <div className="panel filters">
        <label className="field"><span>{t("from")}</span><input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="field"><span>{t("to")}</span><input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        {tab === "production" && (
          <label className="field">
            <span>{t("groupBy")}</span>
            <select value={group} onChange={(e) => setGroup(e.target.value)}>
              {["operation", "equipment", "operator", "shift", "day"].map((g) => <option key={g} value={g}>{t(g)}</option>)}
            </select>
          </label>
        )}
      </div>
      <Err error={error || dlErr} />
      {tab === "oee" && data && data.length > 0 && (
        <div className="kpis">
          {["availability", "performance", "quality", "oee"].map((k) => (
            <div className="kpi" key={k}>
              <div>{t(k)}</div>
              <strong>{pct(avg(k))}</strong>
            </div>
          ))}
        </div>
      )}
      {tab === "downtime" && data && data.length > 0 && (
        <section className="panel">
          {data.map((r) => (
            <div className="mes-hbar" key={r.reason_code}>
              <span>{r.reason_code} · {r.name}</span>
              <Bar value={Number(r.seconds) / Math.max(1, ...data.map((x) => Number(x.seconds)))} danger={r.state === "MAINTENANCE"} />
              <b>{hours(r.seconds)}</b>
            </div>
          ))}
        </section>
      )}
      <section className="panel">
        <Table rows={data} cols={cols[tab]} />
      </section>
    </>
  );
}

// ======================================================================
// Maintenance
// ======================================================================
function Maintenance() {
  const { t, catalog, actor } = useApp();
  const [tab, setTab] = useState("orders");
  const orders = useLoad<Row[]>(tab === "orders" ? "/maintenance-orders" : null),
    plans = useLoad<Row[]>(tab === "pmPlans" ? "/pm-plans" : null),
    tools = useLoad<Row[]>(tab === "tools" ? "/tools" : null);
  const cmd = useCmd();
  const [form, setForm] = useState<string | Row>("");
  const write = can(actor, "equipment_status:set");
  return (
    <>
      <Head title={t("maintenance")} sub="MTTR · MTBF · 保養 · 治具">
        {write && tab === "orders" && <button className="primary" onClick={() => setForm("order")}><Plus size={16} />{t("newOrder")}</button>}
        {can(actor, "equipments:manage") && tab === "pmPlans" && <button className="primary" onClick={() => setForm("plan")}><Plus size={16} />{t("newPlan")}</button>}
        {can(actor, "equipments:manage") && tab === "tools" && <button className="primary" onClick={() => setForm("tool")}><Plus size={16} />{t("newTool")}</button>}
      </Head>
      <Tabs tabs={["orders", "pmPlans", "tools"]} value={tab} set={setTab} />
      <Err error={orders.error || plans.error || tools.error || cmd.error} />
      {tab === "orders" && (
        <section className="panel">
          <Table rows={orders.data} cols={["mo_no", "mes_code", "kind", "reason_code", "description", "status", "assignee", "reported_at", "started_at", "finished_at", "findings", "parts_used"]}
            actions={(r) => write && r.status !== "DONE" && (
              <>
                {r.status === "OPEN" && <button className="text-button" disabled={cmd.busy} onClick={() => cmd.send(`/maintenance-orders/${r.id}`, { action: "start" })}>{t("start")}</button>}
                <button className="text-button" onClick={() => setForm({ ...r, _finish: true })}>{t("finish")}</button>
              </>
            )} />
        </section>
      )}
      {tab === "pmPlans" && <section className="panel"><Table rows={plans.data} cols={["mes_code", "name", "interval_days", "last_done_at", "next_due_at", "overdue"]} /></section>}
      {tab === "tools" && (
        <section className="panel">
          <Table rows={tools.data} cols={["code", "name", "kind", "mes_code", "used", "life_limit", "life_used", "status"]}
            actions={(r) => write && (
              <>
                {r.status !== "MOUNTED" && <button className="text-button" onClick={() => setForm({ ...r, _mount: true })}>{t("mount")}</button>}
                {r.status === "MOUNTED" && <button className="text-button" disabled={cmd.busy} onClick={() => cmd.send(`/tools/${encodeURIComponent(r.code)}/step`, { action: "unmount" })}>{t("unmount")}</button>}
                <button className="text-button" disabled={cmd.busy} onClick={() => cmd.send(`/tools/${encodeURIComponent(r.code)}/step`, { action: "reset" })}>{t("reset")}</button>
              </>
            )} />
        </section>
      )}
      {form === "order" && (
        <FormModal title={t("newOrder")} onClose={() => setForm("")}
          fields={[{ name: "equipment_id", type: "select", options: eqOptions(catalog), required: true, label: "equipment" }, { name: "kind", type: "select", options: [["BREAKDOWN", t("BREAKDOWN")], ["PM", t("PM")]], value: "BREAKDOWN" }, { name: "description", type: "textarea", required: true }]}
          onSubmit={(v, c) => c.send("/maintenance-orders", v)} />
      )}
      {form === "plan" && (
        <FormModal title={t("newPlan")} onClose={() => setForm("")}
          fields={[{ name: "equipment_id", type: "select", options: eqOptions(catalog), required: true, label: "equipment" }, { name: "name", required: true }, { name: "interval_days", type: "number", required: true, value: 30 }]}
          onSubmit={(v, c) => c.send("/pm-plans", { ...v, interval_days: Number(v.interval_days) })} />
      )}
      {form === "tool" && (
        <FormModal title={t("newTool")} onClose={() => setForm("")}
          fields={[{ name: "code", required: true }, { name: "name", required: true }, { name: "kind", value: "TOOL" }, { name: "life_limit", type: "number" }]}
          onSubmit={(v, c) => c.send("/tools", { ...clean(v), life_limit: v.life_limit ? Number(v.life_limit) : undefined })} />
      )}
      {typeof form === "object" && form._finish && (
        <FormModal title={`${form.mo_no} · ${t("finish")}`} onClose={() => setForm("")}
          fields={[{ name: "findings", type: "textarea", required: true }, { name: "parts_used", type: "textarea" },
            { name: "restore_reason_code", type: "select", value: "US_1", options: (catalog.equipment_reasons || []).filter((r: Row) => r.state === "ENABLED" && r.code !== "UP").map((r: Row) => [r.code, `${r.code} · ${r.name}`]) }]}
          onSubmit={(v, c) => c.send(`/maintenance-orders/${form.id}`, { action: "finish", findings: v.findings, parts_used: v.parts_used || undefined, restore_reason_code: v.restore_reason_code || null })} />
      )}
      {typeof form === "object" && form._mount && (
        <FormModal title={`${form.code} · ${t("mount")}`} onClose={() => setForm("")}
          fields={[{ name: "equipment_id", type: "select", options: eqOptions(catalog), required: true, label: "equipment" }]}
          onSubmit={(v, c) => c.send(`/tools/${encodeURIComponent(form.code)}/step`, { action: "mount", equipment_id: v.equipment_id })} />
      )}
    </>
  );
}

// ======================================================================
// People: skills and shifts
// ======================================================================
function People() {
  const { t, catalog, actor } = useApp();
  const [tab, setTab] = useState("skills");
  const skills = useLoad<Row[]>("/skills"),
    shifts = useLoad<Row>("/shifts");
  const cmd = useCmd();
  const [form, setForm] = useState("");
  const write = can(actor, "accounts:manage");
  return (
    <>
      <Head title={t("people")} sub={shifts.data?.current?.code ? `${t("currentShift")}: ${shifts.data.current.code} · ${shifts.data.current.name}` : ""}>
        {write && <button className="primary" onClick={() => setForm(tab)}><Plus size={16} />{tab === "skills" ? t("grant") : t("shifts")}</button>}
      </Head>
      <Tabs tabs={["skills", "shifts"]} value={tab} set={setTab} />
      <Err error={skills.error || shifts.error || cmd.error} />
      {tab === "skills" && (
        <section className="panel">
          <Table rows={skills.data} cols={["employee_no", "display_name", "operation_code", "operation_name", "level", "certified_at", "expires_at", "expired"]}
            actions={(r) => write && <button className="text-button" disabled={cmd.busy} onClick={() => cmd.send("/skills", { employee_no: r.employee_no, operation_code: r.operation_code, remove: true })}>{t("delete")}</button>} />
        </section>
      )}
      {tab === "shifts" && <section className="panel"><Table rows={shifts.data?.shifts || null} cols={["code", "name", "start_time", "end_time", "is_active"]} /></section>}
      {form === "skills" && (
        <FormModal title={t("grant")} onClose={() => setForm("")}
          fields={[{ name: "employee_no", type: "select", required: true, options: (catalog.accounts || []).filter((a: Row) => a.employee_no).map((a: Row) => [a.employee_no, `${a.employee_no} · ${a.display_name}`]) },
            { name: "operation_code", type: "select", required: true, options: opOptions(catalog) }, { name: "level", type: "number", value: 1 }, { name: "expires_at", type: "date" }]}
          onSubmit={(v, c) => c.send("/skills", { ...clean(v), level: Number(v.level || 1) })} />
      )}
      {form === "shifts" && (
        <FormModal title={t("shifts")} onClose={() => setForm("")}
          fields={[{ name: "code", required: true }, { name: "name", required: true }, { name: "start_time", required: true, value: "08:00" }, { name: "end_time", required: true, value: "20:00" }, { name: "is_active", type: "checkbox", value: true }]}
          onSubmit={(v, c) => c.send("/shifts", v)} />
      )}
    </>
  );
}

// ======================================================================
// Documents / SOP
// ======================================================================
function Documents() {
  const { t, catalog, actor } = useApp();
  const docs = useLoad<Row[]>("/documents");
  const cmd = useCmd();
  const [form, setForm] = useState(false),
    [err, setErr] = useState("");
  const write = can(actor, "operations:manage");
  return (
    <>
      <Head title={t("documents")} sub="SOP · 作業指導書 · 版本管理">
        {write && <button className="primary" onClick={() => setForm(true)}><Plus size={16} />{t("upload")}</button>}
      </Head>
      <Err error={docs.error || cmd.error || err} />
      <section className="panel">
        <Table rows={docs.data} cols={["doc_no", "version", "title", "operation_code", "item_no", "status", "file_name", "size", "created_at", "released_at"]}
          actions={(r) => (
            <>
              <button className="text-button" onClick={() => openFile(`/documents/${r.id}/file`).catch((e) => setErr(e.message))}>{t("open")}</button>
              {write && r.status === "DRAFT" && <button className="text-button" disabled={cmd.busy} onClick={() => cmd.send(`/documents/${r.id}/release`, {})}>{t("release")}</button>}
            </>
          )} />
      </section>
      {form && (
        <FormModal title={t("upload")} onClose={() => setForm(false)}
          fields={[{ name: "doc_no", required: true }, { name: "version", required: true, value: "A" }, { name: "title", required: true },
            { name: "operation_code", type: "select", options: opOptions(catalog) }, { name: "item_no" }, { name: "file", type: "file", required: true }]}
          onSubmit={(v, c) => {
            const fd = new FormData();
            Object.entries(v).forEach(([k, x]) => x !== "" && x !== null && fd.append(k, x as any));
            return c.send("/documents", fd);
          }} />
      )}
    </>
  );
}

// ======================================================================
// Integration: devices, ERP reconciliation, production gates
// ======================================================================
function Integration() {
  const { t, catalog, actor } = useApp();
  const [tab, setTab] = useState("devices");
  const devs = useLoad<Row[]>(tab === "devices" ? "/devices" : null),
    rec = useLoad<Row>(tab === "erp" ? "/erp/reconcile" : null),
    settings = useLoad<Row>(tab === "gates" ? "/mes/settings" : null);
  const cmd = useCmd();
  const [form, setForm] = useState(false),
    [token, setToken] = useState<Row | null>(null),
    [draft, setDraft] = useState<Row>({});
  useEffect(() => setDraft(settings.data || {}), [settings.data]);
  const write = can(actor, "mes_sync:manage");
  return (
    <>
      <Head title={t("integration")} sub="Tablet · PLC · ERP">
        {write && tab === "devices" && <button className="primary" onClick={() => setForm(true)}><Plus size={16} />{t("newDevice")}</button>}
      </Head>
      <Tabs tabs={["devices", "erp", "gates"]} value={tab} set={setTab} />
      <Err error={devs.error || rec.error || settings.error || cmd.error} />
      {tab === "devices" && (
        <section className="panel">
          <Table rows={devs.data} cols={["name", "kind", "mes_code", "is_active", "created_at", "last_seen_at"]}
            actions={(r) => write && r.is_active && <button className="text-button" disabled={cmd.busy} onClick={() => cmd.send(`/devices/${r.id}/revoke`, {})}>{t("revoke_device")}</button>} />
          <p className="muted">POST /api/machine/telemetry · X-Device-Token · {"{state: RUN|IDLE|DOWN|OFF, counter, good, scrap, params}"}</p>
          <p className="muted">POST /api/erp/work-orders · /api/erp/boms · /api/erp/material-lots · external_id</p>
        </section>
      )}
      {tab === "erp" && rec.data && (
        <>
          <div className="kpis">
            {rec.data.outbox.map((o: Row) => (
              <div className="kpi" key={o.status}>
                <div>{t(o.status)}</div>
                <strong>{num(o.n)}</strong>
              </div>
            ))}
            <div className="kpi"><div>{t("erp_format")}</div><strong>{rec.data.format}</strong></div>
          </div>
          <p className="muted">{t("outboxHint")}</p>
          <section className="panel"><div className="panel-heading"><h2>Outbox · {t("FAILED")}</h2></div><Table rows={rec.data.failed} cols={["id", "event_type", "attempts", "last_error", "created_at"]} /></section>
          <section className="panel"><div className="panel-heading"><h2>ERP → MES</h2></div><Table rows={rec.data.inbound} cols={["kind", "status", "n"]} /><Table rows={rec.data.rejected} cols={["kind", "external_id", "error", "at"]} /></section>
        </>
      )}
      {tab === "gates" && settings.data && (
        <section className="panel">
          <div className="form-grid">
            {["fai_enforced", "skill_enforced", "pm_block_overdue", "material_enforced"].map((k) => (
              <label className="field" key={k}>
                <span>{t(k)}</span>
                <input type="checkbox" disabled={!write} checked={!!draft[k]} onChange={(e) => setDraft({ ...draft, [k]: e.target.checked })} />
              </label>
            ))}
            <label className="field">
              <span>{t("erp_format")}</span>
              <select disabled={!write} value={draft.erp_format || "generic"} onChange={(e) => setDraft({ ...draft, erp_format: e.target.value })}>
                <option value="generic">generic</option>
                <option value="wip">wip (CHECKIN / CHECKOUT)</option>
              </select>
            </label>
          </div>
          <p className="muted">{t("gatesHint")}</p>
          {write && <button className="primary" disabled={cmd.busy} onClick={() => cmd.send("/mes/settings", draft, "PUT")}>{t("save")}</button>}
        </section>
      )}
      {form && (
        <FormModal title={t("newDevice")} onClose={() => setForm(false)}
          fields={[{ name: "name", required: true }, { name: "kind", type: "select", required: true, options: ["TABLET", "MACHINE", "ERP"].map((k) => [k, t(k)]) },
            { name: "equipment_mes_code", type: "select", options: (catalog.equipments || []).map((e: Row) => [e.mes_code, e.mes_code]) }]}
          onSubmit={async (v, c) => {
            const r = await c.send("/devices", clean(v));
            if (r?.token) setToken(r);
            return r;
          }} />
      )}
      {token && (
        <Modal title={`${token.name} · ${t(token.kind)}`} onClose={() => setToken(null)}>
          <div className="warning">{t("tokenOnce")}</div>
          <pre className="token-box">{token.token}</pre>
        </Modal>
      )}
    </>
  );
}

export const pages: Record<string, React.ComponentType> = {
  workorders: WorkOrders,
  schedule: Schedule,
  materials: Materials,
  trace: Trace,
  quality: Quality,
  reports: Reports,
  maintenance: Maintenance,
  people: People,
  documents: Documents,
  integration: Integration,
};

// ======================================================================
// Tablet / card extras: SOP, BOM issue, first article, plan, serials, label
// ======================================================================
export function CardExtras({ card }: { card: Row }) {
  const { t, catalog, actor } = useApp();
  const { data, error } = useLoad<Row>(`/cards/${card.id}/extras`);
  const cmd = useCmd();
  const [lot, setLot] = useState(""),
    [qty, setQty] = useState(""),
    [fai, setFai] = useState(false),
    [err, setErr] = useState("");
  if (!data) return error ? <Err error={error} /> : null;
  const admin = actor.mode === "admin";
  const eligible = (catalog.equipment_operations || []).filter((x: Row) => x.operation_id === data.operation?.id).map((x: Row) => x.equipment_id);
  const show = data.docs.length || data.bom.length || data.fai_required || data.plan.length || admin;
  if (!show) return null;
  async function issue(e: React.FormEvent) {
    e.preventDefault();
    if (await cmd.send(`/cards/${card.id}/issue-material`, { lot_no: lot.trim(), qty: Number(qty) })) {
      setLot("");
      setQty("");
    }
  }
  return (
    <section className="panel card-extras">
      <Err error={cmd.error || err} />
      {data.plan.length > 0 && (
        <p className="muted">
          {t("plan")}: {data.plan.map((p: Row) => `${p.mes_code} ${when(p.planned_start)} → ${when(p.planned_end)}`).join(" · ")}
        </p>
      )}
      {data.docs.length > 0 && (
        <div className="extras-row">
          <h3>{t("sop")}</h3>
          {data.docs.map((d: Row) => (
            <button key={d.id} className="secondary" onClick={() => openFile(`/documents/${d.id}/file`).catch((e) => setErr(e.message))}>
              <FileText size={16} />
              {d.doc_no} {d.version} · {d.title}
            </button>
          ))}
        </div>
      )}
      {data.bom.length > 0 && (
        <div>
          <h3>BOM · {data.operation?.code}</h3>
          <Table rows={data.bom.map((b: Row) => ({ ...b, progress_pct: Number(b.issued) / Math.max(1e-9, Number(b.required)) }))} cols={["code", "name", "unit", "required", "issued", "progress_pct"]} />
          {card.status === "IN_PROGRESS" && (
            <form className="issue-form" onSubmit={issue}>
              <input value={lot} onChange={(e) => setLot(e.target.value)} placeholder={t("issueMaterial")} required />
              <input type="number" step="any" min="0" value={qty} onChange={(e) => setQty(e.target.value)} placeholder="Qty" required />
              <button className="primary" disabled={cmd.busy}>{t("issued")}</button>
            </form>
          )}
        </div>
      )}
      {data.fai_required && (
        <div>
          <h3>{t("fai")}</h3>
          <p className="muted">{t("faiRequired")} · {data.shift.code}</p>
          <Table rows={data.first_articles} cols={["mes_code", "result", "note", "created_at", "approved_at"]} />
          {card.status === "WAITING" && <button className="secondary" onClick={() => setFai(true)}>{t("submitFai")}</button>}
        </div>
      )}
      {admin && (
        <div className="extras-row">
          <span className="muted">{t("serials")}: {data.serials}</span>
          {can(actor, "run_cards:manage") && card.status !== "COMPLETED" && (
            <button className="secondary" disabled={cmd.busy} onClick={() => cmd.send(`/cards/${card.id}/serials`, {})}>{t("makeSerials")}</button>
          )}
          <button className="secondary" onClick={() => download(`/labels/card/${card.id}`, `${card.card_no}.zpl`).catch((e) => setErr(e.message))}>{t("label")}</button>
        </div>
      )}
      {fai && (
        <FormModal title={t("submitFai")} onClose={() => setFai(false)}
          fields={[{ name: "equipment_id", type: "select", required: true, label: "equipment", options: eqOptions(catalog).filter(([id]) => eligible.includes(id)) },
            { name: "result", type: "select", required: true, value: "PASS", options: [["PASS", t("PASS")], ["FAIL", t("FAIL")]] }, { name: "note", type: "textarea" }]}
          onSubmit={(v, c) => c.send("/first-articles", { ...clean(v), operation_id: data.operation.id, run_card_id: card.id })} />
      )}
    </section>
  );
}

export function EquipmentExtras({ id }: { id: string }) {
  const { t } = useApp();
  const queue = useLoad<Row[]>(`/dispatch/${id}`),
    sig = useLoad<Row[]>(`/equipments/${id}/telemetry`);
  const last = sig.data?.[0];
  if (!queue.data?.length && !last) return null;
  return (
    <section className="panel">
      {last && (
        <p className="muted">
          {t("telemetry")}: {last.state || "—"} · counter {num(last.counter)} · {when(last.at)}
        </p>
      )}
      {queue.data && queue.data.length > 0 && (
        <>
          <div className="panel-heading"><h2>{t("dispatch")}</h2></div>
          <Table rows={queue.data} cols={["card_no", "operation_code", "current_qty", "wo_no", "planned_start", "planned_end", "late"]} />
        </>
      )}
    </section>
  );
}
