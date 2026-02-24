
$(document).ready(function () {
  window.scrollTo(0, 0);

  let pendingSelect = null;
  let pendingProjId = null;
  let pendingOriginalStatus = null;

  function openModal() {
    $("#monitoringIdInput").val("");
    $("#idError").text("");
    $("#msgBox").show();
    $("#monitoringIdInput").focus();
  }

  function closeModal() {
    $("#msgBox").hide();
  }

  function revertSelect() {
    if (pendingSelect && pendingOriginalStatus) {
      pendingSelect.value = pendingOriginalStatus;
      const row = pendingSelect.closest("tr");
      row.classList.remove("greyed");
    }
    pendingSelect = null;
    pendingProjId = null;
    pendingOriginalStatus = null;
  }

  document.querySelectorAll(".status-select").forEach(function (select) {
    select.addEventListener("change", async function () {
      const row = select.closest("tr");
      const originalStatus = select.getAttribute("data-original-status");
      const currentStatus = select.value;
      const proj_id = select.getAttribute("data-proj-id");
      if (currentStatus !== originalStatus) {
            row.classList.add('greyed');
        } else {
            row.classList.remove('greyed');
        }
      // Only require ID for analyzing -> monitoring
      if (originalStatus === "analyzing" && currentStatus === "monitoring") {
        pendingSelect = select;
        pendingProjId = proj_id;
        pendingOriginalStatus = originalStatus;
        openModal();
        return;
      }

      try {
        await update_project_status(proj_id, null, currentStatus);
        select.setAttribute("data-original-status", currentStatus);
      } catch (err) {
        select.value = originalStatus; // revert
        row.classList.remove("greyed");
        alert(err.message);
      }
    });
  });

  // Cancel: close + revert
  $("#cancelBtn").on("click", function (e) {
    e.preventDefault();
    closeModal();
    revertSelect();
  });

  // Continue: validate then save
  $("#monitoringIdForm").on("submit", async function (e) {
    e.preventDefault();

    const monitoring_id = $("#monitoringIdInput").val().trim();

    if (!monitoring_id) {
      $("#idError").text("Monitoring ID is required.");
      return;
    }

    try {
      await update_project_status(pendingProjId, monitoring_id, "monitoring");
      pendingSelect.setAttribute("data-original-status", "monitoring");
      closeModal();
      pendingSelect = null;
      pendingProjId = null;
      pendingOriginalStatus = null;
    } catch (err) {
      $("#idError").text(err.message);
    }
  });
});




function show_modal_example_model() {
    // Select the table by its ID 'projectTable'
    var table = document.getElementById('projectTable');

    // The table rows, excluding the header
    var rows = table.querySelectorAll('tr:not(:first-child)');

    // If there are no rows (excluding the header), it means there are no projects
    if (rows.length == 1 && rows[0].innerText.includes("You do not yet have any saved projects")) {
        document.getElementById('projectExample').style.cssText = "display: block !important;";
    }
}

function set_and_show_error_msg() {
    if (window.location.href.includes("/?internal_error")) {
        let message = "An internal server error occurred!";
        if (navigator.userAgent.indexOf("Firefox") !== -1) {
            message = "An internal server error occurred!\nIt appears that you are using the Firefox browser. " +
                "The error could be related to Firefox. We recommend switching to Chrome or Edge instead.";
        }
        document.getElementById('responseMsg').textContent = message;
        document.getElementById('msgBox').style.display = "block";
    }
}

async function update_project_status(proj_id, monitoring_id, status) {
  const response = await fetch(updateProjectStatusUrl, {
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken,
    },
    method: "POST",
    body: JSON.stringify({
      proj_id: proj_id,
      status: status,
      monitoring_id: monitoring_id,
    }),
  });

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(data.error || "Failed to update project status");
  }

  return data;
}
