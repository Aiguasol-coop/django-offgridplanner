import io
import logging
import os

import numpy as np
import pandas as pd
import pycountry
import xlsxwriter.utility
from country_bounding_boxes import country_subunits_by_iso_code
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from rest_framework.generics import get_object_or_404

from config.settings.base import DEFAULT_COUNTRY
from config.settings.base import LANGUAGES
from offgridplanner.optimization.models import Results
from offgridplanner.optimization.processing import GridProcessor
from offgridplanner.optimization.processing import SupplyProcessor
from offgridplanner.optimization.supply.demand_estimation import ENTERPRISE_LIST
from offgridplanner.optimization.supply.demand_estimation import LARGE_LOAD_KW_MAPPING
from offgridplanner.optimization.supply.demand_estimation import LARGE_LOAD_LIST
from offgridplanner.optimization.supply.demand_estimation import PUBLIC_SERVICE_LIST
from offgridplanner.projects.models import Project

logger = logging.getLogger(__name__)


def _build_consumer_detail_reverse_map():
    all_values = list(ENTERPRISE_LIST) + list(PUBLIC_SERVICE_LIST)
    reverse = {v: v for v in all_values}
    for lang, _verbose in LANGUAGES:
        with translation.override(lang):
            for val in all_values:
                reverse[str(_(val))] = val
    return reverse


CONSUMER_DETAIL_REVERSE_MAP = _build_consumer_detail_reverse_map()

CONSUMER_TYPE_LABEL_KEYS = {
    "household": "Household",
    "enterprise": "Enterprise",
    "public_service": "Public Service",
}


def _build_consumer_type_reverse_map():
    reverse = {key: key for key in CONSUMER_TYPE_LABEL_KEYS}
    reverse.update({label: key for key, label in CONSUMER_TYPE_LABEL_KEYS.items()})
    for lang, _verbose in LANGUAGES:
        with translation.override(lang):
            for key, label in CONSUMER_TYPE_LABEL_KEYS.items():
                reverse[str(_(label))] = key
    return reverse


CONSUMER_TYPE_REVERSE_MAP = _build_consumer_type_reverse_map()


def df_to_file(df, file_type):
    if file_type == "xlsx":
        output = io.BytesIO()
        df.to_excel(output, index=False, engine="xlsxwriter")
        output.seek(0)
        return io.BytesIO(output.getvalue())
    if file_type == "csv":
        output = io.StringIO()
        df.to_csv(output, index=False)
        output.seek(0)
        return io.StringIO(output.getvalue())
    else:
        err = f"File type .{file_type} not supported"
        raise ValueError(err)


def validate_file_extension(filename):
    allowed_extensions = ["csv", "xlsx"]
    file_extension = filename.split(".")[-1].lower()
    if file_extension not in allowed_extensions:
        return False, "Unsupported file type. Please upload a CSV or Excel file."
    return True, file_extension


def convert_file_to_df(file, file_extension):
    try:
        if file_extension == "csv":
            decoded_content = file.read().decode("utf-8")
            df = pd.read_csv(io.StringIO(decoded_content))
        else:  # "xlsx"
            df = pd.read_excel(io.BytesIO(file.read()), engine="openpyxl")

    except UnicodeDecodeError:
        return JsonResponse(
            {"responseMsg": "File encoding error. Please check the file format."},
            status=400,
        )
    except pd.errors.ParserError:
        return JsonResponse(
            {"responseMsg": "Error parsing the file. Ensure it is properly formatted."},
            status=400,
        )
    except OSError as e:
        return JsonResponse(
            {"responseMsg": f"File read/write error: {e!s}"}, status=500
        )

    if df.empty:
        return JsonResponse({"responseMsg": "Uploaded file is empty."}, status=400)
    return df


def check_missing_columns(df, required_columns):
    df.columns = [col.strip().lower() for col in df.columns]
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        error = f"Missing required columns: {missing_columns}"
        raise ValidationError(error)


def set_default_values(df, defaults):
    df = df.replace(["", "n.a."], np.nan)
    for col, val in defaults.items():
        if col in df.columns:
            df[col] = df[col].fillna(val)
    return df


def validate_column_inputs(input_values, column):
    # TODO get these directly from load profiles instead of manual
    allowed_values = {
        "consumer_type": {"household", "enterprise", "public_service"},
        "shs_options": {0, 1},
        "consumer_detail": {
            "",
            "default",
            "low",
            "very_low",
            "middle",
            "high",
            "very_high",
        }
        | set(ENTERPRISE_LIST)
        | set(PUBLIC_SERVICE_LIST),
        "custom_specification": {
            f"{machine} ({LARGE_LOAD_KW_MAPPING[machine]}kW)"
            for machine in LARGE_LOAD_LIST
        }
        | {""},
    }
    invalid_values = set(input_values) - allowed_values[column]
    if invalid_values:
        error = f"Invalid consumer_type values: {list(invalid_values)}. Allowed: {allowed_values[column]}"
        raise ValidationError(error)


def validate_consumer_type_consistency(df):
    """Check that consumer_detail/custom_specification match the row's consumer_type.

    validate_column_inputs only checks each column against the union of all
    allowed values, so e.g. an enterprise detail value on a household row
    would otherwise pass unnoticed.
    """
    allowed_detail_by_type = {
        "household": {"", "default"},
        "enterprise": set(ENTERPRISE_LIST),
        "public_service": set(PUBLIC_SERVICE_LIST),
    }
    mismatched_detail = df[
        ~df.apply(
            lambda row: row["consumer_detail"]
            in allowed_detail_by_type.get(row["consumer_type"], set()),
            axis=1,
        )
    ]
    if not mismatched_detail.empty:
        error = (
            "consumer_detail does not match the selected consumer_type for "
            f"the following rows: {mismatched_detail[['consumer_type', 'consumer_detail']].to_dict('records')}"
        )
        raise ValidationError(error)

    invalid_custom_spec = df[
        (df["custom_specification"] != "") & (df["consumer_type"] != "enterprise")
    ]
    if not invalid_custom_spec.empty:
        error = (
            "custom_specification is only allowed for enterprise consumers: "
            f"{invalid_custom_spec[['consumer_type', 'custom_specification']].to_dict('records')}"
        )
        raise ValidationError(error)


def convert_column_types(df, column_types):
    for col, dtype in column_types.items():
        try:
            df[col] = df[col].astype(dtype)
        except ValueError as e:
            error = f"Error converting '{col}' to {dtype.__name__}: {e}"
            raise ValidationError(error) from e
    return df


def get_country_bounds(proj_id):
    project = get_object_or_404(Project, id=proj_id)

    country = project.country
    country_verbose = pycountry.countries.get(alpha_2=country).name
    country_info = country_subunits_by_iso_code(country)
    bboxes = {c.subunit: c.bbox for c in country_info}

    # Pick the bounding box that encompasses the country and not one of its subunits
    try:
        bbox = bboxes[country_verbose]
    except KeyError:
        logger.warning(
            "An error occurred fetching bounding box data. Either no data was returned, or an error occurred "
            "fetching entire country bounds instead of sub-units. Defaulting to %s bounds",
            DEFAULT_COUNTRY[1],
        )
        country_info = country_subunits_by_iso_code(DEFAULT_COUNTRY[0])
        bboxes = {c.subunit: c.bbox for c in country_info}
        bbox = bboxes[DEFAULT_COUNTRY[1]]

    bounds_data = {
        "longitude_min": bbox[0],
        "latitude_min": bbox[1],
        "longitude_max": bbox[2],
        "latitude_max": bbox[3],
    }

    return bounds_data


def check_geographic_bounds(df, proj_id):
    max_distance = float(os.environ.get("MAX_LAT_LON_DIST", 0.15))
    if (
        df["latitude"].max() - df["latitude"].min() > max_distance
        or df["longitude"].max() - df["longitude"].min() > max_distance
    ):
        error_msg = "Distance between consumers exceeds maximum allowed distance."
        raise ValidationError(error_msg)

    country_bounds = get_country_bounds(proj_id)
    out_of_bounds = df[
        (df["latitude"] < country_bounds["latitude_min"])
        | (df["latitude"] > country_bounds["latitude_max"])
        | (df["longitude"] < country_bounds["longitude_min"])
        | (df["longitude"] > country_bounds["longitude_max"])
    ]
    if not out_of_bounds.empty:
        error_msg = (
            "Some latitude/longitude values are outside the selected country bounds."
        )
        raise ValidationError(error_msg)


def check_imported_consumer_data(df, proj_id):
    """Validate imported consumer data."""
    if df.empty:
        error = "No data could be read."
        raise ValidationError(error)

    check_missing_columns(df, required_columns=["latitude", "longitude"])
    # Default values
    defaults = {
        "consumer_detail": "",
        "consumer_type": "household",
        "custom_specification": "",
        "shs_options": 0,
        "consumer_name": "",
    }
    df = set_default_values(df, defaults)
    df["is_connected"], df["how_added"], df["node_type"] = True, "automatic", "consumer"
    # Normalize translated values back to English keys
    df["consumer_detail"] = df["consumer_detail"].map(
        lambda x: CONSUMER_DETAIL_REVERSE_MAP.get(x, x)
    )
    df["consumer_type"] = df["consumer_type"].map(
        lambda x: CONSUMER_TYPE_REVERSE_MAP.get(x, x)
    )
    # Validate column inputs
    for col in [
        "consumer_type",
        "shs_options",
        "consumer_detail",
        "custom_specification",
    ]:
        if col == "custom_specification":
            custom_loads = df.loc[df[col] != "", col].tolist()
            processed_loads = []
            for entry in custom_loads:
                # split if multiple machinery entries in one enterprise
                machinery = entry.split(";")
                # separate machine name for validation
                processed_entry = [
                    (
                        load.split(" x ", 1)[1]
                        if " x " in load and load[0].isdigit()
                        else load
                    )
                    for load in machinery
                ]
                # add to processed loads list
                processed_loads.extend(processed_entry)
            validate_column_inputs(processed_loads, col)
        else:
            validate_column_inputs(set(df[col]), col)
    validate_consumer_type_consistency(df)

    # Convert column types
    column_types = {
        "latitude": float,
        "longitude": float,
        "shs_options": int,
        "consumer_type": str,
        "custom_specification": str,
        "is_connected": bool,
    }
    convert_column_types(df, column_types)
    # Check geographic bounds
    check_geographic_bounds(df, proj_id)
    base_columns = [
        "latitude",
        "longitude",
        "how_added",
        "node_type",
        "consumer_type",
        "custom_specification",
        "shs_options",
        "consumer_detail",
        "is_connected",
    ]
    if "consumer_name" in df.columns:
        df = df[["consumer_name", *base_columns]]
    else:
        df = df[base_columns]

    return df, ""


def consumer_data_to_file(df, file_type):
    if df.empty:
        df = pd.DataFrame(
            columns=[
                "consumer_name",
                "latitude",
                "longitude",
                "consumer_type",
                "custom_specification",
                "shs_options",
                "consumer_detail",
            ],
        )
    else:
        df = df.drop(columns=["is_connected", "is_fixed", "how_added", "node_type"])

    if file_type == "xlsx":
        return consumer_data_to_formatted_excel(df)
    return df_to_file(df, file_type)


def consumer_data_to_formatted_excel(df):
    output = io.BytesIO()
    # Translate the existing consumer detail data to have export in portuguese
    df["consumer_detail"] = df["consumer_detail"].map(lambda x: _(x))

    # Column headers (translated); reused below for the data column itself,
    # the dropdown source and the named ranges the dependent dropdown needs.
    consumer_type_labels = {
        key: str(_(label)) for key, label in CONSUMER_TYPE_LABEL_KEYS.items()
    }
    # Translate the consumer_type data column too, so the value shown/selected
    # in the dropdown matches the translated options below. Imports map it
    # back to the canonical English key via CONSUMER_TYPE_REVERSE_MAP.
    df["consumer_type"] = df["consumer_type"].map(
        lambda x: consumer_type_labels.get(x, x)
    )
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
        consumer_type_col = df.columns.get_loc("consumer_type")
        consumer_detail_col = df.columns.get_loc("consumer_detail")
        workbook = writer.book
        ws = writer.sheets["Sheet1"]
        options_ws = workbook.add_worksheet(str(_("Options")))
        validation_options = {
            "household": ["default"],
            "enterprise": [_(enterprise) for enterprise in ENTERPRISE_LIST],
            "public_service": [_(service) for service in PUBLIC_SERVICE_LIST],
        }

        # Formats
        header_fmt = workbook.add_format(
            {"bold": True, "bg_color": "#D9E1F2", "border": 1}
        )
        cell_fmt = workbook.add_format({"border": 1})
        title_fmt = workbook.add_format({"bold": True, "font_size": 12})
        wrap_fmt = workbook.add_format(
            {"text_wrap": True, "valign": "top", "border": 1, "bg_color": "#FFF2CC"}
        )

        # Column headers (row 0 = Excel row 1)
        for col_idx, (_name, label) in enumerate(consumer_type_labels.items()):
            options_ws.write(0, col_idx, str(label), header_fmt)
            options_ws.set_column(col_idx, col_idx, 28)

        # Values start at row 1 (Excel row 2)
        for col_idx, (name, values) in enumerate(validation_options.items()):
            for row_idx, val in enumerate(values, start=1):
                options_ws.write(row_idx, col_idx, str(val), cell_fmt)
            col_letter = chr(ord("A") + col_idx)
            sheet_name = _("Options")
            # The named range must be keyed by the *translated* consumer_type
            # label (spaces stripped, since Excel names can't contain them) -
            # that's the literal text INDIRECT() below resolves against, since
            # it's what actually ends up in the consumer_type cell.
            range_name = consumer_type_labels[name].replace(" ", "_")
            workbook.define_name(
                range_name,
                f"='{sheet_name}'!${col_letter}$2:${col_letter}${len(values) + 1}",
            )

        # Explanation text box (column E)
        warn_fmt = workbook.add_format(
            {
                "bold": True,
                "font_color": "#CC0000",
                "font_size": 11,
                "text_wrap": True,
                "valign": "top",
                "border": 2,
                "border_color": "#CC0000",
            }
        )
        options_ws.write(0, 4, str(_("How to use this file")), title_fmt)
        explanation = str(
            _(
                "This sheet lists the valid options for each consumer type.\n\n"
                "Consumer Type column: select from the dropdown "
                "'household', 'enterprise', or 'public_service'.\n\n"
                "Consumer Detail column: the available options update automatically "
                "when you change the Consumer Type. Use the dropdown to see valid choices."
            )
        )
        options_ws.write(1, 4, explanation, wrap_fmt)
        options_ws.set_row(1, 110)
        warning = str(
            _(
                "WARNING: Do not edit or delete this sheet - the dropdowns in the data sheet depend on it."
            )
        )
        options_ws.write(2, 4, warning, warn_fmt)
        options_ws.set_row(2, 40)
        options_ws.set_column(4, 4, 48)

        # Dynamic consumer_detail updates when consumer_type changes
        type_col_letter = xlsxwriter.utility.xl_col_to_name(consumer_type_col)
        ws.data_validation(
            1,
            consumer_type_col,
            len(df) + 1,
            consumer_type_col,
            {"validate": "list", "source": list(consumer_type_labels.values())},
        )
        ws.data_validation(
            1,
            consumer_detail_col,
            len(df) + 1,
            consumer_detail_col,
            {
                "validate": "list",
                # Strip spaces to match the named ranges defined above, since
                # the cell holds the translated consumer_type label rather than the raw English key
                "source": f'=INDIRECT(SUBSTITUTE({type_col_letter}2," ","_"))',
            },
        )

    output.seek(0)
    return output


def check_imported_demand_data(df, project_dict):
    if df.empty:
        return None, "No data could be read."

    df.columns = [col.strip().lower() for col in df.columns]
    if "demand" not in df.columns:
        return None, "Column with title 'demand' is missing."

    df = df["demand"].dropna()
    try:
        df = df.astype(float)
    except ValueError as e:
        return None, f"Error converting demand to float: {e!s}"

    n_days = min(project_dict["n_days"], int(os.environ.get("MAX_DAYS", 365)))
    ts = pd.Series(
        pd.date_range(
            pd.to_datetime("2022").to_pydatetime(),
            pd.to_datetime("2022").to_pydatetime() + pd.to_timedelta(n_days, unit="D"),
            freq="h",
            inclusive="left",
        )
    )
    if len(df) < len(ts):
        start_date_str = project_dict["start_date"].strftime("%d. %B %H:%M")
        return None, (
            f"You specified a start date of {start_date_str} and a simulation period of {n_days} days with an "
            f"hourly frequency, which requires {len(ts.index)} data points. However, only {len(df.index)} data points were provided."
        )

    df.index = ts.to_numpy()[: len(df.index)]
    return df.to_frame("demand"), ""


def process_optimization_results(proj_id, sim_res):
    grid_processor = GridProcessor(proj_id=proj_id, results_json=sim_res.get("grid"))
    grid_processor.grid_results_to_db()
    supply_processor = SupplyProcessor(
        proj_id=proj_id, results_json=sim_res.get("supply")
    )
    supply_processor.process_supply_optimization_results()
    supply_processor.supply_results_to_db()
    # Process shared results (after both grid and supply have been processed)
    results = Results.objects.get(simulation__project__id=proj_id)
    results.process_shared_results()
    results.save()
