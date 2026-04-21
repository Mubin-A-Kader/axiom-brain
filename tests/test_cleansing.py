import json
import numpy as np
from axiom.core.cleansing import MLGradeInterceptor

def test_deduplication():
    raw_data = {
        "columns": ["id", "name"],
        "rows": [
            [1, "alice"],
            [1, "alice"], # Duplicate
            [2, "bob"]
        ]
    }
    interceptor = MLGradeInterceptor()
    resp = interceptor.process(json.dumps(raw_data))
    
    assert resp.metadata.row_count_original == 3
    assert resp.metadata.row_count_cleaned == 2
    assert len(resp.data) == 2

def test_normalization():
    raw_data = {
        "columns": ["user_id", "status_code", "created_at"],
        "rows": [
            [1, "  active  ", "2023-10-01T12:00:00"],
            [2, "PENDING", "2023-10-02"]
        ]
    }
    interceptor = MLGradeInterceptor()
    resp = interceptor.process(json.dumps(raw_data))
    
    data = resp.data
    # Columns should be renamed and user_id should be dropped
    assert "User Id" not in data[0]
    assert "Status Code" in data[0]
    assert "Created At" in data[0]
    
    # Strings should be stripped and capitalized
    assert data[0]["Status Code"] == "Active"
    assert data[1]["Status Code"] == "Pending"
    
    # Dates should be formatted as YYYY-MM-DD
    assert data[0]["Created At"] == "2023-10-01"
    assert data[1]["Created At"] == "2023-10-02"

def test_anomaly_detection_z_score():
    # Make a normal distribution with one obvious outlier
    np.random.seed(42)
    rows = [[50.0 + float(np.random.randn())] for i in range(1, 100)]
    rows.append([1000.0]) # The anomaly
    
    raw_data = {
        "columns": ["score"],
        "rows": rows
    }
    
    interceptor = MLGradeInterceptor()
    resp = interceptor.process(json.dumps(raw_data), anomaly_method="z_score")
    
    assert resp.metadata.anomaly_detected is True
    # Find the anomaly row
    anomaly_row = next(r for r in resp.data if r["Score"] == 1000.0)
    assert anomaly_row["Is Anomaly"] is True
    
    # Check normal row
    normal_row = next(r for r in resp.data if r["Score"] < 100)
    assert normal_row["Is Anomaly"] is False

def test_currency_rounding():
    raw_data = {
        "columns": ["id", "price", "total_revenue"],
        "rows": [
            [1, 10.1234, 100.5678],
            [2, 20.9876, 200.1]
        ]
    }
    
    interceptor = MLGradeInterceptor()
    resp = interceptor.process(json.dumps(raw_data))
    
    assert resp.data[0]["Price"] == 10.12
    assert resp.data[0]["Total Revenue"] == 100.57
    assert resp.data[1]["Price"] == 20.99
    assert resp.data[1]["Total Revenue"] == 200.10

def test_unhashable_types():
    raw_data = {
        "columns": ["id", "tags"],
        "rows": [
            [1, ["a", "b"]],
            [1, ["a", "b"]], # Duplicate with list
            [2, ["c"]]
        ]
    }
    interceptor = MLGradeInterceptor()
    resp = interceptor.process(json.dumps(raw_data))
    
    assert resp.metadata.row_count_original == 3
    assert resp.metadata.row_count_cleaned == 2
    assert len(resp.data) == 2
    assert resp.data[0]["Tags"] == ["a", "b"]
    assert resp.data[1]["Tags"] == ["c"]
