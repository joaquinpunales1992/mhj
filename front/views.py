import json
from django.shortcuts import render
from inventory.models import Property
from django.core.mail import EmailMessage
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.template.loader import render_to_string


def display_home(request):

    properties = Property.objects.filter(show_in_front=True).order_by('-featured', 'price')
    return render(request, 'home.html', context={'properties': properties})

def property_detail(request, pk):

    # Fetch the property details from the database
    property = Property.objects.filter(pk=pk).first()

    return render(request, 'property_detail.html', context={'property': property})



@csrf_exempt
def send_booking_confirmation(request):
    if request.method == 'POST':
        data = json.loads(request.body)

        user_email = data.get('user_email')
        property_url = data.get('url')

        # Render the email template
        html_message = render_to_string('emails/booking_confirmation.html', {'property_url': property_url})

        email = EmailMessage(
            subject='Your House in Japan - Booking Confirmation',
            body=html_message,
            from_email='noreply@myhouseinjapan.com',
            to=[user_email], # Add at least one recipient in the 'to' field
            bcc=['joaquinpunales@gmail.com'],
            reply_to=['noreply@myhouseinjapan.com']
        )

        email.content_subtype = 'html'
        try:
            email.send()
        except Exception as e:
            print(f"Error sending email: {e}")

        return JsonResponse({'message': 'Email sent'})
    return JsonResponse({'error': 'Invalid request'}, status=400)


def contact_seller(request, pk):
    property = Property.objects.filter(pk=pk).first()
    user_email = None

    try:
        if getattr(request.user, 'email', None) is not None:
            user_email = request.user.email
    except AttributeError:
        pass

    try:
        if 'email' in request.COOKIES:
            user_email = request.COOKIES['email']
    except AttributeError:
        pass

    if not user_email:
        return JsonResponse({'error': 'Forbidden request'}, status=403)
    
    return render(request, 'contact_seller.html', context={'property': property, 'user_email': user_email})