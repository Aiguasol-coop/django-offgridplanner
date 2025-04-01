"""
The provided module is a comprehensive energy system optimization tool based on the `oemof` framework, a library for
modeling and optimizing energy systems. It's designed to handle various components of an energy system and operates in
two primary modes: dispatch and design.

**Overview of the Module:**

- **Energy System Components Modeled:**
  - **Photovoltaic (PV) Systems:** Models solar power generation.
  - **Diesel Generator Sets (Gensets):** Represents diesel-based power generation.
  - **Batteries:** For energy storage, handling charging and discharging processes.
  - **Inverters and Rectifiers:** Convert electrical energy from DC to AC and vice versa.
  - **Electricity and Fuel Buses:** Act as intermediaries for energy flow within the system.
  - **Shortage and Surplus Handling:** Manages deficits and excesses in energy supply.

- **Operational Modes:**
  - **Dispatch Mode:** In this mode, the capacities of various components (like PV, batteries, and gensets) are
    predetermined. The optimization focuses on the best way to utilize these capacities to meet the demand efficiently.
  - **Design Mode:** Here, the capacities of the components are not fixed and are subject to optimization. The system
    determines the ideal sizes of PV installations, battery storage, and other components to meet energy demands
    cost-effectively.

**Key Functionalities and Processes:**

- **Initialization and Data Handling:**
  - It initializes by fetching project-specific data, including user IDs and project IDs, and retrieves the design
    parameters for the energy system components.
  - Solar potential data is acquired based on location coordinates, and peak demand values are calculated.

- **Optimization Process:**
  - Utilizes `oemof.solph` for optimization, considering various energy flows and storage dynamics.
  - The optimizer sets up energy flows between different components, considering constraints and efficiencies.
  - It calculates costs for different components and handles investments for the design mode.

- **Results Processing and Database Interaction:**
  - After optimization, the results are processed to extract key metrics such as Levelized Cost of Energy (LCOE),
    renewable energy share (RES), surplus electricity, and energy shortfall.
  - The results are then stored in the database, including detailed component capacities, emissions data, and
    financial metrics.

- **Notification and Status Update:**
  - Updates the project status in the database and, if configured, sends email notifications upon completion of the
    optimization process.
"""

import logging
import time

import numpy as np
import pandas as pd
import pyomo.environ as po
from oemof import solph

from config.settings.base import SOLVER_NAME
from offgridplanner.optimization.models import DemandCoverage
from offgridplanner.optimization.models import DurationCurve
from offgridplanner.optimization.models import Emissions
from offgridplanner.optimization.models import EnergyFlow
from offgridplanner.optimization.models import Links
from offgridplanner.optimization.supply import solar_potential

logger = logging.getLogger(__name__)

from offgridplanner.optimization.base_optimizer import BaseOptimizer


def optimize_energy_system(proj_id):
    try:
        ensys_opt = EnergySystemOptimizer(proj_id=proj_id)
        ensys_opt.optimize()
        ensys_opt.results_to_db()
        return True
    except Exception as exc:
        logger.error(f"An error occurred during optimization: {exc}")
        raise exc


class EnergySystemOptimizer(BaseOptimizer):
    def __init__(
        self,
        proj_id,
    ):
        print("start es opt")
        super().__init__(proj_id)
        energy_system_design = self.project.energysystemdesign.to_nested_dict()
        if (
            energy_system_design["pv"]["settings"]["is_selected"] is True
            or energy_system_design["battery"]["settings"]["is_selected"] is True
        ):
            energy_system_design["inverter"]["settings"]["is_selected"] = True
        if energy_system_design["diesel_genset"]["settings"]["is_selected"] is False:
            energy_system_design["inverter"]["settings"]["is_selected"] = True
            energy_system_design["battery"]["settings"]["is_selected"] = True
            energy_system_design["pv"]["settings"]["is_selected"] = True
        solver = SOLVER_NAME
        if solver == "cbc":
            energy_system_design["diesel_genset"]["settings"]["offset"] = False
        self.solver = solver
        self.pv = energy_system_design["pv"]
        self.diesel_genset = energy_system_design["diesel_genset"]
        self.battery = energy_system_design["battery"]
        self.inverter = energy_system_design["inverter"]
        self.rectifier = energy_system_design["rectifier"]
        self.shortage = energy_system_design["shortage"]
        if not self.nodes.empty:
            self.num_households = len(
                self.nodes[
                    (self.nodes["consumer_type"] == "household")
                    & (self.nodes["is_connected"] == True)
                ].index,
            )
            links, _ = Links.objects.get_or_create(project=self.project)
            self.links = links.df if links.data is not None else None
            if not self.nodes[self.nodes["consumer_type"] == "power_house"].empty:
                lat, lon = self.nodes[self.nodes["consumer_type"] == "power_house"][
                    "latitude",
                    "longitude",
                ].to_list()
            else:
                lat, lon = self.nodes[["latitude", "longitude"]].mean().to_list()
        else:
            lat, lon = 9.055158, 7.497112
            self.num_households = 1
            self.links = links, _ = Links.objects.get_or_create(project=self.project)
        self.solar_potential = solar_potential.get_dc_feed_in_sync_db_query(
            lat,
            lon,
            self.dt_index,
        ).loc[self.dt_index]
        self.solar_potential_peak = self.solar_potential.max()
        self.demand_peak = self.demand.max()
        self.infeasible = False
        self.energy_system_design = energy_system_design

    def optimize(self):
        # define an empty dictionary for all epc values
        start_execution_time = time.monotonic()
        self.epc = {}
        energy_system = solph.EnergySystem(
            timeindex=self.dt_index.copy(),
            infer_last_interval=True,
        )
        # TODO this should definitely be simplified with tabular or similar
        # -------------------- BUSES --------------------
        # create electricity and fuel buses
        b_el_ac = solph.Bus(label="electricity_ac")
        b_el_dc = solph.Bus(label="electricity_dc")
        b_fuel = solph.Bus(label="fuel")
        # -------------------- PV --------------------
        self.epc["pv"] = (
            self.crf
            * self.capex_multi_investment(
                capex_0=self.pv["parameters"]["capex"],
                component_lifetime=self.pv["parameters"]["lifetime"],
            )
            + self.pv["parameters"]["opex"]
        )
        # Make decision about different simulation modes of the PV
        if self.pv["settings"]["is_selected"] is False:
            pv = solph.components.Source(
                label="pv",
                outputs={b_el_dc: solph.Flow(nominal_value=0)},
            )
        elif self.pv["settings"]["design"] is True:
            # DESIGN
            pv = solph.components.Source(
                label="pv",
                outputs={
                    b_el_dc: solph.Flow(
                        fix=self.solar_potential / self.solar_potential_peak,
                        nominal_value=None,
                        investment=solph.Investment(
                            ep_costs=self.epc["pv"] * self.n_days / 365,
                        ),
                        variable_costs=0,
                    ),
                },
            )
        else:
            # DISPATCH
            pv = solph.components.Source(
                label="pv",
                outputs={
                    b_el_dc: solph.Flow(
                        fix=self.solar_potential / self.solar_potential_peak,
                        nominal_value=self.pv["parameters"]["nominal_capacity"],
                        variable_costs=0,
                    ),
                },
            )

        # -------------------- DIESEL GENSET --------------------
        # fuel density is assumed 0.846 kg/l
        fuel_cost = (
            self.diesel_genset["parameters"]["fuel_cost"]
            / 0.846
            / self.diesel_genset["parameters"]["fuel_lhv"]
        )
        fuel_source = solph.components.Source(
            label="fuel_source",
            outputs={b_fuel: solph.Flow(variable_costs=fuel_cost)},
        )
        # optimize capacity of the fuel generator
        self.epc["diesel_genset"] = (
            self.crf
            * self.capex_multi_investment(
                capex_0=self.diesel_genset["parameters"]["capex"],
                component_lifetime=self.diesel_genset["parameters"]["lifetime"],
            )
            + self.diesel_genset["parameters"]["opex"]
        )
        if self.diesel_genset["settings"]["is_selected"] is False:
            diesel_genset = solph.components.Transformer(
                label="diesel_genset",
                inputs={b_fuel: solph.Flow()},
                outputs={b_el_ac: solph.Flow(nominal_value=0)},
            )
        elif self.diesel_genset["settings"]["design"] is True:
            # DESIGN
            if self.diesel_genset["settings"]["offset"] is True:
                diesel_genset = solph.components.Transformer(
                    label="diesel_genset",
                    inputs={b_fuel: solph.flows.Flow()},
                    outputs={
                        b_el_ac: solph.flows.Flow(
                            nominal_value=None,
                            variable_costs=self.diesel_genset["parameters"][
                                "variable_cost"
                            ],
                            min=self.diesel_genset["parameters"]["min_load"],
                            max=1,
                            nonconvex=solph.NonConvex(),
                            investment=solph.Investment(
                                ep_costs=self.epc["diesel_genset"] * self.n_days / 365,
                            ),
                        ),
                    },
                    conversion_factors={
                        b_el_ac: self.diesel_genset["parameters"]["max_efficiency"],
                    },
                )
            else:
                diesel_genset = solph.components.Transformer(
                    label="diesel_genset",
                    inputs={b_fuel: solph.Flow()},
                    outputs={
                        b_el_ac: solph.Flow(
                            nominal_value=None,
                            variable_costs=self.diesel_genset["parameters"][
                                "variable_cost"
                            ],
                            investment=solph.Investment(
                                ep_costs=self.epc["diesel_genset"] * self.n_days / 365,
                            ),
                        ),
                    },
                    conversion_factors={
                        b_el_ac: self.diesel_genset["parameters"]["max_efficiency"],
                    },
                )
        else:
            # DISPATCH
            diesel_genset = solph.components.Transformer(
                label="diesel_genset",
                inputs={b_fuel: solph.Flow()},
                outputs={
                    b_el_ac: solph.Flow(
                        nominal_value=self.diesel_genset["parameters"][
                            "nominal_capacity"
                        ],
                        variable_costs=self.diesel_genset["parameters"][
                            "variable_cost"
                        ],
                    ),
                },
                conversion_factors={
                    b_el_ac: self.diesel_genset["parameters"]["max_efficiency"],
                },
            )

        # -------------------- RECTIFIER --------------------
        self.epc["rectifier"] = (
            self.crf
            * self.capex_multi_investment(
                capex_0=self.rectifier["parameters"]["capex"],
                component_lifetime=self.rectifier["parameters"]["lifetime"],
            )
            + self.rectifier["parameters"]["opex"]
        )

        if self.rectifier["settings"]["is_selected"] is False:
            rectifier = solph.components.Transformer(
                label="rectifier",
                inputs={b_el_ac: solph.Flow(nominal_value=0)},
                outputs={b_el_dc: solph.Flow()},
            )
        elif self.rectifier["settings"]["design"] is True:
            # DESIGN
            rectifier = solph.components.Transformer(
                label="rectifier",
                inputs={
                    b_el_ac: solph.Flow(
                        nominal_value=None,
                        investment=solph.Investment(
                            ep_costs=self.epc["rectifier"] * self.n_days / 365,
                        ),
                        variable_costs=0,
                    ),
                },
                outputs={b_el_dc: solph.Flow()},
                conversion_factors={
                    b_el_dc: self.rectifier["parameters"]["efficiency"],
                },
            )
        else:
            # DISPATCH
            rectifier = solph.components.Transformer(
                label="rectifier",
                inputs={
                    b_el_ac: solph.Flow(
                        nominal_value=self.rectifier["parameters"]["nominal_capacity"],
                        variable_costs=0,
                    ),
                },
                outputs={b_el_dc: solph.Flow()},
                conversion_factors={
                    b_el_dc: self.rectifier["parameters"]["efficiency"],
                },
            )

        # -------------------- INVERTER --------------------
        self.epc["inverter"] = (
            self.crf
            * self.capex_multi_investment(
                capex_0=self.inverter["parameters"]["capex"],
                component_lifetime=self.inverter["parameters"]["lifetime"],
            )
            + self.inverter["parameters"]["opex"]
        )
        if self.inverter["settings"]["is_selected"] is False:
            inverter = solph.components.Transformer(
                label="inverter",
                inputs={b_el_dc: solph.Flow(nominal_value=0)},
                outputs={b_el_ac: solph.Flow()},
            )
        elif self.inverter["settings"]["design"] is True:
            # DESIGN
            inverter = solph.components.Transformer(
                label="inverter",
                inputs={
                    b_el_dc: solph.Flow(
                        nominal_value=None,
                        investment=solph.Investment(
                            ep_costs=self.epc["inverter"] * self.n_days / 365,
                        ),
                        variable_costs=0,
                    ),
                },
                outputs={b_el_ac: solph.Flow()},
                conversion_factors={
                    b_el_ac: self.inverter["parameters"]["efficiency"],
                },
            )
        else:
            # DISPATCH
            inverter = solph.components.Transformer(
                label="inverter",
                inputs={
                    b_el_dc: solph.Flow(
                        nominal_value=self.inverter["parameters"]["nominal_capacity"],
                        variable_costs=0,
                    ),
                },
                outputs={b_el_ac: solph.Flow()},
                conversion_factors={
                    b_el_ac: self.inverter["parameters"]["efficiency"],
                },
            )

        # -------------------- BATTERY --------------------
        self.epc["battery"] = (
            self.crf
            * self.capex_multi_investment(
                capex_0=self.battery["parameters"]["capex"],
                component_lifetime=self.battery["parameters"]["lifetime"],
            )
            + self.battery["parameters"]["opex"]
        )

        if self.battery["settings"]["is_selected"] is False:
            battery = solph.components.GenericStorage(
                label="battery",
                nominal_storage_capacity=0,
                inputs={b_el_dc: solph.Flow()},
                outputs={b_el_dc: solph.Flow()},
            )
        elif self.battery["settings"]["design"] is True:
            # DESIGN
            battery = solph.components.GenericStorage(
                label="battery",
                nominal_storage_capacity=None,
                investment=solph.Investment(
                    ep_costs=self.epc["battery"] * self.n_days / 365,
                ),
                inputs={b_el_dc: solph.Flow(variable_costs=0)},
                outputs={b_el_dc: solph.Flow(investment=solph.Investment(ep_costs=0))},
                initial_storage_level=self.battery["parameters"]["soc_max"],
                min_storage_level=self.battery["parameters"]["soc_min"],
                max_storage_level=self.battery["parameters"]["soc_max"],
                balanced=False,
                inflow_conversion_factor=self.battery["parameters"]["efficiency"],
                outflow_conversion_factor=self.battery["parameters"]["efficiency"],
                invest_relation_input_capacity=self.battery["parameters"]["c_rate_in"],
                invest_relation_output_capacity=self.battery["parameters"][
                    "c_rate_out"
                ],
            )
        else:
            # DISPATCH
            battery = solph.components.GenericStorage(
                label="battery",
                nominal_storage_capacity=self.battery["parameters"]["nominal_capacity"],
                inputs={b_el_dc: solph.Flow(variable_costs=0)},
                outputs={b_el_dc: solph.Flow()},
                initial_storage_level=self.battery["parameters"]["soc_max"],
                min_storage_level=self.battery["parameters"]["soc_min"],
                max_storage_level=self.battery["parameters"]["soc_max"],
                balanced=True,
                inflow_conversion_factor=self.battery["parameters"]["efficiency"],
                outflow_conversion_factor=self.battery["parameters"]["efficiency"],
                invest_relation_input_capacity=self.battery["parameters"]["c_rate_in"],
                invest_relation_output_capacity=self.battery["parameters"][
                    "c_rate_out"
                ],
            )

        # -------------------- DEMAND --------------------
        demand_el = solph.components.Sink(
            label="electricity_demand",
            inputs={
                b_el_ac: solph.Flow(
                    # min=1-max_shortage_timestep,
                    fix=self.demand / self.demand_peak,
                    nominal_value=self.demand_peak,
                ),
            },
        )

        # -------------------- SURPLUS --------------------
        surplus = solph.components.Sink(
            label="surplus",
            inputs={b_el_ac: solph.Flow()},
        )

        # -------------------- SHORTAGE --------------------
        # maximal unserved demand and the variable costs of unserved demand.
        if self.shortage["settings"]["is_selected"]:
            shortage = solph.components.Source(
                label="shortage",
                outputs={
                    b_el_ac: solph.Flow(
                        variable_costs=self.shortage["parameters"][
                            "shortage_penalty_cost"
                        ],
                        nominal_value=self.shortage["parameters"]["max_shortage_total"]
                        * sum(self.demand),
                        full_load_time_max=1,
                    ),
                },
            )
        else:
            shortage = solph.components.Source(
                label="shortage",
                outputs={
                    b_el_ac: solph.Flow(
                        nominal_value=0,
                    ),
                },
            )

        # add all objects to the energy system
        energy_system.add(
            pv,
            fuel_source,
            b_el_dc,
            b_el_ac,
            b_fuel,
            inverter,
            rectifier,
            diesel_genset,
            battery,
            demand_el,
            surplus,
            shortage,
        )
        model = solph.Model(energy_system)
        self.execution_time = time.monotonic() - start_execution_time

        def shortage_per_timestep_rule(model, t):
            expr = 0
            ## ------- Get demand at t ------- #
            demand = model.flow[b_el_ac, demand_el, t]
            expr += self.shortage["parameters"]["max_shortage_timestep"] * demand
            ## ------- Get shortage at t------- #
            expr += -model.flow[shortage, b_el_ac, t]

            return expr >= 0

        if self.shortage["settings"]["is_selected"]:
            model.shortage_timestep = po.Constraint(
                model.TIMESTEPS,
                rule=shortage_per_timestep_rule,
            )

        # def max_surplus_electricity_total_rule(model):
        #     max_surplus_electricity = 0.05  # fraction
        #     expr = 0
        #     ## ------- Get generated at t ------- #
        #     generated_diesel_genset = sum(model.flow[diesel_genset, b_el_ac, :])
        #     generated_pv = sum(model.flow[inverter, b_el_ac, :])
        #     ac_to_dc = sum(model.flow[b_el_ac, rectifier, :])
        #     generated = generated_diesel_genset + generated_pv - ac_to_dc
        #     expr += (generated * max_surplus_electricity)
        #     ## ------- Get surplus at t------- #
        #     surplus_total = sum(model.flow[b_el_ac, surplus, :])
        #     expr += -surplus_total

        #     return expr >= 0

        # model.max_surplus_electricity_total = po.Constraint(
        #     rule=max_surplus_electricity_total_rule
        # )

        # optimize the energy system
        # gurobi --> 'MipGap': '0.01'
        # cbc --> 'ratioGap': '0.01'
        solver_option = {"gurobi": {"MipGap": "0.03"}, "cbc": {"ratioGap": "0.03"}}

        res = model.solve(
            solver=self.solver,
            solve_kwargs={"tee": True},
            cmdline_options=solver_option[self.solver],
        )
        self.model = model
        if model.solutions.__len__() > 0:
            energy_system.results["meta"] = solph.processing.meta_results(model)
            self.results_main = solph.processing.results(model)

            self._process_results()
        else:
            print("No solution found")
        if list(res["Solver"])[0]["Termination condition"] == "infeasible":
            self.infeasible = True

    def _process_results(self):
        results_pv = solph.views.node(results=self.results_main, node="pv")
        results_fuel_source = solph.views.node(
            results=self.results_main,
            node="fuel_source",
        )
        results_diesel_genset = solph.views.node(
            results=self.results_main,
            node="diesel_genset",
        )
        results_inverter = solph.views.node(results=self.results_main, node="inverter")
        results_rectifier = solph.views.node(
            results=self.results_main,
            node="rectifier",
        )
        results_battery = solph.views.node(results=self.results_main, node="battery")
        results_demand_el = solph.views.node(
            results=self.results_main,
            node="electricity_demand",
        )
        results_surplus = solph.views.node(results=self.results_main, node="surplus")
        results_shortage = solph.views.node(results=self.results_main, node="shortage")

        # -------------------- SEQUENCES (DYNAMIC) --------------------
        # hourly demand profile
        self.sequences_demand = results_demand_el["sequences"][
            (("electricity_ac", "electricity_demand"), "flow")
        ]

        # hourly profiles for solar potential and pv production
        self.sequences_pv = results_pv["sequences"][(("pv", "electricity_dc"), "flow")]

        # hourly profiles for fuel consumption and electricity production in the fuel genset
        # the 'flow' from oemof is in kWh and must be converted to liter
        self.sequences_fuel_consumption = (
            results_fuel_source["sequences"][(("fuel_source", "fuel"), "flow")]
            / self.diesel_genset["parameters"]["fuel_lhv"]
            / 0.846
        )  # conversion: kWh -> kg -> l

        self.sequences_fuel_consumption_kWh = results_fuel_source["sequences"][
            (("fuel_source", "fuel"), "flow")
        ]  # conversion: kWh

        self.sequences_genset = results_diesel_genset["sequences"][
            (("diesel_genset", "electricity_ac"), "flow")
        ]

        # hourly profiles for charge, discharge, and content of the battery
        self.sequences_battery_charge = results_battery["sequences"][
            (("electricity_dc", "battery"), "flow")
        ]

        self.sequences_battery_discharge = results_battery["sequences"][
            (("battery", "electricity_dc"), "flow")
        ]

        self.sequences_battery_content = results_battery["sequences"][
            (("battery", "None"), "storage_content")
        ]

        # hourly profiles for inverted electricity from dc to ac
        self.sequences_inverter = results_inverter["sequences"][
            (("inverter", "electricity_ac"), "flow")
        ]

        # hourly profiles for inverted electricity from ac to dc
        self.sequences_rectifier = results_rectifier["sequences"][
            (("rectifier", "electricity_dc"), "flow")
        ]

        # hourly profiles for surplus ac and dc electricity production
        self.sequences_surplus = results_surplus["sequences"][
            (("electricity_ac", "surplus"), "flow")
        ]

        # hourly profiles for shortages in the demand coverage
        self.sequences_shortage = results_shortage["sequences"][
            (("shortage", "electricity_ac"), "flow")
        ]

        # -------------------- SCALARS (STATIC) --------------------
        if self.diesel_genset["settings"]["is_selected"] is False:
            self.capacity_genset = 0
        elif self.diesel_genset["settings"]["design"] is True:
            self.capacity_genset = results_diesel_genset["scalars"][
                (("diesel_genset", "electricity_ac"), "invest")
            ]
        else:
            self.capacity_genset = self.diesel_genset["parameters"]["nominal_capacity"]

        if self.pv["settings"]["is_selected"] is False:
            self.capacity_pv = 0
        elif self.pv["settings"]["design"] is True:
            self.capacity_pv = results_pv["scalars"][
                (("pv", "electricity_dc"), "invest")
            ]
        else:
            self.capacity_pv = self.pv["parameters"]["nominal_capacity"]

        if self.inverter["settings"]["is_selected"] is False:
            self.capacity_inverter = 0
        elif self.inverter["settings"]["design"] is True:
            self.capacity_inverter = results_inverter["scalars"][
                (("electricity_dc", "inverter"), "invest")
            ]
        else:
            self.capacity_inverter = self.inverter["parameters"]["nominal_capacity"]

        if self.rectifier["settings"]["is_selected"] is False:
            self.capacity_rectifier = 0
        elif self.rectifier["settings"]["design"] is True:
            self.capacity_rectifier = results_rectifier["scalars"][
                (("electricity_ac", "rectifier"), "invest")
            ]
        else:
            self.capacity_rectifier = self.rectifier["parameters"]["nominal_capacity"]

        if self.battery["settings"]["is_selected"] is False:
            self.capacity_battery = 0
        elif self.battery["settings"]["design"] is True:
            self.capacity_battery = results_battery["scalars"][
                (("electricity_dc", "battery"), "invest")
            ]
        else:
            self.capacity_battery = self.battery["parameters"]["nominal_capacity"]

        self.total_renewable = (
            (
                self.epc["pv"] * self.capacity_pv
                + self.epc["inverter"] * self.capacity_inverter
                + self.epc["battery"] * self.capacity_battery
            )
            * self.n_days
            / 365
        )
        self.total_non_renewable = (
            self.epc["diesel_genset"] * self.capacity_genset
            + self.epc["rectifier"] * self.capacity_rectifier
        ) * self.n_days / 365 + self.diesel_genset["parameters"][
            "variable_cost"
        ] * self.sequences_genset.sum(
            axis=0,
        )
        self.total_component = self.total_renewable + self.total_non_renewable
        self.total_fuel = self.diesel_genset["parameters"][
            "fuel_cost"
        ] * self.sequences_fuel_consumption.sum(axis=0)
        self.total_revenue = self.total_component + self.total_fuel
        self.total_demand = self.sequences_demand.sum(axis=0)
        self.lcoe = 100 * self.total_revenue / self.total_demand

        self.res = (
            100
            * self.sequences_pv.sum(axis=0)
            / (self.sequences_genset.sum(axis=0) + self.sequences_pv.sum(axis=0))
        )

        self.surplus_rate = (
            100
            * self.sequences_surplus.sum(axis=0)
            / (
                self.sequences_genset.sum(axis=0)
                - self.sequences_rectifier.sum(axis=0)
                + self.sequences_inverter.sum(axis=0)
            )
        )
        self.genset_to_dc = (
            100
            * self.sequences_rectifier.sum(axis=0)
            / self.sequences_genset.sum(axis=0)
        )
        self.shortage = (
            100
            * self.sequences_shortage.sum(axis=0)
            / self.sequences_demand.sum(axis=0)
        )

        print("")
        print(40 * "*")
        print(f"LCOE:\t\t {self.lcoe:.2f} cent/kWh")
        print(f"RES:\t\t {self.res:.0f}%")
        print(f"Surplus:\t {self.surplus_rate:.1f}% of the total production")
        print(f"Shortage:\t {self.shortage:.1f}% of the total demand")
        print(f"AC--DC:\t\t {self.genset_to_dc:.1f}% of the genset production")
        print(40 * "*")
        print(f"genset:\t\t {self.capacity_genset:.0f} kW")
        print(f"pv:\t\t {self.capacity_pv:.0f} kW")
        print(f"st:\t\t {self.capacity_battery:.0f} kW")
        print(f"inv:\t\t {self.capacity_inverter:.0f} kW")
        print(f"rect:\t\t {self.capacity_rectifier:.0f} kW")
        print(f"peak:\t\t {self.sequences_demand.max():.0f} kW")
        print(f"surplus:\t {self.sequences_surplus.max():.0f} kW")
        print(40 * "*")

    def results_to_db(self):
        if len(self.model.solutions) == 0:
            if self.infeasible is True:
                results = self.results
                results.infeasible = self.infeasible
                results.save()
            return False
        self._emissions_to_db()
        self._results_to_db()
        self._energy_flow_to_db()
        self._demand_curve_to_db()
        self._demand_coverage_to_db()
        self._update_project_status_in_db()
        return True

    def _update_project_status_in_db(self):
        # TODO fixup later
        project_setup = self.project
        project_setup.status = "finished"
        # if project_setup.email_notification is True:
        #     user = sync_queries.get_user_by_id(self.user_id)
        #     subject = "PeopleSun: Model Calculation finished"
        #     msg = (
        #         "The calculation of your optimization model is finished. You can view the results at: "
        #         f"\n\n{config.DOMAIN}/simulation_results?project_id={self.project_id}\n"
        #     )
        #     send_mail(user.email, msg, subject=subject)
        project_setup.email_notification = False
        project_setup.save()

    def _demand_coverage_to_db(self):
        df = pd.DataFrame()
        df["demand"] = self.sequences_demand
        df["renewable"] = self.sequences_inverter
        df["non_renewable"] = self.sequences_genset
        df["surplus"] = self.sequences_surplus
        df.index.name = "dt"
        df = df.reset_index()
        df = df.round(3)
        demand_coverage, _ = DemandCoverage.objects.get_or_create(project=self.project)
        demand_coverage.data = df.reset_index(drop=True).to_json()
        demand_coverage.save()

    def _emissions_to_db(self):
        if self.capacity_genset < 60:
            co2_emission_factor = 1.580
        elif self.capacity_genset < 300:
            co2_emission_factor = 0.883
        else:
            co2_emission_factor = 0.699
        # store fuel co2 emissions (kg_CO2 per L of fuel)
        df = pd.DataFrame()
        df["non_renewable_electricity_production"] = (
            np.cumsum(self.demand) * co2_emission_factor / 1000
        )  # tCO2 per year
        df["hybrid_electricity_production"] = (
            np.cumsum(self.sequences_genset) * co2_emission_factor / 1000
        )  # tCO2 per year
        df.index = pd.date_range("2022-01-01", periods=df.shape[0], freq="h")
        df = df.resample("D").max().reset_index(drop=True)
        emissions, _ = Emissions.objects.get_or_create(project=self.project)
        emissions.data = df.reset_index(drop=True).to_json()
        emissions.save()
        self.co2_savings = (
            df["non_renewable_electricity_production"]
            - df["hybrid_electricity_production"]
        ).max()
        self.co2_emission_factor = co2_emission_factor

    def _energy_flow_to_db(self):
        energy_flow_df = pd.DataFrame(
            {
                "diesel_genset_production": self.sequences_genset,
                "pv_production": self.sequences_pv,
                "battery_charge": self.sequences_battery_charge,
                "battery_discharge": self.sequences_battery_discharge,
                "battery_content": self.sequences_battery_content,
                "demand": self.sequences_demand,
                "surplus": self.sequences_surplus,
            },
        ).round(3)
        energy_flow, _ = EnergyFlow.objects.get_or_create(project=self.project)
        energy_flow.data = energy_flow_df.reset_index(drop=True).to_json()
        energy_flow.save()

    def _demand_curve_to_db(self):
        df = pd.DataFrame()
        df["diesel_genset_duration"] = (
            100 * np.sort(self.sequences_genset)[::-1] / self.sequences_genset.max()
        )
        if self.sequences_pv.max() > 0:
            div = self.sequences_pv.max()
        else:
            div = 1
        df["pv_duration"] = 100 * np.sort(self.sequences_pv)[::-1] / div
        if not self.sequences_rectifier.abs().sum() == 0:
            df["rectifier_duration"] = 100 * np.nan_to_num(
                np.sort(self.sequences_rectifier)[::-1]
                / self.sequences_rectifier.max(),
            )
        else:
            df["rectifier_duration"] = 0
        if self.sequences_inverter.max() > 0:
            div = self.sequences_inverter.max()
        else:
            div = 1
        df["inverter_duration"] = 100 * np.sort(self.sequences_inverter)[::-1] / div
        if not self.sequences_battery_charge.max() > 0:
            div = 1
        else:
            div = self.sequences_battery_charge.max()
        df["battery_charge_duration"] = (
            100 * np.sort(self.sequences_battery_charge)[::-1] / div
        )
        if self.sequences_battery_discharge.max() > 0:
            div = self.sequences_battery_discharge.max()
        else:
            div = 1
        df["battery_discharge_duration"] = (
            100 * np.sort(self.sequences_battery_discharge)[::-1] / div
        )
        df = df.copy()
        df.index = pd.date_range("2022-01-01", periods=df.shape[0], freq="h")
        df = df.resample("D").min().reset_index(drop=True)
        df["pv_percentage"] = df.index.copy() / df.shape[0]
        df = df.round(3)
        duration_curve, _ = DurationCurve.objects.get_or_create(project=self.project)
        duration_curve.data = df.reset_index(drop=True).to_json()
        duration_curve.save()

    def _results_to_db(self):
        results = self.results
        if pd.isna(results.cost_grid) is True:
            results.n_consumers = 0
            results.n_shs_consumers = 0
            results.n_poles = 0
            results.length_distribution_cable = 0
            results.length_connection_cable = 0
            results.cost_grid = 0
            results.cost_shs = 0
            results.time_grid_design = 0
            results.n_distribution_links = 0
            results.n_connection_links = 0
            results.upfront_invest_grid = 0
            results.time_grid_design = 0
        results.cost_renewable_assets = self.total_renewable / self.n_days * 365
        results.cost_non_renewable_assets = self.total_non_renewable / self.n_days * 365
        results.cost_fuel = self.total_fuel / self.n_days * 365
        results.cost_grid = (
            results.cost_grid / self.n_days * 365
            if results.cost_grid is not None
            else 0
        )
        results.epc_total = (self.total_revenue + results.cost_grid) / self.n_days * 365
        results.lcoe = (
            100 * (self.total_revenue + results.cost_grid) / self.total_demand
        )
        results.res = self.res
        results.shortage_total = self.shortage
        results.surplus_rate = self.surplus_rate
        results.pv_capacity = self.capacity_pv
        results.battery_capacity = self.capacity_battery
        results.inverter_capacity = self.capacity_inverter
        results.rectifier_capacity = self.capacity_rectifier
        results.diesel_genset_capacity = self.capacity_genset
        results.peak_demand = self.demand.max()
        results.surplus = self.sequences_surplus.max()
        results.infeasible = self.infeasible
        # data for sankey diagram - all in MWh
        results.fuel_to_diesel_genset = (
            self.sequences_fuel_consumption.sum()
            * 0.846
            * self.diesel_genset["parameters"]["fuel_lhv"]
            / 1000
        )
        results.diesel_genset_to_rectifier = (
            self.sequences_rectifier.sum()
            / self.rectifier["parameters"]["efficiency"]
            / 1000
        )
        results.diesel_genset_to_demand = (
            self.sequences_genset.sum() / 1000 - results.diesel_genset_to_rectifier
        )
        results.rectifier_to_dc_bus = self.sequences_rectifier.sum() / 1000
        results.pv_to_dc_bus = self.sequences_pv.sum() / 1000
        results.battery_to_dc_bus = self.sequences_battery_discharge.sum() / 1000
        results.dc_bus_to_battery = self.sequences_battery_charge.sum() / 1000
        if self.inverter["parameters"]["efficiency"] > 0:
            div = self.inverter["parameters"]["efficiency"]
        else:
            div = 1
        results.dc_bus_to_inverter = self.sequences_inverter.sum() / div / 1000
        results.dc_bus_to_surplus = self.sequences_surplus.sum() / 1000
        results.inverter_to_demand = self.sequences_inverter.sum() / 1000
        results.time_energy_system_design = self.execution_time
        results.co2_savings = self.co2_savings / self.n_days * 365
        # TODO this only works with uploaded data if n_days=365
        results.total_annual_consumption = self.demand_full_year.sum() * (
            (100 - self.shortage) / 100
        )
        results.average_annual_demand_per_consumer = (
            self.demand_full_year.mean()
            * (100 - self.shortage)
            / 100
            / self.num_households
            * 1000
        )
        results.base_load = self.demand_full_year.quantile(0.1)
        results.max_shortage = (self.sequences_shortage / self.demand).max() * 100

        results.upfront_invest_diesel_gen = (
            results.diesel_genset_capacity
            * self.energy_system_design["diesel_genset"]["parameters"]["capex"]
        )
        results.upfront_invest_pv = (
            results.pv_capacity * self.energy_system_design["pv"]["parameters"]["capex"]
        )
        results.upfront_invest_inverter = (
            results.inverter_capacity
            * self.energy_system_design["inverter"]["parameters"]["capex"]
        )
        results.upfront_invest_rectifier = (
            results.rectifier_capacity
            * self.energy_system_design["rectifier"]["parameters"]["capex"]
        )
        results.upfront_invest_battery = (
            results.battery_capacity
            * self.energy_system_design["battery"]["parameters"]["capex"]
        )
        results.co2_emissions = (
            self.sequences_genset.sum()
            * self.co2_emission_factor
            / 1000
            / self.n_days
            * 365
        )
        results.fuel_consumption = (
            self.sequences_fuel_consumption.sum() / self.n_days * 365
        )
        results.epc_pv = self.epc["pv"] * self.capacity_pv
        results.epc_diesel_genset = (
            self.epc["diesel_genset"] * self.capacity_genset
        ) + self.diesel_genset["parameters"][
            "variable_cost"
        ] * self.sequences_genset.sum(
            axis=0,
        ) * 365 / self.n_days
        results.epc_inverter = self.epc["inverter"] * self.capacity_inverter
        results.epc_rectifier = self.epc["rectifier"] * self.capacity_rectifier
        results.epc_battery = self.epc["battery"] * self.capacity_battery
        results.save()
