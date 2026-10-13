"""Native async Django views; session identity and CSRF remain server-enforced."""

from asgiref.sync import sync_to_async
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .models import Order
from .services import InvalidTransition, ship_order


@require_GET
async def order_detail(request, order_id):
    user = await request.auser()
    if not user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=401)
    try:
        row = await Order.objects.filter(owner_id=user.pk).values("id", "status").aget(pk=order_id)
    except Order.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)
    return JsonResponse({"id": str(row["id"]), "status": row["status"]})


@require_POST
async def ship_order_view(request, order_id):
    user = await request.auser()
    if not user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=401)
    try:
        row = await sync_to_async(ship_order, thread_sensitive=True)(order_id, user.pk)
    except Order.DoesNotExist:
        return JsonResponse({"error": "not_found"}, status=404)
    except InvalidTransition:
        return JsonResponse({"error": "invalid_transition"}, status=409)
    return JsonResponse(row)
