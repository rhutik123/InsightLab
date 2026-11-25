"""
Standard regression and time-series forecasting evaluation metrics:
MAE, RMSE, MAPE (handling zero actuals safely), and R2.
"""

import math
import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


def evaluate_predictions(actual, predicted):
    """
    Compute MAE, RMSE, MAPE, and R2 on held-out chronological test values.
    Handles zero actual values explicitly so division by zero never occurs.
    """
    y_true = np.asarray(actual, dtype=float)
    y_pred = np.asarray(predicted, dtype=float)

    if len(y_true) == 0 or len(y_true) != len(y_pred):
        return {
            'mae': None,
            'rmse': None,
            'mape': None,
            'r2': None,
            'mape_note': 'Evaluation unavailable due to empty or mismatched prediction arrays.',
        }

    mae = round(float(mean_absolute_error(y_true, y_pred)), 4)
    rmse = round(float(math.sqrt(mean_squared_error(y_true, y_pred))), 4)

    non_zero_mask = np.abs(y_true) > 1e-9
    zero_count = int((~non_zero_mask).sum())
    if non_zero_mask.any():
        mape_val = float(
            np.mean(np.abs((y_true[non_zero_mask] - y_pred[non_zero_mask]) / y_true[non_zero_mask])) * 100.0
        )
        mape = round(mape_val, 2)
        if zero_count > 0:
            mape_note = f'Excluded {zero_count} zero-actual test point(s) from MAPE calculation.'
        else:
            mape_note = 'Computed across all held-out test observations.'
    else:
        mape = None
        mape_note = 'MAPE undefined because all actual test values are zero.'

    r2 = None
    if len(y_true) >= 2 and np.var(y_true) > 1e-12:
        r2 = round(float(r2_score(y_true, y_pred)), 4)

    return {
        'mae': mae,
        'rmse': rmse,
        'mape': mape,
        'r2': r2,
        'mape_note': mape_note,
    }
