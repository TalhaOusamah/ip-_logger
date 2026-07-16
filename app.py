from flask import Flask, request, jsonify, render_template_string, session, redirect
from datetime import datetime, timezone
import json
import os
import uuid
import time
import threading
import requests

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")

DATA_DIR = (
    "/tmp"
    if os.environ.get("VERCEL")
    else os.path.dirname(os.path.abspath(__file__))
)

LOG_FILE = os.path.join(DATA_DIR, "requests.jsonl")
GEOCODE_CACHE_FILE = os.path.join(DATA_DIR, "geocode_cache.json")
GOOGLE_MAPS_REDIRECT = "https://www.google.com/maps"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
NOMINATIM_USER_AGENT = os.environ.get(
    "NOMINATIM_USER_AGENT",
    "metadata-server-local-test/1.0"
)

SAFE_HEADERS = [
    "Host",
    "User-Agent",
    "Accept",
    "Accept-Language",
    "Referer"
]

_geocode_lock = threading.Lock()
_last_geocode_request_time = 0


def get_session_id():
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    return session["session_id"]


def get_safe_headers():
    return {
        header: request.headers.get(header)
        for header in SAFE_HEADERS
        if request.headers.get(header)
    }


def is_probable_bot(user_agent):
    if not user_agent:
        return "Unknown"

    bot_words = [
        "bot",
        "crawl",
        "spider",
        "slurp",
        "curl",
        "wget",
        "python-requests",
        "httpclient"
    ]

    lower_user_agent = user_agent.lower()

    for word in bot_words:
        if word in lower_user_agent:
            return "Yes"

    return "No"


def write_log(metadata):
    with open(LOG_FILE, "a", encoding="utf-8") as file:
        file.write(json.dumps(metadata, ensure_ascii=False) + "\n")


def read_logs():
    logs = []

    if not os.path.exists(LOG_FILE):
        return logs

    with open(LOG_FILE, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if not line:
                continue

            try:
                logs.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    return list(reversed(logs))


def load_geocode_cache():
    if not os.path.exists(GEOCODE_CACHE_FILE):
        return {}

    try:
        with open(GEOCODE_CACHE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError:
        return {}


def save_geocode_cache(cache):
    with open(GEOCODE_CACHE_FILE, "w", encoding="utf-8") as file:
        json.dump(cache, file, indent=2, ensure_ascii=False)


def wait_for_nominatim_rate_limit():
    global _last_geocode_request_time

    with _geocode_lock:
        now = time.time()
        elapsed = now - _last_geocode_request_time

        if elapsed < 1.1:
            time.sleep(1.1 - elapsed)

        _last_geocode_request_time = time.time()


def reverse_geocode(latitude, longitude):
    if latitude is None or longitude is None:
        return {}

    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return {}

    cache_key = f"{round(lat, 5)},{round(lon, 5)}"
    cache = load_geocode_cache()

    if cache_key in cache:
        return cache[cache_key]

    wait_for_nominatim_rate_limit()

    url = "https://nominatim.openstreetmap.org/reverse"

    headers = {
        "User-Agent": NOMINATIM_USER_AGENT
    }

    params = {
        "format": "jsonv2",
        "lat": lat,
        "lon": lon,
        "zoom": 18,
        "addressdetails": 1
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=10
        )

        if response.status_code != 200:
            return {
                "error": f"Nominatim returned status {response.status_code}"
            }

        result = response.json()
        address = result.get("address", {})

        geocoded = {
            "display_name": result.get("display_name"),
            "country": address.get("country"),
            "country_code": address.get("country_code"),
            "state": address.get("state") or address.get("region"),
            "city": (
                address.get("city")
                or address.get("town")
                or address.get("village")
                or address.get("municipality")
                or address.get("county")
            ),
            "suburb": address.get("suburb") or address.get("neighbourhood"),
            "road": address.get("road"),
            "postcode": address.get("postcode")
        }

        cache[cache_key] = geocoded
        save_geocode_cache(cache)

        return geocoded

    except requests.RequestException as error:
        return {
            "error": str(error)
        }


CONSENT_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Location Permission</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #f1f3f5;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
        }
        .box {
            background: white;
            width: 440px;
            max-width: 92%;
            padding: 28px;
            border-radius: 14px;
            box-shadow: 0 10px 35px rgba(0,0,0,0.12);
            text-align: center;
        }
        button {
            background: #1976d2;
            color: white;
            border: none;
            padding: 13px 22px;
            font-size: 16px;
            border-radius: 8px;
            cursor: pointer;
        }
        button:hover {
            background: #125ea8;
        }
        #status {
            margin-top: 18px;
            color: #333;
        }
        .notice {
            font-size: 13px;
            color: #555;
            line-height: 1.5;
            margin-bottom: 18px;
        }
    </style>
</head>
<body>
    <div class="box">
        <h2>Location Permission Required</h2>
        <p class="notice">
            To continue to Google Maps, this page will ask your browser for location permission.
            If you allow, your latitude, longitude, accuracy, request time, IP address, browser user agent,
            and approximate address details from reverse geocoding will be saved for this test server.
        </p>
        <button onclick="askLocation()">Allow Location</button>
        <p id="status"></p>
    </div>

    <script>
        const REDIRECT_URL = {{ redirect_url|tojson }};

        function askLocation() {
            const status = document.getElementById("status");

            if (!navigator.geolocation) {
                status.innerText = "Geolocation is not supported by this browser.";
                return;
            }

            status.innerText = "Requesting location permission...";

            navigator.geolocation.getCurrentPosition(
                function(position) {
                    const locationData = {
                        latitude: position.coords.latitude,
                        longitude: position.coords.longitude,
                        accuracy: position.coords.accuracy,
                        location_timestamp: new Date(position.timestamp).toISOString()
                    };

                    fetch("/log-location", {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json"
                        },
                        body: JSON.stringify(locationData)
                    })
                    .then(response => response.json())
                    .then(data => {
                        status.innerText = "Location saved. Redirecting to Google Maps...";
                        window.location.href = REDIRECT_URL;
                    })
                    .catch(error => {
                        status.innerText = "Failed to send location.";
                    });
                },
                function(error) {
                    if (error.code === error.PERMISSION_DENIED) {
                        status.innerText = "Location permission denied.";
                    } else if (error.code === error.POSITION_UNAVAILABLE) {
                        status.innerText = "Location information is unavailable.";
                    } else if (error.code === error.TIMEOUT) {
                        status.innerText = "Location request timed out.";
                    } else {
                        status.innerText = "Could not get location.";
                    }
                },
                {
                    enableHighAccuracy: true,
                    timeout: 10000,
                    maximumAge: 0
                }
            );
        }
    </script>
</body>
</html>
"""


ADMIN_LOGIN_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Admin Login</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #eef2f6;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
        }
        .login-box {
            background: white;
            width: 360px;
            max-width: 92%;
            padding: 28px;
            border-radius: 14px;
            box-shadow: 0 10px 35px rgba(0,0,0,0.12);
        }
        input {
            width: 100%;
            padding: 12px;
            margin-top: 12px;
            box-sizing: border-box;
            border: 1px solid #ccc;
            border-radius: 8px;
        }
        button {
            width: 100%;
            margin-top: 14px;
            background: #1976d2;
            color: white;
            border: none;
            padding: 12px;
            border-radius: 8px;
            cursor: pointer;
        }
        .error {
            color: #c62828;
            margin-top: 12px;
        }
    </style>
</head>
<body>
    <div class="login-box">
        <h2>Admin Login</h2>
        <form method="POST">
            <input type="password" name="password" placeholder="Enter admin password" required>
            <button type="submit">Login</button>
        </form>
        {% if error %}
            <p class="error">{{ error }}</p>
        {% endif %}
    </div>
</body>
</html>
"""


DASHBOARD_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Metadata Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body {
            margin: 0;
            font-family: Arial, sans-serif;
            background: #e9edf2;
            color: #1f2937;
        }
        .topbar {
            background: #7f8b98;
            color: white;
            padding: 18px 28px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .topbar a {
            color: white;
            text-decoration: none;
            background: rgba(255,255,255,0.18);
            padding: 8px 12px;
            border-radius: 6px;
        }
        .container {
            max-width: 1250px;
            margin: 28px auto;
            padding: 0 18px;
        }
        .panel {
            background: white;
            border-radius: 12px;
            padding: 22px;
            box-shadow: 0 8px 28px rgba(0,0,0,0.10);
        }
        .summary {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 14px;
            margin-bottom: 18px;
        }
        .card {
            background: #f8fafc;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            padding: 15px;
        }
        .card h3 {
            margin: 0;
            font-size: 14px;
            color: #6b7280;
        }
        .card p {
            margin: 10px 0 0;
            font-size: 24px;
            font-weight: bold;
        }
        .main-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 18px;
        }
        #map {
            width: 100%;
            height: 430px;
            border-radius: 10px;
            border: 1px solid #d1d5db;
        }
        .details {
            height: 430px;
            overflow: auto;
            border: 1px solid #d1d5db;
            border-radius: 10px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
        }
        td, th {
            padding: 12px;
            border-bottom: 1px solid #e5e7eb;
            text-align: left;
            vertical-align: top;
        }
        th {
            background: #f3f4f6;
        }
        .logs {
            margin-top: 22px;
            overflow: auto;
            border: 1px solid #d1d5db;
            border-radius: 10px;
            max-height: 380px;
        }
        .small {
            font-size: 12px;
            color: #6b7280;
        }
        .btn {
            display: inline-block;
            background: #1976d2;
            color: white;
            padding: 7px 10px;
            border-radius: 6px;
            text-decoration: none;
            font-size: 13px;
        }
        .muted {
            color: #6b7280;
        }
        @media (max-width: 900px) {
            .main-grid {
                grid-template-columns: 1fr;
            }
            .summary {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="topbar">
        <strong>Metadata Dashboard</strong>
        <a href="/logout">Logout</a>
    </div>

    <div class="container">
        <div class="panel">
            <h1>Extended Data</h1>

            <div class="summary">
                <div class="card">
                    <h3>Total logs</h3>
                    <p>{{ logs|length }}</p>
                </div>
                <div class="card">
                    <h3>Location logs</h3>
                    <p>{{ location_count }}</p>
                </div>
                <div class="card">
                    <h3>Latest IP</h3>
                    <p style="font-size:18px;">{{ latest_ip }}</p>
                </div>
            </div>

            <div class="main-grid">
                <div>
                    <div id="map"></div>
                </div>
                <div class="details">
                    <table>
                        <tbody id="selectedDetails">
                            <tr>
                                <th>Field</th>
                                <th>Value</th>
                            </tr>
                            <tr>
                                <td>Status</td>
                                <td>Select a marker or check latest log below</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <h2>Recent Logs</h2>

            <div class="logs">
                <table>
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>IP</th>
                            <th>City</th>
                            <th>State</th>
                            <th>Country</th>
                            <th>Location</th>
                            <th>User Agent</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for log in logs %}
                        <tr>
                            <td>{{ log.get("timestamp_utc", "") }}</td>
                            <td>{{ log.get("ip", "") }}</td>
                            <td>{{ log.get("geocode", {}).get("city", "Not available") }}</td>
                            <td>{{ log.get("geocode", {}).get("state", "Not available") }}</td>
                            <td>{{ log.get("geocode", {}).get("country", "Not available") }}</td>
                            <td>
                                {% if log.get("location") %}
                                    {{ log["location"].get("latitude") }},
                                    {{ log["location"].get("longitude") }}
                                    <br>
                                    <span class="small">
                                        Accuracy: {{ log["location"].get("accuracy") }} meters
                                    </span>
                                {% else %}
                                    No GPS location
                                {% endif %}
                            </td>
                            <td class="small">
                                {{ log.get("headers", {}).get("User-Agent", "") }}
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>

            <p class="small muted">
                Country, city, state, and address are reverse-geocoded from browser-permitted GPS coordinates.
                They can be approximate.
            </p>
        </div>
    </div>

    <script>
        const logs = {{ logs|tojson }};

        function hasLocation(log) {
            return log.location &&
                   typeof log.location.latitude === "number" &&
                   typeof log.location.longitude === "number";
        }

        const locationLogs = logs.filter(hasLocation);

        let defaultCenter = [24.8608, 67.0104];
        let defaultZoom = 11;

        if (locationLogs.length > 0) {
            defaultCenter = [
                locationLogs[0].location.latitude,
                locationLogs[0].location.longitude
            ];
            defaultZoom = 14;
        }

        const map = L.map("map").setView(defaultCenter, defaultZoom);

        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution: "&copy; OpenStreetMap contributors"
        }).addTo(map);

        function valueOrEmpty(value) {
            if (value === undefined || value === null || value === "") {
                return "Not available";
            }
            return value;
        }

        function escapeHtml(value) {
            return String(valueOrEmpty(value))
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/\"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        function showDetails(log) {
            const latitude = log.location ? log.location.latitude : "";
            const longitude = log.location ? log.location.longitude : "";
            const accuracy = log.location ? log.location.accuracy : "";
            const geocode = log.geocode || {};
            const headers = log.headers || {};

            const googleMapsUrl = latitude && longitude
                ? "https://www.google.com/maps?q=" + latitude + "," + longitude
                : "";

            document.getElementById("selectedDetails").innerHTML = `
                <tr><th>Field</th><th>Value</th></tr>
                <tr><td>Session ID</td><td>${escapeHtml(log.session_id)}</td></tr>
                <tr><td>Timestamp UTC</td><td>${escapeHtml(log.timestamp_utc)}</td></tr>
                <tr><td>IP</td><td>${escapeHtml(log.ip)}</td></tr>
                <tr><td>Bot</td><td>${escapeHtml(log.bot)}</td></tr>
                <tr><td>Method</td><td>${escapeHtml(log.method)}</td></tr>
                <tr><td>Path</td><td>${escapeHtml(log.path)}</td></tr>
                <tr><td>Query string</td><td>${escapeHtml(log.query_string)}</td></tr>
                <tr><td>Country</td><td>${escapeHtml(geocode.country)}</td></tr>
                <tr><td>Country code</td><td>${escapeHtml(geocode.country_code)}</td></tr>
                <tr><td>State</td><td>${escapeHtml(geocode.state)}</td></tr>
                <tr><td>City</td><td>${escapeHtml(geocode.city)}</td></tr>
                <tr><td>Suburb</td><td>${escapeHtml(geocode.suburb)}</td></tr>
                <tr><td>Road</td><td>${escapeHtml(geocode.road)}</td></tr>
                <tr><td>Postcode</td><td>${escapeHtml(geocode.postcode)}</td></tr>
                <tr><td>Full address</td><td>${escapeHtml(geocode.display_name)}</td></tr>
                <tr><td>Geocode error</td><td>${escapeHtml(geocode.error)}</td></tr>
                <tr><td>Latitude</td><td>${escapeHtml(latitude)}</td></tr>
                <tr><td>Longitude</td><td>${escapeHtml(longitude)}</td></tr>
                <tr><td>Accuracy meters</td><td>${escapeHtml(accuracy)}</td></tr>
                <tr><td>Location source</td><td>${log.location ? "Browser permission" : "No GPS permission"}</td></tr>
                <tr><td>Referer</td><td>${escapeHtml(headers.Referer)}</td></tr>
                <tr><td>Accept language</td><td>${escapeHtml(headers["Accept-Language"])}</td></tr>
                <tr><td>User Agent</td><td>${escapeHtml(headers["User-Agent"])}</td></tr>
                <tr><td>Google Maps</td><td>${googleMapsUrl ? `<a class="btn" target="_blank" href="${googleMapsUrl}">Open location</a>` : "Not available"}</td></tr>
            `;
        }

        locationLogs.forEach(function(log) {
            const lat = log.location.latitude;
            const lng = log.location.longitude;

            const marker = L.marker([lat, lng]).addTo(map);

            marker.bindPopup(
                "<b>Session:</b> " + escapeHtml(log.session_id) +
                "<br><b>IP:</b> " + escapeHtml(log.ip) +
                "<br><b>City:</b> " + escapeHtml(log.geocode ? log.geocode.city : "") +
                "<br><b>Accuracy:</b> " + escapeHtml(log.location.accuracy) + " meters"
            );

            marker.on("click", function() {
                showDetails(log);
            });
        });

        if (logs.length > 0) {
            showDetails(logs[0]);
        }
    </script>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def home():
    session_id = get_session_id()
    headers = get_safe_headers()

    metadata = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "ip": request.remote_addr,
        "bot": is_probable_bot(headers.get("User-Agent")),
        "method": request.method,
        "path": request.path,
        "query_string": request.query_string.decode("utf-8", errors="replace"),
        "headers": headers
    }

    write_log(metadata)

    return render_template_string(
        CONSENT_PAGE,
        redirect_url=GOOGLE_MAPS_REDIRECT
    )


@app.route("/log-location", methods=["POST"])
def log_location():
    session_id = get_session_id()
    data = request.get_json(silent=True) or {}
    headers = get_safe_headers()

    latitude = data.get("latitude")
    longitude = data.get("longitude")
    accuracy = data.get("accuracy")

    geocode = reverse_geocode(latitude, longitude)

    metadata = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "ip": request.remote_addr,
        "bot": is_probable_bot(headers.get("User-Agent")),
        "method": request.method,
        "path": request.path,
        "query_string": request.query_string.decode("utf-8", errors="replace"),
        "headers": headers,
        "location": {
            "latitude": latitude,
            "longitude": longitude,
            "accuracy": accuracy,
            "location_timestamp": data.get("location_timestamp")
        },
        "geocode": geocode
    }

    write_log(metadata)

    return jsonify({
        "status": "ok",
        "message": "Location, reverse geocode, and request metadata logged",
        "session_id": session_id
    })


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        password = request.form.get("password", "")

        if password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect("/dashboard")

        return render_template_string(
            ADMIN_LOGIN_PAGE,
            error="Wrong password"
        )

    if session.get("admin_logged_in"):
        return redirect("/dashboard")

    return render_template_string(
        ADMIN_LOGIN_PAGE,
        error=None
    )


@app.route("/dashboard", methods=["GET"])
def dashboard():
    if not session.get("admin_logged_in"):
        return redirect("/admin")

    logs = read_logs()

    location_count = sum(
        1 for log in logs
        if log.get("location")
    )

    latest_ip = logs[0].get("ip", "No logs yet") if logs else "No logs yet"

    return render_template_string(
        DASHBOARD_PAGE,
        logs=logs,
        location_count=location_count,
        latest_ip=latest_ip
    )


@app.route("/logout", methods=["GET"])
def logout():
    session.pop("admin_logged_in", None)
    return redirect("/admin")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="127.0.0.1", port=port)
