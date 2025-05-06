from django.shortcuts import render, redirect
from django.urls import reverse
from membership.utils import notify_user_registered_via_email


def upgrade_premium(request):
    return render(request, 'premium_account.html')

def show_authenticate_page(request, pk, redirect_to_premium=0):
    return render(request, 'authentication_page.html', context={'property_pk': pk, 'redirect_to_premium': redirect_to_premium})

def register_via_email(request, pk, redirect_to_premium=0):
    email = request.POST.get('email')
    if email:
        notify_user_registered_via_email(email)

        if redirect_to_premium == '1':
            response = redirect(reverse('upgrade_premium'))
            response.set_cookie('email', email)
            return response
        response = redirect(reverse('contact_seller', args=[pk]))
        response.set_cookie('email', email)
        return response

def approved_membership_payment(request):
    # TODO
    pass