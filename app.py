import os
from typing import Tuple, List, Dict, Any

import requests
import streamlit as st
from streamlit_folium import st_folium
import folium
from folium import GeoJson, GeoJsonTooltip, LayerControl

# -------------------------
# Page setup
# -------------------------
st.set_page_config(
    page_title="Flash Flood Warning & Rainfall Tracker",
    layout="wide",
)

# -------------------------
# Config / constants
# -------------------------
# Prefer USER_AGENT from env or Streamlit secrets, fallback to a generic string.
USER_AGENT = os.getenv("USER_AGENT", "")
try:
    if not USER_AGENT:
        USER_AGENT = st.secrets.get("USER_AGENT", "")
except Exception:
    pass
if not USER_AGENT:
    USER_AGENT = "FlashFloodTracker/1.0 (contact: youremail@example.com)"

NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"
NWS_ALERTS_PARAMS = {"event": "Flash Flood Warning", "status": "actual"}

# MRMS WMS from Iowa State IEM: useful layers include mrms_p1h (1-hr) and mrms_p24h (24-hr)
MRMS_WMS_BASE = "https://mesonet.agron.iastate.edu/cgi-bin/wms/us/mrms_nn.cgi"

STATE_ABBRS = [
    "AL","AK","AZ","AR","CA","CO","CT","DC","DE","FL","GA","HI","IA","ID","IL","IN",
    "KS","KY","LA","MA","MD","ME","MI","MN","MO","MS","MT","NC","ND","NE","NH","NJ",
    "NM","NV","NY","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VA","VT","WA",
    "WI","WV","WY","PR","GU","VI"
]

# -------------------------
# Sidebar controls
# -------------------------
st.sidebar.header("Filters")
county_query = st.sidebar.text_input("County name (e.g., 'Travis')", "")
state_query = st.sidebar.selectbox("State/Territory", ["(Any)"] + STATE_ABBRS, index=0)

st.sidebar.header("Layers")
show_p1h = st.sidebar.checkbox("Show 1-hour rainfall (MRMS)", value=True)
show_p24h = st.sidebar.checkbox("Show 24-hour rainfall (MRMS)", value=True)
opacity = st.sidebar.slider("Rainfall layer opacity", 0.0, 1.0, 0.55, 0.05)

st.sidebar.caption(
    "Data sources: NWS Alerts API (active Flash Flood Warnings) and MRMS QPE via IEM WMS."
)

# -------------------------
# Helpers
# -------------------------
@st.cache_data(ttl=120, show_spinner=False)
def fetch_nws_flash_flood_warnings() -> Dict[str, Any]:
    """Fetch active Flash Flood Warnings (GeoJSON) from the NWS API."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/geo+json",
    }
    resp = requests.get(NWS_ALERTS_URL, params=NWS_ALERTS_PARAMS, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()

def feature_matches_county_state(feature: Dict[str, Any], county_q: str, state_q: str) -> bool:
    """Filter a GeoJSON feature using substring checks in properties.areaDesc."""
    if not county_q and (not state_q or state_q == "(Any)"):
        return True
    props = feature.get("properties", {})
    area_desc = (props.get("areaDesc") or "").lower()
    county_ok = True
    state_ok = True
    if county_q:
        county_ok = county_q.strip().lower() in area_desc
    if state_q and state_q != "(Any)":
        state_ok = state_q.lower() in area_desc
    return county_ok and state_ok

def compute_initial_map_center(geojson: Dict[str, Any]) -> Tuple[float, float, int]:
    """
    Compute a rough map center/zoom from the first Polygon/MultiPolygon/Point.
    Fallback to a CONUS-ish view if nothing found.
    """
    try:
        for feat in geojson.get("features", []):
            geom = feat.get("geometry") or {}
            gtype = geom.get("type")
            if gtype == "Polygon":
                coords = geom.get("coordinates", [])[0]
                if coords:
                    lon = sum(c[0] for c in coords) / len(coords)
                    lat = sum(c[1] for c in coords) / len(coords)
                    return lat, lon, 6
            elif gtype == "MultiPolygon":
                polys = geom.get("coordinates", [])
                if polys and polys[0] and polys[0][0]:
                    coords = polys[0][0]
                    lon = sum(c[0] for c in coords) / len(coords)
                    lat = sum(c[1] for c in coords) / len(coords)
                    return lat, lon, 6
            elif gtype == "Point":
                lon, lat = geom.get("coordinates", [-98.583, 39.833])
                return lat, lon, 6
    except Exception:
        pass
    return 39.833, -98.583, 4  # CONUS fallback

def add_mrms_wms_layer(m: folium.Map, layer_name: str, label: str, opacity: float = 0.55) -> None:
    """Add an MRMS WMS raster overlay from IEM to the map."""
    folium.raster_layers.WmsTileLayer(
        url=MRMS_WMS_BASE,
        name=label,
        fmt="image/png",
        transparent=True,
        version="1.3.0",
        layers=layer_name,
        opacity=opacity,
        control=True,
        attr="MRMS QPE via IEM",
    ).add_to(m)

def style_fn(_):
    return {"color": "#B80D57", "weight": 2, "fillOpacity": 0.15}

def highlight_fn(_):
    return {"weight": 3, "color": "#FF2E63", "fillOpacity": 0.25}

# -------------------------
# Fetch data
# -------------------------
with st.spinner("Fetching active Flash Flood Warnings from NWS…"):
    try:
        alerts_geojson = fetch_nws_flash_flood_warnings()
    except Exception as e:
        st.error(f"Failed to load NWS alerts: {e}")
        st.stop()

# Filter features by county/state (string match against areaDesc)
all_features = alerts_geojson.get("features", [])
features: List[Dict[str, Any]] = [
    f for f in all_features if feature_matches_county_state(f, county_query, state_query)
]

# -------------------------
# Build the map
# -------------------------
center_lat, center_lon, zoom = compute_initial_map_center(
    {"type": "FeatureCollection", "features": features or all_features}
)

m = folium.Map(location=[center_lat, center_lon], zoom_start=zoom, tiles="CartoDB positron")

# MRMS overlays
if show_p24h:
    add_mrms_wms_layer(m, "mrms_p24h", "MRMS 24-hour Precip", opacity=opacity)
if show_p1h:
    add_mrms_wms_layer(m, "mrms_p1h", "MRMS 1-hour Precip", opacity=opacity)

# ---- Safe tooltip creation (avoids AssertionError when features == []) ----
tooltip = None
if features:
    sample_props = (features[0].get("properties") or {})
    desired_fields = ["headline", "areaDesc", "severity", "certainty", "effective", "expires"]
    fields = [f for f in desired_fields if f in sample_props]
    if fields:  # only attach if at least one field exists
        aliases_map = {
            "headline": "Headline",
            "areaDesc": "Areas",
            "severity": "Severity",
            "certainty": "Certainty",
            "effective": "Effective",
            "expires": "Expires",
        }
        aliases = [aliases_map[f] for f in fields]
        tooltip = GeoJsonTooltip(
            fields=fields,
            aliases=aliases,
            sticky=True,
            localize=True,
        )

# Alerts polygons/lines (works even if features == [])
GeoJson(
    {"type": "FeatureCollection", "features": features},
    name="Active Flash Flood Warnings (NWS)",
    style_function=style_fn,
    highlight_function=highlight_fn,
    tooltip=tooltip,  # may be None when no features
).add_to(m)

LayerControl(collapsed=False).add_to(m)

# -------------------------
# Layout
# -------------------------
st.title("Flash Flood Warning & Rainfall Tracker")
st.write(
    "Overlay of **active NWS Flash Flood Warnings** with **MRMS radar-estimated precipitation** "
    "(1-hour and 24-hour). Filter by county/state to zero in on potential hotspots."
)

col_map, col_panel = st.columns([2.1, 1.0], gap="large")

with col_map:
    st_folium(m, height=740, returned_objects=[])

with col_panel:
    st.subheader("Active Warnings (filtered)")
    if not features:
        st.info("No active Flash Flood Warnings match the current filter. Try clearing county/state.")
    else:
        for f in features:
            p = f.get("properties", {})
            st.markdown(
                f"**{p.get('headline','Flash Flood Warning')}**  \n"
                f"{p.get('areaDesc','')}  \n"
                f"**Severity:** {p.get('severity','')} • **Certainty:** {p.get('certainty','')}  \n"
                f"**Effective:** {p.get('effective','')}  \n"
                f"**Expires:** {p.get('expires','')}"
            )
            st.divider()

st.caption(
    "Rainfall layers: MRMS QPE (radar-only, ~1 km). 24-hr accumulation highlights recent hotspots; "
    "1-hr layer helps spot ongoing heavy rain. Alerts: NWS GeoJSON polygons. "
    "Times are ISO-8601 in your browser’s timezone."
)
