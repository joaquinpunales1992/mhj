from django.shortcuts import render, redirect
from django.urls import reverse
from membership.utils import notify_user_registered_via_email


def upgrade_premium(request):
    # The template's premium-request fetch sends user_email from this context;
    # if it's missing, the backend receives an empty email and we save useless
    # rows we can't follow up on. Resolve from auth user or the email cookie
    # set during the register-via-email flow.
    user_email = ""
    if request.user.is_authenticated:
        user_email = request.user.email or ""
    if not user_email:
        user_email = request.COOKIES.get("email", "")
    return render(request, "premium_account.html", {"user_email": user_email})


def show_authenticate_page(request, pk, redirect_to_premium=0):
    return render(
        request,
        "authentication_page.html",
        context={"property_pk": pk, "redirect_to_premium": redirect_to_premium},
    )


def register_via_email(request, pk, redirect_to_premium=0):
    email = request.POST.get("email")
    if email:
        notify_user_registered_via_email(email)

        if redirect_to_premium == "1":
            response = redirect(reverse("upgrade_premium"))
            response.set_cookie("email", email)
            return response
        user_just_registered = 1
        response = redirect(reverse("property_detail", args=[pk, user_just_registered]))
        response.set_cookie("email", email)
        return response


def approved_membership_payment(request):
    # TODO
    pass
