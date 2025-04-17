from django.shortcuts import render
from my_house_in_japan.inventory.models import Property

def display_home(request):
    """
    View function to render the home page.
    """

    properties = Property.objects.filter(show_in_front=True)
    return render(request, 'home.html', context={'properties': properties})

def property_detail(request, pk):

    # Fetch the property details from the database
    property = Property.objects.filter(pk=pk).first()

    return render(request, 'property_detail.html', context={'property': property})

from django.core.mail import send_mail
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json

@csrf_exempt
def send_booking_confirmation(request):
    if request.method == 'POST':

        send_mail(
            'Your House in Japan',
            f'Hi, we have received your inquiry about a property. We will get back to you soon.',
            'noreply@myhouseinjapan.com',
            ['joaquinpunales@gmail.com'],  
        )
        return JsonResponse({'message': 'Email sent'})
    return JsonResponse({'error': 'Invalid request'}, status=400)


def contact_seller(request, pk):
    property = Property.objects.filter(pk=pk).first()
    return render(request, 'contact_seller.html', context={'property': property})