import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import Enrollment, FlowDefinition, Recipient
from .timezones import timezone_for_country_code


@csrf_exempt
@require_http_methods(["POST"])
def create_flow(request):
    """Receives a Flow definition authored by the React flow builder.

    Expected body: {"name": str, "trigger_type": str,
    "steps": [{"step_index": int, "delay_minutes": int, "channel": "sms",
    "template": str}, ...]}
    """
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid json"}, status=400)

    steps = body.get("steps")
    if not isinstance(steps, list) or not steps:
        return JsonResponse({"error": "steps must be a non-empty list"}, status=400)
    for s in steps:
        if "step_index" not in s or "template" not in s:
            return JsonResponse({"error": "each step needs step_index and template"}, status=400)

    flow = FlowDefinition.objects.create(
        name=body.get("name", "unnamed flow"),
        trigger_type=body.get("trigger_type", "cart_abandoned"),
        steps=steps,
    )
    return JsonResponse({"id": flow.id, "name": flow.name, "steps": flow.steps}, status=201)


@require_http_methods(["GET"])
def get_flow(request, flow_id: int):
    try:
        flow = FlowDefinition.objects.get(id=flow_id)
    except FlowDefinition.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse({"id": flow.id, "name": flow.name, "trigger_type": flow.trigger_type, "steps": flow.steps})


@csrf_exempt
@require_http_methods(["POST"])
def create_enrollment(request):
    """Cart-abandoned trigger fires here: creates (or reuses) the Recipient
    and a new Enrollment in PENDING_OPTIN. No message goes out yet, the
    double opt-in has not happened."""
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid json"}, status=400)

    phone = body.get("phone_e164")
    country_code = body.get("country_code", "1")
    flow_id = body.get("flow_id")
    cart_id = body.get("cart_id", "")
    if not phone or not flow_id:
        return JsonResponse({"error": "phone_e164 and flow_id are required"}, status=400)

    try:
        flow = FlowDefinition.objects.get(id=flow_id)
    except FlowDefinition.DoesNotExist:
        return JsonResponse({"error": "unknown flow_id"}, status=404)

    recipient, _ = Recipient.objects.get_or_create(
        phone_e164=phone,
        defaults={"country_code": country_code, "timezone": timezone_for_country_code(country_code)},
    )
    enrollment = Enrollment.objects.create(recipient=recipient, flow=flow, cart_id=cart_id)
    return JsonResponse({"enrollment_id": enrollment.id, "state": enrollment.state}, status=201)


@csrf_exempt
@require_http_methods(["POST"])
def confirm_optin(request, recipient_id: int):
    """The recipient replied YES to the double-opt-in confirmation SMS.
    Moves every PENDING_OPTIN enrollment for this recipient forward:
    PENDING_OPTIN -> OPTED_IN -> ENROLLED, and schedules the first step."""
    try:
        recipient = Recipient.objects.get(id=recipient_id)
    except Recipient.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)

    moved = []
    for enrollment in recipient.enrollments.filter(state=Enrollment.PENDING_OPTIN):
        enrollment.transition_to(Enrollment.OPTED_IN, reason="recipient replied YES")
        enrollment.transition_to(Enrollment.ENROLLED, reason="opted in, ready for step 0")
        step = enrollment.flow.step(enrollment.current_step_index)
        enrollment.next_send_at = timezone.now() + timezone.timedelta(minutes=step.get("delay_minutes", 0))
        enrollment.save(update_fields=["next_send_at"])
        moved.append(enrollment.id)
    return JsonResponse({"enrollments_opted_in": moved})


@csrf_exempt
@require_http_methods(["POST"])
def confirm_optout(request, recipient_id: int):
    """The recipient replied STOP, or a hard gate suppressed them. Every
    non-terminal enrollment for this recipient moves to SUPPRESSED and
    will never be sent to again, regardless of anything already
    scheduled."""
    try:
        recipient = Recipient.objects.get(id=recipient_id)
    except Recipient.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)

    moved = []
    for enrollment in recipient.enrollments.exclude(state=Enrollment.SUPPRESSED):
        enrollment.transition_to(Enrollment.SUPPRESSED, reason="recipient opted out")
        moved.append(enrollment.id)
    return JsonResponse({"enrollments_suppressed": moved})
