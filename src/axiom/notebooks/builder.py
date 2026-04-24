import base64
import json
from typing import Any, Dict, List


def _cell(cell_type: str, source: str, **extra: Any) -> Dict[str, Any]:
    return {
        "cell_type": cell_type,
        "metadata": {},
        "source": source,
        **extra,
    }


def build_analysis_notebook(
    *,
    question: str,
    sql: str,
    result: Dict[str, Any],
    insight: str | None = None,
) -> tuple[Dict[str, Any], List[str]]:
    columns = result.get("columns", [])
    rows = result.get("rows", [])
    records = [dict(zip(columns, row)) for row in rows]

    # Base64-encode the data so the embedded literal is always safe
    # regardless of quotes, backslashes, or unicode in the values.
    data_b64 = base64.b64encode(
        json.dumps(records, default=str).encode()
    ).decode()

    summaries = [
        "Load query result into a pandas DataFrame.",
        "Generate interactive chart from the data.",
    ]

    intro = f"# {question}\n"
    if insight:
        intro += f"\n{insight}\n"

    setup_code = f"""\
import base64, json
import pandas as pd
import numpy as np
from IPython.display import HTML, display as _display

try:
    import plotly.express as px
    _PLOTLY = True
except ImportError:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _PLOTLY = False

records = json.loads(base64.b64decode({data_b64!r}).decode())
df = pd.DataFrame(records)
df = df.replace(["missing", ""], pd.NA)
for col in df.columns:
    try:
        df[col] = pd.to_numeric(df[col])
    except (ValueError, TypeError):
        pass
"""

    chart_code = """\
def _is_id(name):
    n = name.lower()
    return n in ("id",) or n.endswith("id") or n.endswith("_id") or n.endswith("uuid")

numeric_cols = [c for c in df.select_dtypes(include=[np.number]).columns if not _is_id(c)]
cat_cols = [
    c for c in df.columns
    if c not in numeric_cols and not _is_id(c) and 1 < df[c].nunique() <= 40
]

_COLORS = ["#638A70", "#8FB4A0", "#C26D5C", "#D4A04A", "#6B8BBE"]
_MARGIN = dict(l=24, r=24, t=44, b=40)
_LAYOUT = dict(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(30,30,28,1)",
               font_color="#E6E1D8", margin=_MARGIN)

def _show_plotly(fig):
    fig.update_layout(**_LAYOUT)
    _display(HTML(fig.to_html(include_plotlyjs="cdn", full_html=False)))

def _show_mpl(title=""):
    plt.title(title)
    plt.tight_layout()
    plt.show()

charts = 0

if _PLOTLY:
    if numeric_cols and cat_cols:
        x, y = cat_cols[0], numeric_cols[0]
        plot_df = df[[x, y]].dropna().head(50)
        if not plot_df.empty:
            _show_plotly(px.bar(plot_df, x=x, y=y, title=f"{y} by {x}",
                                color_discrete_sequence=_COLORS))
            charts += 1

    if numeric_cols and charts < 2:
        col = numeric_cols[1 if charts > 0 and len(numeric_cols) > 1 else 0]
        _show_plotly(px.histogram(df[[col]].dropna(), x=col,
                                  title=f"Distribution — {col}",
                                  color_discrete_sequence=_COLORS))
        charts += 1

    if not numeric_cols and cat_cols:
        for col in cat_cols[:3]:
            counts = df[col].value_counts().head(15).reset_index()
            counts.columns = [col, "count"]
            if len(counts) < 2:
                continue
            _show_plotly(px.bar(counts, x=col, y="count",
                                title=f"Distribution — {col}",
                                color_discrete_sequence=_COLORS))
            charts += 1
else:
    # Matplotlib fallback when plotly is unavailable
    if numeric_cols and cat_cols:
        x, y = cat_cols[0], numeric_cols[0]
        plot_df = df[[x, y]].dropna().head(50)
        if not plot_df.empty:
            ax = plot_df.plot(kind="bar", x=x, y=y, color="#638A70",
                              legend=False, figsize=(10, 4))
            _show_mpl(f"{y} by {x}")
    elif numeric_cols:
        col = numeric_cols[0]
        df[[col]].dropna().plot(kind="hist", figsize=(10, 4), color="#638A70")
        _show_mpl(f"Distribution — {col}")
    elif cat_cols:
        for col in cat_cols[:2]:
            counts = df[col].value_counts().head(15)
            if len(counts) < 2:
                continue
            counts.plot(kind="bar", figsize=(10, 4), color="#638A70")
            _show_mpl(f"Distribution — {col}")
"""

    cells = [
        _cell("markdown", intro),
        _cell("code", setup_code, execution_count=None, outputs=[]),
        _cell("code", chart_code, execution_count=None, outputs=[]),
    ]

    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "pygments_lexer": "ipython3",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return notebook, summaries
