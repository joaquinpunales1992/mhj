from django.shortcuts import render, redirect
from django.urls import reverse


def show_authenticate_page(request, pk):
    return render(request, 'authentication_page.html', context={'property_pk': pk})


def register_via_email(request, pk):
    email = request.POST.get('email')
    if email:
        response = redirect(reverse('contact_seller', args=[pk]))
        response.set_cookie('email', email)
        return response

