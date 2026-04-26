import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

def safe_db_urlparse(url: str) -> Dict[str, Any]:
    """
    A more robust URL parser for database connection strings.
    Handles special characters in passwords (like brackets) that crash urllib.parse.
    
    Returns a dict with: scheme, username, password, hostname, port, path
    """
    # Regex to extract: scheme://[user[:password]@]host[:port][/path]
    # We use a non-greedy match for everything before the first / after the scheme
    regex = r"^(?P<scheme>[^:]+)://(?:(?P<user>[^:@/]+)(?::(?P<password>[^@/]+))?@)?(?P<host>[^:/]+)(?::(?P<port>\d+))?(?P<path>/.*)?$"
    
    match = re.match(regex, url)
    if not match:
        # Fallback to a very basic split if regex fails
        try:
            scheme, rest = url.split("://", 1)
            path = ""
            if "/" in rest:
                rest, path = rest.split("/", 1)
                path = "/" + path
            
            user_pass = ""
            host_port = rest
            if "@" in rest:
                user_pass, host_port = rest.rsplit("@", 1)
            
            username = ""
            password = ""
            if ":" in user_pass:
                username, password = user_pass.split(":", 1)
            else:
                username = user_pass
                
            hostname = host_port
            port = None
            if ":" in host_port:
                hostname, port_str = host_port.rsplit(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    hostname = host_port # colon was part of host (IPv6?)
            
            return {
                "scheme": scheme,
                "username": username or None,
                "password": password or None,
                "hostname": hostname,
                "port": port,
                "path": path
            }
        except Exception:
            raise ValueError(f"Could not parse database URL: {url}")

    d = match.groupdict()
    return {
        "scheme": d["scheme"],
        "username": d["user"],
        "password": d["password"],
        "hostname": d["host"],
        "port": int(d["port"]) if d["port"] else None,
        "path": d["path"] or ""
    }


# ---------------------------------------------------------------------------
# MLGradeInterceptor
# ---------------------------------------------------------------------------

@dataclass
class CleaningMetadata:
    row_count_original: int
    row_count_cleaned: int
    anomaly_detected: bool
    anomalous_columns: List[str]
    summary_stats: Dict[str, Any]


@dataclass
class CleanedResponse:
    """Returned by MLGradeInterceptor.process()."""
    data: List[Dict[str, Any]]          # cleaned rows as list-of-dicts
    metadata: CleaningMetadata
    frontend_json: str                   # JSON string ready for the DataTable component
    action_bar: List[str]                # suggested next actions for the UI


class MLGradeInterceptor:
    """
    Lightweight data-cleaning pipeline for SQL result payloads.

    Converts the raw {"columns": [...], "rows": [[...]]} wire format into
    list-of-dicts, optionally flags IQR outliers in numeric columns, and
    computes summary statistics that the ResponseSynthesizerNode embeds in
    its LLM prompt.
    """

    # Columns that contain identifiers / timestamps — skip stats for these
    _SKIP_STAT_PATTERNS = re.compile(
        r"(id|uuid|key|token|hash|created|updated|timestamp|date|time)$",
        re.IGNORECASE,
    )

    def process(
        self,
        sql_result_json: str,
        anomaly_method: str = "iqr",
    ) -> CleanedResponse:
        """
        Parse *sql_result_json*, clean rows, compute metadata.

        Args:
            sql_result_json: JSON string with keys "columns" and "rows".
            anomaly_method:  "iqr" (default) flags rows where any numeric
                             column value is outside [Q1 - 1.5*IQR, Q3 + 1.5*IQR].
                             Any other value disables outlier detection.
        """
        try:
            raw = json.loads(sql_result_json) if sql_result_json else {}
        except (json.JSONDecodeError, TypeError):
            raw = {}

        columns: List[str] = raw.get("columns") or []
        rows: List = raw.get("rows") or []
        total_count: int = raw.get("total_count", len(rows))
        is_lake_result: bool = raw.get("is_lake_result", False)

        # Normalise every row to a dict
        dicts: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                dicts.append(row)
            elif isinstance(row, (list, tuple)) and columns:
                dicts.append({columns[i]: v for i, v in enumerate(row) if i < len(columns)})
            # rows of unknown shape are dropped

        row_count_original = len(dicts)

        # Identify numeric columns
        numeric_cols = self._numeric_columns(dicts, columns)

        # Outlier detection
        anomalous_cols: List[str] = []
        anomalous_row_indices: set = set()
        if anomaly_method == "iqr" and numeric_cols:
            anomalous_cols, anomalous_row_indices = self._iqr_flag(dicts, numeric_cols)

        # Summary statistics (non-id numeric columns only)
        summary_stats = self._summary_stats(dicts, numeric_cols)

        cleaned = [r for i, r in enumerate(dicts) if i not in anomalous_row_indices]
        row_count_cleaned = len(cleaned)

        metadata = CleaningMetadata(
            row_count_original=row_count_original,
            row_count_cleaned=row_count_cleaned,
            anomaly_detected=bool(anomalous_row_indices),
            anomalous_columns=anomalous_cols,
            summary_stats=summary_stats,
        )

        # Re-serialise for the frontend DataTable, preserving wire-format fields
        frontend_payload: Dict[str, Any] = {
            "columns": columns or (list(cleaned[0].keys()) if cleaned else []),
            "rows": [
                [row.get(c) for c in (columns or list(row.keys()))]
                for row in cleaned
            ],
            "total_count": total_count,
        }
        if is_lake_result:
            frontend_payload["is_lake_result"] = True
            frontend_payload["source_count"] = raw.get("source_count", 1)

        frontend_json = json.dumps(frontend_payload, default=str)
        action_bar = self._action_bar(cleaned, numeric_cols, is_lake_result)

        return CleanedResponse(
            data=cleaned,
            metadata=metadata,
            frontend_json=frontend_json,
            action_bar=action_bar,
        )

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _numeric_columns(
        self, rows: List[Dict], columns: List[str]
    ) -> List[str]:
        """Return column names that contain predominantly numeric values."""
        candidates = columns or (list(rows[0].keys()) if rows else [])
        numeric: List[str] = []
        for col in candidates:
            if self._SKIP_STAT_PATTERNS.search(col):
                continue
            values = [r.get(col) for r in rows if r.get(col) is not None]
            if not values:
                continue
            numeric_count = sum(1 for v in values if isinstance(v, (int, float)))
            if numeric_count / len(values) >= 0.7:
                numeric.append(col)
        return numeric

    @staticmethod
    def _iqr_flag(
        rows: List[Dict], numeric_cols: List[str]
    ) -> tuple[List[str], set]:
        """
        Flag rows that are outliers in any numeric column.
        Returns (anomalous_column_names, set_of_row_indices).
        """
        anomalous_cols: List[str] = []
        flagged: set = set()

        for col in numeric_cols:
            vals = [(i, float(r[col])) for i, r in enumerate(rows) if isinstance(r.get(col), (int, float))]
            if len(vals) < 4:
                continue
            sorted_vals = sorted(v for _, v in vals)
            n = len(sorted_vals)
            q1 = sorted_vals[n // 4]
            q3 = sorted_vals[(3 * n) // 4]
            iqr = q3 - q1
            if iqr == 0:
                continue
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            outlier_indices = {i for i, v in vals if v < lo or v > hi}
            if outlier_indices:
                anomalous_cols.append(col)
                flagged.update(outlier_indices)

        return anomalous_cols, flagged

    @staticmethod
    def _summary_stats(
        rows: List[Dict], numeric_cols: List[str]
    ) -> Dict[str, Any]:
        """Compute min/max/mean/count for each numeric column."""
        stats: Dict[str, Any] = {}
        for col in numeric_cols:
            vals = [float(r[col]) for r in rows if isinstance(r.get(col), (int, float))]
            if not vals:
                continue
            stats[col] = {
                "count": len(vals),
                "min": round(min(vals), 4),
                "max": round(max(vals), 4),
                "mean": round(sum(vals) / len(vals), 4),
            }
        return stats

    @staticmethod
    def _action_bar(
        rows: List[Dict], numeric_cols: List[str], is_lake_result: bool
    ) -> List[str]:
        """Suggest relevant actions based on what the data contains."""
        actions: List[str] = []
        if not rows:
            return actions
        if numeric_cols:
            actions.append("Visualize")
        if len(rows) > 1:
            actions.append("Download CSV")
        if is_lake_result:
            actions.append("Compare Sources")
        actions.append("Deep Dive")
        return actions
