import pandas as pd

# Load the generated research log
df = pd.read_csv('data/attendance_log.csv')

# 1. Average Edge Latency
avg_latency = df['latency'].mean()
print(f"Average Edge Latency: {avg_latency:.2f} ms")

# 2. Spoof Rejection Rate (Assuming tests included spoof attempts)
total_spoofs_attempted = len(df[df['reason'].str.contains("Screen|Photo|Static", na=False)])
spoofs_rejected = len(df[df['liveness_label'] == "SPOOF"])
srr = (spoofs_rejected / total_spoofs_attempted) * 100 if total_spoofs_attempted > 0 else 0
print(f"Spoof Rejection Rate: {srr:.2f}%")

# 3. Offload Rate (How often it asks the server for help)
# FIX-10: Correctly targeting the existing 'liveness_label' column to track offloads
# FIXED: filter on decision column in diagnostic_log, not liveness_label in attendance_log.
# attendance_log does not contain the decision column — load diagnostic_log for this metric.
diag_df = pd.read_csv('data/diagnostic_log.csv')
offloads = len(diag_df[diag_df['decision'] == "OFFLOAD_TO_CLOUD"])
offload_rate = (offloads / len(diag_df)) * 100 if len(diag_df) > 0 else 0
print(f"Server Offload Rate: {offload_rate:.2f}%")