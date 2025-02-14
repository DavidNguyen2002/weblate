# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os.path
import subprocess
from base64 import b64decode
from email import message_from_string

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.http.response import HttpResponse, HttpResponseServerError
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from weblate.auth.models import User
from weblate.gitexport.models import SUPPORTED_VCS
from weblate.gitexport.utils import find_git_http_backend
from weblate.utils.errors import report_error
from weblate.utils.views import get_component


def response_authenticate():
    """Return 401 response with authenticate header."""
    response = HttpResponse(status=401)
    response["WWW-Authenticate"] = 'Basic realm="Weblate Git access"'
    return response


def authenticate(request, auth):
    """Perform authentication with HTTP Basic auth."""
    try:
        method, data = auth.split(None, 1)
    except (ValueError, TypeError):
        return False
    if method.lower() == "basic":
        try:
            username, code = b64decode(data).decode("iso-8859-1").split(":", 1)
        except (ValueError, TypeError):
            return False
        try:
            user = User.objects.get(username=username, auth_token__key=code)
        except User.DoesNotExist:
            return False

        if not user.is_active:
            return False

        request.user = user
        return True
    return False


@never_cache
@csrf_exempt
def git_export(request, project, component, path):
    """
    Git HTTP server view.

    Wrapper around git-http-backend to provide Git repositories export over HTTP.
    Performs permission checks and hands over execution to the wrapper.
    """
    # Probably browser access
    if not path:
        return redirect(
            "component", project=project, component=component, permanent=False
        )
    # Strip possible double path separators
    path = path.lstrip("/\\")

    # HTTP authentication
    auth = request.headers.get("authorization", b"")

    # Reject non pull access early
    if request.GET.get("service", "") not in ("", "git-upload-pack"):
        raise PermissionDenied("Only pull is supported")

    if auth and not authenticate(request, auth):
        return response_authenticate()

    # Permissions
    try:
        obj = get_component(request, project, component)
    except Http404:
        if not request.user.is_authenticated:
            return response_authenticate()
        raise
    if not request.user.has_perm("vcs.access", obj):
        if not request.user.is_authenticated:
            return response_authenticate()
        raise PermissionDenied("No VCS permissions")
    if obj.vcs not in SUPPORTED_VCS:
        raise Http404("Not a git repository")
    if obj.is_repo_link:
        kwargs = obj.linked_component.get_reverse_url_kwargs()
        kwargs["path"] = path
        return redirect(
            "{}?{}".format(
                reverse("git-export", kwargs=kwargs), request.META["QUERY_STRING"]
            ),
            permanent=True,
        )

    return run_git_http(request, obj, path)


def run_git_http(request, obj, path):
    """Git HTTP backend execution wrapper."""
    # Find Git HTTP backend
    git_http_backend = find_git_http_backend()
    if git_http_backend is None:
        return HttpResponseServerError("git-http-backend not found")

    # Invoke Git HTTP backend
    query = request.META.get("QUERY_STRING", "")
    process_env = {
        "REQUEST_METHOD": request.method,
        "PATH_TRANSLATED": os.path.join(obj.full_path, path),
        "GIT_HTTP_EXPORT_ALL": "1",
        "CONTENT_TYPE": request.headers.get("content-type", ""),
        "QUERY_STRING": query,
        "HTTP_CONTENT_ENCODING": request.headers.get("content-encoding", ""),
    }
    process = subprocess.Popen(
        [git_http_backend],
        env=process_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output, output_err = process.communicate(request.body)
    retcode = process.poll()

    # Log error
    if output_err:
        output_err = output_err.decode()
        report_error(
            cause="Git backend failure",
            project=obj.project,
            level="error",
            message=True,
        )

    # Handle failure
    if retcode:
        return HttpResponseServerError(output_err)

    headers, content = output.split(b"\r\n\r\n", 1)
    message = message_from_string(headers.decode())

    # Handle status in response
    if "status" in message:
        return HttpResponse(status=int(message["status"].split()[0]))

    # Send content
    response = HttpResponse(content_type=message["content-type"])
    response.write(content)
    return response
