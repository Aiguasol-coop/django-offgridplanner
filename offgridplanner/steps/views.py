import json
import os

import numpy as np
import pandas as pd

from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.utils.translation import gettext_lazy as _

from offgridplanner.opt_models.grid_optimizer import optimize_grid
from offgridplanner.opt_models.supply_optimizer import optimize_energy_system
from offgridplanner.projects.forms import ProjectForm, CustomDemandForm, OptionForm, GridDesignForm
from offgridplanner.projects.models import Project, Options, CustomDemand, Nodes, GridDesign, Energysystemdesign, \
    Simulation
from offgridplanner.projects.tasks import task_is_finished
from offgridplanner.users.models import User
from offgridplanner.projects.demand_estimation import get_demand_timeseries, LOAD_PROFILES

STEPS = [
    _("project_setup"),
    _("consumer_selection"),
    _("demand_estimation"),
    _("grid_design"),
    _("energy_system_design"),
    _("calculating"),
    _("simulation_results"),
]


@require_http_methods(["GET"])
def home(request):

    return render(
        request,
        "pages/landing_page.html",
        {"step_list": STEPS},
    )


# @login_required()
@require_http_methods(["GET", "POST"])
def project_setup(request, proj_id=None):
    if proj_id is not None:
        project = get_object_or_404(Project, id=proj_id)
        if project.user != request.user:
            raise PermissionDenied
    else:
        project = None
    if request.method == "GET":
        max_days = int(os.environ.get("MAX_DAYS", 365))

        context = {}
        if project is not None:
            form = ProjectForm(instance=project)
            opts = OptionForm(instance=project.options)
            context.update({"proj_id": project.id})
        else:
            form = ProjectForm()
            opts = OptionForm()
        context.update({"form": form, "opts_form": opts, "max_days": max_days, "step_id": STEPS.index("project_setup")+1, "step_list": STEPS})

    # TODO in the js figure out what this is supposed to mean, this make the next button jump to either step 'consumer_selection'
    # or step 'demand_estimation'
    # const consumerSelectionHref = `consumer_selection?project_id=${project_id}`;
    # const demandEstimationHref = `demand_estimation?project_id =${project_id}`;
    # If Consumer Selection is hidden (in raw html), go to demand_estimation

        return render(request, "pages/project_setup.html", context)
    elif request.method == "POST":
        if project is None:
            form = ProjectForm(request.POST)
            opts_form  = OptionForm(request.POST)
        else:
            form = ProjectForm(request.POST, instance=project)
            opts_form = OptionForm(request.POST, instance=project.options)
        if form.is_valid() and opts_form.is_valid():
            opts = opts_form.save()
            if project is None:
                project = form.save(commit=False)
                project.user = User.objects.get(email=request.user.email)
                project.options = opts
            project.save()

        return HttpResponseRedirect(reverse("steps:consumer_selection",args=[project.id]))

# @login_required()
@require_http_methods(["GET"])
def consumer_selection(request, proj_id=None):
    # TODO replace these with lists from LOAD_PROFILES.columns
    public_service_list = {
        "group1": "Health_Health Centre",
        "group2": "Health_Clinic",
        "group3": "Health_CHPS",
        "group4": "Education_School",
        "group5": "Education_School_noICT",
    }

    enterprise_list = {
        "group1": "Food_Groceries",
        "group2": "Food_Restaurant",
        "group3": "Food_Bar",
        "group4": "Food_Drinks",
        "group5": "Food_Fruits or vegetables",
        "group6": "Trades_Tailoring",
        "group7": "Trades_Beauty or Hair",
        "group8": "Trades_Metalworks",
        "group9": "Trades_Car or Motorbike Repair",
        "group10": "Trades_Carpentry",
        "group11": "Trades_Laundry",
        "group12": "Trades_Cycle Repair",
        "group13": "Trades_Shoemaking",
        "group14": "Retail_Medical",
        "group15": "Retail_Clothes and accessories",
        "group16": "Retail_Electronics",
        "group17": "Retail_Other",
        "group18": "Retail_Agricultural",
        "group19": "Digital_Mobile or Electronics Repair",
        "group20": "Digital_Digital Other",
        "group21": "Digital_Cybercafé",
        "group22": "Digital_Cinema or Betting",
        "group23": "Digital_Photostudio",
        "group24": "Agricultural_Mill or Thresher or Grater",
        "group25": "Agricultural_Other",
    }

    enterpise_option = ""

    large_load_list = {
        "group1": "Milling Machine (7.5kW)",
        "group2": "Crop Dryer (8kW)",
        "group3": "Thresher (8kW)",
        "group4": "Grinder (5.2kW)",
        "group5": "Sawmill (2.25kW)",
        "group6": "Circular Wood Saw (1.5kW)",
        "group7": "Jigsaw (0.4kW)",
        "group8": "Drill (0.4kW)",
        "group9": "Welder (5.25kW)",
        "group10": "Angle Grinder (2kW)",
    }
    large_load_type = "group1"

    option_load = ""

    context = {
        "public_service_list": public_service_list,
        "enterprise_list": enterprise_list,
        "large_load_list": large_load_list,
        "large_load_type": large_load_type,
        "enterpise_option": enterpise_option,
        "option_load": option_load,
        "step_id": STEPS.index("consumer_selection") + 1,
        "step_list": STEPS
    }
    if proj_id is not None:
        project = get_object_or_404(Project, id=proj_id)
        if project.user.email != request.user.email:
            raise PermissionDenied
        context["proj_id"] = project.id

    # _wizard.js contains info for the POST function set when clicking on next or on another step

    return render(request, "pages/consumer_selection.html", context)


# @login_required()
@require_http_methods(["GET", "POST"])
def demand_estimation(request, proj_id=None):
    # TODO demand import and export from this step still needs to be handled
    step_id = STEPS.index("demand_estimation") + 1
    if proj_id is not None:
        project = get_object_or_404(Project, id=proj_id)
        if project.user != request.user:
            raise PermissionDenied

        custom_demand, _ = CustomDemand.objects.get_or_create(project=project)
        if request.method == "GET":
            form = CustomDemandForm(instance=custom_demand)
            context = {"form": form, "proj_id": proj_id, "step_id": step_id, "step_list": STEPS}

            return render(request, "pages/demand_estimation.html", context)

        elif request.method == "POST":
            form = CustomDemandForm(request.POST, instance=custom_demand)
            if form.is_valid():
                form.save()

            return redirect("steps:ogp_steps", proj_id, step_id + 1)


# @login_required()
@require_http_methods(["GET", "POST"])
def grid_design(request, proj_id=None):
    step_id = STEPS.index("grid_design") + 1
    if proj_id is not None:
        project = get_object_or_404(Project, id=proj_id)
        if project.user != request.user:
            raise PermissionDenied

        grid_design, _ = GridDesign.objects.get_or_create(project=project)
        if request.method == "GET":
            form = GridDesignForm(instance=grid_design)

            context = {"form": form, "proj_id": proj_id, "step_id": step_id, "step_list": STEPS}
            return render(request, "pages/grid_design.html", context)

        elif request.method == "POST":
            form = GridDesignForm(request.POST, instance=grid_design)
            if form.is_valid():
                form.save()

            return redirect("steps:ogp_steps", proj_id, step_id + 1)



# @login_required()
@require_http_methods(["GET", "POST"])
def energy_system_design(request,proj_id=None):
    step_id = STEPS.index("energy_system_design") + 1
    if proj_id is not None:
        project = get_object_or_404(Project, id=proj_id)
        if project.user.email != request.user.email:
            raise PermissionDenied
    if request.method == "GET":
        context = {"proj_id": project.id,"step_id": step_id,"step_list": STEPS}

        # TODO read js/pages/energy-system-design.js
        #todo restore using load_previous_data in the first place, then replace with Django forms


        return render(request, "pages/energy_system_design.html", context)
    elif request.method == "POST":
        data = json.loads(request.body)
        df = pd.json_normalize(data, sep='_')
        d_flat = df.to_dict(orient='records')[0]
        Energysystemdesign.objects.filter(project=project).delete()
        es= Energysystemdesign(**d_flat)
        es.project = project
        es.save()
        return JsonResponse({"href": reverse(f"steps:{STEPS[step_id]}", args=[proj_id])},status=200)


def calculating(request, proj_id=None):
    if proj_id is not None:
        project = get_object_or_404(Project, id=proj_id)
        if project.user.email != request.user.email:
            raise PermissionDenied

        simulation, _ = Simulation.objects.get_or_create(project=project)
        if 'anonymous' in project.user.email:
            msg = 'You will be forwarded after the model calculation is completed.'
            email_opt = False
        else:
            msg = 'You will be forwarded after the model calculation is completed. You can also close the window and view' \
                  ' the results in your user account after the calculation is finished.'
            email_opt = False
        # TODO there was also the condition len(project.task_id) > 20 but I'm not sure why it is needed
        if simulation.task_id is not None and not task_is_finished(simulation.task_id):
            msg = 'CAUTION: You have a calculation in progress that has not yet been completed. Therefore you cannot' \
                  ' start another calculation. You can cancel the already running calculation by clicking on the' \
                  ' following button:'

        context = {
            'proj_id': proj_id,
            'msg': msg,
            'task_id': simulation.task_id,
            'time': 3,
            'email_opt': email_opt
        }
        return render(request, "pages/calculating.html", context)


# @login_required()
@require_http_methods(["GET"])
def simulation_results(request, proj_id=None):
    return render(request, "pages/simulation_results.html", context={"proj_id": proj_id})




# @login_required
@require_http_methods(["GET", "POST"])
def steps(request, proj_id, step_id=None):
    if step_id is None:
        return HttpResponseRedirect(reverse("steps:ogp_steps", args=[proj_id, 1]))

    return HttpResponseRedirect(reverse(f"steps:{STEPS[step_id-1]}", args=[proj_id]))

@require_http_methods(["GET"])
def load_previous_data(request, page_name=None, proj_id=None):
    if proj_id is not None:
        project = get_object_or_404(Project, id=proj_id)
        if project.user.email != request.user.email:
            raise PermissionDenied
    # user = await handle_user_accounts.get_user_from_cookie(request)
    # if user is None:
    #     return
    # project_id = request.query_params.get('project_id')
        if page_name == "project_setup":
            pass
        #     if project_id == 'new':
        #         project_id = await async_queries.next_project_id_of_user(user.id)
        #         return sa_tables.ProjectSetup(project_id=project_id)
        #     try:
        #         project_id = int(project_id)
        #     except (ValueError, TypeError):
        #         return None
        #     project_setup = await async_queries.get_model_instance(sa_tables.ProjectSetup, user.id, project_id)
        #     if hasattr(project_setup, 'start_date'):
        #         project_setup.start_datetime = project_setup.start_date.date().__str__()
        #         return project_setup
        #     else:
        #         return None
        elif page_name == "grid_design":
            grid_design = GridDesign.get(project=project)
            return grid_design
        # elif page_name == "demand_estimation":
            # try:
            #     project_id = int(project_id)
            # except (ValueError, TypeError):
            #     return None
            # demand_estimation = await async_queries.get_model_instance(sa_tables.Demand, user.id, project_id)
            # if (demand_estimation is not None
            #         and hasattr(demand_estimation, 'use_custom_demand')
            #         and demand_estimation.use_custom_demand is True):
            #     return demand_estimation
            # if demand_estimation is None or not hasattr(demand_estimation, 'maximum_peak_load'):
            #     return None
            # if pd.Series([value for key, value in demand_estimation.to_dict().items() if 'custom_share_' in key]).fillna(0).sum() == 0:
            #     wealth_share_dict = default_wealth_share()
            #     demand_estimation.custom_share_1 = wealth_share_dict['custom_share_1']
            #     demand_estimation.custom_share_2 = wealth_share_dict['custom_share_2']
            #     demand_estimation.custom_share_3 = wealth_share_dict['custom_share_3']
            #     demand_estimation.custom_share_4 = wealth_share_dict['custom_share_4']
            #     demand_estimation.custom_share_5 = wealth_share_dict['custom_share_5']
            # demand_estimation.maximum_peak_load = str(demand_estimation.maximum_peak_load) \
            #     if demand_estimation.maximum_peak_load is not None else ''
            # demand_estimation.average_daily_energy = str(demand_estimation.average_daily_energy) \
            #     if demand_estimation.average_daily_energy is not None else ''
            # demand_estimation.custom_calibration = True \
            #     if len(demand_estimation.maximum_peak_load) > 0 or len(demand_estimation.average_daily_energy) > 0 \
            #     else False
            # demand_estimation.calibration_options = 2 if len(demand_estimation.maximum_peak_load) > 0 else 1
            # return demand_estimation
        elif page_name == 'energy_system_design':
            energy_system_design = Energysystemdesign.objects.get(project=project)
            return energy_system_design
