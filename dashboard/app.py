from flask import Flask, render_template, jsonify
import psutil
import platform
import socket
import subprocess
import threading
import time
import json
from datetime import datetime


app = Flask(__name__)


# ============================================================
# CONFIGURATION
# ============================================================

# Estimated wall power consumption in watts.
# Change this after measuring your actual server consumption.
POWER_WATTS = 30.0

# Electricity price in €/kWh.
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

    hours = int(
        (uptime_seconds % 86400) // 3600
    )

    minutes = int(
        (uptime_seconds % 3600) // 60
    )

    return f"{days}d {hours}h {minutes}m"


def get_system():

    memory = psutil.virtual_memory()

    disk = psutil.disk_usage("C:\\")

    return {
        "hostname": socket.gethostname(),

        "os": platform.platform(),

        "cpu": psutil.cpu_percent(
            interval=None
        ),

        "memory": memory.percent,

        "memory_used_gb": round(
            memory.used / (1024 ** 3),
            2
        ),

        "memory_total_gb": round(
            memory.total / (1024 ** 3),
            2
        ),

        "disk": disk.percent,

        "disk_used_gb": round(
            disk.used / (1024 ** 3),
            2
        ),

        "disk_total_gb": round(
            disk.total / (1024 ** 3),
            2
        ),

        "uptime": get_uptime(),

        "boot_time": time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(
                psutil.boot_time()
            )
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
# COLLECTORS
# ============================================================

def system_collector():

    while True:

        try:

            result = get_system()


            with lock:

                data["system"] = result

                # This timestamp represents the moment
                # the monitoring data was successfully updated.
                data["last_updated"] = (
                    datetime.now()
                    .astimezone()
                    .isoformat()
                )


        except Exception:

            pass


        time.sleep(2)


# ------------------------------------------------------------

def process_collector():

    while True:

        try:

            result = get_processes()


            with lock:

                data["processes"] = result


        except Exception:

            pass


        time.sleep(5)


# ------------------------------------------------------------

def disk_collector():

    while True:

        try:

            result = get_disk_temperatures()


            with lock:

                data["disks"] = result


        except Exception:

            pass


        time.sleep(60)


# ------------------------------------------------------------

def tailscale_collector():

    while True:

        try:

            result = get_tailscale()


            with lock:

                data["tailscale"] = result


        except Exception:

            pass


        time.sleep(30)


# ------------------------------------------------------------

def power_collector():

    while True:

        try:

            result = get_power()


            with lock:

                data["power"] = result


        except Exception:

            pass


        time.sleep(30)

# ------------------------------------------------------------

def ai_collector():

    while True:

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

                ai_status = json.loads(result.stdout)

                with lock:
                    data["ai"] = ai_status

        except Exception:
            pass

        time.sleep(5)

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

    with lock:

        return jsonify({

            "system":
                data["system"],

            "processes":
                data["processes"],

            "disks":
                data["disks"],

            "tailscale":
                data["tailscale"],

            "power":
                data["power"],

            "ai":
                data["ai"],

            "last_updated":
                data["last_updated"]
        })


# ============================================================
# START COLLECTORS
# ============================================================

def start_collectors():

    collectors = [
       system_collector,
       process_collector,
       disk_collector,
       tailscale_collector,
       power_collector,
       ai_collector
    ]


    for collector in collectors:

        thread = threading.Thread(

            target=collector,

            daemon=True
        )

        thread.start()


# ============================================================
# APPLICATION START
# ============================================================

if __name__ == "__main__":

    start_collectors()


    app.run(

        host="0.0.0.0",

        port=8080,

        debug=False,

        threaded=True
    )
