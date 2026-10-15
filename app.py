import os
import threading
import time
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()

REGION = os.getenv("AWS_REGION", "ap-south-1")
INSTANCE_ID = os.getenv("INSTANCE_ID")
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN")

# Alert settings (can be changed in .env)
CPU_THRESHOLD = float(os.getenv("CPU_THRESHOLD", "80"))      # percent
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))      # seconds between checks
COOLDOWN_MINUTES = int(os.getenv("COOLDOWN_MINUTES", "15"))  # min gap between repeat emails

if not INSTANCE_ID:
    raise SystemExit(
        "INSTANCE_ID is missing. Create a .env file next to app.py (see .env.example)."
    )

# Credentials are NOT written here. boto3 picks them up from
# `aws configure` or from an IAM role if this runs on EC2.
cloudwatch = boto3.client("cloudwatch", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)

app = Flask(__name__)

DIMS = [{"Name": "InstanceId", "Value": INSTANCE_ID}]

# key -> (namespace, dimensions, statistic, CloudWatch metric name)
METRICS = {
    "cpu": ("AWS/EC2", DIMS, "Average", "CPUUtilization"),
    "network_in": ("AWS/EC2", DIMS, "Sum", "NetworkIn"),
    "network_out": ("AWS/EC2", DIMS, "Sum", "NetworkOut"),
    "status_check": ("AWS/EC2", DIMS, "Maximum", "StatusCheckFailed"),
}


def fetch_metric(key, minutes=60, period=300):
    namespace, dims, stat, name = METRICS[key]
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    resp = cloudwatch.get_metric_statistics(
        Namespace=namespace,
        MetricName=name,
        Dimensions=dims,
        StartTime=start,
        EndTime=end,
        Period=period,
        Statistics=[stat],
    )
    points = sorted(resp["Datapoints"], key=lambda p: p["Timestamp"])
    return [
        {"time": p["Timestamp"].isoformat(), "value": round(p[stat], 2)}
        for p in points
    ]


# ---------------------------------------------------------------- alerting
alert_lock = threading.Lock()
alert_history = []   # newest first, max 50 entries
alert_state = {}     # alert name -> {"active": bool, "last_sent": datetime|None}


def send_alert(name, subject, message, kind="ALARM"):
    """Publish to SNS and record the result in the history list."""
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "kind": kind,
        "message": message,
        "sent": False,
        "error": None,
    }
    if not SNS_TOPIC_ARN:
        entry["error"] = "SNS_TOPIC_ARN is not set in .env"
    else:
        try:
            sns.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject[:100], Message=message)
            entry["sent"] = True
        except (ClientError, BotoCoreError) as e:
            entry["error"] = str(e)
    with alert_lock:
        alert_history.insert(0, entry)
        del alert_history[50:]
    return entry


def evaluate(name, breached, subject, message, ok_message):
    """Send an ALARM when a rule is breached (respecting the cooldown),
    and an OK email when it returns to normal."""
    now = datetime.now(timezone.utc)
    with alert_lock:
        state = alert_state.setdefault(name, {"active": False, "last_sent": None})
        was_active = state["active"]
        last_sent = state["last_sent"]

    if breached:
        cooled_down = (
            last_sent is None
            or now - last_sent >= timedelta(minutes=COOLDOWN_MINUTES)
        )
        if cooled_down:
            entry = send_alert(name, subject, message, "ALARM")
            with alert_lock:
                if entry["sent"]:
                    state["last_sent"] = now
                state["active"] = True
        else:
            with alert_lock:
                state["active"] = True
    elif was_active:
        send_alert(name, f"[OK] {subject}", ok_message, "OK")
        with alert_lock:
            state["active"] = False
            state["last_sent"] = None


def check_alerts():
    cpu = fetch_metric("cpu", minutes=15, period=300)
    if cpu:
        value = cpu[-1]["value"]
        evaluate(
            "High CPU",
            value >= CPU_THRESHOLD,
            f"[ALARM] High CPU on {INSTANCE_ID}",
            f"CPU utilization is {value}% (threshold {CPU_THRESHOLD}%) "
            f"on instance {INSTANCE_ID} in {REGION}.",
            f"CPU is back to {value}% (below {CPU_THRESHOLD}%) on {INSTANCE_ID}.",
        )

    status = fetch_metric("status_check", minutes=15, period=300)
    if status:
        value = status[-1]["value"]
        evaluate(
            "Status check failed",
            value >= 1,
            f"[ALARM] Status check failed on {INSTANCE_ID}",
            f"EC2 status check FAILED on instance {INSTANCE_ID} in {REGION}.",
            f"Status checks are passing again on {INSTANCE_ID}.",
        )


def alert_loop():
    print(f"[alerts] checker started: CPU>{CPU_THRESHOLD}% every {CHECK_INTERVAL}s")
    while True:
        try:
            check_alerts()
        except Exception as e:  # keep the thread alive no matter what
            print("[alerts] check failed:", e)
        time.sleep(CHECK_INTERVAL)


# ------------------------------------------------------------------ routes
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "instance": INSTANCE_ID, "region": REGION})


@app.route("/api/metrics/<key>")
def metrics(key):
    if key not in METRICS:
        return jsonify({"error": f"unknown metric '{key}'"}), 404
    try:
        minutes = int(request.args.get("minutes", 60))
    except ValueError:
        return jsonify({"error": "minutes must be a number"}), 400
    return jsonify({"metric": key, "data": fetch_metric(key, minutes)})


@app.route("/api/alerts")
def alerts():
    with alert_lock:
        history = list(alert_history)
    return jsonify({
        "config": {
            "cpu_threshold": CPU_THRESHOLD,
            "check_interval": CHECK_INTERVAL,
            "cooldown_minutes": COOLDOWN_MINUTES,
            "sns_configured": bool(SNS_TOPIC_ARN),
        },
        "history": history,
    })


@app.route("/api/alerts/test", methods=["POST"])
def alerts_test():
    entry = send_alert(
        "Test alert",
        "[TEST] EC2 monitoring dashboard",
        f"This is a test alert from your EC2 monitoring dashboard "
        f"(instance {INSTANCE_ID}, region {REGION}).",
        "TEST",
    )
    return jsonify(entry), (200 if entry["sent"] else 500)


@app.errorhandler(ClientError)
@app.errorhandler(BotoCoreError)
def aws_error(e):
    # Readable message instead of a long crash page
    return jsonify({"error": "AWS request failed", "details": str(e)}), 500


if __name__ == "__main__":
    # In debug mode Flask starts two processes; only start the checker in the
    # one that actually serves requests, so you don't get duplicate emails.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Thread(target=alert_loop, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=True)