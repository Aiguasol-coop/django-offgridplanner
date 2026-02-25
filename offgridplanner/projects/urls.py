from django.urls import path

from .views import *

app_name = "projects"

urlpatterns = [
    path("", projects_list, name="home"),
    path("projects", projects_list, name="projects_list"),
    path("projects/status/<str:status>", projects_list, name="projects_list"),
    path("duplicate/<int:proj_id>", project_duplicate, name="project_duplicate"),
    path("delete/<int:proj_id>", project_delete, name="project_delete"),
    path("update_project_status", update_project_status, name="update_project_status"),
    path(
        "export_results/<int:proj_id>",
        export_project_results,
        name="export_project_results",
    ),
    path(
        "export_report/<int:proj_id>",
        download_pdf_report,
        name="download_pdf_report",
    ),
    path(
        "download_excel_results/<int:proj_id>",
        download_excel_results,
        name="download_excel_results",
    ),
    path("projects/potential/map/", potential_map, name="potential_map"),
    path("projects/monitoring/map", monitoring, name="monitoring"),
    path("ajax/exploration/start", start_exploration, name="start_exploration"),
    path("ajax/exploration/stop", stop_exploration, name="stop_exploration"),
    path(
        "ajax/load_exploration_sites/",
        load_exploration_sites,
        name="load_exploration_sites",
    ),
    path("ajax/populate_site_data/", populate_site_data, name="populate_site_data"),
    path(
        "ajax/monitoring/refresh/",
        refresh_monitoring_data,
        name="refresh_monitoring_data",
    ),
    path("save_to_projects/<int:proj_id>", save_to_projects, name="save_to_projects"),
]
