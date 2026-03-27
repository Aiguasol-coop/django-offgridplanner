import logging

import pandas as pd
from django.forms import model_to_dict

from offgridplanner.optimization.requests import fetch_demand_profiles

logging.basicConfig(format="%(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


def prepare_load_profiles_data():
    demand_data_json = {}
    for consumer_type in ["household", "enterprise", "public_service"]:
        try:
            demand_data_json[consumer_type] = fetch_demand_profiles(consumer_type)
        except RuntimeError:
            msg = f"Could not fetch load profiles for {consumer_type}"
            logger.exception(msg)

    dt_index = pd.date_range("2024-01-01", periods=8760, freq="h")
    all_profiles = pd.DataFrame(index=dt_index)

    for consumer_type, data in demand_data_json.items():
        consumer_type_verbose = consumer_type.replace("_", " ").title()
        df = pd.DataFrame(data)
        ts = pd.DataFrame()
        for _ix, row in df.iterrows():
            label = (
                f"{consumer_type_verbose}_{row.area_type}_{row.subcategory}"
                if consumer_type_verbose == "Household"
                else f"{consumer_type_verbose}_{row.subcategory}"
            )
            ts[label] = (
                pd.Series(row.hourly_profile) * row.kwh_per_day * 1000
            )  # change from kWh to Wh
        full_year = pd.concat([ts] * 365)
        full_year.index = dt_index
        all_profiles = pd.concat([all_profiles, full_year], axis=1)
    return all_profiles


LOAD_PROFILES = prepare_load_profiles_data()

PUBLIC_SERVICE_LIST = [
    profile.split("_", maxsplit=1)[1]
    for profile in LOAD_PROFILES.columns
    if profile.split("_", maxsplit=1)[0] == "Public Service"
]
ENTERPRISE_LIST = [
    profile.split("_", maxsplit=1)[1]
    for profile in LOAD_PROFILES.columns
    if (
        profile.split("_", maxsplit=1)[0] == "Enterprise"
        and not profile.split("_", maxsplit=1)[1].startswith("Large Load")
    )
]
LARGE_LOAD_LIST = [
    profile.split("_")[2]
    for profile in LOAD_PROFILES.columns
    if (
        profile.split("_", maxsplit=1)[0] == "Enterprise"
        and profile.split("_", maxsplit=1)[1].startswith("Large Load")
    )
]
LARGE_LOAD_KW_MAPPING = {
    "Milling Machine": 7.5,
    "Crop Dryer": 8,
    "Thresher": 8,
    "Grinder": 5.2,
    "Sawmill": 2.25,
    "Circular Wood Saw": 1.5,
    "Jigsaw": 0.4,
    "Drill": 0.4,
    "Welder": 5.25,
    "Angle Grinder": 2,
}


def get_demand_timeseries(nodes, custom_demand, time_range=None):
    """
    Get the demand timeseries for the project
    Parameters:
        nodes (Nodes): Nodes object for the project
        custom_demand (CustomDemand): CustomDemand object for the project
        time_range (range): List of indices corresponding to timesteps (e.g. range(0,24) for first 24 hours)
    Returns:
        demand_df (pd.DataFrame): DataFrame with aggregated demands by columns "households", "enterprises", "public_services"
    """
    load_profiles = (
        LOAD_PROFILES.iloc[time_range].copy()
        if time_range is not None
        else LOAD_PROFILES.copy()
    )
    # TODO change the index to pd.date_range(nodes.project.start_date, nodes.project.start_date + timedelta(nodes.project.n_days), freq='h'))
    # TODO consider not only n_days but also start_date on time_range
    demand_df = pd.DataFrame(index=load_profiles.index)
    demand_df["household"] = combine_profiles(
        nodes,
        "household",
        load_profiles,
        custom_demand=custom_demand,
    )
    demand_df["enterprise"] = combine_profiles(nodes, "enterprise", load_profiles)
    demand_df["public_service"] = combine_profiles(
        nodes,
        "public_service",
        load_profiles,
    )

    demand_df = calibrate_profiles(demand_df, custom_demand)
    return demand_df


def calibrate_profiles(demand_df, custom_demand):
    """
    Calibrate demand profiles based on custom parameters.

    Parameters:
        demand_df (pd.DataFrame): DataFrame with three columns for household, enterprise and public_services demand
        custom_demand (CustomDemand): CustomDemand instance for the project

    Returns:
        demand_df (pd.DataFrame): Calibrated demand based on peak or total annual demand
    """
    calibration_option = custom_demand.calibration_option
    if calibration_option is None:
        return demand_df

    custom_demand_parameters = model_to_dict(custom_demand)
    calibration_target = custom_demand_parameters[calibration_option]

    if calibration_option == "annual_peak_consumption":
        calibration_factor = calibration_target / demand_df.sum(axis=1).max()
    elif calibration_option == "annual_total_consumption":
        calibration_factor = calibration_target / demand_df.sum().sum()
    else:
        msg = f"Unknown calibration option: {calibration_option}"
        raise ValueError(msg)

    return demand_df * calibration_factor


def combine_profiles(nodes, consumer_type, load_profiles, custom_demand=None):
    # Logic will need fixing if name formatting in load profiles changes
    """
    Parameters:
        nodes (Nodes): Nodes object
        consumer_type (str): One of "household", "enterprise", "public_service"
        load_profiles (pd.DataFrame): Load profiles
        custom_demand (CustomDemand): CustomDemand object (only relevant for households)
    Returns:
        total_demand (pd.Series): Total demand for the given consumer type including machinery
    """
    node_counts = nodes.counts
    try:
        consumer_type_counts = node_counts.loc[consumer_type]

        if consumer_type == "household":
            custom_demand_parameters = model_to_dict(custom_demand)
            total_demand = compute_household_demand(
                consumer_type_counts,
                custom_demand_parameters,
                load_profiles,
            )

        else:
            total_demand = compute_standard_demand(
                consumer_type,
                consumer_type_counts,
                load_profiles,
            )

        # Add machinery loads to enterprises
        if consumer_type == "enterprise":
            # Check if there are any large loads in the custom_specifications
            if nodes.have_custom_machinery:
                ent_nodes = nodes.filter_consumers("enterprise")
                large_load_enterprises = ent_nodes[ent_nodes.custom_specification != ""]
                machinery = unpack_machinery(large_load_enterprises)

                # Compute machinery demand and add to enterprises
                machinery_demand = compute_standard_demand(
                    "machinery",
                    machinery,
                    load_profiles,
                )
                total_demand += machinery_demand

    # consumer_type does not exist
    except KeyError:
        logger.warning(
            "Can't compute demand for %s, since none were selected", consumer_type
        )
        total_demand = pd.Series(0, index=load_profiles.index)

    return total_demand


def compute_household_demand(consumer_type_counts, custom_demand_params, load_profiles):
    """
    Compute demand for households applying wealth shares defined in CustomDemand.

    Parameters:
        consumer_type_counts (pd.DataFrame): Household nodes
        custom_demand_params (dict): Custom demand shares dictionary
        load_profiles (pd.DataFrame): Load profiles
    Returns:
        total_demand (pd.Series): Total household demand
    """
    total_demand = pd.Series(0, index=load_profiles.index)
    total_households = consumer_type_counts.sum()

    for demand_param, value in custom_demand_params.items():
        if demand_param in ["very_low", "low", "middle", "high", "very_high"]:
            profile_col = (
                f"Household_{custom_demand_params['settlement_type']}_{demand_param}"
            )
            total_demand += load_profiles[profile_col] * value

    return total_demand * total_households


def compute_standard_demand(consumer_type, consumer_type_counts, load_profiles):
    """
    Compute demand for enterprises, public services or machinery.

    Parameters:
        consumer_type (str): One of "enterprise", "public_service" or "machinery"
        consumer_type_counts (pd.DataFrame): Household nodes
        load_profiles (pd.DataFrame): Load profiles
    Returns:
        total_demand (pd.Series): Total demand
    """
    if consumer_type == "machinery":
        ts_string_prefix = "Enterprise_Large Load"
    else:
        ts_string_prefix = f"{consumer_type.title().replace('_', ' ')}"
    ts_cols = [f"{ts_string_prefix}_{ts}" for ts in consumer_type_counts.index]
    total_demand = load_profiles[ts_cols].dot(consumer_type_counts.to_numpy())

    return total_demand


def unpack_machinery(large_load_enterprises):
    """
    Unpack the machinery strings saved in custom_specification attribute of enterprise nodes.

    Parameters:
        large_load_enterprises (pd.DataFrame): Filtered nodes DataFrame by enterprises with custom machinery
    Returns:
        large_loads (pd.Series): Series with machinery as index and count column
    """
    # Split large loads string into list, then expand into separate rows
    expanded = (
        large_load_enterprises.assign(
            custom_specification=lambda df: df["custom_specification"].str.split(";"),
        )
        .explode("custom_specification")
        .reset_index(drop=True)
    )
    # Drop power ratings and extract counts from string, create series with machinery as index
    large_loads = (
        expanded["custom_specification"]
        .str.extract(r"(\d+)\s*x\s*([^\(]+?)\s*(?:\(|$)")
        .astype({0: int})
        .groupby(1)[0]
        .sum()  # Sum duplicate machinery types
    )

    return large_loads
