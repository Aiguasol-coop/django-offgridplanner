const map = L.map('map').setView([9.0725, 7.5377], 5); // Centered on Europe
let sites = {};

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 18,
}).addTo(map);

const markers = L.markerClusterGroup();

document.addEventListener('DOMContentLoaded', () => {
  const filterForm = document.getElementById("filter-form");
  const startExplorationBtn = document.getElementById("exploration-btn");
  let shouldStop = false;

    document.getElementById('stop-btn').addEventListener('click', () => {
        const response = fetch(stopExplorationUrl);
        shouldStop = true;
    });

  let markersLayer = L.markerClusterGroup().addTo(map); // Reusable marker layer

  // Filter button click
  startExplorationBtn.addEventListener("click", async (event) => {
    event.preventDefault();
    const formData = new FormData(filterForm);
    await sendRequest(formData);
  });

  // Trigger the filter on first load
//  loadExplorationSitesBtn.click();
});

async function sendRequest(body) {
  shouldStop = false;
  const response = await fetch(startExplorationUrl, {
    method: "POST",
    headers: { 'X-CSRFToken': csrfToken },
    body: body
  });
  if (!response.ok) {
    console.error("Failed to start exploration");
    return;
  }
  const data = await response.json();
  startExplorationBtn.disabled = true;

  if (data.status === "DONE") {
    updateResults(data);
  } else if (data.status === "RUNNING") {
    $("#loading_spinner").show();
    pollExplorationResults();
  }
}

async function pollExplorationResults() {
  while (!shouldStop) {
    const response = await fetch(loadExplorationSitesUrl);
    console.log("Polling exploration data...")

    if (!response.ok) {
      console.error("Failed to fetch exploration results");
      $("#loading_spinner").hide();
      break;
    }

    const data = await response.json();
    updateResults(data);

    if (data.status === "DONE") {
      $("#loading_spinner").hide();
      break;
    }

    await new Promise(resolve => setTimeout(resolve, 5000)); // wait 5s
  }
  $("#loading_spinner").hide();
}

function updateResults(data) {
  // Update table
  document.querySelector('#sites-table').innerHTML = data.table;
  // Update map
  markersLayer.clearLayers();
  data.geojson.forEach(feature => {
    const [lng, lat] = feature.geometry.coordinates;
    const props = feature.properties;
    const content = `
      <h3>ID: ${props.id}</h3>
      <p>Building count: ${props.building_count}</p>
      <p>Grid distance: ${props.grid_dist}</p>
      <a href="${projectSetupUrl}">Create project from site</a>`;
    const marker = L.marker([lat, lng]).bindPopup(content);
    markersLayer.addLayer(marker);
  });
}

//const legend = L.control({ position: 'bottomright' });

//legend.onAdd = function () {
//  const div = L.DomUtil.create('div', 'info legend');
//  const grades = [0, 10, 20, 40, 60, 80];
//
//  for (let i = 0; i < grades.length; i++) {
//    div.innerHTML +=
//      `<i style="background:${getColor(grades[i] + 1)}"></i> ` +
//      `${grades[i]}${grades[i + 1] ? '&ndash;' + grades[i + 1] + '<br>' : '+'}`;
//  }
//
//  return div;
//};

//legend.addTo(map);

//function getColor(building_count) {
//  // Customize the ranges as you like
//  return building_count > 80 ? '#800026' :
//         building_count > 60 ? '#BD0026' :
//         building_count > 40 ? '#E31A1C' :
//         building_count > 20 ? '#FD8D3C' :
//         building_count > 10 ? '#FEB24C' :
//                               '#FFEDA0';
//}

function onEachFeature(feature, layer) {
  const props = feature.properties;
  layer.bindPopup(`ID: ${props.id}<br>Buildings: ${props.building_count}<br>Grid Distance: ${props.grid_dist}`);
}

//function pointToLayer(feature, latlng) {
//  return L.circleMarker(latlng, {
//    radius: 8,
//    fillColor: getColor(feature.properties.building_count),
//    color: '#000',
//    weight: 1,
//    opacity: 1,
//    fillOpacity: 0.8
//  });
//}

let currentSortIndex = null;
let sortAscending = true;

function sortTable(colIndex, th) {
  const table = document.getElementById("sites-table");
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
