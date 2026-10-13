from django.urls import path
from orders import views

urlpatterns = [
    path("orders/<uuid:order_id>/", views.order_detail, name="order-detail"),
    path("orders/<uuid:order_id>/ship/", views.ship_order_view, name="order-ship"),
]
