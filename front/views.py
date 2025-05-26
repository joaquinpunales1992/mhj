import json
from django.conf import settings
from django.shortcuts import render
from inventory.models import Property
from django.core.mail import EmailMessage
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.template.loader import render_to_string
from django.shortcuts import redirect


def display_home(request):
    properties = Property.objects.filter(show_in_front=True, price__lte=1500, price__gt=0).order_by('-featured', 'price')[:settings.PROPERTIES_TO_DISPLAY]
    return render(request, 'home.html', context={'properties': properties})

def property_detail(request, pk):
    import pdb;pdb.set_trace()
    # Fetch the property details from the database
    property = Property.objects.filter(pk=pk).first()

    return render(request, 'property_detail.html', context={'property': property})

@csrf_exempt
def submit_premium_request(request):
    if request.method == 'POST':
        data = json.loads(request.body)

        user_email = data.get('user_email')
        property_url = data.get('url')

        # Render the email template
        html_message = render_to_string('emails/premium_request.html', {'property_url': property_url})

        email = EmailMessage(
            subject='Your Akiya in Japan - Premium Account Request',
            body=html_message,
            from_email='noreply@myakiyainjapan.com',
            to=[user_email],
            bcc=['joaquinpunales@gmail.com'],
            reply_to=['noreply@myakiyainjapan.com']
        )

        email.content_subtype = 'html'
        try:
            email.send()
        except Exception as e:
            print(f"Error sending email: {e}")

        return JsonResponse({'message': 'Email sent'})
    return JsonResponse({'error': 'Invalid request'}, status=400)



@csrf_exempt
def send_booking_confirmation(request):
    if request.method == 'POST':
        data = json.loads(request.body)

        user_email = data.get('user_email')
        property_url = data.get('url')

        # Render the email template
        html_message = render_to_string('emails/booking_confirmation.html', {'property_url': property_url})

        email = EmailMessage(
            subject='Your Akiya in Japan - Booking Confirmation',
            body=html_message,
            from_email='noreply@myakiyainjapan.com',
            to=[user_email],
            bcc=['joaquinpunales@gmail.com'],
            reply_to=['noreply@myakiyainjapan.com']
        )

        email.content_subtype = 'html'
        try:
            email.send()
        except Exception as e:
            print(f"Error sending email: {e}")

        return JsonResponse({'message': 'Email sent'})
    return JsonResponse({'error': 'Invalid request'}, status=400)


def contact_seller(request, pk, user_just_registered=0):
    property = Property.objects.filter(pk=pk).first()
    user_email = request.user.email if request.user.is_authenticated else request.COOKIES.get('email')
    return render(request, 'contact_seller.html', context={'property': property, 'user_email': user_email, 'user_just_registered': user_just_registered})


def redirect_404_view(request):
    properties = Property.objects.filter(show_in_front=True, price__lte=1500, price__gt=0).order_by('-featured', 'price')[:settings.PROPERTIES_TO_DISPLAY]
    return render(request, 'home.html', context={'properties': properties})