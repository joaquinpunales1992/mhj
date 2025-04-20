from django.shortcuts import render
from inventory.models import Property

def display_home(request):
    """
    View function to render the home page.
    """

    properties = Property.objects.filter(show_in_front=True).order_by('price', 'featured')
    return render(request, 'home.html', context={'properties': properties})

def property_detail(request, pk):

    # Fetch the property details from the database
    property = Property.objects.filter(pk=pk).first()

    return render(request, 'property_detail.html', context={'property': property})

from django.core.mail import send_mail
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from django.template.loader import render_to_string
from django.utils.html import strip_tags

@csrf_exempt
def send_booking_confirmation(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        user_email = data.get('email')
        property_url = data.get('url')

        # Render the email template
        html_message = render_to_string('emails/booking_confirmation.html', {'property_url': property_url})
        plain_message = strip_tags(html_message)

        send_mail(
            'Your House in Japan',
            plain_message,
            'noreply@myhouseinjapan.com',
            [user_email, 'joaquinpunales@gmail.com'],
            html_message=html_message,
        )
        return JsonResponse({'message': 'Email sent'})
    return JsonResponse({'error': 'Invalid request'}, status=400)


def contact_seller(request, pk):
    property = Property.objects.filter(pk=pk).first()
    return render(request, 'contact_seller.html', context={'property': property})