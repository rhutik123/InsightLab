"""
Visualization payload builders for Chart.js dashboard & EDA views.
"""

import json


def to_chart_json(data):
    """Safely serialize chart data dictionaries for Django templates."""
    return json.dumps(data)
