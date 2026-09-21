from flask import Flask, render_template, jsonify,send_from_directory
import psutil
import platform
import socket
import subprocess
import threading
import time
import json
from datetime import datetime


app = Flask(__name__)

@app.route("/favicon.svg")
def favicon():
    return send_from_directory(
        r"C:\server\shared",
        "favicon.svg",
        mimetype="image/svg+xml"
    )


# ============================================================
# CONFIGURATION
# ============================================================

# Estimated wall power consumption in watts.
# Change this after measuring your actual server consumption.
POWER_WATTS = 30.0

# Electricity price in EUR/kWh.
ELECTRICITY_PRICE = 0.258


# ============================================================
# CACHED MONITORING DATA
# ============================================================

data = {
    "system": {},
    "processes": [],
    "disks": [],
    "tailscale": {},
    "power": {},
    "ai": {},
    "last_updated": None
}

lock = threading.Lock()


# ============================================================
# SYSTEM INFORMATION
# ============================================================

def get_uptime():

    uptime_seconds = time.time() - psutil.boot_time()
    days = int(uptime_seconds // 86400)
    hours = int((uptime_seconds % 86400) // 3600)
    minutes = int((uptime_seconds % 3600) // 60)

    return f"{days}d {hours}h {minutes}m"


def get_system():

    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")

    return {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "cpu": psutil.cpu_percent(interval=None),
        "memory": memory.percent,
        "memory_used_gb": round(memory.used / (1024 ** 3), 2),
        "memory_total_gb": round(memory.total / (1024 ** 3), 2),
        "disk": disk.percent,
        "disk_used_gb": round(disk.used / (1024 ** 3), 2),
        "disk_total_gb": round(disk.total / (1024 ** 3), 2),
        "uptime": get_uptime(),
        "boot_time": time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(psutil.boot_time())
        )
    }


# ============================================================
# PROCESS MONITORING
# ============================================================

def get_processes():

    processes = []

    for process in psutil.process_iter(
        [
            "pid",
            "name",
            "memory_percent"
        ]
    ):

        try:

            cpu = process.cpu_percent(
                interval=None
            )

            memory = process.info[
                "memory_percent"
            ]

            processes.append({
                "pid": process.info["pid"],

                "name":
                    process.info["name"]
                    or "Unknown",

                "cpu":
                    round(cpu, 1),

                "memory":
                    round(memory or 0, 1)
            })

        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess
        ):

            continue


    processes.sort(
        key=lambda x: x["cpu"],
        reverse=True
    )

    return processes[:10]


# ============================================================
# DISK TEMPERATURE
# ============================================================

def get_disk_temperatures():

    try:

        command = [
            "powershell.exe",

            "-NoProfile",

            "-NonInteractive",

            "-Command",

            (
                "Get-PhysicalDisk | "
                "Get-StorageReliabilityCounter | "
                "Select-Object DeviceId,Temperature | "
                "ConvertTo-Json"
            )
        ]

        result = subprocess.run(
            command,

            capture_output=True,

            text=True,

            timeout=5
        )

        if result.returncode != 0:

            return []


        if not result.stdout.strip():

            return []


        result_data = json.loads(
            result.stdout
        )


        if not isinstance(
            result_data,
            list
        ):

            result_data = [
                result_data
            ]


        disks = []

        for disk in result_data:

            temperature = disk.get(
                "Temperature"
            )

            if temperature is not None:

                disks.append({
                    "temperature":
                        float(temperature)
                })


        return disks


    except Exception:

        return []


# ============================================================
# TAILSCALE
# ============================================================

def get_tailscale():

    try:

        result = subprocess.run(
            [
                "tailscale",
                "status",
                "--json"
            ],

            capture_output=True,

            text=True,

            timeout=5
        )


        if result.returncode != 0:

            return {
                "online": False,
                "status": "Unavailable"
            }


        info = json.loads(
            result.stdout
        )


        state = info.get(
            "BackendState",
            "Unknown"
        )


        self_node = info.get(
            "Self",
            {}
        )


        addresses = self_node.get(
            "TailscaleIPs",
            []
        )


        return {

            "online":
                state == "Running",

            "status":
                state,

            "hostname":
                self_node.get(
                    "HostName",
                    socket.gethostname()
                ),

            "tailscale_ip":
                addresses[0]
                if addresses
                else "Unknown"
        }


    except Exception:

        return {
            "online": False,
            "status": "Unavailable"
        }


# ============================================================
# ELECTRICITY
# ============================================================

def get_power():

    watts = POWER_WATTS

    price = ELECTRICITY_PRICE


    # Energy

    kwh_hour = watts / 1000

    kwh_day = kwh_hour * 24

    kwh_month = (
        kwh_day * 30.44
    )


    # Cost

    cost_hour = (
        kwh_hour * price
    )

    cost_day = (
        kwh_day * price
    )

    cost_month = (
        kwh_month * price
    )


    return {

        "watts":
            watts,

        "price_per_kwh":
            price,

        "kwh_hour":
            round(
                kwh_hour,
                4
            ),

        "kwh_day":
            round(
                kwh_day,
                3
            ),

        "kwh_month":
            round(
                kwh_month,
                3
            ),

        "cost_hour":
            round(
                cost_hour,
                4
            ),

        "cost_day":
            round(
                cost_day,
                2
            ),

        "cost_month":
            round(
                cost_month,
                2
            )
    }


# ============================================================
# API
# ============================================================

@app.route("/")
def dashboard():

    return render_template(
        "index.html"
    )


@app.route("/api/system")
def system_api():

    snapshot = {
        "system": get_system(),
        "processes": get_processes(),
        "disks": get_disk_temperatures(),
        "tailscale": get_tailscale(),
        "power": get_power(),
        "ai": {},
        "last_updated": datetime.now().astimezone().isoformat()
    }

    try:
        result = subprocess.run(
            [
                "curl",
                "-s",
                "http://127.0.0.1:8081/api/ai-status"
            ],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0 and result.stdout.strip():
            snapshot["ai"] = json.loads(result.stdout)
    except Exception:
        pass

    with lock:
        data.update(snapshot)
        return jsonify(snapshot)


# ============================================================
# APPLICATION START
# ============================================================

if __name__ == "__main__":
    app.run(

        host="0.0.0.0",

        port=8080,

        debug=False,

        threaded=True
    )
