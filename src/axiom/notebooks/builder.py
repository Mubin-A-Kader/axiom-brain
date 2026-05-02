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

    # Strip any residual system-command prefix so the notebook title is always
    # the clean human question, not an internal routing command.
    _system_prefixes = (
        "CONFIRMED_SOURCE:", "CONFIRMED_SOURCES:", "CONFIRMED_DATABASE:",
        "REJECTED_INTENT:", "CLARIFIED_INTENT:",
    )
    _display_question = question
    for _prefix in _system_prefixes:
        if _display_question.startswith(_prefix):
            # Try to extract the human question from the command payload
            import re as _re
            _m = _re.search(r"[|]\s*question:\s*'(.+)'", _display_question, _re.DOTALL)
            if _m:
                _display_question = _m.group(1).strip().rstrip("'")
            else:
                _m2 = _re.search(r"answer my question about '(.+)'", _display_question, _re.DOTALL)
                if _m2:
                    _display_question = _m2.group(1).strip().rstrip("'")
            break

    intro = f"# {_display_question}\n"
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
    fig.update_layout(
        font_color="#E6E1D8",
        font_family="system-ui, -apple-system, sans-serif",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        autosize=True,
        margin=dict(l=20, r=20, t=50, b=20),
    )
    fig.update_xaxes(gridcolor="rgba(128,128,128,0.12)", zeroline=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.12)", zeroline=False)
    _display(HTML(fig.to_html(include_plotlyjs="cdn", full_html=False)))
"""

    # Use LLM-generated code if provided, else a smart fallback
    chart_code = python_code if python_code else """\
# Smart fallback: detect best column pair and render a ranked bar chart
_num_cols = []
_str_cols = []
for _c in df.columns:
    try:
        df[_c] = pd.to_numeric(df[_c], errors='raise')
        _num_cols.append(_c)
    except Exception:
        _str_cols.append(_c)

if _num_cols and _str_cols and _PLOTLY:
    _lc, _vc = _str_cols[0], _num_cols[0]
    _dff = df.groupby(_lc, as_index=False)[_vc].sum()
    _dff = _dff.sort_values(_vc, ascending=True)
    def _fmt(v):
        av = abs(v)
        if av >= 1e12: return f'{v/1e12:.1f}T'
        if av >= 1e9:  return f'{v/1e9:.1f}B'
        if av >= 1e6:  return f'{v/1e6:.1f}M'
        return f'{v:,.0f}'
    _dff['_lbl'] = _dff[_vc].apply(_fmt)
    _fig = px.bar(_dff, x=_vc, y=_lc, orientation='h', text='_lbl',
                  color=_lc, color_discrete_sequence=px.colors.qualitative.Safe,
                  labels={_vc: '', _lc: ''})
    _fig.update_traces(textposition='outside')
    _fig.update_layout(showlegend=False)
    _show_plotly(_fig)
elif _PLOTLY:
    _display(HTML("<p style='color:rgba(230,225,216,0.4);font:12px system-ui'>No plottable data found.</p>"))
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
