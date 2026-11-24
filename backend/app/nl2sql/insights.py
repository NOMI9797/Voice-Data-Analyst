"""The few facts worth saying out loud about a result, computed exactly.

A listener can't take in a table. What matters is the headline: "sales peaked
in March, about 20% above average", "Rock is a third of all tracks". The LLM
is bad at that arithmetic and only sees the first rows, so the facts are
computed here over ALL rows, then handed to the answer writer as the numbers
to use (and to the no-LLM template as its sentence).
"""

from __future__ import annotations

import math
import re

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def human(name: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").strip().lower()
    return re.sub(r" name$", "", words)


def say_number(v: float) -> str:
    """Rounded the way a person says it: 1,297 / 4.09 / about 1.3 million."""
    a = abs(v)
    if a >= 1_000_000_000:
        return f"about {v / 1e9:.1f} billion"
    if a >= 1_000_000:
        return f"about {v / 1e6:.1f} million"
    if a >= 10_000:
        return f"about {round(v, -2):,.0f}"
    if a >= 100 or float(v).is_integer():
        return f"{v:,.0f}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def say_time(v) -> str:
    """'2017-11-01' -> 'November 2017', '2017-11' -> 'November 2017', 2017 -> '2017'."""
    s = str(v)
    m = re.match(r"^(\d{4})-(\d{2})(?:-01)?(?:[ T]00:00:00.*)?$", s)
    if m:
        return f"{MONTHS[int(m.group(2)) - 1]} {m.group(1)}"
    return re.sub(r"[ T]00:00:00.*$", "", s)


def pct(a: float, b: float) -> str:
    return f"{abs(a - b) / abs(b) * 100:.0f}%" if b else ""


def _pairs(columns, rows, x, y):
    xi, yi = columns.index(x), columns.index(y)
    return [(r[xi], r[yi]) for r in rows if _num(r[yi])]


def facts(columns: list[str], rows: list[list], chart: dict) -> list[str]:
    """Short factual sentences, most important first. Empty when nothing fits."""
    t, x, ys, series = chart.get("type"), chart.get("x"), chart.get("y") or [], chart.get("series")
    out: list[str] = []
    if not rows:
        return ["The query returned no rows."]
    try:
        if t == "kpi":
            nums = [(y, rows[0][columns.index(y)]) for y in ys if _num(rows[0][columns.index(y)])]
            if x and nums:  # "which genre has the most tracks" -> "Rock, with 1,297 total tracks."
                y, v = nums.pop(0)
                out.append(f"{rows[0][columns.index(x)]}, with {say_number(v)} {human(y)}.")
            out += [f"{human(y).capitalize()}: {say_number(v)}." for y, v in nums]
            return out

        if not x or not ys:
            return [f"{len(rows)} rows."]
        y = ys[0]
        temporal = chart.get("column_types", {}).get(x) == "temporal"

        if series:  # one line or bar group per member: who leads overall?
            si, yi = columns.index(series), columns.index(y)
            totals: dict = {}
            for r in rows:
                if _num(r[yi]):
                    totals[r[si]] = totals.get(r[si], 0) + r[yi]
            ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
            if ranked:
                out.append(f"{ranked[0][0]} leads overall with {say_number(ranked[0][1])} {human(y)}"
                           + (f", ahead of {ranked[1][0]} ({say_number(ranked[1][1])})." if len(ranked) > 1 else "."))
            return out

        pairs = _pairs(columns, rows, x, y)
        if not pairs:
            return out
        vals = [v for _, v in pairs]
        avg = sum(vals) / len(vals)

        if t == "scatter":
            xs = [a for a, _ in pairs if _num(a)]
            if len(xs) == len(vals) and len(xs) > 2:
                mx, my = sum(xs) / len(xs), avg
                cov = sum((a - mx) * (b - my) for a, b in zip(xs, vals))
                sx = math.sqrt(sum((a - mx) ** 2 for a in xs))
                sy = math.sqrt(sum((b - my) ** 2 for b in vals))
                if sx and sy:
                    r = cov / (sx * sy)
                    strength = "strongly" if abs(r) > 0.7 else "somewhat" if abs(r) > 0.3 else "barely"
                    direction = "together" if r > 0 else "in opposite directions"
                    out.append(f"{human(x).capitalize()} and {human(y)} move {strength} {direction} "
                               f"(correlation {r:.2f}).")
            return out

        if temporal or t == "line":
            ordered = sorted(pairs, key=lambda p: str(p[0]))
            label = say_time if temporal else str
            # Partial periods at either end (data starts or stops mid-month) distort peak,
            # average and trend: leave out end periods below 10% of the typical value.
            core, notes = ordered, []
            if len(ordered) >= 4:
                typical = sorted(v for _, v in ordered)[len(ordered) // 2]
                if typical > 0:
                    while len(core) > 3 and core[-1][1] < 0.1 * typical:
                        core = core[:-1]
                    while len(core) > 3 and core[0][1] < 0.1 * typical:
                        core = core[1:]
                    if core[-1] is not ordered[-1]:
                        dropped = ordered[ordered.index(core[-1]) + 1:]
                        when = label(dropped[0][0]) if len(dropped) == 1 else \
                            f"{label(dropped[0][0])} to {label(dropped[-1][0])}"
                        notes.append(f"The last period{'s' if len(dropped) > 1 else ''}, {when}, "
                                     f"{'are' if len(dropped) > 1 else 'is'} far below the rest, so the data "
                                     "there may be incomplete.")
            cvals = [v for _, v in core]
            avg = sum(cvals) / len(cvals)
            peak = max(core, key=lambda p: p[1])
            low = min(core, key=lambda p: p[1])
            above = pct(peak[1], avg)
            out.append(f"{human(y).capitalize()} peaked in {label(peak[0])} at {say_number(peak[1])}"
                       + (f", about {above} above the average of {say_number(avg)}." if avg > 0 and above != "0%" else "."))
            first, last = core[0], core[-1]
            if len(core) >= 3 and first[1]:
                if last[1] == first[1]:
                    out.append(f"It was {say_number(first[1])} in both {label(first[0])} and {label(last[0])}.")
                else:
                    direction = "rose" if last[1] > first[1] else "fell"
                    out.append(f"From {label(first[0])} to {label(last[0])} it {direction} from "
                               f"{say_number(first[1])} to {say_number(last[1])}.")
            if low is not peak and low is not first and low is not last:
                out.append(f"The lowest was {label(low[0])} at {say_number(low[1])}.")
            out += notes
            return out

        # categories (bar)
        ranked = sorted(pairs, key=lambda p: p[1], reverse=True)
        top = ranked[0]
        total = sum(vals)
        line = f"{top[0]} is highest with {say_number(top[1])} {human(y)}"
        if len(ranked) > 1 and all(v >= 0 for v in vals) and total > 0:
            share = top[1] / total * 100
            if share >= 5:
                line += f", {share:.0f}% of the total across the {len(ranked)} shown"
        out.append(line + ".")
        if len(ranked) > 1 and ranked[1][1] > 0:
            ratio = top[1] / ranked[1][1]
            if ratio >= 1.5:
                out.append(f"That is {ratio:.1f} times the next one, {ranked[1][0]} ({say_number(ranked[1][1])}).")
            else:
                out.append(f"Next is {ranked[1][0]} with {say_number(ranked[1][1])}.")
        if len(ranked) > 2:
            out.append(f"Lowest is {ranked[-1][0]} with {say_number(ranked[-1][1])} {human(y)}.")
        return out
    except (ValueError, TypeError, ZeroDivisionError):
        return out
