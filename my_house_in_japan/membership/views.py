from django.shortcuts import render


def show_authenticate_page(request, pk):
    return render(request, 'authentication_page.html')

