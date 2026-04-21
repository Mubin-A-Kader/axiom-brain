import json
import logging
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class Metadata(BaseModel):
    row_count_original: int
    row_count_cleaned: int
    anomaly_detected: bool
    summary_stats: Dict[str, Dict[str, Any]]

class BusinessAnalystResponse(BaseModel):
    data: List[Dict[str, Any]]
    metadata: Metadata
    frontend_json: str
    action_bar: List[str] = []

class MLGradeInterceptor:
    def __init__(self) -> None:
        pass

    def process(self, raw_json: str, anomaly_method: str = "iqr") -> BusinessAnalystResponse:
        """
        Process the raw JSON result from SQL execution into cleaned tabular data.
        
        Steps:
        A. Deduplication & Integrity
        B. Categorical Normalization
        C. Outlier & Anomaly Detection
        D. Unit & Temporal Standardization
        E. Smart Action Suggestions
        """
        if not raw_json:
            return BusinessAnalystResponse(
                data=[],
                metadata=Metadata(row_count_original=0, row_count_cleaned=0, anomaly_detected=False, summary_stats={}),
                frontend_json="{}",
                action_bar=[]
            )
        
        try:
            result_dict = json.loads(raw_json)
        except json.JSONDecodeError:
            logger.error("Failed to parse raw_json into dict")
            return BusinessAnalystResponse(
                data=[],
                metadata=Metadata(row_count_original=0, row_count_cleaned=0, anomaly_detected=False, summary_stats={}),
                frontend_json="{}",
                action_bar=[]
            )

        rows = result_dict.get("rows", [])
        columns = result_dict.get("columns", [])
        
        if not rows or not columns:
            return BusinessAnalystResponse(
                data=[],
                metadata=Metadata(row_count_original=0, row_count_cleaned=0, anomaly_detected=False, summary_stats={}),
                frontend_json="{}",
                action_bar=[]
            )

        row_count_original = len(rows)
        
        # Convert to DataFrame
        df = pd.DataFrame(rows, columns=columns)
        
        # A. Deduplication & Integrity
        try:
            df = df.drop_duplicates()
        except (TypeError, ValueError):
            # Fallback for unhashable types (e.g. lists, dicts) in the DataFrame
            # We use a string representation mask for deduplication
            try:
                df = df[~df.astype(str).duplicated()]
            except Exception as e:
                logger.warning(f"Deduplication failed even with string conversion: {e}")
        
        # Handle missing values
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col]):
                df[col] = df[col].replace([np.inf, -np.inf], np.nan)
                df[col] = df[col].fillna(0)
            else:
                df[col] = df[col].fillna("missing")

        # B. Categorical Normalization
        for col in df.columns:
            if pd.api.types.is_string_dtype(df[col]) or pd.api.types.is_object_dtype(df[col]):
                try:
                    # Only capitalize strings, ignore other types that might be objects
                    df[col] = df[col].apply(lambda x: str(x).strip().capitalize() if isinstance(x, str) and x != "missing" else x)
                except Exception:
                    pass

        # Rename columns to Human Readable Title Case
        def rename_col(col_name: str) -> str:
            return col_name.replace("_", " ").title()
            
        df.rename(columns={col: rename_col(col) for col in df.columns}, inplace=True)
        
        # E. Aggressive ID & UUID Stripping (The "Ghost ID" Killer)
        import re
        UUID_REGEX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
        
        cols_to_drop = []
        for col in df.columns:
            lower_col = col.lower()
            # 1. Column Name Match
            if lower_col in ["id", "uuid", "guid", "pk", "fk", "file key", "embedding key"] or \
               lower_col.endswith(" id") or lower_col.endswith(" key") or lower_col.endswith("_id"):
                cols_to_drop.append(col)
                continue
            
            # 2. Value Pattern Match (Check first 3 rows for UUID patterns)
            sample_values = df[col].dropna().head(3).astype(str).tolist()
            if any(UUID_REGEX.match(val) for val in sample_values):
                logger.info(f"Dropping column {col} due to UUID pattern detection.")
                cols_to_drop.append(col)
                
        # Only drop if it doesn't leave the DataFrame empty
        if len(cols_to_drop) < len(df.columns):
            df.drop(columns=cols_to_drop, inplace=True)
        elif len(cols_to_drop) == len(df.columns) and len(df.columns) > 1:
            # Keep at least one column if all were IDs (last resort)
            df.drop(columns=cols_to_drop[:-1], inplace=True)
        
        # C. Outlier & Anomaly Detection
        anomaly_detected = False
        is_anomaly_mask = pd.Series([False] * len(df), index=df.index)
        
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col]):
                if anomaly_method == "z_score":
                    mean = df[col].mean()
                    std = df[col].std()
                    if std > 0:
                        col_anomaly = np.abs(df[col] - mean) > (3 * std)
                        is_anomaly_mask = is_anomaly_mask | col_anomaly
                elif anomaly_method == "iqr":
                    Q1 = df[col].quantile(0.25)
                    Q3 = df[col].quantile(0.75)
                    IQR = Q3 - Q1
                    lower_bound = Q1 - 1.5 * IQR
                    upper_bound = Q3 + 1.5 * IQR
                    col_anomaly = (df[col] < lower_bound) | (df[col] > upper_bound)
                    is_anomaly_mask = is_anomaly_mask | col_anomaly
                    
        df["Is Anomaly"] = is_anomaly_mask
        if is_anomaly_mask.any():
            anomaly_detected = True

        # D. Unit & Temporal Standardization
        for col in df.columns:
            # Check for currency
            lower_col = col.lower()
            if "price" in lower_col or "revenue" in lower_col or "amount" in lower_col or "cost" in lower_col:
                if pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col]):
                    df[col] = df[col].round(2)
                    
            # Check for dates
            if "date" in lower_col or "time" in lower_col or "created" in lower_col or "updated" in lower_col:
                if not pd.api.types.is_numeric_dtype(df[col]):
                    try:
                        parsed = pd.to_datetime(df[col], errors='coerce')
                        df[col] = np.where(parsed.notnull(), parsed.dt.strftime("%Y-%m-%d"), df[col])
                    except Exception:
                        pass
        
        # Smart Action Suggestions
        action_bar = []
        if anomaly_detected:
            action_bar.append("Highlight Outliers")
        
        # Check if there is a categorical string column and a numeric column to group by
        has_categorical = False
        has_numeric = False
        cat_col = ""
        for col in df.columns:
            if col == "Is Anomaly":
                continue
            if pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col]):
                has_numeric = True
            elif pd.api.types.is_string_dtype(df[col]) or pd.api.types.is_object_dtype(df[col]):
                has_categorical = True
                cat_col = col
        
        if has_categorical and has_numeric:
            action_bar.append(f"Summarize by {cat_col}")
        
        if any("date" in c.lower() for c in df.columns):
            action_bar.append("Compare with Last Month")
            
        if not action_bar:
            action_bar = ["Show detailed statistics", "Export to CSV"]

        # Extract summary stats
        summary_stats = {}
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]) and not pd.api.types.is_bool_dtype(df[col]) and col != "Is Anomaly":
                summary_stats[col] = {
                    "mean": float(df[col].mean()) if not pd.isna(df[col].mean()) else 0.0,
                    "median": float(df[col].median()) if not pd.isna(df[col].median()) else 0.0,
                    "min": float(df[col].min()) if not pd.isna(df[col].min()) else 0.0,
                    "max": float(df[col].max()) if not pd.isna(df[col].max()) else 0.0
                }
                
        row_count_cleaned = len(df)
        
        # Convert back to list of dicts
        cleaned_data = df.to_dict(orient="records")
        
        # Prepare frontend JSON payload
        frontend_payload = {
            "columns": list(df.columns),
            "rows": df.replace({np.nan: None}).values.tolist(),
            "total_count": len(df)
        }
        
        metadata = Metadata(
            row_count_original=row_count_original,
            row_count_cleaned=row_count_cleaned,
            anomaly_detected=anomaly_detected,
            summary_stats=summary_stats
        )
        
        return BusinessAnalystResponse(
            data=cleaned_data, 
            metadata=metadata, 
            frontend_json=json.dumps(frontend_payload, default=str),
            action_bar=action_bar
        )
