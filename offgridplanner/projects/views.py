import io
import json
import os
import time
from collections import defaultdict

import numpy as np

# from jsonview.decorators import json_view
import pandas as pd
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.forms import model_to_dict
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.http import JsonResponse
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from offgridplanner.projects.helpers import load_project_from_dict
from offgridplanner.steps.models import CustomDemand
from offgridplanner.steps.models import Energysystemdesign
from offgridplanner.steps.models import GridDesign
from offgridplanner.optimization.models import Nodes
from offgridplanner.projects.models import Options
from offgridplanner.projects.models import Project
from offgridplanner.users.models import User


@require_http_methods(["GET"])
def home(request):
    if request.user.is_authenticated:
        return HttpResponseRedirect(reverse("projects:projects_list"))
    return render(request, "pages/landing_page.html")


@login_required
@require_http_methods(["GET"])
def projects_list(request, proj_id=None):
    projects = (
        Project.objects.filter(Q(user=request.user))
        .distinct()
        .order_by("date_created")
        .reverse()
    )
    for project in projects:
        # TODO this should not be useful
        # project.created_at = project.created_at.date()
        # project.updated_at = project.updated_at.date()
        if bool(os.environ.get("DOCKERIZED")):
            status = "pending"  # TODO connect this to the worker
            # status = worker.AsyncResult(user.task_id).status.lower()
        else:
            status = "success"
        if status in ["success", "failure", "revoked"]:
            # TODO this is not useful
            # user.task_id = ''
            # user.project_id = None
            if status == "success":
                # TODO Here I am not sure we should use the status of the project rather the one of the simulation
                project.status = "finished"
            else:
                project.status = status
            project.save()
            # TODO this is not useful
            # user.task_id = ''
            # user.project_id = None

    return render(request, "pages/user_projects.html", {"projects": projects})


@require_http_methods(["GET", "POST"])
def project_duplicate(request, proj_id):
    if proj_id is not None:
        project = get_object_or_404(Project, id=proj_id)
        if project.user != request.user:
            raise PermissionDenied
        # TODO check user rights to the project
        dm = project.export()
        user = User.objects.get(email=request.user.email)
        # TODO must find user from its email address
        new_proj_id = load_project_from_dict(dm, user=user)

    return HttpResponseRedirect(reverse("projects:projects_list"))


@require_http_methods(["POST"])
def project_delete(request, proj_id):
    project = get_object_or_404(Project, id=proj_id)

    if project.user != request.user:
        raise PermissionDenied

    if request.method == "POST":
        project.delete()
        # message not defined
        messages.success(request, "Project successfully deleted!")

    return HttpResponseRedirect(reverse("projects:projects_list"))


def get_project_data(project):
    # TODO in the original function the user is redirected to whatever page has missing data, i would rather do an error message
    """
    Checks if all necessary data for the optimization exists
    :param project:
    :return:
    """
    options = Options.objects.get(project=project)

    model_qs = {
        "Nodes": Nodes.objects.filter(project=project),
        "CustomDemand": CustomDemand.objects.filter(project=project),
        "GridDesign": GridDesign.objects.filter(project=project),
        "Energysystemdesign": Energysystemdesign.objects.filter(project=project),
    }

    # TODO check which models are only necessary for the skipped steps and exclude them
    if options.do_demand_estimation is False:
        # qs.pop(..)
        pass

    if options.do_grid_optimization is False:
        pass

    if options.do_es_design_optimization is False:
        pass

    missing_qs = [key for key, qs in model_qs.items() if not qs.exists()]
    if missing_qs:
        raise ValueError(
            f"The project does not contain all data required for the optimization."
            f" The following models are missing: {missing_qs}",
        )
    proj_data = {key: qs.get() for key, qs in model_qs.items()}
    return proj_data
