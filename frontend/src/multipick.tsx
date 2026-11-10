// Multi-select for configuration forms. A short list stays a plain row of checkboxes; a long one
// (defect codes, permissions, reason codes...) gets a search box, the current selection as removable
// chips and a fixed-height scrolling list, so 100+ options never turn the dialog into a wall of boxes.
import React, { useState } from "react";
import { Search, X } from "lucide-react";
import { Row } from "./api";
import { useApp } from "./ctx";
import "./multipick.css";

const COMPACT_ABOVE = 8;
const LABELS: Record<string, [string, string]> = {
  search: ["Tìm theo mã hoặc tên…", "依代碼或名稱搜尋…"],
  selected: ["Đã chọn", "已選"],
  none: ["Chưa chọn mục nào", "尚未選擇"],
  noMatch: ["Không có mục nào khớp", "沒有符合的項目"],
  addShown: ["Chọn hết kết quả", "全選結果"],
  dropShown: ["Bỏ chọn kết quả", "取消結果"],
  remove: ["Bỏ", "移除"],
  off: ["ngừng dùng", "停用"],
};

export function MultiPick({
  label,
  options,
  value,
  onChange,
  idKey = "id",
  nameKey = "name",
}: {
  label: string;
  options: Row[];
  value: any[];
  onChange: (next: any[]) => void;
  idKey?: string;
  nameKey?: string;
}) {
  const { lang } = useApp();
  const [q, setQ] = useState("");
  const tr = (k: string) => LABELS[k][lang === "vi" ? 0 : 1];
  // "CODE · name" when the option has its own code; just the name otherwise (permissions, accounts).
  const text = (o: Row) =>
    o.code && o.code !== o[nameKey] ? `${o.code} · ${o[nameKey]}` : String(o[nameKey]);
  const picked = new Set(value);
  const toggle = (id: any) =>
    onChange(picked.has(id) ? value.filter((x) => x !== id) : [...value, id]);
  const box = (o: Row) => (
    <label className={"checkbox" + (o.is_active === false ? " off" : "")} key={o[idKey]} title={text(o)}>
      <input type="checkbox" checked={picked.has(o[idKey])} onChange={() => toggle(o[idKey])} />
      <span>
        {text(o)}
        {o.is_active === false && ` (${tr("off")})`}
      </span>
    </label>
  );
  if (options.length <= COMPACT_ABOVE)
    return (
      <div className="wide multiselect">
        <h3>{label}</h3>
        {options.map(box)}
      </div>
    );

  const needle = q.trim().toLowerCase();
  const shown = needle ? options.filter((o) => text(o).toLowerCase().includes(needle)) : options;
  const shownIds = shown.map((o) => o[idKey]);
  const chosen = options.filter((o) => picked.has(o[idKey]));
  const allShownPicked = shown.length > 0 && shownIds.every((id) => picked.has(id));
  return (
    <div className="wide multiselect multipick">
      <div className="multipick-head">
        <h3>{label}</h3>
        <span className="multipick-count">
          {tr("selected")} <b>{chosen.length}</b> / {options.length}
        </span>
      </div>
      <div className="multipick-chips">
        {chosen.length === 0 && <span className="muted">{tr("none")}</span>}
        {chosen.map((o) => (
          <button
            type="button"
            className="multipick-chip"
            key={o[idKey]}
            title={`${tr("remove")}: ${text(o)}`}
            onClick={() => toggle(o[idKey])}
          >
            {o.code && o.code !== o[nameKey] ? o.code : o[nameKey]}
            <X size={12} />
          </button>
        ))}
      </div>
      <div className="multipick-tools">
        <label className="multipick-search">
          <Search size={15} />
          <input
            type="search"
            value={q}
            placeholder={tr("search")}
            aria-label={tr("search")}
            onChange={(e) => setQ(e.target.value)}
            // Enter here must not submit the surrounding configuration form.
            onKeyDown={(e) => e.key === "Enter" && e.preventDefault()}
          />
        </label>
        <button
          type="button"
          className="text-button"
          disabled={!shown.length}
          onClick={() =>
            onChange(
              allShownPicked
                ? value.filter((id) => !shownIds.includes(id))
                : [...value, ...shownIds.filter((id) => !picked.has(id))],
            )
          }
        >
          {tr(allShownPicked ? "dropShown" : "addShown")} ({shown.length})
        </button>
      </div>
      <div className="multipick-list">
        {shown.map(box)}
        {!shown.length && <span className="muted">{tr("noMatch")}</span>}
      </div>
    </div>
  );
}
