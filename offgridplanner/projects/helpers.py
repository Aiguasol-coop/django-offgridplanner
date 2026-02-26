import csv
import math
from collections import defaultdict
from pathlib import Path

import pandas as pd
from django.contrib.staticfiles.storage import staticfiles_storage
from django.forms import model_to_dict
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.utils.translation import gettext_lazy as _

from offgridplanner.projects.models import Project

TABLE_TEMPLATE_MAPPING = {
    "potential": "widgets/potential_table.html",
    "monitoring": "widgets/monitoring_table.html",
    "alarms": "widgets/alarms_table.html",
}


def get_geojson_centroid(latitudes: pd.Series, longitudes: pd.Series):
    """
    Returns centroid as [longitude, latitude] (GeoJSON format).
    Ignores NaN values safely.
    """
    if latitudes.empty or longitudes.empty:
        return None

    lat = latitudes.mean()
    lon = longitudes.mean()

    if pd.isna(lat) or pd.isna(lon):
        return None

    return [float(lon), float(lat)]


def format_sites_data(sites, table="potential"):
    """
    Takes the JSON from the site exploration API and fits the data into the corresponding geojson/table format to be
    displayed on the exploration site.
    Parameters:
        :param sites: JSON data from fetch_exploration_progress["minigrids"]
        :param table: table which will be rendered, template taken from TABLE_TEMPLATE_MAPPING

    Returns:
        geojson_data: Data to construct the map markers
        table_data: Data formatted into potential_table.html
    """
    # generate geoJSON for map
    table_template_path = TABLE_TEMPLATE_MAPPING[table]
    status = table
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": site["centroid"]["coordinates"],
            },
            "properties": {
                "name": site["id"] if status == "potential" else site["name"],
                "status": status,
            },
        }
        for site in sites
    ]

    # add proj_id to projects which are in the database
    if table == "monitoring":
        component_uuids = [site["component_uuid"] for site in sites]
        projects = Project.objects.filter(monitoring_id__in=component_uuids).values(
            "monitoring_id", "id"
        )
        project_lookup = {p["monitoring_id"]: p["id"] for p in projects}
        for site in sites:
            site["proj_id"] = project_lookup.get(site["component_uuid"], None)

    # generate table HTML
    context = {"sites": sites}
    table_div = render_to_string(table_template_path, context)
    return features, table_div


def collect_project_dataframes(proj_id):
    """
    Collects the following dataframes to use in excel and PDF export:
        input_df (DataFrame): DataFrame containing input data.
        energy_system_design (Any): Data related to energy system design.
        energy_flow_df (DataFrame): DataFrame containing energy flow data.
        results_df (DataFrame): DataFrame containing results data.
        nodes_df (DataFrame): DataFrame containing nodes data.
        links_df (DataFrame): DataFrame containing links data.
        custom_demand_df (DataFrame): DataFrame containing custom demand data.
    """
    project = get_object_or_404(Project, id=proj_id)
    project_df = pd.DataFrame(model_to_dict(project), index=[0])
    options_df = pd.DataFrame(model_to_dict(project.options), index=[0])
    grid_design_df = pd.DataFrame(model_to_dict(project.griddesign), index=[0])
    input_parameters_df = pd.concat([project_df, grid_design_df, options_df], axis=1)
    results_df = pd.DataFrame(model_to_dict(project.simulation.results), index=[0])
    energy_flow_df = project.energyflow.df
    nodes_df = project.nodes.df
    links_df = project.links.df
    energy_system_design_df = pd.DataFrame(
        model_to_dict(project.energysystemdesign), index=[0]
    )
    custom_demand_df = pd.DataFrame(model_to_dict(project.customdemand), index=[0])
    dataframes = {
        "project_df": project_df,
        "options_df": options_df,
        "grid_design_df": grid_design_df,
        "input_parameters_df": input_parameters_df,
        "results_df": results_df,
        "energy_flow_df": energy_flow_df,
        "nodes_df": nodes_df,
        "links_df": links_df,
        "energy_system_design_df": energy_system_design_df,
        "custom_demand_df": custom_demand_df,
    }
    return dataframes


def is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def format_column_names(df):
    # TODO check and fix later
    df.columns = [str(col).replace("_", " ").capitalize() for col in df.columns]
    return df


def from_nested_dict(model_cls, nested_data):
    """
    Convert a nested dict back to {field_name: value} for a Django model.
    """

    def flatten_dict(d, parent_key=""):
        items = []
        for k, v in d.items():
            new_key = f"{parent_key}__{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(flatten_dict(v, new_key))
            else:
                items.append((new_key, v))
        return items

    flat_items = flatten_dict(nested_data)

    # Map db_column -> field_name
    db_to_field = {
        field.db_column: field.name
        for field in model_cls._meta.fields  # noqa: SLF001
        if field.db_column
    }

    params = {}
    for db_column, value in flat_items:
        field_name = db_to_field.get(db_column)
        if not field_name:
            continue

        params[field_name] = value

        # Reverse the efficiency scaling
        if any(
            percentage_param in db_column.split("__")[-1]
            for percentage_param in ["soc", "efficiency", "max_shortage"]
        ):
            percentage_value = value * 100
            params[field_name] = percentage_value

    return params


def csv_to_dict(filepath, label_col="label"):
    """
    Converts a CSV file into a nested dictionary using a specified label column as keys.

    Parameters:
        filepath (str): Path to the CSV file.
        label_col (str): Column name to be used as the dictionary key.

    Returns:
        dict: Nested dictionary where each row is stored under its label.
    """
    result = {}

    file_path = staticfiles_storage.path(filepath)
    if Path(file_path).exists():
        with Path(file_path).open(encoding="utf-8-sig") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                label = row.pop(label_col)  # Remove label from row data
                result[label] = row  # Store remaining fields under this label
                if "verbose" in row:  # Store verbose as translatable string
                    row["verbose"] = _(row["verbose"])

    return result


def convert_value(value, dtype):
    if value == "":
        return None

    if dtype == "float":
        return float(value)
    elif dtype == "int":
        return int(value)
    elif dtype == "bool":
        return bool(value)
    elif dtype == "str":
        return str(value)
    else:
        msg = f"Type {dtype} not supported"
        raise ValueError(msg)


def get_param_from_metadata(param, model=None):
    """
    Extracts a specific parameter for all fields in the given model (or all fields if None) from the
    FORM_FIELD_METADATA dictionary and returns a dictionary with {fields: values} for the parameter.

    Parameters:
        param (str): Parameter to extract from the nested dictionary
        model (str): Model by which to filter the fields

    Returns:
        dict: Field labels and the corresponding value for param
    """
    if model is not None:
        param_dict = {
            field: convert_value(
                FORM_FIELD_METADATA[field][param], FORM_FIELD_METADATA[field]["type"]
            )
            for field in FORM_FIELD_METADATA
            if FORM_FIELD_METADATA[field]["model"] == model
        }
    else:
        param_dict = {
            field: convert_value(
                FORM_FIELD_METADATA[field][param], FORM_FIELD_METADATA[field]["type"]
            )
            for field in FORM_FIELD_METADATA
        }

    return param_dict


def group_form_by_component(form):
    """Create a nested dictionary of form fields split by component. This assumes that the db_column of the model field
    is formatted with a double underscore as 'component_name__parameter_name'.
    Parameters:
        form (ModelForm): ModelForm containing all fields to be displayed

    Returns:
        grouped_fields (collections.defaultdict): Nested dictionary with component as keys and lists of (label, field) tuples as values
    """
    grouped_fields = defaultdict(list)
    for field_name, field in form.fields.items():
        component_name = field.db_column.split("__")[0]
        grouped_fields[component_name].append((field_name, form[field_name]))
    return grouped_fields


def reorder_dict(d, old_index, new_index):
    items = list(d.items())  # Convert dictionary to list of key-value pairs
    item = items.pop(old_index)  # Remove the item at the old index
    items.insert(new_index, item)  # Insert it at the new index
    return dict(items)


def format_results_into_kpi_dict(res):
    df = pd.Series(model_to_dict(res))

    df = df.astype(float)
    output_kpis = OUTPUT_KPIS.copy()

    for kpi in output_kpis:
        output_kpis[kpi]["value"] = df[kpi].round(1)

    return output_kpis


def sanitize_output_kpis(output_kpis):
    # Necessary for the case that they need to be returned as JSON (when the results page is updated via JS and not through the view context)
    cleaned = {}
    for key, meta in output_kpis.items():
        cleaned[key] = {
            "verbose": meta.get("verbose", ""),
            "unit": meta.get("unit", ""),
            "value": round(float(meta.get("value")), 2)
            if not math.isnan(meta.get("value"))
            else None,
        }
    return cleaned


FORM_FIELD_METADATA = csv_to_dict("data/form_parameters.csv")
OUTPUT_KPIS = csv_to_dict("data/output_kpis.csv")
