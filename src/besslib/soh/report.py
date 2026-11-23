"""One self-contained HTML page for an Annex VII record.

``report`` writes the record's ten fields as a table, the method and limits of each,
the data credit and both configuration hashes into one HTML file: inline style, no
scripts, no external files. Given a ``history`` (records at earlier dates), it adds one
small figure per field with numbers, embedded as PNG; the figures need matplotlib.

The same record gives the same bytes: the page holds no time of writing, and the PNG
files carry no software stamp.
"""

from __future__ import annotations

import base64
import io
import math
from html import escape
from pathlib import Path

import pandas as pd

from besslib.soh.record import AnnexVII

NOT_A_CERTIFICATE = (
    "It computes the Annex VII parameters; it does not certify them. It is not a compliance certificate and not "
    "legal advice."
)
NO_MATPLOTLIB = "figures need matplotlib (pip install besslib[plot])"
NO_HISTORY = "A history (records at earlier dates, history=) adds one figure per field."

_COLUMNS = ("Code", "Annex VII field", "besslib label", "Value", "Low", "High", "Unit", "Events", "Status")
_PNG_METADATA = {"Software": None}  # no version stamp: the same figure gives the same bytes
_LINE = "#2a78d6"
_INK = "#0b0b0b"
_INK2 = "#52514e"
_GRID = "#e4e3df"
_SURFACE = "#fcfcfb"

_STYLE = """
:root { color-scheme: light; --bg: #fcfcfb; --surface: #f3f2ee; --ink: #0b0b0b; --ink2: #52514e;
  --rule: #d9d8d2; --accent: #2a78d6; --estimated: #1d6b3a; --absent: #8a4b00; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { color-scheme: dark; --bg: #1a1a19; --surface: #242422; --ink: #f0efec;
    --ink2: #c3c2b7; --rule: #3a3a37; --accent: #3987e5; --estimated: #7fd19c; --absent: #f0b46a; }
}
:root[data-theme="dark"] { color-scheme: dark; --bg: #1a1a19; --surface: #242422; --ink: #f0efec;
  --ink2: #c3c2b7; --rule: #3a3a37; --accent: #3987e5; --estimated: #7fd19c; --absent: #f0b46a; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 72rem; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.5rem; line-height: 1.25; margin: 0 0 4px; }
h2 { font-size: 1.15rem; margin: 32px 0 8px; border-bottom: 1px solid var(--rule); padding-bottom: 4px; }
h3 { font-size: 1rem; margin: 20px 0 4px; }
p { margin: 4px 0; }
.sub { color: var(--ink2); margin: 0 0 16px; }
.notice { background: var(--surface); border-left: 3px solid var(--accent); padding: 8px 12px; margin: 16px 0; }
dl.facts { display: grid; grid-template-columns: max-content 1fr; gap: 2px 16px; margin: 8px 0; }
dl.facts dt { color: var(--ink2); }
dl.facts dd { margin: 0; overflow-wrap: anywhere; }
.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
caption { text-align: left; color: var(--ink2); padding: 0 0 6px; }
th, td { text-align: left; vertical-align: top; padding: 6px 8px; border-bottom: 1px solid var(--rule); }
th { font-weight: 600; background: var(--surface); }
td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
td.estimated { color: var(--estimated); }
td.absent { color: var(--absent); }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.9em; }
.field { margin: 0 0 12px; }
.label { color: var(--ink2); }
figure { margin: 12px 0; }
figure img { display: block; max-width: 100%; height: auto; border-radius: 6px; background: #fcfcfb; }
figcaption { color: var(--ink2); font-size: 0.85rem; margin-top: 4px; }
footer { margin-top: 40px; color: var(--ink2); font-size: 0.85rem; border-top: 1px solid var(--rule);
  padding-top: 12px; overflow-wrap: anywhere; }
"""


def _num(value) -> str:
    """A number to 4 significant digits, without an exponent from 1e-4 to 1e9; blank for NaN or None."""
    if value is None:
        return ""
    v = float(value)
    if not math.isfinite(v):
        return ""
    if v == 0 or not 1e-4 <= abs(v) < 1e9:
        return f"{v:.4g}"
    decimals = 3 - math.floor(math.log10(abs(v)))
    text = f"{round(v, decimals):.{max(decimals, 0)}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _date(stamp) -> str:
    return str(pd.Timestamp(stamp))


def _status_class(status: str) -> str:
    return "estimated" if status == "estimated" else "absent" if status.startswith("not possible") else ""


def _table(record: AnnexVII) -> str:
    head = "".join(f'<th scope="col">{escape(c)}</th>' for c in _COLUMNS)
    rows = []
    for f in record.fields:
        cls = f' class="{_status_class(f.status)}"' if _status_class(f.status) else ""
        rows.append(
            "<tr>"
            f'<th scope="row"><code>{escape(f.code)}</code></th>'
            f"<td>{escape(f.field)}</td>"
            f"<td>{escape(f.label)}</td>"
            f'<td class="num">{_num(f.value)}</td>'
            f'<td class="num">{_num(f.low)}</td>'
            f'<td class="num">{_num(f.high)}</td>'
            f"<td>{escape(f.unit)}</td>"
            f'<td class="num">{_num(f.events)}</td>'
            f"<td{cls}>{escape(f.status)}</td>"
            "</tr>"
        )
    caption = f"The ten parameters of Annex VII. Bands at the level {_num(record.level)}"
    a1_level = next((f.details.get("band_level") for f in record.fields if f.code == "A1"), None)
    caption += f"; A1 at {_num(a1_level)}." if a1_level is not None else "."
    return (
        f'<div class="scroll"><table><caption>{escape(caption)}</caption>'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _texts(details: dict) -> list[tuple[str, str]]:
    """The texts of a field's details that say what it is and where it stops."""
    out = []
    for key, name in (("what_it_is", "What it is"), ("what_it_holds", "What it holds"), ("limits", "Limits")):
        text = details.get(key)
        if isinstance(text, str) and text:
            out.append((name, text))
    for key in ("date_of_manufacture", "date_put_into_service"):
        if key in details:
            value = details[key]
            shown = "" if value is None else str(value)
            status = details.get(f"{key}_status", "")
            text = "; ".join(t for t in (shown, status) if t)
            out.append((key.replace("_", " ").capitalize(), text))
    return out


def _methods(record: AnnexVII) -> str:
    parts = []
    for f in record.fields:
        rows = [("Annex VII field", f.field), ("Method and source", f.method), *_texts(f.details), ("Status", f.status)]
        facts = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)
        parts.append(
            f'<section class="field"><h3><code>{escape(f.code)}</code> '
            f'<span class="label">{escape(f.label)}</span></h3><dl class="facts">{facts}</dl></section>'
        )
    return "".join(parts)


def _series(records: list[AnnexVII], code: str) -> pd.DataFrame:
    rows = []
    for r in records:
        f = r.field(code)
        rows.append({"date": pd.Timestamp(r.date), "value": f.value, "low": f.low, "high": f.high, "unit": f.unit})
    return pd.DataFrame(rows)


def _figure(series: pd.DataFrame, code: str, unit: str) -> str:
    """One field over the dates as base64 PNG: the value as a line, the band as a shaded area."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(6.4, 2.6), dpi=110, facecolor=_SURFACE)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot()
    ax.set_facecolor(_SURFACE)
    x = [pd.Timestamp(d).to_pydatetime() for d in series["date"]]
    band = series["low"].notna() & series["high"].notna()
    if band.any():
        ax.fill_between(x, series["low"], series["high"], where=band.to_numpy(), color=_LINE, alpha=0.18,
                        linewidth=0)
        # a whisker per date, so that a date between dates without numbers still shows its band
        ax.vlines([d for d, ok in zip(x, band) if ok], series["low"][band], series["high"][band], color=_LINE,
                  alpha=0.5, linewidth=1.5)
    ax.plot(x, series["value"], color=_LINE, linewidth=2, marker="o", markersize=5)
    ax.set_title(code, loc="left", fontsize=9, color=_INK)
    ax.set_ylabel(unit, fontsize=8, color=_INK2)
    ax.tick_params(labelsize=8, colors=_INK2)
    ax.grid(axis="y", color=_GRID, linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(_GRID)
    fig.autofmt_xdate()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", metadata=_PNG_METADATA, facecolor=_SURFACE)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _history_table(series: pd.DataFrame) -> str:
    rows = "".join(
        f"<tr><td>{escape(_date(d))}</td><td class=\"num\">{_num(v)}</td><td class=\"num\">{_num(lo)}</td>"
        f"<td class=\"num\">{_num(hi)}</td></tr>"
        for d, v, lo, hi in zip(series["date"], series["value"], series["low"], series["high"])
    )
    return (
        '<div class="scroll"><table><thead><tr><th scope="col">Date</th><th scope="col">Value</th>'
        f'<th scope="col">Low</th><th scope="col">High</th></tr></thead><tbody>{rows}</tbody></table></div>'
    )


def _history(record: AnnexVII, history) -> str:
    if history is None:
        return f"<p>{escape(NO_HISTORY)}</p>"
    by_date = {pd.Timestamp(r.date): r for r in history}
    by_date.setdefault(pd.Timestamp(record.date), record)
    records = [by_date[d] for d in sorted(by_date)]
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        plot = False
    else:
        plot = True
    first, last = _date(records[0].date), _date(records[-1].date)
    parts = [f"<p>{escape(f'{len(records)} dates, {first} to {last}.')}</p>"]
    if not plot:
        parts.append(f"<p>{escape(NO_MATPLOTLIB)}</p>")
    for f in record.fields:
        series = _series(records, f.code)
        if not series["value"].map(lambda v: v is not None and math.isfinite(float(v))).any():
            continue
        parts.append(f"<h3><code>{escape(f.code)}</code> <span class=\"label\">{escape(f.label)}</span></h3>")
        if plot:
            png = _figure(series, f.code, f.unit)
            alt = f"{f.code} {f.label} over {len(records)} dates, in {f.unit}, with its band"
            parts.append(
                f"<figure><img src=\"data:image/png;base64,{png}\" alt=\"{escape(alt)}\" width=\"704\" "
                f"height=\"286\"><figcaption>{escape(f'Value and band, in {f.unit}')}</figcaption></figure>"
            )
        parts.append(_history_table(series))
    return "".join(parts)


def report(record: AnnexVII, path, history=None) -> Path:
    """Write the record as one self-contained HTML page at ``path``; return the path.

    Parameters
    ----------
    record
        An ``AnnexVII`` record (``besslib.soh.annex_vii``).
    path
        The HTML file to write.
    history
        Optional records of the same battery at earlier dates (for example year ends).
        With matplotlib installed, one figure per field that has numbers over those
        dates is embedded as PNG; without it, the page says so.
    """
    path = Path(path)
    name = record.pack.get("name") or "battery"
    when = _date(record.date)
    title = f"Annex VII record: {name}, {when}"
    facts = [
        ("Pack", name),
        ("Chemistry", str(record.pack.get("chemistry") or "")),
        ("Record date", when),
        ("Reference capacity", f"{_num(record.reference_capacity_ah)} Ah"),
        ("Configuration hash, capacity", record.capacity_config_hash),
        ("Configuration hash, record", record.record_config_hash),
        ("Annex VII wording", record.annex_vii_version),
    ]
    facts_html = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in facts if v)
    credit = (
        f"<footer><p><strong>Data credit.</strong> {escape(record.data_credit)}</p></footer>"
        if record.data_credit else ""
    )
    page = (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n<style>{_STYLE}</style>\n</head>\n<body>\n<main>\n"
        f"<h1>Annex VII record: {escape(name)}</h1>\n"
        f'<p class="sub">At {escape(when)}. Made with besslib.</p>\n'
        f'<p class="notice">{escape(NOT_A_CERTIFICATE)}</p>\n'
        f'<dl class="facts">{facts_html}</dl>\n'
        f"<h2>The ten parameters</h2>\n{_table(record)}\n"
        f"<h2>Over time</h2>\n{_history(record, history)}\n"
        f"<h2>Methods and limits</h2>\n{_methods(record)}\n"
        f"{credit}\n</main>\n</body>\n</html>\n"
    )
    path.write_text(page, encoding="utf-8")
    return path
