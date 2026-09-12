from django.urls import path

from flows import views

urlpatterns = [
    path("api/flows", views.create_flow, name="create_flow"),
    path("api/flows/<int:flow_id>", views.get_flow, name="get_flow"),
    path("api/enrollments", views.create_enrollment, name="create_enrollment"),
    path("api/recipients/<int:recipient_id>/optin", views.confirm_optin, name="confirm_optin"),
    path("api/recipients/<int:recipient_id>/optout", views.confirm_optout, name="confirm_optout"),
]
