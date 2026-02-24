const map = L.map('map').setView([-18.7845718, 34.499664], 5);

const osm = L.tileLayer(
  'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  { maxZoom: 18 }
);

const satellite = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  { attribution: 'Tiles © Esri' }
);

// Add default base layer
osm.addTo(map);

// Layer switcher
L.control.layers(
  {
    "OpenStreetMap": osm,
    "Satellite": satellite
  }
).addTo(map);

const monitoringSitesLayer = L.markerClusterGroup({
  disableClusteringAtZoom: 15,
  iconCreateFunction: (cluster) => {
    return L.divIcon({
      html: `<div class="cluster cluster-violet"><span>${cluster.getChildCount()}</span></div>`,
      className: 'cluster-wrapper',
      iconSize: L.point(40, 40)
    });
  }
}).addTo(map);

const monitoringMarker = new L.Icon({
  iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-violet.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41]
});

const lineColors = {
  Existing:  '#e67e22',       // orange
  Planned: '#cc99ff',       // fuchsia
  Unknown: '#f1c40f',       // fuchsia
};

// Line layer groups
const lineLayers = {
  Existing: L.layerGroup().addTo(map),
  Planned:  L.layerGroup().addTo(map),
  Unknown:  L.layerGroup().addTo(map),
};

loadLegend();

let shouldStop = false;

document.addEventListener('DOMContentLoaded', () => {
  const refreshBtn = document.getElementById("refresh-btn");
  const monitoringTable = document.getElementById("monitoring-table");
  const alarmsTable = document.getElementById("alarms-table");

  // Load previous data on first load
  updateResults(monit_table, monit_geojson, "monitoring-table");
  updateResults(alarms_table, null, "alarms-table");

  // load grid network on map
  addGridToMap(grid_network);

  refreshBtn.addEventListener("click", async (event) => {
    $("#loading_spinner").show();
    try {
    const resp = await fetch(fetchMonitoringDataUrl);
    if (!resp.ok) {
      $("#loading_spinner").hide();
      const msg = await resp.json();
      document.getElementById('responseMsg').textContent = msg.error;
      document.getElementById('msgBox').style.display = "block";

    } else {
      $("#loading_spinner").hide();
      data = await resp.json();
      updateResults(data.monit_table, data.monit_geojson, "monitoring-table");
      updateResults(data.alarms_table, null, "alarms-table");
    };
    } catch (err) {
        console.log("An error occurred", err);
    }
  });
});



function updateResults(table_data, map_data, table_id) {
  if (table_data !== null) {
      // Update table
      document.getElementById(table_id).innerHTML = table_data;
      }
  if (map_data !== null) {
      // Update map
      monitoringSitesLayer.clearLayers();
      map_data.forEach(feature => {
        let status = feature.properties.status;
        let [lng, lat] = feature.geometry.coordinates;
        let id = feature.properties.name;

        const content = `
          <div>ID: ${id}</div>
        `;
        marker = L.marker([lat, lng], { icon: monitoringMarker }).bindPopup(content);
        monitoringSitesLayer.addLayer(marker);
    });
  }
  }

let currentSortIndex = null;
let sortAscending = true;

function sortTable(colIndex, th) {
  const table = th.closest("table");
  const tbody = table.querySelector("tbody");
  const rows = Array.from(tbody.querySelectorAll("tr"));

  // Determine sort direction
  if (currentSortIndex === colIndex) {
    sortAscending = !sortAscending;
  } else {
    sortAscending = true;
    currentSortIndex = colIndex;
  }

  // Sort the rows
  rows.sort((a, b) => {
    const valA = a.cells[colIndex].textContent.trim();
    const valB = b.cells[colIndex].textContent.trim();
    const numA = parseFloat(valA);
    const numB = parseFloat(valB);

    if (!isNaN(numA) && !isNaN(numB)) {
      return sortAscending ? numA - numB : numB - numA;
    } else {
      return sortAscending
        ? valA.localeCompare(valB)
        : valB.localeCompare(valA);
    }
  });

  // Reattach rows
  rows.forEach(row => tbody.appendChild(row));

  // Reset all headers
  const headers = th.parentElement.children;
  Array.from(headers).forEach(header => header.classList.remove("asc", "desc"));

  // Set class on current header
  th.classList.add(sortAscending ? "asc" : "desc");
}

function loadLegend() {
  if (typeof legend === 'undefined' || !legend) {
    window.legend = L.control({ position: 'bottomright' });
  } else {
    map.removeControl(legend);
  }

  const labels = [
    {
      img: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-violet.png',
      text: 'Monitored minigrid'
    },
  ];

  legend.onAdd = function () {
    var div = L.DomUtil.create('div', 'info legend');

    labels.forEach(({ img, text }) => {
      const row = document.createElement('div');
      row.className = 'legend-row';

      const icon = document.createElement('img');
      icon.src = img;
      icon.alt = text + ' marker';
      icon.className = 'legend-icon';

      const span = document.createElement('span');
      span.textContent = text;

      row.appendChild(icon);
      row.appendChild(span);
      div.appendChild(row);
    });

       // Grid lines
      Object.keys(lineColors).forEach((key) => {
        const row = document.createElement('div');
        row.className = 'legend-row';

        const swatch = document.createElement('span');
        swatch.className = 'legend-line';
        swatch.style.borderTop = `3px solid ${lineColors[key]}`;

        const label = document.createElement('span');
        label.textContent = key + " line";

        row.appendChild(swatch);
        row.appendChild(label);
        div.appendChild(row);
      });
  return div;
  };
  legend.addTo(map);
}

function lonLatToLatLng(coords) {
  // coords is an array like [lon, lat]
  return [coords[1], coords[0]];
}

function addGridToMap(gridNetwork) {
    gridNetwork.forEach(item => {
        const { geography, status, vltg_kv, classes, province, length_km } = item;
        if (!geography || geography.type !== 'LineString' || !Array.isArray(geography.coordinates)) return;

        const color = lineColors[status] || '#7f8c8d';
        const latlngs = geography.coordinates.map(lonLatToLatLng);

        const line = L.polyline(latlngs, {
          color,
          weight: 2,
        });

      line.bindPopup(
      `<div style="min-width:180px">
         <div><b>Status:</b> ${status ?? 'n/a'}</div>
         <div><b>Voltage:</b> ${vltg_kv ?? 'n/a'} kV</div>
         <div><b>Class:</b> ${classes ?? 'n/a'}</div>
         <div><b>Province:</b> ${province ?? 'n/a'}</div>
         <div><b>Length:</b> ${length_km?.toFixed ? length_km.toFixed(2) : length_km} km</div>
       </div>`
    );

    const targetLayer = lineLayers[status];

    line.addTo(targetLayer);
});
}
