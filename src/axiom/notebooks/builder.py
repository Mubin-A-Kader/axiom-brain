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
    sql: str = "",
    result: Any,
    insight: str | None = None,
    python_code: str | None = None,
) -> tuple[Dict[str, Any], List[str]]:
    if isinstance(result, dict) and "columns" in result and "rows" in result:
        columns = result.get("columns", [])
        rows = result.get("rows", [])
        records = [dict(zip(columns, row)) for row in rows]
    else:
        # App Connector / JSON fallback path
        records = result if isinstance(result, list) else [result]

    # Base64-encode the data so the embedded literal is always safe
    # regardless of quotes, backslashes, or unicode in the values.
    data_b64 = base64.b64encode(
        json.dumps(records, default=str).encode()
    ).decode()

    summaries = [
        "Load query result into a pandas DataFrame.",
        "Execute AI-generated dynamic analysis code.",
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
    import plotly.graph_objects as go
    _PLOTLY = True
except ImportError:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _PLOTLY = False

records = json.loads(base64.b64decode({data_b64!r}).decode())
df = pd.DataFrame(records)

def _show_plotly(fig):
    fig.update_layout(font_color="#E6E1D8")
    _display(HTML(fig.to_html(include_plotlyjs="cdn", full_html=False)))
"""

    # Use LLM-generated code if provided, else fallback to a minimal categorical distribution
    chart_code = python_code if python_code else """\
# Fallback: simple categorical distribution
cat_cols = [c for c in df.columns if df[c].nunique() <= 20]
if cat_cols:
    col = cat_cols[0]
    counts = df[col].value_counts().reset_index()
    counts.columns = [col, "count"]
    if _PLOTLY:
        _show_plotly(px.bar(counts, x=col, y="count", title=f"Distribution of {col}"))
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
