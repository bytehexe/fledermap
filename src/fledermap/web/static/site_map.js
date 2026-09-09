// Site detail page's mini-map (Obsidian backlog, 2026-09-09: "Site's details
// page also needs a minimap!"). Parallel to session_map.js's mini-map, but
// this one also draws the site's own boundary circle -- same blue circle
// style the main map's site layer uses -- since "where is this site, and
// how big is it" is the whole point here, not just "which recordings are
// in it" (recordings.geojson?site=<id> alone would show the markers but
// not the circle they were clustered into). Guarded on the container's
// presence since this script is loaded only on site_detail.html.
document.addEventListener("DOMContentLoaded", () => {
  const container = document.getElementById("site-mini-map");
  if (!container) return;

  const siteId = container.dataset.siteId;
  const lat = parseFloat(container.dataset.lat);
  const lng = parseFloat(container.dataset.lng);
  const radiusM = parseFloat(container.dataset.radiusM);

  const map = L.map(container).setView([51.0, 10.0], 6);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap contributors",
  }).addTo(map);

  const circle = L.circle([lat, lng], { radius: radiusM, color: "blue" }).addTo(map);
  map.fitBounds(circle.getBounds(), { maxZoom: 15 });

  fetch(`/api/recordings.geojson?site=${siteId}`)
    .then((response) => response.json())
    .then((data) => {
      L.geoJSON(data, {
        pointToLayer: (feature, latlng) =>
          L.circleMarker(latlng, { color: "#333333" }),
      }).addTo(map);
    })
    .catch((err) => console.error("site mini-map fetch failed", err));
});
